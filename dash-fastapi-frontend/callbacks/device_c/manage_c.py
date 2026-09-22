import uuid

from dash import ctx, no_update
from dash.dependencies import ALL, Input, Output, State
from dash.exceptions import PreventUpdate

from api.device import DeviceApi
from server import app
from utils.common_util import ValidateUtil
from utils.feedback_util import MessageManager
from utils.permission_util import PermissionManager
from utils.time_format_util import TimeFormatUtil


STATUS_LABELS = {'online': '在线', 'offline': '离线', 'disabled': '已停用'}
BIND_LABELS = {'bound': '已绑定', 'unbound': '未绑定'}


def generate_device_table(query_params: dict):
    result = DeviceApi.list_devices(query_params)
    rows = result['rows']
    for item in rows:
        item['key'] = str(item['device_id'])
        item['status'] = STATUS_LABELS.get(
            item.get('status'), item.get('status')
        )
        item['bind_status'] = BIND_LABELS.get(
            item.get('bind_status'), item.get('bind_status')
        )
        item['battery_display'] = (
            f'{item["battery_level"]}%'
            if item.get('battery_level') is not None
            else '-'
        )
        item['last_seen_time'] = TimeFormatUtil.format_time(
            item.get('last_seen_time')
        )
        item['operation'] = [
            {'content': '修改', 'type': 'link', 'icon': 'antd-edit'}
            if PermissionManager.check_perms('device:manage:edit')
            else {},
            {'content': '删除', 'type': 'link', 'icon': 'antd-delete'}
            if PermissionManager.check_perms('device:manage:remove')
            else {},
        ]
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
    ),
    prevent_initial_call=True,
)
def refresh_device_table(
    search, refresh, pagination, operations, code, name, status
):
    query = {
        'device_code': code,
        'device_name': name,
        'status': status,
        'page_num': 1,
        'page_size': 10,
    }
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
    (click) => click ? [null, null, null, {'type': 'reset'}] : window.dash_clientside.no_update
    """,
    [
        Output('device-code-search', 'value'),
        Output('device-name-search', 'value'),
        Output('device-status-search', 'value'),
        Output('device-operations-store', 'data'),
    ],
    Input('device-reset', 'nClicks'),
    prevent_initial_call=True,
)


app.clientside_callback(
    """
    (selected) => selected?.length === 1 ? false : true
    """,
    Output({'type': 'device-operation-button', 'index': 'edit'}, 'disabled'),
    Input('device-list-table', 'selectedRowKeys'),
    prevent_initial_call=True,
)


app.clientside_callback(
    """
    (selected) => selected?.length > 0 ? false : true
    """,
    Output({'type': 'device-operation-button', 'index': 'delete'}, 'disabled'),
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
        values = {'status': 'offline', 'bind_status': 'unbound'}
        return {
            'visible': True,
            'title': '新增设备',
            'values': values,
            'modal_type': 'add',
            'statuses': None,
            'helps': None,
        }
    if trigger == {'type': 'device-operation-button', 'index': 'edit'} or (
        trigger == 'device-list-table' and clicked == '修改'
    ):
        device_id = (
            int(selected[0])
            if trigger != 'device-list-table'
            else int(row['key'])
        )
        values = DeviceApi.get_device(device_id)['data']
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
    device_code = (values or {}).get('device_code')
    if not ValidateUtil.not_empty(device_code):
        return {
            'statuses': {'设备编码': 'error'},
            'helps': {'设备编码': '设备编码不能为空'},
            'visible': no_update,
            'operation': no_update,
        }
    if modal_type == 'add':
        DeviceApi.add_device(values)
    else:
        DeviceApi.update_device(values)
    MessageManager.success(content='保存成功')
    return {
        'statuses': None,
        'helps': None,
        'visible': False,
        'operation': {'type': modal_type},
    }


@app.callback(
    [
        Output('device-delete-text', 'children'),
        Output('device-delete-confirm-modal', 'visible'),
        Output('device-delete-ids-store', 'data'),
    ],
    [
        Input({'type': 'device-operation-button', 'index': ALL}, 'nClicks'),
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
    prevent_initial_call=True,
)
def confirm_delete(ok, ids):
    if not ok:
        raise PreventUpdate
    DeviceApi.delete_devices(ids)
    MessageManager.success(content='删除成功')
    return {'type': 'delete'}
