"""设备页面纯逻辑测试：筛选参数、展示字段、错误映射与报文脱敏。"""

from callbacks.device_c import device_page_logic as logic


def test_build_device_query_drops_empty_filters():
    """空筛选条件不下发，避免后端把空串当成有效过滤值。"""
    query = logic.build_device_query(
        device_code='',
        device_name=None,
        status=None,
        bind_status='bound',
        page_num=2,
        page_size=30,
    )
    assert query == {'page_num': 2, 'page_size': 30, 'bind_status': 'bound'}


def test_build_device_query_keeps_all_filters():
    query = logic.build_device_query(
        device_code='MLR432',
        device_name='工牌',
        status='online',
        bind_status='bound',
        online_status=2,
        page_num=1,
        page_size=10,
    )
    assert query['device_code'] == 'MLR432'
    assert query['status'] == 'online'
    assert query['online_status'] == 2


def test_build_device_query_keeps_zero_online_status():
    """``online_status=0``（离线）是有效筛选值，不能被当成空值丢弃。"""
    query = logic.build_device_query(online_status=0)
    assert query['online_status'] == 0


HEARTBEAT_DEVICE_ROW = {
    'device_id': 7,
    'device_code': 'MLR432KP42C19568',
    'device_name': '研发工牌',
    'status': 'online',
    'online_status': 2,
    'device_status': 1,
    'bind_status': 'bound',
    'battery_level': 40,
    'signal_strength': 31,
    'record_status': 0,
    'remain_storage': 14833,
    'total_storage': 14950,
    'battery_voltage': 3955,
    'battery_current': 420,
    'charged_status': 'cccv',
    'key_status': 1,
    'usb_status': 1,
    'disk_mount_status': 0,
    'chip': '2108+511',
    'ip_address': '10.113.36.200',
    'today_record_seconds': 120,
    'pending_recordings': 16,
    'tenant_id': 100,
    'audio_id': 'ciqziMsatvo5',
    'last_seen_time': '2025-11-01T19:37:23',
    'heartbeat_time': '2025-11-01T19:37:23',
}


def test_format_device_rows_exposes_full_heartbeat_fields():
    """契约 E6：心跳全字段在列表层可见（缺失字段不隐藏、不报错）。"""
    (row,) = logic.format_device_rows([HEARTBEAT_DEVICE_ROW])
    assert row['key'] == '7'
    assert row['status_display'] == '在线'
    assert row['online_status_display'] == '在线'
    assert row['bind_status_display'] == '已绑定'
    assert row['battery_display'] == '40%'
    assert row['record_status_display'] == '空闲'
    assert row['storage_display'] == '14833/14950 MB'
    assert row['signal_display'] == '31'
    assert row['charged_status_display'] == '充电中'
    assert row['key_status_display'] == '开机'
    assert row['usb_status_display'] == '挂载在工牌'
    assert row['disk_mount_status_display'] == '未挂载'
    assert row['battery_voltage_display'] == '3955 mV'
    assert row['battery_current_display'] == '420 mA'
    assert row['rectd_display'] == '120 s'
    assert row['recnu_display'] == '16'
    assert row['last_seen_time'] == '2025-11-01 19:37:23'


def test_format_device_rows_tolerates_missing_and_unknown_values():
    """厂商取值域未定义（U5/U6/U7）时原样展示，缺失值显示占位符。"""
    rows = logic.format_device_rows(
        [{'device_id': 1, 'charged_status': 'unknown-state'}]
    )
    assert rows[0]['charged_status_display'] == 'unknown-state'
    assert rows[0]['battery_display'] == '-'
    assert rows[0]['storage_display'] == '-'
    assert rows[0]['status_display'] == '-'


DETAIL_LABELS = (
    '设备编码(SN)',
    '心跳 online',
    '心跳 device_status',
    '录音状态',
    '剩余电量',
    '电池电压',
    '电池电流',
    '充电状态',
    '4G 信号(rssi)',
    '剩余存储',
    '总存储',
    '磁盘挂载',
    '开关键',
    'USB 状态',
    '芯片',
    '基站 IP',
    '当日录音时长',
    '待上传录音数',
    '租户 ID',
    '录音标识(nm)',
    '最后在线',
    '心跳上报时间',
)


def test_build_device_detail_items_covers_heartbeat_dictionary():
    """契约 §3 的心跳字段字典必须全部出现在详情层。"""
    items = logic.build_device_detail_items(HEARTBEAT_DEVICE_ROW)
    labels = [item['label'] for item in items]
    for label in DETAIL_LABELS:
        assert label in labels, f'详情缺少心跳字段：{label}'
    values = {item['label']: item['children'] for item in items}
    assert values['4G 信号(rssi)'] == '31'
    assert values['充电状态'] == '充电中'
    assert values['租户 ID'] == '100'


def test_build_device_config_items_keeps_undefined_snapshot_raw():
    """契约 U3：``config_data`` 结构未定义，只做原样展示。"""
    items = logic.build_device_config_items(
        {
            'config_json': '{"a": 1}',
            'config_last_upload_time': '2025-11-01T19:37:23',
            'config_synced_at': '2025-11-02T08:00:00',
        }
    )
    values = {item['label']: item['children'] for item in items}
    assert '{\n  "a": 1\n}' == values['配置快照']
    assert values['配置上传时间'] == '2025-11-01 19:37:23'
    assert values['本地同步时间'] == '2025-11-02 08:00:00'


def test_summarize_batch_result_reports_partial_failure():
    """契约 §4.6 R2/R3：优先消费后端逐台结果，部分失败必须显式提示。"""
    summary = logic.summarize_batch_result(
        ['A', 'B', 'C'],
        {
            'total': 3,
            'succeeded': 2,
            'failed': 1,
            'skipped': 0,
            'partial': True,
            'synced': 2,
            'results': [
                {'sn': 'A', 'ok': True, 'skipped': False},
                {'sn': 'B', 'ok': True, 'skipped': False},
                {
                    'sn': 'C',
                    'ok': False,
                    'skipped': False,
                    'error': '厂商未返回该设备',
                },
            ],
        },
        '同步状态',
    )
    assert summary['level'] == 'warning'
    assert '部分失败' in summary['message']
    assert '2/3' in summary['message']
    assert '厂商未返回该设备' in summary['message']
    assert summary['missing'] == ['C']


def test_summarize_batch_result_uses_legacy_entity_diff_when_results_absent():
    """兼容旧版后端：没有逐台 results 时退回"请求 SN − 返回 entities"口径。"""
    summary = logic.summarize_batch_result(
        ['A', 'B'],
        {'entities': [{'sn': 'A'}], 'synced': 1},
        '同步状态',
    )
    assert summary['level'] == 'warning'
    assert summary['missing'] == ['B']


def test_summarize_batch_result_distinguishes_skipped_from_failed():
    """契约 §4.6 R5：配置同步时本地没有该设备应记为跳过，而不是失败。"""
    skipped_only = logic.summarize_batch_result(
        ['A', 'B'],
        {
            'total': 2,
            'succeeded': 1,
            'failed': 0,
            'skipped': 1,
            'results': [
                {'sn': 'A', 'ok': True, 'skipped': False},
                {
                    'sn': 'B',
                    'ok': False,
                    'skipped': True,
                    'error': '本地台账中不存在该设备',
                },
            ],
        },
        '同步配置',
    )
    assert skipped_only['level'] == 'warning'
    assert '1 台跳过' in skipped_only['message']
    assert skipped_only['missing'] == []
    assert skipped_only['skipped'] == ['B']


def test_summarize_batch_result_all_and_none():
    all_ok = logic.summarize_batch_result(
        ['A'],
        {
            'total': 1,
            'succeeded': 1,
            'failed': 0,
            'skipped': 0,
            'results': [{'sn': 'A', 'ok': True, 'skipped': False}],
        },
        '同步配置',
    )
    assert all_ok['level'] == 'success'
    assert '1/1' in all_ok['message']

    none_ok = logic.summarize_batch_result(
        ['A', 'B'],
        {
            'total': 2,
            'succeeded': 0,
            'failed': 2,
            'skipped': 0,
            'results': [
                {'sn': 'A', 'ok': False, 'skipped': False, 'error': '超时'},
                {'sn': 'B', 'ok': False, 'skipped': False, 'error': '超时'},
            ],
        },
        '同步配置',
    )
    assert none_ok['level'] == 'error'
    assert '全部失败' in none_ok['message']
    assert none_ok['missing'] == ['A', 'B']

    empty = logic.summarize_batch_result([], {}, '同步配置')
    assert empty['level'] == 'warning'


def test_classify_error_maps_backend_semantics():
    """契约 E10 / C10：无权限、超时、登录失效各有专门措辞。"""
    permission = logic.classify_error('该用户无此接口权限', '同步状态')
    assert permission['level'] == 'permission'
    assert '无此操作权限' in permission['message']

    timeout = logic.classify_error('请求超时', '开启录音')
    assert timeout['level'] == 'timeout'
    assert '超时' in timeout['message']

    auth = logic.classify_error('登录状态已过期', '删除设备')
    assert auth['level'] == 'auth'
    assert '重新登录' in auth['message']

    other = logic.classify_error('设备编码 MLR1 已存在', '新增设备')
    assert other['level'] == 'error'
    assert 'MLR1 已存在' in other['message']


def test_sanitize_payload_masks_secrets_and_signed_urls():
    """契约 E9：页面不得泄露密钥、token 或带签名的下载地址。"""
    payload = {
        'sn': 'MLR1',
        'access_token': 'abc123',
        'nested': {'password': 'p@ss', 'clientSecret': 'shh'},
        'files': [{'download_url': 'https://cdn/x.mp3?sign=SECRET&t=1'}],
    }
    sanitized = logic.sanitize_payload(payload)
    assert sanitized['access_token'] == logic.MASK_TEXT
    assert sanitized['nested']['password'] == logic.MASK_TEXT
    assert sanitized['nested']['clientSecret'] == logic.MASK_TEXT
    assert 'SECRET' not in sanitized['files'][0]['download_url']
    assert 'sign=***' in sanitized['files'][0]['download_url']
    assert sanitized['sn'] == 'MLR1'


def test_format_payload_pretty_prints_and_masks():
    text = logic.format_payload(
        '{"token": "t0k3n", "sn": "MLR1", "files": [{"download_url": "https://c/x?sign=s1"}]}'
    )
    assert 't0k3n' not in text
    assert '"sn": "MLR1"' in text
    assert 'sign=***' in text
    assert text.startswith('{\n')


def test_format_payload_returns_raw_text_when_not_json():
    assert logic.format_payload('not-json') == 'not-json'
    assert logic.format_payload(None) == ''


def test_mask_secret_text_redacts_inline_credentials():
    masked = logic.mask_secret_text('厂商返回 password=abc123, token: t-9 失败')
    assert 'abc123' not in masked
    assert 't-9' not in masked


def test_build_callback_query_requires_both_time_bounds():
    """时间范围只有一端的残缺输入不下发（后端按闭区间解析）。"""
    partial = logic.build_callback_query(
        device_code='MLR1', time_range=['2025-11-01', None]
    )
    assert 'begin_time' not in partial

    full = logic.build_callback_query(
        device_code='MLR1',
        event_type='rec',
        process_status='failed',
        duplicate_flag='Y',
        time_range=['2025-11-01', '2025-11-02'],
    )
    assert full['event_type'] == 'rec'
    assert full['process_status'] == 'failed'
    assert full['duplicate_flag'] == 'Y'
    assert full['begin_time'] == '2025-11-01'
    assert full['end_time'] == '2025-11-02'


def test_format_callback_rows_labels_process_status():
    rows = logic.format_callback_rows(
        [
            {
                'callback_id': 3,
                'process_status': 'rejected',
                'duplicate_flag': 'Y',
                'received_at': '2025-11-01T10:00:00',
            }
        ]
    )
    assert rows[0]['key'] == '3'
    assert rows[0]['process_status_display'] == '已拒绝'
    assert rows[0]['duplicate_display'] == '重复'
    assert rows[0]['received_at'] == '2025-11-01 10:00:00'


def test_build_callback_detail_items_separates_idempotency_info():
    """契约 E8：详情要能看出幂等键、重复标记与处理信息。"""
    event_items, process_items = logic.build_callback_detail_items(
        {
            'event_type': 'heartbeat',
            'device_code': 'MLR1',
            'event_id': None,
            'dedup_key': 'a' * 64,
            'duplicate_flag': 'N',
            'signature_valid': 'Y',
            'session_id': 61318,
            'topic_name': 'hermes',
            'item_count': 2,
            'process_status': 'success',
            'error_message': None,
        }
    )
    event_values = {item['label']: item['children'] for item in event_items}
    process_values = {item['label']: item['children'] for item in process_items}
    assert event_values['事件类型'] == 'heartbeat'
    assert process_values['处理结果'] == '成功'
    assert process_values['是否重复'] == '首次'
    assert process_values['签名校验'] == '通过'
    assert process_values['幂等键'] == 'a' * 64
    assert process_values['回调配置 ID'] == '61318'
    assert process_values['条目数'] == '2'


def test_control_action_buttons_follow_permissions():
    """前端按钮显隐与后端守卫使用同一组权限标识（契约 §5.6 E1–E5）。"""
    assert logic.control_action_buttons(
        ['*:*:*']
    ) == logic.control_action_buttons(
        [
            'device:manage:query',
            'device:manage:control',
            'device:manage:edit',
            'device:manage:remove',
        ]
    )
    readonly = logic.control_action_buttons(['device:manage:query'])
    assert [item['content'] for item in readonly] == ['详情']
    write_only = logic.control_action_buttons(['device:manage:edit'])
    assert [item['content'] for item in write_only] == ['修改', '状态维护']
    control_only = logic.control_action_buttons(['device:manage:control'])
    assert [item['content'] for item in control_only] == [
        '开始录音',
        '停止录音',
        '指令结果',
    ]


def test_extract_msg_id():
    """契约 C8：录音指令返回的 msg_id 要能串起指令结果查询。"""
    assert logic.extract_msg_id({'msg_id': 'm-1'}) == 'm-1'
    assert logic.extract_msg_id({}) is None
    assert logic.extract_msg_id(None) is None


def test_command_result_items_merges_remote_and_local():
    """契约 §4.7：`{remote, remote_error, local}` 合并展示，且不泄露敏感值。"""
    items = logic.command_result_items(
        {
            'remote': {
                'sn': 'MLR1',
                'cmd': 'start/shadow',
                'msg_id': 'm-1',
                'status': 3,
                'req_time': '2025-11-01T10:00:00',
                'resp_time': '2025-11-01T10:00:04',
                'response': {'token': 'leak-me'},
            },
            'remote_error': None,
            'local': {
                'device_code': 'MLR1',
                'command': 'start_recording',
                'msg_id': 'm-1',
                'request_status': 'success',
                'remote_status': 0,
                'payload_json': '{"sn": "MLR1"}',
            },
        }
    )
    values = {item['label']: item['children'] for item in items}
    assert values['厂商状态'] == '返回超时'
    assert values['本地受理结果'] == '已受理'
    assert values['请求时间'] == '2025-11-01 10:00:00'
    assert 'leak-me' not in values['厂商响应']


def test_command_result_items_tolerates_missing_remote():
    """厂商侧暂无记录时，用本地控制日志交代结果并保留厂商错误说明。"""
    items = logic.command_result_items(
        {
            'remote': None,
            'remote_error': '指令接收日志不存在',
            'local': {
                'device_code': 'MLR1',
                'command': 'stop_recording',
                'msg_id': 'm-2',
                'request_status': 'failed',
                'error_message': '厂商超时',
            },
        }
    )
    values = {item['label']: item['children'] for item in items}
    assert values['指令'] == '停止录音'
    assert values['厂商状态'] == '-'
    assert values['厂商回执说明'] == '指令接收日志不存在'
    assert values['错误信息'] == '厂商超时'
    assert values['本地受理结果'] == '失败'


def test_command_result_alert_levels():
    assert logic.command_result_alert(
        {'remote': {'status': 1}, 'local': None}
    ) == {'type': 'info', 'text': '厂商回执状态：成功'}
    fallback = logic.command_result_alert(
        {'remote': None, 'remote_error': '记录不存在', 'local': {'msg_id': 'm'}}
    )
    assert fallback['type'] == 'warning'
    assert '本地控制日志' in fallback['text']
    assert logic.command_result_alert({})['type'] == 'error'
