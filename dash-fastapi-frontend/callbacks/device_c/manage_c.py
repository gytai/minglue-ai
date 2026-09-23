import uuid

from dash import ctx, no_update
from dash.dependencies import ALL, Input, Output, State
from dash.exceptions import PreventUpdate

from api.device import DeviceApi
from callbacks.device_c import device_page_logic as logic
from config.exception import (
    AuthException,
    RequestException,
    ServiceException,
    ServiceWarning,
)
from server import app
from utils.cache_util import CacheManager
from utils.common_util import ValidateUtil
from utils.feedback_util import MessageManager


#: 可预期的后端异常：加载失败、无权限、超时、厂商业务错误。
EXPECTED_ERRORS = (
    AuthException,
    RequestException,
    ServiceException,
    ServiceWarning,
)
#: 业务反馈等级 → MessageManager 方法（契约 E10）。
FEEDBACK_HANDLERS = {
    'success': MessageManager.success,
    'warning': MessageManager.warning,
    'error': MessageManager.error,
}


def current_permissions():
    """当前登录会话的权限标识，取值来源与 ``PermissionManager`` 一致。"""
    permissions = CacheManager.get('permissions') or {}
    return permissions.get('perms') or []


def report(level: str, message: str):
    """按等级输出反馈；未知等级统一按错误处理。"""
    handler = FEEDBACK_HANDLERS.get(level, MessageManager.error)
    handler(content=logic.mask_secret_text(message))


def notify_failure(exc, action: str) -> dict:
    """统一处理后端异常：反馈（已脱敏） + 返回分类结果。"""
    classified = logic.classify_error(exc, action)
    report(classified['level'], classified['message'])
    return classified


def generate_device_table(query_params: dict):
    """拉取设备列表并补齐展示字段；失败时给出可读反馈而不是抛原始报文。"""
    try:
        result = DeviceApi.list_devices(query_params)
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, '加载设备列表')
        return (
            [],
            {
                'pageSize': query_params.get('page_size', 10),
                'current': query_params.get('page_num', 1),
                'total': 0,
            },
        )
    rows = logic.format_device_rows(result.get('rows'))
    perms = current_permissions()
    for item in rows:
        item['operation'] = logic.control_action_buttons(perms)
    pagination = {
        'pageSize': result['page_size'],
        'current': result['page_num'],
        'showSizeChanger': True,
        'pageSizeOptions': [10, 30, 50, 100],
        'showQuickJumper': True,
        'total': result['total'],
    }
    return rows, pagination


@app.callback(
    output=dict(
        data=Output('device-list-table', 'data', allow_duplicate=True),
        pagination=Output(
            'device-list-table', 'pagination', allow_duplicate=True
        ),
        key=Output('device-list-table', 'key'),
        selected=Output('device-list-table', 'selectedRowKeys'),
    ),
    inputs=dict(
        search=Input('device-search', 'nClicks'),
        refresh=Input('device-refresh', 'nClicks'),
        pagination=Input('device-list-table', 'pagination'),
        operations=Input('device-operations-store', 'data'),
    ),
    state=dict(
        code=State('device-code-search', 'value'),
        name=State('device-name-search', 'value'),
        status=State('device-status-search', 'value'),
        online_status=State('device-online-status-search', 'value'),
        bind_status=State('device-bind-status-search', 'value'),
    ),
    prevent_initial_call=True,
)
def refresh_device_table(
    search,
    refresh,
    pagination,
    operations,
    code,
    name,
    status,
    online_status,
    bind_status,
):
    query = logic.build_device_query(
        device_code=code,
        device_name=name,
        status=status,
        bind_status=bind_status,
        online_status=online_status,
        page_num=1,
        page_size=10,
    )
    if ctx.triggered_id == 'device-list-table':
        query.update(
            page_num=pagination['current'], page_size=pagination['pageSize']
        )
    rows, page = generate_device_table(query)
    return {
        'data': rows,
        'pagination': page,
        'key': str(uuid.uuid4()),
        'selected': None,
    }


app.clientside_callback(
    """
    (click) => click ? [null, null, null, null, null, {'type': 'reset'}] : window.dash_clientside.no_update
    """,
    [
        Output('device-code-search', 'value'),
        Output('device-name-search', 'value'),
        Output('device-status-search', 'value'),
        Output('device-online-status-search', 'value'),
        Output('device-bind-status-search', 'value'),
        Output('device-operations-store', 'data'),
    ],
    Input('device-reset', 'nClicks'),
    prevent_initial_call=True,
)


#: 批量/行内按钮的启用条件：同步类需勾选若干行，编辑/指令结果需且仅需一行。
BUTTON_ENABLE_RULES = (
    ('sync', 'selected?.length > 0 ? false : true'),
    ('sync-config', 'selected?.length > 0 ? false : true'),
    ('command', 'selected?.length === 1 ? false : true'),
    ('edit', 'selected?.length === 1 ? false : true'),
    ('delete', 'selected?.length > 0 ? false : true'),
)

for _index, _condition in BUTTON_ENABLE_RULES:
    app.clientside_callback(
        f'(selected) => {_condition}',
        Output(
            {'type': 'device-operation-button', 'index': _index}, 'disabled'
        ),
        Input('device-list-table', 'selectedRowKeys'),
        prevent_initial_call=True,
    )


app.clientside_callback(
    """
    (row, values) => {
        const trigger = window.dash_clientside.callback_context.triggered_id;
        if (trigger === 'device-form-store') return [window.dash_clientside.no_update, row];
        if (trigger === 'device-form') {
            Object.assign(row || {}, values || {});
            return [row, window.dash_clientside.no_update];
        }
        throw window.dash_clientside.PreventUpdate;
    }
    """,
    [
        Output('device-form-store', 'data', allow_duplicate=True),
        Output('device-form', 'values'),
    ],
    [Input('device-form-store', 'data'), Input('device-form', 'values')],
    prevent_initial_call=True,
)


@app.callback(
    output=dict(
        visible=Output('device-modal', 'visible', allow_duplicate=True),
        title=Output('device-modal', 'title'),
        values=Output('device-form-store', 'data', allow_duplicate=True),
        modal_type=Output('device-modal-type-store', 'data'),
        statuses=Output(
            'device-form', 'validateStatuses', allow_duplicate=True
        ),
        helps=Output('device-form', 'helps', allow_duplicate=True),
    ),
    inputs=dict(
        operations=Input(
            {'type': 'device-operation-button', 'index': ALL}, 'nClicks'
        ),
        row_button=Input('device-list-table', 'nClicksButton'),
    ),
    state=dict(
        selected=State('device-list-table', 'selectedRowKeys'),
        clicked=State('device-list-table', 'clickedContent'),
        row=State('device-list-table', 'recentlyButtonClickedRow'),
    ),
    prevent_initial_call=True,
)
def open_device_modal(operations, row_button, selected, clicked, row):
    trigger = ctx.triggered_id
    if trigger == {'type': 'device-operation-button', 'index': 'add'}:
        return {
            'visible': True,
            'title': '新增设备',
            'values': {'status': 'offline', 'bind_status': 'unbound'},
            'modal_type': 'add',
            'statuses': None,
            'helps': None,
        }
    is_row_edit = trigger == 'device-list-table' and clicked == '修改'
    if (
        trigger == {'type': 'device-operation-button', 'index': 'edit'}
        or is_row_edit
    ):
        if is_row_edit:
            device_id = int(row['key'])
        elif selected:
            device_id = int(selected[0])
        else:
            raise PreventUpdate
        try:
            values = DeviceApi.get_device(device_id)['data']
        except EXPECTED_ERRORS as exc:
            notify_failure(exc, '加载设备详情')
            raise PreventUpdate from exc
        return {
            'visible': True,
            'title': '编辑设备',
            'values': values,
            'modal_type': 'edit',
            'statuses': None,
            'helps': None,
        }
    raise PreventUpdate


@app.callback(
    output=dict(
        statuses=Output(
            'device-form', 'validateStatuses', allow_duplicate=True
        ),
        helps=Output('device-form', 'helps', allow_duplicate=True),
        visible=Output('device-modal', 'visible'),
        operation=Output(
            'device-operations-store', 'data', allow_duplicate=True
        ),
    ),
    inputs=dict(confirm=Input('device-modal', 'okCounts')),
    state=dict(
        modal_type=State('device-modal-type-store', 'data'),
        values=State('device-form-store', 'data'),
        required_labels=State(
            {'type': 'device-form-label', 'index': ALL, 'required': True},
            'label',
        ),
    ),
    running=[[Output('device-modal', 'confirmLoading'), True, False]],
    prevent_initial_call=True,
)
def save_device(confirm, modal_type, values, required_labels):
    if not confirm:
        raise PreventUpdate
    # 与后端 ``DeviceModel.device_code`` 一致：先去掉首尾空白再判必填，
    # 避免纯空白编码提交到后端才报错。
    payload = dict(values or {})
    device_code = (payload.get('device_code') or '').strip()
    if not ValidateUtil.not_empty(device_code):
        return {
            'statuses': {'设备编码': 'error'},
            'helps': {'设备编码': '设备编码不能为空'},
            'visible': no_update,
            'operation': no_update,
        }
    payload['device_code'] = device_code
    action = '新增设备' if modal_type == 'add' else '修改设备'
    try:
        if modal_type == 'add':
            DeviceApi.add_device(payload)
        else:
            DeviceApi.update_device(payload)
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, action)
        return {
            'statuses': None,
            'helps': None,
            'visible': no_update,
            'operation': no_update,
        }
    MessageManager.success(content=f'{action}成功')
    return {
        'statuses': None,
        'helps': None,
        'visible': False,
        'operation': {'type': modal_type},
    }


@app.callback(
    [
        Output('device-detail-modal', 'visible'),
        Output('device-heartbeat-descriptions', 'items'),
        Output('device-config-descriptions', 'items'),
        Output('device-detail-store', 'data'),
    ],
    Input('device-list-table', 'nClicksButton'),
    [
        State('device-list-table', 'clickedContent'),
        State('device-list-table', 'recentlyButtonClickedRow'),
    ],
    prevent_initial_call=True,
)
def show_device_detail(click, clicked, row):
    """设备详情：完整心跳字段 + 厂商配置快照（契约 E6）。"""
    if not click or clicked != '详情':
        raise PreventUpdate
    try:
        detail = DeviceApi.get_device(int(row['key']))['data']
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, '加载设备详情')
        raise PreventUpdate from exc
    return (
        True,
        logic.build_device_detail_items(detail),
        logic.build_device_config_items(detail),
        detail,
    )


def send_recording_command(command: str, device_code: str, audio_id=None):
    """发送录音控制指令并反馈厂商 ``msg_id``（契约 C8）。"""
    action = '停止录音' if command == 'stop_recording' else '开始录音'
    try:
        if command == 'stop_recording':
            response = DeviceApi.stop_recording(device_code)
        else:
            response = DeviceApi.start_recording(device_code, audio_id)
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, action)
        return {'type': action, 'device_code': device_code, 'ok': False}
    data = response.get('data') if isinstance(response, dict) else None
    msg_id = logic.extract_msg_id(data)
    stat = data.get('stat') if isinstance(data, dict) else None
    if stat not in (None, '', 0, '0', 1, '1'):
        # 厂商已受理但状态非成功：提示回查指令结果，而不是当作成功。
        report(
            'warning',
            f'{action}已下发，厂商状态为 {stat}，请用指令结果查询确认回执',
        )
    else:
        MessageManager.success(
            content=f'{action}指令已发送'
            + (f'，msg_id：{msg_id}' if msg_id else '')
        )
    return {
        'type': action,
        'device_code': device_code,
        'msg_id': msg_id,
        'ok': True,
    }


@app.callback(
    Output('device-operations-store', 'data', allow_duplicate=True),
    [
        Input({'type': 'device-operation-button', 'index': 'sync'}, 'nClicks'),
        Input(
            {'type': 'device-operation-button', 'index': 'sync-config'},
            'nClicks',
        ),
        Input('device-list-table', 'nClicksButton'),
    ],
    [
        State('device-list-table', 'selectedRowKeys'),
        State('device-list-table', 'data'),
        State('device-list-table', 'clickedContent'),
        State('device-list-table', 'recentlyButtonClickedRow'),
    ],
    running=[
        [
            Output(
                {'type': 'device-operation-button', 'index': 'sync'}, 'loading'
            ),
            True,
            False,
        ],
        [
            Output(
                {'type': 'device-operation-button', 'index': 'sync-config'},
                'loading',
            ),
            True,
            False,
        ],
    ],
    prevent_initial_call=True,
)
def run_remote_device_action(
    sync_click, sync_config_click, row_click, selected, table_data, clicked, row
):
    trigger = ctx.triggered_id
    sync_triggers = (
        {'type': 'device-operation-button', 'index': 'sync'},
        {'type': 'device-operation-button', 'index': 'sync-config'},
    )
    if trigger in sync_triggers:
        selected_ids = set(selected or [])
        sns = [
            item['device_code']
            for item in (table_data or [])
            if str(item.get('key')) in selected_ids
        ]
        if not sns:
            raise PreventUpdate
        is_config = trigger == sync_triggers[1]
        action = '同步配置' if is_config else '同步状态'
        try:
            response = (
                DeviceApi.sync_config(sns)
                if is_config
                else DeviceApi.sync_status(sns)
            )
        except EXPECTED_ERRORS as exc:
            notify_failure(exc, action)
            return {'type': action, 'ok': False}
        summary = logic.summarize_batch_result(
            sns, response.get('data'), action
        )
        report(summary['level'], summary['message'])
        return {
            'type': action,
            'ok': summary['level'] == 'success',
            'missing': summary['missing'],
        }
    if trigger == 'device-list-table' and clicked == '开始录音':
        audio_id = uuid.uuid4().hex[:10]
        return send_recording_command(
            'start_recording', row['device_code'], audio_id
        )
    # 停止录音是危险操作：只打开确认弹窗，实际下发在 confirm_stop_recording。
    raise PreventUpdate


@app.callback(
    [
        Output('device-recording-confirm-store', 'data'),
        Output('device-stop-confirm-modal', 'visible'),
        Output('device-stop-text', 'children'),
    ],
    Input('device-list-table', 'nClicksButton'),
    [
        State('device-list-table', 'clickedContent'),
        State('device-list-table', 'recentlyButtonClickedRow'),
    ],
    prevent_initial_call=True,
)
def open_stop_recording_confirm(click, clicked, row):
    """危险操作二次确认（契约 E11）：停止录音先确认再下发。"""
    if not click or clicked != '停止录音':
        raise PreventUpdate
    device_code = row.get('device_code')
    return (
        {'device_code': device_code},
        True,
        f'是否确认停止设备 {device_code} 的录音？',
    )


@app.callback(
    Output('device-operations-store', 'data', allow_duplicate=True),
    Input('device-stop-confirm-modal', 'okCounts'),
    State('device-recording-confirm-store', 'data'),
    running=[
        [Output('device-stop-confirm-modal', 'confirmLoading'), True, False]
    ],
    prevent_initial_call=True,
)
def confirm_stop_recording(ok, payload):
    if not ok:
        raise PreventUpdate
    device_code = (payload or {}).get('device_code')
    if not device_code:
        raise PreventUpdate
    return send_recording_command('stop_recording', device_code)


@app.callback(
    [
        Output('device-delete-text', 'children'),
        Output('device-delete-confirm-modal', 'visible'),
        Output('device-delete-ids-store', 'data'),
    ],
    [
        Input(
            {'type': 'device-operation-button', 'index': 'delete'}, 'nClicks'
        ),
        Input('device-list-table', 'nClicksButton'),
    ],
    [
        State('device-list-table', 'selectedRowKeys'),
        State('device-list-table', 'clickedContent'),
        State('device-list-table', 'recentlyButtonClickedRow'),
    ],
    prevent_initial_call=True,
)
def open_delete_modal(operations, row_click, selected, clicked, row):
    trigger = ctx.triggered_id
    if trigger == {'type': 'device-operation-button', 'index': 'delete'}:
        ids = ','.join(selected)
    elif trigger == 'device-list-table' and clicked == '删除':
        ids = row['key']
    else:
        raise PreventUpdate
    return f'是否确认删除设备编号为 {ids} 的记录？', True, ids


@app.callback(
    Output('device-operations-store', 'data', allow_duplicate=True),
    Input('device-delete-confirm-modal', 'okCounts'),
    State('device-delete-ids-store', 'data'),
    running=[
        [Output('device-delete-confirm-modal', 'confirmLoading'), True, False]
    ],
    prevent_initial_call=True,
)
def confirm_delete(ok, ids):
    if not ok:
        raise PreventUpdate
    try:
        DeviceApi.delete_devices(ids)
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, '删除设备')
        return {'type': 'delete', 'ok': False}
    MessageManager.success(content='删除成功')
    return {'type': 'delete', 'ok': True}


@app.callback(
    [
        Output('device-command-modal', 'visible'),
        Output('device-command-device-code', 'value'),
        Output('device-command-msg-id', 'value'),
    ],
    [
        Input(
            {'type': 'device-operation-button', 'index': 'command'}, 'nClicks'
        ),
        Input('device-list-table', 'nClicksButton'),
    ],
    [
        State('device-list-table', 'selectedRowKeys'),
        State('device-list-table', 'data'),
        State('device-list-table', 'clickedContent'),
        State('device-list-table', 'recentlyButtonClickedRow'),
    ],
    prevent_initial_call=True,
)
def open_command_modal(
    command_click, row_click, selected, table_data, clicked, row
):
    """打开指令结果查询入口（契约 E5）。

    ``msg_id`` 默认留空：查询时按设备取最近一条本地控制日志，不要求用户手工记忆
    厂商返回的 ID。
    """
    trigger = ctx.triggered_id
    if trigger == {'type': 'device-operation-button', 'index': 'command'}:
        selected_ids = set(selected or [])
        device_code = next(
            (
                item['device_code']
                for item in (table_data or [])
                if str(item.get('key')) in selected_ids
            ),
            None,
        )
        if not device_code:
            raise PreventUpdate
        return True, device_code, None
    if trigger == 'device-list-table' and clicked == '指令结果':
        return True, row.get('device_code'), None
    raise PreventUpdate


@app.callback(
    [
        Output('device-command-msg-id', 'value', allow_duplicate=True),
        Output('device-command-alert', 'children'),
        Output('device-command-alert', 'type'),
        Output('device-command-alert', 'style'),
        Output('device-command-result-modal', 'visible'),
        Output('device-command-descriptions', 'items'),
    ],
    Input('device-command-modal', 'okCounts'),
    [
        State('device-command-device-code', 'value'),
        State('device-command-msg-id', 'value'),
    ],
    running=[[Output('device-command-modal', 'confirmLoading'), True, False]],
    prevent_initial_call=True,
)
def query_command_result(ok, device_code, msg_id):
    """查询指令结果：直接消费后端合并结果（契约 §4.5 / §4.7）。

    ``GET /device/{sn}/command/{msg_id}`` 返回 ``{remote, remote_error, local}``，
    厂商侧暂无记录时用本地控制日志兜底，因此无需在前端拼两份数据。
    ``msg_id`` 留空时按设备取最近一条本地控制日志（需要 ``device:control:list``）。
    """
    if not ok or not device_code:
        raise PreventUpdate
    if not msg_id:
        if not logic.has_permission(
            current_permissions(), 'device:control:list'
        ):
            return (
                no_update,
                '请填写 msg_id；本账号无控制日志查询权限，无法自动取最近一条指令',
                'warning',
                {'display': 'block'},
                False,
                no_update,
            )
        try:
            rows = logic.format_control_rows(
                DeviceApi.list_control_logs(
                    logic.build_control_log_query(
                        device_code=device_code, page_num=1, page_size=1
                    )
                ).get('rows')
            )
        except EXPECTED_ERRORS as exc:
            classified = notify_failure(exc, '查询本地控制日志')
            return (
                no_update,
                classified['message'],
                'error',
                {'display': 'block'},
                False,
                no_update,
            )
        msg_id = logic.extract_msg_id(rows[0]) if rows else None
        if not msg_id:
            return (
                no_update,
                '该设备暂无本地控制日志，请先下发开始/停止录音指令',
                'warning',
                {'display': 'block'},
                False,
                no_update,
            )
        MessageManager.info(content=f'已取最近一条控制日志的 msg_id：{msg_id}')
    try:
        data = DeviceApi.get_command_log(device_code, msg_id).get('data')
    except EXPECTED_ERRORS as exc:
        classified = notify_failure(exc, '查询指令结果')
        return (
            msg_id,
            classified['message'],
            'error',
            {'display': 'block'},
            False,
            no_update,
        )
    alert = logic.command_result_alert(data)
    return (
        msg_id,
        alert['text'],
        alert['type'],
        {'display': 'block'},
        True,
        logic.command_result_items(data),
    )


@app.callback(
    [
        Output('device-status-modal', 'visible'),
        Output('device-status-device-code', 'value'),
        Output('device-status-value', 'value'),
        Output('device-status-bind-status', 'value'),
    ],
    Input('device-list-table', 'nClicksButton'),
    [
        State('device-list-table', 'clickedContent'),
        State('device-list-table', 'recentlyButtonClickedRow'),
    ],
    prevent_initial_call=True,
)
def open_status_modal(click, clicked, row):
    """状态维护入口（契约 §4.7 ``PUT /device/status``，守卫 ``device:manage:edit``）。"""
    if not click or clicked != '状态维护':
        raise PreventUpdate
    return (
        True,
        row.get('device_code'),
        row.get('status') if row.get('status') in logic.STATUS_LABELS else None,
        row.get('bind_status')
        if row.get('bind_status') in logic.BIND_LABELS
        else None,
    )


@app.callback(
    Output('device-operations-store', 'data', allow_duplicate=True),
    Input('device-status-modal', 'okCounts'),
    [
        State('device-status-device-code', 'value'),
        State('device-status-value', 'value'),
        State('device-status-bind-status', 'value'),
        State('device-list-table', 'data'),
    ],
    running=[[Output('device-status-modal', 'confirmLoading'), True, False]],
    prevent_initial_call=True,
)
def save_device_status(ok, device_code, status, bind_status, table_data):
    """提交状态维护：只提交被改动的字段，避免把未改字段覆盖成空。"""
    if not ok or not device_code:
        raise PreventUpdate
    device_id = next(
        (
            int(item['key'])
            for item in (table_data or [])
            if item.get('device_code') == device_code
        ),
        None,
    )
    if not device_id:
        report('error', '状态维护失败：未在列表中匹配到该设备，请刷新后重试')
        return {'type': 'status', 'ok': False}
    payload = {'device_id': device_id}
    if status:
        payload['status'] = status
    if bind_status:
        payload['bind_status'] = bind_status
    if len(payload) == 1:
        report('warning', '状态维护需要有改动：请选择本地状态或绑定状态')
        return {'type': 'status', 'ok': False}
    try:
        DeviceApi.update_status(payload)
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, '状态维护')
        return {'type': 'status', 'ok': False}
    MessageManager.success(content='设备状态已更新')
    return {'type': 'status', 'ok': True}
