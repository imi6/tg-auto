"""
账号健康服务
通过官方 @SpamBot 查询账号是否被限制（spamblock），用于在群发前摘掉已经废掉的号
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from telethon.errors import FloodWaitError

from utils.logger import get_logger

SPAM_BOT = 'SpamBot'

STATE_FREE = 'free'
STATE_LIMITED = 'limited'
STATE_UNKNOWN = 'unknown'

# @SpamBot 的回复没有结构化字段，只能按关键词判断。
# 官方英文文案变化不大，这里同时兼容几种常见说法，命中不了就记 unknown 并保留原文。
_FREE_MARKERS = (
    'no limits are currently applied',
    'good news',
    'you are free',
)

_LIMITED_MARKERS = (
    'limited until',
    'your account is limited',
    'i’m very sorry',
    "i'm very sorry",
    'some limitations',
    'restricted until',
)

# 形如 "limited until 12 September 2026, 10:00 UTC"
_UNTIL_PATTERN = re.compile(
    r'until\s+(\d{1,2}\s+\w+\s+\d{4}(?:,?\s+\d{1,2}:\d{2}(?:\s*[A-Z]{2,4})?)?)',
    re.IGNORECASE
)


class HealthService:
    """账号受限状态查询，需要传入已连接且已授权的 Telethon 客户端"""

    def __init__(self):
        self.logger = get_logger(__name__)

    @staticmethod
    def parse_spam_reply(text: str) -> Dict[str, Any]:
        """把 @SpamBot 的回复解析成状态

        判定不了时返回 unknown，宁可让人工看原文，也不要把受限的号当成正常的继续用。
        """
        raw = (text or '').strip()
        lowered = raw.lower()

        if not raw:
            return {'state': STATE_UNKNOWN, 'until': None, 'message': '未收到回复'}

        if any(marker in lowered for marker in _LIMITED_MARKERS):
            match = _UNTIL_PATTERN.search(raw)
            return {
                'state': STATE_LIMITED,
                'until': match.group(1) if match else None,
                'message': raw,
            }

        if any(marker in lowered for marker in _FREE_MARKERS):
            return {'state': STATE_FREE, 'until': None, 'message': raw}

        return {'state': STATE_UNKNOWN, 'until': None, 'message': raw}

    async def _read_spam_reply(self, client, timeout: float) -> Optional[str]:
        """向 @SpamBot 发 /start 并取回复

        优先用 conversation；账号没有在跑更新循环时它会超时，这时退回到轮询历史消息。
        """
        try:
            async with client.conversation(SPAM_BOT, timeout=timeout) as conv:
                await conv.send_message('/start')
                response = await conv.get_response()
                return response.raw_text
        except asyncio.TimeoutError:
            self.logger.debug("等待 @SpamBot 回复超时，改用轮询历史消息")
        except Exception as e:
            self.logger.debug(f"conversation 方式查询失败，改用轮询: {e}")
            try:
                await client.send_message(SPAM_BOT, '/start')
            except Exception as send_error:
                self.logger.warning(f"向 @SpamBot 发送 /start 失败: {send_error}")
                return None

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
            try:
                messages = await client.get_messages(SPAM_BOT, limit=1)
            except Exception as e:
                self.logger.debug(f"读取 @SpamBot 历史消息失败: {e}")
                continue

            if messages and not messages[0].out and messages[0].raw_text:
                return messages[0].raw_text

        return None

    async def check_account(self, client, timeout: float = 25.0) -> Dict[str, Any]:
        """查询单个账号的受限状态"""
        checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')

        try:
            reply = await self._read_spam_reply(client, timeout)
        except FloodWaitError as e:
            seconds = int(getattr(e, 'seconds', 0) or 0)
            return {
                'state': STATE_UNKNOWN,
                'until': None,
                'message': f"查询过于频繁，需等待 {seconds} 秒",
                'checked_at': checked_at,
                'source': 'spambot',
            }
        except Exception as e:
            return {
                'state': STATE_UNKNOWN,
                'until': None,
                'message': f"查询失败: {e or e.__class__.__name__}",
                'checked_at': checked_at,
                'source': 'spambot',
            }

        if reply is None:
            return {
                'state': STATE_UNKNOWN,
                'until': None,
                'message': '未收到 @SpamBot 回复，请稍后重试',
                'checked_at': checked_at,
                'source': 'spambot',
            }

        result = self.parse_spam_reply(reply)
        result['checked_at'] = checked_at
        result['source'] = 'spambot'
        return result
