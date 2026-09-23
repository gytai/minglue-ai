from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from config.database import Base


class MinglueDevice(Base):
    """明略硬件设备。

    设备台账只能由回调、心跳与厂商批量状态接口构建：厂商 AIOT 侧没有设备列表查询接口
    （见 docs/minglue-contract-and-gap-analysis.md §0.2）。
    """

    __tablename__ = 'ml_device'

    device_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='设备主键')
    device_code = Column(String(100), nullable=False, unique=True, comment='设备唯一编码/SN')
    device_name = Column(String(100), nullable=True, default='', comment='设备名称')
    model = Column(String(100), nullable=True, default='', comment='设备型号')
    firmware_version = Column(String(100), nullable=True, default='', comment='固件版本')
    status = Column(String(20), nullable=False, default='offline', comment='本地在线状态（含 disabled 停用）')
    online_status = Column(Integer, nullable=True, comment='心跳 online 三态：2在线 1开机未在线 0离线')
    device_status = Column(Integer, nullable=True, comment='心跳 device_status：1正常在线 0离线')
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
    audio_id = Column(String(100), nullable=True, comment='API录音标识（心跳 nm）')
    last_seen_time = Column(DateTime, nullable=True, comment='最后在线时间')
    heartbeat_time = Column(DateTime, nullable=True, comment='设备上报的 last_update_time 原值')
    config_json = Column(Text, nullable=True, comment='厂商最新配置快照（config/status/batch 原样）')
    config_last_upload_time = Column(DateTime, nullable=True, comment='厂商配置最后上传时间')
    config_synced_at = Column(DateTime, nullable=True, comment='本地配置同步时间')
    activated_at = Column(DateTime, nullable=True, comment='激活时间')
    create_by = Column(String(64), nullable=True, default='', comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, default='', comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(String(500), nullable=True, comment='备注')

    __table_args__ = (
        Index('idx_ml_device_online_status', 'online_status'),
        Index('idx_ml_device_last_seen', 'last_seen_time'),
    )


class MinglueCallbackLog(Base):
    """设备回调原始报文与处理结果。

    幂等策略见 ``module_device.service.device_service.CallbackService``：

    - ``event_id`` 保存"业务唯一键"；无法归一出业务键的心跳等回调置空字符串。
    - ``dedup_key`` 保存 SHA-256 摘要，**永不为空**，由它承担唯一索引，
      避免可空列 NULL 不去重导致的重复落库。
    """

    __tablename__ = 'ml_callback_log'

    callback_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='回调主键')
    event_id = Column(String(255), nullable=True, comment='上游业务唯一键（无业务键时为 NULL）')
    dedup_key = Column(String(128), nullable=False, comment='幂等键 SHA-256，非空且唯一')
    event_type = Column(String(100), nullable=False, default='unknown', comment='事件类型')
    log_type = Column(String(100), nullable=True, comment='upload 类回调的 logType：log/rec')
    device_code = Column(String(100), nullable=True, comment='设备编码')
    session_id = Column(BigInteger, nullable=True, comment='回调配置ID（session_id，webhook id）')
    topic_name = Column(String(64), nullable=True, comment='回调主题（topic_name）')
    item_count = Column(Integer, nullable=True, default=1, comment='单次回调携带的设备/文件条目数')
    event_time = Column(DateTime, nullable=True, comment='设备事件时间')
    received_at = Column(DateTime, nullable=False, default=datetime.now, comment='接收时间')
    signature_valid = Column(String(1), nullable=False, default='Y', comment='签名是否有效')
    process_status = Column(String(20), nullable=False, default='success', comment='处理状态')
    duplicate_flag = Column(String(1), nullable=False, default='N', comment='是否重复回调（Y是 N否）')
    processed_at = Column(DateTime, nullable=True, comment='处理结束时间')
    payload_json = Column(Text, nullable=False, comment='原始JSON报文')
    error_message = Column(String(1000), nullable=True, comment='错误信息')
    request_ip = Column(String(128), nullable=True, comment='请求IP')

    __table_args__ = (
        UniqueConstraint('dedup_key', name='uk_ml_callback_dedup_key'),
        Index('idx_ml_callback_event_id', 'event_id'),
        Index('idx_ml_callback_device_type', 'device_code', 'event_type'),
        Index('idx_ml_callback_received', 'received_at'),
        Index('idx_ml_callback_status_received', 'process_status', 'received_at'),
    )


class MinglueRecordingFile(Base):
    """录音文件与转码产物。

    录音分片来自 `rec` 回调的 ``content[]``；转码结果来自 `fc` 回调的 ``files[]``，
    两者通过厂商 ``object_key`` / ``file_path`` 关联到同一条录音记录。
    """

    __tablename__ = 'ml_recording_file'

    recording_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='录音记录主键')
    object_key = Column(String(500), nullable=False, comment='厂商对象存储路径，录音幂等键')
    device_code = Column(String(100), nullable=True, comment='设备编码')
    session_id = Column(BigInteger, nullable=True, comment='录音会话ID（rec.content[].csn 所在回调）')
    callback_session_id = Column(BigInteger, nullable=True, comment='回调配置ID（session_id）')
    record_no = Column(String(255), nullable=True, comment='录音文件名')
    audio_id = Column(String(100), nullable=True, comment='API录音标识（自定义字段）')
    duration = Column(BigInteger, nullable=True, comment='音频时长')
    size = Column(BigInteger, nullable=True, comment='文件大小（字节）')
    download_url = Column(String(1000), nullable=True, comment='厂商下载地址（带签名，有有效期）')
    merge_success_time = Column(DateTime, nullable=True, comment='录音合并成功时间')
    is_eof = Column(String(1), nullable=True, default='N', comment='是否为结束分片（文件名 _eof）')
    record_status = Column(String(20), nullable=False, default='uploaded', comment='本地处理状态')
    transcode_task_id = Column(String(128), nullable=True, comment='厂商转码任务ID（fc.task_id）')
    transcode_status = Column(String(50), nullable=True, comment='转码状态（原样保存，取值域待厂商确认）')
    group_key = Column(String(128), nullable=True, comment='转码分组键（fc.group_key）')
    transcode_file_path = Column(String(500), nullable=True, comment='转码产物对象路径（fc.files[].file_path）')
    transcode_download_url = Column(String(1000), nullable=True, comment='转码产物下载地址')
    transcode_finished_at = Column(DateTime, nullable=True, comment='转码完成时间')
    asr_task_id = Column(String(128), nullable=True, comment='ASR 任务ID（asrTskId）')
    asr_status = Column(String(50), nullable=True, comment='ASR 状态（原样保存，取值域待厂商确认）')
    asr_text_object_key = Column(String(500), nullable=True, comment='ASR 文本结果对象路径')
    asr_text_json = Column(Text, nullable=True, comment='ASR 文本结果原文')
    event_time = Column(DateTime, nullable=True, comment='事件时间')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')

    __table_args__ = (
        UniqueConstraint('object_key', name='uk_ml_recording_object_key'),
        Index('idx_ml_recording_device', 'device_code'),
        Index('idx_ml_recording_task', 'transcode_task_id'),
        Index('idx_ml_recording_asr', 'asr_task_id'),
    )


class MinglueDeviceControlLog(Base):
    """设备控制指令与厂商指令回查日志。

    厂商 `start/shadow`、`stop/shadow` 均返回必填 ``msg_id``；本地留存该 ID
    才能通过 `cmd/receive/log` 回查指令执行结果。控制日志**不保存 token 与凭证**。
    """

    __tablename__ = 'ml_device_control_log'

    control_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='控制日志主键')
    device_code = Column(String(100), nullable=False, comment='设备编码')
    command = Column(String(50), nullable=False, comment='指令类型：start_recording/stop_recording')
    audio_id = Column(String(100), nullable=True, comment='开启录音携带的音频ID（nm）')
    msg_id = Column(String(128), nullable=True, comment='厂商返回的消息ID，用于回查')
    request_status = Column(String(20), nullable=False, default='success', comment='本地请求结果')
    remote_status = Column(Integer, nullable=True, comment='厂商指令状态：0初始化 1成功 2返回出错 3返回超时')
    payload_json = Column(Text, nullable=True, comment='厂商请求载荷（已脱敏）')
    response_json = Column(Text, nullable=True, comment='厂商响应载荷')
    error_message = Column(String(1000), nullable=True, comment='错误信息')
    operator = Column(String(64), nullable=True, default='', comment='操作人')
    request_ip = Column(String(128), nullable=True, comment='请求IP')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')

    __table_args__ = (
        UniqueConstraint('msg_id', name='uk_ml_control_msg_id'),
        Index('idx_ml_control_device', 'device_code'),
        Index('idx_ml_control_created', 'create_time'),
    )
