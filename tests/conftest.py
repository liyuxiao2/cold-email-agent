import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cold_email.config import settings
from cold_email.database import Base

TEST_DB_URL = settings.database_url.replace("/cold_email", "/cold_email_test")


@pytest_asyncio.fixture(scope="function")
async def async_session() -> AsyncSession:
    """
    Creates all tables fresh for each test, yields a session, then drops everything.
    Requires: docker compose up -d and cold_email_test database to exist.
    Create the test DB once with:
        psql postgresql://cold_email:secret@localhost:5432 -c "CREATE DATABASE cold_email_test;"
    """
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
