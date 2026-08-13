"""
Session 配套元数据解析

.session 文件本身只保存 auth_key 和数据中心信息，不含 api_id / api_hash。
多数 session 来源会附带一个同名 .json，里面记录了创建该 session 时使用的
API 凭据和设备信息，导入时读取它可以免去手工填写，也能让后续连接保持
与首次登录一致的设备指纹。
"""

import json
from typing import Any, Dict, Optional

# 不同来源的字段命名不一致，按优先级依次尝试
_API_ID_KEYS = ('app_id', 'api_id', 'appId', 'apiId')
_API_HASH_KEYS = ('app_hash', 'api_hash', 'appHash', 'apiHash')
_PHONE_KEYS = ('phone', 'phone_number', 'phoneNumber')

# 值为 Telethon 客户端对应的参数名
_DEVICE_KEYS = {
    'device': 'device_model',
    'device_model': 'device_model',
    'deviceModel': 'device_model',
    'sdk': 'system_version',
    'system_version': 'system_version',
    'systemVersion': 'system_version',
    'app_version': 'app_version',
    'appVersion': 'app_version',
    'lang_code': 'lang_code',
    'langCode': 'lang_code',
    'system_lang_code': 'system_lang_code',
    'system_lang_pack': 'system_lang_code',
    'systemLangCode': 'system_lang_code',
    # 官方客户端会上报自己的语言包标识（如 tdesktop），Telethon 不带这个参数，
    # 由 AccountManager.create_client 在建好客户端后单独写回
    'lang_pack': 'lang_pack',
    'langPack': 'lang_pack',
}


def _first_value(data: Dict[str, Any], keys) -> Optional[Any]:
    for key in keys:
        value = data.get(key)
        if value not in (None, ''):
            return value
    return None


def parse_session_metadata(raw: bytes) -> Dict[str, Any]:
    """解析配套 json，返回 {api_id, api_hash, phone, device_params}

    字段缺失或格式不对时对应项为 None，不会抛异常，交由调用方回退到其他来源。
    """
    result: Dict[str, Any] = {
        'api_id': None,
        'api_hash': None,
        'phone': None,
        'device_params': {},
    }

    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except Exception:
        return result

    if not isinstance(data, dict):
        return result

    raw_id = _first_value(data, _API_ID_KEYS)
    if raw_id is not None:
        try:
            api_id = int(str(raw_id).strip())
            if api_id > 0:
                result['api_id'] = api_id
        except (TypeError, ValueError):
            pass

    raw_hash = _first_value(data, _API_HASH_KEYS)
    if isinstance(raw_hash, str) and raw_hash.strip():
        result['api_hash'] = raw_hash.strip()

    raw_phone = _first_value(data, _PHONE_KEYS)
    if raw_phone is not None:
        phone = str(raw_phone).strip()
        if phone:
            result['phone'] = f"+{phone.lstrip('+')}"

    for key, param in _DEVICE_KEYS.items():
        value = data.get(key)
        if param in result['device_params'] or not isinstance(value, (str, int, float)):
            continue

        text = str(value).strip()
        # lang_pack 之类的字段常被填成 "android"，长度超出语言代码范围的一律不采信
        if text and not (param.endswith('lang_code') and len(text) > 8):
            result['device_params'][param] = text

    return result
