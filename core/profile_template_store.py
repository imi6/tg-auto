"""
账号资料模板

把常用的名字、姓氏、简介和头像存起来，勾选账号后一键套用，不用每次手填。
用户名全局唯一，不进模板。
"""

import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.singleton import Singleton


_PHOTO_NAME = re.compile(r'^ptpl_[a-f0-9]{12}\.(jpg|png|webp)$')


class ProfileTemplateStore(metaclass=Singleton):

    def __init__(self):
        self.templates: List[Dict[str, Any]] = []
        self.store_file = Path("data/profile_templates.json")
        self.photo_dir = Path("data/profile_template_photos")
        self.logger = get_logger(__name__)
        self.photo_dir.mkdir(parents=True, exist_ok=True)
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
                   clear_last_name: bool, clear_about: bool,
                   clear_photo: bool = False, has_photo: bool = False) -> Dict[str, Any]:
        first_name = (first_name or '').strip()
        last_name = '' if clear_last_name else (last_name or '').strip()
        about = '' if clear_about else (about or '').strip()

        if not any((
            first_name, last_name, about,
            clear_last_name, clear_about, clear_photo, has_photo
        )):
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
            'clear_photo': bool(clear_photo) and not has_photo,
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
        if item.get('photo'):
            parts.append('更换头像')
        elif item.get('clear_photo'):
            parts.append('删除头像')
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

    def photo_path(self, filename: str) -> Optional[Path]:
        name = (filename or '').strip()
        if not _PHOTO_NAME.match(name):
            return None
        path = (self.photo_dir / name).resolve()
        if path.parent != self.photo_dir.resolve():
            return None
        return path

    def read_photo(self, filename: str) -> Optional[bytes]:
        path = self.photo_path(filename)
        if not path or not path.is_file():
            return None
        return path.read_bytes()

    def _write_photo(self, template_id: str, data: bytes, ext: str) -> str:
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        for old in self.photo_dir.glob(f'{template_id}.*'):
            old.unlink(missing_ok=True)
        filename = f'{template_id}{ext}'
        (self.photo_dir / filename).write_bytes(data)
        return filename

    def _remove_photo(self, filename: str) -> None:
        path = self.photo_path(filename)
        if path and path.is_file():
            path.unlink(missing_ok=True)

    def add(self, title: str = '', first_name: str = '', last_name: str = '',
            about: str = '', clear_last_name: bool = False, clear_about: bool = False,
            clear_photo: bool = False, photo_bytes: Optional[bytes] = None,
            photo_ext: str = '') -> Dict[str, Any]:
        has_photo = bool(photo_bytes)
        fields = self._normalize(
            title, first_name, last_name, about,
            clear_last_name, clear_about, clear_photo, has_photo
        )
        now = self._now()
        template_id = f'ptpl_{secrets.token_hex(6)}'
        photo = ''
        if has_photo:
            photo = self._write_photo(template_id, photo_bytes, photo_ext or '.jpg')
            fields['clear_photo'] = False
        item = {
            'template_id': template_id,
            **fields,
            'photo': photo,
            'used_count': 0,
            'created_at': now,
            'updated_at': now,
            'last_used_at': None,
        }
        item['preview'] = self.preview(item)
        self.templates.append(item)
        self._save()
        return dict(item)

    def update(self, template_id: str, title: str = '', first_name: str = '',
               last_name: str = '', about: str = '',
               clear_last_name: bool = False, clear_about: bool = False,
               clear_photo: bool = False, photo_bytes: Optional[bytes] = None,
               photo_ext: str = '') -> Optional[Dict[str, Any]]:
        item = self.get(template_id)
        if not item:
            return None

        has_photo = bool(photo_bytes) or (bool(item.get('photo')) and not clear_photo)
        fields = self._normalize(
            title, first_name, last_name, about,
            clear_last_name, clear_about, clear_photo, bool(photo_bytes) or has_photo
        )
        item.update(fields)

        if photo_bytes:
            item['photo'] = self._write_photo(template_id, photo_bytes, photo_ext or '.jpg')
            item['clear_photo'] = False
        elif clear_photo:
            self._remove_photo(item.get('photo') or '')
            item['photo'] = ''
            item['clear_photo'] = True

        item['preview'] = self.preview(item)
        item['updated_at'] = self._now()
        self._save()
        return dict(item)

    def delete(self, template_id: str) -> bool:
        item = self.get(template_id)
        if not item:
            return False
        self._remove_photo(item.get('photo') or '')
        self.templates = [row for row in self.templates if row['template_id'] != template_id]
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
