import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_engine = None
_SessionLocal = None


def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = os.environ["DATABASE_URL"]
        _engine = create_async_engine(url, pool_size=5, max_overflow=2)
        _SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


class SessionLocal:
    """Proxy that creates the engine on first use so import doesn't require DATABASE_URL."""

    def __new__(cls):
        _get_engine()
        return _SessionLocal()


async def get_session() -> AsyncSession:
    _get_engine()
    async with _SessionLocal() as session:
        yield session
