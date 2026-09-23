"""Stage 1 遗留的集成测试，已按 Stage 2（GYTAI-128）的新契约重写。

原测试断言了当时（Stage 1）的行为：
- 心跳回调 `event_id is None`
- `rec` 回调只取首条 `object_key` 作为幂等键

这两条正是 Stage 1 契约文档标记的 P0 缺陷（B11 / D5），Stage 2 已修复，
因此测试改为断言**修复后的**契约行为，并在下方保留了回归保护点。
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from module_device.dao.device_dao import CallbackDao, DeviceDao, RecordingDao
from module_device.service.device_service import CallbackService
from module_device.service.minglue_api_service import MinglueApiService


def test_encrypt_password_matches_aes_128_ecb_pkcs7_vector():
    """A2：AES-128-ECB + PKCS7 + Base64。

    这里改用**非敏感的合成测试密钥**，不记录 Apifox 文档示例里的那个 16 字节
    密钥（契约 §6-4 / D10：无法确认它是否为真实厂商凭证，因此不留在仓库里）。
    期望密文由独立实现复算得到，不取自被测代码：

    ```text
    printf '%s' 'xxx' | openssl enc -aes-128-ecb -K 746573742d6f6e6c792d6b65792d3031 -nosalt -base64
    → JqR3ZGo6WmN/7nxhQXOKLg==
    ```
    """
    assert MinglueApiService.encrypt_password('xxx', 'test-only-key-01') == 'JqR3ZGo6WmN/7nxhQXOKLg=='


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
        'online': 2,
    }
    with (
        patch.object(DeviceDao, 'get_by_code', AsyncMock(return_value=None)),
        patch.object(DeviceDao, 'add', AsyncMock()) as add_device,
        patch.object(CallbackDao, 'get_by_dedup_key', AsyncMock(return_value=None)),
        patch.object(CallbackDao, 'add', AsyncMock(return_value=SimpleNamespace(callback_id=7))) as add_callback,
        patch.object(CallbackService, '_persist_business_item', AsyncMock()),
    ):
        result = await CallbackService.receive(db, payload, None, '127.0.0.1')

    device = add_device.await_args.args[1]
    assert device.device_code == payload['sn']
    assert device.status == 'online'
    # D2：三态 online 原值必须留存，不能被压扁成 online/offline
    assert device.online_status == 2
    assert device.device_status == 1
    assert device.record_status == 1
    assert device.battery_level == 40
    assert device.remain_storage == 14833
    assert device.total_storage == 14950
    assert device.audio_id == 'ciqzi12345'
    assert device.heartbeat_time == datetime(2025, 11, 1, 19, 37, 23)

    callback_values = add_callback.await_args.args[1]
    assert callback_values['event_type'] == 'heartbeat'
    # B11/D5：心跳没有业务键，但幂等键必须非空（否则唯一索引无法去重）
    assert callback_values['event_id'] is None
    assert callback_values['dedup_key']
    assert len(callback_values['dedup_key']) == 64
    assert callback_values['process_status'] == 'success'
    assert callback_values['duplicate_flag'] == 'N'
    assert result['event_type'] == 'heartbeat'
    assert result['items'] == 1
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recording_callback_persists_every_device_and_uses_per_object_key_idempotency():
    """B11：`rec` 单次多设备时，每个 object_key 都要各自落库与更新设备。"""
    db = AsyncMock()
    payload = {
        'type': 'rec',
        'session_id': 61318,
        'topic_name': 'hermes',
        'content': [
            {'sn': 'SN-1', 'object_key': 'lt4/rec/one.opus', 'duration': 1000},
            {'sn': 'SN-2', 'object_key': 'lt4/rec/two.opus', 'duration': 2000},
        ],
    }
    with (
        patch.object(CallbackDao, 'get_by_dedup_key', AsyncMock(return_value=None)),
        patch.object(CallbackService, '_apply_item', AsyncMock()) as apply_item,
        patch.object(RecordingDao, 'get_by_object_key', AsyncMock(return_value=None)),
        patch.object(RecordingDao, 'add', AsyncMock()) as add_recording,
        patch.object(CallbackDao, 'add', AsyncMock(return_value=SimpleNamespace(callback_id=8))) as add_callback,
    ):
        result = await CallbackService.receive(db, payload, None, None)

    # 两个设备都被更新，不再只处理首条
    assert [call.args[2] for call in apply_item.await_args_list] == ['SN-1', 'SN-2']
    # 两条录音各自建档
    assert [call.args[1]['object_key'] for call in add_recording.await_args_list] == [
        'lt4/rec/one.opus',
        'lt4/rec/two.opus',
    ]

    # 每条回调记录的幂等键互不相同
    callback_values = [call.args[1] for call in add_callback.await_args_list]
    assert len(callback_values) == 2
    assert all(item['event_type'] == 'rec' for item in callback_values)
    assert callback_values[0]['event_id'] == 'lt4/rec/one.opus'
    assert callback_values[1]['event_id'] == 'lt4/rec/two.opus'
    keys = {item['dedup_key'] for item in callback_values}
    assert len(keys) == 2 and all(keys)
    assert result['items'] == 2


@pytest.mark.asyncio
async def test_duplicate_callback_returns_duplicate_and_does_not_update_device():
    """D5：真实重复回调必须被识别，且不重复更新设备状态。"""
    db = AsyncMock()
    payload = {'sn': 'SN-1', 'device_status': 1, 'update_time': '2025-11-01 19:37:23'}
    with (
        patch.object(CallbackDao, 'get_by_dedup_key', AsyncMock(return_value=SimpleNamespace(callback_id=11))),
        patch.object(CallbackDao, 'add', AsyncMock()) as add_callback,
        patch.object(CallbackService, '_apply_item', AsyncMock()) as apply_item,
    ):
        result = await CallbackService.receive(db, payload, None, None)

    assert result == {'callback_id': 11, 'duplicate': True}
    apply_item.assert_not_awaited()
    # 重复回调仍留痕，且标记 duplicate_flag='Y'
    recorded = add_callback.await_args.args[1]
    assert recorded['duplicate_flag'] == 'Y'
    assert recorded['process_status'] == 'duplicate'


@pytest.mark.asyncio
async def test_upload_callback_is_not_misclassified_as_rec():
    """B6：`logType=rec` 的 upload 不能落成 `rec` 类型（与真实录音回调冲突）。"""
    db = AsyncMock()
    payload = {
        'updatedAt': '2025-04-07 16:22:55',
        'logType': 'rec',
        'sn': 'MLR431Z08EI3OI9E',
        'objectKey': 'lt4/rec/x.opus',
        'fileName': 'x.opus',
        'status': 6,
    }
    with (
        patch.object(CallbackDao, 'get_by_dedup_key', AsyncMock(return_value=None)),
        patch.object(CallbackDao, 'add', AsyncMock(return_value=SimpleNamespace(callback_id=9))) as add_callback,
        patch.object(CallbackService, '_apply_item', AsyncMock()),
    ):
        await CallbackService.receive(db, payload, None, None)

    recorded = add_callback.await_args.args[1]
    assert recorded['event_type'] == 'upload'
    assert recorded['log_type'] == 'rec'


@pytest.mark.asyncio
async def test_failed_processing_still_records_raw_payload():
    """B12：处理失败时原始报文必须留痕，不能随事务回滚一起消失。"""
    db = AsyncMock()
    payload = {'type': 'rec', 'content': [{'sn': 'SN-1', 'object_key': 'lt4/rec/a.opus'}]}
    recorded = []

    async def _add(_db, values):
        recorded.append(values)
        return SimpleNamespace(callback_id=len(recorded))

    with (
        patch.object(CallbackDao, 'get_by_dedup_key', AsyncMock(return_value=None)),
        patch.object(CallbackDao, 'add', AsyncMock(side_effect=_add)),
        patch.object(CallbackService, '_apply_item', AsyncMock(side_effect=RuntimeError('设备更新失败'))),
    ):
        with pytest.raises(RuntimeError):
            await CallbackService.receive(db, payload, None, None)

    failure_records = [item for item in recorded if item.get('process_status') == 'failed']
    assert failure_records, '失败回调必须留下审计记录'
    assert failure_records[0]['payload_json'] == (
        '{"type": "rec", "content": [{"sn": "SN-1", "object_key": "lt4/rec/a.opus"}]}'
    )
    assert '设备更新失败' in failure_records[0]['error_message']
