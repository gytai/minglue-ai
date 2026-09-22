from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from module_device.dao.device_dao import CallbackDao, DeviceDao
from module_device.service.device_service import CallbackService
from module_device.service.minglue_api_service import MinglueApiService


def test_encrypt_password_matches_aes_128_ecb_pkcs7_vector():
    assert MinglueApiService.encrypt_password('xxx', '10c0a163653b0071') == 'R5V4MqWkJ4kD/zNcqaxPwQ=='


@pytest.mark.asyncio
async def test_heartbeat_callback_maps_vendor_fields_to_device():
    db = AsyncMock()
    payload = {
        'sn': 'MLR432KP42C19568',
        'device_status': 1,
        'record_status': 1,
        'remain_power': 40,
        'remain_storage': 14833,
        'rssi': 31,
        'update_time': '2025-11-01 19:37:23',
        'version': '5.4.49',
        'usb_status': 1,
        'battery_voltage': 3955,
        'battery_current': 420,
        'charged_status': 'cccv',
        'total_storage': 14950,
        'rectd': 60,
        'recnu': 16,
        'tenant_id': 100,
        'nm': 'ciqzi12345',
    }
    with (
        patch.object(DeviceDao, 'get_by_code', AsyncMock(return_value=None)),
        patch.object(DeviceDao, 'add', AsyncMock()) as add_device,
        patch.object(CallbackDao, 'add', AsyncMock(return_value=SimpleNamespace(callback_id=7))) as add_callback,
    ):
        result = await CallbackService.receive(db, payload, None, '127.0.0.1')

    device = add_device.await_args.args[1]
    assert device.device_code == payload['sn']
    assert device.status == 'online'
    assert device.record_status == 1
    assert device.battery_level == 40
    assert device.remain_storage == 14833
    assert device.total_storage == 14950
    assert device.audio_id == 'ciqzi12345'
    callback_values = add_callback.await_args.args[1]
    assert callback_values['event_type'] == 'heartbeat'
    assert callback_values['event_id'] is None
    assert result == {'callback_id': 7, 'duplicate': False}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recording_callback_uses_object_key_for_idempotency_and_updates_all_devices():
    db = AsyncMock()
    payload = {
        'type': 'rec',
        'session_id': 61318,
        'content': [
            {'sn': 'SN-1', 'object_key': 'lt4/rec/one.opus'},
            {'sn': 'SN-2', 'object_key': 'lt4/rec/two.opus'},
        ],
    }
    with (
        patch.object(CallbackDao, 'get_by_event_id', AsyncMock(return_value=None)),
        patch.object(CallbackService, '_upsert_device', AsyncMock()) as upsert,
        patch.object(CallbackDao, 'add', AsyncMock(return_value=SimpleNamespace(callback_id=8))) as add_callback,
    ):
        await CallbackService.receive(db, payload, None, None)

    assert [call.args[1] for call in upsert.await_args_list] == ['SN-1', 'SN-2']
    callback_values = add_callback.await_args.args[1]
    assert callback_values['event_id'] == 'lt4/rec/one.opus'
    assert callback_values['event_type'] == 'rec'
