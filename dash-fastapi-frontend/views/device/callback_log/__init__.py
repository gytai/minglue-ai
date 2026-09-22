import feffery_antd_components as fac
from dash import dcc, html

from callbacks.device_c import callback_log_c
from utils.permission_util import PermissionManager


def render(*args, **kwargs):
    table_data, table_pagination = callback_log_c.generate_callback_table(
        {'page_num': 1, 'page_size': 10}
    )
    return [
        dcc.Store(id='callback-operations-store'),
        dcc.Store(id='callback-delete-ids-store'),
        fac.AntdForm(
            fac.AntdSpace(
                [
                    fac.AntdFormItem(
                        fac.AntdInput(
                            id='callback-device-code-search',
                            placeholder='设备编码',
                            allowClear=True,
                            style={'width': 190},
                        ),
                        label='设备编码',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(
                            id='callback-event-type-search',
                            placeholder='事件类型',
                            allowClear=True,
                            style={'width': 180},
                        ),
                        label='事件类型',
                    ),
                    fac.AntdFormItem(
                        fac.AntdDateRangePicker(
                            id='callback-time-search', style={'width': 250}
                        ),
                        label='接收时间',
                    ),
                    fac.AntdFormItem(
                        fac.AntdButton(
                            '搜索',
                            id='callback-search',
                            type='primary',
                            icon=fac.AntdIcon(icon='antd-search'),
                        )
                    ),
                    fac.AntdFormItem(
                        fac.AntdButton(
                            '重置',
                            id='callback-reset',
                            icon=fac.AntdIcon(icon='antd-sync'),
                        )
                    ),
                ]
            ),
            layout='inline',
        ),
        fac.AntdSpace(
            [
                fac.AntdButton(
                    '删除',
                    id='callback-delete',
                    danger=True,
                    disabled=True,
                    icon=fac.AntdIcon(icon='antd-delete'),
                )
                if PermissionManager.check_perms('device:callback:remove')
                else [],
                fac.AntdButton(
                    '刷新',
                    id='callback-refresh',
                    icon=fac.AntdIcon(icon='antd-sync'),
                ),
            ],
            style={'paddingBottom': '12px'},
        ),
        fac.AntdSpin(
            fac.AntdTable(
                id='callback-list-table',
                data=table_data,
                columns=[
                    {'title': 'ID', 'dataIndex': 'callback_id', 'width': 90},
                    {'title': '事件类型', 'dataIndex': 'event_type'},
                    {'title': '设备编码', 'dataIndex': 'device_code'},
                    {
                        'title': '事件时间',
                        'dataIndex': 'event_time',
                        'width': 170,
                    },
                    {
                        'title': '接收时间',
                        'dataIndex': 'received_at',
                        'width': 170,
                    },
                    {
                        'title': '处理状态',
                        'dataIndex': 'process_status',
                        'width': 100,
                    },
                    {'title': '来源IP', 'dataIndex': 'request_ip'},
                    {
                        'title': '操作',
                        'dataIndex': 'operation',
                        'width': 100,
                        'renderOptions': {'renderType': 'button'},
                    },
                ],
                rowSelectionType='checkbox',
                rowSelectionWidth=50,
                bordered=True,
                pagination=table_pagination,
                mode='server-side',
                style={'width': '100%'},
            ),
            text='回调日志加载中',
        ),
        fac.AntdModal(
            [
                fac.AntdDescriptions(
                    id='callback-detail-descriptions', column=2, bordered=True
                ),
                fac.AntdDivider('原始回调报文'),
                html.Pre(
                    id='callback-payload-preview',
                    style={
                        'maxHeight': 420,
                        'overflow': 'auto',
                        'whiteSpace': 'pre-wrap',
                    },
                ),
            ],
            id='callback-detail-modal',
            title='回调详情',
            width=880,
            renderFooter=False,
        ),
        fac.AntdModal(
            '是否确认删除选中的回调记录？',
            id='callback-delete-confirm-modal',
            visible=False,
            title='提示',
            renderFooter=True,
        ),
    ]
