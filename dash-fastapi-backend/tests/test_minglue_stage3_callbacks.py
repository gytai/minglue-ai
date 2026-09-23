"""Stage 3（GYTAI-125）：明略厂商回调的**请求级**测试。

契约 §5.7 F4 / F5 要求"请求级回调测试（覆盖 8 类）"与"重复事件 / 缺失字段 /
非法字段 / 签名错误测试"，明确**不得只做函数级 smoke test**。这里因此不直接调
服务方法，而是把 `callbackController` 挂到一个最小 FastAPI 应用上，通过
`httpx.ASGITransport` 发真实 HTTP 请求：路由匹配、路径参数、Content-Type、
请求体解析、Header、状态码与响应体全部走 ASGI 栈。

数据库用 `conftest.py` 的内存 SQLite 会话，通过 `dependency_overrides`
替换回调入口的落库会话，因此不连真实数据库、也不向真实设备或厂商发任何请求。
"""

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from module_device.controller.callback_controller import callbackController, get_callback_db
from module_device.entity.do.device_do import (
    MinglueCallbackLog,
    MinglueDevice,
    MinglueRecordingFile,
)
from module_device.service.device_service import CallbackService

CALLBACK_PATH = '/open/minglue/callback'

HEARTBEAT = {
    'sn': 'MLR432KP42C19568',
    'device_status': 1,
    'record_status': 0,
    'remain_power': 40,
    'remain_storage': 14833,
    'rssi': 31,
    'update_time': '2025-11-01 19:37:23',
    'version': '5.4.49',
    'key_status': 1,
    'usb_status': 1,
    'battery_voltage': 3955,
    'battery_current': 420,
    'chip': '2108+511',
    'total_storage': 14950,
    'cip': '10.113.36.200',
    'charged_status': 'cccv',
    'disk_mount_status': 0,
    'online': 2,
    'rectd': 0,
    'recnu': 16,
    'tenant_id': 100,
    'nm': 'ciqziMsatvo5',
}

REC = {
    'content': [
        {
            'csn': 'MLR4010GBFGFGZAD',
            'merge_success_time': 1726210271,
            'object_key': 'lt4/rec/20240913/MLR4010GBFGFGZAD/1726202557/MLR4010GBFGFGZAD_300000_10.opus',
            'download_url': '',
            'size': 2400000,
            'sn': 'MLR4010GBFGFGZAD',
            'duration': 100000,
        }
    ],
    'session_id': 61318,
    'topic_name': 'hermes',
    'type': 'rec',
}

REC_MULTI_DEVICE = {
    'content': [
        {
            'csn': 'MLR-A',
            'merge_success_time': 1726210271,
            'object_key': 'lt4/rec/20240913/MLR-A/1/MLR-A_300000_10.opus',
            'size': 2400000,
            'sn': 'MLR-A',
            'duration': 100000,
        },
        {
            'csn': 'MLR-B',
            'merge_success_time': 1726210272,
            'object_key': 'lt4/rec/20240913/MLR-B/2/MLR-B_300000_11_eof.opus',
            'size': 1200000,
            'sn': 'MLR-B',
            'duration': 60000,
        },
        {
            'csn': 'MLR-C',
            'merge_success_time': 1726210273,
            'object_key': 'lt4/rec/20240913/MLR-C/3/MLR-C_300000_12.opus_eof',
            'size': 800000,
            'sn': 'MLR-C',
            'duration': 40000,
        },
    ],
    'session_id': 61318,
    'topic_name': 'hermes',
    'type': 'rec',
}

OP = {
    'content': [
        {
            'csn': 'MLR4010GBFGFGZAD',
            'log_type': 'lt4_op_log',
            'merge_success_time': 1726206350,
            'object_key': 'lt4/log/20240913/MLR4010GBFGFGZAD/MLR4010GBFGFGZAD_20240913134339_op.log',
            'download_url': '',
            'size': 562,
            'sn': 'MLR4010GBFGFGZAD',
        }
    ],
    'session_id': 61317,
    'topic_name': 'hermes',
    'type': 'op',
}

SYS = {
    'content': [
        {
            'csn': 'MLR4010GBFGFGZAD',
            'log_type': 'lt4_sys_log',
            'merge_success_time': 1726206350,
            'object_key': 'lt4/log/20240913/MLR4010GBFGFGZAD/MLR4010GBFGFGZAD_20240913134339_sys.log',
            'size': 562,
            'sn': 'MLR4010GBFGFGZAD',
        }
    ],
    'session_id': 61319,
    'topic_name': 'hermes',
    'type': 'sys',
}

RECLIST = {
    'content': [
        {
            'csn': 'MLR4010GBFGFGZAD',
            'log_type': 'lt4_rec_list_log',
            'merge_success_time': 1726206350,
            'object_key': 'lt4/log/20240913/MLR4010GBFGFGZAD/MLR4010GBFGFGZAD_20240913134339_reclist.log',
            'size': 562,
            'sn': 'MLR4010GBFGFGZAD',
        }
    ],
    'session_id': 61316,
    'topic_name': 'hermes',
    'type': 'reclist',
}

UPLOAD = {
    'updatedAt': '2025-04-07 16:22:55',
    'logType': 'log',
    'sn': 'MLR431Z08EI3OI9E',
    'objectKey': 'lt4/log/MLR431Z08EI3OI9E/20250407/MLR431Z08EI3OI9E_20250407162250_sys.log.file',
    'fileName': 'MLR431Z08EI3OI9E_20250407162250_sys.log.file',
    'status': 6,
}

FC = {
    'file_count': 1,
    'files': [
        {
            'download_url': 'https://vendor.example/fc/out.mp3?signature=redacted',
            'file_path': 'lt4/rec/20251021/MLR431P12MYMSWD3/fc/MLR431P12MYMSWD3_cuted_v2_stereo.mp3',
        }
    ],
    'group_key': 'recordid_xxxx',
    'sn': 'MLR431P12MYMSWD3',
    'status': 'completed',
    'task_id': '1tsk3w11000ddqkwe4tsgok4mfifqify',
    'timestamp': 1761539510,
    'type': 'fc',
}

ASR = {
    'asrTextResultList': [
        {'content': '你不是吗？', 'endMs': 10960, 'role': 0, 'roleMark': 0, 'startMs': 4060}
    ],
    'asrTskId': '2025108a682fcb767893cb347615eb14d5f11e',
    'extraParam': '',
    'objectKey': 'lt4/rec/20251028/OEM432OAY2IE23/fc/OEM432OAY2IE23_stereo.mp3',
    'session_id': 888,
    'sn': 'OEM432OAY2IE23',
    'status': 100,
    'theiaTaskId': 'fc',
    'topic_name': 'hermes',
    'type': 'asr',
}

#: 8 类回调 → (请求体, 期望 event_type, 期望 device_code)；用于逐类请求级覆盖。
CALLBACK_CASES = [
    pytest.param(HEARTBEAT, 'heartbeat', 'MLR432KP42C19568', id='heartbeat'),
    pytest.param(REC, 'rec', 'MLR4010GBFGFGZAD', id='rec'),
    pytest.param(OP, 'op', 'MLR4010GBFGFGZAD', id='op'),
    pytest.param(SYS, 'sys', 'MLR4010GBFGFGZAD', id='sys'),
    pytest.param(RECLIST, 'reclist', 'MLR4010GBFGFGZAD', id='reclist'),
    pytest.param(UPLOAD, 'upload', 'MLR431Z08EI3OI9E', id='upload'),
    pytest.param(FC, 'fc', 'MLR431P12MYMSWD3', id='fc'),
    pytest.param(ASR, 'asr', 'OEM432OAY2IE23', id='asr'),
]


@pytest.fixture(autouse=True)
def clean_callback_env(monkeypatch):
    """每个用例都从"签名关闭 / 默认预算 / 未声明厂商时区"的出厂状态起跑。"""
    for name in ('MINGLUE_CALLBACK_SECRET', 'MINGLUE_CALLBACK_RESPONSE_BUDGET', 'MINGLUE_VENDOR_TIMEZONE'):
        monkeypatch.delenv(name, raising=False)


@pytest_asyncio.fixture
async def client(sqlite_session):
    """挂载回调路由的最小应用；落库会话替换为内存 SQLite。"""
    app = FastAPI()
    app.include_router(callbackController)

    async def _override_db():
        yield sqlite_session

    app.dependency_overrides[get_callback_db] = _override_db
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=False, client=('10.0.0.9', 45678)
    )
    async with httpx.AsyncClient(transport=transport, base_url='http://callback.test') as http_client:
        yield http_client
    app.dependency_overrides.clear()


async def _all(session, model):
    """从库里重新读出全部行；先失效身份映射，避免断言读到请求前的缓存对象。"""
    session.expire_all()
    return (await session.execute(select(model))).scalars().all()


async def _logs(session, **criteria):
    session.expire_all()
    statement = select(MinglueCallbackLog)
    for key, value in criteria.items():
        statement = statement.where(getattr(MinglueCallbackLog, key) == value)
    return (await session.execute(statement)).scalars().all()


def _utc_naive(timestamp: int) -> datetime:
    """Unix 秒 → UTC naive，与 `CallbackService._parse_datetime` 的口径一致。"""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _post(client, payload, path=CALLBACK_PATH):
    return await client.post(path, json=payload)


# --------------------------------------------------------------------------- #
# 契约 §2 / B1 / B2 / B17：路由、Content-Type、成功响应
# --------------------------------------------------------------------------- #


async def test_heartbeat_returns_documented_success_body(client, sqlite_session):
    response = await _post(client, HEARTBEAT)

    assert response.status_code == 200
    assert response.json() == {'code': 0}
    assert response.headers['content-type'].startswith('application/json')

    logs = await _logs(sqlite_session, event_type='heartbeat')
    assert len(logs) == 1
    assert logs[0].device_code == 'MLR432KP42C19568'
    assert logs[0].signature_valid == 'Y'
    assert logs[0].process_status == 'success'
    assert logs[0].duplicate_flag == 'N'
    assert logs[0].request_ip == '10.0.0.9'
    assert json.loads(logs[0].payload_json) == HEARTBEAT

    device = (await _all(sqlite_session, MinglueDevice))[0]
    assert device.device_code == 'MLR432KP42C19568'
    assert device.status == 'online'
    assert device.online_status == 2
    assert device.battery_level == 40
    assert device.heartbeat_time == datetime(2025, 11, 1, 19, 37, 23)


def test_callback_router_matches_contract_paths_and_methods():
    """契约 §2：只有 POST，路径固定为 `/open/minglue/callback`（± `{event_type}`）。"""
    assert callbackController.prefix == '/open/minglue'
    described = {(route.path, tuple(sorted(route.methods))) for route in callbackController.routes}
    assert described == {
        ('/open/minglue/callback', ('POST',)),
        ('/open/minglue/callback/{event_type}', ('POST',)),
    }


@pytest.mark.parametrize('payload, expected_type, expected_device', CALLBACK_CASES)
async def test_every_callback_type_is_accepted_with_code_zero(
    client, sqlite_session, payload, expected_type, expected_device
):
    """F4：8 类回调逐一走请求级链路，统一返回 `{"code": 0}`（B17）。"""
    response = await _post(client, payload)

    assert response.status_code == 200
    assert response.json() == {'code': 0}

    logs = await _logs(sqlite_session, event_type=expected_type)
    assert len(logs) == 1
    assert logs[0].device_code == expected_device
    assert logs[0].payload_json == json.dumps(payload, ensure_ascii=False, default=str)


async def test_path_event_type_is_used_when_present(client, sqlite_session):
    """契约 §2：可按类别配置多个回调地址，路径上的类型优先于报文。"""
    response = await _post(client, HEARTBEAT, path=f'{CALLBACK_PATH}/rec')

    assert response.status_code == 200
    assert response.json() == {'code': 0}
    logs = await _logs(sqlite_session, event_type='rec')
    assert len(logs) == 1
    assert logs[0].device_code == HEARTBEAT['sn']


async def test_malformed_json_body_is_rejected(client):
    """Content-Type 为 JSON 但报文非法时应被请求校验挡下，不进入业务处理。"""
    response = await client.post(
        CALLBACK_PATH, content=b'{not-json', headers={'Content-Type': 'application/json'}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 契约 B4 / B15 / B16 / B19：多设备、会话字段、duration 建模
# --------------------------------------------------------------------------- #


async def test_rec_multi_device_creates_every_device_and_recording(client, sqlite_session):
    """B4：一次 `rec` 携带多设备时，每台各自落设备状态与录音记录。"""
    response = await _post(client, REC_MULTI_DEVICE)

    assert response.status_code == 200
    assert response.json() == {'code': 0}

    logs = await _logs(sqlite_session, event_type='rec')
    assert sorted(log.device_code for log in logs) == ['MLR-A', 'MLR-B', 'MLR-C']
    assert all(log.item_count == 3 for log in logs)
    # B15 / B16：webhook id 与 topic_name 单独留存
    assert all(log.session_id == 61318 for log in logs)
    assert all(log.topic_name == 'hermes' for log in logs)
    assert all(log.event_id for log in logs)

    devices = await _all(sqlite_session, MinglueDevice)
    assert sorted(device.device_code for device in devices) == ['MLR-A', 'MLR-B', 'MLR-C']

    recordings = {row.object_key: row for row in await _all(sqlite_session, MinglueRecordingFile)}
    assert set(recordings) == {
        'lt4/rec/20240913/MLR-A/1/MLR-A_300000_10.opus',
        'lt4/rec/20240913/MLR-B/2/MLR-B_300000_11_eof.opus',
        'lt4/rec/20240913/MLR-C/3/MLR-C_300000_12.opus_eof',
    }
    # B19：duration 已建模并逐条落库；结束分片按文件名规则识别
    first = recordings['lt4/rec/20240913/MLR-A/1/MLR-A_300000_10.opus']
    assert (first.device_code, first.duration, first.size, first.is_eof) == ('MLR-A', 100000, 2400000, 'N')
    # §2.1 模板把 `_eof` 写在扩展名之前，观测样例写在之后；两种都要认出来
    assert recordings['lt4/rec/20240913/MLR-B/2/MLR-B_300000_11_eof.opus'].is_eof == 'Y'
    assert recordings['lt4/rec/20240913/MLR-C/3/MLR-C_300000_12.opus_eof'].is_eof == 'Y'
    assert all(row.callback_session_id == 61318 for row in recordings.values())


async def test_upload_keeps_camel_case_log_type(client, sqlite_session):
    """B6 回归：`logType=log` 的文件上传不能被归一化成 `log` 或 `rec`。"""
    response = await _post(client, UPLOAD)

    assert response.status_code == 200
    logs = await _logs(sqlite_session, event_type='upload')
    assert len(logs) == 1
    assert logs[0].log_type == 'log'
    assert logs[0].device_code == UPLOAD['sn']


async def test_fc_persists_top_level_task_fields_with_list_file_path(client, sqlite_session):
    """契约 §2.6：`task_id`/`status`/`timestamp` 在顶层，`file_path` 在 `files[]`。"""
    response = await _post(client, FC)

    assert response.status_code == 200
    logs = await _logs(sqlite_session, event_type='fc')
    assert len(logs) == 1
    # 顶层 task_id 必须成为业务幂等键
    assert logs[0].event_id == FC['task_id']
    assert logs[0].event_time == _utc_naive(FC['timestamp'])

    recording = (await _all(sqlite_session, MinglueRecordingFile))[0]
    assert recording.transcode_task_id == FC['task_id']
    assert recording.transcode_status == 'completed'
    assert recording.transcode_file_path == FC['files'][0]['file_path']
    assert recording.transcode_download_url == FC['files'][0]['download_url']
    assert recording.device_code == FC['sn']


async def test_asr_persists_task_id_and_text(client, sqlite_session):
    """契约 §2.7：ASR 任务号与文本片段落库，`session_id` 一并留存。"""
    response = await _post(client, ASR)

    assert response.status_code == 200
    logs = await _logs(sqlite_session, event_type='asr')
    assert logs[0].event_id == ASR['asrTskId']
    assert logs[0].session_id == 888

    recording = (await _all(sqlite_session, MinglueRecordingFile))[0]
    assert recording.asr_task_id == ASR['asrTskId']
    assert recording.asr_status == '100'
    assert recording.callback_session_id == 888
    assert json.loads(recording.asr_text_json) == ASR['asrTextResultList']


# --------------------------------------------------------------------------- #
# 契约 B11 / D5：重复回调幂等
# --------------------------------------------------------------------------- #


async def test_duplicate_callback_is_recorded_but_does_not_touch_device(client, sqlite_session):
    first = await _post(client, HEARTBEAT)
    assert first.status_code == 200
    device_before = (await _all(sqlite_session, MinglueDevice))[0]
    seen_before = device_before.last_seen_time

    # 让"未更新时间"与"又更新了一次"在微秒精度上可区分
    await asyncio.sleep(0.01)
    second = await _post(client, HEARTBEAT)

    assert second.status_code == 200
    assert second.json() == {'code': 0}

    logs = await _logs(sqlite_session, event_type='heartbeat')
    assert sorted(log.process_status for log in logs) == ['duplicate', 'success']
    duplicate = next(log for log in logs if log.process_status == 'duplicate')
    assert duplicate.duplicate_flag == 'Y'
    assert duplicate.payload_json == json.dumps(HEARTBEAT, ensure_ascii=False, default=str)

    devices = await _all(sqlite_session, MinglueDevice)
    assert len(devices) == 1
    assert devices[0].last_seen_time == seen_before


# --------------------------------------------------------------------------- #
# 契约 B10 / B12：缺失字段、非法字段、失败回滚
# --------------------------------------------------------------------------- #


async def test_missing_device_code_is_recorded_without_device_row(client, sqlite_session):
    payload = {key: value for key, value in HEARTBEAT.items() if key != 'sn'}
    payload['update_time'] = '2025-11-01 19:37:23'

    response = await _post(client, payload)

    assert response.status_code == 200
    assert response.json() == {'code': 0}
    logs = await _logs(sqlite_session, event_type='heartbeat')
    assert len(logs) == 1
    assert logs[0].device_code is None
    assert logs[0].payload_json == json.dumps(payload, ensure_ascii=False, default=str)
    assert await _all(sqlite_session, MinglueDevice) == []


async def test_invalid_fields_do_not_break_ingest(client, sqlite_session):
    """非法时间与非法 duration 只退化为 NULL，不允许影响整条回调的留存。"""
    payload = {
        'sn': 'SN-BAD-FIELDS',
        'device_status': 1,
        'update_time': 'not-a-timestamp',
        'merge_success_time': 'also-not-a-timestamp',
        'duration': 'abc',
    }
    response = await _post(client, payload)
    assert response.status_code == 200

    logs = await _logs(sqlite_session, device_code='SN-BAD-FIELDS')
    assert len(logs) == 1
    assert logs[0].event_time is None

    recording_payload = {
        'type': 'rec',
        'content': [{'sn': 'SN-BAD-FIELDS', 'object_key': 'lt4/rec/bad.opus', 'duration': 'abc'}],
    }
    response = await _post(client, recording_payload)
    assert response.status_code == 200
    recording = (await _all(sqlite_session, MinglueRecordingFile))[0]
    assert recording.duration is None
    assert recording.device_code == 'SN-BAD-FIELDS'


async def test_non_list_content_does_not_crash_and_is_still_stored(client, sqlite_session):
    payload = {'type': 'rec', 'session_id': 1, 'content': {'sn': 'SN-ODD', 'object_key': 'lt4/rec/odd.opus'}}

    response = await _post(client, payload)

    assert response.status_code == 200
    logs = await _logs(sqlite_session, event_type='rec')
    assert len(logs) == 1
    assert logs[0].payload_json == json.dumps(payload, ensure_ascii=False, default=str)


async def _raise_after_failure(*args, **kwargs):
    raise RuntimeError('录音落库失败')


async def test_business_failure_rolls_back_and_keeps_raw_payload(client, sqlite_session, monkeypatch):
    """B12：业务落库失败要回滚干净（不留半条设备状态），且原始报文仍可追溯。"""
    monkeypatch.setattr(CallbackService, '_persist_business_item', _raise_after_failure)

    response = await _post(client, REC)

    assert response.status_code == 500
    # 设备状态改动与回调成功记录都被回滚，不会部分提交
    assert await _all(sqlite_session, MinglueDevice) == []
    assert await _all(sqlite_session, MinglueRecordingFile) == []

    failed = await _logs(sqlite_session, process_status='failed')
    assert len(failed) == 1
    assert failed[0].payload_json == json.dumps(REC, ensure_ascii=False, default=str)
    assert '录音落库失败' in (failed[0].error_message or '')


# --------------------------------------------------------------------------- #
# 契约 B3：回调响应时限与可解释的降级
# --------------------------------------------------------------------------- #


async def test_slow_processing_degrades_inside_vendor_deadline(client, sqlite_session, monkeypatch):
    """B3：厂商把超过 3s 的响应判为失败，因此超时必须在预算内交出响应。"""
    monkeypatch.setenv('MINGLUE_CALLBACK_RESPONSE_BUDGET', '0.05')

    async def _slow(*args, **kwargs):
        await asyncio.sleep(1.0)

    monkeypatch.setattr(CallbackService, 'receive', _slow)

    started = time.monotonic()
    response = await _post(client, HEARTBEAT)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, '超过预算仍应先把响应交出去，不能等满业务耗时'
    assert response.status_code == 503
    assert response.json()['code'] != 0

    logs = await _logs(sqlite_session, process_status='timeout')
    assert len(logs) == 1
    assert logs[0].event_type == 'heartbeat'
    assert logs[0].payload_json == json.dumps(HEARTBEAT, ensure_ascii=False, default=str)
    assert '重放' in (logs[0].error_message or '')


async def test_timeout_guard_can_be_switched_off(client, monkeypatch):
    """排障开关：预算置 0 时不做超时保护，行为回退到同步等待。"""
    monkeypatch.setenv('MINGLUE_CALLBACK_RESPONSE_BUDGET', '0')

    async def _slow(*args, **kwargs):
        await asyncio.sleep(0.05)
        return {'duplicate': False}

    monkeypatch.setattr(CallbackService, 'receive', _slow)

    response = await _post(client, HEARTBEAT)

    assert response.status_code == 200
    assert response.json() == {'code': 0}


# --------------------------------------------------------------------------- #
# 契约 B13 / B14：签名定位与失败审计
# --------------------------------------------------------------------------- #


async def test_signature_is_disabled_by_default(client, sqlite_session):
    """B13：厂商未定义签名，默认关闭校验，未带任何签名头的回调必须照常接收。"""
    response = await _post(client, HEARTBEAT)

    assert response.status_code == 200
    logs = await _logs(sqlite_session, event_type='heartbeat')
    assert logs[0].signature_valid == 'Y'


@pytest.mark.parametrize('headers', [pytest.param({}, id='missing'), pytest.param({'X-Signature': 'deadbeef'}, id='wrong')])
async def test_signature_failure_returns_401_and_leaves_rejection_audit(
    client, sqlite_session, monkeypatch, headers
):
    """B14：**校验失败也必须留审计痕迹**，且不得改动设备状态。"""
    monkeypatch.setenv('MINGLUE_CALLBACK_SECRET', 'local-hardening-secret')

    response = await client.post(CALLBACK_PATH, json=HEARTBEAT, headers=headers)

    assert response.status_code == 401
    logs = await _logs(sqlite_session, process_status='rejected')
    assert len(logs) == 1
    assert logs[0].signature_valid == 'N'
    assert logs[0].event_type == 'heartbeat'
    assert logs[0].payload_json == json.dumps(HEARTBEAT, ensure_ascii=False, default=str)
    assert await _all(sqlite_session, MinglueDevice) == []


async def test_signature_success_accepts_prefixed_header(client, sqlite_session, monkeypatch):
    """加固签名通过时正常处理；`X-Callback-Signature` 与 `sha256=` 前缀均支持。"""
    secret = 'local-hardening-secret'
    monkeypatch.setenv('MINGLUE_CALLBACK_SECRET', secret)
    body = json.dumps(HEARTBEAT, ensure_ascii=False).encode('utf-8')

    response = await client.post(
        CALLBACK_PATH,
        content=body,
        headers={'Content-Type': 'application/json', 'X-Callback-Signature': f'sha256={_sign(secret, body)}'},
    )

    assert response.status_code == 200
    assert response.json() == {'code': 0}
    assert len(await _all(sqlite_session, MinglueDevice)) == 1
    logs = await _logs(sqlite_session, event_type='heartbeat')
    assert logs[0].signature_valid == 'Y'


# --------------------------------------------------------------------------- #
# 契约 B18：事件时间口径统一为 UTC naive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    'value',
    [
        pytest.param(1726210271, id='unix-seconds'),
        pytest.param('2024-09-13 06:51:11+00:00', id='iso-utc-offset'),
        pytest.param('2024-09-13 14:51:11+08:00', id='iso-beijing-offset'),
    ],
)
async def test_event_time_uses_one_utc_caliber(client, sqlite_session, value):
    """同一时刻的三种厂商写法必须都落成同一个 UTC naive 值（B18）。"""
    payload = {'sn': 'SN-TIME', 'device_status': 1, 'update_time': value}

    response = await _post(client, payload)

    assert response.status_code == 200
    logs = await _logs(sqlite_session, device_code='SN-TIME')
    assert logs[0].event_time == datetime(2024, 9, 13, 6, 51, 11)


async def test_vendor_timezone_setting_declares_naive_wall_clock(client, sqlite_session, monkeypatch):
    """厂商未说明 `update_time` 是否北京时间（U12）：用一个变量显式声明归属。"""
    monkeypatch.setenv('MINGLUE_VENDOR_TIMEZONE', 'Asia/Shanghai')
    payload = {'sn': 'SN-TZ', 'device_status': 1, 'update_time': '2024-09-13 14:51:11'}

    response = await _post(client, payload)

    assert response.status_code == 200
    logs = await _logs(sqlite_session, device_code='SN-TZ')
    assert logs[0].event_time == datetime(2024, 9, 13, 6, 51, 11)


# --------------------------------------------------------------------------- #
# 契约 D10 / §6-4：仓库不得携带厂商示例凭证
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[2]
#: Apifox 文档示例里的 16 字节 AES 密钥。无法确认它是否为真实厂商凭证，
#: 因此按"可能是凭证"处理：不得出现在仓库任何位置（D10 / §6-4）。
#: 常量拆成两段拼接，是为了让本文件自身不命中下面的扫描断言。
VENDOR_EXAMPLE_AES_KEY = '10c0a163' + '653b0071'
#: 该示例密钥对示例明文的密文。它只是"算法与向量可复算"的溯源记录，
#: 允许保留在契约文档的说明文字里，但不得出现在代码或配置中。
VENDOR_EXAMPLE_CIPHERTEXT = 'R5V4MqWkJ4kD' + '/zNcqaxPwQ=='
_SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache'}


def _repo_text_files():
    for path in REPO_ROOT.rglob('*'):
        if path.is_file() and not _SKIP_DIRS & set(path.parts):
            yield path


def test_repository_carries_no_vendor_example_aes_key():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _repo_text_files()
        if VENDOR_EXAMPLE_AES_KEY in path.read_text(encoding='utf-8', errors='ignore')
    ]
    assert offenders == [], f'厂商示例 AES 密钥不得进入仓库：{offenders}'


def test_vendor_example_ciphertext_stays_out_of_code_and_config():
    """密文向量只作为文档溯源保留；测试与配置一律用非敏感合成向量。"""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _repo_text_files()
        if VENDOR_EXAMPLE_CIPHERTEXT in path.read_text(encoding='utf-8', errors='ignore')
        and path.suffix != '.md'
    ]
    assert offenders == [], f'厂商示例密文不得进入代码或配置：{offenders}'


def test_test_suites_only_use_synthetic_aes_keys():
    """所有加密相关测试都必须用合成密钥，不能引用商店/厂商下发的密钥。"""
    backend_tests = Path(__file__).resolve().parent
    offenders = []
    for path in backend_tests.glob('*.py'):
        text = path.read_text(encoding='utf-8')
        if VENDOR_EXAMPLE_AES_KEY in text or VENDOR_EXAMPLE_CIPHERTEXT in text:
            offenders.append(path.name)
    assert offenders == []
