from datetime import datetime, time

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from module_device.entity.do.device_do import (
    MinglueCallbackLog,
    MinglueDevice,
    MinglueDeviceControlLog,
    MinglueRecordingFile,
)
from module_device.entity.vo.device_vo import (
    CallbackPageQueryModel,
    ControlLogPageQueryModel,
    DeviceModel,
    DevicePageQueryModel,
    RecordingPageQueryModel,
)
from utils.page_util import PageUtil


def _date_range(column, begin_time: str = None, end_time: str = None):
    """把 ``YYYY-MM-DD`` 区间转成闭区间过滤条件，缺省时返回恒真。"""
    if not (begin_time and end_time):
        return True
    return column.between(
        datetime.combine(datetime.strptime(begin_time, '%Y-%m-%d'), time.min),
        datetime.combine(datetime.strptime(end_time, '%Y-%m-%d'), time.max),
    )


class DeviceDao:
    @classmethod
    async def get_by_id(cls, db: AsyncSession, device_id: int):
        return (await db.execute(select(MinglueDevice).where(MinglueDevice.device_id == device_id))).scalars().first()

    @classmethod
    async def get_by_code(cls, db: AsyncSession, device_code: str):
        return (
            (await db.execute(select(MinglueDevice).where(MinglueDevice.device_code == device_code))).scalars().first()
        )

    @classmethod
    async def get_list(cls, db: AsyncSession, query: DevicePageQueryModel, is_page: bool = True):
        statement = (
            select(MinglueDevice)
            .where(
                MinglueDevice.device_code.like(f'%{query.device_code}%') if query.device_code else True,
                MinglueDevice.device_name.like(f'%{query.device_name}%') if query.device_name else True,
                MinglueDevice.model.like(f'%{query.model}%') if query.model else True,
                MinglueDevice.status == query.status if query.status else True,
                MinglueDevice.online_status == query.online_status if query.online_status is not None else True,
                MinglueDevice.bind_status == query.bind_status if query.bind_status else True,
                MinglueDevice.owner_name.like(f'%{query.owner_name}%') if query.owner_name else True,
            )
            .order_by(MinglueDevice.device_id.desc())
        )
        return await PageUtil.paginate(db, statement, query.page_num, query.page_size, is_page)

    @classmethod
    async def get_stale_online_devices(cls, db: AsyncSession, threshold: datetime):
        """查出"本地仍标记在线但心跳已超时"的设备；停用设备不在范围内。"""
        return (
            (
                await db.execute(
                    select(MinglueDevice).where(
                        MinglueDevice.status == 'online',
                        MinglueDevice.last_seen_time.is_not(None),
                        MinglueDevice.last_seen_time < threshold,
                    )
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def add(cls, db: AsyncSession, device: DeviceModel):
        record = MinglueDevice(**device.model_dump())
        db.add(record)
        await db.flush()
        return record

    @classmethod
    async def update(cls, db: AsyncSession, values: dict):
        await db.execute(update(MinglueDevice), [values])

    @classmethod
    async def delete(cls, db: AsyncSession, device_ids: list[int]):
        await db.execute(delete(MinglueDevice).where(MinglueDevice.device_id.in_(device_ids)))


class CallbackDao:
    @classmethod
    async def get_by_id(cls, db: AsyncSession, callback_id: int):
        return (
            (await db.execute(select(MinglueCallbackLog).where(MinglueCallbackLog.callback_id == callback_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_by_dedup_key(cls, db: AsyncSession, dedup_key: str):
        """按幂等键查重；``dedup_key`` 非空唯一，重复回调在此被识别。"""
        return (
            (
                await db.execute(
                    select(MinglueCallbackLog).where(MinglueCallbackLog.dedup_key == dedup_key)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_by_event_id(cls, db: AsyncSession, event_id: str):
        return (
            (await db.execute(select(MinglueCallbackLog).where(MinglueCallbackLog.event_id == event_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_list(cls, db: AsyncSession, query: CallbackPageQueryModel):
        statement = (
            select(MinglueCallbackLog)
            .where(
                MinglueCallbackLog.event_type.like(f'%{query.event_type}%') if query.event_type else True,
                MinglueCallbackLog.device_code.like(f'%{query.device_code}%') if query.device_code else True,
                MinglueCallbackLog.process_status == query.process_status if query.process_status else True,
                MinglueCallbackLog.duplicate_flag == query.duplicate_flag if query.duplicate_flag else True,
                MinglueCallbackLog.session_id == query.session_id if query.session_id else True,
                _date_range(MinglueCallbackLog.received_at, query.begin_time, query.end_time),
            )
            .order_by(MinglueCallbackLog.callback_id.desc())
        )
        return await PageUtil.paginate(db, statement, query.page_num, query.page_size, True)

    @classmethod
    async def add(cls, db: AsyncSession, values: dict):
        record = MinglueCallbackLog(**values)
        db.add(record)
        await db.flush()
        return record

    @classmethod
    async def delete(cls, db: AsyncSession, callback_ids: list[int]):
        await db.execute(delete(MinglueCallbackLog).where(MinglueCallbackLog.callback_id.in_(callback_ids)))


class RecordingDao:
    """录音文件与转码/ASR 产物。"""

    @classmethod
    async def get_by_object_key(cls, db: AsyncSession, object_key: str):
        return (
            (
                await db.execute(
                    select(MinglueRecordingFile).where(MinglueRecordingFile.object_key == object_key)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_by_transcode_task_id(cls, db: AsyncSession, task_id: str):
        return (
            (
                await db.execute(
                    select(MinglueRecordingFile).where(MinglueRecordingFile.transcode_task_id == task_id)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_by_transcode_file_path(cls, db: AsyncSession, file_path: str):
        return (
            (
                await db.execute(
                    select(MinglueRecordingFile).where(MinglueRecordingFile.transcode_file_path == file_path)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_by_asr_task_id(cls, db: AsyncSession, task_id: str):
        return (
            (await db.execute(select(MinglueRecordingFile).where(MinglueRecordingFile.asr_task_id == task_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_list(cls, db: AsyncSession, query: RecordingPageQueryModel):
        statement = (
            select(MinglueRecordingFile)
            .where(
                MinglueRecordingFile.device_code.like(f'%{query.device_code}%') if query.device_code else True,
                MinglueRecordingFile.object_key.like(f'%{query.object_key}%') if query.object_key else True,
                MinglueRecordingFile.record_status == query.record_status if query.record_status else True,
                MinglueRecordingFile.transcode_task_id == query.transcode_task_id if query.transcode_task_id else True,
                MinglueRecordingFile.asr_task_id == query.asr_task_id if query.asr_task_id else True,
                _date_range(MinglueRecordingFile.event_time, query.begin_time, query.end_time),
            )
            .order_by(MinglueRecordingFile.recording_id.desc())
        )
        return await PageUtil.paginate(db, statement, query.page_num, query.page_size, True)

    @classmethod
    async def add(cls, db: AsyncSession, values: dict):
        record = MinglueRecordingFile(**values)
        db.add(record)
        await db.flush()
        return record

    @classmethod
    async def update(cls, db: AsyncSession, values: dict):
        await db.execute(update(MinglueRecordingFile), [values])

    @classmethod
    async def delete(cls, db: AsyncSession, recording_ids: list[int]):
        await db.execute(delete(MinglueRecordingFile).where(MinglueRecordingFile.recording_id.in_(recording_ids)))


class ControlLogDao:
    """设备控制指令日志。"""

    @classmethod
    async def get_by_msg_id(cls, db: AsyncSession, msg_id: str):
        return (
            (await db.execute(select(MinglueDeviceControlLog).where(MinglueDeviceControlLog.msg_id == msg_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_list(cls, db: AsyncSession, query: ControlLogPageQueryModel):
        statement = (
            select(MinglueDeviceControlLog)
            .where(
                MinglueDeviceControlLog.device_code.like(f'%{query.device_code}%') if query.device_code else True,
                MinglueDeviceControlLog.command == query.command if query.command else True,
                MinglueDeviceControlLog.request_status == query.request_status if query.request_status else True,
                MinglueDeviceControlLog.msg_id == query.msg_id if query.msg_id else True,
                _date_range(MinglueDeviceControlLog.create_time, query.begin_time, query.end_time),
            )
            .order_by(MinglueDeviceControlLog.control_id.desc())
        )
        return await PageUtil.paginate(db, statement, query.page_num, query.page_size, True)

    @classmethod
    async def add(cls, db: AsyncSession, values: dict):
        record = MinglueDeviceControlLog(**values)
        db.add(record)
        await db.flush()
        return record

    @classmethod
    async def update(cls, db: AsyncSession, values: dict):
        await db.execute(update(MinglueDeviceControlLog), [values])

    @classmethod
    async def delete(cls, db: AsyncSession, control_ids: list[int]):
        await db.execute(delete(MinglueDeviceControlLog).where(MinglueDeviceControlLog.control_id.in_(control_ids)))
