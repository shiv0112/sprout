"""Public surface of `sprout_shared`.

The spec / model exports are always available — they have no heavy deps.
The auth helpers are only re-exported when the optional `[server]` extra is
installed (they pull in fastapi / jwt / httpx). SDK-only consumers can do
`from sprout_shared import SproutTool` without dragging the server stack in.
"""

from .spec import SproutTool as SproutTool
from .spec import SproutToolSpec as SproutToolSpec
from .spec import ToolParam as ToolParam
from .spec import ToolReturn as ToolReturn
from .spec import sprout_tool as sprout_tool

__all__ = [
    "SproutToolSpec",
    "SproutTool",
    "ToolParam",
    "ToolReturn",
    "sprout_tool",
]

# Optional server-only helpers. Use EAFP: try the real import, and only
# swallow ImportError when its root cause is one of the known server-extra
# dependencies missing. Any other ImportError — a typo in auth.py, a bad
# re-export, a circular import, or even a corrupted install of fastapi
# itself (which raises ImportError with a different `name`) — must bubble
# up rather than silently dropping SproutUser / require_auth.
_SERVER_DEPS = {"fastapi", "httpx", "jwt", "starlette", "pydantic_settings"}

try:
    from .auth import SproutUser as SproutUser
    from .auth import require_auth as require_auth
    from .auth import require_jwt_auth as require_jwt_auth
except ImportError as exc:
    # `exc.name` is the top-level module Python couldn't find. If it's one
    # of the server-extra deps, this is an SDK-only install — stay quiet.
    # Otherwise re-raise so the real bug surfaces.
    if exc.name not in _SERVER_DEPS:
        raise
else:
    __all__.extend(["SproutUser", "require_auth", "require_jwt_auth"])
