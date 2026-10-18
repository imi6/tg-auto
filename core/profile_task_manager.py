"""
批量资料修改任务管理器 - 应用单例模式
每个账号只执行一次资料更新，因此不需要账号内的间隔限速
用户名全局唯一，不参与批量修改，只能在单账号编辑中设置
"""

from typing import Any, Dict, List, Optional

from models.task import STATUS_FAILED, STATUS_SUCCESS
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
        photo: str = '',
        clear_photo: bool = False,
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        item = {
            'first_name': first_name,
            'last_name': last_name,
            'about': about,
            'photo': photo or '',
            'clear_photo': bool(clear_photo) and not photo,
        }
        changed = [key for key in ('first_name', 'last_name', 'about') if item.get(key) is not None]
        if item['photo']:
            changed.append('photo')
        elif item['clear_photo']:
            changed.append('clear_photo')

        return super().create_task(
            account_ids,
            [item],
            options,
            extra={'changed_fields': changed}
        )

    async def execute_item(self, client, item: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        messages = []
        if any(item.get(key) is not None for key in ('first_name', 'last_name', 'about')):
            result = await self.profile_service.update_profile(
                client,
                first_name=item.get('first_name'),
                last_name=item.get('last_name'),
                about=item.get('about')
            )
            if result.get('status') != STATUS_SUCCESS:
                return result
            messages.append(result.get('message') or '资料已更新')

        photo_name = (item.get('photo') or '').strip()
        if photo_name:
            from core.profile_template_store import ProfileTemplateStore
            data = ProfileTemplateStore().read_photo(photo_name)
            if not data:
                return {'status': STATUS_FAILED, 'message': '模板头像文件不存在'}
            result = await self.profile_service.set_profile_photo(client, data, photo_name)
            if result.get('status') != STATUS_SUCCESS:
                return result
            messages.append(result.get('message') or '头像已更新')
        elif item.get('clear_photo'):
            result = await self.profile_service.delete_profile_photo(client)
            if result.get('status') != STATUS_SUCCESS:
                return result
            messages.append(result.get('message') or '头像已删除')

        if not messages:
            return {'status': STATUS_SUCCESS, 'message': '没有需要修改的字段'}
        return {'status': STATUS_SUCCESS, 'message': '；'.join(messages)}

    def describe_item(self, item: Dict[str, Any]) -> str:
        labels = {'first_name': '名字', 'last_name': '姓氏', 'about': '简介'}
        changed = [labels[key] for key in labels if item.get(key) is not None]
        if item.get('photo'):
            changed.append('头像')
        elif item.get('clear_photo'):
            changed.append('删除头像')
        return '、'.join(changed) if changed else '资料更新'
