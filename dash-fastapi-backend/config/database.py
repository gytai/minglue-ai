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


# 惰性单例缓存。**不得**在模块层声明 `async_engine = None` 这类哨兵属性：
# 模块 `__getattr__` 只在常规属性查找失败时才被调用，哨兵会让它永远不触发，
# `from config.database import async_engine` 就绑定到 None，
# 应用启动阶段 `async_engine.begin()` 直接报 `'NoneType' object has no attribute 'begin'`。
_lazy_engine_cache: dict = {}


def get_async_engine():
    """惰性创建 async engine。

    契约 §5.7 F9：原先在 import 期就 `create_async_engine`，导致只要环境里缺少
    数据库驱动（`asyncmy` / `asyncpg`），连只依赖 ORM 模型的单元测试都无法被收集。
    改为首次使用时创建后，模型、VO 与纯逻辑测试可以在干净环境运行，
    同时避免应用启动阶段就绑定连接池。
    """
    if _lazy_engine_cache.get('engine') is None:
        _lazy_engine_cache['engine'] = create_async_engine(
            ASYNC_SQLALCHEMY_DATABASE_URL,
            echo=DataBaseConfig.db_echo,
            max_overflow=DataBaseConfig.db_max_overflow,
            pool_size=DataBaseConfig.db_pool_size,
            pool_recycle=DataBaseConfig.db_pool_recycle,
            pool_timeout=DataBaseConfig.db_pool_timeout,
        )
    return _lazy_engine_cache['engine']


def get_async_session_local():
    """惰性创建会话工厂，绑定到惰性 engine。

    ``expire_on_commit=False`` 是异步 SQLAlchemy 的必需配置：默认的
    ``expire_on_commit=True`` 会在 ``commit()`` 后把实例属性全部标记过期，
    之后再读任意属性（如 ``callback_id``）都会触发一次**同步**惰性刷新，
    在 async 会话里表现为 `MissingGreenlet: greenlet_spawn has not been called`
    ── 与真实业务无关的内部错误，且会被误记进审计表。
    """
    if _lazy_engine_cache.get('session_local') is None:
        _lazy_engine_cache['session_local'] = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            bind=get_async_engine(),
        )
    return _lazy_engine_cache['session_local']


def __getattr__(name: str):
    """保持 `from config.database import async_engine, AsyncSessionLocal` 的既有写法可用。

    属性在首次被访问时才真正创建 engine（此时才需要数据库驱动）。
    """
    if name == 'async_engine':
        return get_async_engine()
    if name == 'AsyncSessionLocal':
        return get_async_session_local()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
