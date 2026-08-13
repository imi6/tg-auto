"""
多账号批量任务调度骨架
负责账号级并行、账号内串行限速、FloodWait 处理、进度推送与任务持久化，
具体的 Telegram 操作由子类通过 execute_item 提供
"""

import asyncio
import json
import random
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from models.task import (
    ACCOUNT_ABORTED,
    ACCOUNT_CANCELLED,
    ACCOUNT_COMPLETED,
    ACCOUNT_FAILED,
    ACCOUNT_PENDING,
    ACCOUNT_RUNNING,
    STATUS_FAILED,
    STATUS_FLOOD,
    SUMMARY_STATUSES,
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_INTERRUPTED,
    TASK_RUNNING,
)
from utils.logger import get_logger

from .account_manager import AccountManager


class BatchTaskManager:
    """批量任务基类

    子类至少要实现 execute_item；需要在界面上显示更友好的条目名称时可覆盖 describe_item。
    """

    # 任务 ID 前缀与日志中的任务名称
    task_type = 'batch'
    task_label = '批量任务'

    default_options = {
        'delay_min': 30,
        'delay_max': 60,
        'max_per_account': 20,
        'max_flood_wait': 600,
    }

    # 账号内两次操作的最小间隔，子类可按操作的风控强度调整
    min_delay = 5
    max_history_tasks = 50

    def __init__(self, tasks_file: str):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.tasks_file = Path(tasks_file)
        self.logger = get_logger(self.__class__.__module__)
        self.account_manager = AccountManager()

        self._runners: Dict[str, asyncio.Task] = {}
        self._cancelled: set = set()
        self._listeners: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []

        self._load_tasks()

    # ------------------------------------------------------------ 子类扩展点

    async def execute_item(self, client, item: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        """执行单条操作，返回带 status 的结果字典

        status 取 models.task 中的常量；返回 STATUS_FLOOD 时需附带 wait_seconds，
        基类会按 max_flood_wait 决定等待重试还是中止该账号。
        """
        raise NotImplementedError

    def describe_item(self, item: Dict[str, Any]) -> str:
        return str(item)

    # -------------------------------------------------------------- 持久化

    def _load_tasks(self):
        if not self.tasks_file.exists():
            return

        try:
            with open(self.tasks_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for task in data.get('tasks', []):
                # 进程重启后运行中的任务无法恢复，标记为中断
                if task.get('status') == TASK_RUNNING:
                    task['status'] = TASK_INTERRUPTED
                    for account_state in task.get('accounts', {}).values():
                        if account_state.get('status') == ACCOUNT_RUNNING:
                            account_state['status'] = TASK_INTERRUPTED
                self.tasks[task['task_id']] = task

            self.logger.info(f"已加载 {len(self.tasks)} 个{self.task_label}记录")
        except Exception as e:
            self.logger.error(f"加载{self.task_label}记录失败: {e}")

    def _save_tasks(self):
        try:
            self.tasks_file.parent.mkdir(parents=True, exist_ok=True)

            ordered = sorted(self.tasks.values(), key=lambda t: t.get('created_at', ''), reverse=True)
            kept = ordered[:self.max_history_tasks]
            self.tasks = {task['task_id']: task for task in kept}

            with open(self.tasks_file, 'w', encoding='utf-8') as f:
                json.dump({'tasks': kept}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存{self.task_label}记录失败: {e}")

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
                self.logger.debug(f"推送{self.task_label}进度失败: {e}")

    # -------------------------------------------------------------- 任务管理

    def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        ordered = sorted(self.tasks.values(), key=lambda t: t.get('created_at', ''), reverse=True)
        return ordered[:limit]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)

    def has_running_task(self) -> bool:
        return any(task.get('status') == TASK_RUNNING for task in self.tasks.values())

    def _merge_options(self, options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = dict(self.default_options)
        merged.update({k: v for k, v in (options or {}).items() if v is not None})

        delay_min = max(self.min_delay, int(merged['delay_min']))
        delay_max = max(delay_min, int(merged['delay_max']))
        merged['delay_min'] = delay_min
        merged['delay_max'] = delay_max
        merged['max_per_account'] = max(1, int(merged['max_per_account']))
        merged['max_flood_wait'] = max(0, int(merged['max_flood_wait']))

        return merged

    def create_task(
        self,
        account_ids: List[str],
        items: List[Dict[str, Any]],
        options: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        merged_options = self._merge_options(options)
        per_account = items[:merged_options['max_per_account']]
        task_id = f"{self.task_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"

        task = {
            'task_id': task_id,
            'task_type': self.task_type,
            'created_at': datetime.now().isoformat(),
            'finished_at': None,
            'status': TASK_RUNNING,
            'options': merged_options,
            'items': per_account,
            'skipped_items': len(items) - len(per_account),
            'accounts': {
                account_id: {
                    'account_id': account_id,
                    'status': ACCOUNT_PENDING,
                    'current': None,
                    'items': []
                }
                for account_id in account_ids
            },
            'summary': dict(
                {'total': len(per_account) * len(account_ids), 'done': 0},
                **{status: 0 for status in SUMMARY_STATUSES}
            )
        }
        if extra:
            task.update(extra)

        self.tasks[task_id] = task
        self._save_tasks()

        self._runners[task_id] = asyncio.create_task(self._run_task(task_id))
        self.logger.info(
            f"创建{self.task_label} {task_id}: {len(account_ids)} 个账号 x {len(per_account)} 项"
        )

        return task

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task['status'] != TASK_RUNNING:
            return False

        self._cancelled.add(task_id)
        self.logger.info(f"{self.task_label} {task_id} 已请求取消")
        return True

    def delete_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task['status'] == TASK_RUNNING:
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
            self.logger.error(f"{self.task_label} {task_id} 执行异常: {e}")
        finally:
            cancelled = task_id in self._cancelled
            task['status'] = TASK_CANCELLED if cancelled else TASK_COMPLETED
            task['finished_at'] = datetime.now().isoformat()
            self._cancelled.discard(task_id)
            self._runners.pop(task_id, None)

            summary = task['summary']
            self.logger.info(
                f"{self.task_label} {task_id} 结束: "
                + ", ".join(f"{status} {summary[status]}" for status in SUMMARY_STATUSES)
            )
            await self._notify(task)

    async def _run_account(self, task_id: str, account_id: str):
        task = self.tasks[task_id]
        state = task['accounts'][account_id]
        options = task['options']

        client = await self._prepare_client(account_id)
        if not client:
            state['status'] = ACCOUNT_FAILED
            for item in task['items']:
                self._record(task, account_id, item, {
                    'status': STATUS_FAILED,
                    'message': '账号未登录或客户端连接失败'
                })
            await self._notify(task)
            return

        state['status'] = ACCOUNT_RUNNING
        await self._notify(task)

        for index, item in enumerate(task['items']):
            if task_id in self._cancelled:
                state['status'] = ACCOUNT_CANCELLED
                await self._notify(task)
                return

            label = self.describe_item(item)

            if index > 0 and options['delay_max'] > 0:
                delay = random.randint(options['delay_min'], options['delay_max'])
                state['current'] = {'target': label, 'waiting_seconds': delay}
                await self._notify(task, persist=False)
                if not await self._sleep_cancellable(task_id, delay):
                    state['status'] = ACCOUNT_CANCELLED
                    await self._notify(task)
                    return

            state['current'] = {'target': label, 'waiting_seconds': 0}
            await self._notify(task, persist=False)

            result = await self._execute_with_flood_retry(task_id, account_id, client, item, label)
            if result is None:
                return

            self._record(task, account_id, item, result)
            await self._notify(task)

            if result.get('fatal'):
                state['status'] = ACCOUNT_ABORTED
                state['current'] = None
                await self._notify(task)
                return

        state['status'] = ACCOUNT_COMPLETED
        state['current'] = None
        await self._notify(task)

    async def _execute_with_flood_retry(
        self, task_id: str, account_id: str, client, item: Dict[str, Any], label: str
    ) -> Optional[Dict[str, Any]]:
        """执行单条操作并处理限流，返回 None 表示该账号已终止（取消或超过等待上限）"""
        task = self.tasks[task_id]
        state = task['accounts'][account_id]
        options = task['options']

        try:
            result = await self.execute_item(client, item, task)
        except Exception as e:
            self.logger.error(f"{self.task_label} {task_id} 账号 {account_id} 执行 {label} 异常: {e}")
            return {'status': STATUS_FAILED, 'message': str(e) or e.__class__.__name__}

        if result.get('status') != STATUS_FLOOD:
            return result

        wait_seconds = int(result.get('wait_seconds', 0) or 0)
        if wait_seconds > options['max_flood_wait']:
            self._record(task, account_id, item, {
                'status': STATUS_FAILED,
                'message': f"触发频率限制需等待 {wait_seconds} 秒，超过上限，已中止该账号"
            })
            state['status'] = ACCOUNT_ABORTED
            state['current'] = None
            await self._notify(task)
            return None

        self.logger.warning(f"账号 {account_id} 触发限流，等待 {wait_seconds} 秒后重试")
        state['current'] = {'target': label, 'waiting_seconds': wait_seconds, 'flood': True}
        await self._notify(task, persist=False)

        if not await self._sleep_cancellable(task_id, wait_seconds):
            state['status'] = ACCOUNT_CANCELLED
            await self._notify(task)
            return None

        try:
            retried = await self.execute_item(client, item, task)
        except Exception as e:
            return {'status': STATUS_FAILED, 'message': str(e) or e.__class__.__name__}

        if retried.get('status') == STATUS_FLOOD:
            return {'status': STATUS_FAILED, 'message': '重试后仍被限流'}

        return retried

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
                self.logger.warning(f"账号 {account_id} 未授权，无法执行{self.task_label}")
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
            await asyncio.sleep(1)
            remaining -= 1
        return task_id not in self._cancelled

    def _record(self, task: Dict[str, Any], account_id: str, item: Dict[str, Any], result: Dict[str, Any]):
        state = task['accounts'][account_id]
        status = result.get('status', STATUS_FAILED)

        state['items'].append({
            'target': self.describe_item(item),
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
