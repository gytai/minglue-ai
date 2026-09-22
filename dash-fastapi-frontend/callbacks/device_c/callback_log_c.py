import json
import uuid

from dash import ctx
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

from api.device import DeviceApi
from server import app
from utils.feedback_util import MessageManager
from utils.time_format_util import TimeFormatUtil


def generate_callback_table(query_params: dict):
    result = DeviceApi.list_callbacks(query_params)
    rows = result['rows']
    for item in rows:
        item['key'] = str(item['callback_id'])
        item['event_time'] = TimeFormatUtil.format_time(item.get('event_time'))
        item['received_at'] = TimeFormatUtil.format_time(
            item.get('received_at')
        )
        item['process_status'] = (
            '成功' if item.get('process_status') == 'success' else '失败'
        )
        item['operation'] = [
            {'content': '详情', 'type': 'link', 'icon': 'antd-eye'}
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
        data=Output('callback-list-table', 'data', allow_duplicate=True),
        pagination=Output(
            'callback-list-table', 'pagination', allow_duplicate=True
        ),
        key=Output('callback-list-table', 'key'),
        selected=Output('callback-list-table', 'selectedRowKeys'),
    ),
    inputs=dict(
        search=Input('callback-search', 'nClicks'),
        refresh=Input('callback-refresh', 'nClicks'),
        pagination=Input('callback-list-table', 'pagination'),
        operations=Input('callback-operations-store', 'data'),
    ),
    state=dict(
        device_code=State('callback-device-code-search', 'value'),
        event_type=State('callback-event-type-search', 'value'),
        time_range=State('callback-time-search', 'value'),
    ),
    prevent_initial_call=True,
)
def refresh_callback_table(
    search, refresh, pagination, operations, device_code, event_type, time_range
):
    query = {
        'device_code': device_code,
        'event_type': event_type,
        'page_num': 1,
        'page_size': 10,
    }
    if time_range:
        query.update(begin_time=time_range[0], end_time=time_range[1])
    if ctx.triggered_id == 'callback-list-table':
        query.update(
            page_num=pagination['current'], page_size=pagination['pageSize']
        )
    rows, page = generate_callback_table(query)
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
        Output('callback-device-code-search', 'value'),
        Output('callback-event-type-search', 'value'),
        Output('callback-time-search', 'value'),
        Output('callback-operations-store', 'data'),
    ],
    Input('callback-reset', 'nClicks'),
    prevent_initial_call=True,
)


app.clientside_callback(
    """
    (selected) => selected?.length > 0 ? false : true
    """,
    Output('callback-delete', 'disabled'),
    Input('callback-list-table', 'selectedRowKeys'),
    prevent_initial_call=True,
)


@app.callback(
    [
        Output('callback-detail-modal', 'visible'),
        Output('callback-detail-descriptions', 'items'),
        Output('callback-payload-preview', 'children'),
    ],
    Input('callback-list-table', 'nClicksButton'),
    [
        State('callback-list-table', 'clickedContent'),
        State('callback-list-table', 'recentlyButtonClickedRow'),
    ],
    prevent_initial_call=True,
)
def show_callback_detail(click, content, row):
    if not click or content != '详情':
        raise PreventUpdate
    detail = DeviceApi.get_callback(int(row['key']))['data']
    items = [
        {'label': '事件类型', 'children': detail.get('event_type') or '-'},
        {'label': '设备编码', 'children': detail.get('device_code') or '-'},
        {'label': '事件ID', 'children': detail.get('event_id') or '-'},
        {'label': '来源IP', 'children': detail.get('request_ip') or '-'},
        {
            'label': '接收时间',
            'children': TimeFormatUtil.format_time(detail.get('received_at')),
        },
        {'label': '处理状态', 'children': detail.get('process_status')},
    ]
    try:
        pretty_payload = json.dumps(
            json.loads(detail.get('payload_json') or '{}'),
            ensure_ascii=False,
            indent=2,
        )
    except (TypeError, ValueError):
        pretty_payload = detail.get('payload_json') or ''
    return True, items, pretty_payload


@app.callback(
    [
        Output('callback-delete-confirm-modal', 'visible'),
        Output('callback-delete-ids-store', 'data'),
    ],
    Input('callback-delete', 'nClicks'),
    State('callback-list-table', 'selectedRowKeys'),
    prevent_initial_call=True,
)
def show_delete_modal(click, selected):
    if not click or not selected:
        raise PreventUpdate
    return True, ','.join(selected)


@app.callback(
    Output('callback-operations-store', 'data', allow_duplicate=True),
    Input('callback-delete-confirm-modal', 'okCounts'),
    State('callback-delete-ids-store', 'data'),
    prevent_initial_call=True,
)
def delete_callbacks(ok, callback_ids):
    if not ok:
        raise PreventUpdate
    DeviceApi.delete_callbacks(callback_ids)
    MessageManager.success(content='删除成功')
    return {'type': 'delete'}
