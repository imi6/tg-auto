"""
群组库

把常用的群链接、@用户名先存起来，之后建入群任务时直接勾选，
不用每次都去别处翻链接再粘贴。
"""

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from services.group_service import GroupService
from utils.logger import get_logger
from utils.singleton import Singleton

KIND_LABELS = {
    'username': '用户名',
    'invite': '邀请链接',
    'id': '群组 ID',
}


class GroupLibraryStore(metaclass=Singleton):

    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self.library_file = Path("data/group_library.json")
        self.logger = get_logger(__name__)

        self._load()

    def _load(self):
        if not self.library_file.exists():
            return

        try:
            with open(self.library_file, 'r', encoding='utf-8') as f:
                self.entries = json.load(f)
        except Exception as e:
            self.logger.error(f"加载群组库失败: {e}")
            self.entries = []

    def _save(self):
        try:
            self.library_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.library_file, 'w', encoding='utf-8') as f:
                json.dump(self.entries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存群组库失败: {e}")

    @staticmethod
    def _dedup_key(kind: str, value: str) -> str:
        return f"{kind}:{value.lower()}"

    def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        return next((entry for entry in self.entries if entry['entry_id'] == entry_id), None)

    def list_entries(self, keyword: str = '', tag: str = '') -> List[Dict[str, Any]]:
        """按添加时间倒序返回，可按关键词和标签过滤"""
        entries = self.entries

        if tag:
            entries = [e for e in entries if (e.get('tag') or '') == tag]

        if keyword:
            needle = keyword.strip().lower()
            entries = [
                e for e in entries
                if needle in (e.get('raw') or '').lower()
                or needle in (e.get('title') or '').lower()
                or needle in (e.get('tag') or '').lower()
            ]

        return list(reversed(entries))

    def list_tags(self) -> List[str]:
        tags = {entry.get('tag') for entry in self.entries if entry.get('tag')}
        return sorted(tags)

    def add_many(
        self,
        raw_targets: Union[str, List[str]],
        tag: str = '',
        title: str = ''
    ) -> Dict[str, Any]:
        """批量添加，复用入群那套解析逻辑，返回新增/重复/无法识别的明细"""
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets]

        parsed = GroupService.parse_targets(raw_targets or [])
        existing = {self._dedup_key(e['kind'], e['value']) for e in self.entries}

        added: List[Dict[str, Any]] = []
        duplicated: List[str] = []

        for target in parsed['targets']:
            key = self._dedup_key(target['kind'], target['value'])
            if key in existing:
                duplicated.append(target['raw'])
                continue

            existing.add(key)
            entry = {
                'entry_id': f"lib_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}",
                'raw': target['raw'],
                'kind': target['kind'],
                'value': target['value'],
                'title': title.strip(),
                'tag': tag.strip(),
                'created_at': datetime.now().isoformat(timespec='seconds'),
                'last_used_at': None,
                'used_count': 0,
            }
            self.entries.append(entry)
            added.append(entry)

        if added:
            self._save()

        return {'added': added, 'duplicated': duplicated, 'invalid': parsed['invalid']}

    def update_entry(self, entry_id: str, title: Optional[str] = None,
                     tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
        entry = self.get_entry(entry_id)
        if not entry:
            return None

        if title is not None:
            entry['title'] = title.strip()
        if tag is not None:
            entry['tag'] = tag.strip()

        self._save()

        return entry

    def delete_entries(self, entry_ids: List[str]) -> int:
        wanted = set(entry_ids)
        before = len(self.entries)

        self.entries = [entry for entry in self.entries if entry['entry_id'] not in wanted]

        removed = before - len(self.entries)
        if removed:
            self._save()

        return removed

    def mark_used(self, targets: List[Dict[str, str]]) -> int:
        """入群任务用到库里的目标时更新使用次数，方便看哪些群常用"""
        used_keys = {self._dedup_key(t['kind'], t['value']) for t in targets}
        now = datetime.now().isoformat(timespec='seconds')
        touched = 0

        for entry in self.entries:
            if self._dedup_key(entry['kind'], entry['value']) in used_keys:
                entry['used_count'] = entry.get('used_count', 0) + 1
                entry['last_used_at'] = now
                touched += 1

        if touched:
            self._save()

        return touched
