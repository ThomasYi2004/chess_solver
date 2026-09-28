"""Thin wrapper around a UCI engine that returns White-POV values in the
configured metric and caches analyses by position."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

from .config import EngineConfig, Metric

# Centipawn value used for mate scores (minus the distance to mate).
MATE_CP = 100_000

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class MoveEval:
    move: chess.Move
    value: float  # White's point of view, in the configured metric
    mate: int | None  # moves to mate from White's POV (>0 White mates), if any


def to_value(info: chess.engine.InfoDict, metric: Metric) -> float:
    score = info["score"].white()
    if metric == "cp":
        return float(score.score(mate_score=MATE_CP))
    wdl = info["wdl"].white() if "wdl" in info else score.wdl(model="sf")
    if metric == "win_prob":
        return wdl.wins / wdl.total()
    return wdl.expectation()


def resolve_engine_path(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or candidate.exists():
        return str(candidate)
    if (PROJECT_ROOT / candidate).exists():
        return str(PROJECT_ROOT / candidate)
    return path  # let popen search $PATH


class Engine:
    def __init__(self, cfg: EngineConfig, metric: Metric):
        self.metric = metric
        self.cfg = cfg
        self._engine = chess.engine.SimpleEngine.popen_uci(resolve_engine_path(cfg.path))
        options = {"Threads": cfg.threads, "Hash": cfg.hash_mb}
        if "UCI_ShowWDL" in self._engine.options:
            options["UCI_ShowWDL"] = True
        options.update(cfg.options)
        self._engine.configure({k: v for k, v in options.items() if k in self._engine.options})
        unknown = set(cfg.options) - set(self._engine.options)
        if unknown:
            raise ValueError(f"engine does not support options: {sorted(unknown)}")
        self.name = self._engine.id.get("name", os.path.basename(cfg.path))
        # (EPD, depth) -> best-first analysis with the most lines requested so
        # far. Keyed without move history, so a position reached by different
        # move orders reuses one analysis.
        self.cache: dict[tuple[str, int | None], list[MoveEval]] = {}
        self.calls = 0
        self.cache_hits = 0

    def analyse(self, board: chess.Board, multipv: int, depth: int | None = None) -> list[MoveEval]:
        """Top `multipv` moves for the side to move, best first for that side.
        `depth` overrides the configured depth for this call."""
        multipv = min(multipv, board.legal_moves.count())
        depth = depth if depth is not None else self.cfg.depth
        key = (board.epd(), depth)
        cached = self.cache.get(key)
        if cached is not None and len(cached) >= multipv:
            self.cache_hits += 1
            return cached[:multipv]
        self.calls += 1
        limit = chess.engine.Limit(depth=depth, nodes=self.cfg.nodes, time=self.cfg.time)
        infos = self._engine.analyse(board, limit, multipv=multipv)
        result = []
        for info in infos:
            if not info.get("pv"):
                continue
            mate = info["score"].white().mate()
            result.append(MoveEval(info["pv"][0], to_value(info, self.metric), mate))
        self.cache[key] = result
        return result

    def evaluate(self, board: chess.Board) -> MoveEval:
        """Engine evaluation of `board` (the value of its best line)."""
        return self.analyse(board, 1)[0]

    def close(self) -> None:
        try:
            self._engine.quit()
        except chess.engine.EngineTerminatedError:
            pass  # already gone, e.g. killed by the same Ctrl-C as we were

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
