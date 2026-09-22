from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import quote_plus
from config.env import DataBaseConfig

ASYNC_SQLALCHEMY_DATABASE_URL = (
    f'mysql+asyncmy://{DataBaseConfig.db_username}:{quote_plus(DataBaseConfig.db_password)}@'
    f'{DataBaseConfig.db_host}:{DataBaseConfig.db_port}/{DataBaseConfig.db_database}'
)
if DataBaseConfig.db_type == 'postgresql':
    ASYNC_SQLALCHEMY_DATABASE_URL = (
        f'postgresql+asyncpg://{DataBaseConfig.db_username}:{quote_plus(DataBaseConfig.db_password)}@'
        f'{DataBaseConfig.db_host}:{DataBaseConfig.db_port}/{DataBaseConfig.db_database}'
    )


class Base(AsyncAttrs, DeclarativeBase):
    pass


async_engine = None
AsyncSessionLocal = None


def get_async_engine():
    """惰性创建 async engine。

    契约 §5.7 F9：原先在 import 期就 `create_async_engine`，导致只要环境里缺少
    数据库驱动（`asyncmy` / `asyncpg`），连只依赖 ORM 模型的单元测试都无法被收集。
    改为首次使用时创建后，模型、VO 与纯逻辑测试可以在干净环境运行，
    同时避免应用启动阶段就绑定连接池。
    """
    global async_engine
    if async_engine is None:
        async_engine = create_async_engine(
            ASYNC_SQLALCHEMY_DATABASE_URL,
            echo=DataBaseConfig.db_echo,
            max_overflow=DataBaseConfig.db_max_overflow,
            pool_size=DataBaseConfig.db_pool_size,
            pool_recycle=DataBaseConfig.db_pool_recycle,
            pool_timeout=DataBaseConfig.db_pool_timeout,
        )
    return async_engine


def get_async_session_local():
    """惰性创建会话工厂，绑定到惰性 engine。"""
    global AsyncSessionLocal
    if AsyncSessionLocal is None:
        AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=get_async_engine())
    return AsyncSessionLocal


def __getattr__(name: str):
    """保持 `from config.database import async_engine, AsyncSessionLocal` 的既有写法可用。

    属性在首次被访问时才真正创建 engine（此时才需要数据库驱动）。
    """
    if name == 'async_engine':
        return get_async_engine()
    if name == 'AsyncSessionLocal':
        return get_async_session_local()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
