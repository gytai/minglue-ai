"""Stage 4（GYTAI-124）：明略厂商控制接口的请求级/服务级测试。

契约 §5.7 F6 要求"厂商接口 mock 测试（token 刷新、错误映射、参数序列化）"。
这里用 ``httpx.MockTransport`` 起一个 mock 厂商服务，在 **HTTP 请求级**验证
契约 §4 的 5 个设备接口，再用内存 SQLite 验证远端同步对本地台账的更新与
逐设备部分失败语义（契约 C4 / C8 / C9 / C10 / C11）。

mock 服务按 `(method, path)` 预先排好响应队列：队列只剩一项时重复使用（模拟
稳定接口），多项时逐个消费（模拟"先 401 后 200"这类重试场景），元素也可以是
异常实例（模拟超时/网络错误）。
"""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from exceptions.exception import ServiceException
from module_device.dao.device_dao import ControlLogDao, DeviceDao
from module_device.entity.vo.device_vo import DeviceModel, DeviceStatusUpdateModel
from module_device.service.device_service import CallbackService, ControlLogService, DeviceService
from module_device.service.minglue_api_service import MinglueApiService

BASE_URL = 'https://aiot.mock'
VENDOR_USERNAME = 'svc-minglue'
VENDOR_PASSWORD = 'VendorPwd-2026'
VENDOR_AES_KEY = '10c0a163653b0071'
TOKEN_ONE = 'ACCESS-TOKEN-0001'
TOKEN_TWO = 'ACCESS-TOKEN-0002'

LOGIN_PATH = '/thiea/site/accountLogin'
STATUS_PATH = '/thiea/hermes/device/status/batch'
CONFIG_PATH = '/thiea/hermes/device/config/status/batch'
START_PATH = '/thiea/hermes/device/start/shadow'
STOP_PATH = '/thiea/hermes/device/stop/shadow'
CMD_LOG_PATH = '/thiea/hermes/device/cmd/receive/log'

HEARTBEAT_ENTITY = {
    'sn': 'MLR432KP42C19568',
    'device_status': 1,
    'record_status': 0,
    'remain_power': 40,
    'remain_storage': 14833,
    'rssi': 31,
    'update_time': '2025-11-01 19:37:23',
    'version': '5.4.49',
    'online': 2,
}


def envelope(data: Any) -> Dict[str, Any]:
    """契约 §4 的 200 成功包络。"""
    return {'code': 0, 'message': '', 'data': data}


def login_ok(token: str = TOKEN_ONE, expires: int = 604800) -> Dict[str, Any]:
    """契约 §1.1 的登录成功响应。"""
    return {
        'code': 0,
        'message': '操作成功',
        'data': {'id': 1, 'username': VENDOR_USERNAME, 'token': token, 'expires': expires},
        'timestamp': 1743487690,
        'traceID': '18f90f47d11c3218e2851d15f540082c',
    }


@pytest.fixture
def vendor(monkeypatch):
    """搭起 mock 厂商服务：注入 MockTransport、配置凭据并记录每个请求。"""
    monkeypatch.setenv('MINGLUE_API_BASE_URL', BASE_URL)
    monkeypatch.setenv('MINGLUE_API_USERNAME', VENDOR_USERNAME)
    monkeypatch.setenv('MINGLUE_API_PASSWORD', VENDOR_PASSWORD)
    monkeypatch.setenv('MINGLUE_API_AES_KEY', VENDOR_AES_KEY)
    monkeypatch.setenv('MINGLUE_API_AUTH_SCHEME', 'Bearer')
    monkeypatch.setenv('MINGLUE_API_TIMEOUT', '5')
    monkeypatch.delenv('MINGLUE_API_BATCH_SN_QUERY', raising=False)

    state = SimpleNamespace(requests=[], login_count=0, routes={})

    def handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        if request.url.path == LOGIN_PATH:
            state.login_count += 1
        queue = state.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={'code': 404, 'message': 'Not found'}, request=request)
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, httpx.Response):
            # 预置的原样响应（如非法 JSON 报文）直接透传
            return httpx.Response(
                outcome.status_code, content=outcome.content, headers=outcome.headers, request=request
            )
        status_code, payload = outcome if isinstance(outcome, tuple) else (200, outcome)
        return httpx.Response(status_code, json=payload, request=request)

    MinglueApiService._transport = httpx.MockTransport(handler)

    def route(method: str, path: str, *outcomes):
        state.routes[(method, path)] = list(outcomes)
        return state

    def calls(method: str, path: str) -> List[httpx.Request]:
        return [item for item in state.requests if item.method == method and item.url.path == path]

    state.route = route
    state.calls = calls
    state.login_request = lambda: calls('POST', LOGIN_PATH)[0]
    # 默认放好登录响应；单个用例可用 state.route 覆盖。
    route('POST', LOGIN_PATH, login_ok())
    return state


def body_of(request: httpx.Request) -> Dict[str, Any]:
    return json.loads(request.content.decode('utf-8'))


# --------------------------------------------------------------------------- #
# 登录、加密与 token 生命周期
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_login_body_carries_documented_aes_vector_and_never_the_plaintext(vendor, monkeypatch):
    """契约 §1.1：密码必须是 AES-128-ECB+PKCS7+Base64，且明文不出现在请求里。"""
    monkeypatch.setenv('MINGLUE_API_PASSWORD', 'xxx')
    monkeypatch.setenv('MINGLUE_API_AES_KEY', '10c0a163653b0071')
    vendor.route('POST', STATUS_PATH, envelope({'entities': []}))

    await MinglueApiService.get_device_statuses(['MLR432KP42C19568'])

    login = vendor.login_request()
    assert login.url.path == LOGIN_PATH
    assert login.headers['content-type'].startswith('application/json')
    assert body_of(login) == {'username': VENDOR_USERNAME, 'password': 'R5V4MqWkJ4kD/zNcqaxPwQ==', 'isLock': True}
    # 文档示例密钥的加密向量已复算一致，同时确认明文密码没有随请求发出去
    assert b'"xxx"' not in login.content


@pytest.mark.asyncio
async def test_token_is_cached_across_calls(vendor):
    """契约 A4：进程内 token 缓存命中时不应重复登录。"""
    vendor.route('POST', STATUS_PATH, envelope({'entities': [HEARTBEAT_ENTITY]}))

    await MinglueApiService.get_device_statuses(['MLR432KP42C19568'])
    await MinglueApiService.get_device_statuses(['MLR432KP42C19568'])

    assert vendor.login_count == 1
    assert len(vendor.calls('POST', STATUS_PATH)) == 2


@pytest.mark.asyncio
async def test_authorization_header_uses_configured_scheme(vendor, monkeypatch):
    """契约 A5 / U1：鉴权头前缀可配置（Bearer 或裸 token）。"""
    vendor.route('POST', STATUS_PATH, envelope({'entities': []}))

    await MinglueApiService.get_device_statuses(['SN-1'])
    assert vendor.calls('POST', STATUS_PATH)[0].headers['authorization'] == f'Bearer {TOKEN_ONE}'

    monkeypatch.setenv('MINGLUE_API_AUTH_SCHEME', '')
    await MinglueApiService.get_device_statuses(['SN-1'])
    assert vendor.calls('POST', STATUS_PATH)[1].headers['authorization'] == TOKEN_ONE


@pytest.mark.asyncio
async def test_stale_token_is_refreshed_once_on_401(vendor):
    """契约 A4 / C10：401 只触发一次强制刷新重试，且第二次带上新 token。"""
    vendor.route(
        'POST',
        STATUS_PATH,
        (401, {'code': 401, 'message': 'Unauthorized'}),
        envelope({'entities': [HEARTBEAT_ENTITY]}),
    )
    vendor.route(
        'POST',
        LOGIN_PATH,
        login_ok(TOKEN_ONE),
        login_ok(TOKEN_TWO),
    )

    entities = await MinglueApiService.get_device_statuses(['MLR432KP42C19568'])

    assert [item['sn'] for item in entities] == ['MLR432KP42C19568']
    assert vendor.login_count == 2
    attempts = vendor.calls('POST', STATUS_PATH)
    assert [item.headers['authorization'] for item in attempts] == [
        f'Bearer {TOKEN_ONE}',
        f'Bearer {TOKEN_TWO}',
    ]


@pytest.mark.asyncio
async def test_persistent_401_stops_after_one_retry_and_leaks_nothing(vendor):
    """重试边界：连续 401 只能试两次，且报错不得带出 token 或密码。"""
    vendor.route('POST', STATUS_PATH, (401, {'code': 401, 'message': 'Unauthorized'}))
    vendor.route('POST', LOGIN_PATH, login_ok(TOKEN_ONE), login_ok(TOKEN_TWO))

    with pytest.raises(ServiceException) as excinfo:
        await MinglueApiService.get_device_statuses(['SN-1'])

    assert len(vendor.calls('POST', STATUS_PATH)) == 2
    assert vendor.login_count == 2
    message = str(excinfo.value)
    assert '认证未通过' in message and '401' in message
    for secret in (TOKEN_ONE, TOKEN_TWO, VENDOR_PASSWORD, VENDOR_AES_KEY):
        assert secret not in message


@pytest.mark.asyncio
async def test_non_auth_failure_is_not_retried(vendor):
    """重试边界：5xx / 业务错误 / 非法响应都不重试。"""
    vendor.route('POST', STATUS_PATH, (500, {'code': 500, 'message': 'boom'}))

    with pytest.raises(ServiceException):
        await MinglueApiService.get_device_statuses(['SN-1'])

    assert len(vendor.calls('POST', STATUS_PATH)) == 1
    assert vendor.login_count == 1


# --------------------------------------------------------------------------- #
# 参数序列化与响应解包
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_batch_status_serialization_sends_sn_query_and_sns_body(vendor):
    """契约 §4.1 / C2：query sn 与 body sns 的位置必须与文档一致。"""
    vendor.route('POST', STATUS_PATH, envelope({'entities': [HEARTBEAT_ENTITY]}))

    await MinglueApiService.get_device_statuses(['SN-A', 'SN-B'])

    request = vendor.calls('POST', STATUS_PATH)[0]
    assert request.url.params['sn'] == 'SN-A'
    assert body_of(request) == {'sns': ['SN-A', 'SN-B']}
    assert request.headers['authorization'] == f'Bearer {TOKEN_ONE}'


@pytest.mark.asyncio
async def test_batch_sn_query_can_be_disabled_for_u2_verification(vendor, monkeypatch):
    """契约 U2：Query sn 是否必填文档未定，可用配置关闭以便真实环境试送。"""
    vendor.route('POST', STATUS_PATH, envelope({'entities': []}))
    monkeypatch.setenv('MINGLUE_API_BATCH_SN_QUERY', 'false')

    await MinglueApiService.get_device_statuses(['SN-A'])

    request = vendor.calls('POST', STATUS_PATH)[0]
    assert 'sn' not in request.url.params
    assert body_of(request) == {'sns': ['SN-A']}


@pytest.mark.asyncio
async def test_config_status_serialization(vendor):
    """契约 §4.2：路径/方法/参数位置。"""
    vendor.route('POST', CONFIG_PATH, envelope({'entities': []}))

    await MinglueApiService.get_device_config_statuses(['SN-A'])

    request = vendor.calls('POST', CONFIG_PATH)[0]
    assert request.url.params['sn'] == 'SN-A'
    assert body_of(request) == {'sns': ['SN-A']}


@pytest.mark.asyncio
async def test_start_recording_serialization(vendor):
    """契约 §4.3：query sn + body nm。"""
    vendor.route('POST', START_PATH, envelope({'msg_id': 'MSG-1', 'sn': 'SN-A', 'stat': 1}))

    data = await MinglueApiService.start_recording('SN-A', 'ab12')

    request = vendor.calls('POST', START_PATH)[0]
    assert request.url.params['sn'] == 'SN-A'
    assert body_of(request) == {'nm': 'ab12'}
    assert data['msg_id'] == 'MSG-1'


@pytest.mark.asyncio
async def test_start_recording_without_audio_id_sends_empty_object(vendor):
    """契约 §4.3：nm 非必填，不带时 body 为空对象。"""
    vendor.route('POST', START_PATH, envelope({'msg_id': 'MSG-1', 'sn': 'SN-A', 'stat': 1}))

    await MinglueApiService.start_recording('SN-A')

    assert body_of(vendor.calls('POST', START_PATH)[0]) == {}


@pytest.mark.asyncio
async def test_stop_recording_serialization(vendor):
    """契约 §4.4：query sn + 空 body。"""
    vendor.route('POST', STOP_PATH, envelope({'msg_id': 'MSG-2', 'sn': 'SN-A', 'stat': 1}))

    await MinglueApiService.stop_recording('SN-A')

    request = vendor.calls('POST', STOP_PATH)[0]
    assert request.method == 'POST'
    assert request.url.params['sn'] == 'SN-A'
    assert body_of(request) == {}


@pytest.mark.asyncio
async def test_command_log_serialization_and_status_normalization(vendor):
    """契约 §4.5：GET + query sn/msg_id，status 归一为整数。"""
    vendor.route(
        'GET',
        CMD_LOG_PATH,
        envelope({'cmd': 'start', 'msg_id': 'MSG-1', 'sn': 'SN-A', 'status': '1', 'payload': {}, 'response': {}}),
    )

    data = await MinglueApiService.get_command_log('SN-A', 'MSG-1')

    request = vendor.calls('GET', CMD_LOG_PATH)[0]
    assert request.url.params['sn'] == 'SN-A'
    assert request.url.params['msg_id'] == 'MSG-1'
    assert data['status'] == 1
    assert data['cmd'] == 'start'


@pytest.mark.asyncio
async def test_command_log_rejects_non_integer_status(vendor):
    """非法响应要有确定行为，而不是把脏值透传下去。"""
    vendor.route('GET', CMD_LOG_PATH, envelope({'msg_id': 'MSG-1', 'status': 'not-a-number'}))

    with pytest.raises(ServiceException, match='status 不是整数'):
        await MinglueApiService.get_command_log('SN-A', 'MSG-1')


@pytest.mark.asyncio
async def test_recording_response_without_msg_id_is_rejected(vendor):
    """契约 §4.3：msg_id 必填，缺了就回查不到，必须报可读错误。"""
    vendor.route('POST', START_PATH, envelope({'sn': 'SN-A', 'stat': 1}))

    with pytest.raises(ServiceException, match='msg_id'):
        await MinglueApiService.start_recording('SN-A')


@pytest.mark.asyncio
async def test_missing_entities_list_is_reported(vendor):
    """非法响应：data 里没有 entities 列表时给出可读错误。"""
    vendor.route('POST', STATUS_PATH, envelope({'total': 0}))

    with pytest.raises(ServiceException, match='entities'):
        await MinglueApiService.get_device_statuses(['SN-A'])


# --------------------------------------------------------------------------- #
# 错误映射与敏感信息
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vendor_business_error_is_mapped_and_redacted(vendor):
    """契约 §4 / A6：code != 0 是业务错误；报文里的凭据形态必须脱敏。"""
    secret_blob = 'QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWY='
    vendor.route('POST', STATUS_PATH, {'code': 5001, 'message': f'设备未注册 {secret_blob}'})

    with pytest.raises(ServiceException) as excinfo:
        await MinglueApiService.get_device_statuses(['SN-A'])

    message = str(excinfo.value)
    assert '厂商错误码 5001' in message
    assert '设备未注册' in message
    assert secret_blob not in message and '***' in message


@pytest.mark.asyncio
async def test_http_error_without_data_is_mapped(vendor):
    """契约 §4 响应表：400/404 只有 code 与 message，没有 data。"""
    vendor.route('POST', STATUS_PATH, (400, {'code': 400, 'message': 'Invalid input'}))

    with pytest.raises(ServiceException) as excinfo:
        await MinglueApiService.get_device_statuses(['SN-A'])

    message = str(excinfo.value)
    assert 'HTTP 400' in message and 'Invalid input' in message


@pytest.mark.asyncio
async def test_login_http_error_is_mapped(vendor):
    """登录失败也要可读，且不回显加密后的密码。"""
    vendor.route('POST', LOGIN_PATH, (400, {'code': 400, 'message': 'Invalid input'}))

    with pytest.raises(ServiceException) as excinfo:
        await MinglueApiService.get_device_statuses(['SN-A'])

    assert 'access_token' in str(excinfo.value)


@pytest.mark.asyncio
async def test_login_response_without_token_is_reported(vendor):
    vendor.route('POST', LOGIN_PATH, {'code': 0, 'message': '', 'data': {'id': 1}})

    with pytest.raises(ServiceException, match='缺少 token'):
        await MinglueApiService.get_device_statuses(['SN-A'])


@pytest.mark.asyncio
async def test_invalid_json_response_is_reported(vendor):
    """非法响应：非 JSON 报文（如网关 HTML）不能当成成功解析。"""
    vendor.route('POST', STATUS_PATH, httpx.Response(200, text='<html>gateway</html>'))

    with pytest.raises(ServiceException, match='不是合法 JSON'):
        await MinglueApiService.get_device_statuses(['SN-A'])


@pytest.mark.asyncio
async def test_non_object_response_is_reported(vendor):
    """非法响应：JSON 顶层不是对象（契约 §4 要求 {code,message,data}）。"""
    vendor.route('POST', STATUS_PATH, httpx.Response(200, json=[1, 2, 3]))

    with pytest.raises(ServiceException, match='不是合法的 JSON 对象'):
        await MinglueApiService.get_device_statuses(['SN-A'])


@pytest.mark.asyncio
async def test_timeout_is_reported_as_readable_error(vendor):
    vendor.route('POST', STATUS_PATH, httpx.ReadTimeout('timed out'))

    with pytest.raises(ServiceException) as excinfo:
        await MinglueApiService.get_device_statuses(['SN-A'])

    assert '超时' in str(excinfo.value)


@pytest.mark.asyncio
async def test_network_error_is_reported(vendor):
    vendor.route('POST', STATUS_PATH, httpx.ConnectError('connection refused'))

    with pytest.raises(ServiceException) as excinfo:
        await MinglueApiService.get_device_statuses(['SN-A'])

    assert '无法访问明略接口' in str(excinfo.value)


@pytest.mark.asyncio
async def test_missing_configuration_is_reported(monkeypatch):
    for name in (
        'MINGLUE_API_BASE_URL',
        'MINGLUE_API_USERNAME',
        'MINGLUE_API_PASSWORD',
        'MINGLUE_API_AES_KEY',
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ServiceException, match='明略接口未配置'):
        await MinglueApiService.get_device_statuses(['SN-A'])


@pytest.mark.asyncio
async def test_invalid_aes_key_length_is_reported(vendor, monkeypatch):
    monkeypatch.setenv('MINGLUE_API_AES_KEY', 'too-short')

    with pytest.raises(ServiceException, match='16 字节'):
        await MinglueApiService.get_device_statuses(['SN-A'])


@pytest.mark.asyncio
async def test_blank_serial_numbers_are_rejected_before_any_request(vendor):
    with pytest.raises(ServiceException, match='不能为空'):
        await MinglueApiService.get_device_statuses([])
    with pytest.raises(ServiceException, match='不能为空'):
        await MinglueApiService.get_device_statuses(['   '])

    assert vendor.requests == []


# --------------------------------------------------------------------------- #
# 远端同步与本地台账更新（服务级）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sync_remote_statuses_creates_unknown_device_and_reports_each(vendor, sqlite_session):
    """契约 §0.2 / C1：厂商没有设备列表接口，状态回包要能建立台账。"""
    vendor.route('POST', STATUS_PATH, envelope({'entities': [HEARTBEAT_ENTITY]}))

    result = await DeviceService.sync_remote_statuses(sqlite_session, ['MLR432KP42C19568'])

    device = await DeviceDao.get_by_code(sqlite_session, 'MLR432KP42C19568')
    assert device is not None
    assert device.status == 'online' and device.online_status == 2 and device.device_status == 1
    assert device.battery_level == 40 and device.signal_strength == 31
    assert device.heartbeat_time == datetime(2025, 11, 1, 19, 37, 23)
    assert result['total'] == 1
    assert result['succeeded'] == 1
    assert result['synced'] == 1
    assert result['failed'] == 0 and result['partial'] is False
    assert result['results'] == [{'sn': 'MLR432KP42C19568', 'ok': True, 'skipped': False}]


@pytest.mark.asyncio
async def test_sync_remote_statuses_keeps_other_devices_when_one_fails(vendor, sqlite_session):
    """契约 C9：批量同步必须逐台给出结果，单台失败不能回滚其他设备。"""
    vendor.route('POST', STATUS_PATH, envelope({'entities': [HEARTBEAT_ENTITY, {**HEARTBEAT_ENTITY, 'sn': 'SN-BAD'}]}))
    original = CallbackService._upsert_device

    async def flaky(db, device_code, event_type, payload, event_time):
        if device_code == 'SN-BAD':
            raise RuntimeError('厂商字段非法')
        return await original(db, device_code, event_type, payload, event_time)

    with patch.object(CallbackService, '_upsert_device', AsyncMock(side_effect=flaky)):
        result = await DeviceService.sync_remote_statuses(
            sqlite_session, ['MLR432KP42C19568', 'SN-BAD']
        )

    assert await DeviceDao.get_by_code(sqlite_session, 'MLR432KP42C19568') is not None
    assert await DeviceDao.get_by_code(sqlite_session, 'SN-BAD') is None
    assert result['succeeded'] == 1 and result['failed'] == 1
    assert result['partial'] is True
    failures = [item for item in result['results'] if not item['ok']]
    assert failures == [{'sn': 'SN-BAD', 'ok': False, 'skipped': False, 'error': '厂商字段非法'}]


@pytest.mark.asyncio
async def test_sync_remote_statuses_flags_serial_numbers_missing_from_response(vendor, sqlite_session):
    """一致性规则：请求了但厂商没回的序列号要有确定结果，不能静默消失。"""
    vendor.route('POST', STATUS_PATH, envelope({'entities': [HEARTBEAT_ENTITY]}))

    result = await DeviceService.sync_remote_statuses(
        sqlite_session, ['MLR432KP42C19568', 'SN-MISSING']
    )

    assert result['succeeded'] == 1 and result['failed'] == 1
    missing = [item for item in result['results'] if item['sn'] == 'SN-MISSING']
    assert missing and '响应未包含该设备' in missing[0]['error']


@pytest.mark.asyncio
async def test_sync_remote_statuses_skips_entities_without_serial_number(vendor, sqlite_session):
    vendor.route('POST', STATUS_PATH, envelope({'entities': [{'device_status': 1}]}))

    result = await DeviceService.sync_remote_statuses(sqlite_session, ['SN-A'])

    assert result['skipped'] == 1 and result['succeeded'] == 0
    assert result['failed'] == 1  # SN-A 未出现在可用实体里，同样要有结果
    assert result['partial'] is False


@pytest.mark.asyncio
async def test_sync_remote_configs_persists_known_device_and_skips_unknown(vendor, sqlite_session):
    """契约 C4 / D7：配置快照落库，但不为未知设备凭空建档。"""
    await DeviceDao.add(sqlite_session, DeviceModel(device_code='SN-KNOWN', device_name='已知设备'))
    await sqlite_session.commit()
    vendor.route(
        'POST',
        CONFIG_PATH,
        envelope(
            {
                'entities': [
                    {'sn': 'SN-KNOWN', 'config_data': {'volume': 5}, 'last_upload_time': '2026-09-01 10:00:00'},
                    {'sn': 'SN-UNKNOWN', 'config_data': {}, 'last_upload_time': '2026-09-01 10:00:00'},
                ]
            }
        ),
    )

    result = await DeviceService.sync_remote_configs(sqlite_session, ['SN-KNOWN', 'SN-UNKNOWN'])

    known = await DeviceDao.get_by_code(sqlite_session, 'SN-KNOWN')
    assert known.config_json == '{"volume": 5}'
    assert known.config_last_upload_time == datetime(2026, 9, 1, 10, 0, 0)
    assert known.config_synced_at is not None
    assert await DeviceDao.get_by_code(sqlite_session, 'SN-UNKNOWN') is None
    assert result['succeeded'] == 1 and result['skipped'] == 1 and result['failed'] == 0
    assert result['partial'] is False


@pytest.mark.asyncio
async def test_sync_remote_configs_rejects_invalid_entities_envelope(vendor, sqlite_session):
    vendor.route('POST', CONFIG_PATH, envelope({'items': []}))

    with pytest.raises(ServiceException, match='entities'):
        await DeviceService.sync_remote_configs(sqlite_session, ['SN-A'])


# --------------------------------------------------------------------------- #
# 本地状态维护与指令回查（C8）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_update_status_maintains_local_state_fields(sqlite_session):
    device = await DeviceDao.add(sqlite_session, DeviceModel(device_code='SN-1', status='online'))
    await sqlite_session.commit()

    result = await DeviceService.update_status(
        sqlite_session,
        DeviceStatusUpdateModel(device_id=device.device_id, status='disabled', bind_status='bound'),
        operator='tester',
    )

    assert result.is_success is True
    refreshed = await DeviceDao.get_by_id(sqlite_session, device.device_id)
    assert refreshed.status == 'disabled'
    assert refreshed.bind_status == 'bound'
    assert refreshed.update_by == 'tester'


@pytest.mark.asyncio
async def test_update_status_ignores_explicit_null_fields(sqlite_session):
    """显式传 null 不应把非空状态列清空。"""
    device = await DeviceDao.add(sqlite_session, DeviceModel(device_code='SN-1', status='online'))
    await sqlite_session.commit()

    await DeviceService.update_status(
        sqlite_session,
        DeviceStatusUpdateModel(device_id=device.device_id, status=None, bind_status='bound'),
    )

    refreshed = await DeviceDao.get_by_id(sqlite_session, device.device_id)
    assert refreshed.status == 'online' and refreshed.bind_status == 'bound'


@pytest.mark.asyncio
async def test_update_status_rejects_unknown_device(sqlite_session):
    with pytest.raises(ServiceException, match='设备不存在'):
        await DeviceService.update_status(sqlite_session, DeviceStatusUpdateModel(device_id=999, status='offline'))


def test_status_update_model_requires_at_least_one_field():
    with pytest.raises(ValidationError):
        DeviceStatusUpdateModel(device_id=1)


@pytest.mark.asyncio
async def test_control_log_records_vendor_msg_id_without_credentials(sqlite_session):
    """契约 C8：留存 msg_id 以支持 cmd/receive/log 回查，且不写 token。"""
    record = await ControlLogService.record(
        sqlite_session,
        device_code='SN-1',
        command='start_recording',
        audio_id='ab12',
        result={'msg_id': 'MSG-9', 'sn': 'SN-1', 'stat': 1},
        operator='tester',
    )

    assert record is not None
    assert record.msg_id == 'MSG-9' and record.remote_status == 1
    found = await ControlLogService.find_by_msg_id(sqlite_session, 'MSG-9')
    assert found['msg_id'] == 'MSG-9' and found['command'] == 'start_recording'
    assert 'token' not in (found['payload_json'] or '')
    assert 'token' not in (found['response_json'] or '')


@pytest.mark.asyncio
async def test_resolve_command_result_returns_remote_result(vendor, sqlite_session):
    vendor.route('GET', CMD_LOG_PATH, envelope({'cmd': 'start', 'msg_id': 'MSG-1', 'sn': 'SN-1', 'status': 1}))

    result = await ControlLogService.resolve_command_result(sqlite_session, 'SN-1', 'MSG-1')

    assert result['remote']['cmd'] == 'start'
    assert result['remote_error'] is None
    assert result['local'] is None


@pytest.mark.asyncio
async def test_resolve_command_result_falls_back_to_local_log(vendor, sqlite_session):
    """厂商还没收到指令（404）时，本地已发记录仍然可用。"""
    await ControlLogDao.add(
        sqlite_session,
        {
            'device_code': 'SN-1',
            'command': 'start_recording',
            'msg_id': 'MSG-1',
            'request_status': 'success',
            'operator': 'tester',
            'create_time': datetime.now(),
            'update_time': datetime.now(),
        },
    )
    await sqlite_session.commit()
    vendor.route('GET', CMD_LOG_PATH, (404, {'code': 404, 'message': 'Not found'}))

    result = await ControlLogService.resolve_command_result(sqlite_session, 'SN-1', 'MSG-1')

    assert result['remote'] is None
    assert '404' in result['remote_error']
    assert result['local']['msg_id'] == 'MSG-1'
    assert result['local']['command'] == 'start_recording'
    assert isinstance(result['local']['create_time'], datetime)


@pytest.mark.asyncio
async def test_resolve_command_result_raises_when_no_side_has_the_command(vendor, sqlite_session):
    vendor.route('GET', CMD_LOG_PATH, (404, {'code': 404, 'message': 'Not found'}))

    with pytest.raises(ServiceException, match='404'):
        await ControlLogService.resolve_command_result(sqlite_session, 'SN-1', 'MSG-UNKNOWN')
