"""
消息模板

把常用广告文案存起来，建定时任务时直接选用，不用每次重新粘贴。
"""

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.singleton import Singleton


class MessageTemplateStore(metaclass=Singleton):

    def __init__(self):
        self.templates: List[Dict[str, Any]] = []
        self.store_file = Path("data/message_templates.json")
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
            self.logger.error(f"加载消息模板失败: {e}")
            self.templates = []

    def _save(self):
        try:
            self.store_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_file, 'w', encoding='utf-8') as f:
                json.dump({'templates': self.templates}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存消息模板失败: {e}")

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _preview(content: str, limit: int = 80) -> str:
        text = ' '.join((content or '').split())
        return text if len(text) <= limit else text[:limit] + '…'

    def get(self, template_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self.templates if item['template_id'] == template_id), None)

    def list_templates(self, keyword: str = '') -> List[Dict[str, Any]]:
        items = self.templates
        if keyword:
            needle = keyword.strip().lower()
            items = [
                item for item in items
                if needle in (item.get('title') or '').lower()
                or needle in (item.get('content') or '').lower()
            ]

        return list(reversed(items))

    def add(self, title: str, content: str) -> Dict[str, Any]:
        title = (title or '').strip()
        content = (content or '').strip()
        if not content:
            raise ValueError('模板内容不能为空')

        if not title:
            title = self._preview(content, 24)

        now = self._now()
        item = {
            'template_id': f'tpl_{secrets.token_hex(6)}',
            'title': title,
            'content': content,
            'preview': self._preview(content),
            'used_count': 0,
            'created_at': now,
            'updated_at': now,
            'last_used_at': None,
        }
        self.templates.append(item)
        self._save()
        return dict(item)

    def update(self, template_id: str, title: Optional[str] = None,
               content: Optional[str] = None) -> Optional[Dict[str, Any]]:
        item = self.get(template_id)
        if not item:
            return None

        if title is not None:
            title = title.strip()
            if title:
                item['title'] = title

        if content is not None:
            content = content.strip()
            if not content:
                raise ValueError('模板内容不能为空')
            item['content'] = content
            item['preview'] = self._preview(content)

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
