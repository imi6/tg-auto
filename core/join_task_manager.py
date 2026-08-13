"""
批量入群任务管理器 - 应用单例模式
调度逻辑复用 BatchTaskManager，这里只负责把单个目标交给 GroupService 加入
"""

from typing import Any, Dict, List, Optional

from services.group_service import GroupService
from utils.singleton import Singleton

from .batch_task_manager import BatchTaskManager


class JoinTaskManager(BatchTaskManager, metaclass=Singleton):

    task_type = 'join'
    task_label = '批量入群任务'

    def __init__(self):
        super().__init__("data/join_tasks.json")
        self.group_service = GroupService()

    def create_task(
        self,
        account_ids: List[str],
        targets: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return super().create_task(account_ids, targets, options)

    async def execute_item(self, client, item: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        return await self.group_service.join(client, item)

    def describe_item(self, item: Dict[str, Any]) -> str:
        return item.get('raw', '')
