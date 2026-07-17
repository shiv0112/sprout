"""CLI entrypoints for kiln_registry.

Exposed as the ``kiln-registry`` console script so k8s Jobs and init-containers
can run migrations without invoking Alembic directly::

    kiln-registry migrate              # upgrade head
    kiln-registry migrate --revision X # upgrade to specific revision
    kiln-registry migrate --downgrade  # downgrade one step

Alembic is an optional dependency (it ships with the ``[server]`` extra). The
CLI lazy-imports it inside ``migrate`` so the entrypoint still works — e.g.
for ``--help`` — on a base install without Alembic.
"""
from __future__ import annotations

import argparse
import sys
from importlib import resources


def _alembic_config():
    """Build an Alembic Config in-memory from the packaged migrations dir.

    We deliberately avoid depending on the repo-root ``alembic.ini`` because
    that file is not shipped inside the wheel; deployed containers only have
    the ``kiln_registry`` package on the path.
    """
    try:
        from alembic.config import Config
    except ImportError as e:
        raise RuntimeError(
            "alembic is required to run migrations. "
            "Install with: pip install 'kiln-registry-api[server]'"
        ) from e

    migrations_dir = resources.files("kiln_registry").joinpath("migrations")
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    return cfg


def migrate(args: argparse.Namespace) -> int:
    try:
        from alembic import command
    except ImportError as e:
        raise RuntimeError(
            "alembic is required to run migrations. "
            "Install with: pip install 'kiln-registry-api[server]'"
        ) from e

    cfg = _alembic_config()
    if args.downgrade:
        command.downgrade(cfg, args.revision or "-1")
    else:
        command.upgrade(cfg, args.revision or "head")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kiln-registry")
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("migrate", help="Run database migrations")
    m.add_argument("--revision", default=None, help="Target revision (default: head)")
    m.add_argument("--downgrade", action="store_true", help="Downgrade instead of upgrade")
    m.set_defaults(func=migrate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
