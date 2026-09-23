import uuid

from dash import ctx
from dash.dependencies import Input, Output, State
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
from utils.feedback_util import MessageManager


EXPECTED_ERRORS = (
    AuthException,
    RequestException,
    ServiceException,
    ServiceWarning,
)
FEEDBACK_HANDLERS = {
    'success': MessageManager.success,
    'warning': MessageManager.warning,
    'error': MessageManager.error,
}


def notify_failure(exc, action: str):
    """后端异常 → 可读反馈（无权限/超时/业务错误各用对应等级）。"""
    classified = logic.classify_error(exc, action)
    FEEDBACK_HANDLERS.get(classified['level'], MessageManager.error)(
        content=logic.mask_secret_text(classified['message'])
    )
    return classified


def generate_callback_table(query_params: dict):
    """回调日志列表；失败时给出可读反馈并返回空页。"""
    try:
        result = DeviceApi.list_callbacks(query_params)
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, '加载回调日志')
        return (
            [],
            {
                'pageSize': query_params.get('page_size', 10),
                'current': query_params.get('page_num', 1),
                'total': 0,
            },
        )
    rows = logic.format_callback_rows(result.get('rows'))
    for item in rows:
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
        process_status=State('callback-process-status-search', 'value'),
        duplicate_flag=State('callback-duplicate-search', 'value'),
        time_range=State('callback-time-search', 'value'),
    ),
    prevent_initial_call=True,
)
def refresh_callback_table(
    search,
    refresh,
    pagination,
    operations,
    device_code,
    event_type,
    process_status,
    duplicate_flag,
    time_range,
):
    query = logic.build_callback_query(
        device_code=device_code,
        event_type=event_type,
        process_status=process_status,
        duplicate_flag=duplicate_flag,
        time_range=time_range,
        page_num=1,
        page_size=10,
    )
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
    (click) => click ? [null, null, null, null, null, {'type': 'reset'}] : window.dash_clientside.no_update
    """,
    [
        Output('callback-device-code-search', 'value'),
        Output('callback-event-type-search', 'value'),
        Output('callback-process-status-search', 'value'),
        Output('callback-duplicate-search', 'value'),
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
        Output('callback-process-descriptions', 'items'),
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
    """回调详情：事件信息 + 幂等/处理信息 + 脱敏后的原始报文（契约 E8/E9）。"""
    if not click or content != '详情':
        raise PreventUpdate
    try:
        detail = DeviceApi.get_callback(int(row['key']))['data']
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, '加载回调详情')
        raise PreventUpdate from exc
    event_items, process_items = logic.build_callback_detail_items(detail)
    return (
        True,
        event_items,
        process_items,
        logic.format_payload(detail.get('payload_json')),
    )


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
    running=[
        [Output('callback-delete-confirm-modal', 'confirmLoading'), True, False]
    ],
    prevent_initial_call=True,
)
def delete_callbacks(ok, callback_ids):
    if not ok:
        raise PreventUpdate
    try:
        DeviceApi.delete_callbacks(callback_ids)
    except EXPECTED_ERRORS as exc:
        notify_failure(exc, '删除回调记录')
        return {'type': 'delete', 'ok': False}
    MessageManager.success(content='删除成功')
    return {'type': 'delete', 'ok': True}
