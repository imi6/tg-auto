"""
账号资料服务
提供个人资料（用户名、名字、简介）的读取、校验与修改能力
"""

import re
from typing import Any, Dict, Optional

from telethon import errors as tg_errors
from telethon.errors import FloodWaitError
from telethon.tl.functions.account import (
    CheckUsernameRequest,
    UpdateProfileRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.users import GetFullUserRequest

from models.task import STATUS_FAILED, STATUS_FLOOD, STATUS_SUCCESS
from utils.logger import get_logger


class _NeverRaised(Exception):
    """占位异常，用于当前 Telethon 版本缺失某个错误类型时保持 except 分支可用"""


def _error(name: str):
    return getattr(tg_errors, name, _NeverRaised)


UsernameOccupiedError = _error('UsernameOccupiedError')
UsernameInvalidError = _error('UsernameInvalidError')
UsernameNotModifiedError = _error('UsernameNotModifiedError')
UsernamePurchaseAvailableError = _error('UsernamePurchaseAvailableError')
AboutTooLongError = _error('AboutTooLongError')
FirstNameInvalidError = _error('FirstnameInvalidError')

# Telegram 侧的字段长度限制（简介 70 字符，Premium 账号为 140）
FIRST_NAME_MAX = 64
LAST_NAME_MAX = 64
ABOUT_MAX = 70
USERNAME_MIN = 5
USERNAME_MAX = 32

_USERNAME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*[a-zA-Z0-9]$')


class ProfileService:
    """账号资料读写，所有方法都需要传入已连接且已授权的 Telethon 客户端"""

    def __init__(self):
        self.logger = get_logger(__name__)

    # ------------------------------------------------------------------ 校验

    @staticmethod
    def validate_username(username: str) -> Optional[str]:
        """校验用户名格式，通过返回 None，否则返回中文错误说明

        空字符串代表清除用户名，属于合法输入。
        """
        value = (username or '').strip()
        if not value:
            return None

        if len(value) < USERNAME_MIN or len(value) > USERNAME_MAX:
            return f"用户名长度需在 {USERNAME_MIN}-{USERNAME_MAX} 个字符之间"
        if not _USERNAME_PATTERN.match(value):
            return "用户名只能包含字母、数字和下划线，且必须以字母开头、以字母或数字结尾"
        if '__' in value:
            return "用户名中不能出现连续的下划线"

        return None

    @staticmethod
    def validate_profile_fields(
        first_name: Optional[str], last_name: Optional[str], about: Optional[str]
    ) -> Optional[str]:
        if first_name is not None and len(first_name) > FIRST_NAME_MAX:
            return f"名字不能超过 {FIRST_NAME_MAX} 个字符"
        if last_name is not None and len(last_name) > LAST_NAME_MAX:
            return f"姓氏不能超过 {LAST_NAME_MAX} 个字符"
        if about is not None and len(about) > ABOUT_MAX:
            return f"简介不能超过 {ABOUT_MAX} 个字符"
        if first_name is not None and not first_name.strip():
            return "名字不能为空"

        return None

    # ------------------------------------------------------------------ 读取

    async def get_profile(self, client) -> Dict[str, Any]:
        me = await client.get_me()

        about = ''
        try:
            full = await client(GetFullUserRequest('me'))
            about = getattr(full.full_user, 'about', '') or ''
        except Exception as e:
            self.logger.debug(f"获取账号简介失败: {e}")

        username = getattr(me, 'username', None)
        if not username:
            usernames = getattr(me, 'usernames', None) or []
            if usernames:
                username = getattr(usernames[0], 'username', None)

        return {
            'user_id': me.id,
            'phone': getattr(me, 'phone', '') or '',
            'username': username or '',
            'first_name': getattr(me, 'first_name', '') or '',
            'last_name': getattr(me, 'last_name', '') or '',
            'about': about,
            'premium': bool(getattr(me, 'premium', False)),
            'limits': {
                'first_name': FIRST_NAME_MAX,
                'last_name': LAST_NAME_MAX,
                'about': ABOUT_MAX,
            }
        }

    async def check_username(self, client, username: str) -> Dict[str, Any]:
        """检查用户名是否可用，不做任何修改"""
        error = self.validate_username(username)
        if error:
            return {'available': False, 'message': error}

        value = (username or '').strip()
        if not value:
            return {'available': False, 'message': '请输入用户名'}

        try:
            available = await client(CheckUsernameRequest(value))
            return {
                'available': bool(available),
                'message': '该用户名可用' if available else '该用户名已被占用'
            }
        except FloodWaitError as e:
            return {
                'available': False,
                'message': f"检查过于频繁，请等待 {int(getattr(e, 'seconds', 0) or 0)} 秒后重试"
            }
        except UsernameInvalidError:
            return {'available': False, 'message': '用户名格式无效'}
        except UsernamePurchaseAvailableError:
            return {'available': False, 'message': '该用户名需要通过 Fragment 购买'}
        except Exception as e:
            return {'available': False, 'message': str(e) or e.__class__.__name__}

    # ------------------------------------------------------------------ 修改

    async def update_profile(
        self,
        client,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        about: Optional[str] = None
    ) -> Dict[str, Any]:
        """更新名字、姓氏与简介，传 None 的字段保持不变

        传空字符串表示清空该字段（名字除外，Telegram 不允许名字为空）。
        """
        if first_name is None and last_name is None and about is None:
            return {'status': STATUS_SUCCESS, 'message': '没有需要修改的字段'}

        error = self.validate_profile_fields(first_name, last_name, about)
        if error:
            return {'status': STATUS_FAILED, 'message': error}

        payload = {}
        if first_name is not None:
            payload['first_name'] = first_name
        if last_name is not None:
            payload['last_name'] = last_name
        if about is not None:
            payload['about'] = about

        try:
            await client(UpdateProfileRequest(**payload))
            return {
                'status': STATUS_SUCCESS,
                'message': '资料已更新',
                'updated_fields': sorted(payload.keys())
            }
        except FloodWaitError as e:
            seconds = int(getattr(e, 'seconds', 0) or 0)
            return {
                'status': STATUS_FLOOD,
                'wait_seconds': seconds,
                'message': f"触发频率限制，需等待 {seconds} 秒"
            }
        except AboutTooLongError:
            return {'status': STATUS_FAILED, 'message': f"简介超出 {ABOUT_MAX} 字符限制"}
        except FirstNameInvalidError:
            return {'status': STATUS_FAILED, 'message': '名字不合法'}
        except Exception as e:
            return {'status': STATUS_FAILED, 'message': str(e) or e.__class__.__name__}

    async def set_username(self, client, username: str) -> Dict[str, Any]:
        """设置或清除用户名

        用户名全局唯一，修改后旧用户名会立即释放，可能被他人占用。
        """
        error = self.validate_username(username)
        if error:
            return {'status': STATUS_FAILED, 'message': error}

        value = (username or '').strip()

        try:
            await client(UpdateUsernameRequest(value))
            return {
                'status': STATUS_SUCCESS,
                'message': '用户名已清除' if not value else f'用户名已设置为 @{value}',
                'username': value
            }
        except UsernameNotModifiedError:
            return {'status': STATUS_SUCCESS, 'message': '用户名未发生变化', 'username': value}
        except FloodWaitError as e:
            seconds = int(getattr(e, 'seconds', 0) or 0)
            return {
                'status': STATUS_FLOOD,
                'wait_seconds': seconds,
                'message': f"修改用户名过于频繁，需等待 {seconds} 秒"
            }
        except UsernameOccupiedError:
            return {'status': STATUS_FAILED, 'message': '该用户名已被占用'}
        except UsernameInvalidError:
            return {'status': STATUS_FAILED, 'message': '用户名格式无效'}
        except UsernamePurchaseAvailableError:
            return {'status': STATUS_FAILED, 'message': '该用户名需要通过 Fragment 购买'}
        except Exception as e:
            return {'status': STATUS_FAILED, 'message': str(e) or e.__class__.__name__}
