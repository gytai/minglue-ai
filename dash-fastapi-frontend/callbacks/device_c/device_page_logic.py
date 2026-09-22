"""设备相关页面的纯逻辑层。

本模块**不依赖 dash / feffery_antd_components**，只处理查询参数拼装、列表与详情
展示格式化、后端错误语义到前端反馈的映射，以及原始报文的脱敏与格式化。把逻辑从
回调模块里拆出来，既避免回调文件继续膨胀，也让这些规则可以在没有前端运行时依赖
的环境里直接做单元测试（见 ``dash-fastapi-frontend/tests/``）。
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

#: 心跳/状态字典（契约 §3）到中文展示的映射。未命中的取值原样返回，
#: 因为厂商对部分字段（如 ``charged_status``、``upload.status``）未给出完整取值域。
STATUS_LABELS = {'online': '在线', 'offline': '离线', 'disabled': '已停用'}
BIND_LABELS = {'bound': '已绑定', 'unbound': '未绑定'}
ONLINE_STATUS_LABELS = {2: '在线', 1: '开机未在线', 0: '离线'}
DEVICE_STATUS_LABELS = {1: '正常在线', 0: '离线'}
RECORD_STATUS_LABELS = {1: '录音中', 0: '空闲'}
KEY_STATUS_LABELS = {1: '开机', 0: '关机'}
USB_STATUS_LABELS = {1: '挂载在工牌', 0: '挂载在别处'}
DISK_MOUNT_LABELS = {1: '已挂载', 0: '未挂载'}
CHARGED_STATUS_LABELS = {
    'charge-off': '放电中',
    'cccv': '充电中',
    'finished': '已充满',
    'finish': '已充满',
}
#: 回调处理结果。``rejected`` 是签名校验失败留痕，``duplicate`` 为幂等命中的重复回调。
PROCESS_STATUS_LABELS = {
    'success': '成功',
    'failed': '失败',
    'rejected': '已拒绝',
    'duplicate': '重复',
}
#: 厂商指令状态（契约 §4.5）。
COMMAND_STATUS_LABELS = {
    0: '初始化',
    1: '成功',
    2: '返回出错',
    3: '返回超时',
}
#: 厂商回调的 8 类事件（契约 §2）。
CALLBACK_EVENT_TYPES = [
    'rec',
    'op',
    'sys',
    'reclist',
    'upload',
    'fc',
    'asr',
    'heartbeat',
]

#: 请求体/报文中不允许出现在页面上的敏感键（大小写不敏感、按子串匹配）。
SENSITIVE_KEYWORDS = (
    'password',
    'passwd',
    'pwd',
    'secret',
    'token',
    'authorization',
    'signature',
    'credential',
    'accesskey',
    'access_key',
    'appkey',
    'app_key',
    'privatekey',
    'private_key',
)
#: URL 查询串里的签名类参数，取值带有效期，同样不应展示。
SENSITIVE_QUERY_KEYS = (
    'signature',
    'sign',
    'token',
    'accesskey',
    'access_key',
    'x-signature',
)
MASK_TEXT = '***'


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower()
    return any(word in normalized for word in SENSITIVE_KEYWORDS)


def _mask_url(value: str) -> str:
    """把 URL 查询串中的签名参数替换为掩码。"""
    if '?' not in value:
        return value
    head, _, query = value.partition('?')
    parts = []
    for chunk in query.split('&'):
        name, sep, _ = chunk.partition('=')
        if sep and name.lower() in SENSITIVE_QUERY_KEYS:
            parts.append(f'{name}={MASK_TEXT}')
        else:
            parts.append(chunk)
    return f'{head}?{"&".join(parts)}'


def sanitize_payload(value: Any, _depth: int = 0) -> Any:
    """递归脱敏报文，供回调详情展示。

    命中 :data:`SENSITIVE_KEYWORDS` 的键一律替换为掩码；字符串中的签名 URL 只保留
    参数名。嵌套超过 8 层时原样返回，避免恶意报文造成无限递归。
    """
    if _depth > 8:
        return value
    if isinstance(value, dict):
        return {
            key: MASK_TEXT
            if _is_sensitive_key(key)
            else sanitize_payload(item, _depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item, _depth + 1) for item in value]
    if isinstance(value, str):
        return _mask_url(value)
    return value


def format_payload(payload_json: Optional[str]) -> str:
    """把落库的原始报文格式化为缩进 JSON；非 JSON 内容原样返回。"""
    if not payload_json:
        return ''
    if isinstance(payload_json, (dict, list)):
        payload = payload_json
    else:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            return str(payload_json)
    return json.dumps(
        sanitize_payload(payload), ensure_ascii=False, indent=2, default=str
    )


def build_device_query(
    device_code: Optional[str] = None,
    device_name: Optional[str] = None,
    status: Optional[str] = None,
    bind_status: Optional[str] = None,
    online_status: Optional[int] = None,
    page_num: int = 1,
    page_size: int = 10,
) -> Dict[str, Any]:
    """拼装设备列表查询参数，空条件不下发。"""
    query: Dict[str, Any] = {'page_num': page_num, 'page_size': page_size}
    if device_code:
        query['device_code'] = device_code
    if device_name:
        query['device_name'] = device_name
    if status:
        query['status'] = status
    if bind_status:
        query['bind_status'] = bind_status
    if online_status is not None:
        query['online_status'] = online_status
    return query


def _display(value: Any, suffix: str = '') -> str:
    return f'{value}{suffix}' if value is not None else '-'


def _label(mapping: Dict[Any, str], value: Any) -> str:
    if value is None:
        return '-'
    return mapping.get(value, str(value))


def format_device_rows(
    rows: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """补齐设备列表的展示字段；原始字段保持不变。"""
    formatted = []
    for row in rows or []:
        item = dict(row)
        item['key'] = str(item.get('device_id'))
        item['status_display'] = _label(STATUS_LABELS, item.get('status'))
        item['online_status_display'] = _label(
            ONLINE_STATUS_LABELS, item.get('online_status')
        )
        item['bind_status_display'] = _label(
            BIND_LABELS, item.get('bind_status')
        )
        item['battery_display'] = _display(item.get('battery_level'), '%')
        item['record_status_display'] = _label(
            RECORD_STATUS_LABELS, item.get('record_status')
        )
        item['storage_display'] = (
            f'{item["remain_storage"]}/{item["total_storage"]} MB'
            if item.get('remain_storage') is not None
            and item.get('total_storage') is not None
            else '-'
        )
        item['signal_display'] = _display(item.get('signal_strength'))
        item['charged_status_display'] = _label(
            CHARGED_STATUS_LABELS, item.get('charged_status')
        )
        item['key_status_display'] = _label(
            KEY_STATUS_LABELS, item.get('key_status')
        )
        item['usb_status_display'] = _label(
            USB_STATUS_LABELS, item.get('usb_status')
        )
        item['disk_mount_status_display'] = _label(
            DISK_MOUNT_LABELS, item.get('disk_mount_status')
        )
        item['battery_voltage_display'] = _display(
            item.get('battery_voltage'), ' mV'
        )
        item['battery_current_display'] = _display(
            item.get('battery_current'), ' mA'
        )
        item['rectd_display'] = _display(item.get('today_record_seconds'), ' s')
        item['recnu_display'] = _display(item.get('pending_recordings'))
        item['last_seen_time'] = format_time(item.get('last_seen_time'))
        item['heartbeat_time'] = format_time(item.get('heartbeat_time'))
        formatted.append(item)
    return formatted


def format_time(value: Any) -> Any:
    """复用后端时间字符串口径；datetime 转 ``Y-m-d H:i:s``。"""
    if value in (None, ''):
        return value
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    text = str(value)
    return text.replace('T', ' ')


def build_device_detail_items(detail: Dict[str, Any]) -> List[Dict[str, str]]:
    """设备详情：完整心跳字段字典（契约 §3）按语义分组展示。"""
    detail = detail or {}
    return [
        {'label': '设备编码(SN)', 'children': detail.get('device_code') or '-'},
        {'label': '设备名称', 'children': detail.get('device_name') or '-'},
        {'label': '设备型号', 'children': detail.get('model') or '-'},
        {
            'label': '固件版本',
            'children': detail.get('firmware_version') or '-',
        },
        {
            'label': '本地状态',
            'children': _label(STATUS_LABELS, detail.get('status')),
        },
        {
            'label': '心跳 online',
            'children': _label(
                ONLINE_STATUS_LABELS, detail.get('online_status')
            ),
        },
        {
            'label': '心跳 device_status',
            'children': _label(
                DEVICE_STATUS_LABELS, detail.get('device_status')
            ),
        },
        {
            'label': '绑定状态',
            'children': _label(BIND_LABELS, detail.get('bind_status')),
        },
        {'label': '使用人', 'children': detail.get('owner_name') or '-'},
        {'label': '手机号', 'children': detail.get('owner_phone') or '-'},
        {'label': '所属部门', 'children': detail.get('department') or '-'},
        {
            'label': '录音状态',
            'children': _label(
                RECORD_STATUS_LABELS, detail.get('record_status')
            ),
        },
        {
            'label': '剩余电量',
            'children': _display(detail.get('battery_level'), '%'),
        },
        {
            'label': '电池电压',
            'children': _display(detail.get('battery_voltage'), ' mV'),
        },
        {
            'label': '电池电流',
            'children': _display(detail.get('battery_current'), ' mA'),
        },
        {
            'label': '充电状态',
            'children': _label(
                CHARGED_STATUS_LABELS, detail.get('charged_status')
            ),
        },
        {
            'label': '4G 信号(rssi)',
            'children': _display(detail.get('signal_strength')),
        },
        {
            'label': '剩余存储',
            'children': _display(detail.get('remain_storage'), ' MB'),
        },
        {
            'label': '总存储',
            'children': _display(detail.get('total_storage'), ' MB'),
        },
        {
            'label': '磁盘挂载',
            'children': _label(
                DISK_MOUNT_LABELS, detail.get('disk_mount_status')
            ),
        },
        {
            'label': '开关键',
            'children': _label(KEY_STATUS_LABELS, detail.get('key_status')),
        },
        {
            'label': 'USB 状态',
            'children': _label(USB_STATUS_LABELS, detail.get('usb_status')),
        },
        {'label': '芯片', 'children': detail.get('chip') or '-'},
        {'label': '基站 IP', 'children': detail.get('ip_address') or '-'},
        {
            'label': '当日录音时长',
            'children': _display(detail.get('today_record_seconds'), ' s'),
        },
        {
            'label': '待上传录音数',
            'children': _display(detail.get('pending_recordings')),
        },
        {
            'label': '租户 ID',
            'children': _display(detail.get('tenant_id')),
        },
        {'label': '录音标识(nm)', 'children': detail.get('audio_id') or '-'},
        {
            'label': '最后在线',
            'children': format_time(detail.get('last_seen_time')) or '-',
        },
        {
            'label': '心跳上报时间',
            'children': format_time(detail.get('heartbeat_time')) or '-',
        },
        {
            'label': '配置同步时间',
            'children': format_time(detail.get('config_synced_at')) or '-',
        },
        {
            'label': '配置上传时间',
            'children': format_time(detail.get('config_last_upload_time'))
            or '-',
        },
        {
            'label': '激活时间',
            'children': format_time(detail.get('activated_at')) or '-',
        },
        {'label': '备注', 'children': detail.get('remark') or '-'},
    ]


def build_device_config_items(detail: Dict[str, Any]) -> List[Dict[str, str]]:
    """设备详情：厂商最新配置快照。

    契约 §4.2 / U3 明确 ``config_data`` 结构未定义，因此只做原样 JSON 展示。
    """
    detail = detail or {}
    return [
        {
            'label': '配置快照',
            'children': format_payload(detail.get('config_json')) or '暂无',
        },
        {
            'label': '配置上传时间',
            'children': format_time(detail.get('config_last_upload_time'))
            or '-',
        },
        {
            'label': '本地同步时间',
            'children': format_time(detail.get('config_synced_at')) or '-',
        },
    ]


def summarize_batch_result(
    requested: List[str],
    result: Optional[Dict[str, Any]],
    action: str,
) -> Dict[str, Any]:
    """批量同步的逐设备结果归纳，产出"成功/部分失败/全部失败"反馈。

    契约 §4.6 R1–R3：后端对每台设备独立提交，并在 ``results`` 里逐台给出
    ``ok``/``skipped``/``error``，同时汇总 ``succeeded``/``failed``/``skipped``/
    ``partial``。前端优先消费这些字段，能区分"处理失败"与"按规则跳过"（例如配置
    同步时本地台账没有该设备）。若后端响应里没有 ``results``（旧版本），退回
    "请求 SN − 返回 entities"的差集口径，保证不会把部分失败展示成完全成功。
    """
    requested = [item for item in (requested or []) if item]
    payload = result if isinstance(result, dict) else {}
    results = payload.get('results')
    if isinstance(results, list) and results:
        failed = [
            item
            for item in results
            if isinstance(item, dict)
            and not item.get('ok')
            and not item.get('skipped')
        ]
        skipped = [
            item
            for item in results
            if isinstance(item, dict) and item.get('skipped')
        ]
        total = payload.get('total') or len(requested) or len(results)
        succeeded = payload.get('succeeded')
        if succeeded is None:
            succeeded = total - len(failed) - len(skipped)
        failed_codes = [str(item.get('sn') or '未知设备') for item in failed]
        skipped_codes = [str(item.get('sn') or '未知设备') for item in skipped]
        detail = next(
            (str(item.get('error')) for item in failed if item.get('error')),
            '',
        )
        if not failed and not skipped:
            return {
                'level': 'success',
                'message': f'{action}成功：{succeeded}/{total} 台设备已更新',
                'missing': [],
                'skipped': [],
            }
        if not failed:
            return {
                'level': 'warning',
                'message': (
                    f'{action}完成：{succeeded}/{total} 台已更新，'
                    f'{len(skipped)} 台跳过（{"、".join(skipped_codes)}）'
                ),
                'missing': [],
                'skipped': skipped_codes,
            }
        if not succeeded:
            return {
                'level': 'error',
                'message': (
                    f'{action}全部失败：0/{total} 台成功；'
                    f'失败：{"、".join(failed_codes)}'
                    + (f'（{detail}）' if detail else '')
                ),
                'missing': failed_codes,
                'skipped': skipped_codes,
            }
        return {
            'level': 'warning',
            'message': (
                f'{action}部分失败：{succeeded}/{total} 台成功；'
                f'失败：{"、".join(failed_codes)}'
                + (f'（{detail}）' if detail else '')
                + (
                    f'；跳过：{"、".join(skipped_codes)}'
                    if skipped_codes
                    else ''
                )
            ),
            'missing': failed_codes,
            'skipped': skipped_codes,
        }

    entities = payload.get('entities')
    returned = set()
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict) and entity.get('sn'):
                returned.add(str(entity['sn']))
    missing = [sn for sn in requested if sn not in returned]
    total = len(requested)
    ok_count = total - len(missing)
    if not requested:
        return {
            'level': 'warning',
            'message': f'{action}未选择设备',
            'missing': [],
            'skipped': [],
        }
    if not missing:
        return {
            'level': 'success',
            'message': f'{action}成功：{ok_count}/{total} 台设备已更新',
            'missing': [],
            'skipped': [],
        }
    if ok_count == 0:
        return {
            'level': 'error',
            'message': (
                f'{action}全部失败：0/{total} 台设备返回数据；'
                f'未返回：{"、".join(missing)}'
            ),
            'missing': missing,
            'skipped': [],
        }
    return {
        'level': 'warning',
        'message': (
            f'{action}部分失败：{ok_count}/{total} 台成功；'
            f'未返回：{"、".join(missing)}'
        ),
        'missing': missing,
        'skipped': [],
    }


#: 后端错误语义 → 前端反馈等级（契约 C10 / E10）。
ERROR_KEYWORDS = (
    ('无此接口权限', 'permission'),
    ('无权限', 'permission'),
    ('令牌', 'auth'),
    ('登录', 'auth'),
    ('超时', 'timeout'),
    ('timeout', 'timeout'),
    ('timed out', 'timeout'),
    ('部分', 'partial'),
)


def classify_error(
    exc: Union[BaseException, str, None],
    action: str,
    default_message: str = '操作失败',
) -> Dict[str, str]:
    """把后端异常/错误文案映射为可读反馈。

    后端 ``utils/request.py`` 已把 HTTP 层失败统一转成自定义异常，``exceptions``
    的 ``PermissionException`` 会带上"该用户无此接口权限"。这里按关键字归类，
    保证加载、超时、无权限、部分失败在页面上都有明确措辞且不泄露底层报文。
    """
    if exc is None:
        detail = ''
    elif isinstance(exc, str):
        detail = exc
    else:
        detail = getattr(exc, 'message', None) or str(exc) or ''
    detail = str(detail).strip()
    matched = 'error'
    for keyword, level in ERROR_KEYWORDS:
        if keyword and keyword.lower() in detail.lower():
            matched = level
            break
    messages = {
        'permission': f'{action}失败：当前账号无此操作权限',
        'auth': f'{action}失败：登录状态已失效，请重新登录',
        'timeout': f'{action}超时：厂商服务响应过慢，请稍后重试',
    }
    message = messages.get(
        matched, f'{action}失败：{detail or default_message}'
    )
    return {'level': matched, 'message': message}


def build_control_log_query(
    device_code: Optional[str] = None,
    command: Optional[str] = None,
    request_status: Optional[str] = None,
    msg_id: Optional[str] = None,
    page_num: int = 1,
    page_size: int = 10,
) -> Dict[str, Any]:
    """控制指令日志查询参数。"""
    query: Dict[str, Any] = {'page_num': page_num, 'page_size': page_size}
    if device_code:
        query['device_code'] = device_code
    if command:
        query['command'] = command
    if request_status:
        query['request_status'] = request_status
    if msg_id:
        query['msg_id'] = msg_id
    return query


COMMAND_LABELS = {
    'start_recording': '开启录音',
    'stop_recording': '停止录音',
}
REQUEST_STATUS_LABELS = {'success': '已受理', 'failed': '失败'}


def format_control_rows(
    rows: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    formatted = []
    for row in rows or []:
        item = dict(row)
        item['key'] = str(item.get('control_id'))
        item['command_display'] = _label(COMMAND_LABELS, item.get('command'))
        item['request_status_display'] = _label(
            REQUEST_STATUS_LABELS, item.get('request_status')
        )
        item['remote_status_display'] = _label(
            COMMAND_STATUS_LABELS, item.get('remote_status')
        )
        item['create_time'] = format_time(item.get('create_time'))
        formatted.append(item)
    return formatted


def build_callback_query(
    device_code: Optional[str] = None,
    event_type: Optional[str] = None,
    process_status: Optional[str] = None,
    duplicate_flag: Optional[str] = None,
    time_range: Optional[List[str]] = None,
    page_num: int = 1,
    page_size: int = 10,
) -> Dict[str, Any]:
    """回调日志查询参数：设备、类型、时间、处理结果、重复标记。"""
    query: Dict[str, Any] = {'page_num': page_num, 'page_size': page_size}
    if device_code:
        query['device_code'] = device_code
    if event_type:
        query['event_type'] = event_type
    if process_status:
        query['process_status'] = process_status
    if duplicate_flag:
        query['duplicate_flag'] = duplicate_flag
    if time_range and len(time_range) == 2 and time_range[0] and time_range[1]:
        query['begin_time'] = time_range[0]
        query['end_time'] = time_range[1]
    return query


def format_callback_rows(
    rows: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    formatted = []
    for row in rows or []:
        item = dict(row)
        item['key'] = str(item.get('callback_id'))
        item['event_time'] = format_time(item.get('event_time'))
        item['received_at'] = format_time(item.get('received_at'))
        item['process_status_display'] = _label(
            PROCESS_STATUS_LABELS, item.get('process_status')
        )
        item['duplicate_display'] = (
            '重复' if item.get('duplicate_flag') == 'Y' else '首次'
        )
        formatted.append(item)
    return formatted


def build_callback_detail_items(
    detail: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """回调详情拆成"事件信息"与"幂等/处理信息"两组。

    两组都不展示原始签名密钥；``dedup_key`` 是本地计算的服务端幂等键，用于排查
    重复回调，不包含厂商凭证，可以展示。
    """
    detail = detail or {}
    event_items = [
        {'label': '事件类型', 'children': detail.get('event_type') or '-'},
        {'label': '设备编码', 'children': detail.get('device_code') or '-'},
        {'label': '事件 ID', 'children': detail.get('event_id') or '-'},
        {'label': '日志类型', 'children': detail.get('log_type') or '-'},
        {
            'label': '事件时间',
            'children': format_time(detail.get('event_time')) or '-',
        },
        {
            'label': '接收时间',
            'children': format_time(detail.get('received_at')) or '-',
        },
        {'label': '来源 IP', 'children': detail.get('request_ip') or '-'},
    ]
    process_items = [
        {
            'label': '处理结果',
            'children': _label(
                PROCESS_STATUS_LABELS, detail.get('process_status')
            ),
        },
        {
            'label': '是否重复',
            'children': '重复'
            if detail.get('duplicate_flag') == 'Y'
            else '首次',
        },
        {
            'label': '签名校验',
            'children': '通过'
            if detail.get('signature_valid') == 'Y'
            else '未通过/未校验',
        },
        {
            'label': '幂等键',
            'children': detail.get('dedup_key') or '-',
        },
        {
            'label': '回调配置 ID',
            'children': _display(detail.get('session_id')),
        },
        {'label': 'topic', 'children': detail.get('topic_name') or '-'},
        {
            'label': '条目数',
            'children': _display(detail.get('item_count')),
        },
        {
            'label': '处理时间',
            'children': format_time(detail.get('processed_at')) or '-',
        },
        {
            'label': '错误信息',
            'children': detail.get('error_message') or '-',
        },
    ]
    return event_items, process_items


def parse_error_text(response: Any) -> str:
    """兼容 dict/异常两种错误载体，提取可读信息。"""
    if response is None:
        return ''
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return str(response.get('message') or response.get('msg') or '')
    return str(getattr(response, 'message', '') or response)


def extract_msg_id(response: Any) -> Optional[str]:
    """从开启/停止录音响应里取出厂商 ``msg_id``（契约 §4.3 / C8）。"""
    if isinstance(response, dict) and response.get('msg_id'):
        return str(response['msg_id'])
    return None


def command_result_items(
    data: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """指令结果：合并厂商回执与本地控制日志（契约 §4.5 / §4.7）。

    契约 Stage 4 定稿的响应为 ``{remote, remote_error, local}``：厂商侧"记录不存在"
    时仍能用本地留存的 ``msg_id`` 交代结果，因此这里把 ``remote_error`` 单独展示，
    不当作整体失败。厂商 ``payload``/``response`` 结构未定义（U9），按原样脱敏展示。
    兼容旧版直接把厂商对象作为 ``data`` 返回的情况。
    """
    payload = data if isinstance(data, dict) else {}
    if 'remote' in payload or 'local' in payload or 'remote_error' in payload:
        remote = payload.get('remote')
        local = payload.get('local')
        remote_error = payload.get('remote_error')
    else:
        remote, local, remote_error = payload, None, None
    remote = remote if isinstance(remote, dict) else {}
    local = local if isinstance(local, dict) else {}
    status_value = remote.get('status')
    status_label = (
        COMMAND_STATUS_LABELS.get(status_value, str(status_value))
        if status_value is not None
        else '-'
    )
    return [
        {
            'label': '设备编码',
            'children': remote.get('sn') or local.get('device_code') or '-',
        },
        {
            'label': '指令',
            'children': remote.get('cmd')
            or _label(COMMAND_LABELS, local.get('command')),
        },
        {
            'label': '消息 ID',
            'children': remote.get('msg_id') or local.get('msg_id') or '-',
        },
        {'label': '厂商状态', 'children': status_label},
        {
            'label': '请求时间',
            'children': format_time(remote.get('req_time')) or '-',
        },
        {
            'label': '响应时间',
            'children': format_time(remote.get('resp_time')) or '-',
        },
        {
            'label': '本地受理结果',
            'children': _label(
                REQUEST_STATUS_LABELS, local.get('request_status')
            ),
        },
        {
            'label': '厂商指令状态码',
            'children': _display(local.get('remote_status')),
        },
        {
            'label': '请求载荷',
            'children': format_payload(local.get('payload_json')) or '暂无',
        },
        {
            'label': '厂商响应',
            'children': format_payload(remote.get('response')) or '暂无',
        },
        {
            'label': '厂商回执说明',
            'children': mask_secret_text(str(remote_error)) or '-',
        },
        {
            'label': '错误信息',
            'children': local.get('error_message')
            or remote.get('message')
            or '-',
        },
    ]


def command_result_alert(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """指令结果的顶部提示：区分"厂商回执正常/厂商侧暂无记录/两边都无"。"""
    payload = data if isinstance(data, dict) else {}
    remote = payload.get('remote')
    local = payload.get('local')
    remote_error = payload.get('remote_error')
    if isinstance(remote, dict):
        status = remote.get('status')
        return {
            'type': 'info',
            'text': (
                f'厂商回执状态：{COMMAND_STATUS_LABELS.get(status, status)}'
            ),
        }
    if isinstance(local, dict):
        return {
            'type': 'warning',
            'text': (
                '厂商侧暂无该指令记录'
                + (f'（{remote_error}）' if remote_error else '')
                + '，以下为本地控制日志'
            ),
        }
    return {'type': 'error', 'text': '未查询到指令结果，请确认 msg_id 是否正确'}


def has_permission(perms: Optional[List[str]], required: str) -> bool:
    """与 ``PermissionManager.check_perms`` 同语义的纯函数版本，便于测试。"""
    perm_list = perms or []
    if '*:*:*' in perm_list:
        return True
    return required in perm_list


def control_action_buttons(perms: Optional[List[str]]) -> List[Dict[str, str]]:
    """按权限生成设备列表行内操作按钮，顺序与后端路由守卫一一对应。"""
    buttons = []
    if has_permission(perms, 'device:manage:query'):
        buttons.append({'content': '详情', 'type': 'link', 'icon': 'antd-eye'})
    if has_permission(perms, 'device:manage:control'):
        buttons.extend(
            [
                {'content': '开始录音', 'type': 'link', 'icon': 'antd-audio'},
                {
                    'content': '停止录音',
                    'type': 'link',
                    'icon': 'antd-pause-circle',
                },
                {
                    'content': '指令结果',
                    'type': 'link',
                    'icon': 'antd-file-search',
                },
            ]
        )
    if has_permission(perms, 'device:manage:edit'):
        buttons.extend(
            [
                {'content': '修改', 'type': 'link', 'icon': 'antd-edit'},
                {
                    'content': '状态维护',
                    'type': 'link',
                    'icon': 'antd-setting',
                },
            ]
        )
    if has_permission(perms, 'device:manage:remove'):
        buttons.append(
            {'content': '删除', 'type': 'link', 'icon': 'antd-delete'}
        )
    return buttons


def mask_secret_text(text: Optional[str]) -> str:
    """对任意文本做敏感键脱敏（用于异常信息兜底）。"""
    if not text:
        return ''
    masked = re.sub(
        r'(?i)(password|passwd|pwd|secret|token|authorization|signature)'
        r'(\s*[:=]\s*)("?[^\s,;"}]+"?)',
        lambda match: f'{match.group(1)}{match.group(2)}{MASK_TEXT}',
        str(text),
    )
    return _mask_url(masked)
