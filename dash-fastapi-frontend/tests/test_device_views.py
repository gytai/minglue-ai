"""设备页面组件级测试：按钮按权限渲染、弹窗与表格列齐备。

这里用测试桩组件（见 conftest）真实调用页面 ``render()``，验证"前端显示"这一层：
无权限的按钮根本不渲染，有权限的按钮与详情/指令结果弹窗都在页面上。
"""

import pytest

from callbacks.device_c import callback_log_c, manage_c
from views.device import callback_log, manage


DEVICE_PAGE_ROW = {
    'device_id': 1,
    'device_code': 'MLR1',
    'device_name': '工牌',
    'status': 'online',
}


def collect_text(node, sink=None):
    """深度遍历测试桩组件树，收集所有文本节点。"""
    if sink is None:
        sink = []
    if isinstance(node, str):
        sink.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            collect_text(value, sink)
    elif isinstance(node, (list, tuple)):
        for item in node:
            collect_text(item, sink)
    elif hasattr(node, 'children'):
        for child in node.children:
            collect_text(child, sink)
        for value in getattr(node, 'kwargs', {}).values():
            collect_text(value, sink)
    return sink


def collect_type_texts(node, type_name, sink=None):
    """收集指定类型组件内部的文本，用于区分按钮与弹窗标题。"""
    if sink is None:
        sink = []
    if isinstance(node, (list, tuple)):
        for item in node:
            collect_type_texts(item, type_name, sink)
    elif isinstance(node, dict):
        for value in node.values():
            collect_type_texts(value, type_name, sink)
    elif hasattr(node, 'children'):
        if type(node).__name__ == type_name:
            collect_text(node, sink)
            return sink
        for child in node.children:
            collect_type_texts(child, type_name, sink)
        for value in getattr(node, 'kwargs', {}).values():
            collect_type_texts(value, type_name, sink)
    return sink


@pytest.fixture
def render_pages(monkeypatch):
    def list_devices(_query):
        return {
            'rows': [dict(DEVICE_PAGE_ROW)],
            'page_num': 1,
            'page_size': 10,
            'total': 1,
        }

    def list_callbacks(_query):
        return {
            'rows': [
                {
                    'callback_id': 1,
                    'event_type': 'heartbeat',
                    'device_code': 'MLR1',
                    'process_status': 'success',
                    'received_at': '2025-11-01T10:00:00',
                    'duplicate_flag': 'N',
                }
            ],
            'page_num': 1,
            'page_size': 10,
            'total': 1,
        }

    monkeypatch.setattr(
        manage_c.DeviceApi, 'list_devices', staticmethod(list_devices)
    )
    monkeypatch.setattr(
        callback_log_c.DeviceApi,
        'list_callbacks',
        staticmethod(list_callbacks),
    )

    def _render():
        return manage.render(), callback_log.render()

    return _render


def test_device_page_renders_only_granted_buttons(
    session_context, render_pages
):
    """只有查询权限时，页面不应渲染新增/删除/控制类按钮（与后端守卫一致）。"""
    session_context.login(perms=['device:manage:query', 'device:manage:list'])
    page = render_pages()[0]
    buttons = collect_type_texts(page, 'AntdButton')
    for label in (
        '新增设备',
        '修改',
        '删除',
        '同步状态',
        '同步配置',
        '指令结果',
    ):
        assert label not in buttons, f'无权限时仍渲染了按钮：{label}'
    assert '刷新' in buttons


def test_device_page_renders_all_controls_with_full_permissions(
    session_context, render_pages
):
    session_context.login(perms=None)
    page = render_pages()[0]
    buttons = collect_type_texts(page, 'AntdButton')
    for label in (
        '新增设备',
        '修改',
        '删除',
        '同步状态',
        '同步配置',
        '指令结果',
        '刷新',
    ):
        assert label in buttons, f'缺少按钮：{label}'
    # 详情 / 指令结果 / 状态维护 / 停止录音确认弹窗都必须挂载在页面上。
    modals = collect_type_texts(page, 'AntdModal')
    for title in (
        '设备详情',
        '指令结果查询',
        '指令结果',
        '状态维护',
        '停止录音确认',
    ):
        assert title in modals, f'缺少弹窗：{title}'


def test_device_page_table_columns_cover_heartbeat_telemetry(
    session_context, render_pages
):
    """契约 E6：列表层至少覆盖电量/充电/录音/存储/信号/最后在线等心跳字段。"""
    session_context.login(perms=None)
    texts = collect_text(render_pages()[0])
    for title in (
        '电量',
        '充电状态',
        '录音状态',
        '存储空间',
        '4G信号',
        '最后在线',
        '心跳在线',
    ):
        assert title in texts, f'列表缺少列：{title}'


def test_callback_page_renders_filters_and_detail(
    session_context, render_pages
):
    """契约 E7：回调日志页面提供设备/类型/处理结果/时间/重复标记筛选。"""
    session_context.login(perms=None)
    texts = collect_text(render_pages()[1])
    for label in ('设备编码', '事件类型', '处理结果', '是否重复', '接收时间'):
        assert label in texts, f'缺少筛选条件：{label}'
    assert '原始回调报文（已脱敏）' in texts
    assert '幂等与处理信息' in texts


def test_callback_page_hides_delete_without_permission(
    session_context, render_pages
):
    session_context.login(perms=['device:callback:list'])
    texts = collect_text(render_pages()[1])
    assert '删除' not in texts
    assert '刷新' in texts
