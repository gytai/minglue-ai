"""测试装配。

契约 §5.7 F9：`config/database.py` 在 import 期就创建 async engine，裸环境缺
`asyncmy` 时连测试收集都会失败。这里提供内存 SQLite 装配，让模型、唯一约束与
DAO 行为可以在不连真实数据库的前提下验证。

生产库选择仍由 `.env.dev` 的 `DB_TYPE`/`DATABASE_TYPE` 决定，测试进程不修改任何
仓库配置。
"""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest_asyncio  # noqa: E402
from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import config.database as database_module  # noqa: E402


# SQLite 只对 `INTEGER PRIMARY KEY` 自动分配主键，`BIGINT PRIMARY KEY` 不会自增。
# 生产库是 MySQL/PG，`BigInteger autoincrement` 语义正确；这里仅在 SQLite 方言下
# 把 BigInteger 渲染为 INTEGER，让内存库能够像生产库一样自动生成主键。
# 这是测试装配的方言适配，不改变任何 ORM 定义或生产建表脚本。
@compiles(BigInteger, 'sqlite')
def _compile_bigint_as_integer(_type, _compiler, **_kw):
    return 'INTEGER'


@pytest_asyncio.fixture
async def sqlite_engine():
    """内存 SQLite 引擎，建出全部 ORM 表以验证模型与唯一约束。"""
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(database_module.Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sqlite_session(sqlite_engine):
    """绑定到内存库的会话。"""
    factory = async_sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
