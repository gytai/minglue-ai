import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import ServiceException
from module_admin.entity.vo.common_vo import CrudResponseModel
from module_device.dao.device_dao import CallbackDao, ControlLogDao, DeviceDao, RecordingDao
from module_device.entity.vo.device_vo import (
    CallbackPageQueryModel,
    ControlLogPageQueryModel,
    DeleteDeviceModel,
    DeviceModel,
    DevicePageQueryModel,
    DeviceStatusUpdateModel,
    RecordingPageQueryModel,
)
from module_device.service.minglue_api_service import MinglueApiService

# 心跳：设备约每 3 分钟上报一次；厂商文档明确"超过 5 分钟没有上报"即视为离线。
HEARTBEAT_OFFLINE_SECONDS = 300


class DeviceService:
    @classmethod
    async def get_list(cls, db: AsyncSession, query: DevicePageQueryModel):
        return await DeviceDao.get_list(db, query, is_page=True)

    @classmethod
    async def get_detail(cls, db: AsyncSession, device_id: int):
        device = await DeviceDao.get_by_id(db, device_id)
        if not device:
            raise ServiceException(message='设备不存在')
        return DeviceModel.model_validate(device)

    @classmethod
    async def add(cls, db: AsyncSession, device: DeviceModel):
        if await DeviceDao.get_by_code(db, device.device_code):
            raise ServiceException(message=f'设备编码 {device.device_code} 已存在')
        try:
            await DeviceDao.add(db, device)
            await db.commit()
            return CrudResponseModel(is_success=True, message='新增成功')
        except Exception:
            await db.rollback()
            raise

    @classmethod
    async def edit(cls, db: AsyncSession, device: DeviceModel):
        if not device.device_id or not await DeviceDao.get_by_id(db, device.device_id):
            raise ServiceException(message='设备不存在')
        same_code = await DeviceDao.get_by_code(db, device.device_code)
        if same_code and same_code.device_id != device.device_id:
            raise ServiceException(message=f'设备编码 {device.device_code} 已存在')
        try:
            await DeviceDao.update(db, device.model_dump(exclude_unset=True))
            await db.commit()
            return CrudResponseModel(is_success=True, message='更新成功')
        except Exception:
            await db.rollback()
            raise

    @classmethod
    async def delete(cls, db: AsyncSession, command: DeleteDeviceModel):
        try:
            ids = [int(item) for item in command.device_ids.split(',') if item]
        except ValueError as exc:
            raise ServiceException(message='设备ID格式不正确') from exc
        if not ids:
            raise ServiceException(message='请选择要删除的设备')
        try:
            await DeviceDao.delete(db, ids)
            await db.commit()
            return CrudResponseModel(is_success=True, message='删除成功')
        except Exception:
            await db.rollback()
            raise

    @classmethod
    async def sync_remote_statuses(cls, db: AsyncSession, sns: list[str]):
        """拉取厂商批量设备状态并写回本地台账。

        一致性规则（契约 §4.6）：

        1. 厂商状态接口是远端**读**，本地只做台账写：逐设备独立 savepoint + 提交，
           单台失败只回滚该台，不牵连同批其他设备。
        2. 厂商已知而本地不存在的设备按回调同一策略建档——厂商 AIOT 侧没有设备列表
           接口，台账只能由回调、心跳与批量状态构建（契约 §0.2）。
        3. 响应里逐台给出 ``results``，并对"请求了但厂商没回"的序列号补一条确定结果，
           因此不存在"批量失败后状态不可解释"的情况。
        """
        entities = await MinglueApiService.get_device_statuses(sns)
        return await cls._apply_remote_batch(
            db,
            sns,
            entities,
            operation='批量获取设备状态',
            apply_entity=cls._apply_remote_status,
        )

    @classmethod
    async def sync_remote_configs(cls, db: AsyncSession, sns: list[str]):
        """拉取厂商最新配置状态并落库。

        ``config_data`` 的结构在厂商文档中为未定义的空对象（契约 §4.2 / U3），
        因此按原样 JSON 保存，不做字段级校验，待厂商给出定义后再结构化。

        与状态同步的区别：配置快照**不为未知设备建档**，本地台账还没有该设备时
        记为 ``skipped``，避免只凭配置回包凭空创造一条没有主数据的设备记录。
        """
        entities = await MinglueApiService.get_device_config_statuses(sns)
        return await cls._apply_remote_batch(
            db,
            sns,
            entities,
            operation='批量获取设备配置状态',
            apply_entity=cls._apply_remote_config,
        )

    @classmethod
    async def _apply_remote_batch(
        cls,
        db: AsyncSession,
        sns: list[str],
        entities: list,
        *,
        operation: str,
        apply_entity,
    ) -> Dict[str, Any]:
        """逐设备应用远端结果，返回可解释的逐台结果与汇总。

        - ``skipped``：厂商回了但本地按规则不处理（缺 sn / 台账无此设备）。
        - ``failed``：处理抛错，或请求的 sn 未出现在厂商响应里。
        - ``partial``：同一批里既有成功又有失败，前端据此提示"部分失败"。
        """
        normalized = MinglueApiService._require_sns(sns)
        results: list[Dict[str, Any]] = []
        succeeded = skipped = failed = 0
        for entity in entities:
            device_code = str(entity.get('sn')).strip() if isinstance(entity, dict) and entity.get('sn') else ''
            if not device_code:
                skipped += 1
                results.append({'sn': None, 'ok': False, 'skipped': True, 'error': '厂商响应实体缺少 sn'})
                continue
            try:
                # 每台设备一个 savepoint：单台失败只回滚该台，其余设备照常提交。
                async with db.begin_nested():
                    outcome = await apply_entity(db, entity, device_code)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                failed += 1
                results.append({'sn': device_code, 'ok': False, 'skipped': False, 'error': str(exc)[:500]})
                continue
            if outcome == 'skipped':
                skipped += 1
                results.append({'sn': device_code, 'ok': False, 'skipped': True, 'error': '本地台账中不存在该设备'})
            else:
                succeeded += 1
                results.append({'sn': device_code, 'ok': True, 'skipped': False})
        returned = {item['sn'] for item in results if item.get('sn')}
        for sn in normalized:
            if sn not in returned:
                failed += 1
                results.append({'sn': sn, 'ok': False, 'skipped': False, 'error': f'{operation}响应未包含该设备'})
        return {
            'total': len(normalized),
            'succeeded': succeeded,
            'failed': failed,
            'skipped': skipped,
            # 保持既有字段名：前端按"已同步 N 台"展示。
            'synced': succeeded,
            'partial': succeeded > 0 and failed > 0,
            'entities': entities,
            'results': results,
        }

    @classmethod
    async def _apply_remote_status(cls, db: AsyncSession, entity: Dict[str, Any], device_code: str) -> str:
        """把一条批量状态回包按心跳口径写入本地台账（未知设备按回调策略建档）。"""
        if not isinstance(entity, dict):
            return 'skipped'
        event_time = CallbackService._parse_datetime(entity.get('update_time'))
        await CallbackService._upsert_device(db, device_code, 'heartbeat', entity, event_time)
        return 'applied'

    @classmethod
    async def _apply_remote_config(cls, db: AsyncSession, entity: Dict[str, Any], device_code: str) -> str:
        """把一条配置状态回包写入本地台账；本地无此设备时跳过（不凭空建档）。"""
        if not isinstance(entity, dict):
            return 'skipped'
        device = await DeviceDao.get_by_code(db, device_code)
        if not device:
            return 'skipped'
        await DeviceDao.update(
            db,
            {
                'device_id': device.device_id,
                'config_json': json.dumps(entity.get('config_data'), ensure_ascii=False, default=str),
                'config_last_upload_time': CallbackService._parse_datetime(entity.get('last_upload_time')),
                'config_synced_at': datetime.now(),
                'update_time': datetime.now(),
            },
        )
        return 'applied'

    @classmethod
    async def update_status(
        cls, db: AsyncSession, command: DeviceStatusUpdateModel, operator: str = ''
    ) -> CrudResponseModel:
        """维护本地状态字段（在线/绑定），供人工纠正厂商回包之外的状态。"""
        device = await DeviceDao.get_by_id(db, command.device_id)
        if not device:
            raise ServiceException(message='设备不存在')
        # 显式传 null 不应当把非空状态列清空，因此丢弃 None 值。
        values = {
            key: value
            for key, value in command.model_dump(exclude_unset=True, exclude={'device_id'}).items()
            if value is not None
        }
        if not values:
            raise ServiceException(message='请至少指定一个要维护的状态字段')
        values.update(device_id=device.device_id, update_by=operator, update_time=datetime.now())
        try:
            await DeviceDao.update(db, values)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return CrudResponseModel(is_success=True, message='设备状态已更新')

    @classmethod
    async def mark_stale_devices_offline(
        cls,
        db: AsyncSession,
        timeout_seconds: int = HEARTBEAT_OFFLINE_SECONDS,
    ) -> int:
        """心跳超时兜底：超过阈值未上报的设备置为离线。

        只能改写 `last_seen_time` 早于阈值的设备，**不触碰 `disabled` 停用设备**。
        """
        threshold = datetime.now() - timedelta(seconds=max(1, timeout_seconds))
        stale = await DeviceDao.get_stale_online_devices(db, threshold)
        if not stale:
            return 0
        for device in stale:
            await DeviceDao.update(
                db,
                {
                    'device_id': device.device_id,
                    'status': 'offline',
                    'device_status': 0,
                    'online_status': 0,
                    'update_time': datetime.now(),
                },
            )
        await db.commit()
        return len(stale)


_VENDOR_TZ_CACHE: Dict[str, Any] = {}


def _vendor_timezone():
    """厂商墙钟字符串（`Y-m-d H:i:s`）所在时区；未配置时按原样保存（契约 B18）。

    厂商文档未说明 `update_time` 的时区（§5.9 U12）。为让两条时间路径口径一致，
    这里把口径收敛为"落库一律 UTC naive"：

    - Unix 秒：本身即 UTC epoch，直接换算成 UTC naive（无歧义）。
    - 带偏移的 ISO 串（`+08:00` / `Z`）：按偏移换算成 UTC naive（修正原先直接
      丢弃 tzinfo 导致的口径错误）。
    - 不带偏移的墙钟串：由 `MINGLUE_VENDOR_TIMEZONE` 显式声明归属；留空表示
      "按原样保存"，等价于声明它就是存储时区（UTC）。

    若真实设备确认是北京时间，把该变量设为 `Asia/Shanghai` 即可，无需改代码。
    """
    name = os.getenv('MINGLUE_VENDOR_TIMEZONE', '').strip()
    if not name:
        return None
    if name not in _VENDOR_TZ_CACHE:
        try:
            _VENDOR_TZ_CACHE[name] = ZoneInfo(name)
        except Exception:
            # 配置写错时降级为"不做换算"，避免每条回调都因配置问题失败。
            _VENDOR_TZ_CACHE[name] = None
    return _VENDOR_TZ_CACHE[name]


class CallbackService:
    DEVICE_CODE_KEYS = ('device_code', 'deviceCode', 'device_id', 'deviceId', 'deviceSn', 'sn', 'imei')
    EVENT_ID_KEYS = (
        'event_id',
        'eventId',
        'message_id',
        'messageId',
        'requestId',
        'request_id',
        'task_id',
        'asrTskId',
        'object_key',
        'objectKey',
        'asrTextObjectKey',
    )
    EVENT_TYPE_KEYS = ('event_type', 'eventType', 'type', 'logType', 'log_type', 'action', 'topic')
    EVENT_TIME_KEYS = (
        'event_time',
        'eventTime',
        'update_time',
        'updatedAt',
        'merge_success_time',
        'timestamp',
        'time',
        'occurTime',
    )
    #: 厂商回调的 8 类事件（契约 §2）。`upload` 由 logType 归一化而来。
    CALLBACK_EVENT_TYPES = ('rec', 'op', 'sys', 'reclist', 'upload', 'fc', 'asr', 'heartbeat')
    #: 单次回调可携带多条目，这些顶层键下的每个 dict 都要按设备分别落库。
    ITEM_KEYS = ('content', 'files', 'entities')

    @staticmethod
    def _first(payload: Dict[str, Any], keys: tuple[str, ...]) -> Any:
        containers = [payload]
        for name in ('data', 'body', 'payload', 'device'):
            value = payload.get(name)
            if isinstance(value, dict):
                containers.append(value)
        for name in ('content', 'files', 'entities'):
            value = payload.get(name)
            if isinstance(value, list):
                containers.extend(item for item in value if isinstance(item, dict))
        for container in containers:
            for key in keys:
                value = container.get(key)
                if value not in (None, ''):
                    return value
        return None

    @classmethod
    def _parse_datetime(cls, value: Any) -> Optional[datetime]:
        """把厂商时间字段统一成 **UTC naive**（契约 B18）。

        两条口径（`update_time` 字符串 / `merge_success_time`、`timestamp` Unix 秒）
        此前一条按原样墙钟、一条按 UTC 换算，混用会产生固定时差。现在统一为：

        - 数值：Unix 秒（毫秒自动识别）→ UTC naive；
        - 字符串带偏移：按偏移换算 → UTC naive；
        - 字符串不带偏移：按 `MINGLUE_VENDOR_TIMEZONE` 换算（默认留空 = 原样）。
        """
        if value in (None, ''):
            return None
        if isinstance(value, datetime):
            return cls._to_utc_naive(value)
        if isinstance(value, (int, float)):
            timestamp = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return cls._to_utc_naive(parsed)
        vendor_tz = _vendor_timezone()
        if vendor_tz is not None:
            parsed = parsed.replace(tzinfo=vendor_tz)
            return cls._to_utc_naive(parsed)
        return parsed

    @staticmethod
    def _to_utc_naive(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _normalize_event_type(cls, payload: Dict[str, Any], path_event_type: Optional[str]) -> str:
        """归一化事件类型。

        P0（契约 B6）：厂商 `upload` 回调**没有顶层 `type`**，只有 `logType`
        （取值 `log`/`rec`）。若直接采信 `logType`，`logType=rec` 会被误判为录音
        回调 `rec`，与真正的 `rec` 音频回调冲突。这里先按 `type` 判定，只在
        确实没有 `type` 时才用 camelCase 特征（`objectKey`/`fileName`/`updatedAt`）
        识别为 `upload`。
        """
        if path_event_type:
            return str(path_event_type)[:100]
        explicit = payload.get('type') or payload.get('event_type') or payload.get('eventType')
        if explicit not in (None, ''):
            return str(explicit)[:100]
        if any(key in payload for key in ('objectKey', 'fileName', 'updatedAt')):
            return 'upload'
        return cls._infer_event_type(payload)

    @classmethod
    def _items(cls, payload: Dict[str, Any]) -> list[Dict[str, Any]]:
        """展开单次回调中的设备/文件条目；无列表结构时退化为顶层单条目。"""
        items: list[Dict[str, Any]] = []
        for name in cls.ITEM_KEYS:
            value = payload.get(name)
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
        return items or [payload]

    @classmethod
    def _merged_item(cls, payload: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        """把顶层字段与列表条目合成一个视图（条目字段优先）。

        `fc` 的设备号/`task_id`/`status`/`timestamp` 在**顶层**，文件路径在
        `files[]`；`rec` 的 `session_id` 在顶层、`object_key` 在 `content[]`。
        只取其中一层会丢业务键与事件时间，因此统一在这里合并；列表本身保留在
        视图里，供 `_persist_transcode` 再取 `files[].file_path`。
        """
        return {**payload, **item}

    @classmethod
    def _item_device_code(cls, item: Dict[str, Any], fallback: Optional[str]) -> Optional[str]:
        value = cls._first(item, cls.DEVICE_CODE_KEYS)
        if value in (None, ''):
            return fallback
        return str(value)[:100]

    @classmethod
    def _build_event_id(cls, event_type: str, item: Dict[str, Any]) -> Optional[str]:
        """提取业务唯一键；无业务键的回调（心跳、upload）返回 None。"""
        if event_type == 'heartbeat':
            return None
        value = cls._first(item, cls.EVENT_ID_KEYS)
        if value in (None, ''):
            value = cls._first(item, ('task_id', 'asrTskId', 'objectKey', 'object_key'))
        if value in (None, ''):
            return None
        return str(value)[:255]

    @classmethod
    def _build_dedup_key(cls, event_type: str, event_id: Optional[str], item: Dict[str, Any]) -> str:
        """构造**永不为空**的幂等键。

        P0（契约 B11 / D5）：唯一索引若建在可空列上，NULL 之间不去重，心跳会无限
        重复落库。这里始终返回 64 位十六进制 SHA-256：

        - 有业务键：`sha256(event_type|business_key)`，同一条业务事件重复推送即命中。
        - 无业务键（心跳 / upload）：退化为整条报文的规范化摘要，报文完全相同才视为
          重复，避免把同一设备不同时刻的真实心跳误判为重复。
        """
        business_key = event_id
        if business_key in (None, ''):
            canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            business_key = f'payload:{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}'
        return hashlib.sha256(f'{event_type}|{business_key}'.encode('utf-8')).hexdigest()

    @classmethod
    async def receive(
        cls,
        db: AsyncSession,
        payload: Dict[str, Any],
        path_event_type: Optional[str],
        request_ip: Optional[str],
    ):
        event_type = cls._normalize_event_type(payload, path_event_type)
        log_type_value = payload.get('logType')
        log_type = str(log_type_value)[:100] if log_type_value not in (None, '') else None
        session_id = cls._as_int(payload.get('session_id'))
        topic_name = payload.get('topic_name')
        topic_name = str(topic_name)[:64] if topic_name not in (None, '') else None
        items = cls._items(payload)
        fallback_code = cls._first(
            {key: value for key, value in payload.items() if key not in cls.ITEM_KEYS},
            cls.DEVICE_CODE_KEYS,
        )
        fallback_code = str(fallback_code)[:100] if fallback_code not in (None, '') else None
        raw_payload = json.dumps(payload, ensure_ascii=False, default=str)
        received_at = datetime.now()

        base_values = {
            'event_type': event_type,
            'log_type': log_type,
            'session_id': session_id,
            'topic_name': topic_name,
            'item_count': len(items),
            'received_at': received_at,
            'signature_valid': 'Y',
            'payload_json': raw_payload,
            'request_ip': request_ip,
        }

        first_device_code = None
        for index, item in enumerate(items):
            # 顶层字段（fc 的 task_id/status/group_key/timestamp、rec 的 session_id 等）
            # 与列表条目（rec.content[] 的 object_key、fc.files[] 的 file_path）分居两层，
            # 这里先合成一个视图再取设备/业务键/时间，避免只看到其中一层。
            merged_item = cls._merged_item(payload, item)
            device_code = cls._item_device_code(merged_item, fallback_code)
            first_device_code = first_device_code or device_code
            event_id = cls._build_event_id(event_type, merged_item)
            dedup_key = cls._build_dedup_key(event_type, event_id, merged_item)
            try:
                existing = await CallbackDao.get_by_dedup_key(db, dedup_key)
                if existing:
                    # 提交前先把主键取出来：commit 之后 ORM 实例属性可能已过期，
                    # 再读 `existing.callback_id` 会触发惰性刷新（异步会话下即
                    # MissingGreenlet），使重复回调被误记为处理失败。
                    existing_callback_id = existing.callback_id
                    # 重复回调仍留痕：新增一条 duplicate_flag='Y' 的审计记录，
                    # 但不重复触发设备状态更新。
                    await CallbackDao.add(
                        db,
                        {
                            **base_values,
                            'event_id': event_id,
                            'dedup_key': f'{dedup_key[:110]}:dup:{received_at.timestamp():.6f}:{index}',
                            'device_code': device_code,
                            'event_time': cls._item_event_time(merged_item, event_type),
                            'process_status': 'duplicate',
                            'duplicate_flag': 'Y',
                            'processed_at': datetime.now(),
                        },
                    )
                    await db.commit()
                    if len(items) == 1:
                        return {'callback_id': existing_callback_id, 'duplicate': True}
                    continue
                callback = await CallbackDao.add(
                    db,
                    {
                        **base_values,
                        'event_id': event_id,
                        'dedup_key': dedup_key,
                        'device_code': device_code,
                        'event_time': cls._item_event_time(merged_item, event_type),
                        'process_status': 'success',
                        'duplicate_flag': 'N',
                    },
                )
                if device_code:
                    await cls._apply_item(db, event_type, device_code, merged_item, item_index=index)
                    await cls._persist_business_item(db, event_type, device_code, merged_item, session_id)
                callback.process_status = 'success'
                callback.processed_at = datetime.now()
                await db.commit()
            except Exception as exc:
                # P0（契约 B12）：处理失败不能让原始报文随事务回滚而消失。
                # 先回滚业务改动，再用独立事务补一条 failed 审计记录，
                # 保证厂商重试前本地已有可追溯痕迹。
                await db.rollback()
                await cls._record_failure(
                    db,
                    base_values=base_values,
                    event_id=event_id,
                    dedup_key=dedup_key,
                    device_code=device_code,
                    item=merged_item,
                    event_type=event_type,
                    received_at=received_at,
                    index=index,
                    error=exc,
                )
                if not cls._is_duplicate_conflict(exc):
                    raise
        return {
            'callback_id': None,
            'duplicate': False,
            'event_type': event_type,
            'device_code': first_device_code,
            'items': len(items),
        }

    @classmethod
    def _item_event_time(cls, item: Dict[str, Any], event_type: str) -> Optional[datetime]:
        if event_type == 'upload':
            explicit = item.get('updatedAt') or item.get('update_time')
            if explicit not in (None, ''):
                return cls._parse_datetime(explicit)
        return cls._parse_datetime(cls._first(item, cls.EVENT_TIME_KEYS))

    @classmethod
    async def _record_failure(
        cls,
        db: AsyncSession,
        *,
        base_values: Dict[str, Any],
        event_id: Optional[str],
        dedup_key: str,
        device_code: Optional[str],
        item: Dict[str, Any],
        event_type: str,
        received_at: datetime,
        index: int,
        error: Exception,
    ):
        try:
            await CallbackDao.add(
                db,
                {
                    **base_values,
                    'event_id': event_id,
                    # 失败记录同样要能落库：唯一索引冲突时换用带时间戳的键，
                    # 语义上仍能通过 event_id / payload_json 追溯同一条事件。
                    'dedup_key': f'{dedup_key[:110]}:fail:{received_at.timestamp():.6f}:{index}',
                    'device_code': device_code,
                    'event_time': cls._item_event_time(item, event_type),
                    'process_status': 'failed',
                    'duplicate_flag': 'N',
                    'processed_at': datetime.now(),
                    'error_message': str(error)[:1000],
                },
            )
            await db.commit()
        except Exception:
            await db.rollback()

    @staticmethod
    def _is_duplicate_conflict(exc: Exception) -> bool:
        """判断异常是否为幂等键唯一约束冲突（并发重推时的正常竞争）。

        必须**先按异常类型**判断，不能对异常文本做裸子串匹配：ORM 报错会回显
        整条 SELECT 的列清单，其中含 ``duplicate_flag`` 列名，裸匹配 'duplicate'
        会把任何报错（例如 MissingGreenlet）都误判成"幂等冲突"而静默吞掉，
        真实故障因此既不返回错误、也不留在可读的审计信息里。
        """
        if isinstance(exc, IntegrityError):
            return True
        text = str(exc).lower()
        return 'uniqueviolation' in text or 'duplicate entry' in text or 'duplicate key' in text

    @classmethod
    async def _apply_item(
        cls,
        db: AsyncSession,
        event_type: str,
        device_code: str,
        item: Dict[str, Any],
        item_index: int = 0,
    ):
        """按条目更新设备状态；每条目独立入参，`rec` 多设备不再只取首条。

        `item` 应是 `_merged_item` 合成后的视图（顶层 + 列表条目）。
        """
        event_time = cls._item_event_time(item, event_type)
        await cls._upsert_device(db, device_code, event_type, item, event_time)

    @classmethod
    async def _persist_business_item(
        cls,
        db: AsyncSession,
        event_type: str,
        device_code: str,
        item: Dict[str, Any],
        session_id: Optional[int],
    ):
        """把 rec / fc / asr 的业务字段落到 ml_recording_file。"""
        if event_type == 'rec':
            await cls._persist_recording(db, device_code, item, session_id)
        elif event_type == 'fc':
            await cls._persist_transcode(db, device_code, item)
        elif event_type == 'asr':
            await cls._persist_asr(db, device_code, item, session_id)

    @classmethod
    def _is_eof_object_key(cls, object_key: Optional[str]) -> str:
        """按契约 §2.1 的文件名规则判断结束分片。

        §2.1 的模板把标记写成 `..._[文件序列号]_eof.[后缀]`（`_eof` 在扩展名
        **之前**），而实现侧观测到的样例是 `..._10.opus_eof`（在扩展名**之后**）。
        两处证据指向的位置不同，这里两种都认：basename 以 `_eof` 结尾，或去掉
        扩展名后的主干以 `_eof` 结尾。歧义本身记在契约文档的不确定项里。
        """
        if not object_key:
            return 'N'
        name = str(object_key).rsplit('/', 1)[-1].lower()
        if name.endswith('_eof'):
            return 'Y'
        stem = name.rsplit('.', 1)[0] if '.' in name else name
        return 'Y' if stem.endswith('_eof') else 'N'

    @classmethod
    async def _persist_recording(
        cls,
        db: AsyncSession,
        device_code: str,
        item: Dict[str, Any],
        session_id: Optional[int],
    ):
        object_key = item.get('object_key')
        if object_key in (None, ''):
            return
        object_key = str(object_key)[:500]
        merge_time = cls._parse_datetime(item.get('merge_success_time'))
        record_no = object_key.rsplit('/', 1)[-1][:255]
        values = {
            'object_key': object_key,
            'device_code': device_code[:100],
            'callback_session_id': session_id,
            'record_no': record_no,
            'duration': cls._as_int(item.get('duration')),
            'size': cls._as_int(item.get('size')),
            'download_url': str(item.get('download_url'))[:1000] if item.get('download_url') else None,
            'merge_success_time': merge_time,
            'is_eof': cls._is_eof_object_key(object_key),
            'event_time': merge_time,
            'update_time': datetime.now(),
        }
        existing = await RecordingDao.get_by_object_key(db, object_key)
        if existing:
            values['recording_id'] = existing.recording_id
            # 分片续传时 device_code / 时长可能补齐，但不覆盖已解析出的转码与 ASR 结果。
            await RecordingDao.update(db, values)
        else:
            await RecordingDao.add(db, {**values, 'record_status': 'uploaded', 'create_time': datetime.now()})

    @classmethod
    async def _persist_transcode(cls, db: AsyncSession, device_code: str, item: Dict[str, Any]):
        files = item.get('files')
        file_path = None
        download_url = None
        if isinstance(files, list):
            for entry in files:
                if isinstance(entry, dict) and entry.get('file_path'):
                    file_path = str(entry['file_path'])[:500]
                    download_url = str(entry.get('download_url'))[:1000] if entry.get('download_url') else None
                    break
        task_id = item.get('task_id')
        task_id = str(task_id)[:128] if task_id not in (None, '') else None
        if not task_id and not file_path:
            return
        values = {
            'device_code': device_code[:100],
            'transcode_task_id': task_id,
            'transcode_status': str(item.get('status'))[:50] if item.get('status') not in (None, '') else None,
            'group_key': str(item.get('group_key'))[:128] if item.get('group_key') else None,
            'transcode_file_path': file_path,
            'transcode_download_url': download_url,
            'transcode_finished_at': cls._parse_datetime(item.get('timestamp')),
            'event_time': cls._parse_datetime(item.get('timestamp')),
            'update_time': datetime.now(),
        }
        existing = None
        if file_path:
            existing = await RecordingDao.get_by_transcode_file_path(db, file_path)
        if not existing and task_id:
            existing = await RecordingDao.get_by_transcode_task_id(db, task_id)
        if existing:
            values['recording_id'] = existing.recording_id
            await RecordingDao.update(db, values)
            return
        # 转码产物尚未有录音主记录时先建档，后续 rec 回调按 object_key 补齐时长等信息。
        object_key = file_path or f'fc/{task_id}'
        await RecordingDao.add(
            db,
            {
                **values,
                'object_key': object_key[:500],
                'record_status': 'transcoded',
                'create_time': datetime.now(),
            },
        )

    @classmethod
    async def _persist_asr(
        cls,
        db: AsyncSession,
        device_code: str,
        item: Dict[str, Any],
        session_id: Optional[int],
    ):
        task_id = item.get('asrTskId')
        task_id = str(task_id)[:128] if task_id not in (None, '') else None
        text_object_key = item.get('asrTextObjectKey')
        text_object_key = str(text_object_key)[:500] if text_object_key else None
        if not task_id and not text_object_key:
            return
        text_result = item.get('asrTextResultList')
        values = {
            'device_code': device_code[:100],
            'callback_session_id': session_id,
            'asr_task_id': task_id,
            'asr_status': str(item.get('status'))[:50] if item.get('status') not in (None, '') else None,
            'asr_text_object_key': text_object_key,
            'asr_text_json': json.dumps(text_result, ensure_ascii=False, default=str) if text_result else None,
            'record_status': 'asr_done',
            'event_time': cls._parse_datetime(item.get('timestamp')),
            'update_time': datetime.now(),
        }
        existing = None
        if text_object_key:
            existing = await RecordingDao.get_by_object_key(db, text_object_key)
        if not existing and task_id:
            existing = await RecordingDao.get_by_asr_task_id(db, task_id)
        if existing:
            values['recording_id'] = existing.recording_id
            await RecordingDao.update(db, values)
            return
        object_key = text_object_key or f'asr/{task_id}'
        await RecordingDao.add(db, {**values, 'object_key': object_key[:500], 'create_time': datetime.now()})

    @classmethod
    def _device_codes(cls, payload: Dict[str, Any]) -> list[str]:
        values = []
        direct_payload = {
            key: value for key, value in payload.items() if key not in ('content', 'files', 'entities')
        }
        direct = cls._first(direct_payload, cls.DEVICE_CODE_KEYS)
        if direct not in (None, ''):
            values.append(str(direct))
        for name in ('content', 'files', 'entities'):
            items = payload.get(name)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = cls._first(item, cls.DEVICE_CODE_KEYS)
                if value not in (None, ''):
                    values.append(str(value))
        return list(dict.fromkeys(values))

    @staticmethod
    def _infer_event_type(payload: Dict[str, Any]) -> str:
        if 'device_status' in payload and 'update_time' in payload:
            return 'heartbeat'
        if 'asrTextResultList' in payload or 'asrTextObjectKey' in payload:
            return 'asr'
        if 'files' in payload and 'group_key' in payload:
            return 'fc'
        return 'unknown'

    @classmethod
    def _payload_for_device(cls, payload: Dict[str, Any], device_code: str) -> Dict[str, Any]:
        merged = dict(payload)
        for name in ('content', 'files', 'entities'):
            items = payload.get(name)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = cls._first(item, cls.DEVICE_CODE_KEYS)
                if value not in (None, '') and str(value) == device_code:
                    merged.update(item)
                    return merged
        return merged

    @classmethod
    async def _upsert_device(
        cls,
        db: AsyncSession,
        device_code: str,
        event_type: str,
        payload: Dict[str, Any],
        event_time: Optional[datetime],
    ):
        device = await DeviceDao.get_by_code(db, device_code)
        event_name = event_type.lower()
        online_value = cls._as_int(cls._first(payload, ('online',)))
        device_status = cls._as_int(cls._first(payload, ('device_status', 'deviceStatus')))
        if online_value is not None:
            # 契约 D2：心跳 online 是三态（2 在线 / 1 开机但可能不在线 / 0 离线），
            # 压扁成 online/offline 会丢失"开机未在线"，因此原值单独存 online_status。
            status = 'online' if online_value == 2 else 'offline'
        elif device_status is not None:
            status = 'online' if device_status == 1 else 'offline'
        else:
            status = 'offline' if any(word in event_name for word in ('offline', 'disconnect', 'logout')) else 'online'
        now = event_time or datetime.now()
        battery = cls._first(payload, ('remain_power', 'battery', 'batteryLevel', 'battery_level', 'power', 'rsoc'))
        signal = cls._first(payload, ('signal', 'signalStrength', 'signal_strength', 'rssi'))
        firmware = cls._first(payload, ('firmware', 'firmwareVersion', 'firmware_version', 'version', 'v'))
        telemetry_fields = {
            'record_status': ('record_status', 'rec'),
            'remain_storage': ('remain_storage', 'df'),
            'total_storage': ('total_storage', 'total_df'),
            'battery_voltage': ('battery_voltage', 'vbat'),
            'battery_current': ('battery_current', 'ibat'),
            'key_status': ('key_status', 'key'),
            'usb_status': ('usb_status', 'usb'),
            'disk_mount_status': ('disk_mount_status', 'mnt'),
            'today_record_seconds': ('rectd',),
            'pending_recordings': ('recnu',),
            'tenant_id': ('tenant_id',),
        }
        values = {
            'status': status,
            'last_seen_time': now,
            'update_time': datetime.now(),
        }
        if online_value is not None:
            values['online_status'] = online_value
        if device_status is not None:
            values['device_status'] = device_status
        if event_type.lower() == 'heartbeat':
            values['heartbeat_time'] = now
        if battery is not None:
            parsed = cls._as_int(battery)
            if parsed is not None:
                values['battery_level'] = max(0, min(100, parsed))
        if signal is not None:
            parsed = cls._as_int(signal)
            if parsed is not None:
                values['signal_strength'] = parsed
        if firmware is not None:
            values['firmware_version'] = str(firmware)[:100]
        for field, keys in telemetry_fields.items():
            parsed = cls._as_int(cls._first(payload, keys))
            if parsed is not None:
                values[field] = parsed
        text_fields = {
            'charged_status': ('charged_status', 'chg'),
            'chip': ('chip',),
            'ip_address': ('cip', 'ip'),
            'audio_id': ('nm',),
        }
        for field, keys in text_fields.items():
            value = cls._first(payload, keys)
            if value not in (None, ''):
                values[field] = str(value)[:100]
        if device:
            values['device_id'] = device.device_id
            if device.status == 'disabled':
                values['status'] = 'disabled'
            await DeviceDao.update(db, values)
        else:
            values.update(
                device_code=device_code,
                device_name=device_code,
                activated_at=now,
                create_by='callback',
                update_by='callback',
            )
            await DeviceDao.add(
                db,
                DeviceModel(**values),
            )

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        if value in (None, ''):
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    async def record_rejected(
        cls,
        db: AsyncSession,
        payload: Dict[str, Any],
        path_event_type: Optional[str],
        request_ip: Optional[str],
    ):
        """签名校验失败时留痕，且不触发任何设备状态更新（契约 B14）。"""
        event_type = cls._normalize_event_type(payload, path_event_type)
        received_at = datetime.now()
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()
        try:
            await CallbackDao.add(
                db,
                {
                    'event_type': event_type,
                    'log_type': str(payload.get('logType'))[:100] if payload.get('logType') else None,
                    'dedup_key': hashlib.sha256(
                        f'rejected|{digest}|{received_at.timestamp():.6f}'.encode('utf-8')
                    ).hexdigest(),
                    'session_id': cls._as_int(payload.get('session_id')),
                    'topic_name': str(payload.get('topic_name'))[:64] if payload.get('topic_name') else None,
                    'item_count': len(cls._items(payload)),
                    'received_at': received_at,
                    'signature_valid': 'N',
                    'process_status': 'rejected',
                    'duplicate_flag': 'N',
                    'processed_at': datetime.now(),
                    'payload_json': json.dumps(payload, ensure_ascii=False, default=str),
                    'error_message': '回调签名校验失败',
                    'request_ip': request_ip,
                },
            )
            await db.commit()
        except Exception:
            await db.rollback()

    @classmethod
    async def record_timeout(
        cls,
        db: AsyncSession,
        payload: Dict[str, Any],
        path_event_type: Optional[str],
        request_ip: Optional[str],
        elapsed: float,
    ):
        """B3：同步落库超时后的降级留痕。

        厂商明确"回调地址响应超过 3s 即视为失败"，所以调用方必须在预算内把响应
        交出去。未被处理完的报文以 `process_status='timeout'` 独立落库，
        `payload_json` 完整保留，可按该记录重放；`error_message` 写明耗时，
        使降级原因可解释。
        """
        event_type = cls._normalize_event_type(payload, path_event_type)
        received_at = datetime.now()
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()
        try:
            await CallbackDao.add(
                db,
                {
                    'event_type': event_type,
                    'log_type': str(payload.get('logType'))[:100] if payload.get('logType') else None,
                    'dedup_key': hashlib.sha256(
                        f'timeout|{digest}|{received_at.timestamp():.6f}'.encode('utf-8')
                    ).hexdigest(),
                    'session_id': cls._as_int(payload.get('session_id')),
                    'topic_name': str(payload.get('topic_name'))[:64] if payload.get('topic_name') else None,
                    'item_count': len(cls._items(payload)),
                    'received_at': received_at,
                    'signature_valid': 'Y',
                    'process_status': 'timeout',
                    'duplicate_flag': 'N',
                    'processed_at': datetime.now(),
                    'payload_json': json.dumps(payload, ensure_ascii=False, default=str),
                    'error_message': f'同步落库超过回调预算（{elapsed:.3f}s），原始报文已留存，可据此重放',
                    'request_ip': request_ip,
                },
            )
            await db.commit()
        except Exception:
            await db.rollback()

    @classmethod
    async def get_list(cls, db: AsyncSession, query: CallbackPageQueryModel):
        return await CallbackDao.get_list(db, query)

    @classmethod
    async def get_detail(cls, db: AsyncSession, callback_id: int):
        record = await CallbackDao.get_by_id(db, callback_id)
        if not record:
            raise ServiceException(message='回调记录不存在')
        return record

    @classmethod
    async def delete(cls, db: AsyncSession, callback_ids: str):
        try:
            ids = [int(item) for item in callback_ids.split(',') if item]
        except ValueError as exc:
            raise ServiceException(message='回调记录ID格式不正确') from exc
        await CallbackDao.delete(db, ids)
        await db.commit()
        return CrudResponseModel(is_success=True, message='删除成功')


class RecordingService:
    """录音文件与转码/ASR 产物查询。"""

    @classmethod
    async def get_list(cls, db: AsyncSession, query: RecordingPageQueryModel):
        return await RecordingDao.get_list(db, query)


class ControlLogService:
    """设备控制指令日志：写入与查询。

    控制日志只保存厂商 ``msg_id``、状态与原样载荷，**不保存 token 或凭证**。
    """

    @classmethod
    async def record(
        cls,
        db: AsyncSession,
        *,
        device_code: str,
        command: str,
        audio_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        operator: Optional[str] = None,
        request_ip: Optional[str] = None,
    ):
        """记录一次控制请求；写失败不影响主流程（控制结果已返回给调用方）。"""
        payload = {
            'device_code': device_code[:100],
            'command': command[:50],
            'audio_id': audio_id,
            'request_status': 'failed' if error else 'success',
            'operator': (operator or '')[:64],
            'request_ip': request_ip,
            'payload_json': json.dumps({'sn': device_code, 'nm': audio_id}, ensure_ascii=False),
            'response_json': json.dumps(result, ensure_ascii=False, default=str) if result else None,
            'error_message': str(error)[:1000] if error else None,
            'remote_status': CallbackService._as_int(result.get('stat')) if isinstance(result, dict) else None,
            'create_time': datetime.now(),
            'update_time': datetime.now(),
        }
        if isinstance(result, dict) and result.get('msg_id'):
            payload['msg_id'] = str(result['msg_id'])[:128]
        try:
            record = await ControlLogDao.add(db, payload)
            await db.commit()
            return record
        except Exception:
            await db.rollback()
            return None

    @classmethod
    async def get_list(cls, db: AsyncSession, query: ControlLogPageQueryModel):
        return await ControlLogDao.get_list(db, query)

    #: 对外返回的控制日志字段；`payload_json`/`response_json` 只含 sn/nm/msg_id/stat，
    #: 不含 token 或凭证（契约 C11）。
    RESPONSE_FIELDS = (
        'control_id',
        'device_code',
        'command',
        'audio_id',
        'msg_id',
        'request_status',
        'remote_status',
        'error_message',
        'operator',
        'request_ip',
        'payload_json',
        'response_json',
        'create_time',
        'update_time',
    )

    @classmethod
    async def find_by_msg_id(cls, db: AsyncSession, msg_id: str) -> Optional[Dict[str, Any]]:
        """按厂商 ``msg_id`` 查本地控制日志；未找到时返回 ``None`` 而不是抛错。

        指令回查允许"厂商还没有接收记录、但本地已经发过指令"的中间态，
        因此这里保持可选语义，并直接返回可序列化的字典，避免 ORM 实例
        带着 ``_sa_instance_state`` 进入响应体。
        """
        record = await ControlLogDao.get_by_msg_id(db, msg_id)
        if not record:
            return None
        return {field: getattr(record, field) for field in cls.RESPONSE_FIELDS}

    @classmethod
    async def resolve_command_result(cls, db: AsyncSession, device_code: str, message_id: str) -> Dict[str, Any]:
        """指令结果查询：合并厂商指令接收日志与本地控制日志（契约 §4.5）。

        刚下发指令时厂商侧常返回 ``记录不存在``（厂商状态 0 初始化）；
        此时本地已经记录下 ``msg_id``，把它作为 ``local`` 返回并保留
        ``remote_error``，比整体报错更有用。两边都没有记录才判定为不存在。
        """
        remote = None
        remote_error = None
        try:
            remote = await MinglueApiService.get_command_log(device_code, message_id)
        except ServiceException as exc:
            remote_error = exc.message
        local = await cls.find_by_msg_id(db, message_id)
        if remote is None and local is None:
            raise ServiceException(message=remote_error or '指令接收日志不存在')
        return {'remote': remote, 'remote_error': remote_error, 'local': local}
