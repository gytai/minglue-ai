import asyncio
import base64
import os
import time
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from exceptions.exception import ServiceException


class MinglueApiService:
    """明略 AIOT 接口客户端。"""

    _token: Optional[str] = None
    _token_expires_at: float = 0
    _token_lock = asyncio.Lock()

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

    @staticmethod
    def encrypt_password(password: str, aes_key: str) -> str:
        """按厂商要求使用 AES-128-ECB + PKCS7，并输出 Base64。"""
        key = aes_key.encode('utf-8')
        padder = PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(password.encode('utf-8')) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(encrypted).decode('ascii')

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
                async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
                    response = await client.post('/thiea/site/accountLogin', json=payload)
                    response.raise_for_status()
                    result = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ServiceException(message=f'获取明略 access_token 失败：{exc}') from exc
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

    @staticmethod
    def _unwrap(result: Any, operation: str) -> Any:
        if not isinstance(result, dict):
            return result
        if 'code' in result:
            try:
                successful = int(result['code']) == 0
            except (TypeError, ValueError):
                successful = False
            if not successful:
                message = result.get('message') or result.get('msg') or '未知错误'
                raise ServiceException(message=f'{operation}失败：{message}')
            return result.get('data', result)
        return result

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
        base_url, _, _, _, timeout = cls._settings()
        for attempt in range(2):
            token = await cls._get_token(force_refresh=attempt == 1)
            scheme = os.getenv('MINGLUE_API_AUTH_SCHEME', 'Bearer').strip()
            authorization = f'{scheme} {token}'.strip()
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
                    response = await client.request(
                        method,
                        path,
                        params=params,
                        json=json,
                        headers={'Authorization': authorization},
                    )
            except httpx.HTTPError as exc:
                raise ServiceException(message=f'{operation}失败：{exc}') from exc
            if response.status_code in (401, 403) and attempt == 0:
                continue
            try:
                response.raise_for_status()
                result = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                detail = response.text[:500] if response.text else str(exc)
                raise ServiceException(message=f'{operation}失败：{detail}') from exc
            return cls._unwrap(result, operation)
        raise ServiceException(message=f'{operation}失败：认证未通过')

    @classmethod
    async def get_device_statuses(cls, sns: list[str]):
        return await cls._request(
            'POST',
            '/thiea/hermes/device/status/batch',
            '批量获取设备状态',
            params={'sn': sns[0]},
            json={'sns': sns},
        )

    @classmethod
    async def get_device_config_statuses(cls, sns: list[str]):
        return await cls._request(
            'POST',
            '/thiea/hermes/device/config/status/batch',
            '批量获取设备配置状态',
            params={'sn': sns[0]},
            json={'sns': sns},
        )

    @classmethod
    async def start_recording(cls, sn: str, audio_id: Optional[str] = None):
        payload = {'nm': audio_id} if audio_id else {}
        return await cls._request(
            'POST',
            '/thiea/hermes/device/start/shadow',
            '开启设备录音',
            params={'sn': sn},
            json=payload,
        )

    @classmethod
    async def stop_recording(cls, sn: str):
        return await cls._request(
            'POST',
            '/thiea/hermes/device/stop/shadow',
            '停止设备录音',
            params={'sn': sn},
            json={},
        )

    @classmethod
    async def get_command_log(cls, sn: str, message_id: str):
        return await cls._request(
            'GET',
            '/thiea/hermes/device/cmd/receive/log',
            '获取设备指令接收日志',
            params={'sn': sn, 'msg_id': message_id},
        )
