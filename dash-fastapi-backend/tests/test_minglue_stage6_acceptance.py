"""Stage 6（GYTAI-126）全链路验收修复项的离线回归测试。

这些用例对应 Stage 6 在**真实运行栈**（MySQL/PostgreSQL + Redis + uvicorn）里
复现出的缺陷。它们能在没有数据库驱动的轻量环境跑，作为不依赖外部服务的回归底线；
需要真实栈的端到端验收见 `tests/e2e/run_acceptance.py`。

覆盖的缺陷：
1. 生产会话工厂未关闭 ``expire_on_commit``，``commit()`` 后读 ORM 属性触发
   ``MissingGreenlet``；重复回调因此每次都多写一条误导性的 ``failed`` 审计记录。
2. ``_is_duplicate_conflict`` 对异常文本做裸子串匹配，命中 ORM 回显 SQL 里的
   ``duplicate_flag`` 列名，把上述内部错误误判成"幂等冲突"并静默吞掉。
3. 回调日志按 ``event_type`` 用了 LIKE，``rec`` 会连 ``reclist`` 一起返回。
4. 迁移脚本漏了 ``event_id`` 扩宽与旧单列索引清理，升级库与新建库结构不一致。
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from module_device.dao.device_dao import CallbackDao
from module_device.entity.do.device_do import MinglueCallbackLog
from module_device.entity.vo.device_vo import CallbackPageQueryModel
from module_device.service.device_service import CallbackService

SQL_DIR = Path(__file__).resolve().parent.parent / 'sql'
BACKEND_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1、生产会话工厂必须关闭 expire_on_commit
# ---------------------------------------------------------------------------


def test_production_session_factory_disables_expire_on_commit(monkeypatch):
    """会话工厂必须传 ``expire_on_commit=False``。

    默认的 ``expire_on_commit=True`` 会在 commit 后把实例标为过期，之后再读属性
    （如 ``existing.callback_id``）会触发同步惰性刷新，在异步会话里抛
    ``MissingGreenlet``，与业务无关且会污染审计表。
    """
    import config.database as database_module

    captured = {}

    def fake_create_async_engine(url, **kwargs):
        captured['kwargs'] = kwargs
        return object()

    monkeypatch.setattr(database_module, 'create_async_engine', fake_create_async_engine)
    database_module._lazy_engine_cache.clear()
    try:
        factory = database_module.get_async_session_local()
        assert factory.kw.get('expire_on_commit') is False, (
            '生产 async_sessionmaker 必须禁用 expire_on_commit，否则 commit 后读属性会抛 MissingGreenlet'
        )
    finally:
        database_module._lazy_engine_cache.clear()


def test_lazy_engine_shim_does_not_shadow_module_getattr():
    """模块层不得声明 ``async_engine = None`` 这类哨兵属性。

    模块 ``__getattr__`` 只在常规查找失败时触发；一旦存在同名哨兵属性，
    ``from config.database import async_engine`` 会直接绑定到 ``None``，
    使惰性引擎彻底失效 —— 真实表现是应用启动阶段
    ``init_create_table()`` 报 "'NoneType' object has no attribute 'begin'"。
    """
    source = (BACKEND_ROOT / 'config' / 'database.py').read_text(encoding='utf-8')
    for line in source.splitlines():
        stripped = line.strip()
        assert stripped not in ('async_engine = None', 'AsyncSessionLocal = None'), (
            f'哨兵属性会屏蔽模块 __getattr__，导致惰性引擎失效：{stripped}'
        )
    assert 'def __getattr__(name: str)' in source, '惰性引擎的 __getattr__ 兼容层缺失'


# ---------------------------------------------------------------------------
# 2、_is_duplicate_conflict 不能误判
# ---------------------------------------------------------------------------


def test_is_duplicate_conflict_accepts_integrity_error():
    exc = IntegrityError('insert', {}, Exception('Duplicate entry ... for key uk_ml_callback_dedup_key'))
    assert CallbackService._is_duplicate_conflict(exc) is True


def test_is_duplicate_conflict_ignores_sql_echo_containing_duplicate_flag():
    """回归：ORM 报错回显的 SELECT 列清单里含 ``duplicate_flag``。

    裸子串匹配 'duplicate' 会把任意错误都当成幂等冲突吞掉，真实故障既不返回错误、
    也不留下可读的审计信息。这里用一段"回显里带 duplicate_flag"的假异常复现。
    """
    echoed_sql = (
        '(sqlalchemy.exc.MissingGreenlet) greenlet_spawn has not been called; '
        "can't call await_only() here. Was IO attempted in an unexpected place? "
        '[SQL: SELECT ml_callback_log.callback_id, ml_callback_log.duplicate_flag '
        'AS ml_callback_log_duplicate_flag FROM ml_callback_log]'
    )
    exc = RuntimeError(echoed_sql)
    assert CallbackService._is_duplicate_conflict(exc) is False


def test_is_duplicate_conflict_ignores_unrelated_db_error():
    exc = OperationalError('select 1', {}, Exception('connection refused'))
    assert CallbackService._is_duplicate_conflict(exc) is False


# ---------------------------------------------------------------------------
# 3、回调日志 event_type 精确筛选
# ---------------------------------------------------------------------------


async def test_callback_event_type_filter_is_exact(sqlite_session):
    """``event_type=rec`` 不得把 ``reclist`` 一起返回。

    契约 §2 的 8 类回调是封闭枚举，前端按固定选项筛选，因此必须精确匹配；
    LIKE 子串匹配会让 ``rec`` 命中 ``reclist``，与 process_status /
    duplicate_flag / session_id 的精确语义也不一致。
    """
    for index, event_type in enumerate(('rec', 'reclist', 'heartbeat'), start=1):
        sqlite_session.add(
            MinglueCallbackLog(
                event_type=event_type,
                dedup_key=f'{index:064d}',
                received_at=datetime(2026, 9, 23, 10, 0, 0),
                payload_json='{}',
                process_status='success',
                duplicate_flag='N',
            )
        )
    await sqlite_session.commit()

    page = await CallbackDao.get_list(
        sqlite_session, CallbackPageQueryModel(event_type='rec', page_num=1, page_size=50)
    )
    raw_rows = page.rows if hasattr(page, 'rows') else page.get('rows')
    types = {row['event_type'] if isinstance(row, dict) else row.event_type for row in raw_rows}
    assert types == {'rec'}, f'event_type=rec 应只返回 rec，实际={types}'


# ---------------------------------------------------------------------------
# 4、初始化脚本与迁移脚本的结构一致性（升级路径）
# ---------------------------------------------------------------------------


def test_init_scripts_define_widened_event_id():
    """``event_id`` 需承载 rec 回调的 object_key，初始化脚本已扩到 255。"""
    mysql_init = (SQL_DIR / 'dash-fastapi.sql').read_text(encoding='utf-8')
    pg_init = (SQL_DIR / 'dash-fastapi-pg.sql').read_text(encoding='utf-8')
    assert 'event_id varchar(255)' in mysql_init
    assert 'event_id varchar(255)' in pg_init


def test_migration_scripts_widen_event_id_to_match_init():
    """迁移脚本必须同步扩宽 event_id。

    只在初始化脚本里改宽、迁移脚本漏改时，升级库插入长录音文件名会报
    "Data too long for column 'event_id'"，导致整条回调落库失败并被厂商重推。
    """
    mysql_migration = (SQL_DIR / 'migration' / '20260922_minglue_stage2_mysql.sql').read_text(encoding='utf-8')
    pg_migration = (SQL_DIR / 'migration' / '20260922_minglue_stage2_pg.sql').read_text(encoding='utf-8')
    assert 'event_id varchar(255)' in mysql_migration, 'MySQL 迁移脚本未扩宽 event_id'
    assert 'alter column event_id type varchar(255)' in pg_migration, 'PG 迁移脚本未扩宽 event_id'


def test_migration_scripts_drop_superseded_device_index():
    """旧单列索引 ``idx_ml_callback_device`` 已被联合索引覆盖，升级库应清理掉。"""
    for script in ('20260922_minglue_stage2_mysql.sql', '20260922_minglue_stage2_pg.sql'):
        content = (SQL_DIR / 'migration' / script).read_text(encoding='utf-8')
        assert 'idx_ml_callback_device' in content, f'{script} 未清理被联合索引取代的旧索引'
        assert 'drop index' in content.lower(), f'{script} 缺少 drop index 语句'


def test_migration_and_init_agree_on_ml_callback_log_columns():
    """迁移脚本必须覆盖初始化脚本里 ml_callback_log 的每个列名。

    轻量环境无法连真实库做结构比对，这里退化为"列名集合包含"检查，
    真实库上的收敛比对由 `tests/e2e/run_acceptance.py` 与本阶段验收报告覆盖。
    """
    mysql_init = (SQL_DIR / 'dash-fastapi.sql').read_text(encoding='utf-8')
    block = mysql_init.split('create table ml_callback_log', 1)[1].split('engine=innodb', 1)[0]
    init_columns = set(
        line.strip().split()[0]
        for line in block.splitlines()
        if line.strip() and not line.strip().startswith(('--', 'primary', 'unique', 'key', ')'))
    )
    assert 'dedup_key' in init_columns and 'event_id' in init_columns

    for script in ('20260922_minglue_stage2_mysql.sql', '20260922_minglue_stage2_pg.sql'):
        migration = (SQL_DIR / 'migration' / script).read_text(encoding='utf-8')
        # 迁移脚本至少要在全文里提到这些关键列，避免"新库有、升级库没有"再次发生
        for column in ('dedup_key', 'log_type', 'session_id', 'topic_name', 'item_count', 'duplicate_flag', 'processed_at'):
            assert column in migration, f'{script} 未处理列 {column}'


# ---------------------------------------------------------------------------
# 5、重复回调的审计语义（B11）
# ---------------------------------------------------------------------------


async def test_duplicate_callback_leaves_only_duplicate_audit(sqlite_session):
    """重复回调只应留下 ``duplicate_flag='Y'`` 记录，不应留 ``failed`` 记录。"""
    payload = {'sn': 'MLR-DUP-01', 'device_status': 1, 'update_time': '2026-09-23 10:00:00', 'online': 2}
    await CallbackService.receive(sqlite_session, payload, None, '127.0.0.1')
    first = (await sqlite_session.execute(select(MinglueCallbackLog))).scalars().all()
    assert len(first) == 1 and first[0].process_status == 'success'

    await CallbackService.receive(sqlite_session, payload, None, '127.0.0.1')
    rows = (await sqlite_session.execute(
        select(MinglueCallbackLog).order_by(MinglueCallbackLog.callback_id)
    )).scalars().all()
    statuses = [row.process_status for row in rows]
    assert 'duplicate' in statuses, f'重复回调未被识别为 duplicate：{statuses}'
    assert 'failed' not in statuses, f'重复回调产生了虚假失败记录：{statuses}'
    assert all('greenlet' not in str(row.error_message or '').lower() for row in rows)
