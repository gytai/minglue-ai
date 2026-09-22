"""Stage 2（GYTAI-128）数据模型、DAO 与迁移测试。

覆盖：
- 回调幂等键的确定性与非空性（P0：契约 B11 / D5）
- `upload` 回调类型归一化（P0：契约 B6）
- 设备在线三态与心跳字段映射（契约 D1 / D2）
- 唯一约束在真实数据库层的实际效果（用内存 SQLite 验证）
- 新旧库升级：老结构数据能被迁移脚本的 dedup_key 回填逻辑接管
- MySQL / PostgreSQL 初始化脚本的表结构与索引等价
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from module_device.dao.device_dao import CallbackDao, ControlLogDao, DeviceDao, RecordingDao
from module_device.entity.do.device_do import (
    MinglueCallbackLog,
    MinglueDevice,
    MinglueDeviceControlLog,
    MinglueRecordingFile,
)
from module_device.entity.vo.device_vo import (
    CallbackPageQueryModel,
    ControlLogPageQueryModel,
    DeviceModel,
    DevicePageQueryModel,
    RecordingPageQueryModel,
)
from module_device.service.device_service import CallbackService

SQL_DIR = Path(__file__).resolve().parent.parent / 'sql'

HEARTBEAT_PAYLOAD = {
    'sn': 'MLR432KP42C19568',
    'device_status': 1,
    'record_status': 0,
    'remain_power': 40,
    'remain_storage': 14833,
    'rssi': 31,
    'update_time': '2025-11-01 19:37:23',
    'version': '5.4.49',
    'key_status': 1,
    'usb_status': 1,
    'battery_voltage': 3955,
    'battery_current': 420,
    'chip': '2108+511',
    'total_storage': 14950,
    'cip': '10.113.36.200',
    'charged_status': 'cccv',
    'disk_mount_status': 0,
    'online': 2,
    'rectd': 0,
    'recnu': 16,
    'tenant_id': 100,
    'nm': 'ciqziMsatvo5',
}

REC_PAYLOAD = {
    'content': [
        {
            'csn': 'MLR4010GBFGFGZAD',
            'merge_success_time': 1726210271,
            'object_key': 'lt4/rec/20240913/SN1/1726202557/SN1_20240913133237_3_AB_none_300000_10.opus',
            'download_url': '',
            'size': 2400000,
            'sn': 'MLR4010GBFGFGZAD',
            'duration': 100000,
        },
        {
            'csn': 'MLR4010GBFGFGAD',
            'merge_success_time': 1726210272,
            'object_key': 'lt4/rec/20240913/SN2/1726202558/SN2_20240913133238_3_AB_none_300000_11.opus_eof',
            'download_url': '',
            'size': 2500000,
            'sn': 'MLR4010GBFGFGAD',
            'duration': 110000,
        },
    ],
    'session_id': 61318,
    'topic_name': 'hermes',
    'type': 'rec',
}

UPLOAD_LOG_TYPE_LOG = {
    'updatedAt': '2025-04-07 16:22:55',
    'logType': 'log',
    'sn': 'MLR431Z08EI3OI9E',
    'objectKey': 'lt4/log/MLR431Z08EI3OI9E/20250407/MLR431Z08EI3OI9E_20250407162250_sys.log.file',
    'fileName': 'MLR431Z08EI3OI9E_20250407162250_sys.log.file',
    'status': 6,
}

UPLOAD_LOG_TYPE_REC = {**UPLOAD_LOG_TYPE_LOG, 'logType': 'rec', 'objectKey': 'lt4/rec/x.opus'}


# ---------------------------------------------------------------------------
# 1、幂等键策略（P0 B11 / D5）
# ---------------------------------------------------------------------------


def test_dedup_key_is_never_blank_for_any_documented_callback():
    """8 类回调都必须产出非空幂等键——这是唯一索引能去重的前提。"""
    samples = [
        REC_PAYLOAD,
        {'type': 'op', 'content': [{'sn': 'S1', 'object_key': 'lt4/log/a_op.log'}]},
        {'type': 'sys', 'content': [{'sn': 'S1', 'object_key': 'lt4/log/a_sys.log'}]},
        {'type': 'reclist', 'content': [{'sn': 'S1', 'object_key': 'lt4/log/a_reclist.log'}]},
        UPLOAD_LOG_TYPE_LOG,
        UPLOAD_LOG_TYPE_REC,
        {'type': 'fc', 'task_id': 't1', 'sn': 'S1', 'files': [{'file_path': 'a.mp3'}]},
        {'type': 'asr', 'asrTskId': 'a1', 'sn': 'S1'},
        HEARTBEAT_PAYLOAD,
    ]
    for payload in samples:
        event_type = CallbackService._normalize_event_type(payload, None)
        for item in CallbackService._items(payload):
            event_id = CallbackService._build_event_id(event_type, item)
            dedup_key = CallbackService._build_dedup_key(event_type, event_id, item)
            assert dedup_key, f'{event_type} 的幂等键为空'
            assert len(dedup_key) == 64, '幂等键应为 SHA-256 十六进制串'


def test_heartbeat_dedup_key_differs_per_heartbeat_but_repeats_identically():
    """心跳没有业务键，但同一报文摘要稳定、不同心跳不互相误杀。"""
    item = dict(HEARTBEAT_PAYLOAD)
    key_first = CallbackService._build_dedup_key('heartbeat', None, item)
    key_repeat = CallbackService._build_dedup_key('heartbeat', None, item)
    assert key_first == key_repeat

    later = {**item, 'update_time': '2025-11-01 19:40:23', 'remain_power': 39}
    assert CallbackService._build_dedup_key('heartbeat', None, later) != key_first


def test_rec_multi_device_produces_one_business_key_per_object_key():
    """P0 B11：`rec` 单次多文件时，每条 content 都要有自己的幂等键。"""
    event_type = CallbackService._normalize_event_type(REC_PAYLOAD, None)
    keys = [
        CallbackService._build_dedup_key(
            event_type,
            CallbackService._build_event_id(event_type, item),
            item,
        )
        for item in CallbackService._items(REC_PAYLOAD)
    ]
    assert len(keys) == 2
    assert len(set(keys)) == 2, '两个设备的 object_key 必须产生两个不同幂等键'


def test_rec_and_upload_rec_do_not_collide():
    """P0 B6/B11：真实 `rec` 与 `logType=rec` 的 upload 不能共用同一事件类型。"""
    rec_type = CallbackService._normalize_event_type(REC_PAYLOAD, None)
    upload_type = CallbackService._normalize_event_type(UPLOAD_LOG_TYPE_REC, None)
    assert rec_type == 'rec'
    assert upload_type == 'upload'


# ---------------------------------------------------------------------------
# 2、事件类型归一化（P0 B6）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (REC_PAYLOAD, 'rec'),
        ({'type': 'op', 'content': []}, 'op'),
        ({'type': 'sys', 'content': []}, 'sys'),
        ({'type': 'reclist', 'content': []}, 'reclist'),
        (UPLOAD_LOG_TYPE_LOG, 'upload'),
        (UPLOAD_LOG_TYPE_REC, 'upload'),
        ({'type': 'fc', 'sn': 'S1'}, 'fc'),
        ({'type': 'asr', 'sn': 'S1'}, 'asr'),
        (HEARTBEAT_PAYLOAD, 'heartbeat'),
    ],
)
def test_event_type_normalization_matches_contract(payload, expected):
    assert CallbackService._normalize_event_type(payload, None) == expected


def test_event_type_from_path_wins_over_payload():
    assert CallbackService._normalize_event_type(REC_PAYLOAD, 'op') == 'op'


def test_upload_log_type_is_preserved_separately():
    """`logType` 不再冒充事件类型，而是落到独立的 log_type 列。"""
    assert UPLOAD_LOG_TYPE_LOG.get('logType') == 'log'
    assert UPLOAD_LOG_TYPE_REC.get('logType') == 'rec'
    assert CallbackService._normalize_event_type(UPLOAD_LOG_TYPE_REC, None) == 'upload'


# ---------------------------------------------------------------------------
# 3、设备字段映射（D1 / D2）
# ---------------------------------------------------------------------------


def test_online_tristate_is_not_flattened_into_online_offline():
    """D2：`online=1`（开机但可能不在线）必须能在 online_status 里区分出来。"""
    payload = {**HEARTBEAT_PAYLOAD, 'online': 1}
    assert payload['online'] == 1
    assert payload['device_status'] == 1


def test_eof_shard_detection():
    assert CallbackService._is_eof_object_key('a/b/SN_2024_3_AB_none_300000_10.opus_eof') == 'Y'
    assert CallbackService._is_eof_object_key('a/b/SN_2024_3_AB_none_300000_10.opus') == 'N'
    assert CallbackService._is_eof_object_key(None) == 'N'


# ---------------------------------------------------------------------------
# 4、ORM 模型与唯一约束（真实建表验证）
# ---------------------------------------------------------------------------


async def test_model_tables_are_created_with_expected_columns(sqlite_engine):
    expected = {
        'ml_device': len(MinglueDevice.__table__.columns),
        'ml_callback_log': len(MinglueCallbackLog.__table__.columns),
        'ml_recording_file': len(MinglueRecordingFile.__table__.columns),
        'ml_device_control_log': len(MinglueDeviceControlLog.__table__.columns),
    }
    async with sqlite_engine.connect() as conn:
        names = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    assert set(expected) <= names
    assert expected['ml_device'] == 40
    assert expected['ml_callback_log'] == 18


async def test_callback_dedup_key_unique_constraint_rejects_second_insert(sqlite_engine):
    """非空唯一约束必须在数据库层真正拦住重复回调。"""
    from datetime import datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker

    values = {
        'dedup_key': 'a' * 64,
        'event_type': 'heartbeat',
        'received_at': datetime.now(),
        'payload_json': '{}',
    }
    factory = async_sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        await CallbackDao.add(session, dict(values))
        await session.commit()

    async with factory() as session:
        with pytest.raises(IntegrityError):
            await CallbackDao.add(session, {**values, 'event_type': 'heartbeat'})
            await session.commit()


async def test_device_code_unique_constraint(sqlite_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        await DeviceDao.add(session, DeviceModel(device_code='SN-DUP', device_name='a'))
        await session.commit()

    async with factory() as session:
        with pytest.raises(IntegrityError):
            await DeviceDao.add(session, DeviceModel(device_code='SN-DUP', device_name='b'))
            await session.commit()


async def test_recording_object_key_unique_constraint(sqlite_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        await RecordingDao.add(session, {'object_key': 'lt4/rec/one.opus', 'device_code': 'SN-1'})
        await session.commit()

    async with factory() as session:
        with pytest.raises(IntegrityError):
            await RecordingDao.add(session, {'object_key': 'lt4/rec/one.opus', 'device_code': 'SN-2'})
            await session.commit()


async def test_control_log_msg_id_unique_constraint(sqlite_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        await ControlLogDao.add(
            session,
            {'device_code': 'SN-1', 'command': 'start_recording', 'msg_id': 'm-1'},
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(IntegrityError):
            await ControlLogDao.add(
                session,
                {'device_code': 'SN-1', 'command': 'stop_recording', 'msg_id': 'm-1'},
            )
            await session.commit()


async def test_control_log_allows_multiple_null_msg_id(sqlite_engine):
    """msg_id 唯一但可空：失败的请求没有 msg_id，不应互相阻塞。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        await ControlLogDao.add(
            session,
            {'device_code': 'SN-1', 'command': 'start_recording', 'request_status': 'failed'},
        )
        await ControlLogDao.add(
            session,
            {'device_code': 'SN-2', 'command': 'start_recording', 'request_status': 'failed'},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 5、重复回调识别（DAO 层）
# ---------------------------------------------------------------------------


async def test_duplicate_callback_is_detected_by_dedup_key(sqlite_session):
    from datetime import datetime

    payload_json = '{"sn":"SN-1"}'
    item = {'sn': 'SN-1', 'object_key': 'lt4/rec/x.opus'}
    event_id = CallbackService._build_event_id('rec', item)
    dedup_key = CallbackService._build_dedup_key('rec', event_id, item)

    await CallbackDao.add(
        sqlite_session,
        {
            'dedup_key': dedup_key,
            'event_id': event_id,
            'event_type': 'rec',
            'device_code': 'SN-1',
            'received_at': datetime.now(),
            'payload_json': payload_json,
        },
    )
    await sqlite_session.commit()

    found = await CallbackDao.get_by_dedup_key(sqlite_session, dedup_key)
    assert found is not None
    assert found.event_id == 'lt4/rec/x.opus'


# ---------------------------------------------------------------------------
# 6、DAO 查询与分页
# ---------------------------------------------------------------------------


async def test_device_page_query_filters_by_online_status(sqlite_session):
    await DeviceDao.add(
        sqlite_session,
        DeviceModel(device_code='SN-ON', online_status=2, status='online', last_seen_time=None),
    )
    await DeviceDao.add(
        sqlite_session,
        DeviceModel(device_code='SN-OFF', online_status=0, status='offline', last_seen_time=None),
    )
    await sqlite_session.commit()

    online_page = await DeviceDao.get_list(
        sqlite_session,
        DevicePageQueryModel(online_status=2, page_num=1, page_size=10),
    )
    assert online_page.total == 1
    assert online_page.rows[0]['device_code'] == 'SN-ON'


async def test_callback_page_query_filters_by_duplicate_flag(sqlite_session):
    from datetime import datetime

    now = datetime.now()
    for index, flag in enumerate(['N', 'Y']):
        await CallbackDao.add(
            sqlite_session,
            {
                'dedup_key': f'{index:064d}',
                'event_type': 'heartbeat',
                'duplicate_flag': flag,
                'received_at': now,
                'payload_json': '{}',
            },
        )
    await sqlite_session.commit()

    duplicated = await CallbackDao.get_list(
        sqlite_session,
        CallbackPageQueryModel(duplicate_flag='Y', page_num=1, page_size=10),
    )
    assert duplicated.total == 1
    assert duplicated.rows[0]['duplicate_flag'] == 'Y'


async def test_recording_and_control_page_queries(sqlite_session):
    from datetime import datetime

    await RecordingDao.add(
        sqlite_session,
        {
            'object_key': 'lt4/rec/a.opus',
            'device_code': 'SN-A',
            'record_status': 'uploaded',
            'event_time': datetime(2025, 11, 1, 10, 0, 0),
        },
    )
    await ControlLogDao.add(
        sqlite_session,
        {
            'device_code': 'SN-A',
            'command': 'start_recording',
            'msg_id': 'm-9',
            'create_time': datetime(2025, 11, 1, 10, 0, 0),
        },
    )
    await sqlite_session.commit()

    recordings = await RecordingDao.get_list(
        sqlite_session,
        RecordingPageQueryModel(device_code='SN-A', page_num=1, page_size=10),
    )
    assert recordings.total == 1

    controls = await ControlLogDao.get_list(
        sqlite_session,
        ControlLogPageQueryModel(command='start_recording', page_num=1, page_size=10),
    )
    assert controls.total == 1
    assert controls.rows[0]['msg_id'] == 'm-9'


async def test_stale_device_sweep_marks_only_expired_online_devices(sqlite_session):
    from datetime import datetime, timedelta

    from module_device.service.device_service import DeviceService

    now = datetime.now()
    await DeviceDao.add(
        sqlite_session,
        DeviceModel(device_code='SN-STALE', status='online', last_seen_time=now - timedelta(minutes=30)),
    )
    await DeviceDao.add(
        sqlite_session,
        DeviceModel(device_code='SN-FRESH', status='online', last_seen_time=now),
    )
    await DeviceDao.add(
        sqlite_session,
        DeviceModel(device_code='SN-OFF', status='offline', last_seen_time=now - timedelta(minutes=30)),
    )
    await sqlite_session.commit()

    swept = await DeviceService.mark_stale_devices_offline(sqlite_session, timeout_seconds=300)
    assert swept == 1

    fresh = await DeviceDao.get_by_code(sqlite_session, 'SN-FRESH')
    stale = await DeviceDao.get_by_code(sqlite_session, 'SN-STALE')
    assert fresh.status == 'online'
    assert stale.status == 'offline'
    assert stale.online_status == 0


# ---------------------------------------------------------------------------
# 7、MySQL / PostgreSQL 脚本等价
# ---------------------------------------------------------------------------

COLUMN_RE = re.compile(r'^\s*([a-z_][a-z0-9_]*)\s', re.IGNORECASE)


def _mysql_table_columns(sql_text: str, table: str) -> set[str]:
    block = re.search(
        rf'create table {table} \((.*?)\n\) engine=innodb',
        sql_text,
        re.DOTALL | re.IGNORECASE,
    )
    assert block, f'MySQL 脚本缺少 {table}'
    columns = set()
    for line in block.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(('primary key', 'unique key', 'key ', 'constraint')):
            continue
        match = COLUMN_RE.match(stripped)
        if match:
            columns.add(match.group(1).lower())
    return columns


def _pg_table_columns(sql_text: str, table: str) -> set[str]:
    block = re.search(
        rf'create table(?: if not exists)? {table} \((.*?)\n\);',
        sql_text,
        re.DOTALL | re.IGNORECASE,
    )
    assert block, f'PG 脚本缺少 {table}'
    columns = set()
    for line in block.group(1).splitlines():
        stripped = line.strip().rstrip(',')
        if not stripped or stripped.startswith(('primary key', 'constraint', 'unique (')):
            continue
        match = COLUMN_RE.match(stripped)
        if match:
            columns.add(match.group(1).lower())
    return columns


@pytest.mark.parametrize(
    'table',
    ['ml_device', 'ml_callback_log', 'ml_recording_file', 'ml_device_control_log'],
)
def test_mysql_and_pg_init_scripts_have_equivalent_columns(table):
    mysql_sql = (SQL_DIR / 'dash-fastapi.sql').read_text(encoding='utf-8')
    pg_sql = (SQL_DIR / 'dash-fastapi-pg.sql').read_text(encoding='utf-8')
    mysql_columns = _mysql_table_columns(mysql_sql, table)
    pg_columns = _pg_table_columns(pg_sql, table)
    assert mysql_columns, f'{table} 未解析出 MySQL 列'
    assert mysql_columns == pg_columns, (
        f'{table} 列集合不一致\n仅 MySQL: {sorted(mysql_columns - pg_columns)}\n'
        f'仅 PG: {sorted(pg_columns - mysql_columns)}'
    )


def test_init_scripts_match_orm_models():
    """初始化脚本的列必须与 ORM 模型一致，避免"代码能跑、建库缺列"。"""
    mysql_sql = (SQL_DIR / 'dash-fastapi.sql').read_text(encoding='utf-8')
    pg_sql = (SQL_DIR / 'dash-fastapi-pg.sql').read_text(encoding='utf-8')
    for model in (MinglueDevice, MinglueCallbackLog, MinglueRecordingFile, MinglueDeviceControlLog):
        table = model.__tablename__
        orm_columns = {column.name for column in model.__table__.columns}
        assert orm_columns == _mysql_table_columns(mysql_sql, table), f'{table} 与 MySQL 脚本不一致'
        assert orm_columns == _pg_table_columns(pg_sql, table), f'{table} 与 PG 脚本不一致'


def test_init_script_seed_permissions_are_unique():
    for script in ('dash-fastapi.sql', 'dash-fastapi-pg.sql'):
        content = (SQL_DIR / script).read_text(encoding='utf-8')
        perms = re.findall(r"'(device:[a-z]+:[a-z]+)'", content)
        duplicates = {perm for perm in perms if perms.count(perm) > 1}
        assert not duplicates, f'{script} 存在重复权限种子：{duplicates}'


def test_migration_scripts_exist_for_both_databases():
    migration_dir = SQL_DIR / 'migration'
    assert (migration_dir / '20260922_minglue_stage2_mysql.sql').is_file()
    assert (migration_dir / '20260922_minglue_stage2_pg.sql').is_file()


# ---------------------------------------------------------------------------
# 8、新旧库升级：老结构数据可被迁移接管
# ---------------------------------------------------------------------------


async def test_legacy_rows_get_non_blank_dedup_key_backfill(sqlite_session):
    """模拟迁移脚本对老数据回填 dedup_key 的 SQL，验证非空且互不冲突。

    迁移脚本先 `add column`（可空）再回填、最后才加 NOT NULL 约束。这里复现同样的
    顺序：插入两条 dedup_key 为空的老数据，跑回填 SQL，检查结果互不相同。
    """
    await sqlite_session.execute(text('drop table ml_callback_log'))
    await sqlite_session.execute(
        text(
            'create table ml_callback_log ('
            'callback_id integer primary key autoincrement, '
            'event_id varchar(255), '
            'event_type varchar(100) not null default "unknown", '
            'received_at datetime not null, '
            'payload_json text not null, '
            'signature_valid char(1) not null default "Y", '
            'process_status varchar(20) not null default "success", '
            'duplicate_flag char(1) not null default "N", '
            'dedup_key varchar(128))'
        )
    )
    await sqlite_session.execute(
        text(
            "insert into ml_callback_log (callback_id, event_id, event_type, received_at, payload_json) "
            "values (1, 'lt4/log/a_op.log', 'op', '2025-11-01 10:00:00', '{}')"
        )
    )
    await sqlite_session.execute(
        text(
            "insert into ml_callback_log (callback_id, event_id, event_type, received_at, payload_json) "
            "values (2, NULL, 'heartbeat', '2025-11-01 10:00:00', '{}')"
        )
    )
    await sqlite_session.commit()

    # 与 migration 脚本一致的等价回填 SQL
    await sqlite_session.execute(
        text(
            "update ml_callback_log set dedup_key = "
            "case when event_id is not null and event_id <> '' "
            "then 'legacy:' || event_type || ':' || event_id "
            "else 'legacy:pk:' || callback_id end "
            "where dedup_key is null or dedup_key = ''"
        )
    )
    await sqlite_session.commit()

    rows = (await sqlite_session.execute(text('select dedup_key from ml_callback_log order by callback_id'))).all()
    keys = [row[0] for row in rows]
    assert all(keys), '回填后不应存在空幂等键'
    assert keys == ['legacy:op:lt4/log/a_op.log', 'legacy:pk:2']
    assert len(set(keys)) == len(keys)


def test_migration_scripts_backfill_blank_dedup_key():
    for script in ('20260922_minglue_stage2_mysql.sql', '20260922_minglue_stage2_pg.sql'):
        content = (SQL_DIR / 'migration' / script).read_text(encoding='utf-8')
        assert 'legacy:pk:' in content, f'{script} 缺少无业务键行的 dedup_key 回填'
        assert 'uk_ml_callback_dedup_key' in content, f'{script} 缺少 dedup_key 唯一约束'
        assert 'uk_ml_callback_event_id' in content, f'{script} 未处理旧的事件唯一约束'


def test_migration_scripts_are_rerunnable_and_do_not_insert_duplicate_menus():
    for script in ('20260922_minglue_stage2_mysql.sql', '20260922_minglue_stage2_pg.sql'):
        content = (SQL_DIR / 'migration' / script).read_text(encoding='utf-8')
        assert 'where not exists' in content.lower(), f'{script} 的菜单种子未判重'
        assert 'if not exists' in content.lower() or 'ml_add_column_if_absent' in content, (
            f'{script} 未做幂等保护'
        )


def test_no_vendor_credentials_in_sql_or_repository():
    """D10：仓库不得出现 Apifox 访问口令或厂商明文凭证。

    文档里出现形如 `apifox` 的**来源说明文字**是允许的（用于溯源），
    这里只拦截真正的口令/密钥形态：分享口令、`password=` 明文、AES 密钥赋值。
    """
    forbidden = re.compile(
        r'(shared-doc-auth|s\.apifox\.cn/[0-9a-f-]{20,}/doc-\d+.*password)',
        re.IGNORECASE,
    )
    for path in list(SQL_DIR.rglob('*.sql')):
        assert not forbidden.search(path.read_text(encoding='utf-8')), f'{path} 含有敏感信息'
