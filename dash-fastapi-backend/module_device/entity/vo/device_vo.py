from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeviceModel(BaseModel):
    """设备新增、编辑和详情模型。"""

    model_config = ConfigDict(from_attributes=True)

    device_id: Optional[int] = Field(default=None, description='设备主键')
    device_code: str = Field(min_length=1, max_length=100, description='设备唯一编码/SN')
    device_name: Optional[str] = Field(default='', max_length=100, description='设备名称')
    model: Optional[str] = Field(default='', max_length=100, description='设备型号')
    firmware_version: Optional[str] = Field(default='', max_length=100, description='固件版本')
    status: Literal['online', 'offline', 'disabled'] = Field(default='offline', description='在线状态')
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
    activated_at: Optional[datetime] = Field(default=None, description='激活时间')
    config_json: Optional[str] = Field(default=None, description='扩展配置JSON')
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
    status: Optional[str] = Field(default=None, description='在线状态')
    bind_status: Optional[str] = Field(default=None, description='绑定状态')
    owner_name: Optional[str] = Field(default=None, description='使用人')
    page_num: int = Field(default=1, ge=1, description='当前页码')
    page_size: int = Field(default=10, ge=1, le=500, description='每页记录数')


class DeleteDeviceModel(BaseModel):
    device_ids: str = Field(description='设备主键，多个用逗号分隔')


class DeviceStatusModel(BaseModel):
    status: Literal['online', 'offline', 'disabled']


class CallbackPageQueryModel(BaseModel):
    event_type: Optional[str] = None
    device_code: Optional[str] = None
    process_status: Optional[str] = None
    begin_time: Optional[str] = None
    end_time: Optional[str] = None
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=500)


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
