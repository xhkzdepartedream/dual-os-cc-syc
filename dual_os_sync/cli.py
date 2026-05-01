"""Command-line entry point for dual-os-sync."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .auto_discover import auto_discover
from .config import AppConfig
from .prune import run_prune
from .sync_engine import run_sync


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    config = AppConfig.from_yaml(config_path)

    if args.auto or args.prune is not None:
        tasks, inferred = auto_discover(config)
        if not tasks:
            print("[SKIP] No projects discovered")
            return
        logger = logging.getLogger(__name__)
        logger.info("Auto-discovered %d task(s)", len(tasks))
        config.base_mappings.extend(inferred)
        object.__setattr__(config, "tasks", tasks)

    if args.prune is not None:
        reports = run_prune(
            config,
            list(config.tasks),
            min_user_prompts=args.prune,
            dry_run=args.dry_run,
        )
    else:
        reports = run_sync(
            config,
            task_filter=args.task,
            dry_run=args.dry_run,
            force_direction=args.force,
            force_sync=args.force_sync,
        )

    for line in reports:
        print(line)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dual-os-sync",
        description="Cross-OS state synchronisation for development tools",
    )
    p.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    p.add_argument(
        "-t", "--task",
        default=None,
        help="Run only the named task (default: all tasks)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without making changes",
    )
    p.add_argument(
        "--force",
        choices=["win_to_linux", "linux_to_win"],
        default=None,
        help="Override the sync direction",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="Auto-discover projects from both sides instead of using configured tasks",
    )
    p.add_argument(
        "--force-sync",
        action="store_true",
        help="Ignore saved state and always sync (picks the newer side)",
    )
    p.add_argument(
        "--prune",
        type=int,
        metavar="N",
        nargs="?",
        const=1,
        default=None,
        help="Delete sessions with fewer than N user prompts (default: 1). "
             "Operates on both OS sides. Implies --auto.",
    )
    return p


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


if __name__ == "__main__":
    main()
