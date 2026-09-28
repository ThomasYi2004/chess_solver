"""Solver configuration.

Every tunable lives here. Values are resolved in increasing priority from the
dataclass defaults, a TOML file (``--config``), and ``--set section.key=value``
overrides on the command line.

TOML has no null, so optional values are disabled with the string ``"none"``.

Units: all thresholds and margins are expressed in the unit of
``leaf.metric``, always from White's point of view:

* ``cp``           centipawns (100 = one pawn)
* ``win_prob``     engine's probability that White wins, in [0, 1]
* ``expectation``  engine's expected score for White (W + D/2), in [0, 1]
"""

from __future__ import annotations

import dataclasses
import tomllib
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import chess

Metric = Literal["cp", "win_prob", "expectation"]


@dataclass
class EngineConfig:
    path: str = "bin/stockfish"
    threads: int = 4
    hash_mb: int = 1024
    # Search limit per engine call. Any combination may be set; the engine
    # stops at whichever is hit first.
    depth: int | None = 18
    # Depth for the MultiPV (move selection) search at White / Black nodes;
    # "none" = `depth`. With moves.child_eval = "multipv" these searches also
    # provide the children's leaf evals.
    white_depth: int | None = None
    black_depth: int | None = None
    nodes: int | None = None
    time: float | None = None
    # Extra UCI options passed verbatim, e.g. {"SyzygyPath" = "/tb"}.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class LeafConfig:
    metric: Metric = "cp"
    # eval >= win_threshold      -> treat as a White win (proven leaf)
    win_threshold: float = 100.0
    # eval <= loss_threshold     -> treat as "White does not win" (disproven leaf)
    loss_threshold: float = 0.0
    # Eval cutoffs are ignored before this ply (relative to the root), so the
    # first plies are always expanded. Checkmate/stalemate still apply.
    min_ply: int = 0
    # A position that occurs this many times on the line from the start
    # position is closed as "White does not win" (2 = any repeat, 3 = the
    # threefold rule). 0 disables the check.
    repetition_count: int = 2
    # No-progress rule: a node whose eval is less than `progress_min_gain`
    # above its ancestor `progress_window` plies up the line is closed as
    # "White does not win" - a forced win has to make progress. The window
    # must be even so both evals are from the same side to move. 0 = off.
    progress_window: int = 0
    progress_min_gain: float = 0.0


@dataclass
class MovesConfig:
    # White (OR nodes): candidates within `white_margin` of White's best move,
    # at most `white_max_moves` of them. Pruning here can only lose proofs,
    # never create false ones.
    white_margin: float | None = 30.0
    white_max_moves: int | None = 3
    # Black (AND nodes): pruning here assumes the skipped replies lose for
    # Black. This is where unsound "proofs" come from, so keep it wide.
    black_margin: float | None = 150.0
    black_max_moves: int | None = None
    # Lines requested from the engine (MultiPV). "none" = max_moves if set,
    # otherwise every legal move (the margin only filters within these lines).
    # Moves outside the MultiPV window are pruned.
    white_multipv: int | None = None
    black_multipv: int | None = None
    # How a new child gets its leaf eval:
    #   "multipv"  - reuse the score of its line in the parent's MultiPV search
    #   "separate" - run a fresh engine search on the child position
    child_eval: Literal["multipv", "separate"] = "multipv"


@dataclass
class SearchConfig:
    fen: str = chess.STARTING_FEN
    # UCI moves applied to `fen` before the search starts, e.g. ["e2e4", "e7e5"].
    moves: list[str] = field(default_factory=list)
    # Budgets; the search stops at the first one reached.
    max_nodes: int | None = 100_000
    max_seconds: float | None = None
    # Unresolved nodes at this ply are closed as "White does not win".
    max_ply: int = 80
    log_every: int = 100


@dataclass
class VerifyConfig:
    # Leaf verifiers, applied in order to leaves closed by an eval cutoff.
    # Each entry is a table: name (registered in verify.VERIFIERS),
    # apply_to (subset of ["win", "not_winning"]), plus verifier parameters.
    leaf_verifiers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OutputConfig:
    # Plies of the proof / disproof / frontier tree to print.
    print_depth: int = 6
    # Write the whole search tree as JSON to this path.
    tree_json: str | None = None
    # Save the search state here periodically and on exit; resume it with
    # --resume. "none" disables checkpointing.
    checkpoint: str | None = None
    checkpoint_every_seconds: float = 300.0


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    leaf: LeafConfig = field(default_factory=LeafConfig)
    moves: MovesConfig = field(default_factory=MovesConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def validate(self) -> None:
        if self.leaf.metric not in typing.get_args(Metric):
            raise ValueError(f"leaf.metric must be one of {typing.get_args(Metric)}")
        if self.leaf.win_threshold <= self.leaf.loss_threshold:
            raise ValueError("leaf.win_threshold must be greater than leaf.loss_threshold")
        if self.leaf.repetition_count == 1 or self.leaf.repetition_count < 0:
            raise ValueError("leaf.repetition_count must be 0 (off) or >= 2")
        if self.leaf.progress_window < 0 or self.leaf.progress_window % 2:
            raise ValueError("leaf.progress_window must be an even number >= 0")
        if self.moves.child_eval not in ("multipv", "separate"):
            raise ValueError("moves.child_eval must be 'multipv' or 'separate'")
        for name in ("white_max_moves", "black_max_moves", "white_multipv", "black_multipv"):
            value = getattr(self.moves, name)
            if value is not None and value < 1:
                raise ValueError(f"moves.{name} must be >= 1 or 'none'")
        if self.engine.depth is None and self.engine.nodes is None and self.engine.time is None:
            raise ValueError("engine needs at least one of depth, nodes, time")
        for spec in self.verify.leaf_verifiers:
            if "name" not in spec:
                raise ValueError("every verify.leaf_verifiers entry needs a 'name'")


def _coerce(value: Any, hint: Any) -> Any:
    """Convert a raw TOML/CLI value to the field's annotated type."""
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    optional = origin in (typing.Union, types.UnionType) and type(None) in args
    if isinstance(value, str) and value.lower() in ("none", "null"):
        if not optional:
            raise ValueError(f"'none' is not allowed here (expected {hint})")
        return None
    if optional:
        hint = next(a for a in args if a is not type(None))
    if hint is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def _apply(section: Any, values: dict[str, Any], prefix: str) -> None:
    hints = typing.get_type_hints(type(section))
    names = {f.name for f in dataclasses.fields(section)}
    for key, value in values.items():
        if key not in names:
            raise KeyError(f"unknown config key '{prefix}{key}'")
        current = getattr(section, key)
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise TypeError(f"'{prefix}{key}' must be a table")
            _apply(current, value, f"{prefix}{key}.")
        else:
            setattr(section, key, _coerce(value, hints[key]))


def _parse_override(item: str) -> dict[str, Any]:
    """Turn ``a.b=value`` into ``{"a": {"b": value}}``. Values use TOML syntax;
    anything that is not valid TOML is taken as a bare string."""
    if "=" not in item:
        raise ValueError(f"override '{item}' must look like section.key=value")
    path, raw = item.split("=", 1)
    try:
        value = tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw
    result: dict[str, Any] = {}
    node = result
    keys = path.strip().split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value
    return result


def load_config(
    path: str | Path | None = None, overrides: list[str] = (), base: Config | None = None
) -> Config:
    config = base if base is not None else Config()
    if path is not None:
        with open(path, "rb") as f:
            _apply(config, tomllib.load(f), "")
    for item in overrides:
        _apply(config, _parse_override(item), "")
    config.validate()
    return config
