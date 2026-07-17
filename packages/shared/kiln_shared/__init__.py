"""Public surface of `kiln_shared`.

The spec / model exports are always available — they have no heavy deps.
The auth helpers are only re-exported when the optional `[server]` extra is
installed (they pull in fastapi / jwt / httpx). SDK-only consumers can do
`from kiln_shared import KilnTool` without dragging the server stack in.
"""

from .spec import KilnTool as KilnTool
from .spec import KilnToolSpec as KilnToolSpec
from .spec import ToolParam as ToolParam
from .spec import ToolReturn as ToolReturn
from .spec import kiln_tool as kiln_tool

__all__ = [
    "KilnToolSpec",
    "KilnTool",
    "ToolParam",
    "ToolReturn",
    "kiln_tool",
]

# Optional server-only helpers. Use EAFP: try the real import, and only
# swallow ImportError when its root cause is one of the known server-extra
# dependencies missing. Any other ImportError — a typo in auth.py, a bad
# re-export, a circular import, or even a corrupted install of fastapi
# itself (which raises ImportError with a different `name`) — must bubble
# up rather than silently dropping KilnUser / require_auth.
_SERVER_DEPS = {"fastapi", "httpx", "jwt", "starlette", "pydantic_settings"}

try:
    from .auth import KilnUser as KilnUser
    from .auth import require_auth as require_auth
    from .auth import require_jwt_auth as require_jwt_auth
except ImportError as exc:
    # `exc.name` is the top-level module Python couldn't find. If it's one
    # of the server-extra deps, this is an SDK-only install — stay quiet.
    # Otherwise re-raise so the real bug surfaces.
    if exc.name not in _SERVER_DEPS:
        raise
else:
    __all__.extend(["KilnUser", "require_auth", "require_jwt_auth"])
