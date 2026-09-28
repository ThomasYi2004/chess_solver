"""Command line entry point: ``python -m chess_solver [--config FILE] [--set k=v ...]``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from .config import load_config
from .engine import Engine
from .report import format_report, write_tree_json
from .search import Solver, load_checkpoint
from .verify import build_verifiers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chess_solver",
        description="Engine-guided proof-number search: can White force a win?",
    )
    parser.add_argument("-c", "--config", help="TOML config file")
    parser.add_argument(
        "-s", "--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
        help="override a config value (TOML syntax, 'none' to unset); repeatable",
    )
    parser.add_argument("--fen", help="shorthand for --set search.fen=...")
    parser.add_argument(
        "-r", "--resume", metavar="CHECKPOINT",
        help="continue a saved search; its config is the base for --config/--set",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="no progress logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )

    overrides = list(args.set)
    if args.fen:
        overrides.append(f'search.fen="{args.fen}"')
    state = load_checkpoint(args.resume) if args.resume else None
    config = load_config(args.config, overrides, base=state["config"] if state else None)
    if state and not config.output.checkpoint:
        config.output.checkpoint = args.resume

    with Engine(config.engine, config.leaf.metric) as engine:
        solver = Solver(config, engine, build_verifiers(engine, config), state)

        def stop(signum, frame):
            logging.warning("stopping after the current expansion (signal again to abort)")
            solver.request_stop()
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        result = solver.run()
        print(format_report(result, config, engine.name, engine.calls, engine.cache_hits))
    if config.output.checkpoint:
        print(f"\nCheckpoint: {config.output.checkpoint}  (continue with --resume)")
    if config.output.tree_json:
        write_tree_json(result, config.output.tree_json)
        print(f"Tree written to {config.output.tree_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
