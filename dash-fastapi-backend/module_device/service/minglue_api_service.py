import asyncio
import base64
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from exceptions.exception import ServiceException

#: 错误信息里最多回显的厂商响应片段长度，避免整包报文进入日志与响应。
MAX_ERROR_DETAIL = 200

#: 需要从错误信息里抹掉的凭据形态：JWT 与长 Base64/十六进制串
#: （涵盖加密后的密码、access_token，以及带签名的下载地址片段）。
_CREDENTIAL_LIKE = re.compile(r'(?:eyJ[\w.-]{10,}|[A-Za-z0-9+/_=-]{24,})')


class MinglueApiService:
    """明略 AIOT 接口客户端（契约 §4）。

    统一响应包络为 ``{code, message, data}``，``code=0`` 为成功；除 200 外，
    400/404 只有 ``code``/``message`` 而没有 ``data``（契约 §4 表）。

    厂商文档未定义、但必须能在真实环境切换验证的两项，做成环境变量：

    - ``MINGLUE_API_AUTH_SCHEME``（契约 U1）：鉴权头是否带 ``Bearer `` 前缀，
      置空即发送裸 token。
    - ``MINGLUE_API_BATCH_SN_QUERY``（契约 U2）：批量接口是否同时发送 query
      ``sn``。默认 ``true``（与文档"sn 必填"一致），置 ``false`` 只发 body ``sns``。
    """

    _token: Optional[str] = None
    _token_expires_at: float = 0.0
    _token_lock = asyncio.Lock()
    #: 测试注入点：非空时用它构造 httpx.AsyncClient，便于用 MockTransport 起 mock 厂商服务。
    _transport: Optional[httpx.AsyncBaseTransport] = None

    @classmethod
    def reset_token_cache(cls):
        """清空进程内 token 缓存；配置变更或需要强制重新登录时调用。"""
        cls._token = None
        cls._token_expires_at = 0.0

    @classmethod
    def _settings(cls):
        base_url = os.getenv('MINGLUE_API_BASE_URL', '').strip().rstrip('/')
        username = os.getenv('MINGLUE_API_USERNAME', '').strip()
        password = os.getenv('MINGLUE_API_PASSWORD', '')
        aes_key = os.getenv('MINGLUE_API_AES_KEY', '')
        if not all((base_url, username, password, aes_key)):
            raise ServiceException(
                message=(
                    '明略接口未配置，请设置 MINGLUE_API_BASE_URL、'
                    'MINGLUE_API_USERNAME、MINGLUE_API_PASSWORD 和 MINGLUE_API_AES_KEY'
                )
            )
        if len(aes_key.encode('utf-8')) != 16:
            raise ServiceException(message='MINGLUE_API_AES_KEY 必须是 16 字节 AES-128 密钥')
        try:
            timeout = float(os.getenv('MINGLUE_API_TIMEOUT', '10'))
        except ValueError as exc:
            raise ServiceException(message='MINGLUE_API_TIMEOUT 必须是数字') from exc
        return base_url, username, password, aes_key, timeout

    @classmethod
    def _client(cls, base_url: str, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=cls._transport)

    @staticmethod
    def encrypt_password(password: str, aes_key: str) -> str:
        """按厂商要求使用 AES-128-ECB + PKCS7，并输出 Base64（契约 §1.1）。"""
        key = aes_key.encode('utf-8')
        padder = PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(password.encode('utf-8')) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(encrypted).decode('ascii')

    @classmethod
    def _sanitize(cls, detail: Any) -> str:
        """错误信息脱敏：不泄露密码、token 与厂商敏感报文（契约 C11 / E9）。

        先按值替换当前 token/密码/AES 密钥，再按形态抹掉 JWT 与长 Base64 串，
        最后压缩空白并截断长度。
        """
        if detail in (None, ''):
            return ''
        message = str(detail)
        for secret in (
            cls._token,
            os.getenv('MINGLUE_API_PASSWORD', ''),
            os.getenv('MINGLUE_API_AES_KEY', ''),
        ):
            # 只替换足够长的凭据，避免用 '1' 这类短值把正常报文切碎。
            if secret and len(secret) >= 6:
                message = message.replace(secret, '***')
        message = _CREDENTIAL_LIKE.sub('***', message)
        return ' '.join(message.split())[:MAX_ERROR_DETAIL]

    @staticmethod
    def _authorization(token: str) -> str:
        """按 ``MINGLUE_API_AUTH_SCHEME`` 组装鉴权头（契约 U1）。"""
        scheme = os.getenv('MINGLUE_API_AUTH_SCHEME', 'Bearer').strip()
        return f'{scheme} {token}'.strip()

    @classmethod
    def _unwrap(cls, result: Any, operation: str) -> Any:
        """拆解厂商统一响应包络（契约 §4）。

        - 非 JSON 对象 → 非法响应
        - 缺少 ``code`` → 非法响应
        - ``code != 0`` → 厂商业务错误，只回传脱敏后的 ``message``
        - ``code == 0`` → 返回 ``data``
        """
        if not isinstance(result, dict):
            raise ServiceException(message=f'{operation}失败：明略接口响应不是合法的 JSON 对象')
        if 'code' not in result:
            raise ServiceException(message=f'{operation}失败：明略接口响应缺少 code 字段')
        raw_code = result.get('code')
        try:
            successful = int(raw_code) == 0
        except (TypeError, ValueError) as exc:
            raise ServiceException(message=f'{operation}失败：明略接口响应 code 非法') from exc
        if not successful:
            detail = cls._sanitize(result.get('message') or result.get('msg')) or '厂商未提供错误信息'
            raise ServiceException(message=f'{operation}失败（厂商错误码 {raw_code}）：{detail}')
        return result.get('data', result)

    @classmethod
    async def _get_token(cls, force_refresh: bool = False) -> str:
        if not force_refresh and cls._token and time.time() < cls._token_expires_at:
            return cls._token
        async with cls._token_lock:
            if not force_refresh and cls._token and time.time() < cls._token_expires_at:
                return cls._token
            base_url, username, password, aes_key, timeout = cls._settings()
            payload = {
                'username': username,
                'password': cls.encrypt_password(password, aes_key),
                'isLock': True,
            }
            try:
                async with cls._client(base_url, timeout) as client:
                    response = await client.post('/thiea/site/accountLogin', json=payload)
            except httpx.TimeoutException as exc:
                raise ServiceException(message=f'获取明略 access_token 失败：请求超时（{timeout}s）') from exc
            except httpx.HTTPError as exc:
                raise ServiceException(
                    message=f'获取明略 access_token 失败：无法访问明略接口（{cls._sanitize(exc)}）'
                ) from exc
            if response.status_code >= 400:
                raise ServiceException(
                    message=(
                        f'获取明略 access_token 失败：明略接口返回 '
                        f'HTTP {response.status_code} {cls._sanitize(response.text)}'
                    )
                )
            try:
                result = response.json()
            except ValueError as exc:
                raise ServiceException(message='获取明略 access_token 失败：明略接口响应不是合法 JSON') from exc
            data = cls._unwrap(result, '获取明略 access_token')
            if not isinstance(data, dict) or not data.get('token'):
                raise ServiceException(message='获取明略 access_token 失败：响应中缺少 token')
            cls._token = str(data['token'])
            try:
                expires = max(60, int(data.get('expires', 604800)))
            except (TypeError, ValueError):
                expires = 604800
            cls._token_expires_at = time.time() + max(30, expires - 60)
            return cls._token

    @classmethod
    async def _request(
        cls,
        method: str,
        path: str,
        operation: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """发送一次带鉴权的厂商请求，并把失败映射成可读的 ``ServiceException``。

        重试边界：**只有** 401/403 会强制刷新 token 后重试，且最多一次；
        超时、网络错误、4xx/5xx、业务错误与非法响应都不重试。
        """
        base_url, _, _, _, timeout = cls._settings()
        for attempt in (0, 1):
            token = await cls._get_token(force_refresh=attempt == 1)
            headers = {'Authorization': cls._authorization(token)}
            try:
                async with cls._client(base_url, timeout) as client:
                    response = await client.request(method, path, params=params, json=json, headers=headers)
            except httpx.TimeoutException as exc:
                raise ServiceException(message=f'{operation}失败：请求明略接口超时（{timeout}s）') from exc
            except httpx.HTTPError as exc:
                raise ServiceException(
                    message=f'{operation}失败：无法访问明略接口（{cls._sanitize(exc)}）'
                ) from exc
            if response.status_code in (401, 403):
                if attempt == 0:
                    continue
                raise ServiceException(
                    message=f'{operation}失败：明略接口认证未通过（HTTP {response.status_code}），请检查账号与鉴权头配置'
                )
            if response.status_code >= 400:
                raise ServiceException(
                    message=(
                        f'{operation}失败：明略接口返回 HTTP {response.status_code} '
                        f'{cls._sanitize(response.text)}'
                    )
                )
            try:
                result = response.json()
            except ValueError as exc:
                raise ServiceException(message=f'{operation}失败：明略接口响应不是合法 JSON') from exc
            return cls._unwrap(result, operation)
        # 两次尝试内必定返回或抛出，此处仅为控制流完整性。
        raise ServiceException(message=f'{operation}失败：明略接口认证未通过')

    @staticmethod
    def _entities(data: Any, operation: str) -> List[Dict[str, Any]]:
        """取出 ``data.entities``；结构非法时给出可读错误而不是让调用方崩在 `.get`。"""
        if not isinstance(data, dict):
            raise ServiceException(message=f'{operation}失败：响应 data 不是 JSON 对象')
        entities = data.get('entities')
        if not isinstance(entities, list):
            raise ServiceException(message=f'{operation}失败：响应缺少 entities 列表')
        return entities

    @classmethod
    def _batch_params(cls, sns: List[str]) -> Optional[Dict[str, Any]]:
        """契约 C2 / U2：query ``sn`` 是否必填文档未定。

        默认与文档一致（发送 ``sn=<首个序列号>``），可通过
        ``MINGLUE_API_BATCH_SN_QUERY=false`` 关闭，只依赖 body ``sns``。
        """
        disabled = os.getenv('MINGLUE_API_BATCH_SN_QUERY', 'true').strip().lower() in ('false', '0', 'no')
        return None if disabled or not sns else {'sn': sns[0]}

    @staticmethod
    def _require_sns(sns: List[str]) -> List[str]:
        normalized = [str(item).strip() for item in (sns or []) if str(item).strip()]
        if not normalized:
            raise ServiceException(message='设备序列号不能为空')
        return normalized

    @classmethod
    async def get_device_statuses(cls, sns: list[str]) -> List[Dict[str, Any]]:
        """批量获取设备状态（契约 §4.1），返回 ``data.entities``。"""
        normalized = cls._require_sns(sns)
        data = await cls._request(
            'POST',
            '/thiea/hermes/device/status/batch',
            '批量获取设备状态',
            params=cls._batch_params(normalized),
            json={'sns': normalized},
        )
        return cls._entities(data, '批量获取设备状态')

    @classmethod
    async def get_device_config_statuses(cls, sns: list[str]) -> List[Dict[str, Any]]:
        """批量获取设备最新配置状态（契约 §4.2），返回 ``data.entities``。"""
        normalized = cls._require_sns(sns)
        data = await cls._request(
            'POST',
            '/thiea/hermes/device/config/status/batch',
            '批量获取设备配置状态',
            params=cls._batch_params(normalized),
            json={'sns': normalized},
        )
        return cls._entities(data, '批量获取设备配置状态')

    @classmethod
    def _require_msg_id(cls, data: Any, operation: str) -> Dict[str, Any]:
        """录音控制响应必须带有 ``msg_id``，否则无法用指令日志回查（契约 §4.3）。"""
        if not isinstance(data, dict):
            raise ServiceException(message=f'{operation}失败：响应 data 不是 JSON 对象')
        if not data.get('msg_id'):
            raise ServiceException(message=f'{operation}失败：响应缺少 msg_id，无法回查指令结果')
        return data

    @classmethod
    async def start_recording(cls, sn: str, audio_id: Optional[str] = None) -> Dict[str, Any]:
        """开启设备录音（契约 §4.3）：query ``sn`` + body ``{"nm": 音频ID}``。"""
        data = await cls._request(
            'POST',
            '/thiea/hermes/device/start/shadow',
            '开启设备录音',
            params={'sn': sn},
            json={'nm': audio_id} if audio_id else {},
        )
        return cls._require_msg_id(data, '开启设备录音')

    @classmethod
    async def stop_recording(cls, sn: str) -> Dict[str, Any]:
        """停止设备录音（契约 §4.4）：query ``sn`` + 空 body。"""
        data = await cls._request(
            'POST',
            '/thiea/hermes/device/stop/shadow',
            '停止设备录音',
            params={'sn': sn},
            json={},
        )
        return cls._require_msg_id(data, '停止设备录音')

    @classmethod
    async def get_command_log(cls, sn: str, message_id: str) -> Dict[str, Any]:
        """获取设备指令接收日志（契约 §4.5）：query ``sn`` + ``msg_id``。"""
        data = await cls._request(
            'GET',
            '/thiea/hermes/device/cmd/receive/log',
            '获取设备指令接收日志',
            params={'sn': sn, 'msg_id': message_id},
        )
        if not isinstance(data, dict):
            raise ServiceException(message='获取设备指令接收日志失败：响应 data 不是 JSON 对象')
        if data.get('status') is not None:
            try:
                data['status'] = int(data['status'])
            except (TypeError, ValueError):
                raise ServiceException(message='获取设备指令接收日志失败：响应 status 不是整数') from None
        return data
