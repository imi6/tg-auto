"""
批量资料修改任务管理器 - 应用单例模式
每个账号只执行一次资料更新，因此不需要账号内的间隔限速
用户名全局唯一，不参与批量修改，只能在单账号编辑中设置
"""

from typing import Any, Dict, List, Optional

from services.profile_service import ProfileService
from utils.singleton import Singleton

from .batch_task_manager import BatchTaskManager


class ProfileTaskManager(BatchTaskManager, metaclass=Singleton):

    task_type = 'profile'
    task_label = '批量资料任务'

    # 每个账号只有一条待执行项，账号内不会产生等待
    default_options = {
        'delay_min': 0,
        'delay_max': 0,
        'max_per_account': 1,
        'max_flood_wait': 600,
    }
    min_delay = 0

    def __init__(self):
        super().__init__("data/profile_tasks.json")
        self.profile_service = ProfileService()

    def create_task(
        self,
        account_ids: List[str],
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        about: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        item = {'first_name': first_name, 'last_name': last_name, 'about': about}
        changed = [key for key, value in item.items() if value is not None]

        return super().create_task(
            account_ids,
            [item],
            options,
            extra={'changed_fields': changed}
        )

    async def execute_item(self, client, item: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        return await self.profile_service.update_profile(
            client,
            first_name=item.get('first_name'),
            last_name=item.get('last_name'),
            about=item.get('about')
        )

    def describe_item(self, item: Dict[str, Any]) -> str:
        labels = {'first_name': '名字', 'last_name': '姓氏', 'about': '简介'}
        changed = [labels[key] for key in labels if item.get(key) is not None]
        return '、'.join(changed) if changed else '资料更新'
