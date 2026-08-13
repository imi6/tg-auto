"""
账号健康状态存储

记录每个账号最近一次的受限检测结果。群发和入群前会读这里，
把已经进了 spamblock 的号摘掉，避免继续发无效消息、把封禁坐实。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.health_service import STATE_LIMITED, STATE_UNKNOWN
from utils.logger import get_logger
from utils.singleton import Singleton

# @SpamBot 给的解封时间形如 "12 September 2026, 10:00 UTC"
_UNTIL_FORMATS = (
    '%d %B %Y, %H:%M %Z',
    '%d %B %Y, %H:%M',
    '%d %B %Y',
)


class AccountHealthStore(metaclass=Singleton):

    def __init__(self):
        self.records: Dict[str, Dict[str, Any]] = {}
        self.health_file = Path("data/account_health.json")
        self.logger = get_logger(__name__)

        self._load()

    def _load(self):
        if not self.health_file.exists():
            return

        try:
            with open(self.health_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.records = data.get('accounts', {}) if isinstance(data, dict) else {}
        except Exception as e:
            self.logger.error(f"加载账号健康状态失败: {e}")
            self.records = {}

    def _save(self):
        try:
            self.health_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.health_file, 'w', encoding='utf-8') as f:
                json.dump({'accounts': self.records}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存账号健康状态失败: {e}")

    @staticmethod
    def _parse_until(until: Optional[str]) -> Optional[datetime]:
        """把解封时间解析成 datetime，解析不了返回 None（当作长期受限处理）"""
        if not until:
            return None

        text = re.sub(r'\s+', ' ', str(until)).strip()
        for fmt in _UNTIL_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def get(self, account_id: str) -> Dict[str, Any]:
        record = self.records.get(account_id)
        if not record:
            return {'state': STATE_UNKNOWN, 'checked_at': None, 'message': '尚未检测'}

        return dict(record)

    def all(self) -> Dict[str, Dict[str, Any]]:
        return {account_id: dict(record) for account_id, record in self.records.items()}

    def set_result(self, account_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            'state': result.get('state', STATE_UNKNOWN),
            'until': result.get('until'),
            'message': result.get('message', ''),
            'checked_at': result.get('checked_at') or datetime.now().isoformat(timespec='seconds'),
            'source': result.get('source', 'spambot'),
        }

        self.records[account_id] = record
        self._save()

        return dict(record)

    def mark_limited(self, account_id: str, reason: str, source: str = 'error') -> Dict[str, Any]:
        """发送过程中撞上风控时直接标记，不用等下一次主动检测"""
        return self.set_result(account_id, {
            'state': STATE_LIMITED,
            'until': None,
            'message': reason,
            'source': source,
        })

    def is_limited(self, account_id: str) -> bool:
        """账号当前是否处于受限状态，解封时间已过的记录不再算数"""
        record = self.records.get(account_id)
        if not record or record.get('state') != STATE_LIMITED:
            return False

        until = self._parse_until(record.get('until'))
        if until and until <= datetime.now():
            return False

        return True

    def limited_accounts(self, account_ids: List[str]) -> List[str]:
        return [account_id for account_id in account_ids if self.is_limited(account_id)]

    def describe(self, account_id: str) -> str:
        record = self.records.get(account_id) or {}
        if record.get('state') != STATE_LIMITED:
            return '账号状态正常'

        until = record.get('until')
        return f"账号被 Telegram 限制至 {until}" if until else "账号被 Telegram 限制"

    def clear(self, account_id: Optional[str] = None):
        if account_id:
            self.records.pop(account_id, None)
        else:
            self.records = {}

        self._save()
