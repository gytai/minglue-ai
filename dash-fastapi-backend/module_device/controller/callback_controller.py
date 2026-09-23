"""明略厂商回调接收入口（契约 §2）。

单独成模块的原因：`device_controller` 会连带导入 `module_admin` 的日志/鉴权/登录
链路，那条链路依赖 loguru、apscheduler 等运行期依赖。回调入口是厂商直连的对外
面，应当能在只装测试依赖的环境里被 import 并发起请求级测试（与契约 §5.7 F9
同源），所以这里只依赖 fastapi + `module_device` 服务层，数据库会话延迟导入。

路由、Content-Type、参数位置、成功响应与错误响应见契约 §2 / §5.3：

- `POST /open/minglue/callback` 与 `POST /open/minglue/callback/{event_type}`
- 成功统一 `200 {"code": 0}`（心跳是文档唯一明确定义响应体的一类，其余沿用）
- 签名校验失败 `401`；同步落库超时 `503`（两者都先留审计痕迹）
"""

import asyncio
import hashlib
import hmac
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from module_device.service.device_service import CallbackService

callbackController = APIRouter(prefix='/open/minglue')

#: 契约 §2.8：厂商明确"回调地址响应超过 3s 即视为失败"。
VENDOR_RESPONSE_LIMIT_SECONDS = 3.0
#: 同步落库预算，留 0.5s 给 ASGI/网络写出，保证整体仍落在厂商时限内。
DEFAULT_RESPONSE_BUDGET_SECONDS = 2.5


async def get_callback_db():
    """回调落库会话。

    延迟导入 `config.get_db`，避免仅为接收回调而拉起 admin 运行期依赖；
    测试通过 `dependency_overrides` 注入内存 SQLite 会话。
    """
    from config.get_db import get_db

    async for session in get_db():
        yield session


def response_budget_seconds() -> float:
    """本次回调允许占用的同步落库时间（秒）。

    `MINGLUE_CALLBACK_RESPONSE_BUDGET` 可覆盖默认值；置 0 或负值表示关闭超时
    保护（仅用于排障，生产不建议）。
    """
    raw = os.getenv('MINGLUE_CALLBACK_RESPONSE_BUDGET', '').strip()
    if not raw:
        return DEFAULT_RESPONSE_BUDGET_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_RESPONSE_BUDGET_SECONDS


def callback_secret() -> str:
    """本系统加固用的回调签名密钥；为空即关闭签名校验（默认）。"""
    return os.getenv('MINGLUE_CALLBACK_SECRET', '').strip()


def verify_callback_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """校验本系统的加固签名（契约 B13）。

    **厂商文档未定义任何回调签名机制**，因此这里不是厂商标准，只是接入方自选的
    加固手段，兼容策略如下：

    - `MINGLUE_CALLBACK_SECRET` 留空（默认）：完全不校验，任何报文照常接收；
    - 配置了密钥：接受 `X-Signature` 或 `X-Callback-Signature`，值为
      `hex(hmac_sha256(secret, 原始请求体))`，允许带 `sha256=` 前缀、不区分大小写；
      缺失或不匹配一律 `401` 并落 `signature_valid='N'` 的拒绝记录。
    """
    secret = callback_secret()
    if not secret:
        return True
    if not signature:
        return False
    normalized = signature.removeprefix('sha256=').strip().lower()
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(normalized, expected)


@callbackController.post('/callback')
@callbackController.post('/callback/{event_type}')
async def receive_callback(
    request: Request,
    event_type: Optional[str] = None,
    payload: Dict[str, Any] = Body(...),
    x_signature: Optional[str] = Header(default=None, alias='X-Signature'),
    x_callback_signature: Optional[str] = Header(default=None, alias='X-Callback-Signature'),
    query_db: AsyncSession = Depends(get_callback_db),
):
    """接收明略设备回调；事件字段未定时仍完整保存原始 JSON。

    厂商文档未定义回调签名；`MINGLUE_CALLBACK_SECRET` 是本系统的加固能力，
    默认关闭，不得对外声称是厂商标准（契约 B13）。
    """
    request_ip = request.headers.get('X-Forwarded-For') or (request.client.host if request.client else None)

    if callback_secret() and not verify_callback_signature(
        await request.body(), x_signature or x_callback_signature
    ):
        # B14：校验失败也必须留审计痕迹，记录 signature_valid='N'，且不改动设备状态。
        await CallbackService.record_rejected(query_db, payload, event_type, request_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='回调签名校验失败')

    budget = response_budget_seconds()
    if budget <= 0:
        await CallbackService.receive(query_db, payload, event_type, request_ip)
        return JSONResponse(status_code=status.HTTP_200_OK, content={'code': 0})

    started = time.monotonic()
    try:
        await asyncio.wait_for(
            CallbackService.receive(query_db, payload, event_type, request_ip), timeout=budget
        )
    except asyncio.TimeoutError:
        # B3：厂商对超过 3s 的响应判失败，因此这里必须先把响应交出去。降级行为：
        # 回滚未提交的业务改动 → 用独立事务落一条 process_status='timeout' 的
        # 审计记录（payload_json 完整保留，可据此重放）→ 返回可重试的 503。
        elapsed = time.monotonic() - started
        await query_db.rollback()
        await CallbackService.record_timeout(query_db, payload, event_type, request_ip, elapsed)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={'code': 1, 'message': '回调处理超时，原始报文已留存，请重试'},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={'code': 0})
