from datetime import datetime, time

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from module_device.entity.do.device_do import MinglueCallbackLog, MinglueDevice
from module_device.entity.vo.device_vo import CallbackPageQueryModel, DeviceModel, DevicePageQueryModel
from utils.page_util import PageUtil


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
                MinglueDevice.bind_status == query.bind_status if query.bind_status else True,
                MinglueDevice.owner_name.like(f'%{query.owner_name}%') if query.owner_name else True,
            )
            .order_by(MinglueDevice.device_id.desc())
        )
        return await PageUtil.paginate(db, statement, query.page_num, query.page_size, is_page)

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
    async def get_by_event_id(cls, db: AsyncSession, event_id: str):
        return (
            (await db.execute(select(MinglueCallbackLog).where(MinglueCallbackLog.event_id == event_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_list(cls, db: AsyncSession, query: CallbackPageQueryModel):
        received_filter = True
        if query.begin_time and query.end_time:
            received_filter = MinglueCallbackLog.received_at.between(
                datetime.combine(datetime.strptime(query.begin_time, '%Y-%m-%d'), time.min),
                datetime.combine(datetime.strptime(query.end_time, '%Y-%m-%d'), time.max),
            )
        statement = (
            select(MinglueCallbackLog)
            .where(
                MinglueCallbackLog.event_type.like(f'%{query.event_type}%') if query.event_type else True,
                MinglueCallbackLog.device_code.like(f'%{query.device_code}%') if query.device_code else True,
                MinglueCallbackLog.process_status == query.process_status if query.process_status else True,
                received_filter,
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
