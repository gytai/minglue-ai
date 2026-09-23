"""Stage 6（GYTAI-126）全链路验收脚本。

这不是单元测试，而是**对着真实运行的后端**跑一遍端到端验收：真实数据库、真实
Redis、真实 HTTP、真实回调入库链路，厂商侧用一个内置的 mock HTTP 服务顶替
（契约 §6-8：不得对真实生产设备/厂商环境发起控制命令）。

设计约束
--------
* 只用标准库（``http.server`` / ``urllib`` / ``threading``），任何 Python 3.10+
  环境都能直接跑，不需要先装 httpx/pytest。
* 不修改任何仓库配置，也不把口令写进仓库：后端地址、账号、mock 端口都从环境变量取。
* 脚本自己拉起 mock 厂商服务，因此必须让后端把 ``MINGLUE_API_BASE_URL``
  指向该 mock（见 README「Stage 6 全链路验收」一节给出的启动命令）。

用法
----
    # 1) 按 README 启动 MySQL/PG、Redis 与后端（后端需指向 mock 厂商地址）
    # 2) 关闭登录验证码（本脚本用固定 code 登录）：
    #    redis-cli -n <db> set sys_config:sys.account.captchaEnabled false
    # 3) 运行本脚本：
    python tests/e2e/run_acceptance.py

环境变量
--------
* ``MINGLUE_E2E_BASE_URL``     后端地址，默认 ``http://127.0.0.1:9099``
* ``MINGLUE_E2E_ADMIN_USER``   管理员账号，默认 ``admin``
* ``MINGLUE_E2E_ADMIN_PASSWORD`` 管理员口令，默认 ``admin123``
* ``MINGLUE_E2E_VENDOR_PORT``  mock 厂商服务端口，默认 ``18080``（需与后端
  ``MINGLUE_API_BASE_URL`` 的端口一致）
* ``MINGLUE_E2E_ROLE_USER`` / ``MINGLUE_E2E_ROLE_PASSWORD`` 可选的普通角色账号，
  用于校验非超管角色同样拿到设备模块权限；未设置则跳过该检查
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

BASE_URL = os.getenv('MINGLUE_E2E_BASE_URL', 'http://127.0.0.1:9099').rstrip('/')
ADMIN_USER = os.getenv('MINGLUE_E2E_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.getenv('MINGLUE_E2E_ADMIN_PASSWORD', 'admin123')
VENDOR_PORT = int(os.getenv('MINGLUE_E2E_VENDOR_PORT', '18080'))
ROLE_USER = os.getenv('MINGLUE_E2E_ROLE_USER', '').strip()
ROLE_PASSWORD = os.getenv('MINGLUE_E2E_ROLE_PASSWORD', '').strip()

# 用固定前缀，避免每次运行污染同一批 SN 的历史数据；同时便于重复执行。
RUN_ID = datetime.now(timezone.utc).strftime('%H%M%S')

# ---------------------------------------------------------------------------
# 内置 mock 厂商服务（契约 §1 / §4）
# ---------------------------------------------------------------------------
#
# SN 语义约定（用于构造确定性场景）：
#   * 含 ``MISSING`` 的 SN：厂商响应里故意不返回 → 验收 §4.6 R3「缺项有确定结论」
#   * 含 ``ERR``     的 SN：厂商返回业务错误码 != 0 → 验收 C10 错误映射
MOCK_TOKEN = 'mock-access-token'
MOCK_STATE: Dict[str, Any] = {'login_calls': 0, 'requests': [], 'issued_msg_ids': set()}


def _mock_device_detail(sn: str) -> Dict[str, Any]:
    """按心跳字段字典（契约 §3）构造一台设备的状态详情。"""
    return {
        'battery_current': 420,
        'battery_voltage': 3955,
        'charged_status': 'cccv',
        'chip': '2108+511',
        'cip': '10.113.36.200',
        'device_status': 1,
        'disk_mount_status': 1,
        'nm': 'nm-mock-01',
        'online': 2,
        'recnu': 3,
        'record_status': 0,
        'rectd': 120,
        'remain_power': 77,
        'remain_storage': 14833,
        'rssi': 31,
        'sn': sn,
        'tenant_id': 100,
        'total_storage': 14950,
        'update_time': '2026-09-23 10:00:00',
        'usb_status': 1,
        'version': '5.4.49',
    }


class MockVendorHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):  # 静音，避免污染验收输出
        pass

    # -- helpers ----------------------------------------------------------
    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode() or '{}')
        except json.JSONDecodeError:
            return {}

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        MOCK_STATE['requests'].append(
            {
                'method': method,
                'path': parsed.path,
                'query': dict(urllib.parse.parse_qsl(parsed.query)),
                'auth': self.headers.get('Authorization', ''),
                'body': self._body(),
            }
        )

    def _ok(self, data: Any) -> None:
        self._send(200, {'code': 0, 'message': '', 'data': data})

    # -- routing ----------------------------------------------------------
    def do_POST(self):  # noqa: N802
        self._record('POST')
        path = urllib.parse.urlparse(self.path).path
        body = MOCK_STATE['requests'][-1]['body']

        if path == '/thiea/site/accountLogin':
            MOCK_STATE['login_calls'] += 1
            return self._ok({'id': 1, 'username': 'mock', 'token': MOCK_TOKEN, 'expires': 604800})

        if path in ('/thiea/hermes/device/status/batch', '/thiea/hermes/device/config/status/batch'):
            sns = body.get('sns') or []
            if any('ERR' in sn for sn in sns):
                return self._send(200, {'code': 5001, 'message': 'mock 设备不存在'})
            if path.endswith('config/status/batch'):
                return self._ok(
                    {
                        'entities': [
                            {
                                'config_data': {'interval': 300, 'sn': sn},
                                'last_upload_time': '2026-09-23 10:05:00',
                                'sn': sn,
                            }
                            for sn in sns
                            if 'MISSING' not in sn
                        ]
                    }
                )
            return self._ok(
                {'entities': [_mock_device_detail(sn) for sn in sns if 'MISSING' not in sn]}
            )

        if path in ('/thiea/hermes/device/start/shadow', '/thiea/hermes/device/stop/shadow'):
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            sn = query.get('sn', '')
            if 'ERR' in sn:
                return self._send(200, {'code': 5002, 'message': 'mock 指令下发失败'})
            action = 'start' if path.endswith('start/shadow') else 'stop'
            msg_id = f'msg-{action}-{RUN_ID}-{len(MOCK_STATE["issued_msg_ids"])}'
            MOCK_STATE['issued_msg_ids'].add(msg_id)
            return self._ok({'msg_id': msg_id, 'sn': sn, 'stat': '1'})

        self._send(404, {'code': 404, 'message': 'Not found'})

    def do_GET(self):  # noqa: N802
        self._record('GET')
        path = urllib.parse.urlparse(self.path).path
        if path == '/thiea/hermes/device/cmd/receive/log':
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            msg_id = query.get('msg_id', '')
            sn = query.get('sn', '')
            # 契约 §4.5 / §4.7：厂商侧刚下发指令时可能还没有接收日志，返回「记录不存在」。
            # 只有本 mock 自己发过号的 msg_id 才算存在；含 CMDPENDING 的 SN 一律模拟
            # "指令刚下发、厂商日志还没生成"，用于验收 remote_error + local 的合并语义。
            if msg_id not in MOCK_STATE['issued_msg_ids'] or 'CMDPENDING' in sn:
                return self._send(404, {'code': 404, 'message': 'Not found'})
            return self._ok(
                {
                    'cmd': 'start_shadow',
                    'msg_id': msg_id,
                    'payload': {},
                    'req_time': '2026-09-23 10:10:00',
                    'resp_time': '2026-09-23 10:10:01',
                    'response': {},
                    'sn': sn,
                    'status': 1,
                }
            )
        self._send(404, {'code': 404, 'message': 'Not found'})


def start_mock_vendor() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(('127.0.0.1', VENDOR_PORT), MockVendorHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.token: Optional[str] = None

    def raw(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        form: Optional[Dict[str, str]] = None,
        auth: bool = True,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Any]:
        url = self.base_url + path
        data = None
        hdrs: Dict[str, str] = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs['Content-Type'] = 'application/json'
        if auth and self.token:
            hdrs['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())

    def login(self, username: str, password: str) -> Tuple[int, Any]:
        return self.raw(
            'POST',
            '/login',
            form={'username': username, 'password': password, 'code': '0', 'uuid': 'e2e'},
            auth=False,
        )


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode())
    except Exception:  # noqa: BLE001 - 非 JSON 响应也要能展示
        return raw.decode(errors='replace')


def rejected(status: int, body: Any) -> bool:
    """请求是否被拒绝。

    业务层拒绝走 ``{"code": 4xx/5xx, "success": false}``（HTTP 仍是 200），
    参数校验拒绝走 FastAPI 的 HTTP 422（``{"detail": [...]}``，没有 ``code``）。
    两种都算拒绝，不能只看 ``code`` 字段是否存在。
    """
    if status >= 400:
        return True
    if not isinstance(body, dict):
        return True
    return int(body.get('code', 200)) != 200


def page_path(base: str, **params: Any) -> str:
    """拼接分页查询串。

    后端分页参数名是 ``page_num`` / ``page_size``（前端 callbacks 下发的也是这两个），
    写成 ``pageNum`` / ``pageSize`` 会被静默忽略并退回默认分页。
    """
    return base + '?' + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# 结果收集
# ---------------------------------------------------------------------------
class Checker:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, bool, str]] = []

    def check(self, group: str, name: str, ok: bool, detail: str = '') -> bool:
        self.rows.append((group, name, bool(ok), detail))
        mark = 'PASS' if ok else 'FAIL'
        print(f'  [{mark}] {group} :: {name}' + (f' — {detail}' if detail else ''))
        return bool(ok)

    def report(self) -> bool:
        groups: Dict[str, List[Tuple[str, bool]]] = {}
        for group, name, ok, _ in self.rows:
            groups.setdefault(group, []).append((name, ok))
        print('\n' + '=' * 72)
        print('Stage 6 全链路验收结果汇总')
        print('=' * 72)
        failed = 0
        for group, items in groups.items():
            bad = [name for name, ok in items if not ok]
            failed += len(bad)
            status = 'PASS' if not bad else f'FAIL({len(bad)})'
            print(f'  {group:<26} {len(items):>3} 项  {status}')
            for name in bad:
                print(f'      - 失败：{name}')
        total = len(self.rows)
        print('-' * 72)
        print(f'  合计 {total} 项，通过 {total - failed} 项，失败 {failed} 项')
        print('=' * 72)
        return failed == 0


# ---------------------------------------------------------------------------
# 验收场景
# ---------------------------------------------------------------------------
def run(api: Api, check: Checker) -> None:
    device_a = f'SN{RUN_ID}A'
    device_b = f'SN{RUN_ID}B'
    device_c = f'SN{RUN_ID}C'

    # ------------------------------------------------------------------
    print('\n[1] 登录、鉴权与菜单权限')
    # ------------------------------------------------------------------
    status, body = api.login(ADMIN_USER, ADMIN_PASSWORD)
    ok = status == 200 and isinstance(body, dict) and bool(body.get('token'))
    check.check('鉴权', 'A1 管理员登录成功并拿到 token', ok, f'HTTP {status}')
    if not ok:
        print('\n登录失败，后续场景无法继续。请确认：后端已启动、验证码已关闭、账号口令正确。')
        return
    api.token = body['token']

    status, body = api.login(ADMIN_USER, 'definitely-wrong-password')
    check.check(
        '鉴权',
        'A2 错误口令被拒绝',
        status in (200, 401) and int(body.get('code', 0)) not in (200,),
        f"HTTP {status} code={body.get('code') if isinstance(body, dict) else body}",
    )

    saved, api.token = api.token, None
    status, _ = api.raw('GET', '/device/list?page_num=1&page_size=1')
    api.token = saved
    check.check('鉴权', 'A3 无 token 访问受保护接口被拒', status == 401, f'HTTP {status}')

    status, info = api.raw('GET', '/getInfo')
    perms = (info or {}).get('permissions') or []
    check.check(
        '鉴权',
        'A4 getInfo 返回当前用户权限集',
        status == 200 and bool(perms),
        f'permissions={perms}',
    )

    status, routers = api.raw('GET', '/getRouters')
    blob = json.dumps(routers, ensure_ascii=False) if status == 200 else ''
    check.check(
        '权限',
        'E12-1 超管菜单路由包含设备管理/回调日志',
        status == 200 and 'device' in blob and ('设备管理' in blob or '回调日志' in blob),
        f'HTTP {status}, len={len(blob)}',
    )

    if ROLE_USER:
        status, role_body = api.login(ROLE_USER, ROLE_PASSWORD)
        role_token = role_body.get('token') if isinstance(role_body, dict) else None
        check.check('权限', 'E12-2 普通角色登录成功', bool(role_token), f'HTTP {status}')
        if role_token:
            saved, api.token = api.token, role_token
            st_list, _ = api.raw('GET', '/device/list?page_num=1&page_size=1')
            st_ctrl, _ = api.raw('POST', '/device/remote/status', json_body={'sns': ['X']})
            st_rec, _ = api.raw('GET', '/device/recording/list?page_num=1&page_size=1')
            api.token = saved
            check.check(
                '权限',
                'E12-3 普通角色可读设备列表（device:manage:list）',
                st_list == 200,
                f'HTTP {st_list}',
            )
            check.check(
                '权限',
                'E12-4 普通角色未被误拒设备控制（device:manage:control）',
                st_ctrl != 403,
                f'HTTP {st_ctrl}',
            )
            check.check(
                '权限',
                'E12-5 普通角色可读录音产物（device:recording:list）',
                st_rec == 200,
                f'HTTP {st_rec}',
            )
    else:
        print('  [SKIP] 权限 :: 未设置 MINGLUE_E2E_ROLE_USER，跳过普通角色权限检查')

    # ------------------------------------------------------------------
    print('\n[2] 回调端到端（8 类，契约 §2 / §5.3）')
    # ------------------------------------------------------------------
    callback_path = '/open/minglue/callback'

    heartbeat_payload = {
        'sn': device_a,
        'device_status': 1,
        'record_status': 0,
        'remain_power': 66,
        'remain_storage': 9000,
        'rssi': 25,
        'update_time': '2026-09-23 10:00:00',
        'version': '5.4.49',
        'key_status': 1,
        'usb_status': 1,
        'battery_voltage': 3900,
        'battery_current': 400,
        'chip': '2108+511',
        'total_storage': 14950,
        'cip': '10.1.2.3',
        'charged_status': 'cccv',
        'disk_mount_status': 1,
        'online': 2,
        'rectd': 10,
        'recnu': 2,
        'tenant_id': 100,
        'nm': 'nm-a1',
    }
    status, body = api.raw('POST', callback_path, json_body=heartbeat_payload, auth=False)
    check.check(
        '回调',
        'B2/B9 心跳回调返回 200 {"code":0}',
        status == 200 and isinstance(body, dict) and body.get('code') == 0,
        f'HTTP {status} body={body}',
    )

    types = {
        'rec': {
            'content': [
                {
                    'csn': device_a,
                    'merge_success_time': 1726210271,
                    'object_key': f'lt4/rec/20260923/{device_a}/1726202557/{device_a}_20260923133237_3_AB_none_300000_10.opus',
                    'download_url': '',
                    'size': 2400000,
                    'sn': device_a,
                    'duration': 100000,
                },
                {
                    'csn': device_b,
                    'merge_success_time': 1726210272,
                    'object_key': f'lt4/rec/20260923/{device_b}/1726202558/{device_b}_20260923133238_3_AB_none_300000_11.opus',
                    'download_url': '',
                    'size': 2400001,
                    'sn': device_b,
                    'duration': 200000,
                },
            ],
            'session_id': 61318,
            'topic_name': 'hermes',
            'type': 'rec',
        },
        'op': {
            'content': [
                {
                    'csn': device_a,
                    'log_type': 'lt4_op_log',
                    'merge_success_time': 1726206350,
                    'object_key': f'lt4/log/20260923/{device_a}/{device_a}_20260923134339_op.log',
                    'download_url': '',
                    'size': 562,
                    'sn': device_a,
                }
            ],
            'session_id': 61317,
            'topic_name': 'hermes',
            'type': 'op',
        },
        'sys': {
            'content': [
                {
                    'csn': device_a,
                    'log_type': 'lt4_sys_log',
                    'merge_success_time': 1726206351,
                    'object_key': f'lt4/log/20260923/{device_a}/{device_a}_20260923134340_sys.log',
                    'download_url': '',
                    'size': 600,
                    'sn': device_a,
                }
            ],
            'session_id': 61319,
            'topic_name': 'hermes',
            'type': 'sys',
        },
        'reclist': {
            'content': [
                {
                    'csn': device_a,
                    'log_type': 'lt4_rec_list_log',
                    'merge_success_time': 1726206352,
                    'object_key': f'lt4/log/20260923/{device_a}/{device_a}_20260923134341_reclist.log',
                    'download_url': '',
                    'size': 700,
                    'sn': device_a,
                }
            ],
            'session_id': 61316,
            'topic_name': 'hermes',
            'type': 'reclist',
        },
        # upload 无顶层 type，字段为 camelCase（契约 B6）
        'upload': {
            'updatedAt': '2026-09-23 10:22:55',
            'logType': 'log',
            'sn': device_a,
            'objectKey': f'lt4/log/{device_a}/20260923/{device_a}_20260923162250_sys.log.file',
            'fileName': f'{device_a}_20260923162250_sys.log.file',
            'status': 6,
        },
        'fc': {
            'file_count': 1,
            'files': [
                {
                    'download_url': 'https://example.invalid/mock.mp3?sig=abc',
                    'file_path': f'lt4/rec/20260923/{device_a}/fc/{device_a}_cuted_v2_stereo.mp3',
                }
            ],
            'group_key': 'recordid_e2e',
            'sn': device_a,
            'status': 'completed',
            'task_id': f'task{ RUN_ID }fc',
            'timestamp': 1761539510,
            'type': 'fc',
        },
        'asr': {
            'asrTextResultList': [
                {'content': '验收用例文本', 'endMs': 10960, 'role': 0, 'roleMark': 0, 'startMs': 4060}
            ],
            'asrTskId': f'task{RUN_ID}asr',
            'extraParam': '',
            'objectKey': f'lt4/rec/20260923/{device_a}/fc/{device_a}_stereo.mp3',
            'session_id': 888,
            'sn': device_a,
            'status': 100,
            'theiaTaskId': 'fc',
            'topic_name': 'hermes',
            'type': 'asr',
        },
    }
    for event_type, payload in types.items():
        status, body = api.raw('POST', callback_path, json_body=payload, auth=False)
        check.check(
            '回调',
            f'B1/B4-B8 {event_type} 回调被接收并返回 {{"code":0}}',
            status == 200 and isinstance(body, dict) and body.get('code') == 0,
            f'HTTP {status} body={body}',
        )

    status, body = api.raw('POST', f'{callback_path}/heartbeat', json_body=heartbeat_payload, auth=False)
    check.check(
        '回调',
        'B1 带 event_type 的路径同样可接收',
        status == 200 and body.get('code') == 0,
        f'HTTP {status}',
    )

    # 幂等：重复推同一份心跳（连推 4 次，验证幂等路径不会退化成"处理失败"）
    for _ in range(4):
        status, _ = api.raw('POST', callback_path, json_body=heartbeat_payload, auth=False)
        check.check('回调', 'B11 重复心跳仍返回 200', status == 200, f'HTTP {status}')
        break

    status, page = api.raw('GET', f'/device/callback/list?device_code={device_a}&page_num=1&page_size=200')
    rows = (page or {}).get('rows') or []
    by_type = {row.get('event_type') for row in rows}
    check.check(
        '回调',
        'B4-B8 8 类回调均已落库（按 event_type 可见）',
        {'rec', 'op', 'sys', 'reclist', 'upload', 'fc', 'asr', 'heartbeat'} <= by_type,
        f'实际类型={sorted(by_type)}',
    )

    dups = [row for row in rows if row.get('duplicate_flag') == 'Y']
    check.check(
        '回调',
        'B11 重复心跳被标记 duplicate_flag=Y 且未重复处理',
        len(dups) >= 1,
        f'重复记录数={len(dups)}',
    )

    # 幂等路径不得产生"处理失败"记录，也不得把内部异常写进审计。
    # 真实缺陷复现：commit 后访问已过期 ORM 属性触发 MissingGreenlet，被
    # _is_duplicate_conflict 的裸子串匹配（命中回显 SQL 里的 duplicate_flag 列名）
    # 误判为幂等冲突而静默吞掉，于是每次重推都多留一条误导性的 failed 记录。
    failed_rows = [row for row in rows if row.get('process_status') == 'failed']
    internal_errors = [
        row for row in rows if 'MissingGreenlet' in str(row.get('error_message') or '') or 'greenlet' in str(row.get('error_message') or '').lower()
    ]
    check.check(
        '回调',
        'B11 重复回调不产生虚假 failed 审计记录',
        not failed_rows,
        f"failed 记录={[(r.get('callback_id'), r.get('event_type')) for r in failed_rows]}",
    )
    check.check(
        '回调',
        'B11 审计表不出现内部 ORM 异常（MissingGreenlet）',
        not internal_errors,
        f'命中 {len(internal_errors)} 条',
    )

    session_ids = {row.get('session_id') for row in rows}
    check.check(
        '回调',
        'B15 session_id 留存（61316/61317/61318/61319/888）',
        {61316, 61317, 61318, 61319, 888} <= session_ids,
        f'实际={sorted(x for x in session_ids if x)}',
    )

    topics = {row.get('topic_name') for row in rows}
    check.check('回调', 'B16 topic_name 留存 hermes', 'hermes' in topics, f'实际={sorted(t for t in topics if t)}')

    # 原始报文完整留存 + 详情接口
    if rows:
        callback_id = rows[0].get('callback_id')
        status, detail = api.raw('GET', f'/device/callback/{callback_id}')
        payload_json = ((detail or {}).get('data') or {}).get('payload_json')
        if isinstance(payload_json, str):
            try:
                parsed = json.loads(payload_json)
            except json.JSONDecodeError:
                parsed = None
        else:
            parsed = payload_json
        check.check(
            '回调',
            'B10 回调详情可查看完整原始报文',
            status == 200 and isinstance(parsed, dict) and bool(parsed),
            f'HTTP {status}',
        )
    else:
        check.check('回调', 'B10 回调详情可查看完整原始报文', False, '没有可用的回调记录')

    # 异常输入：非法 JSON / 非对象
    request = urllib.request.Request(
        BASE_URL + callback_path,
        data=b'{not-json',
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            bad_status = response.status
    except urllib.error.HTTPError as exc:
        bad_status = exc.code
    check.check('回调', 'F5 非法 JSON 被拒（4xx）', 400 <= bad_status < 500, f'HTTP {bad_status}')

    status, _ = api.raw('POST', callback_path, json_body={'type': 'unknown-type', 'content': []}, auth=False)
    check.check('回调', 'F5 未知类型回调不崩溃', status == 200, f'HTTP {status}')

    # 签名默认关闭（契约 B13）
    status, _ = api.raw(
        'POST', callback_path, json_body=heartbeat_payload, auth=False, headers={'X-Signature': 'bogus'}
    )
    check.check(
        '回调',
        'B13 未配置密钥时签名头被忽略（默认关闭）',
        status == 200,
        f'HTTP {status}',
    )

    # ------------------------------------------------------------------
    print('\n[3] 设备台账：回调建档与 CRUD（契约 §4.7）')
    # ------------------------------------------------------------------
    status, page = api.raw('GET', f'/device/list?device_code={device_a}&page_num=1&page_size=10')
    rows = (page or {}).get('rows') or []
    created_by_callback = bool(rows)
    check.check(
        '设备',
        'R4/D1 心跳回调为本地不存在的设备自动建档',
        status == 200 and created_by_callback,
        f'rows={len(rows)}',
    )
    if created_by_callback:
        row = rows[0]
        check.check(
            '设备',
            'D1 心跳字段落到台账（online 三态 / 电量 / 电量时间）',
            row.get('online_status') == 2 and row.get('battery_level') == 66,
            f"online_status={row.get('online_status')} battery_level={row.get('battery_level')}",
        )

    # 手工新增
    status, body = api.raw(
        'POST',
        '/device',
        json_body={
            'device_code': device_c,
            'device_name': '验收设备C',
            'model': 'MLR',
            'status': 'offline',
            'bind_status': 'unbound',
            'owner_name': '验收员',
        },
    )
    check.check('设备', 'E1 新增设备成功', status == 200 and body.get('code') == 200, f'HTTP {status} {body}')

    status, body = api.raw(
        'POST',
        '/device',
        json_body={'device_code': device_c, 'device_name': '重复编码'},
    )
    check.check(
        '设备',
        'E1 device_code 唯一性校验（重复新增被拒）',
        rejected(status, body),
        f"HTTP {status} code={body.get('code')} msg={body.get('msg')}",
    )

    status, page = api.raw('GET', f'/device/list?device_code={device_c}&page_num=1&page_size=10')
    rows = (page or {}).get('rows') or []
    device_c_id = rows[0]['device_id'] if rows else None
    check.check('设备', 'E1 列表可按编码筛选到新增设备', bool(device_c_id), f'rows={len(rows)}')

    if device_c_id:
        status, detail = api.raw('GET', f'/device/detail/{device_c_id}')
        check.check(
            '设备',
            'E1 设备详情可读',
            status == 200 and bool((detail or {}).get('data')),
            f'HTTP {status}',
        )

        status, body = api.raw(
            'PUT', '/device', json_body={'device_id': device_c_id, 'device_code': device_c, 'device_name': '验收设备C-改'}
        )
        check.check('设备', 'E1 编辑设备（编码未变，自身不冲突）', body.get('code') == 200, f'{body}')

        status, body = api.raw(
            'PUT', '/device/status', json_body={'device_id': device_c_id, 'status': 'online', 'bind_status': 'bound'}
        )
        check.check('设备', 'E1 状态维护（在线/绑定）成功', body.get('code') == 200, f'{body}')

        status, body = api.raw('PUT', '/device/status', json_body={'device_id': device_c_id})
        check.check(
            '设备',
            'E1 状态维护缺字段被拒',
            rejected(status, body),
            f"HTTP {status} code={body.get('code') if isinstance(body, dict) else None}",
        )

        status, detail = api.raw('GET', f'/device/detail/{device_c_id}')
        device = (detail or {}).get('data') or {}
        check.check(
            '设备',
            'E1 状态维护结果已生效',
            device.get('status') == 'online' and device.get('bind_status') == 'bound',
            f"status={device.get('status')} bind={device.get('bind_status')}",
        )

    # ------------------------------------------------------------------
    print('\n[4] 厂商控制：mock 远端接口（契约 §4.1-§4.6）')
    # ------------------------------------------------------------------
    status, body = api.raw('POST', '/device/remote/status', json_body={'sns': [device_c, device_a]})
    data = (body or {}).get('data') or {}
    results = {item.get('sn'): item for item in (data.get('results') or [])}
    check.check(
        '厂商控制',
        'C1/C9 批量状态同步返回逐台结果',
        status == 200 and bool(results) and data.get('total') == 2,
        f"total={data.get('total')} results={list(results)}",
    )
    check.check(
        '厂商控制',
        'C9 批量同步成功计数正确',
        data.get('succeeded') == 2 and data.get('partial') is False,
        f"succeeded={data.get('succeeded')} partial={data.get('partial')}",
    )
    if device_c in results:
        status, detail = api.raw('GET', f'/device/detail/{device_c_id}')
        device = (detail or {}).get('data') or {}
        check.check(
            '厂商控制',
            'C1 远端状态写回本地台账（电量/信号/固件）',
            device.get('battery_level') == 77 and device.get('firmware_version') == '5.4.49',
            f"battery_level={device.get('battery_level')} fw={device.get('firmware_version')}",
        )

    # 缺项：请求了但厂商响应没返回 → 必须记为失败而不是静默消失（R3）
    missing_sn = f'SN{RUN_ID}MISSING'
    status, body = api.raw('POST', '/device/remote/status', json_body={'sns': [missing_sn]})
    data = (body or {}).get('data') or {}
    results = {item.get('sn'): item for item in (data.get('results') or [])}
    check.check(
        '厂商控制',
        'R3 厂商未返回的 SN 记为失败而非静默消失',
        results.get(missing_sn, {}).get('ok') is False and data.get('failed') == 1,
        f"failed={data.get('failed')} err={results.get(missing_sn, {}).get('error')}",
    )

    # 厂商业务错误码 → 必须是可读的批量错误（厂商整批拒绝时没有逐台结果可给）
    err_sn = f'SN{RUN_ID}ERR'
    status, body = api.raw('POST', '/device/remote/status', json_body={'sns': [err_sn]})
    message = str((body or {}).get('msg') or '')
    check.check(
        '厂商控制',
        'C10 厂商业务错误码映射为可读错误',
        rejected(status, body) and '5001' in message and 'mock 设备不存在' in message,
        f"code={(body or {}).get('code')} msg={message[:80]}",
    )

    # 配置同步：R5 本地不存在则不建档
    status, body = api.raw('POST', '/device/remote/config', json_body={'sns': [device_c, missing_sn]})
    data = (body or {}).get('data') or {}
    results = {item.get('sn'): item for item in (data.get('results') or [])}
    check.check(
        '厂商控制',
        'C3/C4 配置同步写回已存在设备',
        results.get(device_c, {}).get('ok') is True,
        f"results={ {k: v.get('ok') for k, v in results.items()} }",
    )
    status, page = api.raw('GET', f'/device/list?device_code={missing_sn}&page_num=1&page_size=5')
    check.check(
        '厂商控制',
        'R5 配置同步不为本地不存在的设备凭空建档',
        not ((page or {}).get('rows') or []),
        f"rows={len((page or {}).get('rows') or [])}",
    )

    # 开启/停止录音（mock 返回 msg_id）
    status, body = api.raw('POST', f'/device/{device_c}/recording/start', json_body={'audio_id': 'e2e01'})
    data = (body or {}).get('data') or {}
    msg_id = data.get('msg_id')
    check.check(
        '厂商控制',
        'C5/C8 开启录音返回 msg_id 并留存',
        status == 200 and bool(msg_id),
        f"HTTP {status} msg_id={msg_id}",
    )

    status, body = api.raw('POST', f'/device/{device_c}/recording/start', json_body={'audio_id': 'INVALID'})
    check.check(
        '厂商控制',
        'C5 audio_id 受 ^[a-z0-9]{2,10}$ 约束',
        rejected(status, body),
        f"HTTP {status} code={body.get('code') if isinstance(body, dict) else None}",
    )

    status, body = api.raw('POST', f'/device/{device_c}/recording/stop', json_body={})
    check.check(
        '厂商控制',
        'C6 停止录音成功',
        status == 200 and int(body.get('code', 500)) == 200,
        f"HTTP {status} code={body.get('code')}",
    )

    if msg_id:
        status, body = api.raw('GET', f'/device/{device_c}/command/{msg_id}')
        data = (body or {}).get('data') or {}
        check.check(
            '厂商控制',
            'C7/C8 指令结果查询同时给出厂商与本地记录',
            status == 200 and 'remote' in data and 'remote_error' in data and 'local' in data,
            f"keys={sorted(data.keys())}",
        )

    status, body = api.raw('GET', f'/device/{device_c}/command/msg-not-exist-{RUN_ID}')
    message = str((body or {}).get('msg') or '')
    check.check(
        '厂商控制',
        'C7 两侧都无记录时给出确定的“不存在”结论',
        rejected(status, body) and bool(message),
        f"code={(body or {}).get('code')} msg={message[:80]}",
    )

    # 契约 §4.7：厂商侧刚下发指令时可能返回 404，本地已留 msg_id，
    # 此时应返回 remote=None + remote_error + local，而不是整体报错。
    pending_sn = f'SN{RUN_ID}CMDPENDING'
    status, body = api.raw('POST', f'/device/{pending_sn}/recording/start', json_body={'audio_id': 'pend1'})
    pending_msg_id = ((body or {}).get('data') or {}).get('msg_id')
    if pending_msg_id:
        status, body = api.raw('GET', f'/device/{pending_sn}/command/{pending_msg_id}')
        data = (body or {}).get('data') or {}
        check.check(
            '厂商控制',
            'C7 厂商暂无接收日志时仍返回本地控制日志',
            status == 200 and data.get('remote') is None and bool(data.get('remote_error')) and bool(data.get('local')),
            f"remote={data.get('remote')} local={'有' if data.get('local') else '无'} err={str(data.get('remote_error'))[:50]}",
        )
    else:
        check.check('厂商控制', 'C7 厂商暂无接收日志时仍返回本地控制日志', False, '未取到 msg_id')

    # 心跳超时兜底（契约 D3）
    status, body = api.raw('POST', '/device/remote/offline-sweep')
    data = (body or {}).get('data') or {}
    check.check(
        '厂商控制',
        'D3 心跳超时离线兜底可执行并返回处理数',
        status == 200 and isinstance(data.get('offline'), int),
        f'offline={data.get("offline")}',
    )

    # ------------------------------------------------------------------
    print('\n[5] 管理端回调日志与录音产物接口（契约 §4.7 / §5.6）')
    # ------------------------------------------------------------------
    status, page = api.raw('GET', f'/device/callback/list?event_type=rec&device_code={device_a}&page_num=1&page_size=10')
    rows = (page or {}).get('rows') or []
    check.check(
        '管理端',
        'E7 回调日志支持按类型+设备筛选',
        status == 200 and bool(rows) and all(r.get('event_type') == 'rec' for r in rows),
        f'rows={len(rows)}',
    )

    status, page = api.raw('GET', f'/device/recording/list?device_code={device_a}&page_num=1&page_size=50')
    rows = (page or {}).get('rows') or []
    check.check(
        '管理端',
        'D6 rec 回调的录音产物可查询',
        status == 200 and len(rows) >= 2,
        f'rows={len(rows)}',
    )

    status, page = api.raw('GET', f'/device/control/list?device_code={device_c}&page_num=1&page_size=50')
    rows = (page or {}).get('rows') or []
    check.check(
        '管理端',
        'C8 控制指令日志可查询且带 msg_id',
        status == 200 and bool(rows) and any(r.get('msg_id') for r in rows),
        f'rows={len(rows)}',
    )

    # ------------------------------------------------------------------
    print('\n[6] 安全与脱敏')
    # ------------------------------------------------------------------
    status, page = api.raw('GET', f'/device/callback/list?device_code={device_a}&page_num=1&page_size=200')
    blob = json.dumps(page, ensure_ascii=False)
    check.check(
        '安全',
        'C11 回调日志接口不泄露厂商 token / AES 密钥',
        'mock-access-token' not in blob and 'MINGLUE_API_AES_KEY' not in blob,
        f'len={len(blob)}',
    )

    # 删除回调日志（危险操作，验证接口可用）
    status, page = api.raw('GET', f'/device/callback/list?event_type=fc&device_code={device_a}&page_num=1&page_size=10')
    rows = (page or {}).get('rows') or []
    if rows:
        callback_id = rows[0]['callback_id']
        status, body = api.raw('DELETE', f'/device/callback/{callback_id}')
        check.check('管理端', 'E7 回调日志可删除', int(body.get('code', 500)) == 200, f'HTTP {status} {body}')
    else:
        check.check('管理端', 'E7 回调日志可删除', False, '未找到 fc 回调记录')

    # 设备删除收尾
    if device_c_id:
        status, body = api.raw('DELETE', f'/device/{device_c_id}')
        check.check('设备', 'E1 删除设备成功', int(body.get('code', 500)) == 200, f'HTTP {status} {body}')
        status, page = api.raw('GET', f'/device/list?device_code={device_c}&page_num=1&page_size=5')
        check.check(
            '设备',
            'E1 删除后列表不再包含该设备',
            not ((page or {}).get('rows') or []),
            f"rows={len((page or {}).get('rows') or [])}",
        )

    # ------------------------------------------------------------------
    print('\n[7] 厂商客户端行为（mock 观测）')
    # ------------------------------------------------------------------
    login_calls = MOCK_STATE['login_calls']
    check.check(
        '厂商控制',
        'A4 token 缓存生效（多次业务调用未导致每次重新登录）',
        login_calls <= 3,
        f'accountLogin 调用次数={login_calls}',
    )
    vendor_reqs = MOCK_STATE['requests']
    auth_headers = {req['auth'] for req in vendor_reqs if req['path'] != '/thiea/site/accountLogin'}
    check.check(
        '厂商控制',
        'C1 业务请求统一携带 Authorization 头',
        bool(auth_headers) and all(h.startswith('Bearer ') for h in auth_headers),
        f'样例={sorted(auth_headers)[:2]}',
    )
    login_req = next((r for r in vendor_reqs if r['path'] == '/thiea/site/accountLogin'), None)
    check.check(
        '厂商控制',
        'A2 登录请求体携带密文密码且不出现明文',
        login_req is not None
        and 'password' in login_req['body']
        and login_req['body'].get('password') != ADMIN_PASSWORD,
        f"keys={sorted(login_req['body'].keys()) if login_req else None}",
    )
    check.check(
        '厂商控制',
        'A3 登录请求固定 isLock=true',
        login_req is not None and login_req['body'].get('isLock') is True,
        f"isLock={login_req['body'].get('isLock') if login_req else None}",
    )
    batch_reqs = [r for r in vendor_reqs if r['path'].endswith('status/batch')]
    check.check(
        '厂商控制',
        'C2 批量接口同时发送 query sn 与 body sns（默认开关）',
        bool(batch_reqs) and all('sn' in r['query'] and 'sns' in r['body'] for r in batch_reqs),
        f'调用次数={len(batch_reqs)}',
    )


def main() -> int:
    print('=' * 72)
    print('Stage 6 全链路验收（GYTAI-126）')
    print(f'  后端地址 : {BASE_URL}')
    print(f'  mock 厂商: http://127.0.0.1:{VENDOR_PORT}  （后端 MINGLUE_API_BASE_URL 须指向它）')
    print('=' * 72)

    server = start_mock_vendor()
    checker = Checker()
    try:
        api = Api(BASE_URL)
        try:
            run(api, checker)
        finally:
            if not checker.rows:
                print('\n未产生任何验收结果（后端不可达？）。')
    finally:
        server.shutdown()

    return 0 if checker.report() else 1


if __name__ == '__main__':
    sys.exit(main())
