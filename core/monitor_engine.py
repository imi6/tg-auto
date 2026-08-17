"""
监控引擎 - 应用观察者模式
负责协调各种监控器和处理消息事件
"""

import json
import asyncio
import pytz
from pathlib import Path
from typing import List, Dict, Set, Optional
from datetime import datetime
from telethon import events
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from models import MessageEvent, TelegramMessage, MessageSender, Account
from monitors import BaseMonitor, MonitorResult, monitor_factory
from utils.singleton import Singleton
from utils.logger import get_logger


class MonitorEngine(metaclass=Singleton):

    def __init__(self):
        self.monitors: Dict[str, List[BaseMonitor]] = {}
        self.processed_messages: Set[str] = set()
        self.scheduled_messages: List[Dict] = []
        self._running_scheduled_jobs: Set[str] = set()
        self.logger = get_logger(__name__)
        self.monitors_file = Path("data/monitor_configs.json")
        self.scheduled_messages_file = Path("data/scheduled_messages.json")

        self.scheduler = None
        self._scheduler_started = False

        self._load_monitors()
        self._load_scheduled_messages()

    def _ensure_scheduler_started(self):
        if not self._scheduler_started:
            try:
                loop = asyncio.get_running_loop()
                if not self.scheduler:
                    self.scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Shanghai'))

                if not self.scheduler.running:
                    self.scheduler.start()
                    self.logger.info("调度器已启动")

                self._scheduler_started = True

                self._restore_scheduled_jobs()

            except RuntimeError:
                self.logger.debug("事件循环尚未启动，调度器将延后启动")

    @staticmethod
    def build_schedule_trigger(message_config: dict):
        """按任务自己的模式生成触发器，间隔任务不能拿去当 Cron 解析"""
        schedule_mode = message_config.get('schedule_mode', 'cron')
        cron_expr = message_config.get('cron') or message_config.get('schedule') or ''
        timezone = pytz.timezone('Asia/Shanghai')

        if not cron_expr:
            raise ValueError('定时规则为空')

        parts = cron_expr.split()
        # 间隔任务存的是「小时 分钟」两段数字；编辑时哪怕 schedule_mode 丢了也不能当 Cron 解析
        use_interval = schedule_mode == 'interval' or (
            len(parts) == 2 and parts[0].lstrip('-').isdigit() and parts[1].lstrip('-').isdigit()
        )
        if use_interval:
            hours = int(parts[0])
            minutes = int(parts[1])
            if hours < 0 or minutes < 0 or (hours == 0 and minutes == 0):
                raise ValueError(f'间隔时间无效: {cron_expr}')
            return IntervalTrigger(hours=hours, minutes=minutes, timezone=timezone)

        return CronTrigger.from_crontab(cron_expr, timezone=timezone)

    def schedule_job(self, job_id: str, message_config: Optional[dict] = None) -> bool:
        """把一条定时消息挂到调度器上，已存在则覆盖"""
        self._ensure_scheduler_started()
        if not self.scheduler or not self.scheduler.running:
            self.logger.warning(f"调度器未启动，无法挂载任务: {job_id}")
            return False

        config = message_config
        if config is None:
            config = next((msg for msg in self.scheduled_messages if msg.get('job_id') == job_id), None)
        if not config:
            raise ValueError(f'未找到定时消息: {job_id}')

        trigger = self.build_schedule_trigger(config)
        self.scheduler.add_job(
            self._execute_scheduled_message,
            trigger,
            id=job_id,
            args=[job_id],
            replace_existing=True
        )
        return True

    def unschedule_job(self, job_id: str) -> bool:
        if not self.scheduler or not self.scheduler.running:
            return False
        try:
            self.scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def _restore_scheduled_jobs(self):
        if not self.scheduler or not self.scheduler.running:
            return

        restored_count = 0
        for message in self.scheduled_messages:
            job_id = message.get('job_id')
            if not job_id or not message.get('active', True):
                continue
            if not (message.get('cron') or message.get('schedule')):
                continue

            try:
                self.schedule_job(job_id, message)
                restored_count += 1
            except Exception as scheduler_error:
                self.logger.error(f"恢复调度任务失败 {job_id}: {scheduler_error}")

        if restored_count > 0:
            self.logger.info(f"恢复 {restored_count} 个调度任务")

    def _load_monitors(self):
        old_config_file = Path("data/monitor.config")
        if old_config_file.exists():
            self.logger.warning("检测到旧版本的monitor.config文件，正在尝试删除...")
            try:
                old_config_file.unlink()
                self.logger.info("已删除旧版本的monitor.config文件")
            except Exception as e:
                self.logger.error(f"删除旧版本monitor.config文件失败: {e}")
                self.logger.warning("建议手动删除data/monitor.config文件后重新启动程序")
                return

        if not self.monitors_file.exists():
            self.logger.info("监控器配置文件不存在，跳过加载")
            return

        try:
            with open(self.monitors_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for account_id, monitors_data in data.items():
                for monitor_data in monitors_data:
                    try:
                        monitor_type = monitor_data.get('type')
                        config_data = monitor_data.get('config', {})

                        if monitor_type == 'keyword':
                            from models.config import KeywordConfig, MatchType
                            config = KeywordConfig(
                                keyword=config_data.get('keyword', ''),
                                match_type=MatchType(config_data.get('match_type', 'partial')),
                                chats=config_data.get('chats', []),
                                users=config_data.get('users', []),
                                blocked_users=config_data.get('blocked_users', []),
                                blocked_channels=config_data.get('blocked_channels', []),
                                blocked_bots=config_data.get('blocked_bots', []),
                                bot_ids=config_data.get('bot_ids', []),
                                channel_ids=config_data.get('channel_ids', []),
                                group_ids=config_data.get('group_ids', []),
                                email_notify=config_data.get('email_notify', False),
                                auto_forward=config_data.get('auto_forward', False),
                                forward_targets=config_data.get('forward_targets', []),
                                enhanced_forward=config_data.get('enhanced_forward', False),
                                reply_enabled=config_data.get('reply_enabled', False),
                                reply_texts=config_data.get('reply_texts', []),
                                reply_delay_min=config_data.get('reply_delay_min', 0),
                                reply_delay_max=config_data.get('reply_delay_max', 5),
                                reply_mode=config_data.get('reply_mode', 'reply'),
                                max_executions=config_data.get('max_executions'),
                                priority=config_data.get('priority', 50),
                                execution_mode=config_data.get('execution_mode', 'merge'),
                                log_file=config_data.get('log_file')
                            )
                            monitor = monitor_factory.create_monitor(config)
                            if monitor:
                                self.add_monitor(account_id, monitor)

                        elif monitor_type == 'file':
                            from models.config import FileConfig
                            config = FileConfig(
                                file_extension=config_data.get('file_extension', ''),
                                chats=config_data.get('chats', []),
                                users=config_data.get('users', []),
                                blocked_users=config_data.get('blocked_users', []),
                                blocked_channels=config_data.get('blocked_channels', []),
                                blocked_bots=config_data.get('blocked_bots', []),
                                bot_ids=config_data.get('bot_ids', []),
                                channel_ids=config_data.get('channel_ids', []),
                                group_ids=config_data.get('group_ids', []),
                                save_folder=config_data.get('save_folder'),
                                min_size=config_data.get('min_size'),
                                max_size=config_data.get('max_size'),
                                email_notify=config_data.get('email_notify', False),
                                auto_forward=config_data.get('auto_forward', False),
                                forward_targets=config_data.get('forward_targets', []),
                                enhanced_forward=config_data.get('enhanced_forward', False),
                                max_download_size_mb=config_data.get('max_download_size_mb'),
                                max_executions=config_data.get('max_executions'),
                                priority=config_data.get('priority', 50),
                                execution_mode=config_data.get('execution_mode', 'merge'),
                                log_file=config_data.get('log_file')
                            )
                            monitor = monitor_factory.create_monitor(config)
                            if monitor:
                                self.add_monitor(account_id, monitor)
                                self.logger.info(f"加载文件监控器: {config.file_extension}")

                        elif monitor_type == 'ai':
                            from models.config import AIMonitorConfig
                            config = AIMonitorConfig(
                                ai_prompt=config_data.get('ai_prompt', ''),
                                confidence_threshold=config_data.get('confidence_threshold', 0.7),
                                ai_model=config_data.get('ai_model', 'gpt-4o'),
                                chats=config_data.get('chats', []),
                                users=config_data.get('users', []),
                                blocked_users=config_data.get('blocked_users', []),
                                blocked_channels=config_data.get('blocked_channels', []),
                                blocked_bots=config_data.get('blocked_bots', []),
                                bot_ids=config_data.get('bot_ids', []),
                                channel_ids=config_data.get('channel_ids', []),
                                group_ids=config_data.get('group_ids', []),
                                email_notify=config_data.get('email_notify', False),
                                auto_forward=config_data.get('auto_forward', False),
                                forward_targets=config_data.get('forward_targets', []),
                                enhanced_forward=config_data.get('enhanced_forward', False),
                                reply_enabled=config_data.get('reply_enabled', False),
                                reply_texts=config_data.get('reply_texts', []),
                                reply_delay_min=config_data.get('reply_delay_min', 0),
                                reply_delay_max=config_data.get('reply_delay_max', 5),
                                reply_mode=config_data.get('reply_mode', 'reply'),
                                max_executions=config_data.get('max_executions'),
                                priority=config_data.get('priority', 50),
                                execution_mode=config_data.get('execution_mode', 'merge'),
                                log_file=config_data.get('log_file')
                            )
                            monitor = monitor_factory.create_monitor(config)
                            if monitor:
                                self.add_monitor(account_id, monitor)
                                self.logger.info(f"加载AI监控器: {config.ai_prompt[:50]}...")

                        elif monitor_type == 'allmessages' or monitor_type == 'all_messages':
                            from models.config import AllMessagesConfig
                            config = AllMessagesConfig(
                                chat_id=config_data.get('chat_id', 0),
                                chats=config_data.get('chats', []),
                                users=config_data.get('users', []),
                                blocked_users=config_data.get('blocked_users', []),
                                blocked_channels=config_data.get('blocked_channels', []),
                                blocked_bots=config_data.get('blocked_bots', []),
                                bot_ids=config_data.get('bot_ids', []),
                                channel_ids=config_data.get('channel_ids', []),
                                group_ids=config_data.get('group_ids', []),
                                email_notify=config_data.get('email_notify', False),
                                auto_forward=config_data.get('auto_forward', False),
                                forward_targets=config_data.get('forward_targets', []),
                                enhanced_forward=config_data.get('enhanced_forward', False),
                                reply_enabled=config_data.get('reply_enabled', False),
                                reply_texts=config_data.get('reply_texts', []),
                                reply_delay_min=config_data.get('reply_delay_min', 0),
                                reply_delay_max=config_data.get('reply_delay_max', 5),
                                reply_mode=config_data.get('reply_mode', 'reply'),
                                max_executions=config_data.get('max_executions'),
                                priority=config_data.get('priority', 50),
                                execution_mode=config_data.get('execution_mode', 'merge'),
                                log_file=config_data.get('log_file')
                            )
                            monitor = monitor_factory.create_monitor(config)
                            if monitor:
                                self.add_monitor(account_id, monitor)
                                self.logger.info(f"加载全量监控器: 聊天{config.chat_id}")

                        else:
                            self.logger.warning(f"未知的监控器类型: {monitor_type}")

                    except Exception as e:
                        self.logger.error(f"加载监控器配置失败: {e}")

        except Exception as e:
            self.logger.error(f"加载监控器文件失败: {e}")

    def _save_monitors(self):
        try:
            self.monitors_file.parent.mkdir(parents=True, exist_ok=True)

            monitors_data = {}
            for account_id, monitors in self.monitors.items():
                monitors_data[account_id] = []
                for monitor in monitors:
                    if hasattr(monitor, 'config'):
                        config = monitor.config
                        monitor_data = {
                            'type': monitor.__class__.__name__.replace('Monitor', '').lower(),
                            'config': {}
                        }

                        for attr in dir(config):
                            if not attr.startswith('_'):
                                value = getattr(config, attr)
                                if not callable(value) and isinstance(value, (str, int, float, bool, list, dict)):
                                    monitor_data['config'][attr] = value
                                elif hasattr(value, 'value'):
                                    monitor_data['config'][attr] = value.value

                        monitors_data[account_id].append(monitor_data)

            with open(self.monitors_file, 'w', encoding='utf-8') as f:
                json.dump(monitors_data, f, indent=2, ensure_ascii=False)

            self.logger.info(f"已保存监控器配置")

        except Exception as e:
            self.logger.error(f"保存监控器文件失败: {e}")

    async def start(self):
        try:
            self._ensure_scheduler_started()

            from core import AccountManager
            account_manager = AccountManager()

            for account in account_manager.list_accounts():
                if account.client and account.is_connected():
                    if account.monitor_active:
                        self.setup_event_handlers(account)
                        self.logger.info(f"为账号 {account.account_id} 启动监控")
                else:
                    if await account_manager.connect_account(account.account_id):
                        if account.monitor_active:
                            self.setup_event_handlers(account)
                            self.logger.info(f"为账号 {account.account_id} 启动监控")
                    else:
                        self.logger.warning(f"账号 {account.account_id} 未连接，跳过监控设置")

            self.logger.info("监控引擎启动完成")

        except Exception as e:
            self.logger.error(f"启动监控引擎失败: {e}")

    def add_monitor(self, account_id: str, monitor: BaseMonitor, monitor_key: str = None):
        if account_id not in self.monitors:
            self.monitors[account_id] = []

        if monitor_key:
            self.remove_monitor(account_id, monitor_key)

        self.monitors[account_id].append(monitor)

        self._save_monitors()

        self.logger.info(f"为账号 {account_id} 添加监控器: {monitor.__class__.__name__}")

    def remove_monitor(self, account_id: str, monitor_key: str = None, monitor_type: type = None) -> bool:
        if account_id not in self.monitors:
            return False

        monitors = self.monitors[account_id]
        original_count = len(monitors)

        if monitor_type:
            monitors[:] = [m for m in monitors if not isinstance(m, monitor_type)]
            return len(monitors) < original_count

        if monitor_key:
            try:
                if '_' in monitor_key:
                    parts = monitor_key.split('_')
                    if len(parts) >= 2 and parts[-1].isdigit():
                        index = int(parts[-1])
                        if 0 <= index < len(monitors):
                            monitors.pop(index)
                            self.logger.info(f"移除监控器: {monitor_key}")
                            return True

                monitor_type_name = monitor_key.split('_')[0]
                for i, monitor in enumerate(monitors):
                    if monitor.__class__.__name__ == monitor_type_name:
                        monitors.pop(i)
                        self.logger.info(f"移除监控器: {monitor_key}")
                        return True

            except (ValueError, IndexError) as e:
                self.logger.error(f"解析监控器键值失败: {e}")

        return False

    def get_monitors(self, account_id: str) -> List[BaseMonitor]:
        return self.monitors.get(account_id, [])

    def clear_monitors(self, account_id: str):
        if account_id in self.monitors:
            del self.monitors[account_id]
            self._save_monitors()
            self.logger.info(f"已清除账号 {account_id} 的所有监控器并保存配置")

    def remove_all_monitors(self, account_id: str):
        self.clear_monitors(account_id)

    async def process_message(self, message_event: MessageEvent, account: Account):
        if not self.monitors.get(account.account_id):
            return

        monitors_list = []
        for i, monitor in enumerate(self.monitors[account.account_id]):
            monitor_key = f"{monitor.__class__.__name__}_{i}"
            priority = getattr(monitor.config, 'priority', 50)
            execution_mode = getattr(monitor.config, 'execution_mode', 'merge')
            monitors_list.append((priority, monitor_key, monitor, execution_mode))

        monitors_list.sort(key=lambda x: x[0])

        await self._process_monitors_with_individual_modes(message_event, account, monitors_list)

    async def _process_monitors_with_individual_modes(self, message_event: MessageEvent, account: Account,
                                                      monitors_list: list):
        merge_monitors = []
        merge_actions = {
            'email_notify': False,
            'forward_targets': set(),
            'enhanced_forward': False,
            'log_files': set(),
            'reply_enabled': False,
            'reply_texts': [],
            'reply_delay_min': 0,
            'reply_delay_max': 0,
            'reply_mode': 'reply',
            'reply_content_type': 'custom',
            'ai_reply_prompt': '',
            'custom_actions': []
        }

        for priority, monitor_key, monitor, execution_mode in monitors_list:
            try:
                result = await monitor.process_message(message_event, account)

                if result.result == MonitorResult.MATCHED:
                    self.logger.info(f"✅ 监控器 {monitor_key} 匹配成功 [优先级:{priority}] [模式:{execution_mode}]")

                    if execution_mode == 'first_match':
                        self.logger.info(f"🎯 [首次匹配停止] {monitor_key} 匹配，执行动作后停止")
                        matched_monitors = [{
                            'key': monitor_key,
                            'monitor': monitor,
                            'result': result,
                            'priority': priority
                        }]
                        actions = self._collect_monitor_actions(monitor, monitor_key)
                        await self._execute_merged_actions(message_event, account, actions, matched_monitors)
                        return

                    elif execution_mode == 'all':
                        self.logger.info(f"🔄 [全部独立执行] {monitor_key} 匹配，独立执行动作")
                        matched_monitors = [{
                            'key': monitor_key,
                            'monitor': monitor,
                            'result': result,
                            'priority': priority
                        }]
                        actions = self._collect_monitor_actions(monitor, monitor_key)
                        await self._execute_merged_actions(message_event, account, actions, matched_monitors)

                    else:
                        self.logger.info(f"🔗 [合并模式] {monitor_key} 匹配，收集动作待合并")
                        merge_monitors.append({
                            'key': monitor_key,
                            'monitor': monitor,
                            'result': result,
                            'priority': priority
                        })

                        self._merge_monitor_actions(monitor, monitor_key, merge_actions)

            except Exception as e:
                self.logger.error(f"监控器 {monitor_key} 处理消息失败: {e}")

        if merge_monitors:
            self.logger.info(f"🔗 [合并执行] 共 {len(merge_monitors)} 个merge模式监控器，合并执行动作")
            await self._execute_merged_actions(message_event, account, merge_actions, merge_monitors)

    def _merge_monitor_actions(self, monitor, monitor_key: str, all_actions: dict):
        config = monitor.config

        if config.email_notify:
            all_actions['email_notify'] = True

        if config.auto_forward and config.forward_targets:
            all_actions['forward_targets'].update(config.forward_targets)
            if config.enhanced_forward:
                all_actions['enhanced_forward'] = True

        if config.log_file:
            all_actions['log_files'].add(config.log_file)

        if not all_actions['reply_enabled'] and hasattr(config, 'reply_enabled') and config.reply_enabled:
            all_actions['reply_enabled'] = True

            reply_content_type = getattr(config, 'reply_content_type', 'custom')
            if hasattr(reply_content_type, 'value'):
                reply_content_type = reply_content_type.value
            all_actions['reply_content_type'] = reply_content_type

            all_actions['ai_reply_prompt'] = getattr(config, 'ai_reply_prompt', '')

            if hasattr(monitor, 'get_dynamic_reply_content'):
                dynamic_reply_texts = monitor.get_dynamic_reply_content()
                if dynamic_reply_texts:
                    all_actions['reply_texts'] = dynamic_reply_texts
                    self.logger.debug(f"使用监控器 {monitor_key} 的动态回复内容: {len(dynamic_reply_texts)}条")
                else:
                    config_reply_texts = getattr(config, 'reply_texts', [])
                    if not config_reply_texts and hasattr(config, 'ai_reply_prompt') and getattr(config,
                                                                                                 'ai_reply_prompt'):
                        all_actions['reply_content_type'] = 'ai'
                        all_actions['ai_reply_prompt'] = getattr(config, 'ai_reply_prompt')
                    else:
                        all_actions['reply_texts'] = config_reply_texts
            else:
                all_actions['reply_texts'] = getattr(config, 'reply_texts', [])

            all_actions['reply_delay_min'] = getattr(config, 'reply_delay_min', 0)
            all_actions['reply_delay_max'] = getattr(config, 'reply_delay_max', 0)
            reply_mode_value = getattr(config, 'reply_mode', 'reply')
            if hasattr(reply_mode_value, 'value'):
                reply_mode_value = reply_mode_value.value
            all_actions['reply_mode'] = reply_mode_value

    def _collect_monitor_actions(self, monitor, monitor_key: str) -> dict:
        config = monitor.config
        actions = {
            'email_notify': config.email_notify,
            'forward_targets': set(config.forward_targets) if config.auto_forward else set(),
            'enhanced_forward': config.enhanced_forward if config.auto_forward else False,
            'log_files': {config.log_file} if config.log_file else set(),
            'reply_enabled': False,
            'reply_texts': [],
            'reply_delay_min': 0,
            'reply_delay_max': 0,
            'reply_mode': 'reply',
            'reply_content_type': 'custom',
            'ai_reply_prompt': '',
            'custom_actions': []
        }

        if hasattr(config, 'reply_enabled') and config.reply_enabled:
            actions['reply_enabled'] = True

            reply_content_type = getattr(config, 'reply_content_type', 'custom')
            if hasattr(reply_content_type, 'value'):
                reply_content_type = reply_content_type.value
            actions['reply_content_type'] = reply_content_type

            actions['ai_reply_prompt'] = getattr(config, 'ai_reply_prompt', '')

            if hasattr(monitor, 'get_dynamic_reply_content'):
                dynamic_reply_texts = monitor.get_dynamic_reply_content()
                if dynamic_reply_texts:
                    actions['reply_texts'] = dynamic_reply_texts
                else:
                    actions['reply_texts'] = getattr(config, 'reply_texts', [])
            else:
                actions['reply_texts'] = getattr(config, 'reply_texts', [])

            actions['reply_delay_min'] = getattr(config, 'reply_delay_min', 0)
            actions['reply_delay_max'] = getattr(config, 'reply_delay_max', 0)

            reply_mode_value = getattr(config, 'reply_mode', 'reply')
            if hasattr(reply_mode_value, 'value'):
                reply_mode_value = reply_mode_value.value
            actions['reply_mode'] = reply_mode_value

        return actions

    async def _execute_merged_actions(self, message_event: MessageEvent, account: Account,
                                      actions: dict, matched_monitors: list):

        message = message_event.message

        try:
            if actions['email_notify']:
                email_content = await self._build_enhanced_email_content(
                    message_event, account, matched_monitors
                )

                asyncio.create_task(self._send_email_notification_async(
                    subject=f"TG监控系统 - 检测到 {len(matched_monitors)} 个匹配",
                    content=email_content,
                    email_addresses=actions.get('email_addresses', []),
                    monitor_count=len(matched_monitors)
                ))

            if actions['forward_targets']:
                target_ids = [tid for tid in actions['forward_targets'] if tid != message.chat_id]

                if target_ids:
                    if actions['enhanced_forward']:
                        from services import EnhancedForwardService
                        service = EnhancedForwardService()
                        await service.forward_message_enhanced(
                            message=message,
                            account=account,
                            target_ids=target_ids
                        )
                        self.logger.info(f"增强转发消息到 {len(target_ids)} 个目标（去重后）")
                    else:
                        client = account.client
                        for target_id in target_ids:
                            try:
                                await client.forward_messages(target_id, [message.message_id], message.chat_id)
                                self.logger.info(f"转发消息到: {target_id}")
                            except Exception as e:
                                self.logger.error(f"转发消息到 {target_id} 失败: {e}")

            for log_file in actions['log_files']:
                try:
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(f"[{message.timestamp}] {message.text}\n")
                except Exception as e:
                    self.logger.error(f"写入日志文件 {log_file} 失败: {e}")

            if actions['reply_enabled']:
                import random

                delay = random.uniform(
                    actions['reply_delay_min'],
                    actions['reply_delay_max']
                ) if actions['reply_delay_max'] > actions['reply_delay_min'] else actions['reply_delay_min']

                if delay > 0:
                    await asyncio.sleep(delay)

                reply_text = ""
                reply_content_type = actions.get('reply_content_type', 'custom')

                if reply_content_type == 'ai' and actions.get('ai_reply_prompt'):
                    from services import AIService
                    ai_service = AIService()

                    if ai_service.is_configured():
                        ai_prompt = f"{actions['ai_reply_prompt']}\n\n原始消息: {message.text or '(非文本消息)'}"

                        ai_response = await ai_service.get_chat_completion([
                            {"role": "user", "content": ai_prompt}
                        ])

                        if ai_response:
                            reply_text = ai_response.strip()
                        else:
                            self.logger.warning("AI服务返回空结果，跳过回复")
                            return
                    else:
                        self.logger.warning("AI服务未配置，跳过AI回复")
                        return
                elif actions['reply_texts']:
                    # NOSONAR - 用于随机选择回复文本以模拟人类行为，不需要密码学安全性
                    reply_text = random.choice(actions['reply_texts'])  # NOSONAR
                else:
                    self.logger.debug("没有可用的回复内容，跳过回复")
                    return

                if not reply_text:
                    self.logger.debug("回复内容为空，跳过回复")
                    return

                client = account.client
                reply_mode = actions.get('reply_mode', 'reply')

                delay_info = f"延迟:{delay:.2f}s" if delay > 0 else "即时"
                reply_preview = reply_text[:30] + "..." if len(reply_text) > 30 else reply_text
                mode_info = "直接发送" if reply_mode == 'send' else "回复消息"

                triggered_monitors = []
                for match in matched_monitors:
                    monitor = match['monitor']
                    monitor_type = monitor.__class__.__name__.replace('Monitor', '')

                    if hasattr(monitor, '_get_monitor_type_info'):
                        type_info = await monitor._get_monitor_type_info()
                    else:
                        type_info = ""

                    triggered_monitors.append(f"{monitor_type}{type_info}")

                monitors_info = " | ".join(triggered_monitors) if len(triggered_monitors) > 1 else triggered_monitors[0]

                try:
                    if reply_mode == 'send':
                        await client.send_message(message.chat_id, reply_text)
                        self.logger.info(
                            f"✅ [{monitors_info}] 频道:{message.chat_id} 发送者:{message.sender.id if message.sender else 'N/A'} [{mode_info}] [{delay_info}] 回复:\"{reply_preview}\"")
                    else:
                        await client.send_message(
                            message.chat_id,
                            reply_text,
                            reply_to=message.message_id
                        )
                        self.logger.info(
                            f"✅ [{monitors_info}] 频道:{message.chat_id} 发送者:{message.sender.id if message.sender else 'N/A'} [{mode_info}] [{delay_info}] 回复:\"{reply_preview}\"")
                except Exception as reply_error:
                    self.logger.error(f"❌ [{monitors_info}] 频道:{message.chat_id} 回复失败: {reply_error}")
                    try:
                        await client.send_message(message.chat_id, reply_text)
                        self.logger.info(
                            f"✅ [{monitors_info}] 频道:{message.chat_id} 发送者:{message.sender.id if message.sender else 'N/A'} [回退-直接发送] [{delay_info}] 回复:\"{reply_preview}\"")
                    except Exception as fallback_error:
                        self.logger.error(f"❌ [{monitors_info}] 频道:{message.chat_id} 回退发送失败: {fallback_error}")

            for match in matched_monitors:
                config = match['monitor'].config
                old_count = config.execution_count
                old_active = config.active

                config.increment_execution()
                new_count = config.execution_count

                self.logger.debug(
                    f"监控器 {match['key']} 执行计数更新: {old_count} → {new_count}/{config.max_executions or '无限制'}")

                if config.is_execution_limit_reached():
                    config.active = False
                    config.reset_execution_count()
                    self.logger.info(f"🛑 监控器 {match['key']} 已执行 {config.max_executions} 次，已暂停并重置执行计数")
                    self._save_monitors()

        except Exception as e:
            self.logger.error(f"执行合并动作时出错: {e}")

    async def _build_enhanced_email_content(self, message_event: MessageEvent, account: Account,
                                            matched_monitors: list) -> str:
        """
        构建增强的邮件通知内容

        Args:
            message_event: 消息事件
            account: 账号信息
            matched_monitors: 匹配的监控器列表

        Returns:
            增强的邮件内容
        """
        message = message_event.message

        chat_info = "未知聊天"
        try:
            if hasattr(account, 'client') and account.client:
                entity = await account.client.get_entity(message.chat_id)
                if hasattr(entity, 'title'):
                    chat_info = f"{entity.title} (ID: {message.chat_id})"
                elif hasattr(entity, 'username'):
                    chat_info = f"@{entity.username} (ID: {message.chat_id})"
                else:
                    chat_info = f"聊天ID: {message.chat_id}"
        except Exception:
            chat_info = f"聊天ID: {message.chat_id}"

        sender_info = "未知发送者"
        if message.sender:
            sender_name = message.sender.full_name or "未知用户"
            sender_username = f"@{message.sender.username}" if message.sender.username else ""
            sender_info = f"{sender_name} {sender_username} (ID: {message.sender.id})".strip()

        email_content = "=" * 50 + "\n"
        email_content += "📢 TG监控系统 - 消息匹配通知\n"
        email_content += "=" * 50 + "\n\n"

        email_content += "📍 基本信息：\n"
        email_content += f"⏰ 时间：{message.timestamp}\n"
        email_content += f"👤 发送者：{sender_info}\n"
        email_content += f"💬 聊天：{chat_info}\n"
        email_content += f"🎯 监控账号：{account.account_id}\n\n"

        email_content += "📝 消息内容：\n"
        if message.text:
            message_text = message.text[:500] + "..." if len(message.text) > 500 else message.text
            email_content += f'"{message_text}"\n\n'
        else:
            email_content += "[无文字内容]\n\n"

        email_content += "📄 消息类型：\n"
        if message.media and message.media.has_media:
            email_content += f"📎 媒体类型：{message.media.media_type}\n"
            if message.media.file_name:
                email_content += f"📁 文件名：{message.media.file_name}\n"
            if message.media.file_size:
                email_content += f"📐 文件大小：{message.media.file_size / 1024 / 1024:.2f} MB\n"
        else:
            email_content += "📄 普通文字消息\n"

        if message.has_buttons:
            email_content += f"🔘 包含按钮：{', '.join(message.button_texts)}\n"

        if message.is_forwarded:
            email_content += "🔄 转发消息\n"

        email_content += "\n"

        email_content += "🎯 匹配的监控器：\n"
        for i, match in enumerate(matched_monitors, 1):
            monitor = match['monitor']
            monitor_type = monitor.__class__.__name__.replace('Monitor', '')

            email_content += f"{i}. 【{monitor_type}监控器】\n"

            if hasattr(monitor, 'config'):
                config = monitor.config

                if monitor_type == 'Keyword':
                    keyword = getattr(config, 'keyword', '未知')
                    match_type = getattr(config, 'match_type', '未知')
                    email_content += f"   🔍 关键词：{keyword}\n"
                    email_content += f"   📋 匹配类型：{match_type}\n"

                elif monitor_type == 'AI':
                    ai_prompt = getattr(config, 'ai_prompt', '未知')[:100]
                    email_content += f"   🤖 AI提示词：{ai_prompt}...\n"

                elif monitor_type == 'File':
                    file_ext = getattr(config, 'file_extension', '未知')
                    email_content += f"   📄 文件类型：{file_ext}\n"

                elif monitor_type == 'AllMessages':
                    email_content += f"   📊 全量监控\n"

                execution_count = getattr(config, 'execution_count', 0)
                max_executions = getattr(config, 'max_executions', None)
                if max_executions:
                    email_content += f"   📈 执行次数：{execution_count}/{max_executions}\n"
                else:
                    email_content += f"   📈 执行次数：{execution_count}\n"

            email_content += "\n"

        email_content += "-" * 30 + "\n"
        email_content += "🔧 系统信息：\n"
        email_content += f"📧 此邮件由 TG监控系统 自动发送\n"
        email_content += f"⚙️ 监控引擎版本：v2.0\n"

        return email_content

    async def process_message_event(self, event: events.NewMessage, account: Account):
        try:
            if not account.monitor_active:
                return

            sender = await event.get_sender()
            if not sender:
                sender = self._create_pseudo_sender(event)

            message_sender = MessageSender.from_telethon_entity(sender)

            telegram_message = TelegramMessage.from_telethon_event(event, message_sender)

            if event.message.media:
                self.logger.debug(f"消息包含媒体: {type(event.message.media).__name__}")
                if hasattr(event.message.media, 'document') and event.message.media.document:
                    self.logger.debug(f"消息包含文档")
                    if hasattr(event.message.media.document, 'attributes'):
                        for attr in event.message.media.document.attributes:
                            if hasattr(attr, 'file_name'):
                                self.logger.debug(f"文件名: {attr.file_name}")
                                break

            message_event = MessageEvent(
                account_id=account.account_id,
                message=telegram_message
            )

            if self._is_message_processed(message_event):
                return

            self._mark_message_processed(message_event)

            await self.process_message(message_event, account)

        except Exception as e:
            self.logger.error(f"处理消息事件时出错: {e}")

    def _create_pseudo_sender(self, event):

        class PseudoSender:
            def __init__(self, event):
                self.id = event.chat_id
                self.username = ""
                self.first_name = event.message.post_author or "未知"
                self.last_name = ""
                self.bot = False
                self.title = event.message.post_author

        return PseudoSender(event)

    def _is_message_processed(self, message_event: MessageEvent) -> bool:
        return message_event.unique_id in self.processed_messages

    def _mark_message_processed(self, message_event: MessageEvent):
        self.processed_messages.add(message_event.unique_id)

        if len(self.processed_messages) > 10000:
            old_messages = list(self.processed_messages)[:5000]
            for msg_id in old_messages:
                self.processed_messages.discard(msg_id)

    def _log_processing_results(self, message_event: MessageEvent, results: List):
        matched_count = 0
        error_count = 0

        for result in results:
            if isinstance(result, Exception):
                error_count += 1
            elif hasattr(result, 'result') and result.result == MonitorResult.MATCHED:
                matched_count += 1

        if matched_count > 0 or error_count > 0:
            self.logger.info(
                f"消息处理完成: 聊天={message_event.message.chat_id}, "
                f"匹配={matched_count}, 错误={error_count}"
            )

    def setup_event_handlers(self, account: Account):
        if not account.client:
            return

        account.client.add_event_handler(
            lambda event: self.process_message_event(event, account),
            events.NewMessage()
        )

        self.logger.info(f"为账号 {account.account_id} 设置事件处理器")

    def get_statistics(self) -> Dict[str, int]:
        return {
            "total_accounts": len(self.monitors),
            "total_monitors": sum(len(monitors) for monitors in self.monitors.values()),
            "processed_messages": len(self.processed_messages)
        }

    def add_scheduled_message(self, config):
        try:
            target_ids = list(getattr(config, 'target_ids', None) or [config.target_id])
            
            message_dict = {
                'job_id': config.job_id,
                'target_id': target_ids[0],
                'channel_id': target_ids[0],
                'target_ids': target_ids,
                'send_interval': getattr(config, 'send_interval', 5),
                'precheck': getattr(config, 'precheck', True),
                'message': config.message,
                'cron': config.cron,
                'schedule': config.cron,
                'account_id': config.account_id,
                'random_offset': getattr(config, 'random_offset', 0),
                'random_delay': getattr(config, 'random_offset', 0),
                'delete_after_sending': getattr(config, 'delete_after_sending', False),
                'delete_after_send': getattr(config, 'delete_after_sending', False),
                'max_executions': getattr(config, 'max_executions', None),
                'execution_count': 0,
                'created_at': str(config.created_at) if hasattr(config, 'created_at') else None,
                'enabled': True,
                'active': True,
                'use_ai': getattr(config, 'use_ai', False),
                'ai_prompt': getattr(config, 'ai_prompt', None),
                'ai_model': getattr(config, 'ai_model', 'gpt-4o'),
                'schedule_mode': getattr(config, 'schedule_mode', 'cron')
            }

            self.scheduled_messages.append(message_dict)

            self._save_scheduled_messages()

            self.logger.info(f"添加定时消息: {config.job_id}")

            try:
                self.schedule_job(config.job_id, message_dict)
                self.logger.info(f"已启动定时任务: {config.job_id}")
            except Exception as scheduler_error:
                self.logger.error(f"添加调度任务失败: {scheduler_error}")

        except Exception as e:
            self.logger.error(f"添加定时消息失败: {e}")

    def get_scheduled_messages(self):
        return self.scheduled_messages

    @staticmethod
    def get_message_targets(message_config: dict) -> List[int]:
        """取出任务的目标列表，兼容只有单个 target_id 的旧配置"""
        raw_targets = message_config.get('target_ids')
        if not raw_targets:
            raw_targets = [message_config.get('target_id') or message_config.get('channel_id')]

        targets = []
        for raw in raw_targets:
            if raw in (None, ''):
                continue
            try:
                target = int(raw)
            except (TypeError, ValueError):
                continue
            if target not in targets:
                targets.append(target)

        return targets

    def _record_send_result(self, message_config: Optional[dict], job_id: str, status: str,
                            message: str = '', error: Optional[str] = None,
                            stage: Optional[str] = None, targets: Optional[dict] = None):
        """记录一次发送结果，并把最近状态回写到任务上供列表展示"""
        from core.send_record_store import SendRecordStore

        config = message_config or {}
        record = SendRecordStore().add(
            job_id=job_id,
            status=status,
            account_id=config.get('account_id'),
            target_id=config.get('target_id'),
            message=message,
            error=error,
            stage=stage,
            targets=targets
        )

        if message_config is not None:
            message_config['last_run_at'] = record['time']
            message_config['last_status'] = status
            message_config['last_error'] = error

        return record

    @staticmethod
    def _summarize_failures(summary: dict) -> Optional[str]:
        """把失败明细压成一句话，方便直接显示在列表里"""
        failures = summary.get('failures') or []
        if not failures:
            return None

        first = failures[0]
        text = f"{first['target_id']}: {first['error']}"
        if summary['failed'] > 1:
            text += f"（另有 {summary['failed'] - 1} 个目标失败）"

        return text

    @staticmethod
    def _is_spamblock_error(error: Exception) -> bool:
        """判断这个异常是不是账号级风控，而不是单个群的问题"""
        name = error.__class__.__name__
        if name in ('PeerFloodError', 'UserRestrictedError', 'UserDeactivatedBanError'):
            return True

        text = str(error).upper()
        return 'PEER_FLOOD' in text or 'USER_RESTRICTED' in text or 'INPUT_USER_DEACTIVATED' in text

    @staticmethod
    def _is_unsendable_error(error: Exception) -> Optional[str]:
        """单个群发不出去，换下一个目标即可，不必记成整轮失败"""
        name = error.__class__.__name__
        text = (str(error) or '').lower()

        if name in ('ChatWriteForbiddenError', 'UserBannedInChannelError', 'ChatGuestSendForbiddenError'):
            return '当前账号无权在该群发言'
        if name in ('ChannelPrivateError', 'ChatForbiddenError'):
            return '目标为私有群组且当前账号不在其中'
        if 'cannot send plain results' in text:
            return '该群开启了话题，不能直接往群里发普通消息'
        if 'channel_private' in text or 'chat_forbidden' in text:
            return '目标为私有群组且当前账号不在其中'
        if 'chat_write_forbidden' in text or "can't write in this chat" in text:
            return '当前账号无权在该群发言'

        return None

    # 这些情况短期内不会自己变好，继续留在任务里只会每轮都占一条跳过
    _PERMANENT_SKIP_HINTS = (
        '全员禁言',
        '当前账号在该群被禁言',
        '已退出',
        '被移出',
        '私有群组',
        '话题',
        '仅管理员',
        '找不到该目标',
        '无权在该群发言',
        'cannot send plain results',
    )

    @classmethod
    def _is_permanent_skip(cls, reason: str) -> bool:
        text = reason or ''
        lowered = text.lower()
        return any(hint.lower() in lowered or hint in text for hint in cls._PERMANENT_SKIP_HINTS)

    def _prune_unsendable_targets(self, message_config: dict, summary: dict) -> List[dict]:
        """把确定发不出去的目标从任务里摘掉，下一轮不再重复预检"""
        drop = {}
        for item in (summary.get('skips') or []) + (summary.get('failures') or []):
            target_id = item.get('target_id')
            reason = item.get('error') or ''
            if target_id in (None, '') or not self._is_permanent_skip(reason):
                continue
            try:
                drop[int(target_id)] = reason
            except (TypeError, ValueError):
                continue

        if not drop:
            return []

        targets = self.get_message_targets(message_config)
        kept = [target for target in targets if target not in drop]
        removed = [
            {'target_id': target, 'error': drop[target]}
            for target in targets if target in drop
        ]

        message_config['target_ids'] = kept
        if kept:
            message_config['target_id'] = kept[0]
            message_config['channel_id'] = kept[0]

        excluded = message_config.setdefault('excluded_targets', [])
        existing = set()
        for item in excluded:
            try:
                existing.add(int(item.get('target_id')))
            except (TypeError, ValueError):
                continue

        now = datetime.now().isoformat(timespec='seconds')
        for item in removed:
            if item['target_id'] in existing:
                continue
            excluded.append({**item, 'removed_at': now})

        summary['removed'] = len(removed)
        summary['removed_targets'] = removed
        return removed

    async def _broadcast_to_targets(self, job_id: str, message_config: dict, account,
                                    targets: List[int], message_text: str) -> dict:
        """依次把消息发往所有目标，返回本轮汇总

        目标可能成百上千，因此逐个发送、逐个记录失败原因，单个目标出错不影响其余目标。
        """
        from core.account_health_store import AccountHealthStore
        from services.precheck_service import PrecheckService

        interval = message_config.get('send_interval', 5)
        try:
            interval = max(0.0, float(interval))
        except (TypeError, ValueError):
            interval = 5.0

        precheck_enabled = message_config.get('precheck', True)
        precheck = PrecheckService() if precheck_enabled else None

        summary = {'total': len(targets), 'success': 0, 'failed': 0, 'skipped': 0,
                   'failures': [], 'skips': []}
        sent_any = False

        for index, target_id in enumerate(targets):
            # 群发途中被暂停或删除时立即停手，避免继续骚扰剩余群组
            if not message_config.get('active', True):
                self.logger.warning(f"定时消息在群发途中被暂停，剩余 {len(targets) - index} 个目标未发送: {job_id}")
                summary['stopped'] = True
                break

            if precheck:
                # 预检发不出去的目标直接跳过，省掉一次必然失败的发送请求
                check = await precheck.check_target(account.client, target_id)
                if PrecheckService.is_blocking(check['code']):
                    self.logger.info(f"⏭️ 跳过目标 {target_id}: {check['reason']}")
                    self._collect_skip(summary, target_id, check['reason'])
                    continue

            if sent_any and interval > 0:
                await asyncio.sleep(interval)

            try:
                # send_message 内部会解析实体，无需额外 get_entity，省掉一半 API 调用
                await account.client.send_message(target_id, message_text)
                summary['success'] += 1
                sent_any = True

            except Exception as send_error:
                wait_seconds = getattr(send_error, 'seconds', None)
                if isinstance(wait_seconds, int) and wait_seconds > 0:
                    # FloodWait：等满再重试一次，超过 5 分钟就放弃本轮，留到下次触发
                    if wait_seconds > 300:
                        self.logger.error(f"⛔ 触发限流需等待 {wait_seconds} 秒，中止本轮群发: {job_id}")
                        self._collect_failure(summary, target_id, f"触发限流，需等待 {wait_seconds} 秒，已中止本轮")
                        summary['stopped'] = True
                        break

                    self.logger.warning(f"⏳ 触发限流，等待 {wait_seconds} 秒后重试目标 {target_id}")
                    await asyncio.sleep(wait_seconds + 1)
                    try:
                        await account.client.send_message(target_id, message_text)
                        summary['success'] += 1
                        sent_any = True
                        continue
                    except Exception as retry_error:
                        send_error = retry_error

                reason = str(send_error) or send_error.__class__.__name__

                skip_reason = self._is_unsendable_error(send_error)
                if skip_reason:
                    self.logger.info(f"⏭️ 跳过目标 {target_id}: {skip_reason}")
                    self._collect_skip(summary, target_id, skip_reason)
                    continue

                if self._is_spamblock_error(send_error):
                    # 账号级风控，继续发只会加重处罚，立刻标记并中止本轮
                    account_id = message_config.get('account_id')
                    if account_id:
                        AccountHealthStore().mark_limited(
                            account_id, f"群发时触发风控: {reason}", source='error'
                        )
                    self.logger.error(f"⛔ 账号 {account_id} 触发风控，中止本轮群发: {reason}")
                    self._collect_failure(summary, target_id, f"账号触发风控: {reason}")
                    summary['stopped'] = True
                    summary['account_limited'] = True
                    break

                self.logger.error(f"❌ 发送失败 {target_id}: {reason}")
                self._collect_failure(summary, target_id, reason)

        return summary

    @staticmethod
    def _collect_failure(summary: dict, target_id: int, reason: str):
        """累计失败数，明细只留前 50 条，避免上千目标撑爆记录文件"""
        summary['failed'] += 1
        if len(summary['failures']) < 50:
            summary['failures'].append({'target_id': target_id, 'error': reason})

    @staticmethod
    def _collect_skip(summary: dict, target_id: int, reason: str):
        """预检未通过的目标同样只留前 50 条明细"""
        summary['skipped'] += 1
        if len(summary['skips']) < 50:
            summary['skips'].append({'target_id': target_id, 'error': reason})

    async def run_scheduled_message_now(self, job_id: str):
        """立刻执行一轮，不走 Cron / 间隔，也不算进执行次数，方便测试"""
        message_config = next((msg for msg in self.scheduled_messages if msg.get('job_id') == job_id), None)
        if not message_config:
            raise ValueError('未找到定时消息')
        if job_id in self._running_scheduled_jobs:
            raise RuntimeError('上一轮群发尚未结束')

        message_config['_run_now'] = True
        try:
            self.logger.info(f"立即发送定时消息: {job_id}")
            await self._execute_scheduled_message(job_id)
        finally:
            message_config.pop('_run_now', None)

    async def _execute_scheduled_message(self, job_id: str):
        message_config = None
        try:
            for msg in self.scheduled_messages:
                if msg['job_id'] == job_id:
                    message_config = msg
                    break

            if not message_config:
                self.logger.error(f"未找到定时消息配置: {job_id}")
                self._record_send_result(None, job_id, 'failed', error="未找到定时消息配置", stage='config')
                return

            run_now = bool(message_config.get('_run_now'))

            if not message_config.get('active', True) and not run_now:
                self.logger.debug(f"定时消息已暂停，跳过执行: {job_id}")
                return

            # 群发几百上千个目标可能跑过下一次触发时间，重入会造成重复发送
            if job_id in self._running_scheduled_jobs:
                self.logger.warning(f"上一轮群发尚未结束，跳过本次触发: {job_id}")
                self._record_send_result(
                    message_config, job_id, 'skipped',
                    error="上一轮群发尚未结束，本次触发已跳过", stage='running'
                )
                self._save_scheduled_messages()
                return

            max_executions = message_config.get('max_executions')
            execution_count = message_config.get('execution_count', 0)

            if max_executions and execution_count >= max_executions and not run_now:
                self.logger.info(f"定时消息达到执行次数限制，停止执行: {job_id}")
                try:
                    self.scheduler.remove_job(job_id)
                except:
                    pass
                return

            account_id = message_config.get('account_id')
            targets = self.get_message_targets(message_config)
            message_text = message_config.get('message', '')

            if not account_id or not targets:
                self.logger.error(f"定时消息配置不完整: account_id={account_id}, targets={targets}")
                self._record_send_result(
                    message_config, job_id, 'failed', message=message_text,
                    error="配置不完整，缺少账号或目标", stage='config'
                )
                self._save_scheduled_messages()
                return

            from core.account_manager import AccountManager
            account_manager = AccountManager()
            account = account_manager.get_account(account_id)

            if not account or not account.client:
                self.logger.error(f"账号未找到或未连接: {account_id}")
                self._record_send_result(
                    message_config, job_id, 'failed', message=message_text,
                    error=f"账号 {account_id} 未找到或未连接", stage='account'
                )
                self._save_scheduled_messages()
                return

            from core.account_health_store import AccountHealthStore
            health_store = AccountHealthStore()

            if health_store.is_limited(account_id):
                # 受限的号继续群发只会加重处罚，而且一条也发不出去
                reason = health_store.describe(account_id)
                self.logger.warning(f"⛔ 账号处于受限状态，跳过本轮群发: {account_id}（{reason}）")
                self._record_send_result(
                    message_config, job_id, 'skipped', message=message_text,
                    error=reason, stage='account'
                )
                self._save_scheduled_messages()
                return

            if message_config.get('use_ai', False) and message_config.get('ai_prompt'):
                try:
                    from services import AIService
                    ai_service = AIService()

                    if ai_service.is_configured():
                        self.logger.info(f"🤖 开始AI内容生成: {job_id}")

                        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        enhanced_prompt = f"""
当前时间: {current_time}
任务ID: {job_id}
目标聊天: {', '.join(str(t) for t in targets[:20])}

用户提示词: {message_config.get('ai_prompt')}

请根据上述信息生成合适的消息内容。要求：
1. 内容要符合用户的提示词要求
2. 可以包含当前时间信息（如果相关）
3. 内容要简洁明了，适合发送到Telegram
4. 直接返回消息内容，不要包含额外的解释

请生成消息内容：
"""

                        ai_response = await ai_service.get_chat_completion([
                            {"role": "user", "content": enhanced_prompt}
                        ])

                        if ai_response and ai_response.strip():
                            message_text = ai_response.strip()
                            self.logger.info(
                                f"✅ AI内容生成成功: \"{message_text[:50]}{'...' if len(message_text) > 50 else ''}\"")
                        else:
                            self.logger.warning(f"⚠️ AI返回空内容，跳过此次执行")
                            self._record_send_result(
                                message_config, job_id, 'skipped',
                                error="AI 返回空内容", stage='ai'
                            )
                            self._save_scheduled_messages()
                            return
                    else:
                        self.logger.error(f"❌ AI服务未配置，跳过此次执行")
                        self._record_send_result(
                            message_config, job_id, 'skipped',
                            error="AI 服务未配置", stage='ai'
                        )
                        self._save_scheduled_messages()
                        return

                except Exception as ai_error:
                    self.logger.error(f"❌ AI生成内容失败: {ai_error}")
                    self._record_send_result(
                        message_config, job_id, 'failed',
                        error=f"AI 生成内容失败: {ai_error}", stage='ai'
                    )
                    self._save_scheduled_messages()
                    return

            if not message_text or not message_text.strip():
                self.logger.error(f"❌ 消息内容为空，跳过发送: {job_id}")
                self._record_send_result(
                    message_config, job_id, 'skipped',
                    error="消息内容为空", stage='content'
                )
                self._save_scheduled_messages()
                return

            random_delay = 0 if run_now else message_config.get('random_delay', message_config.get('random_offset', 0))
            if random_delay > 0:
                import random  # NOSONAR - 用于模拟人类发送延迟，不需要密码学安全性
                actual_delay = random.randint(0, random_delay)  # NOSONAR
                self.logger.info(f"⏰ 定时消息延时发送: {actual_delay} 秒 (最大延时: {random_delay} 秒)")
                await asyncio.sleep(actual_delay)

            self._running_scheduled_jobs.add(job_id)
            try:
                summary = await self._broadcast_to_targets(
                    job_id, message_config, account, targets, message_text
                )
            finally:
                self._running_scheduled_jobs.discard(job_id)

            removed = self._prune_unsendable_targets(message_config, summary)
            if removed:
                self.logger.info(
                    f"已从任务 {job_id} 剔除 {len(removed)} 个发不出去的目标，剩余 {len(self.get_message_targets(message_config))} 个"
                )

            if summary['success'] == 0:
                # 一条都没发出去：全被预检拦下算跳过，真发失败才算失败
                all_skipped = summary['failed'] == 0 and summary.get('skipped')
                status = 'skipped' if all_skipped else 'failed'
                reason = (f"全部 {summary['skipped']} 个目标预检未通过"
                          if all_skipped else self._summarize_failures(summary))

                self.logger.error(f"❌ 定时消息未发出任何消息: {job_id}（共 {summary['total']} 个目标）")
                self._record_send_result(
                    message_config, job_id, status, message=message_text,
                    error=reason, stage='send', targets=summary
                )
                self._save_scheduled_messages()
                return

            old_count = execution_count
            if not run_now:
                message_config['execution_count'] = execution_count + 1
            new_count = message_config['execution_count']
            max_executions = message_config.get('max_executions')

            skipped = summary.get('skipped', 0)
            partial = summary['failed'] > 0 or skipped > 0
            reason = self._summarize_failures(summary)
            if skipped and not reason:
                reason = f"{skipped} 个目标预检未通过，已跳过"
            if removed:
                extra = f"已从任务中剔除 {len(removed)} 个无效目标"
                reason = f"{reason}；{extra}" if reason else extra

            self._record_send_result(
                message_config, job_id, 'partial' if partial else 'success', message=message_text,
                error=reason if partial else None,
                stage='send' if partial else None, targets=summary
            )
            
            self.logger.info(
                f"✅ 定时消息执行完成: {job_id}，成功 {summary['success']}/{summary['total']} 个目标"
                + (f"，失败 {summary['failed']} 个" if summary['failed'] else "")
                + (f"，跳过 {skipped} 个" if skipped else "")
            )
            self.logger.info(f"📊 执行统计更新: {old_count} → {new_count}/{max_executions or '无限制'} 次")
            if random_delay > 0:
                self.logger.info(f"⏰ 延时设置: {random_delay} 秒")

            self._save_scheduled_messages()

            if max_executions and message_config['execution_count'] >= max_executions:
                try:
                    if self.scheduler and self.scheduler.running:
                        try:
                            self.scheduler.pause_job(job_id)
                            self.logger.info(f"⏸️ 定时消息任务已暂停: {job_id}")
                        except Exception as pause_error:
                            self.scheduler.remove_job(job_id)
                            self.logger.warning(f"无法暂停任务，已移除: {job_id}")

                    message_config['active'] = False

                    self._save_scheduled_messages()
                    self.logger.info(f"🛑 定时消息已达到执行限制 ({max_executions} 次)，已暂停任务: {job_id}")
                except Exception as pause_error:
                    self.logger.error(f"暂停达到限制的定时任务失败: {pause_error}")
            else:
                self.logger.info(
                    f"📈 定时消息继续运行，剩余执行次数: {max_executions - message_config['execution_count'] if max_executions else '无限制'}")

            if message_config.get('delete_after_send', False):
                try:
                    pass
                except Exception as delete_error:
                    self.logger.error(f"删除消息失败: {delete_error}")

        except Exception as e:
            self.logger.error(f"执行定时消息失败 {job_id}: {e}")
            try:
                self._record_send_result(message_config, job_id, 'failed', error=str(e), stage='unknown')
                self._save_scheduled_messages()
            except Exception as record_error:
                self.logger.error(f"记录发送结果失败: {record_error}")

    def pause_account_scheduled_messages(self, account_id: str) -> int:
        """账号删除后停掉它名下的定时消息，避免下一轮还去找这个号"""
        paused = 0
        for message in self.scheduled_messages:
            if message.get('account_id') != account_id or not message.get('active', True):
                continue

            message['active'] = False
            job_id = message.get('job_id')
            if self.scheduler and self.scheduler.running and job_id:
                try:
                    self.scheduler.pause_job(job_id)
                except Exception:
                    try:
                        self.scheduler.remove_job(job_id)
                    except Exception as e:
                        self.logger.debug(f"暂停已删账号的定时任务失败 {job_id}: {e}")

            paused += 1

        if paused:
            self._save_scheduled_messages()
            self.logger.info(f"账号 {account_id} 已删除，暂停了 {paused} 条定时消息")

        return paused

    def remove_scheduled_message(self, job_id: str):
        try:
            original_count = len(self.scheduled_messages)
            self.scheduled_messages = [msg for msg in self.scheduled_messages if msg.get('job_id') != job_id]

            if len(self.scheduled_messages) < original_count:
                if self.scheduler and self.scheduler.running:
                    try:
                        self.scheduler.remove_job(job_id)
                        self.logger.info(f"从调度器中移除任务: {job_id}")
                    except Exception as scheduler_error:
                        self.logger.warning(f"从调度器移除任务失败 {job_id}: {scheduler_error}")
                else:
                    self.logger.debug(f"调度器未运行，跳过移除任务: {job_id}")

                self._save_scheduled_messages()
                self.logger.info(f"删除定时消息: {job_id}")

                return True
            else:
                self.logger.warning(f"未找到定时消息: {job_id}")
                return False

        except Exception as e:
            self.logger.error(f"删除定时消息失败: {e}")
            return False

    def _save_scheduled_messages(self):
        try:
            self.scheduled_messages_file.parent.mkdir(parents=True, exist_ok=True)

            payload = []
            for message in self.scheduled_messages:
                item = dict(message)
                item.pop('_run_now', None)
                payload.append(item)

            with open(self.scheduled_messages_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

            self.logger.info(f"已保存 {len(self.scheduled_messages)} 条定时消息")

        except Exception as e:
            self.logger.error(f"保存定时消息文件失败: {e}")

    def _load_scheduled_messages(self):
        if not self.scheduled_messages_file.exists():
            self.logger.info("定时消息文件不存在，跳过加载")
            return

        try:
            with open(self.scheduled_messages_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.scheduled_messages = data
            self.logger.info(f"已加载 {len(self.scheduled_messages)} 条定时消息")

        except Exception as e:
            self.logger.error(f"加载定时消息文件失败: {e}")

    async def _send_email_notification(self, subject: str, content: str, email_addresses: list = None):
        if not email_addresses:
            try:
                from utils.config import config
                default_emails = []
                if hasattr(config, 'EMAIL_TO') and config.EMAIL_TO:
                    default_emails = [config.EMAIL_TO]
                elif hasattr(config, 'email_to') and config.email_to:
                    default_emails = [config.email_to]

                if not default_emails:
                    self.logger.warning("未配置邮件接收地址，跳过邮件通知")
                    return

                email_addresses = default_emails
            except Exception as e:
                self.logger.error(f"读取邮件配置失败: {e}")
                return

        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.header import Header
            from utils.config import config

            smtp_host = getattr(config, 'EMAIL_SMTP_SERVER', None) or getattr(config, 'SMTP_HOST',
                                                                              None) or 'smtp.qq.com'
            smtp_port = getattr(config, 'EMAIL_SMTP_PORT', None) or getattr(config, 'SMTP_PORT', None) or 465
            email_from = getattr(config, 'EMAIL_FROM', None) or getattr(config, 'EMAIL_USERNAME', None)
            email_password = getattr(config, 'EMAIL_PASSWORD', None)

            try:
                smtp_port = int(smtp_port)
            except (ValueError, TypeError):
                smtp_port = 465

            self.logger.debug(
                f"邮件配置读取: SMTP={smtp_host}:{smtp_port}, FROM={email_from}, PASSWORD={'已配置' if email_password else '未配置'}")

            if not email_from or not email_password:
                missing_fields = []
                if not email_from: missing_fields.append('EMAIL_FROM 或 EMAIL_USERNAME')
                if not email_password: missing_fields.append('EMAIL_PASSWORD')

                self.logger.warning(f"邮件服务器配置不完整，缺少字段: {', '.join(missing_fields)}")
                self.logger.warning("请在.env文件中配置：EMAIL_FROM=your@email.com 和 EMAIL_PASSWORD=your_password")
                return

            msg = MIMEMultipart()
            msg['From'] = email_from
            msg['To'] = ', '.join(email_addresses)
            msg['Subject'] = Header(subject, 'utf-8')

            msg.attach(MIMEText(content, 'plain', 'utf-8'))

            server = smtplib.SMTP_SSL(smtp_host, int(smtp_port))
            server.login(email_from, email_password)

            for email in email_addresses:
                server.sendmail(email_from, [email], msg.as_string())

            server.quit()

            self.logger.debug(f"邮件通知发送成功，接收者: {', '.join(email_addresses)}")
            self.logger.debug(f"使用配置: {smtp_host}:{smtp_port}, 发件人: {email_from}")

        except Exception as e:
            self.logger.error(f"发送邮件通知失败: {e}")
            self.logger.error(f"邮件配置：SMTP_HOST={smtp_host}, "
                              f"SMTP_PORT={smtp_port}, EMAIL_FROM={email_from}")

    def get_system_stats(self) -> dict:
        total_monitors = sum(len(monitors) for monitors in self.monitors.values())
        return {
            "total_monitors": total_monitors,
            "scheduled_messages": len(self.scheduled_messages),
            "processed_messages": len(self.processed_messages)
        }

    async def _send_email_notification_async(
            self,
            subject: str,
            content: str,
            email_addresses: list = None,
            monitor_count: int = 1
    ):
        try:
            await self._send_email_notification(subject, content, email_addresses)
            self.logger.debug(f"邮件通知已后台发送完成 ({monitor_count}个监控器)")
        except Exception as e:
            self.logger.error(f"后台邮件发送失败: {e}")
