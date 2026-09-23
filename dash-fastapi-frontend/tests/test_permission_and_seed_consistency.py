"""前端按钮权限与后端接口守卫、双库种子数据的一致性检查。

验收要求"菜单、角色与按钮权限在前端显示和后端校验两层一致；MySQL/PostgreSQL 种子
数据均覆盖"。这组用例直接读前端源码、后端控制器与两套 SQL 脚本，逐项比对权限标识
与角色授权，避免只在页面上加按钮、后端却 403（或反之）的漂移。
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / 'dash-fastapi-frontend'
BACKEND_ROOT = REPO_ROOT / 'dash-fastapi-backend'

FRONTEND_GATE_FILES = (
    FRONTEND_ROOT / 'views' / 'device' / 'manage' / '__init__.py',
    FRONTEND_ROOT / 'views' / 'device' / 'callback_log' / '__init__.py',
    FRONTEND_ROOT / 'callbacks' / 'device_c' / 'device_page_logic.py',
    FRONTEND_ROOT / 'callbacks' / 'device_c' / 'manage_c.py',
    FRONTEND_ROOT / 'callbacks' / 'device_c' / 'callback_log_c.py',
)
BACKEND_CONTROLLER = (
    BACKEND_ROOT / 'module_device' / 'controller' / 'device_controller.py'
)
SQL_FILES = {
    'mysql_base': BACKEND_ROOT / 'sql' / 'dash-fastapi.sql',
    'pg_base': BACKEND_ROOT / 'sql' / 'dash-fastapi-pg.sql',
    'mysql_stage5': (
        BACKEND_ROOT / 'sql' / 'migration' / '20260922_minglue_stage5_mysql.sql'
    ),
    'pg_stage5': (
        BACKEND_ROOT / 'sql' / 'migration' / '20260922_minglue_stage5_pg.sql'
    ),
}

#: 菜单/按钮种子：菜单 ID 与权限标识位于同一条 insert 语句内（可跨行）。
MENU_PERM_PATTERN = re.compile(
    r'(\d{3,4})\D[^;]*?(device:[a-z_]+:[a-z_]+)', re.S
)
#: 初始化脚本里的角色-菜单关联（MySQL 用字符串值，PG 用数字值）。
ROLE_MENU_PATTERNS = {
    'mysql': re.compile(
        r"sys_role_menu values\s*\(\s*'(\d+)'\s*,\s*'(\d+)'\s*\)"
    ),
    'pg': re.compile(r'sys_role_menu values\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)'),
}
#: Stage 5 迁移脚本里给默认角色补授的菜单（MySQL 为 union all 列表，PG 为 values 列表）。
MIGRATION_ROLE2_PATTERNS = {
    'mysql_stage5': re.compile(r"select '2'(?:,\s*| as role_id,\s*)'(\d+)'"),
    'pg_stage5': re.compile(r'\(\s*2\s*,\s*(\d+)\s*\)'),
}

#: 设备页面的按钮级权限：这些必须同时出现在前端判断和后端守卫里。
BUTTON_PERMS = {
    'device:manage:query',
    'device:manage:add',
    'device:manage:edit',
    'device:manage:remove',
    'device:manage:control',
    'device:callback:remove',
    'device:control:list',
}
#: 菜单级权限：由后端 /getRouters 返回的菜单树控制，前端不重复判断。
MENU_PERMS = {'device:manage:list', 'device:callback:list'}
#: 后端已实现但本期未建页面的权限，仍需在种子数据中存在。
BACKEND_ONLY_PERMS = {'device:recording:list'}


def frontend_gated_perms():
    perms = set()
    for path in FRONTEND_GATE_FILES:
        text = path.read_text(encoding='utf-8')
        perms.update(
            re.findall(r"check_perms\(\s*'([^']+)'", text)
            + re.findall(r"has_permission\([^,]+,\s*'([^']+)'", text)
        )
    return {perm for perm in perms if perm.startswith('device:')}


def backend_guarded_perms():
    text = BACKEND_CONTROLLER.read_text(encoding='utf-8')
    return set(re.findall(r"CheckUserInterfaceAuth\(\s*'([^']+)'", text))


def sql_menu_perms(path: Path):
    return {
        perm
        for _, perm in MENU_PERM_PATTERN.findall(
            path.read_text(encoding='utf-8')
        )
    }


def sql_role2_menu_ids(path: Path, migration: bool = False):
    text = path.read_text(encoding='utf-8')
    if migration:
        key = 'mysql_stage5' if 'mysql' in path.name else 'pg_stage5'
        return {
            int(menu_id)
            for menu_id in MIGRATION_ROLE2_PATTERNS[key].findall(text)
        }
    dialect = 'mysql' if path.name == 'dash-fastapi.sql' else 'pg'
    return {
        int(menu_id)
        for role_id, menu_id in ROLE_MENU_PATTERNS[dialect].findall(text)
        if role_id == '2'
    }


@pytest.fixture(scope='module')
def gated_perms():
    return frontend_gated_perms()


def test_frontend_gates_use_the_same_identifiers_as_backend_guards(gated_perms):
    """前端按钮/交互判断用的权限标识必须都能在后端找到同名守卫。"""
    missing = sorted(gated_perms - backend_guarded_perms())
    assert not missing, f'前端使用但后端没有守卫的权限：{missing}'


def test_button_and_menu_permissions_exist_on_both_layers(gated_perms):
    """按钮级权限两层都判断，菜单级权限由后端菜单树控制但两层都要有标识。"""
    assert BUTTON_PERMS <= gated_perms, '前端缺少按钮级权限判断'
    assert BUTTON_PERMS <= backend_guarded_perms(), '后端缺少按钮级接口守卫'
    assert MENU_PERMS <= backend_guarded_perms(), '后端缺少菜单级接口守卫'
    assert MENU_PERMS <= sql_menu_perms(SQL_FILES['mysql_base'])
    assert MENU_PERMS <= sql_menu_perms(SQL_FILES['pg_base'])
    assert BACKEND_ONLY_PERMS <= backend_guarded_perms()


@pytest.mark.parametrize('name', sorted(SQL_FILES))
def test_device_permission_seed_set_is_identical_across_databases(name):
    """MySQL / PostgreSQL 的初始化与迁移脚本必须覆盖同一组设备权限。"""
    expected = backend_guarded_perms()
    seeded = sql_menu_perms(SQL_FILES[name])
    missing = sorted(expected - seeded)
    assert not missing, f'{name} 缺少权限种子：{missing}'


def test_default_role_is_granted_the_permissions_behind_device_buttons():
    """普通角色（role_id=2）必须拿到设备页面依赖的权限，否则前端可见但接口 403。"""
    gated = frontend_gated_perms() | {
        'device:manage:list',
        'device:callback:list',
    }
    for name in ('mysql_base', 'pg_base'):
        path = SQL_FILES[name]
        menu_perms = {
            int(menu_id): perm
            for menu_id, perm in MENU_PERM_PATTERN.findall(
                path.read_text(encoding='utf-8')
            )
        }
        granted = sql_role2_menu_ids(path)
        granted_perms = {
            menu_perms[menu_id] for menu_id in granted if menu_id in menu_perms
        }
        missing = sorted(gated - granted_perms)
        assert not missing, f'{name} 未给默认角色授权：{missing}'


def test_stage5_migrations_grant_the_same_role_menu_ids():
    """两个 Stage 5 迁移脚本给默认角色补授的菜单必须一致，且覆盖设备模块全部权限。"""
    mysql_ids = sql_role2_menu_ids(SQL_FILES['mysql_stage5'], migration=True)
    pg_ids = sql_role2_menu_ids(SQL_FILES['pg_stage5'], migration=True)
    assert mysql_ids == pg_ids
    assert {1061, 1062, 1063, 1064, 1065, 1066, 1067, 1068, 1069} <= pg_ids
