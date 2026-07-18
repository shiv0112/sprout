"""
sprout_registry/safety.py
───────────────────────
Blocked package validation for tool publishing.

Before a tool is registered, its implementation file is scanned for
dangerous imports. Tools that import blocked packages are rejected.
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
    "eval",
    "exec",
    "compile",
    "code",
    # Network abuse
    "socket",
    "socketserver",
    "xmlrpc",
    # GUI automation (can interact with host)
    "pyautogui",
    "pynput",
    "keyboard",
    "mouse",
    # File system manipulation beyond basic I/O
    "pathlib",  # tools should use explicit file paths if needed
    "glob",
    # Dangerous stdlib
    "pickle",
    "shelve",
    "marshal",
    "pty",
    "resource",
    "signal",
})

# Specific function calls that are blocked even if the module is allowed
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


def validate_imports(source_code: str) -> list[str]:
    """
    Scan Python source code for blocked imports.

    Returns a list of violation messages. Empty list = safe.
    """
    violations: list[str] = []

    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return [f"Syntax error in implementation: {e}"]

    for node in ast.walk(tree):
        # Check `import X` statements
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                if module in BLOCKED_PACKAGES or alias.name in BLOCKED_PACKAGES:
                    violations.append(
                        f"Blocked import: '{alias.name}' (line {node.lineno})"
                    )

        # Check `from X import Y` statements
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module = node.module.split(".")[0]
                if module in BLOCKED_PACKAGES or node.module in BLOCKED_PACKAGES:
                    violations.append(
                        f"Blocked import: 'from {node.module}' (line {node.lineno})"
                    )

        # Check dangerous function calls
        elif isinstance(node, ast.Call):
            call_name = _get_call_name(node)
            if call_name and call_name in BLOCKED_CALLS:
                violations.append(
                    f"Blocked function call: '{call_name}' (line {node.lineno})"
                )

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
