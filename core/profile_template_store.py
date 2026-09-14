"""
账号资料模板

把常用的名字、姓氏、简介存起来，勾选账号后一键套用，不用每次手填。
用户名全局唯一，不进模板。
"""

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.singleton import Singleton


class ProfileTemplateStore(metaclass=Singleton):

    def __init__(self):
        self.templates: List[Dict[str, Any]] = []
        self.store_file = Path("data/profile_templates.json")
        self.logger = get_logger(__name__)
        self._load()

    def _load(self):
        if not self.store_file.exists():
            return

        try:
            with open(self.store_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.templates = data.get('templates', data) if isinstance(data, dict) else data
        except Exception as e:
            self.logger.error(f"加载资料模板失败: {e}")
            self.templates = []

    def _save(self):
        try:
            self.store_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_file, 'w', encoding='utf-8') as f:
                json.dump({'templates': self.templates}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存资料模板失败: {e}")

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _preview_text(content: str, limit: int = 40) -> str:
        text = ' '.join((content or '').split())
        return text if len(text) <= limit else text[:limit] + '…'

    @staticmethod
    def _normalize(title: str, first_name: str, last_name: str, about: str,
                   clear_last_name: bool, clear_about: bool) -> Dict[str, Any]:
        first_name = (first_name or '').strip()
        last_name = '' if clear_last_name else (last_name or '').strip()
        about = '' if clear_about else (about or '').strip()

        if not first_name and not clear_last_name and not last_name and not clear_about and not about:
            raise ValueError('模板至少要包含一个要修改的字段')

        if not (title or '').strip():
            title = first_name or last_name or ProfileTemplateStore._preview_text(about, 16) or '资料模板'
        else:
            title = title.strip()

        return {
            'title': title,
            'first_name': first_name,
            'last_name': last_name,
            'about': about,
            'clear_last_name': bool(clear_last_name),
            'clear_about': bool(clear_about),
        }

    @classmethod
    def preview(cls, item: Dict[str, Any]) -> str:
        parts = []
        if item.get('first_name'):
            parts.append(f"名字 {item['first_name']}")
        if item.get('clear_last_name'):
            parts.append('清空姓氏')
        elif item.get('last_name'):
            parts.append(f"姓氏 {item['last_name']}")
        if item.get('clear_about'):
            parts.append('清空简介')
        elif item.get('about'):
            parts.append(cls._preview_text(item['about']))
        return ' · '.join(parts) or '（空模板）'

    @staticmethod
    def to_profile_fields(item: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """转成批量改资料接口用的字段：None 表示不改，空字符串表示清空"""
        first_name = (item.get('first_name') or '').strip() or None
        if item.get('clear_last_name'):
            last_name = ''
        else:
            last_name = (item.get('last_name') or '').strip() or None
        if item.get('clear_about'):
            about = ''
        else:
            about = (item.get('about') or '').strip() or None

        if first_name is None and last_name is None and about is None:
            raise ValueError('模板没有可应用的字段')
        return {'first_name': first_name, 'last_name': last_name, 'about': about}

    def get(self, template_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self.templates if item['template_id'] == template_id), None)

    def list_templates(self, keyword: str = '') -> List[Dict[str, Any]]:
        items = self.templates
        if keyword:
            needle = keyword.strip().lower()
            items = [
                item for item in items
                if needle in (item.get('title') or '').lower()
                or needle in (item.get('first_name') or '').lower()
                or needle in (item.get('last_name') or '').lower()
                or needle in (item.get('about') or '').lower()
            ]
        return list(reversed(items))

    def add(self, title: str = '', first_name: str = '', last_name: str = '',
            about: str = '', clear_last_name: bool = False, clear_about: bool = False) -> Dict[str, Any]:
        fields = self._normalize(title, first_name, last_name, about, clear_last_name, clear_about)
        now = self._now()
        item = {
            'template_id': f'ptpl_{secrets.token_hex(6)}',
            **fields,
            'preview': self.preview(fields),
            'used_count': 0,
            'created_at': now,
            'updated_at': now,
            'last_used_at': None,
        }
        self.templates.append(item)
        self._save()
        return dict(item)

    def update(self, template_id: str, title: str = '', first_name: str = '',
               last_name: str = '', about: str = '',
               clear_last_name: bool = False, clear_about: bool = False) -> Optional[Dict[str, Any]]:
        item = self.get(template_id)
        if not item:
            return None

        fields = self._normalize(title, first_name, last_name, about, clear_last_name, clear_about)
        item.update(fields)
        item['preview'] = self.preview(item)
        item['updated_at'] = self._now()
        self._save()
        return dict(item)

    def delete(self, template_id: str) -> bool:
        before = len(self.templates)
        self.templates = [item for item in self.templates if item['template_id'] != template_id]
        if len(self.templates) == before:
            return False
        self._save()
        return True

    def mark_used(self, template_id: str) -> Optional[Dict[str, Any]]:
        item = self.get(template_id)
        if not item:
            return None
        item['used_count'] = int(item.get('used_count') or 0) + 1
        item['last_used_at'] = self._now()
        self._save()
        return dict(item)
