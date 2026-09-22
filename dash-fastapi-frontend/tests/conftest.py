"""前端测试装配。

Dash 应用自身依赖 ``dash`` / ``feffery-antd-components`` 等运行时组件库，而这些包
在本仓库的 CI/本地校验环境里并不一定安装。为了让设备页面与回调模块的**业务逻辑**
（列表筛选、详情字段、控制操作、权限显隐、错误与部分失败反馈、报文脱敏）能在干净
环境里被直接回归，这里在导入应用代码之前把缺失的第三方模块替换成轻量桩：

- 组件桩按名字动态生成，只保留 ``children`` 与关键字参数，不做任何渲染；
- ``dash`` 桩提供 ``ctx`` / ``no_update`` / ``set_props`` / ``Dash``，其中
  ``set_props`` 会把全局提示（AntdMessage / AntdNotification）记录下来，供断言
  "成功 / 部分失败 / 超时 / 无权限" 等反馈文案；
- ``cachebox`` / ``loguru`` / ``user_agents`` 等仅被上层工具模块引用，给最小实现。

因此本目录的测试只覆盖回调层逻辑，不作为浏览器渲染验证；真实渲染仍需在安装完整
依赖的环境启动前端。
"""

import sys
import types
from pathlib import Path

import pytest

FRONTEND_ROOT = Path(__file__).resolve().parent.parent
if str(FRONTEND_ROOT) not in sys.path:
    sys.path.insert(0, str(FRONTEND_ROOT))


# ---------------------------------------------------------------------------
# 记录型的全局提示容器
# ---------------------------------------------------------------------------
CAPTURED_PROPS = []


class _CallbackContext:
    """``dash.ctx`` 的最小替代，测试直接给 ``triggered_id`` 赋值。"""

    triggered_id = None


class _NoUpdate:
    def __repr__(self):
        return 'no_update'

    def __bool__(self):
        return False


class _Component:
    """轻量组件：只记住 children 与 kwargs。"""

    def __init__(self, *children, **kwargs):
        self.children = children
        self.kwargs = kwargs

    def __repr__(self):
        return f'<{type(self).__name__} {self.kwargs}>'


class _StubModule(types.ModuleType):
    """按属性名动态生成组件类，覆盖 fac/fuc/dcc/html 的全部组件。"""

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        component = type(name, (_Component,), {})
        setattr(self, name, component)
        return component


class _FakeFlaskApp:
    """``app.server`` 需要的最小 Flask 应用表面。"""

    def __init__(self):
        self.config = {}
        self.secret_key = None

    def before_request(self, func):
        return func


class _FakeDash:
    """``Dash`` 的最小替代：注册回调并原样返回被装饰函数。"""

    def __init__(self, *args, **kwargs):
        self.registered = []
        self.clientside = []
        self.server = _FakeFlaskApp()
        self.config = {}
        self.title = ''

    def callback(self, *args, **kwargs):
        def decorator(func):
            self.registered.append({'func': func, 'options': kwargs})
            return func

        return decorator

    def clientside_callback(self, *args, **kwargs):
        self.clientside.append({'args': args, 'options': kwargs})
        return None


class _FlaskSession(dict):
    pass


def _install_stubs():
    """把缺失（或需要可控行为）的第三方模块替换为桩。"""
    dash = _StubModule('dash')
    dash.ctx = _CallbackContext()
    dash.no_update = _NoUpdate()
    dash.Dash = _FakeDash
    dash.dcc = _StubModule('dash.dcc')
    dash.html = _StubModule('dash.html')

    def set_props(target_id, props, namespace=None):
        CAPTURED_PROPS.append(
            {'id': target_id, 'props': props, 'namespace': namespace}
        )

    dash.set_props = set_props
    dash.register_page = lambda *a, **k: None

    dependencies = _StubModule('dash.dependencies')

    class _Dependency:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __repr__(self):
            return f'{type(self).__name__}{self.args}'

    for name in ('Input', 'Output', 'State', 'ClientsideFunction'):
        setattr(dependencies, name, type(name, (_Dependency,), {}))
    dependencies.ALL = object()
    dependencies.MATCH = object()

    exceptions = types.ModuleType('dash.exceptions')

    class PreventUpdate(Exception):
        pass

    class NoUpdate(Exception):
        pass

    exceptions.PreventUpdate = PreventUpdate
    exceptions.NoUpdate = NoUpdate
    dash.dependencies = dependencies
    dash.exceptions = exceptions

    dash_exceptions = types.ModuleType('dash.exceptions')
    dash_exceptions.PreventUpdate = PreventUpdate
    dash_exceptions.NoUpdate = NoUpdate

    user_agents = types.ModuleType('user_agents')

    class _Browser:
        family = 'Chrome'
        version = (120, 0)

    class _UserAgent:
        browser = _Browser()

    user_agents.parse = lambda _value: _UserAgent()

    cachebox = types.ModuleType('cachebox')

    class _Cache(dict):
        def __init__(
            self, maxsize=None, iterable=None, capacity=None, ttl=None
        ):
            super().__init__()
            self.maxsize = maxsize
            self.capacity = capacity
            self.ttl = ttl

        def insert(self, key, value):
            self[key] = value

    cachebox.LRUCache = _Cache
    cachebox.TTLCache = _Cache

    loguru = types.ModuleType('loguru')

    class _Logger:
        def add(self, *args, **kwargs):
            return 0

        def _noop(self, *args, **kwargs):
            return None

        info = warning = error = exception = debug = critical = _noop

    loguru.logger = _Logger()

    for name in (
        'dash',
        'dash.dependencies',
        'dash.exceptions',
        'feffery_antd_components',
        'feffery_utils_components',
        'feffery_antd_charts',
        'jsonpath_ng',
        'waitress',
        'cachebox',
        'loguru',
        'user_agents',
    ):
        module = {
            'dash': dash,
            'dash.dependencies': dependencies,
            'dash.exceptions': dash_exceptions,
            'cachebox': cachebox,
            'loguru': loguru,
            'user_agents': user_agents,
        }.get(name) or _StubModule(name)
        sys.modules[name] = module

    return dash


DASH_STUB = _install_stubs()


def _preload_config_env():
    """``config/env.py`` 在导入期解析命令行参数，会和 pytest 自己的参数冲突。

    这里先用"干净"的 argv 完成一次导入，后续模块直接命中 ``sys.modules`` 缓存。
    """
    original_argv = sys.argv
    sys.argv = [original_argv[0] if original_argv else 'pytest']
    try:
        import config.env  # noqa: F401
    finally:
        sys.argv = original_argv


_preload_config_env()


def message_events():
    """把捕获到的全局提示解析成 ``{'type', 'content'}`` 列表。"""
    events = []
    for captured in CAPTURED_PROPS:
        children = (captured.get('props') or {}).get('children')
        if not isinstance(children, dict):
            continue
        props = children.get('props') or {}
        events.append(
            {
                'type': props.get('type'),
                'content': props.get('content'),
                'message': props.get('message'),
                'description': props.get('description'),
            }
        )
    return events


@pytest.fixture(autouse=True)
def clear_messages():
    CAPTURED_PROPS.clear()
    yield
    CAPTURED_PROPS.clear()


@pytest.fixture(autouse=True)
def reset_triggered_id():
    DASH_STUB.ctx.triggered_id = None
    yield
    DASH_STUB.ctx.triggered_id = None


@pytest.fixture
def messages():
    return message_events


@pytest.fixture
def dash_ctx():
    """``dash.ctx`` 桩：测试通过它的 ``triggered_id`` 指定回调触发源。"""
    return DASH_STUB.ctx


@pytest.fixture
def session_context():
    """提供 flask 请求上下文与当前登录会话，供 CacheManager 读取权限。"""
    from flask import Flask, session

    from utils.cache_util import CacheManager

    flask_app = Flask(__name__)
    flask_app.secret_key = 'frontend-test-secret'
    with flask_app.test_request_context('/'):
        session['Authorization'] = 'frontend-test-token'
        CacheManager.clear()
        yield _SessionHelper(CacheManager, session)
        CacheManager.clear()


class _SessionHelper:
    def __init__(self, cache_manager, session):
        self._cache_manager = cache_manager
        self._session = session

    def login(self, perms=None, roles=None):
        """模拟登录后的权限缓存；``perms=None`` 表示超级管理员通配权限。"""
        self._cache_manager.set(
            {
                'user_info': {'user_name': 'tester'},
                'permissions': {
                    'perms': ['*:*:*'] if perms is None else list(perms),
                    'roles': roles or [],
                },
            }
        )
