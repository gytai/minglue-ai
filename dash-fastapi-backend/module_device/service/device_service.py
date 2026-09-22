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


class CallbackService:
    DEVICE_CODE_KEYS = ('device_code', 'deviceCode', 'device_id', 'deviceId', 'deviceSn', 'sn', 'imei')
    EVENT_ID_KEYS = ('event_id', 'eventId', 'message_id', 'messageId', 'requestId', 'request_id')
    EVENT_TYPE_KEYS = ('event_type', 'eventType', 'type', 'action', 'topic')
    EVENT_TIME_KEYS = ('event_time', 'eventTime', 'timestamp', 'time', 'occurTime')

    @staticmethod
    def _first(payload: Dict[str, Any], keys: tuple[str, ...]) -> Any:
        containers = [payload]
        for name in ('data', 'body', 'payload', 'device'):
            value = payload.get(name)
            if isinstance(value, dict):
                containers.append(value)
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
        event_id_value = cls._first(payload, cls.EVENT_ID_KEYS)
        event_id = str(event_id_value) if event_id_value not in (None, '') else None
        if event_id:
            existing = await CallbackDao.get_by_event_id(db, event_id)
            if existing:
                return {'callback_id': existing.callback_id, 'duplicate': True}

        device_code_value = cls._first(payload, cls.DEVICE_CODE_KEYS)
        device_code = str(device_code_value) if device_code_value not in (None, '') else None
        event_type_value = path_event_type or cls._first(payload, cls.EVENT_TYPE_KEYS) or 'unknown'
        event_type = str(event_type_value)[:100]
        event_time = cls._parse_datetime(cls._first(payload, cls.EVENT_TIME_KEYS))

        try:
            if device_code:
                await cls._upsert_device(db, device_code, event_type, payload, event_time)
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
        status = 'offline' if any(word in event_name for word in ('offline', 'disconnect', 'logout')) else 'online'
        now = event_time or datetime.now()
        battery = cls._first(payload, ('battery', 'batteryLevel', 'battery_level', 'power'))
        signal = cls._first(payload, ('signal', 'signalStrength', 'signal_strength', 'rssi'))
        firmware = cls._first(payload, ('firmware', 'firmwareVersion', 'firmware_version', 'version'))
        if device:
            values = {
                'device_id': device.device_id,
                'status': status,
                'last_seen_time': now,
                'update_time': datetime.now(),
            }
            if battery is not None:
                try:
                    values['battery_level'] = max(0, min(100, int(float(battery))))
                except (TypeError, ValueError):
                    pass
            if signal is not None:
                try:
                    values['signal_strength'] = int(float(signal))
                except (TypeError, ValueError):
                    pass
            if firmware is not None:
                values['firmware_version'] = str(firmware)[:100]
            await DeviceDao.update(db, values)
        else:
            await DeviceDao.add(
                db,
                DeviceModel(
                    device_code=device_code,
                    device_name=device_code,
                    status=status,
                    last_seen_time=now,
                    activated_at=now,
                    create_by='callback',
                    update_by='callback',
                ),
            )

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
