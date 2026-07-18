"""
sprout_registry/db.py
───────────────────
Async database layer using SQLAlchemy 2.0 + asyncpg for PostgreSQL.

Falls back to SQLite (via aiosqlite) when DATABASE_URL is not set,
making local development zero-config.

Usage:
    from sprout_registry.db import get_engine, get_session, ToolModel

    async with get_session() as session:
        result = await session.execute(select(ToolModel))
        tools = result.scalars().all()
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ── Database URL ──────────────────────────────────────────────────────────────

def _get_database_url() -> str:
    """Get async database URL from environment or default to SQLite."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        # Convert postgres:// to postgresql+asyncpg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    # Default: async SQLite for local dev (separate from the legacy sync SQLite)
    return "sqlite+aiosqlite:///sprout_async.db"


# ── SQLAlchemy Base ───────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class ToolModel(Base):
    """Persisted tool metadata. Callables stay in-memory (Python limitation)."""

    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="general", index=True)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


class ToolStatModel(Base):
    """Per-tool execution statistics — the social-proof signal of the registry.

    One row per tool, kept in sync with executions via ``db_record_execution``.
    Cheap aggregation: avg_duration_ms is a running average so we never need to
    join against an executions table for the listing endpoint.
    """

    __tablename__ = "tool_stats"

    tool_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    execution_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_status: Mapped[str] = mapped_column(String(20), nullable=False, default="never")
    favorite_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ── Engine & Session ──────────────────────────────────────────────────────────

_engine = None
_session_factory = None


def get_engine():
    global _engine  # noqa: PLW0603
    if _engine is None:
        url = _get_database_url()
        _engine = create_async_engine(
            url,
            echo=os.environ.get("SQL_ECHO", "").lower() == "true",
            pool_pre_ping=True,
        )
    return _engine


def _get_session_factory():
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = _get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def _auto_create_enabled() -> bool:
    """Whether ``init_db`` may auto-create tables.

    Defaults to true for SQLite (local dev + tests) and false for everything
    else (prod Postgres expects migrations via ``sprout-registry migrate``).
    Override with ``SPROUT_DB_AUTO_CREATE=true|false``.
    """
    override = os.environ.get("SPROUT_DB_AUTO_CREATE", "").lower()
    if override in {"true", "1", "yes"}:
        return True
    if override in {"false", "0", "no"}:
        return False
    return _get_database_url().startswith("sqlite")


async def init_db() -> None:
    """Ensure the schema exists.

    On SQLite or when ``SPROUT_DB_AUTO_CREATE=true``, creates tables directly.
    Otherwise this is a no-op — migrations are expected to have been applied
    by a k8s Job / init-container running ``sprout-registry migrate``.
    """
    if not _auto_create_enabled():
        return
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Query helpers ─────────────────────────────────────────────────────────────

async def db_list_tools() -> list[ToolModel]:
    async with get_session() as session:
        result = await session.execute(select(ToolModel).order_by(ToolModel.name))
        return list(result.scalars().all())


async def db_get_tool(tool_id: str) -> ToolModel | None:
    async with get_session() as session:
        return await session.get(ToolModel, tool_id)


async def db_upsert_tool(tool_id: str, name: str, spec_json: str, **kwargs) -> ToolModel:
    async with get_session() as session:
        existing = await session.get(ToolModel, tool_id)
        if existing:
            existing.name = name
            existing.spec_json = spec_json
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.updated_at = datetime.now(UTC)
            return existing

        tool = ToolModel(id=tool_id, name=name, spec_json=spec_json, **kwargs)
        session.add(tool)
        return tool


async def db_delete_tool(tool_id: str) -> bool:
    async with get_session() as session:
        tool = await session.get(ToolModel, tool_id)
        if tool:
            await session.delete(tool)
            return True
        return False


async def db_search_tools(query: str) -> list[ToolModel]:
    """Word-level search across name, description, tags, and id.

    Splits the query into individual terms and matches tools that contain
    ANY of the terms (OR logic). This finds relevant results even when the
    query doesn't appear as a contiguous substring.
    """
    terms = [t.strip() for t in query.lower().split() if t.strip()]
    if not terms:
        return []

    from sqlalchemy import or_

    conditions = []
    for term in terms:
        pattern = f"%{term}%"
        conditions.append(func.lower(ToolModel.name).like(pattern))
        conditions.append(func.lower(ToolModel.description).like(pattern))
        conditions.append(func.lower(ToolModel.tags_json).like(pattern))
        conditions.append(func.lower(ToolModel.id).like(pattern))

    async with get_session() as session:
        result = await session.execute(
            select(ToolModel).where(or_(*conditions)).order_by(ToolModel.name)
        )
        return list(result.scalars().all())


# ── Stats helpers ─────────────────────────────────────────────────────────────


async def db_record_execution(tool_id: str, success: bool, duration_ms: float) -> None:
    """Atomically increment execution counters for a tool.

    Uses SQL-level expressions so concurrent requests don't lose increments.
    If the row doesn't exist yet, inserts it; otherwise updates atomically.
    """
    now = datetime.now(UTC)
    status = "success" if success else "error"

    async with get_session() as session:
        stat = await session.get(ToolStatModel, tool_id)
        if stat is None:
            stat = ToolStatModel(
                tool_id=tool_id,
                execution_count=1,
                success_count=1 if success else 0,
                error_count=0 if success else 1,
                avg_duration_ms=duration_ms,
                last_executed_at=now,
                last_status=status,
            )
            session.add(stat)
            return

        col = ToolStatModel.__table__.c
        new_count_expr = col.execution_count + 1
        new_avg_expr = (col.avg_duration_ms * col.execution_count + duration_ms) / new_count_expr

        await session.execute(
            update(ToolStatModel)
            .where(ToolStatModel.tool_id == tool_id)
            .values(
                execution_count=new_count_expr,
                success_count=col.success_count + (1 if success else 0),
                error_count=col.error_count + (0 if success else 1),
                avg_duration_ms=new_avg_expr,
                last_executed_at=now,
                last_status=status,
            )
        )
        await session.refresh(stat)


async def db_get_tool_stats(tool_id: str) -> ToolStatModel | None:
    async with get_session() as session:
        return await session.get(ToolStatModel, tool_id)


async def db_list_all_tool_stats() -> dict[str, ToolStatModel]:
    """Return a tool_id → ToolStatModel map for batch enrichment of /tools."""
    async with get_session() as session:
        result = await session.execute(select(ToolStatModel))
        return {row.tool_id: row for row in result.scalars().all()}


async def db_toggle_favorite(tool_id: str, delta: int) -> int:
    """Atomically increment or decrement the favorite count. Returns the new count.

    Uses SQL-level expression to prevent lost updates from concurrent requests.
    """
    async with get_session() as session:
        stat = await session.get(ToolStatModel, tool_id)
        if stat is None:
            stat = ToolStatModel(
                tool_id=tool_id,
                favorite_count=max(0, delta),
            )
            session.add(stat)
            return stat.favorite_count

        col = ToolStatModel.__table__.c
        new_val = case(
            (col.favorite_count + delta < 0, 0),
            else_=col.favorite_count + delta,
        )
        await session.execute(
            update(ToolStatModel)
            .where(ToolStatModel.tool_id == tool_id)
            .values(favorite_count=new_val)
        )
        await session.refresh(stat)
        return stat.favorite_count
