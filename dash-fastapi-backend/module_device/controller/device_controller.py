import hashlib
import hmac
import os
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import BusinessType
from config.get_db import get_db
from module_admin.annotation.log_annotation import Log
from module_admin.aspect.interface_auth import CheckUserInterfaceAuth
from module_admin.entity.vo.user_vo import CurrentUserModel
from module_admin.service.login_service import LoginService
from module_device.entity.vo.device_vo import (
    CallbackPageQueryModel,
    DeleteDeviceModel,
    DeviceBatchModel,
    DeviceModel,
    DevicePageQueryModel,
    StartRecordingModel,
)
from module_device.service.device_service import CallbackService, DeviceService
from module_device.service.minglue_api_service import MinglueApiService
from utils.page_util import PageResponseModel
from utils.response_util import ResponseUtil


deviceController = APIRouter(prefix='/device', dependencies=[Depends(LoginService.get_current_user)])
callbackController = APIRouter(prefix='/open/minglue')


@deviceController.get(
    '/list', response_model=PageResponseModel, dependencies=[Depends(CheckUserInterfaceAuth('device:manage:list'))]
)
async def list_devices(query: DevicePageQueryModel = Query(), query_db: AsyncSession = Depends(get_db)):
    return ResponseUtil.success(model_content=await DeviceService.get_list(query_db, query))


@deviceController.get('/detail/{device_id}', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:query'))])
async def get_device(device_id: int, query_db: AsyncSession = Depends(get_db)):
    return ResponseUtil.success(data=await DeviceService.get_detail(query_db, device_id))


@deviceController.post('', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:add'))])
@Log(title='设备管理', business_type=BusinessType.INSERT)
async def add_device(
    request: Request,
    device: DeviceModel,
    query_db: AsyncSession = Depends(get_db),
    current_user: CurrentUserModel = Depends(LoginService.get_current_user),
):
    device.create_by = current_user.user.user_name
    device.update_by = current_user.user.user_name
    device.create_time = datetime.now()
    device.update_time = datetime.now()
    result = await DeviceService.add(query_db, device)
    return ResponseUtil.success(msg=result.message)


@deviceController.put('', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:edit'))])
@Log(title='设备管理', business_type=BusinessType.UPDATE)
async def edit_device(
    request: Request,
    device: DeviceModel,
    query_db: AsyncSession = Depends(get_db),
    current_user: CurrentUserModel = Depends(LoginService.get_current_user),
):
    device.update_by = current_user.user.user_name
    device.update_time = datetime.now()
    result = await DeviceService.edit(query_db, device)
    return ResponseUtil.success(msg=result.message)


@deviceController.delete('/{device_ids}', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:remove'))])
@Log(title='设备管理', business_type=BusinessType.DELETE)
async def delete_device(request: Request, device_ids: str, query_db: AsyncSession = Depends(get_db)):
    result = await DeviceService.delete(query_db, DeleteDeviceModel(device_ids=device_ids))
    return ResponseUtil.success(msg=result.message)


@deviceController.post(
    '/remote/status', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))]
)
@Log(title='设备状态同步', business_type=BusinessType.UPDATE)
async def sync_remote_status(
    request: Request,
    command: DeviceBatchModel,
    query_db: AsyncSession = Depends(get_db),
):
    result = await DeviceService.sync_remote_statuses(query_db, command.sns)
    return ResponseUtil.success(data=result)


@deviceController.post(
    '/remote/config', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))]
)
async def get_remote_config(command: DeviceBatchModel):
    result = await MinglueApiService.get_device_config_statuses(command.sns)
    return ResponseUtil.success(data=result)


@deviceController.post(
    '/{device_code}/recording/start',
    dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))],
)
@Log(title='开启设备录音', business_type=BusinessType.UPDATE)
async def start_device_recording(request: Request, device_code: str, command: StartRecordingModel):
    result = await MinglueApiService.start_recording(device_code, command.audio_id)
    return ResponseUtil.success(data=result)


@deviceController.post(
    '/{device_code}/recording/stop',
    dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))],
)
@Log(title='停止设备录音', business_type=BusinessType.UPDATE)
async def stop_device_recording(request: Request, device_code: str):
    result = await MinglueApiService.stop_recording(device_code)
    return ResponseUtil.success(data=result)


@deviceController.get(
    '/{device_code}/command/{message_id}',
    dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))],
)
async def get_device_command_log(device_code: str, message_id: str):
    result = await MinglueApiService.get_command_log(device_code, message_id)
    return ResponseUtil.success(data=result)


@deviceController.get(
    '/callback/list',
    response_model=PageResponseModel,
    dependencies=[Depends(CheckUserInterfaceAuth('device:callback:list'))],
)
async def list_callbacks(query: CallbackPageQueryModel = Query(), query_db: AsyncSession = Depends(get_db)):
    return ResponseUtil.success(model_content=await CallbackService.get_list(query_db, query))


@deviceController.get(
    '/callback/{callback_id}', dependencies=[Depends(CheckUserInterfaceAuth('device:callback:query'))]
)
async def get_callback(callback_id: int, query_db: AsyncSession = Depends(get_db)):
    return ResponseUtil.success(data=await CallbackService.get_detail(query_db, callback_id))


@deviceController.delete(
    '/callback/{callback_ids}', dependencies=[Depends(CheckUserInterfaceAuth('device:callback:remove'))]
)
@Log(title='设备回调日志', business_type=BusinessType.DELETE)
async def delete_callback(request: Request, callback_ids: str, query_db: AsyncSession = Depends(get_db)):
    result = await CallbackService.delete(query_db, callback_ids)
    return ResponseUtil.success(msg=result.message)


def _verify_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    secret = os.getenv('MINGLUE_CALLBACK_SECRET', '').strip()
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
    query_db: AsyncSession = Depends(get_db),
):
    """接收明略设备回调；事件字段未定时仍完整保存原始JSON。"""
    if not _verify_signature(await request.body(), x_signature or x_callback_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='回调签名校验失败')
    request_ip = request.headers.get('X-Forwarded-For') or (request.client.host if request.client else None)
    await CallbackService.receive(query_db, payload, event_type, request_ip)
    return JSONResponse(status_code=status.HTTP_200_OK, content={'code': 0})
