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
