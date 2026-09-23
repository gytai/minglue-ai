from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import BusinessType
from config.get_db import get_db
from module_admin.annotation.log_annotation import Log
from module_admin.aspect.interface_auth import CheckUserInterfaceAuth
from module_admin.entity.vo.user_vo import CurrentUserModel
from module_admin.service.login_service import LoginService
from module_device.controller.callback_controller import callbackController
from module_device.entity.vo.device_vo import (
    CallbackPageQueryModel,
    ControlLogPageQueryModel,
    DeleteDeviceModel,
    DeviceBatchModel,
    DeviceModel,
    DevicePageQueryModel,
    DeviceStatusUpdateModel,
    RecordingPageQueryModel,
    StartRecordingModel,
)
from module_device.service.device_service import (
    CallbackService,
    ControlLogService,
    DeviceService,
    RecordingService,
)
from module_device.service.minglue_api_service import MinglueApiService
from utils.page_util import PageResponseModel
from utils.response_util import ResponseUtil


deviceController = APIRouter(prefix='/device', dependencies=[Depends(LoginService.get_current_user)])

#: 厂商回调路由（`/open/minglue/callback`）定义在 callback_controller.py：那条路径
#: 由厂商直连，不应被 admin 侧的运行期依赖牵住。这里转出以保持既有导入路径可用。
__all__ = ['callbackController', 'deviceController']


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


@deviceController.put('/status', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:edit'))])
@Log(title='设备状态维护', business_type=BusinessType.UPDATE)
async def update_device_status(
    request: Request,
    command: DeviceStatusUpdateModel,
    query_db: AsyncSession = Depends(get_db),
    current_user: CurrentUserModel = Depends(LoginService.get_current_user),
):
    """维护本地状态字段（在线/绑定）；远端同步只覆盖遥测字段。"""
    result = await DeviceService.update_status(query_db, command, current_user.user.user_name)
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
@Log(title='设备配置同步', business_type=BusinessType.UPDATE)
async def sync_remote_config(
    request: Request,
    command: DeviceBatchModel,
    query_db: AsyncSession = Depends(get_db),
):
    result = await DeviceService.sync_remote_configs(query_db, command.sns)
    return ResponseUtil.success(data=result)


@deviceController.post(
    '/remote/offline-sweep', dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))]
)
async def mark_stale_offline(query_db: AsyncSession = Depends(get_db)):
    """心跳超时兜底：把超过 5 分钟未上报的设备置为离线。"""
    count = await DeviceService.mark_stale_devices_offline(query_db)
    return ResponseUtil.success(data={'offline': count})


@deviceController.post(
    '/{device_code}/recording/start',
    dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))],
)
@Log(title='开启设备录音', business_type=BusinessType.UPDATE)
async def start_device_recording(
    request: Request,
    device_code: str,
    command: StartRecordingModel,
    query_db: AsyncSession = Depends(get_db),
    current_user: CurrentUserModel = Depends(LoginService.get_current_user),
):
    try:
        result = await MinglueApiService.start_recording(device_code, command.audio_id)
    except Exception as exc:
        await ControlLogService.record(
            query_db,
            device_code=device_code,
            command='start_recording',
            audio_id=command.audio_id,
            error=exc,
            operator=current_user.user.user_name,
            request_ip=request.client.host if request.client else None,
        )
        raise
    await ControlLogService.record(
        query_db,
        device_code=device_code,
        command='start_recording',
        audio_id=command.audio_id,
        result=result if isinstance(result, dict) else None,
        operator=current_user.user.user_name,
        request_ip=request.client.host if request.client else None,
    )
    return ResponseUtil.success(data=result)


@deviceController.post(
    '/{device_code}/recording/stop',
    dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))],
)
@Log(title='停止设备录音', business_type=BusinessType.UPDATE)
async def stop_device_recording(
    request: Request,
    device_code: str,
    query_db: AsyncSession = Depends(get_db),
    current_user: CurrentUserModel = Depends(LoginService.get_current_user),
):
    try:
        result = await MinglueApiService.stop_recording(device_code)
    except Exception as exc:
        await ControlLogService.record(
            query_db,
            device_code=device_code,
            command='stop_recording',
            error=exc,
            operator=current_user.user.user_name,
            request_ip=request.client.host if request.client else None,
        )
        raise
    await ControlLogService.record(
        query_db,
        device_code=device_code,
        command='stop_recording',
        result=result if isinstance(result, dict) else None,
        operator=current_user.user.user_name,
        request_ip=request.client.host if request.client else None,
    )
    return ResponseUtil.success(data=result)


@deviceController.get(
    '/{device_code}/command/{message_id}',
    dependencies=[Depends(CheckUserInterfaceAuth('device:manage:control'))],
)
async def get_device_command_log(
    device_code: str, message_id: str, query_db: AsyncSession = Depends(get_db)
):
    """指令结果查询（契约 §4.5）：返回厂商接收日志与本地控制日志。"""
    result = await ControlLogService.resolve_command_result(query_db, device_code, message_id)
    return ResponseUtil.success(data=result)

@deviceController.get(
    '/recording/list',
    response_model=PageResponseModel,
    dependencies=[Depends(CheckUserInterfaceAuth('device:recording:list'))],
)
async def list_recordings(query: RecordingPageQueryModel = Query(), query_db: AsyncSession = Depends(get_db)):
    return ResponseUtil.success(model_content=await RecordingService.get_list(query_db, query))


@deviceController.get(
    '/control/list',
    response_model=PageResponseModel,
    dependencies=[Depends(CheckUserInterfaceAuth('device:control:list'))],
)
async def list_control_logs(query: ControlLogPageQueryModel = Query(), query_db: AsyncSession = Depends(get_db)):
    return ResponseUtil.success(model_content=await ControlLogService.get_list(query_db, query))


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
