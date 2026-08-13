"""
代理管理器 - 应用单例模式
统一维护代理配置，供账号登录、导入与连接时复用，并提供在线连通性测试
"""

import asyncio
import json
import secrets
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import socks

from utils.logger import get_logger
from utils.singleton import Singleton

SUPPORTED_TYPES = ('socks5', 'socks4', 'http')

_SOCKS_TYPES = {
    'socks5': socks.SOCKS5,
    'socks4': socks.SOCKS4,
    'http': socks.HTTP,
}

# 测试连通性时使用的 Telegram 接入点（DC2，官方公开地址）
TEST_ENDPOINT = ('149.154.167.51', 443)
TEST_TIMEOUT = 15


def build_proxy_tuple(proxy_config: Optional[Dict[str, Any]]) -> Optional[tuple]:
    """把代理配置字典转换成 Telethon / PySocks 需要的元组"""
    if not proxy_config:
        return None

    socks_type = _SOCKS_TYPES.get(str(proxy_config.get('type', '')).lower())
    host = proxy_config.get('host')
    port = proxy_config.get('port')

    if not socks_type or not host or not port:
        return None

    username = proxy_config.get('username')
    password = proxy_config.get('password')

    if username and password:
        return (socks_type, host, int(port), True, username, password)
    return (socks_type, host, int(port))


class ProxyManager(metaclass=Singleton):

    def __init__(self):
        self.proxies: Dict[str, Dict[str, Any]] = {}
        self.proxies_file = Path("data/proxies.json")
        self.logger = get_logger(__name__)

        self._load_proxies()

    # -------------------------------------------------------------- 持久化

    def _load_proxies(self):
        if not self.proxies_file.exists():
            return

        try:
            with open(self.proxies_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for proxy in data.get('proxies', []):
                self.proxies[proxy['proxy_id']] = proxy

            self.logger.info(f"已加载 {len(self.proxies)} 个代理配置")
        except Exception as e:
            self.logger.error(f"加载代理配置失败: {e}")

    def _save_proxies(self):
        try:
            self.proxies_file.parent.mkdir(parents=True, exist_ok=True)

            ordered = sorted(self.proxies.values(), key=lambda p: p.get('created_at', ''))
            with open(self.proxies_file, 'w', encoding='utf-8') as f:
                json.dump({'proxies': ordered}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存代理配置失败: {e}")

    # ---------------------------------------------------------------- 校验

    @staticmethod
    def validate(data: Dict[str, Any]) -> Optional[str]:
        """校验代理配置，通过返回 None，否则返回中文错误说明"""
        proxy_type = str(data.get('type', '')).lower()
        if proxy_type not in SUPPORTED_TYPES:
            return f"代理类型只支持 {', '.join(SUPPORTED_TYPES)}"

        host = str(data.get('host', '')).strip()
        if not host:
            return "请填写代理主机地址"
        if ' ' in host:
            return "代理主机地址不能包含空格"

        try:
            port = int(data.get('port'))
        except (TypeError, ValueError):
            return "端口号必须是数字"
        if not 1 <= port <= 65535:
            return "端口号需在 1-65535 之间"

        username = data.get('username') or ''
        password = data.get('password') or ''
        if password and not username:
            return "填写了密码就必须同时填写用户名"

        return None

    # ------------------------------------------------------------ 读取与转换

    @staticmethod
    def _public_view(proxy: Dict[str, Any]) -> Dict[str, Any]:
        """对外返回的视图，隐藏密码明文"""
        view = {k: v for k, v in proxy.items() if k != 'password'}
        view['has_password'] = bool(proxy.get('password'))
        return view

    def list_proxies(self) -> List[Dict[str, Any]]:
        ordered = sorted(self.proxies.values(), key=lambda p: p.get('created_at', ''))
        return [self._public_view(proxy) for proxy in ordered]

    def get_proxy(self, proxy_id: str) -> Optional[Dict[str, Any]]:
        return self.proxies.get(proxy_id)

    def build_proxy_config(self, proxy_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 取出可直接传给 AccountFactory 的代理配置"""
        proxy = self.get_proxy(proxy_id)
        if not proxy:
            return None

        return {
            'type': proxy['type'],
            'host': proxy['host'],
            'port': proxy['port'],
            'username': proxy.get('username'),
            'password': proxy.get('password'),
        }

    def to_telethon_proxy(self, proxy_id: str) -> Optional[tuple]:
        return build_proxy_tuple(self.build_proxy_config(proxy_id))

    # -------------------------------------------------------------- 增删改

    def add_proxy(self, data: Dict[str, Any]) -> Dict[str, Any]:
        proxy_id = f"proxy_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}"
        host = str(data['host']).strip()
        port = int(data['port'])

        proxy = {
            'proxy_id': proxy_id,
            'name': (data.get('name') or '').strip() or f"{host}:{port}",
            'type': str(data['type']).lower(),
            'host': host,
            'port': port,
            'username': (data.get('username') or '').strip(),
            'password': data.get('password') or '',
            'created_at': datetime.now().isoformat(),
            'last_test': None,
        }

        self.proxies[proxy_id] = proxy
        self._save_proxies()
        self.logger.info(f"新增代理配置: {proxy['name']} ({proxy['type']})")

        return self._public_view(proxy)

    def update_proxy(self, proxy_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        proxy = self.proxies.get(proxy_id)
        if not proxy:
            return None

        host = str(data['host']).strip()
        port = int(data['port'])

        proxy['name'] = (data.get('name') or '').strip() or f"{host}:{port}"
        proxy['type'] = str(data['type']).lower()
        proxy['host'] = host
        proxy['port'] = port
        proxy['username'] = (data.get('username') or '').strip()

        # 密码留空表示沿用原密码，清空用户名时同步清空密码
        if data.get('password'):
            proxy['password'] = data['password']
        elif not proxy['username']:
            proxy['password'] = ''

        self._save_proxies()
        self.logger.info(f"更新代理配置: {proxy['name']}")

        return self._public_view(proxy)

    def delete_proxy(self, proxy_id: str) -> bool:
        if proxy_id not in self.proxies:
            return False

        name = self.proxies[proxy_id].get('name', proxy_id)
        del self.proxies[proxy_id]
        self._save_proxies()
        self.logger.info(f"删除代理配置: {name}")

        return True

    # ---------------------------------------------------------------- 测试

    async def test_proxy(
        self,
        proxy_config: Dict[str, Any],
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        proxy_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """测试代理能否连通 Telegram

        有 API 凭据时走完整的 MTProto 握手，否则退化为通过代理建立 TCP 连接。
        """
        telethon_proxy = build_proxy_tuple(proxy_config)
        if not telethon_proxy:
            return {'ok': False, 'message': '代理配置不完整', 'latency_ms': 0}

        if api_id and api_hash:
            result = await self._test_via_telegram(telethon_proxy, api_id, api_hash)
        else:
            result = await self._test_via_socket(telethon_proxy)

        if proxy_id and proxy_id in self.proxies:
            self.proxies[proxy_id]['last_test'] = {**result, 'time': datetime.now().isoformat()}
            self._save_proxies()

        return result

    async def _test_via_telegram(self, telethon_proxy: tuple, api_id: int, api_hash: str) -> Dict[str, Any]:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            proxy=telethon_proxy,
            timeout=TEST_TIMEOUT,
            connection_retries=1,
            retry_delay=0
        )

        started = time.monotonic()
        try:
            await asyncio.wait_for(client.connect(), timeout=TEST_TIMEOUT + 5)
            latency = int((time.monotonic() - started) * 1000)

            if not client.is_connected():
                return {'ok': False, 'message': '代理已连接但无法与 Telegram 建立会话', 'latency_ms': latency}

            return {'ok': True, 'message': f'连接正常，握手耗时 {latency} ms', 'latency_ms': latency}
        except asyncio.TimeoutError:
            return {'ok': False, 'message': f'连接超时（超过 {TEST_TIMEOUT} 秒）', 'latency_ms': 0}
        except Exception as e:
            return {'ok': False, 'message': str(e) or e.__class__.__name__, 'latency_ms': 0}
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _test_via_socket(self, telethon_proxy: tuple) -> Dict[str, Any]:
        def connect() -> int:
            sock = socks.socksocket()
            sock.settimeout(TEST_TIMEOUT)
            try:
                if len(telethon_proxy) >= 6:
                    sock.set_proxy(
                        telethon_proxy[0], telethon_proxy[1], telethon_proxy[2],
                        True, telethon_proxy[4], telethon_proxy[5]
                    )
                else:
                    sock.set_proxy(telethon_proxy[0], telethon_proxy[1], telethon_proxy[2])

                started = time.monotonic()
                sock.connect(TEST_ENDPOINT)
                return int((time.monotonic() - started) * 1000)
            finally:
                try:
                    sock.close()
                except Exception:
                    pass

        loop = asyncio.get_event_loop()
        try:
            latency = await loop.run_in_executor(None, connect)
            return {
                'ok': True,
                'message': f'代理可达 Telegram 服务器，耗时 {latency} ms（未配置 API 凭据，仅测试了 TCP 连通性）',
                'latency_ms': latency
            }
        except socket.timeout:
            return {'ok': False, 'message': f'连接超时（超过 {TEST_TIMEOUT} 秒）', 'latency_ms': 0}
        except Exception as e:
            return {'ok': False, 'message': str(e) or e.__class__.__name__, 'latency_ms': 0}
