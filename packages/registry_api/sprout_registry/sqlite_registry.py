"""
sprout_registry/sqlite_registry.py
─────────────────────────────────
SQLite-backed drop-in replacement for SproutRegistry.

Why SQLite?
  - Tool specs persist across process restarts.
  - Multiple processes (e.g. several agents) can share one registry file.
  - Discovery and querying work without loading Python modules first.

Limitation (by design):
  Python callables cannot be serialised to disk, so `fn` is always held
  in an in-memory dict. Tools must be re-imported on each startup to
  reload their callables — exactly what `import sprout_registry.tools.core_tools`
  already does.

Schema:
    tools(id TEXT PK, name TEXT, spec_json TEXT, registered_at TEXT)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator

from sprout_shared.spec import SproutTool, SproutToolSpec, ToolParam, ToolReturn

logger = logging.getLogger(__name__)

# ── Spec <-> dict serialisation ───────────────────────────────────────────────

def _spec_to_dict(spec: SproutToolSpec) -> dict:
    return {
        "id":          spec.id,
        "name":        spec.name,
        "description": spec.description,
        "version":     spec.version,
        "author":      spec.author,
        "category":    spec.category,
        "tags":        spec.tags,
        "params": [
            {
                "name":        p.name,
                "type":        p.type,
                "description": p.description,
                "required":    p.required,
                "default":     p.default,
                "enum":        p.enum,
            }
            for p in spec.params
        ],
        "returns": {
            "type":        spec.returns.type,
            "description": spec.returns.description,
        },
    }


def _dict_to_spec(d: dict) -> SproutToolSpec:
    return SproutToolSpec(
        id=d["id"],
        name=d["name"],
        description=d["description"],
        version=d.get("version", "1.0.0"),
        author=d.get("author", ""),
        category=d.get("category", "general"),
        tags=d.get("tags", []),
        params=[
            ToolParam(
                name=p["name"],
                type=p["type"],
                description=p.get("description", ""),
                required=p.get("required", True),
                default=p.get("default"),
                enum=p.get("enum"),
            )
            for p in d.get("params", [])
        ],
        returns=ToolReturn(
            type=d.get("returns", {}).get("type", "dict"),
            description=d.get("returns", {}).get("description", ""),
        ),
    )


# ── SQLiteRegistry ────────────────────────────────────────────────────────────

class SQLiteRegistry:
    """
    SQLite-backed Sprout tool registry.

    Identical public interface to SproutRegistry — swap it in by changing
    one line in registry.py. No adapter, runtime, or tool code changes.

    Usage:
        registry = SQLiteRegistry("sprout_registry.db")
        registry.register(my_tool)
        tool = registry.get("com.sprout.tools.weather")
    """

    def __init__(self, db_path: str = "sprout_registry.db"):
        self._db_path = db_path
        self._fns: dict[str, Callable] = {}   # tool_id -> callable (in-memory)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tools (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    spec_json     TEXT NOT NULL,
                    registered_at TEXT DEFAULT (datetime('now'))
                )
            """)

    def _conn(self):
        return sqlite3.connect(self._db_path, check_same_thread=False)

    def _row_to_tool(self, row) -> SproutTool | None:
        """Rebuild a SproutTool from a DB row. Returns None if callable not loaded."""
        spec = _dict_to_spec(json.loads(row[0]))
        fn   = self._fns.get(spec.id)
        if fn is None:
            return None
        return SproutTool(spec=spec, fn=fn)

    # ── Write ──────────────────────────────────────────────────────────────────

    def register(self, tool: SproutTool) -> None:
        """Persist spec to SQLite and cache the callable in memory."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tools (id, name, spec_json) VALUES (?, ?, ?)",
                (tool.id, tool.spec.name, json.dumps(_spec_to_dict(tool.spec))),
            )
        self._fns[tool.id] = tool.fn
        logger.info(f"Registered: {tool.id} ({tool.spec.name})")

    def unregister(self, tool_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
        self._fns.pop(tool_id, None)

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(self, tool_id: str) -> SproutTool | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT spec_json FROM tools WHERE id = ?", (tool_id,)
            ).fetchone()
        return self._row_to_tool(row) if row else None

    def query(self, name: str) -> SproutTool | None:
        """Fuzzy lookup: exact name match first, then partial ID match."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT spec_json FROM tools WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT spec_json FROM tools WHERE id LIKE ?",
                    (f"%{name.lower()}%",),
                ).fetchone()
        return self._row_to_tool(row) if row else None

    def has(self, tool_id: str) -> bool:
        with self._conn() as conn:
            return conn.execute(
                "SELECT 1 FROM tools WHERE id = ?", (tool_id,)
            ).fetchone() is not None

    def list_all(self) -> list[SproutTool]:
        with self._conn() as conn:
            rows = conn.execute("SELECT spec_json FROM tools").fetchall()
        return [t for t in (self._row_to_tool(r) for r in rows) if t is not None]

    def list_ids(self) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute("SELECT id FROM tools").fetchall()]

    def by_category(self, category: str) -> list[SproutTool]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT spec_json FROM tools "
                "WHERE json_extract(spec_json, '$.category') = ?",
                (category,),
            ).fetchall()
        return [t for t in (self._row_to_tool(r) for r in rows) if t is not None]

    def by_tag(self, tag: str) -> list[SproutTool]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT spec_json FROM tools "
                "WHERE json_extract(spec_json, '$.tags') LIKE ?",
                (f'%"{tag}"%',),
            ).fetchall()
        return [t for t in (self._row_to_tool(r) for r in rows) if t is not None]

    def __len__(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]

    def __iter__(self) -> Iterator[SproutTool]:
        return iter(self.list_all())

    def __repr__(self) -> str:
        return f"SQLiteRegistry(db={self._db_path!r}, tools={len(self)})"

    # ── Summary ────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        tools = self.list_all()
        if not tools:
            return "SQLite registry is empty."
        lines = ["┌─ Sprout SQLite Registry ───────────────────────────────────┐"]
        for tool in tools:
            s = tool.spec
            lines.append(f"│  {s.id:<44} v{s.version}")
            lines.append(f"│    {s.description[:60]}")
            lines.append("│")
        lines.append("└──────────────────────────────────────────────────────────┘")
        return "\n".join(lines)
