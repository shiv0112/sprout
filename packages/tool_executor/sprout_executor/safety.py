"""
sprout_executor/safety.py
───────────────────────
Blocked-package validation for tool execution.

Before executing any tool code, the source is scanned for dangerous imports
and function calls. Tools that use blocked packages are rejected.

This is a defense-in-depth measure — even though registry_api validates at
registration time, the executor re-validates before execution to guard against
code that was modified after registration or injected via other paths.
"""

from __future__ import annotations

import ast
import logging

logger = logging.getLogger(__name__)

# Packages that are too dangerous for tool implementations
BLOCKED_PACKAGES = frozenset({
    # System-level access
    "subprocess",
    "os.system",
    "shutil",
    "ctypes",
    "multiprocessing",
    # Code execution
    "code",
    # Network abuse
    "socket",
    "socketserver",
    "xmlrpc",
    # GUI automation
    "pyautogui",
    "pynput",
    "keyboard",
    "mouse",
    # File system manipulation
    "pathlib",
    "glob",
    # Dangerous stdlib — serialization that can execute arbitrary code
    "pickle",
    "shelve",
    "marshal",
    "pty",
    "resource",
    "signal",
})

BLOCKED_CALLS = frozenset({
    "os.system",
    "os.popen",
    "os.exec",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.spawn",
    "os.spawnl",
    "os.spawnle",
    "os.kill",
    "os.remove",
    "os.rmdir",
    "os.unlink",
    "eval",
    "exec",
    "__import__",
})


def validate_source(source_code: str) -> list[str]:
    """
    Scan Python source code for blocked imports and calls.
    Returns a list of violation messages. Empty list = safe.
    """
    violations: list[str] = []

    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return [f"Syntax error in tool code: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                if module in BLOCKED_PACKAGES or alias.name in BLOCKED_PACKAGES:
                    violations.append(f"Blocked import: '{alias.name}' (line {node.lineno})")

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module = node.module.split(".")[0]
                if module in BLOCKED_PACKAGES or node.module in BLOCKED_PACKAGES:
                    violations.append(f"Blocked import: 'from {node.module}' (line {node.lineno})")

        elif isinstance(node, ast.Call):
            call_name = _get_call_name(node)
            if call_name and call_name in BLOCKED_CALLS:
                violations.append(f"Blocked call: '{call_name}' (line {node.lineno})")

    return violations


def _get_call_name(node: ast.Call) -> str | None:
    """Extract the full dotted name of a function call."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = []
        # `current` walks down the attribute chain. Declare it as the wider
        # ast.expr so reassigning `current.value` (which is ast.expr) is sound;
        # the `isinstance` check inside the loop narrows it back to Attribute.
        current: ast.expr = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
    return None
