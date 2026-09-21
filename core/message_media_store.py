"""
定时消息 / 模板配图

文件落在 data/message_media，JSON 里只存文件名，读取时再拼回绝对路径。
"""

import re
import secrets
from pathlib import Path
from typing import Optional

from utils.singleton import Singleton

ALLOWED_TYPES = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/pjpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'image/gif': '.gif',
}

ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
MAX_BYTES = 10 * 1024 * 1024
NAME_RE = re.compile(r'^img_[a-f0-9]{16}\.(jpg|png|webp|gif)$')


class MessageMediaStore(metaclass=Singleton):

    def __init__(self):
        self.media_dir = Path('data/message_media')
        self.media_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_valid_name(filename: str) -> bool:
        return bool(filename and NAME_RE.match(filename))

    def resolve(self, filename: Optional[str]) -> Optional[Path]:
        name = (filename or '').strip()
        if not self.is_valid_name(name):
            return None
        path = (self.media_dir / name).resolve()
        if self.media_dir.resolve() not in path.parents and path.parent != self.media_dir.resolve():
            return None
        if not path.is_file():
            return None
        return path

    def public_url(self, filename: str) -> str:
        return f'/media/messages/{filename}'

    def save(self, content: bytes, content_type: str = '', original_name: str = '') -> str:
        if not content:
            raise ValueError('图片内容为空')
        if len(content) > MAX_BYTES:
            raise ValueError('图片不能超过 10MB')

        ext = ALLOWED_TYPES.get((content_type or '').lower().split(';')[0].strip())
        if not ext:
            suffix = Path(original_name or '').suffix.lower()
            if suffix == '.jpeg':
                suffix = '.jpg'
            if suffix in ALLOWED_EXT:
                ext = '.jpg' if suffix == '.jpeg' else suffix
        if not ext:
            raise ValueError('只支持 jpg / png / webp / gif')

        filename = f'img_{secrets.token_hex(8)}{ext}'
        path = self.media_dir / filename
        path.write_bytes(content)
        return filename
