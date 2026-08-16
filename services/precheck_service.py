"""
发送前预检
在真正调用 send_message 之前判断这个目标还发不发得出去，
避免对着全员禁言、只允许管理员发言或者早就被踢出去的群反复试探。
"""

from typing import Any, Dict, List

from telethon.errors import FloodWaitError
from telethon.tl import types

from utils.logger import get_logger

CODE_OK = 'ok'
CODE_MUTED_ALL = 'muted_all'
CODE_ADMIN_ONLY = 'admin_only'
CODE_RESTRICTED = 'restricted'
CODE_LEFT = 'left'
CODE_FORUM = 'forum'
CODE_NOT_FOUND = 'not_found'
CODE_FLOOD = 'flood'
CODE_UNKNOWN = 'unknown'

CODE_TEXT = {
    CODE_OK: '可以发送',
    CODE_MUTED_ALL: '全员禁言',
    CODE_ADMIN_ONLY: '仅管理员可发言',
    CODE_RESTRICTED: '当前账号被禁言',
    CODE_LEFT: '已退出或被移出',
    CODE_FORUM: '话题群，无法直接发送',
    CODE_NOT_FOUND: '找不到该目标',
    CODE_FLOOD: '触发限流，未能检查',
    CODE_UNKNOWN: '状态未知',
}

# 拿不准的一律放行，交给真实发送去判断，避免预检误伤正常目标
_FAIL_OPEN_CODES = {CODE_FLOOD, CODE_UNKNOWN}


class PrecheckService:
    """目标可发送性检查，需要传入已连接且已授权的 Telethon 客户端"""

    def __init__(self):
        self.logger = get_logger(__name__)

    @staticmethod
    def is_blocking(code: str) -> bool:
        """该结果是否应该阻止发送"""
        return code != CODE_OK and code not in _FAIL_OPEN_CODES

    @staticmethod
    def _result(code: str, title: str = '', detail: str = '') -> Dict[str, Any]:
        return {
            'code': code,
            'can_send': code == CODE_OK or code in _FAIL_OPEN_CODES,
            'reason': detail or CODE_TEXT.get(code, code),
            'title': title,
        }

    @classmethod
    def inspect_entity(cls, entity) -> Dict[str, Any]:
        """只看实体自身携带的权限字段做判断，不额外发请求

        Channel 的 banned_rights 就是当前账号在该群的限制，
        default_banned_rights 是群的默认权限，两者都为空才说明能正常发言。
        """
        title = getattr(entity, 'title', '') or getattr(entity, 'first_name', '') or ''

        if isinstance(entity, types.User):
            return cls._result(CODE_OK, title)

        if isinstance(entity, (types.ChatForbidden, types.ChannelForbidden)):
            return cls._result(CODE_LEFT, title, '已被移出该群组')

        if isinstance(entity, types.Chat):
            if getattr(entity, 'deactivated', False):
                return cls._result(CODE_LEFT, title, '群组已解散')
            if getattr(entity, 'kicked', False):
                return cls._result(CODE_LEFT, title, '已被移出该群组')
            if getattr(entity, 'left', False):
                return cls._result(CODE_LEFT, title, '已退出该群组')

            is_admin = bool(getattr(entity, 'creator', False) or getattr(entity, 'admin_rights', None))
            if not is_admin and cls._muted(getattr(entity, 'default_banned_rights', None)):
                return cls._result(CODE_MUTED_ALL, title)

            return cls._result(CODE_OK, title)

        if isinstance(entity, types.Channel):
            if getattr(entity, 'left', False):
                return cls._result(CODE_LEFT, title, '已退出该群组或频道')

            if cls._muted(getattr(entity, 'banned_rights', None)):
                return cls._result(CODE_RESTRICTED, title, '当前账号在该群被禁言')

            is_creator = bool(getattr(entity, 'creator', False))
            admin_rights = getattr(entity, 'admin_rights', None)

            if getattr(entity, 'broadcast', False):
                can_post = is_creator or bool(admin_rights and getattr(admin_rights, 'post_messages', False))
                return cls._result(CODE_OK, title) if can_post else cls._result(CODE_ADMIN_ONLY, title)

            # 开启话题的超级群不能往群根节点发普通消息，Telegram 会回 cannot send plain results
            if getattr(entity, 'forum', False):
                return cls._result(CODE_FORUM, title, '该群开启了话题，不能直接往群里发普通消息')

            if not (is_creator or admin_rights) and cls._muted(getattr(entity, 'default_banned_rights', None)):
                return cls._result(CODE_MUTED_ALL, title)

            return cls._result(CODE_OK, title)

        return cls._result(CODE_UNKNOWN, title)

    @staticmethod
    def _muted(rights) -> bool:
        return bool(rights and getattr(rights, 'send_messages', False))

    async def check_target(self, client, target) -> Dict[str, Any]:
        """检查单个目标，返回结果里带 target 便于前端对号入座"""
        try:
            entity = await client.get_entity(target)
            result = self.inspect_entity(entity)
        except FloodWaitError as e:
            seconds = int(getattr(e, 'seconds', 0) or 0)
            result = self._result(CODE_FLOOD, '', f"触发限流，需等待 {seconds} 秒")
        except (ValueError, TypeError) as e:
            # Telethon 找不到实体时抛 ValueError，通常是没加过或者已经被踢
            result = self._result(CODE_NOT_FOUND, '', f"无法解析目标: {e}")
        except Exception as e:
            reason = str(e) or e.__class__.__name__
            name = e.__class__.__name__
            lowered = reason.lower()
            if name in ('ChannelPrivateError', 'ChatForbiddenError') or 'channel_private' in lowered or 'private' in lowered:
                result = self._result(CODE_LEFT, '', '目标为私有群组且当前账号不在其中')
            else:
                self.logger.debug(f"预检目标 {target} 失败: {reason}")
                result = self._result(CODE_UNKNOWN, '', reason)

        result['target'] = target
        return result

    async def check_targets(self, client, targets: List[Any]) -> Dict[str, Any]:
        """批量预检，返回逐个结果与汇总"""
        results = []
        for target in targets:
            results.append(await self.check_target(client, target))

        blocked = [item for item in results if self.is_blocking(item['code'])]

        summary = {'total': len(results), 'sendable': len(results) - len(blocked), 'blocked': len(blocked)}
        for item in blocked:
            summary[item['code']] = summary.get(item['code'], 0) + 1

        return {'results': results, 'summary': summary}
