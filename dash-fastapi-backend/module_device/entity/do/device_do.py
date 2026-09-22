from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint

from config.database import Base


class MinglueDevice(Base):
    """明略硬件设备。"""

    __tablename__ = 'ml_device'

    device_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='设备主键')
    device_code = Column(String(100), nullable=False, unique=True, comment='设备唯一编码/SN')
    device_name = Column(String(100), nullable=True, default='', comment='设备名称')
    model = Column(String(100), nullable=True, default='', comment='设备型号')
    firmware_version = Column(String(100), nullable=True, default='', comment='固件版本')
    status = Column(String(20), nullable=False, default='offline', comment='在线状态')
    bind_status = Column(String(20), nullable=False, default='unbound', comment='绑定状态')
    owner_name = Column(String(100), nullable=True, default='', comment='使用人')
    owner_phone = Column(String(50), nullable=True, default='', comment='使用人手机号')
    department = Column(String(100), nullable=True, default='', comment='所属部门')
    battery_level = Column(Integer, nullable=True, comment='电量百分比')
    signal_strength = Column(Integer, nullable=True, comment='信号强度')
    record_status = Column(Integer, nullable=True, comment='录音状态')
    remain_storage = Column(BigInteger, nullable=True, comment='剩余存储空间MB')
    total_storage = Column(BigInteger, nullable=True, comment='总存储空间MB')
    battery_voltage = Column(Integer, nullable=True, comment='电池电压mV')
    battery_current = Column(Integer, nullable=True, comment='电池电流mA')
    charged_status = Column(String(30), nullable=True, comment='充电状态')
    key_status = Column(Integer, nullable=True, comment='开关键状态')
    usb_status = Column(Integer, nullable=True, comment='USB状态')
    disk_mount_status = Column(Integer, nullable=True, comment='磁盘挂载状态')
    chip = Column(String(100), nullable=True, comment='芯片类型')
    ip_address = Column(String(64), nullable=True, comment='设备IP')
    today_record_seconds = Column(Integer, nullable=True, comment='当日录音秒数')
    pending_recordings = Column(Integer, nullable=True, comment='待上传录音数')
    tenant_id = Column(BigInteger, nullable=True, comment='厂商租户ID')
    audio_id = Column(String(100), nullable=True, comment='API录音标识')
    last_seen_time = Column(DateTime, nullable=True, comment='最后在线时间')
    activated_at = Column(DateTime, nullable=True, comment='激活时间')
    config_json = Column(Text, nullable=True, comment='设备扩展配置JSON')
    create_by = Column(String(64), nullable=True, default='', comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, default='', comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(String(500), nullable=True, comment='备注')


class MinglueCallbackLog(Base):
    """设备回调原始报文与处理结果。"""

    __tablename__ = 'ml_callback_log'
    __table_args__ = (UniqueConstraint('event_id', name='uk_ml_callback_event_id'),)

    callback_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='回调主键')
    event_id = Column(String(128), nullable=True, comment='上游事件唯一标识')
    event_type = Column(String(100), nullable=False, default='unknown', comment='事件类型')
    device_code = Column(String(100), nullable=True, comment='设备编码')
    event_time = Column(DateTime, nullable=True, comment='设备事件时间')
    received_at = Column(DateTime, nullable=False, default=datetime.now, comment='接收时间')
    signature_valid = Column(String(1), nullable=False, default='Y', comment='签名是否有效')
    process_status = Column(String(20), nullable=False, default='success', comment='处理状态')
    payload_json = Column(Text, nullable=False, comment='原始JSON报文')
    error_message = Column(String(1000), nullable=True, comment='错误信息')
    request_ip = Column(String(128), nullable=True, comment='请求IP')
