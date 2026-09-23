from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DeviceModel(BaseModel):
    """设备新增、编辑和详情模型。"""

    model_config = ConfigDict(from_attributes=True)

    device_id: Optional[int] = Field(default=None, description='设备主键')
    device_code: str = Field(min_length=1, max_length=100, description='设备唯一编码/SN')
    device_name: Optional[str] = Field(default='', max_length=100, description='设备名称')
    model: Optional[str] = Field(default='', max_length=100, description='设备型号')
    firmware_version: Optional[str] = Field(default='', max_length=100, description='固件版本')
    status: Literal['online', 'offline', 'disabled'] = Field(default='offline', description='本地在线状态')
    online_status: Optional[int] = Field(default=None, ge=0, le=2, description='心跳 online 三态：2在线 1开机未在线 0离线')
    device_status: Optional[int] = Field(default=None, ge=0, le=1, description='心跳 device_status：1正常在线 0离线')
    bind_status: Literal['bound', 'unbound'] = Field(default='unbound', description='绑定状态')
    owner_name: Optional[str] = Field(default='', max_length=100, description='使用人')
    owner_phone: Optional[str] = Field(default='', max_length=50, description='手机号')
    department: Optional[str] = Field(default='', max_length=100, description='所属部门')
    battery_level: Optional[int] = Field(default=None, ge=0, le=100, description='电量百分比')
    signal_strength: Optional[int] = Field(default=None, description='信号强度')
    record_status: Optional[int] = Field(default=None, ge=0, le=1, description='录音状态')
    remain_storage: Optional[int] = Field(default=None, ge=0, description='剩余存储空间MB')
    total_storage: Optional[int] = Field(default=None, ge=0, description='总存储空间MB')
    battery_voltage: Optional[int] = Field(default=None, description='电池电压mV')
    battery_current: Optional[int] = Field(default=None, description='电池电流mA')
    charged_status: Optional[str] = Field(default=None, max_length=30, description='充电状态')
    key_status: Optional[int] = Field(default=None, ge=0, le=1, description='开关键状态')
    usb_status: Optional[int] = Field(default=None, ge=0, le=1, description='USB状态')
    disk_mount_status: Optional[int] = Field(default=None, ge=0, le=1, description='磁盘挂载状态')
    chip: Optional[str] = Field(default=None, max_length=100, description='芯片类型')
    ip_address: Optional[str] = Field(default=None, max_length=64, description='设备IP')
    today_record_seconds: Optional[int] = Field(default=None, ge=0, description='当日录音秒数')
    pending_recordings: Optional[int] = Field(default=None, ge=0, description='待上传录音数')
    tenant_id: Optional[int] = Field(default=None, description='厂商租户ID')
    audio_id: Optional[str] = Field(default=None, max_length=100, description='API录音标识')
    last_seen_time: Optional[datetime] = Field(default=None, description='最后在线时间')
    heartbeat_time: Optional[datetime] = Field(default=None, description='设备上报的心跳时间原值')
    config_json: Optional[str] = Field(default=None, description='厂商最新配置快照JSON')
    config_last_upload_time: Optional[datetime] = Field(default=None, description='厂商配置最后上传时间')
    config_synced_at: Optional[datetime] = Field(default=None, description='本地配置同步时间')
    activated_at: Optional[datetime] = Field(default=None, description='激活时间')
    create_by: Optional[str] = Field(default='', description='创建者')
    create_time: Optional[datetime] = Field(default=None, description='创建时间')
    update_by: Optional[str] = Field(default='', description='更新者')
    update_time: Optional[datetime] = Field(default=None, description='更新时间')
    remark: Optional[str] = Field(default=None, max_length=500, description='备注')

    @field_validator('device_code')
    @classmethod
    def strip_device_code(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError('设备编码不能为空')
        return value


class DevicePageQueryModel(BaseModel):
    device_code: Optional[str] = Field(default=None, description='设备编码')
    device_name: Optional[str] = Field(default=None, description='设备名称')
    model: Optional[str] = Field(default=None, description='设备型号')
    status: Optional[str] = Field(default=None, description='本地在线状态')
    online_status: Optional[int] = Field(default=None, description='心跳 online 三态')
    bind_status: Optional[str] = Field(default=None, description='绑定状态')
    owner_name: Optional[str] = Field(default=None, description='使用人')
    page_num: int = Field(default=1, ge=1, description='当前页码')
    page_size: int = Field(default=10, ge=1, le=500, description='每页记录数')


class DeleteDeviceModel(BaseModel):
    device_ids: str = Field(description='设备主键，多个用逗号分隔')


class DeviceStatusUpdateModel(BaseModel):
    """本地状态字段维护：只允许改在线状态与绑定状态。

    远端同步（回调 / 批量状态）覆盖的是遥测字段，这里用于人工纠正本地状态，
    停用（``disabled``）也走这个入口。
    """

    device_id: int = Field(gt=0, description='设备主键')
    status: Optional[Literal['online', 'offline', 'disabled']] = Field(default=None, description='本地在线状态')
    bind_status: Optional[Literal['bound', 'unbound']] = Field(default=None, description='绑定状态')

    @model_validator(mode='after')
    def require_any_field(self):
        if self.status is None and self.bind_status is None:
            raise ValueError('请至少指定 status 或 bind_status')
        return self


class CallbackPageQueryModel(BaseModel):
    event_type: Optional[str] = Field(default=None, description='事件类型')
    device_code: Optional[str] = Field(default=None, description='设备编码')
    process_status: Optional[str] = Field(default=None, description='处理状态')
    duplicate_flag: Optional[str] = Field(default=None, description='是否重复回调（Y/N）')
    session_id: Optional[int] = Field(default=None, description='回调配置ID')
    begin_time: Optional[str] = Field(default=None, description='接收时间起，YYYY-MM-DD')
    end_time: Optional[str] = Field(default=None, description='接收时间止，YYYY-MM-DD')
    page_num: int = Field(default=1, ge=1, description='当前页码')
    page_size: int = Field(default=10, ge=1, le=500, description='每页记录数')


class CallbackPayloadModel(BaseModel):
    """兼容型回调载荷；保留厂商全部字段。"""

    model_config = ConfigDict(extra='allow')

    event_id: Optional[str] = None
    event_type: Optional[str] = None
    device_code: Optional[str] = None
    event_time: Optional[datetime] = None
    data: Optional[Dict[str, Any]] = None


class DeviceBatchModel(BaseModel):
    sns: list[str] = Field(min_length=1, max_length=100, description='设备序列号列表')

    @field_validator('sns')
    @classmethod
    def normalize_sns(cls, value: list[str]):
        normalized = list(dict.fromkeys(item.strip() for item in value if item and item.strip()))
        if not normalized:
            raise ValueError('设备序列号不能为空')
        return normalized


class StartRecordingModel(BaseModel):
    audio_id: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=10,
        pattern=r'^[a-z0-9]{2,10}$',
        description='音频ID，仅允许2-10位小写字母或数字',
    )


class RecordingPageQueryModel(BaseModel):
    """录音文件与转码/ASR 产物查询。"""

    device_code: Optional[str] = Field(default=None, description='设备编码')
    object_key: Optional[str] = Field(default=None, description='对象存储路径')
    record_status: Optional[str] = Field(default=None, description='本地处理状态')
    transcode_task_id: Optional[str] = Field(default=None, description='转码任务ID')
    asr_task_id: Optional[str] = Field(default=None, description='ASR任务ID')
    begin_time: Optional[str] = Field(default=None, description='事件时间起，YYYY-MM-DD')
    end_time: Optional[str] = Field(default=None, description='事件时间止，YYYY-MM-DD')
    page_num: int = Field(default=1, ge=1, description='当前页码')
    page_size: int = Field(default=10, ge=1, le=500, description='每页记录数')


class ControlLogPageQueryModel(BaseModel):
    """设备控制指令日志查询。"""

    device_code: Optional[str] = Field(default=None, description='设备编码')
    command: Optional[str] = Field(default=None, description='指令类型')
    request_status: Optional[str] = Field(default=None, description='本地请求结果')
    msg_id: Optional[str] = Field(default=None, description='厂商消息ID')
    begin_time: Optional[str] = Field(default=None, description='创建时间起，YYYY-MM-DD')
    end_time: Optional[str] = Field(default=None, description='创建时间止，YYYY-MM-DD')
    page_num: int = Field(default=1, ge=1, description='当前页码')
    page_size: int = Field(default=10, ge=1, le=500, description='每页记录数')
