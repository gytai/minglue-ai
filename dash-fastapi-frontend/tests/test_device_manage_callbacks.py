"""设备管理回调测试：列表筛选、详情、控制操作、权限与反馈。"""

import pytest
from dash.exceptions import PreventUpdate

from callbacks.device_c import manage_c
from config.exception import ServiceException


DEVICE_ROW = {
    'device_id': 12,
    'device_code': 'MLR432KP42C19568',
    'device_name': '研发工牌',
    'status': 'online',
    'online_status': 2,
    'bind_status': 'bound',
    'battery_level': 40,
    'record_status': 0,
    'remain_storage': 100,
    'total_storage': 200,
    'signal_strength': 31,
    'last_seen_time': '2025-11-01T19:37:23',
}
FULL_PERMS = [
    'device:manage:query',
    'device:manage:add',
    'device:manage:edit',
    'device:manage:remove',
    'device:manage:control',
    'device:control:list',
]


@pytest.fixture
def device_api(monkeypatch):
    """记录调用参数的 DeviceApi 替身。"""
    calls = {}

    def page(rows, page_num=1, page_size=10, total=None):
        return {
            'rows': rows,
            'page_num': page_num,
            'page_size': page_size,
            'total': len(rows) if total is None else total,
        }

    class FakeApi:
        @staticmethod
        def list_devices(query):
            calls['list_devices'] = query
            return page([dict(DEVICE_ROW)], total=1)

        @staticmethod
        def get_device(device_id):
            calls['get_device'] = device_id
            return {'data': dict(DEVICE_ROW)}

        @staticmethod
        def add_device(payload):
            calls['add_device'] = payload
            return {'code': 200}

        @staticmethod
        def update_device(payload):
            calls['update_device'] = payload
            return {'code': 200}

        @staticmethod
        def delete_devices(ids):
            calls['delete_devices'] = ids
            return {'code': 200}

        @staticmethod
        def sync_status(sns):
            calls['sync_status'] = sns
            return {
                'data': {
                    'entities': [{'sn': sn} for sn in sns],
                    'synced': len(sns),
                }
            }

        @staticmethod
        def sync_config(sns):
            calls['sync_config'] = sns
            return {
                'data': {
                    'entities': [{'sn': sn} for sn in sns],
                    'synced': len(sns),
                }
            }

        @staticmethod
        def start_recording(device_code, audio_id=None):
            calls['start_recording'] = (device_code, audio_id)
            return {'data': {'msg_id': 'm-1', 'sn': device_code, 'stat': 1}}

        @staticmethod
        def stop_recording(device_code):
            calls['stop_recording'] = device_code
            return {'data': {'msg_id': 'm-2', 'sn': device_code, 'stat': 1}}

        @staticmethod
        def list_control_logs(query):
            calls['list_control_logs'] = query
            return page(
                [
                    {
                        'control_id': 5,
                        'device_code': query.get('device_code'),
                        'command': 'start_recording',
                        'msg_id': 'm-1',
                        'request_status': 'success',
                        'remote_status': 0,
                        'create_time': '2025-11-01T10:00:00',
                    }
                ]
            )

        @staticmethod
        def get_command_log(device_code, message_id):
            calls['get_command_log'] = (device_code, message_id)
            return {
                'data': {
                    'sn': device_code,
                    'cmd': 'start/shadow',
                    'msg_id': message_id,
                    'status': 1,
                    'req_time': '2025-11-01T10:00:00',
                    'resp_time': '2025-11-01T10:00:02',
                    'response': {'ok': True},
                }
            }

    monkeypatch.setattr(manage_c, 'DeviceApi', FakeApi)
    return calls


def test_list_rows_carry_permission_gated_row_actions(
    session_context, device_api
):
    session_context.login(perms=FULL_PERMS)
    rows, pagination = manage_c.generate_device_table(
        {'page_num': 1, 'page_size': 10}
    )
    assert pagination['total'] == 1
    assert [item['content'] for item in rows[0]['operation']] == [
        '详情',
        '开始录音',
        '停止录音',
        '指令结果',
        '修改',
        '删除',
    ]
    assert rows[0]['battery_display'] == '40%'


def test_list_rows_hide_actions_without_permission(session_context, device_api):
    session_context.login(perms=['device:manage:query'])
    rows, _ = manage_c.generate_device_table({'page_num': 1, 'page_size': 10})
    assert [item['content'] for item in rows[0]['operation']] == ['详情']


def test_search_filters_are_forwarded_to_backend(
    session_context, device_api, dash_ctx
):
    """列表筛选：编码/名称/本地状态/心跳在线/绑定状态都要下发。"""
    session_context.login(perms=FULL_PERMS)
    dash_ctx.triggered_id = 'device-search'
    result = manage_c.refresh_device_table(
        None,
        None,
        {'current': 1, 'pageSize': 10},
        None,
        'MLR432',
        '研发',
        'online',
        2,
        'bound',
    )
    query = device_api['list_devices']
    assert query['device_code'] == 'MLR432'
    assert query['device_name'] == '研发'
    assert query['status'] == 'online'
    assert query['online_status'] == 2
    assert query['bind_status'] == 'bound'
    assert query['page_num'] == 1
    assert result['selected'] is None


def test_pagination_keeps_current_page_and_size(
    session_context, device_api, dash_ctx
):
    session_context.login(perms=FULL_PERMS)
    dash_ctx.triggered_id = 'device-list-table'
    manage_c.refresh_device_table(
        None,
        None,
        {'current': 3, 'pageSize': 30},
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert device_api['list_devices']['page_num'] == 3
    assert device_api['list_devices']['page_size'] == 30


def test_list_failure_reports_error_and_returns_empty_page(
    session_context, device_api, monkeypatch, messages
):
    session_context.login(perms=FULL_PERMS)

    def boom(_query):
        raise ServiceException(message='数据库连接异常')

    monkeypatch.setattr(manage_c.DeviceApi, 'list_devices', staticmethod(boom))
    rows, pagination = manage_c.generate_device_table(
        {'page_num': 1, 'page_size': 10}
    )
    assert rows == []
    assert pagination['total'] == 0
    assert messages()[-1]['content'].startswith('加载设备列表失败')


def test_detail_modal_renders_heartbeat_dictionary(session_context, device_api):
    session_context.login(perms=FULL_PERMS)
    visible, items, config_items, detail = manage_c.show_device_detail(
        1, '详情', {'key': '12'}
    )
    assert visible is True
    assert detail['device_code'] == 'MLR432KP42C19568'
    labels = [item['label'] for item in items]
    assert '4G 信号(rssi)' in labels and '心跳 online' in labels
    assert config_items[0]['label'] == '配置快照'


def test_detail_requires_the_detail_button(session_context, device_api):
    session_context.login(perms=FULL_PERMS)
    with pytest.raises(PreventUpdate):
        manage_c.show_device_detail(1, '修改', {'key': '12'})


def test_save_device_blocks_empty_device_code(session_context, device_api):
    session_context.login(perms=FULL_PERMS)
    result = manage_c.save_device(1, 'add', {'device_code': '  '}, ['设备编码'])
    assert result['statuses'] == {'设备编码': 'error'}
    assert 'add_device' not in device_api


def test_save_device_reports_backend_error_and_keeps_modal(
    session_context, device_api, monkeypatch, messages
):
    session_context.login(perms=FULL_PERMS)

    def boom(_payload):
        raise ServiceException(message='设备编码 MLR1 已存在')

    monkeypatch.setattr(manage_c.DeviceApi, 'add_device', staticmethod(boom))
    result = manage_c.save_device(
        1, 'add', {'device_code': 'MLR1'}, ['设备编码']
    )
    assert result['visible'] is not False
    assert 'MLR1 已存在' in messages()[-1]['content']


def test_save_device_success_closes_modal_and_reloads(
    session_context, device_api, messages
):
    session_context.login(perms=FULL_PERMS)
    result = manage_c.save_device(
        1, 'edit', {'device_id': 12, 'device_code': 'MLR1'}, ['设备编码']
    )
    assert result['visible'] is False
    assert result['operation'] == {'type': 'edit'}
    assert messages()[-1]['content'] == '修改设备成功'


def test_sync_status_partial_failure_is_reported(
    session_context, device_api, dash_ctx, monkeypatch, messages
):
    """契约 C9/E10：后端只回部分设备时，页面必须提示部分失败。"""
    session_context.login(perms=FULL_PERMS)

    def partial(sns):
        return {'data': {'entities': [{'sn': sns[0]}], 'synced': 1}}

    monkeypatch.setattr(
        manage_c.DeviceApi, 'sync_status', staticmethod(partial)
    )
    dash_ctx.triggered_id = {
        'type': 'device-operation-button',
        'index': 'sync',
    }
    table_data = [
        {'key': '12', 'device_code': 'A'},
        {'key': '13', 'device_code': 'B'},
    ]
    result = manage_c.run_remote_device_action(
        None, None, None, ['12', '13'], table_data, None, None
    )
    assert result['ok'] is False
    assert result['missing'] == ['B']
    assert '部分失败' in messages()[-1]['content']
    assert messages()[-1]['type'] == 'warning'


def test_sync_config_success_message(
    session_context, device_api, dash_ctx, messages
):
    session_context.login(perms=FULL_PERMS)
    dash_ctx.triggered_id = {
        'type': 'device-operation-button',
        'index': 'sync-config',
    }
    table_data = [{'key': '12', 'device_code': 'A'}]
    result = manage_c.run_remote_device_action(
        None, None, None, ['12'], table_data, None, None
    )
    assert result['ok'] is True
    assert device_api['sync_config'] == ['A']
    assert '同步配置成功' in messages()[-1]['content']


def test_sync_timeout_is_reported_as_timeout(
    session_context, device_api, dash_ctx, monkeypatch, messages
):
    session_context.login(perms=FULL_PERMS)

    def timeout(_sns):
        raise ServiceException(message='厂商接口请求超时')

    monkeypatch.setattr(
        manage_c.DeviceApi, 'sync_status', staticmethod(timeout)
    )
    dash_ctx.triggered_id = {
        'type': 'device-operation-button',
        'index': 'sync',
    }
    manage_c.run_remote_device_action(
        None,
        None,
        None,
        ['12'],
        [{'key': '12', 'device_code': 'A'}],
        None,
        None,
    )
    assert '超时' in messages()[-1]['content']


def test_start_recording_reports_msg_id(
    session_context, device_api, dash_ctx, messages
):
    session_context.login(perms=FULL_PERMS)
    dash_ctx.triggered_id = 'device-list-table'
    result = manage_c.run_remote_device_action(
        None, None, 1, None, None, '开始录音', dict(DEVICE_ROW)
    )
    assert result['ok'] is True
    assert result['msg_id'] == 'm-1'
    assert device_api['start_recording'][0] == 'MLR432KP42C19568'
    assert 'msg_id：m-1' in messages()[-1]['content']


def test_start_recording_without_permission_reports_permission_error(
    session_context, device_api, monkeypatch, messages
):
    session_context.login(perms=FULL_PERMS)

    def denied(_device_code, _audio_id=None):
        raise ServiceException(message='该用户无此接口权限')

    monkeypatch.setattr(
        manage_c.DeviceApi, 'start_recording', staticmethod(denied)
    )
    manage_c.send_recording_command('start_recording', 'MLR1', 'abcd123456')
    assert '无此操作权限' in messages()[-1]['content']


def test_stop_recording_requires_confirmation(
    session_context, device_api, dash_ctx, messages
):
    """契约 E11：危险操作先确认；未确认时不得调用厂商接口。"""
    session_context.login(perms=FULL_PERMS)
    dash_ctx.triggered_id = 'device-list-table'
    with pytest.raises(PreventUpdate):
        manage_c.run_remote_device_action(
            None, None, 1, None, None, '停止录音', dict(DEVICE_ROW)
        )
    assert 'stop_recording' not in device_api

    store, visible, text = manage_c.open_stop_recording_confirm(
        1, '停止录音', dict(DEVICE_ROW)
    )
    assert visible is True
    assert store == {'device_code': 'MLR432KP42C19568'}
    assert '确认停止' in text

    manage_c.confirm_stop_recording(1, store)
    assert device_api['stop_recording'] == 'MLR432KP42C19568'
    assert '停止录音指令已发送' in messages()[-1]['content']


def test_confirm_stop_recording_without_ok_does_nothing(
    session_context, device_api, monkeypatch
):
    session_context.login(perms=FULL_PERMS)
    called = []
    monkeypatch.setattr(
        manage_c,
        'send_recording_command',
        lambda *args, **kwargs: called.append(args),
    )
    with pytest.raises(PreventUpdate):
        manage_c.confirm_stop_recording(0, {'device_code': 'MLR1'})
    assert called == []


def test_delete_requires_confirmation_and_reports_failure(
    session_context, device_api, dash_ctx, monkeypatch, messages
):
    session_context.login(perms=FULL_PERMS)
    dash_ctx.triggered_id = {
        'type': 'device-operation-button',
        'index': 'delete',
    }
    text, visible, ids = manage_c.open_delete_modal(
        None, None, ['12', '13'], '删除', {'key': '12'}
    )
    assert visible is True
    assert ids == '12,13'
    assert '12,13' in text

    def denied(_ids):
        raise ServiceException(message='该用户无此接口权限')

    monkeypatch.setattr(
        manage_c.DeviceApi, 'delete_devices', staticmethod(denied)
    )
    result = manage_c.confirm_delete(1, ids)
    assert result['ok'] is False
    assert '无此操作权限' in messages()[-1]['content']


def test_open_command_modal_prefills_selected_device(
    session_context, device_api, dash_ctx
):
    session_context.login(perms=FULL_PERMS)
    dash_ctx.triggered_id = {
        'type': 'device-operation-button',
        'index': 'command',
    }
    visible, device_code, msg_id = manage_c.open_command_modal(
        None, None, ['12'], [{'key': '12', 'device_code': 'MLR1'}], None, None
    )
    assert (visible, device_code, msg_id) == (True, 'MLR1', None)


def test_query_command_result_combines_vendor_and_local_log(
    session_context, device_api, messages
):
    """契约 E5/C8：用 msg_id 串起本地控制日志与厂商指令回执。"""
    session_context.login(perms=FULL_PERMS)
    result = manage_c.query_command_result(1, 'MLR1', 'm-1')
    visible, alert_type, alert_text, values, rows = (
        result[4],
        result[2],
        result[1],
        {item['label']: item['children'] for item in result[5]},
        result[6],
    )
    assert visible is True
    assert alert_type == 'info'
    assert '成功' in alert_text
    assert values['厂商状态'] == '成功'
    assert values['本地受理结果'] == '已受理'
    assert rows[0]['command_display'] == '开启录音'
    assert device_api['list_control_logs']['msg_id'] == 'm-1'


def test_query_command_result_falls_back_to_latest_local_msg_id(
    session_context, device_api, messages
):
    """用户未填 msg_id 时，自动取最近一条本地控制日志，并回填输入框。"""
    session_context.login(perms=FULL_PERMS)
    result = manage_c.query_command_result(1, 'MLR1', None)
    assert result[0] == 'm-1'
    assert result[4] is True
    assert 'm-1' in messages()[-1]['content']
    assert 'msg_id' not in device_api['list_control_logs']


def test_query_command_result_without_control_log_permission(
    session_context, device_api, messages
):
    """无 ``device:control:list`` 时不查本地日志，只展示厂商回执并提示。"""
    session_context.login(perms=['device:manage:control'])
    result = manage_c.query_command_result(1, 'MLR1', 'm-1')
    assert result[4] is True
    assert 'list_control_logs' not in device_api
    assert messages()[-1]['type'] == 'warning'
    assert '无控制日志查询权限' in messages()[-1]['content']


def test_query_command_result_reports_missing_result(
    session_context, device_api, monkeypatch, messages
):
    session_context.login(perms=FULL_PERMS)

    def not_found(_device_code, _msg_id):
        raise ServiceException(message='Not found')

    monkeypatch.setattr(
        manage_c.DeviceApi, 'get_command_log', staticmethod(not_found)
    )
    monkeypatch.setattr(
        manage_c.DeviceApi,
        'list_control_logs',
        staticmethod(
            lambda _query: {
                'rows': [],
                'page_num': 1,
                'page_size': 5,
                'total': 0,
            }
        ),
    )
    result = manage_c.query_command_result(1, 'MLR1', 'm-404')
    assert result[4] is False
    assert result[2] == 'error'


def test_query_command_result_no_local_log_and_no_msg_id(
    session_context, device_api, monkeypatch
):
    session_context.login(perms=FULL_PERMS)
    monkeypatch.setattr(
        manage_c.DeviceApi,
        'list_control_logs',
        staticmethod(
            lambda _query: {
                'rows': [],
                'page_num': 1,
                'page_size': 5,
                'total': 0,
            }
        ),
    )
    result = manage_c.query_command_result(1, 'MLR1', None)
    assert result[4] is False
    assert result[2] == 'warning'
