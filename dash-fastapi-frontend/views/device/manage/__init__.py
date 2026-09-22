import feffery_antd_components as fac
from dash import dcc, html

from callbacks.device_c import manage_c
from callbacks.device_c.device_page_logic import (
    BIND_LABELS,
    ONLINE_STATUS_LABELS,
    STATUS_LABELS,
)
from utils.permission_util import PermissionManager


STATUS_OPTIONS = [
    {'label': label, 'value': value} for value, label in STATUS_LABELS.items()
]
BIND_OPTIONS = [
    {'label': label, 'value': value} for value, label in BIND_LABELS.items()
]
ONLINE_STATUS_OPTIONS = [
    {'label': label, 'value': value}
    for value, label in ONLINE_STATUS_LABELS.items()
]


def render(*args, **kwargs):
    table_data, table_pagination = manage_c.generate_device_table(
        {'page_num': 1, 'page_size': 10}
    )
    return [
        dcc.Store(id='device-operations-store'),
        dcc.Store(id='device-modal-type-store'),
        dcc.Store(id='device-form-store'),
        dcc.Store(id='device-delete-ids-store'),
        dcc.Store(id='device-detail-store'),
        dcc.Store(id='device-recording-confirm-store'),
        dcc.Store(id='device-command-store'),
        fac.AntdRow(
            fac.AntdCol(
                [
                    fac.AntdForm(
                        fac.AntdSpace(
                            [
                                fac.AntdFormItem(
                                    fac.AntdInput(
                                        id='device-code-search',
                                        placeholder='设备编码/SN',
                                        allowClear=True,
                                        style={'width': 180},
                                    ),
                                    label='设备编码',
                                ),
                                fac.AntdFormItem(
                                    fac.AntdInput(
                                        id='device-name-search',
                                        placeholder='设备名称',
                                        allowClear=True,
                                        style={'width': 150},
                                    ),
                                    label='设备名称',
                                ),
                                fac.AntdFormItem(
                                    fac.AntdSelect(
                                        id='device-status-search',
                                        options=STATUS_OPTIONS,
                                        placeholder='全部状态',
                                        allowClear=True,
                                        style={'width': 130},
                                    ),
                                    label='本地状态',
                                ),
                                fac.AntdFormItem(
                                    fac.AntdSelect(
                                        id='device-online-status-search',
                                        options=ONLINE_STATUS_OPTIONS,
                                        placeholder='全部',
                                        allowClear=True,
                                        style={'width': 130},
                                    ),
                                    label='心跳在线',
                                ),
                                fac.AntdFormItem(
                                    fac.AntdSelect(
                                        id='device-bind-status-search',
                                        options=BIND_OPTIONS,
                                        placeholder='全部',
                                        allowClear=True,
                                        style={'width': 120},
                                    ),
                                    label='绑定状态',
                                ),
                                fac.AntdFormItem(
                                    fac.AntdButton(
                                        '搜索',
                                        id='device-search',
                                        type='primary',
                                        icon=fac.AntdIcon(icon='antd-search'),
                                    )
                                ),
                                fac.AntdFormItem(
                                    fac.AntdButton(
                                        '重置',
                                        id='device-reset',
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
                                '新增设备',
                                id={
                                    'type': 'device-operation-button',
                                    'index': 'add',
                                },
                                icon=fac.AntdIcon(icon='antd-plus'),
                                type='primary',
                            )
                            if PermissionManager.check_perms(
                                'device:manage:add'
                            )
                            else [],
                            fac.AntdButton(
                                '修改',
                                id={
                                    'type': 'device-operation-button',
                                    'index': 'edit',
                                },
                                icon=fac.AntdIcon(icon='antd-edit'),
                                disabled=True,
                            )
                            if PermissionManager.check_perms(
                                'device:manage:edit'
                            )
                            else [],
                            fac.AntdButton(
                                '删除',
                                id={
                                    'type': 'device-operation-button',
                                    'index': 'delete',
                                },
                                icon=fac.AntdIcon(icon='antd-delete'),
                                danger=True,
                                disabled=True,
                            )
                            if PermissionManager.check_perms(
                                'device:manage:remove'
                            )
                            else [],
                            fac.AntdButton(
                                '同步状态',
                                id={
                                    'type': 'device-operation-button',
                                    'index': 'sync',
                                },
                                icon=fac.AntdIcon(icon='antd-cloud-sync'),
                                disabled=True,
                            )
                            if PermissionManager.check_perms(
                                'device:manage:control'
                            )
                            else [],
                            fac.AntdButton(
                                '同步配置',
                                id={
                                    'type': 'device-operation-button',
                                    'index': 'sync-config',
                                },
                                icon=fac.AntdIcon(icon='antd-cloud-download'),
                                disabled=True,
                            )
                            if PermissionManager.check_perms(
                                'device:manage:control'
                            )
                            else [],
                            fac.AntdButton(
                                '指令结果',
                                id={
                                    'type': 'device-operation-button',
                                    'index': 'command',
                                },
                                icon=fac.AntdIcon(icon='antd-file-search'),
                                disabled=True,
                            )
                            if PermissionManager.check_perms(
                                'device:manage:control'
                            )
                            else [],
                            fac.AntdButton(
                                '刷新',
                                id='device-refresh',
                                icon=fac.AntdIcon(icon='antd-sync'),
                            ),
                        ],
                        style={'paddingBottom': '12px'},
                    ),
                    fac.AntdSpin(
                        fac.AntdTable(
                            id='device-list-table',
                            data=table_data,
                            columns=[
                                {
                                    'title': '设备编码',
                                    'dataIndex': 'device_code',
                                },
                                {
                                    'title': '设备名称',
                                    'dataIndex': 'device_name',
                                },
                                {'title': '型号', 'dataIndex': 'model'},
                                {
                                    'title': '固件',
                                    'dataIndex': 'firmware_version',
                                },
                                {
                                    'title': '本地状态',
                                    'dataIndex': 'status_display',
                                },
                                {
                                    'title': '心跳在线',
                                    'dataIndex': 'online_status_display',
                                },
                                {
                                    'title': '绑定状态',
                                    'dataIndex': 'bind_status_display',
                                },
                                {'title': '使用人', 'dataIndex': 'owner_name'},
                                {
                                    'title': '电量',
                                    'dataIndex': 'battery_display',
                                },
                                {
                                    'title': '充电状态',
                                    'dataIndex': 'charged_status_display',
                                },
                                {
                                    'title': '录音状态',
                                    'dataIndex': 'record_status_display',
                                },
                                {
                                    'title': '存储空间',
                                    'dataIndex': 'storage_display',
                                },
                                {
                                    'title': '4G信号',
                                    'dataIndex': 'signal_display',
                                },
                                {
                                    'title': '最后在线',
                                    'dataIndex': 'last_seen_time',
                                    'width': 170,
                                },
                                {
                                    'title': '操作',
                                    'dataIndex': 'operation',
                                    'width': 320,
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
                        text='设备数据加载中',
                    ),
                ],
                span=24,
            ),
            gutter=8,
        ),
        fac.AntdModal(
            fac.AntdForm(
                [
                    fac.AntdFormItem(
                        fac.AntdInput(
                            name='device_code',
                            placeholder='请输入设备唯一编码/SN',
                            allowClear=True,
                        ),
                        label='设备编码',
                        required=True,
                        id={
                            'type': 'device-form-label',
                            'index': 'device_code',
                            'required': True,
                        },
                        hasFeedback=True,
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(name='device_name', allowClear=True),
                        label='设备名称',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(name='model', allowClear=True),
                        label='设备型号',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(name='firmware_version', allowClear=True),
                        label='固件版本',
                    ),
                    fac.AntdFormItem(
                        fac.AntdSelect(name='status', options=STATUS_OPTIONS),
                        label='本地状态',
                    ),
                    fac.AntdFormItem(
                        fac.AntdSelect(
                            name='bind_status', options=BIND_OPTIONS
                        ),
                        label='绑定状态',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(name='owner_name', allowClear=True),
                        label='使用人',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(name='owner_phone', allowClear=True),
                        label='手机号',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(name='department', allowClear=True),
                        label='所属部门',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInputNumber(
                            name='battery_level',
                            min=0,
                            max=100,
                            style={'width': '100%'},
                        ),
                        label='电量(%)',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(name='remark', mode='text-area'),
                        label='备注',
                    ),
                ],
                id='device-form',
                enableBatchControl=True,
                labelCol={'span': 6},
                wrapperCol={'span': 18},
            ),
            id='device-modal',
            width=620,
            mask=False,
            renderFooter=True,
            okClickClose=False,
        ),
        fac.AntdModal(
            [
                fac.AntdDescriptions(
                    id='device-heartbeat-descriptions',
                    column=3,
                    bordered=True,
                ),
                fac.AntdDivider('厂商最新配置快照'),
                fac.AntdDescriptions(
                    id='device-config-descriptions',
                    column=1,
                    bordered=True,
                ),
            ],
            id='device-detail-modal',
            title='设备详情',
            width=980,
            renderFooter=False,
        ),
        fac.AntdModal(
            fac.AntdForm(
                [
                    fac.AntdFormItem(
                        fac.AntdInput(
                            id='device-command-device-code',
                            disabled=True,
                            allowClear=True,
                        ),
                        label='设备编码',
                    ),
                    fac.AntdFormItem(
                        fac.AntdInput(
                            id='device-command-msg-id',
                            placeholder='厂商返回的 msg_id，可留空按最近一条查询',
                            allowClear=True,
                        ),
                        label='消息ID',
                    ),
                ],
                labelCol={'span': 6},
                wrapperCol={'span': 18},
            ),
            id='device-command-modal',
            width=680,
            renderFooter=True,
            okClickClose=False,
            title='指令结果查询',
        ),
        fac.AntdModal(
            [
                fac.AntdAlert(
                    id='device-command-alert',
                    type='info',
                    showIcon=True,
                    visible=False,
                ),
                fac.AntdDescriptions(
                    id='device-command-descriptions',
                    column=2,
                    bordered=True,
                ),
                fac.AntdDivider('最近一次本地控制日志'),
                fac.AntdTable(
                    id='device-command-log-table',
                    columns=[
                        {'title': '指令', 'dataIndex': 'command_display'},
                        {'title': '消息ID', 'dataIndex': 'msg_id'},
                        {
                            'title': '受理结果',
                            'dataIndex': 'request_status_display',
                        },
                        {
                            'title': '厂商状态',
                            'dataIndex': 'remote_status_display',
                        },
                        {
                            'title': '时间',
                            'dataIndex': 'create_time',
                            'width': 170,
                        },
                    ],
                    size='small',
                    bordered=True,
                    pagination=False,
                    style={'width': '100%'},
                ),
            ],
            id='device-command-result-modal',
            title='指令结果',
            width=820,
            renderFooter=False,
        ),
        fac.AntdModal(
            fac.AntdText('是否确认停止该设备的录音？', id='device-stop-text'),
            id='device-stop-confirm-modal',
            visible=False,
            title='停止录音确认',
            renderFooter=True,
            centered=True,
        ),
        fac.AntdModal(
            fac.AntdText('是否确认删除？', id='device-delete-text'),
            id='device-delete-confirm-modal',
            visible=False,
            title='提示',
            renderFooter=True,
            centered=True,
        ),
        html.Div(),
    ]
