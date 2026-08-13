"""
群组服务
提供公开群组/频道的搜索、目标链接解析以及加入群组能力
"""

import asyncio
import re
from typing import Any, Dict, List, Optional

from telethon import utils as tg_utils
from telethon import errors as tg_errors
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import GetFullChannelRequest, JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import ChatInviteAlready, ChatInvitePeek

from models.task import (
    STATUS_ALREADY,
    STATUS_FAILED,
    STATUS_FLOOD,
    STATUS_PENDING,
    STATUS_SUCCESS,
)
from utils.logger import get_logger


class _NeverRaised(Exception):
    """占位异常，用于当前 Telethon 版本缺失某个错误类型时保持 except 分支可用"""


def _error(name: str):
    return getattr(tg_errors, name, _NeverRaised)


UserAlreadyParticipantError = _error('UserAlreadyParticipantError')
InviteHashExpiredError = _error('InviteHashExpiredError')
InviteHashInvalidError = _error('InviteHashInvalidError')
InviteRequestSentError = _error('InviteRequestSentError')
ChannelsTooMuchError = _error('ChannelsTooMuchError')
ChannelPrivateError = _error('ChannelPrivateError')
UsernameNotOccupiedError = _error('UsernameNotOccupiedError')
UsernameInvalidError = _error('UsernameInvalidError')
UserBannedInChannelError = _error('UserBannedInChannelError')
UserDeactivatedBanError = _error('UserDeactivatedBanError')

_INVITE_PATTERN = re.compile(r'^(?:\+|joinchat/)(?P<hash>[\w-]+)$', re.IGNORECASE)
_USERNAME_PATTERN = re.compile(r'^[a-zA-Z][\w]{2,31}$')


class GroupService:
    """群组搜索与加入服务，所有方法都需要传入已连接的 Telethon 客户端"""

    def __init__(self):
        self.logger = get_logger(__name__)

    # ------------------------------------------------------------------ 解析

    @staticmethod
    def parse_target(raw: str) -> Optional[Dict[str, str]]:
        """把用户输入的一行文本解析成统一的目标结构

        支持 @username、t.me/username、t.me/+hash、t.me/joinchat/hash、
        纯数字 ID 以及 -100 开头的频道 ID。无法识别时返回 None。
        """
        text = (raw or '').strip()
        if not text:
            return None

        cleaned = re.sub(r'^(?:https?://)?(?:www\.)?(?:t(?:elegram)?\.me|telegram\.dog)/', '', text, flags=re.IGNORECASE)
        cleaned = cleaned.lstrip('@').strip('/')
        cleaned = cleaned.split('?')[0]

        if not cleaned:
            return None

        invite_match = _INVITE_PATTERN.match(cleaned)
        if invite_match:
            return {'raw': text, 'kind': 'invite', 'value': invite_match.group('hash')}

        if re.fullmatch(r'-?\d+', cleaned):
            return {'raw': text, 'kind': 'id', 'value': cleaned}

        # t.me/c/123456/1 形式指向私有频道内部 ID，需要账号已有该对话才可用
        c_match = re.match(r'^c/(\d+)', cleaned)
        if c_match:
            return {'raw': text, 'kind': 'id', 'value': f'-100{c_match.group(1)}'}

        username = cleaned.split('/')[0]
        if _USERNAME_PATTERN.match(username):
            return {'raw': text, 'kind': 'username', 'value': username}

        return None

    @classmethod
    def parse_targets(cls, raw_targets: List[str]) -> Dict[str, List[Any]]:
        """批量解析目标，返回去重后的有效目标和无法识别的原始文本"""
        parsed: List[Dict[str, str]] = []
        invalid: List[str] = []
        seen = set()

        for raw in raw_targets:
            for line in re.split(r'[\s,;]+', raw or ''):
                if not line.strip():
                    continue
                target = cls.parse_target(line)
                if not target:
                    invalid.append(line.strip())
                    continue
                dedup_key = f"{target['kind']}:{target['value'].lower()}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                parsed.append(target)

        return {'targets': parsed, 'invalid': invalid}

    # ------------------------------------------------------------------ 搜索

    async def search(
        self,
        client,
        keyword: str,
        limit: int = 50,
        group_type: str = 'all',
        with_details: bool = True
    ) -> List[Dict[str, Any]]:
        """全局搜索公开群组/频道

        Telegram 的全局搜索只覆盖公开实体且单次返回条数有限；
        当关键词本身是链接或用户名时，额外做一次精确解析并置顶。
        """
        keyword = (keyword or '').strip()
        if not keyword:
            return []

        items: List[Dict[str, Any]] = []
        seen_ids = set()

        exact = await self._resolve_exact(client, keyword)
        if exact:
            items.append(exact)
            seen_ids.add(exact['id'])

        try:
            found = await client(SearchRequest(q=keyword, limit=min(max(limit, 1), 100)))
            chats = list(getattr(found, 'chats', []) or [])
        except FloodWaitError:
            raise
        except Exception as e:
            self.logger.warning(f"搜索群组失败: {e}")
            chats = []

        for chat in chats:
            item = self._build_item(chat)
            if not item or item['id'] in seen_ids:
                continue
            seen_ids.add(item['id'])
            items.append(item)

        if group_type in ('group', 'channel'):
            items = [item for item in items if item['type'] == group_type]

        if with_details:
            await self._fill_member_counts(client, items)

        return items[:limit]

    async def _resolve_exact(self, client, keyword: str) -> Optional[Dict[str, Any]]:
        target = self.parse_target(keyword)
        if not target:
            return None

        try:
            if target['kind'] == 'invite':
                info = await client(CheckChatInviteRequest(target['value']))
                chat = getattr(info, 'chat', None)
                if chat is not None:
                    item = self._build_item(chat)
                else:
                    item = {
                        'id': 0,
                        'title': getattr(info, 'title', '') or '私有群组',
                        'username': None,
                        'type': 'group' if getattr(info, 'megagroup', False) else 'channel',
                        'members_count': getattr(info, 'participants_count', 0) or 0,
                        'verified': False,
                        'is_private': True,
                    }
                if item:
                    item['link'] = f"https://t.me/+{target['value']}"
                    item['join_target'] = keyword.strip()
                    item['is_private'] = True
                return item

            if target['kind'] == 'username':
                entity = await client.get_entity(target['value'])
                return self._build_item(entity)
        except FloodWaitError:
            raise
        except Exception as e:
            self.logger.debug(f"精确解析 {keyword} 失败: {e}")

        return None

    def _build_item(self, chat) -> Optional[Dict[str, Any]]:
        class_name = chat.__class__.__name__

        if class_name in ('ChannelForbidden', 'ChatForbidden', 'ChatEmpty'):
            return None

        if class_name == 'Channel':
            chat_type = 'group' if (getattr(chat, 'megagroup', False) or getattr(chat, 'gigagroup', False)) else 'channel'
        elif class_name == 'Chat':
            chat_type = 'group'
        else:
            return None

        username = getattr(chat, 'username', None)
        if not username:
            usernames = getattr(chat, 'usernames', None) or []
            if usernames:
                username = getattr(usernames[0], 'username', None)

        try:
            chat_id = tg_utils.get_peer_id(chat)
        except Exception:
            chat_id = getattr(chat, 'id', 0)

        return {
            'id': chat_id,
            'title': (getattr(chat, 'title', '') or '')[:120],
            'username': username,
            'type': chat_type,
            'members_count': getattr(chat, 'participants_count', 0) or 0,
            'verified': bool(getattr(chat, 'verified', False)),
            'scam': bool(getattr(chat, 'scam', False)),
            'is_private': not username,
            'joined': getattr(chat, 'left', True) is False,
            'link': f'https://t.me/{username}' if username else '',
            'join_target': f'@{username}' if username else str(chat_id),
        }

    async def _fill_member_counts(self, client, items: List[Dict[str, Any]], max_lookups: int = 24):
        """搜索结果通常不带成员数，按需补齐（受限流影响时提前停止）"""
        pending = [item for item in items if not item['members_count'] and item['username']][:max_lookups]
        if not pending:
            return

        semaphore = asyncio.Semaphore(4)
        aborted = asyncio.Event()

        async def fetch(item):
            if aborted.is_set():
                return
            async with semaphore:
                try:
                    full = await client(GetFullChannelRequest(item['username']))
                    item['members_count'] = getattr(full.full_chat, 'participants_count', 0) or 0
                    about = getattr(full.full_chat, 'about', '') or ''
                    item['description'] = about[:160]
                except FloodWaitError:
                    aborted.set()
                except Exception:
                    pass

        await asyncio.gather(*[fetch(item) for item in pending], return_exceptions=True)

    # ------------------------------------------------------------------ 加入

    async def join(self, client, target: Dict[str, str]) -> Dict[str, Any]:
        """加入单个群组/频道

        返回 status 为 success / already / pending / failed / flood 的结果字典，
        flood 时附带 wait_seconds，由调用方决定等待还是中止。
        """
        try:
            if target['kind'] == 'invite':
                return await self._join_by_invite(client, target['value'])
            return await self._join_by_entity(client, target)
        except FloodWaitError as e:
            return {
                'status': STATUS_FLOOD,
                'wait_seconds': int(getattr(e, 'seconds', 0) or 0),
                'message': f"触发频率限制，需等待 {int(getattr(e, 'seconds', 0) or 0)} 秒"
            }
        except UserDeactivatedBanError:
            return {'status': STATUS_FAILED, 'message': '账号已被封禁', 'fatal': True}
        except ChannelsTooMuchError:
            return {'status': STATUS_FAILED, 'message': '该账号加入的群组/频道数已达上限', 'fatal': True}
        except UserAlreadyParticipantError:
            return {'status': STATUS_ALREADY, 'message': '已在群内'}
        except InviteRequestSentError:
            return {'status': STATUS_PENDING, 'message': '已发送加入申请，等待管理员批准'}
        except (InviteHashExpiredError, InviteHashInvalidError):
            return {'status': STATUS_FAILED, 'message': '邀请链接无效或已过期'}
        except (UsernameNotOccupiedError, UsernameInvalidError):
            return {'status': STATUS_FAILED, 'message': '用户名不存在'}
        except UserBannedInChannelError:
            return {'status': STATUS_FAILED, 'message': '该账号被此群组封禁'}
        except ChannelPrivateError:
            return {'status': STATUS_FAILED, 'message': '私有群组，当前账号无权加入'}
        except Exception as e:
            return {'status': STATUS_FAILED, 'message': str(e) or e.__class__.__name__}

    async def _join_by_invite(self, client, invite_hash: str) -> Dict[str, Any]:
        try:
            info = await client(CheckChatInviteRequest(invite_hash))
            if isinstance(info, (ChatInviteAlready, ChatInvitePeek)):
                chat = info.chat
                return {
                    'status': STATUS_ALREADY,
                    'message': '已在群内',
                    'chat_id': tg_utils.get_peer_id(chat),
                    'title': getattr(chat, 'title', '')
                }
            if getattr(info, 'request_needed', False):
                await client(ImportChatInviteRequest(invite_hash))
                return {'status': STATUS_PENDING, 'message': '已发送加入申请，等待管理员批准'}
        except FloodWaitError:
            raise
        except (InviteHashExpiredError, InviteHashInvalidError):
            raise
        except Exception as e:
            self.logger.debug(f"检查邀请链接 {invite_hash} 失败: {e}")

        updates = await client(ImportChatInviteRequest(invite_hash))
        chat = self._chat_from_updates(updates)
        return {
            'status': STATUS_SUCCESS,
            'message': '加入成功',
            'chat_id': tg_utils.get_peer_id(chat) if chat else None,
            'title': getattr(chat, 'title', '') if chat else ''
        }

    async def _join_by_entity(self, client, target: Dict[str, str]) -> Dict[str, Any]:
        lookup = target['value'] if target['kind'] == 'username' else int(target['value'])
        entity = await client.get_entity(lookup)

        title = getattr(entity, 'title', '') or ''
        if getattr(entity, 'left', True) is False:
            return {
                'status': STATUS_ALREADY,
                'message': '已在群内',
                'chat_id': tg_utils.get_peer_id(entity),
                'title': title
            }

        await client(JoinChannelRequest(entity))
        return {
            'status': STATUS_SUCCESS,
            'message': '加入成功',
            'chat_id': tg_utils.get_peer_id(entity),
            'title': title
        }

    @staticmethod
    def _chat_from_updates(updates):
        chats = getattr(updates, 'chats', None) or []
        return chats[0] if chats else None
