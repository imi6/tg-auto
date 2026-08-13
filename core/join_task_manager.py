"""
批量入群任务管理器 - 应用单例模式
负责多账号并发加入群组、限速控制、进度上报与任务持久化
"""

import asyncio
import json
import random
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .account_manager import AccountManager
from services.group_service import (
    GroupService,
    STATUS_ALREADY,
    STATUS_FAILED,
    STATUS_FLOOD,
    STATUS_PENDING,
    STATUS_SUCCESS,
)
from utils.logger import get_logger
from utils.singleton import Singleton

# 单个账号内两次加群之间的最小间隔，低于该值会显著提高风控概率
MIN_JOIN_DELAY = 5
MAX_HISTORY_TASKS = 50

DEFAULT_OPTIONS = {
    'delay_min': 30,
    'delay_max': 60,
    'max_per_account': 20,
    'max_flood_wait': 600,
}


class JoinTaskManager(metaclass=Singleton):

    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.tasks_file = Path("data/join_tasks.json")
        self.logger = get_logger(__name__)
        self.account_manager = AccountManager()
        self.group_service = GroupService()

        self._runners: Dict[str, asyncio.Task] = {}
        self._cancelled: set = set()
        self._listeners: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []

        self._load_tasks()

    # -------------------------------------------------------------- 持久化

    def _load_tasks(self):
        if not self.tasks_file.exists():
            return

        try:
            with open(self.tasks_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for task in data.get('tasks', []):
                # 进程重启后运行中的任务无法恢复，标记为中断
                if task.get('status') == 'running':
                    task['status'] = 'interrupted'
                    for account_state in task.get('accounts', {}).values():
                        if account_state.get('status') == 'running':
                            account_state['status'] = 'interrupted'
                self.tasks[task['task_id']] = task

            self.logger.info(f"已加载 {len(self.tasks)} 个入群任务记录")
        except Exception as e:
            self.logger.error(f"加载入群任务记录失败: {e}")

    def _save_tasks(self):
        try:
            self.tasks_file.parent.mkdir(parents=True, exist_ok=True)

            ordered = sorted(self.tasks.values(), key=lambda t: t.get('created_at', ''), reverse=True)
            kept = ordered[:MAX_HISTORY_TASKS]
            self.tasks = {task['task_id']: task for task in kept}

            with open(self.tasks_file, 'w', encoding='utf-8') as f:
                json.dump({'tasks': kept}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存入群任务记录失败: {e}")

    # -------------------------------------------------------------- 进度上报

    def add_listener(self, listener: Callable[[Dict[str, Any]], Awaitable[None]]):
        if listener not in self._listeners:
            self._listeners.append(listener)

    async def _notify(self, task: Dict[str, Any], persist: bool = True):
        if persist:
            self._save_tasks()

        for listener in list(self._listeners):
            try:
                await listener(task)
            except Exception as e:
                self.logger.debug(f"推送入群任务进度失败: {e}")

    # -------------------------------------------------------------- 任务管理

    def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        ordered = sorted(self.tasks.values(), key=lambda t: t.get('created_at', ''), reverse=True)
        return ordered[:limit]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)

    def has_running_task(self) -> bool:
        return any(task.get('status') == 'running' for task in self.tasks.values())

    def create_task(
        self,
        account_ids: List[str],
        targets: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        merged_options = dict(DEFAULT_OPTIONS)
        merged_options.update({k: v for k, v in (options or {}).items() if v is not None})

        delay_min = max(MIN_JOIN_DELAY, int(merged_options['delay_min']))
        delay_max = max(delay_min, int(merged_options['delay_max']))
        merged_options['delay_min'] = delay_min
        merged_options['delay_max'] = delay_max
        merged_options['max_per_account'] = max(1, int(merged_options['max_per_account']))
        merged_options['max_flood_wait'] = max(0, int(merged_options['max_flood_wait']))

        per_account = targets[:merged_options['max_per_account']]
        task_id = f"join_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"

        task = {
            'task_id': task_id,
            'created_at': datetime.now().isoformat(),
            'finished_at': None,
            'status': 'running',
            'options': merged_options,
            'targets': per_account,
            'skipped_targets': len(targets) - len(per_account),
            'accounts': {
                account_id: {
                    'account_id': account_id,
                    'status': 'pending',
                    'current': None,
                    'items': []
                }
                for account_id in account_ids
            },
            'summary': {
                'total': len(per_account) * len(account_ids),
                'done': 0,
                STATUS_SUCCESS: 0,
                STATUS_ALREADY: 0,
                STATUS_PENDING: 0,
                STATUS_FAILED: 0,
            }
        }

        self.tasks[task_id] = task
        self._save_tasks()

        self._runners[task_id] = asyncio.create_task(self._run_task(task_id))
        self.logger.info(
            f"创建批量入群任务 {task_id}: {len(account_ids)} 个账号 x {len(per_account)} 个目标"
        )

        return task

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task['status'] != 'running':
            return False

        self._cancelled.add(task_id)
        self.logger.info(f"批量入群任务 {task_id} 已请求取消")
        return True

    def delete_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task['status'] == 'running':
            return False

        del self.tasks[task_id]
        self._runners.pop(task_id, None)
        self._cancelled.discard(task_id)
        self._save_tasks()
        return True

    # -------------------------------------------------------------- 任务执行

    async def _run_task(self, task_id: str):
        task = self.tasks[task_id]

        try:
            await self._notify(task)
            await asyncio.gather(
                *[self._run_account(task_id, account_id) for account_id in list(task['accounts'].keys())],
                return_exceptions=True
            )
        except Exception as e:
            self.logger.error(f"批量入群任务 {task_id} 执行异常: {e}")
        finally:
            cancelled = task_id in self._cancelled
            task['status'] = 'cancelled' if cancelled else 'completed'
            task['finished_at'] = datetime.now().isoformat()
            self._cancelled.discard(task_id)
            self._runners.pop(task_id, None)

            summary = task['summary']
            self.logger.info(
                f"批量入群任务 {task_id} 结束: 成功 {summary[STATUS_SUCCESS]}, "
                f"已在群 {summary[STATUS_ALREADY]}, 待批准 {summary[STATUS_PENDING]}, "
                f"失败 {summary[STATUS_FAILED]}"
            )
            await self._notify(task)

    async def _run_account(self, task_id: str, account_id: str):
        task = self.tasks[task_id]
        state = task['accounts'][account_id]
        options = task['options']

        client = await self._prepare_client(account_id)
        if not client:
            state['status'] = 'failed'
            for target in task['targets']:
                self._record(task, account_id, target, {
                    'status': STATUS_FAILED,
                    'message': '账号未登录或客户端连接失败'
                })
            await self._notify(task)
            return

        state['status'] = 'running'
        await self._notify(task)

        for index, target in enumerate(task['targets']):
            if task_id in self._cancelled:
                state['status'] = 'cancelled'
                await self._notify(task)
                return

            if index > 0:
                delay = random.randint(options['delay_min'], options['delay_max'])
                state['current'] = {'target': target['raw'], 'waiting_seconds': delay}
                await self._notify(task, persist=False)
                if not await self._sleep_cancellable(task_id, delay):
                    state['status'] = 'cancelled'
                    await self._notify(task)
                    return

            state['current'] = {'target': target['raw'], 'waiting_seconds': 0}
            await self._notify(task, persist=False)

            result = await self.group_service.join(client, target)

            if result['status'] == STATUS_FLOOD:
                wait_seconds = result.get('wait_seconds', 0)
                if wait_seconds > options['max_flood_wait']:
                    self._record(task, account_id, target, {
                        'status': STATUS_FAILED,
                        'message': f"触发频率限制需等待 {wait_seconds} 秒，超过上限，已中止该账号"
                    })
                    state['status'] = 'aborted'
                    state['current'] = None
                    await self._notify(task)
                    return

                self.logger.warning(f"账号 {account_id} 触发限流，等待 {wait_seconds} 秒后重试")
                state['current'] = {'target': target['raw'], 'waiting_seconds': wait_seconds, 'flood': True}
                await self._notify(task, persist=False)

                if not await self._sleep_cancellable(task_id, wait_seconds):
                    state['status'] = 'cancelled'
                    await self._notify(task)
                    return

                result = await self.group_service.join(client, target)
                if result['status'] == STATUS_FLOOD:
                    result = {'status': STATUS_FAILED, 'message': '重试后仍被限流'}

            self._record(task, account_id, target, result)
            await self._notify(task)

            if result.get('fatal'):
                state['status'] = 'aborted'
                state['current'] = None
                await self._notify(task)
                return

        state['status'] = 'completed'
        state['current'] = None
        await self._notify(task)

    async def _prepare_client(self, account_id: str):
        account = self.account_manager.get_account(account_id)
        if not account:
            return None

        try:
            if not account.client:
                await self.account_manager.connect_account(account_id)
                account = self.account_manager.get_account(account_id)

            if not account or not account.client:
                return None

            if not account.client.is_connected():
                await account.client.connect()

            if not await account.client.is_user_authorized():
                self.logger.warning(f"账号 {account_id} 未授权，无法执行入群")
                return None

            return account.client
        except Exception as e:
            self.logger.error(f"准备账号 {account_id} 客户端失败: {e}")
            return None

    async def _sleep_cancellable(self, task_id: str, seconds: int) -> bool:
        """分片休眠，任务被取消时立即返回 False"""
        remaining = seconds
        while remaining > 0:
            if task_id in self._cancelled:
                return False
            step = min(1, remaining)
            await asyncio.sleep(step)
            remaining -= step
        return task_id not in self._cancelled

    def _record(self, task: Dict[str, Any], account_id: str, target: Dict[str, str], result: Dict[str, Any]):
        state = task['accounts'][account_id]
        status = result.get('status', STATUS_FAILED)

        state['items'].append({
            'target': target['raw'],
            'title': result.get('title', ''),
            'chat_id': result.get('chat_id'),
            'status': status,
            'message': result.get('message', ''),
            'time': datetime.now().isoformat()
        })
        state['current'] = None

        summary = task['summary']
        summary['done'] += 1
        if status in summary:
            summary[status] += 1
        else:
            summary[STATUS_FAILED] += 1
