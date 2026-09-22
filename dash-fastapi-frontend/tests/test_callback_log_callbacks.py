"""回调日志回调测试：筛选、详情展示、脱敏与删除确认。"""

import pytest
from dash.exceptions import PreventUpdate

from callbacks.device_c import callback_log_c
from config.exception import ServiceException


CALLBACK_ROW = {
    'callback_id': 33,
    'event_type': 'heartbeat',
    'device_code': 'MLR432KP42C19568',
    'event_id': None,
    'event_time': '2025-11-01T19:37:23',
    'received_at': '2025-11-01T19:37:24',
    'process_status': 'success',
    'duplicate_flag': 'N',
    'request_ip': '10.0.0.9',
    'session_id': 61318,
    'topic_name': 'hermes',
    'item_count': 1,
    'dedup_key': 'b' * 64,
    'signature_valid': 'Y',
    'processed_at': '2025-11-01T19:37:24',
    'error_message': None,
    'payload_json': (
        '{"sn": "MLR432KP42C19568", "device_status": 1,'
        ' "access_token": "super-secret-token",'
        ' "download_url": "https://cdn/x.opus?sign=SIGNED-VALUE"}'
    ),
}


@pytest.fixture
def callback_api(monkeypatch):
    calls = {}

    class FakeApi:
        @staticmethod
        def list_callbacks(query):
            calls['list_callbacks'] = query
            return {
                'rows': [dict(CALLBACK_ROW)],
                'page_num': query.get('page_num', 1),
                'page_size': query.get('page_size', 10),
                'total': 1,
            }

        @staticmethod
        def get_callback(callback_id):
            calls['get_callback'] = callback_id
            return {'data': dict(CALLBACK_ROW)}

        @staticmethod
        def delete_callbacks(ids):
            calls['delete_callbacks'] = ids
            return {'code': 200}

    monkeypatch.setattr(callback_log_c, 'DeviceApi', FakeApi)
    return calls


def test_list_filters_cover_device_type_time_and_result(
    session_context, callback_api
):
    """契约 E7：按设备、类型、时间、处理结果、重复标记查询。"""
    rows, pagination = callback_log_c.generate_callback_table(
        {
            'device_code': 'MLR',
            'event_type': 'heartbeat',
            'process_status': 'failed',
            'duplicate_flag': 'Y',
            'begin_time': '2025-11-01',
            'end_time': '2025-11-02',
            'page_num': 1,
            'page_size': 10,
        }
    )
    query = callback_api['list_callbacks']
    assert query['device_code'] == 'MLR'
    assert query['event_type'] == 'heartbeat'
    assert query['process_status'] == 'failed'
    assert query['duplicate_flag'] == 'Y'
    assert query['begin_time'] == '2025-11-01'
    assert rows[0]['process_status_display'] == '成功'
    assert rows[0]['duplicate_display'] == '首次'
    assert rows[0]['operation'][0]['content'] == '详情'
    assert pagination['total'] == 1


def test_refresh_callback_table_builds_query_from_form(
    session_context, callback_api, dash_ctx
):
    dash_ctx.triggered_id = 'callback-search'
    callback_log_c.refresh_callback_table(
        None,
        None,
        {'current': 1, 'pageSize': 10},
        None,
        'MLR',
        'rec',
        'failed',
        'Y',
        ['2025-11-01', '2025-11-02'],
    )
    query = callback_api['list_callbacks']
    assert query['event_type'] == 'rec'
    assert query['process_status'] == 'failed'
    assert query['begin_time'] == '2025-11-01'
    assert query['end_time'] == '2025-11-02'


def test_refresh_callback_table_ignores_partial_time_range(
    session_context, callback_api, dash_ctx
):
    dash_ctx.triggered_id = 'callback-refresh'
    callback_log_c.refresh_callback_table(
        None,
        None,
        {'current': 1, 'pageSize': 10},
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert 'begin_time' not in callback_api['list_callbacks']


def test_list_failure_is_reported(
    session_context, callback_api, monkeypatch, messages
):
    def boom(_query):
        raise ServiceException(message='该用户无此接口权限')

    monkeypatch.setattr(
        callback_log_c.DeviceApi, 'list_callbacks', staticmethod(boom)
    )
    rows, pagination = callback_log_c.generate_callback_table(
        {'page_num': 1, 'page_size': 10}
    )
    assert rows == [] and pagination['total'] == 0
    assert messages()[-1]['type'] == 'error'
    assert '无此操作权限' in messages()[-1]['content']


def test_detail_shows_idempotency_info_and_masks_secrets(
    session_context, callback_api
):
    """契约 E8/E9：展示幂等与处理信息，且不泄露 token / 带签名地址。"""
    visible, event_items, process_items, payload = (
        callback_log_c.show_callback_detail(1, '详情', {'key': '33'})
    )
    assert visible is True
    event_values = {item['label']: item['children'] for item in event_items}
    process_values = {item['label']: item['children'] for item in process_items}
    assert event_values['事件类型'] == 'heartbeat'
    assert event_values['接收时间'] == '2025-11-01 19:37:24'
    assert process_values['幂等键'] == 'b' * 64
    assert process_values['是否重复'] == '首次'
    assert process_values['签名校验'] == '通过'
    assert process_values['回调配置 ID'] == '61318'
    assert 'super-secret-token' not in payload
    assert 'SIGNED-VALUE' not in payload
    assert 'MLR432KP42C19568' in payload


def test_detail_requires_the_detail_button(session_context, callback_api):
    with pytest.raises(PreventUpdate):
        callback_log_c.show_callback_detail(1, '删除', {'key': '33'})


def test_detail_failure_is_reported(
    session_context, callback_api, monkeypatch, messages
):
    def boom(_callback_id):
        raise ServiceException(message='回调记录不存在')

    monkeypatch.setattr(
        callback_log_c.DeviceApi, 'get_callback', staticmethod(boom)
    )
    with pytest.raises(PreventUpdate):
        callback_log_c.show_callback_detail(1, '详情', {'key': '33'})
    assert '回调记录不存在' in messages()[-1]['content']


def test_delete_is_confirmed_before_calling_backend(
    session_context, callback_api, messages
):
    visible, ids = callback_log_c.show_delete_modal(1, ['33', '34'])
    assert visible is True
    assert ids == '33,34'
    assert 'delete_callbacks' not in callback_api

    result = callback_log_c.delete_callbacks(1, ids)
    assert result['ok'] is True
    assert callback_api['delete_callbacks'] == '33,34'
    assert messages()[-1]['content'] == '删除成功'


def test_delete_cancelled_does_nothing(session_context, callback_api):
    with pytest.raises(PreventUpdate):
        callback_log_c.delete_callbacks(0, '33')
    assert 'delete_callbacks' not in callback_api


def test_delete_failure_is_reported(
    session_context, callback_api, monkeypatch, messages
):
    def boom(_ids):
        raise ServiceException(message='该用户无此接口权限')

    monkeypatch.setattr(
        callback_log_c.DeviceApi, 'delete_callbacks', staticmethod(boom)
    )
    result = callback_log_c.delete_callbacks(1, '33')
    assert result['ok'] is False
    assert '无此操作权限' in messages()[-1]['content']
