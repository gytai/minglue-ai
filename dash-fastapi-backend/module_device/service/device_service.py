import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import ServiceException
from module_admin.entity.vo.common_vo import CrudResponseModel
from module_device.dao.device_dao import CallbackDao, DeviceDao
from module_device.entity.vo.device_vo import (
    CallbackPageQueryModel,
    DeleteDeviceModel,
    DeviceModel,
    DevicePageQueryModel,
)
from module_device.service.minglue_api_service import MinglueApiService


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
        result = await MinglueApiService.get_device_statuses(sns)
        entities = result.get('entities', []) if isinstance(result, dict) else []
        if not isinstance(entities, list):
            raise ServiceException(message='批量获取设备状态失败：响应 entities 格式不正确')
        try:
            for entity in entities:
                if isinstance(entity, dict) and entity.get('sn'):
                    event_time = CallbackService._parse_datetime(entity.get('update_time'))
                    await CallbackService._upsert_device(
                        db,
                        str(entity['sn']),
                        'heartbeat',
                        entity,
                        event_time,
                    )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return {'entities': entities, 'synced': len(entities)}


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
        if value in (None, ''):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            timestamp = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
        try:
            return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return None

    @classmethod
    async def receive(
        cls,
        db: AsyncSession,
        payload: Dict[str, Any],
        path_event_type: Optional[str],
        request_ip: Optional[str],
    ):
        event_type_value = path_event_type or cls._first(payload, cls.EVENT_TYPE_KEYS) or cls._infer_event_type(payload)
        event_type = str(event_type_value)[:100]
        event_id_value = cls._first(payload, cls.EVENT_ID_KEYS)
        event_id = str(event_id_value) if event_id_value not in (None, '') else None
        if event_id:
            existing = await CallbackDao.get_by_event_id(db, event_id)
            if existing:
                return {'callback_id': existing.callback_id, 'duplicate': True}

        device_code_values = cls._device_codes(payload)
        device_code = device_code_values[0] if device_code_values else None
        event_time = cls._parse_datetime(cls._first(payload, cls.EVENT_TIME_KEYS))

        try:
            for current_code in device_code_values:
                device_payload = cls._payload_for_device(payload, current_code)
                device_event_time = cls._parse_datetime(cls._first(device_payload, cls.EVENT_TIME_KEYS)) or event_time
                await cls._upsert_device(db, current_code, event_type, device_payload, device_event_time)
            callback = await CallbackDao.add(
                db,
                {
                    'event_id': event_id,
                    'event_type': event_type,
                    'device_code': device_code,
                    'event_time': event_time,
                    'received_at': datetime.now(),
                    'signature_valid': 'Y',
                    'process_status': 'success',
                    'payload_json': json.dumps(payload, ensure_ascii=False, default=str),
                    'request_ip': request_ip,
                },
            )
            await db.commit()
            return {'callback_id': callback.callback_id, 'duplicate': False}
        except Exception:
            await db.rollback()
            raise

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
        online_value = cls._first(payload, ('online',))
        device_status = cls._first(payload, ('device_status', 'deviceStatus'))
        if online_value is not None:
            status = 'online' if cls._as_int(online_value) in (1, 2) else 'offline'
        elif device_status is not None:
            status = 'online' if cls._as_int(device_status) == 1 else 'offline'
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
            'config_json': json.dumps(payload, ensure_ascii=False, default=str),
        }
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
