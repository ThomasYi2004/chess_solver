"""Proof-number search over engine-pruned move lists.

Leaves are closed by:
* checkmate                         (exact)
* stalemate                         (exact: the game ended without a White win)
* repetition                        (closed as a non-win, see leaf.repetition_count)
* no progress over leaf.progress_window plies (closed as a non-win)
* eval >= leaf.win_threshold        (heuristic win, subject to leaf verifiers)
* eval <= leaf.loss_threshold       (heuristic non-win, subject to leaf verifiers)
* reaching search.max_ply           (closed as a non-win)

Everything in between is expanded, most-proving node first.
"""

from __future__ import annotations

import logging
import os
import pickle
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import chess
import chess.engine

from .config import Config
from .engine import Engine, MoveEval
from .tree import Node, Status
from .verify import ActiveVerifier

log = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1


@dataclass
class Stats:
    nodes: int = 0
    expansions: int = 0
    leaf_reasons: Counter = field(default_factory=Counter)
    vetoes: Counter = field(default_factory=Counter)
    pruned_white: int = 0  # White moves never tried (can only cost proofs)
    pruned_black: int = 0  # Black replies assumed losing (unsound direction)
    deepest_ply: int = 0
    seconds: float = 0.0  # summed over all sessions of a resumed search
    stop_reason: str = ""


@dataclass
class Result:
    status: Status
    root: Node
    root_board: chess.Board
    stats: Stats


def select_moves(
    evals: list[MoveEval],
    white_to_move: bool,
    margin: float | None,
    max_moves: int | None,
) -> list[MoveEval]:
    """Candidates for the side to move: within `margin` of its best move, at
    most `max_moves` of them, best first."""
    ranked = sorted(evals, key=lambda e: e.value, reverse=white_to_move)
    if not ranked:
        return []
    best = ranked[0].value
    if margin is not None:
        if white_to_move:
            ranked = [e for e in ranked if e.value >= best - margin]
        else:
            ranked = [e for e in ranked if e.value <= best + margin]
    if max_moves is not None:
        ranked = ranked[:max_moves]
    return ranked


def root_board(config: Config) -> chess.Board:
    board = chess.Board(config.search.fen)
    for uci in config.search.moves:
        board.push_uci(uci)
    return board


class Solver:
    def __init__(
        self,
        config: Config,
        engine: Engine,
        verifiers: list[ActiveVerifier] = (),
        state: dict | None = None,
    ):
        """Start a new search, or continue the one in `state` (see load_checkpoint)."""
        self.cfg = config
        self.engine = engine
        self.verifiers = list(verifiers)
        self.root_board = root_board(config)
        self.stop_requested = False
        if state is None:
            self.stats = Stats()
            self.root = self._make_node(self.root_board, None, None, 0, None)
        else:
            if state["root_fen"] != self.root_board.fen():
                raise ValueError("checkpoint was made for a different root position")
            self.stats = state["stats"]
            self.root = state["root"]
            engine.cache.update(state["engine_cache"])

    # -- leaf classification -------------------------------------------------

    def _classify(
        self, board: chess.Board, ply: int, known: MoveEval | None, parent: Node | None = None
    ) -> tuple[Status, str | None, float | None]:
        if board.is_checkmate():
            return (Status.DISPROVEN if board.turn == chess.WHITE else Status.PROVEN), "checkmate", None
        if board.is_stalemate():
            return Status.DISPROVEN, "stalemate", None
        leaf = self.cfg.leaf
        if leaf.repetition_count and board.is_repetition(leaf.repetition_count):
            return Status.DISPROVEN, "repetition", None

        value = (known or self.engine.evaluate(board)).value
        if ply >= leaf.min_ply:
            verdict = None
            if value >= leaf.win_threshold:
                verdict, reason = Status.PROVEN, "eval_win"
            elif value <= leaf.loss_threshold:
                verdict, reason = Status.DISPROVEN, "eval_not_winning"
            if verdict is not None and self._verified(board, verdict, value):
                return verdict, reason, value
            if self._no_progress(parent, value):
                return Status.DISPROVEN, "no_progress", value

        if ply >= self.cfg.search.max_ply:
            return Status.DISPROVEN, "max_ply", value
        return Status.UNKNOWN, None, value

    def _no_progress(self, parent: Node | None, value: float) -> bool:
        """True if `value` is not `progress_min_gain` above the eval
        `progress_window` plies up the line (`parent` is one ply up)."""
        window = self.cfg.leaf.progress_window
        if not window:
            return False
        ancestor = parent
        for _ in range(window - 1):
            if ancestor is None:
                return False
            ancestor = ancestor.parent
        if ancestor is None or ancestor.value is None:
            return False
        return value - ancestor.value < self.cfg.leaf.progress_min_gain

    def _verified(self, board: chess.Board, verdict: Status, value: float) -> bool:
        for active in self.verifiers:
            if verdict in active.applies_to and not active.verifier.verify(board, verdict, value):
                self.stats.vetoes[active.name] += 1
                return False
        return True

    def _make_node(
        self, board: chess.Board, move: chess.Move | None, parent: Node | None,
        ply: int, known: MoveEval | None,
    ) -> Node:
        status, reason, value = self._classify(board, ply, known, parent)
        node = Node(move, parent, board.turn == chess.WHITE, ply, status, reason, value)
        self.stats.nodes += 1
        self.stats.deepest_ply = max(self.stats.deepest_ply, ply)
        if reason is not None:
            self.stats.leaf_reasons[reason] += 1
        return node

    # -- expansion -----------------------------------------------------------

    def _expand(self, node: Node, board: chess.Board) -> None:
        """Create all children of `node`. The children are attached only once
        all of them exist, so an interrupted expansion leaves the tree intact."""
        mc = self.cfg.moves
        white = board.turn == chess.WHITE
        if white:
            margin, max_moves, multipv = mc.white_margin, mc.white_max_moves, mc.white_multipv
            depth = self.cfg.engine.white_depth
        else:
            margin, max_moves, multipv = mc.black_margin, mc.black_max_moves, mc.black_multipv
            depth = self.cfg.engine.black_depth
        legal = board.legal_moves.count()
        if multipv is None:
            multipv = max_moves if max_moves is not None else legal
        candidates = select_moves(self.engine.analyse(board, multipv, depth), white, margin, max_moves)

        stats_before = (self.stats.nodes, self.stats.deepest_ply, self.stats.leaf_reasons.copy())
        children = []
        try:
            for cand in candidates:
                board.push(cand.move)
                try:
                    known = cand if mc.child_eval == "multipv" else None
                    children.append(self._make_node(board, cand.move, node, node.ply + 1, known))
                finally:
                    board.pop()
        except BaseException:
            self.stats.nodes, self.stats.deepest_ply, self.stats.leaf_reasons = stats_before
            raise

        node.pruned = legal - len(children)
        if white:
            self.stats.pruned_white += node.pruned
        else:
            self.stats.pruned_black += node.pruned
        node.children = children
        self.stats.expansions += 1

    # -- main loop -----------------------------------------------------------

    def _select_leaf(self) -> tuple[Node, chess.Board]:
        node = self.root
        board = self.root_board.copy()
        while node.expanded:
            node = node.most_proving_child()
            board.push(node.move)
        return node, board

    @staticmethod
    def _backpropagate(node: Node | None) -> None:
        while node is not None:
            node.update_numbers()
            node = node.parent

    def _budget_exhausted(self, session_start: float) -> str | None:
        s = self.cfg.search
        if s.max_nodes is not None and self.stats.nodes >= s.max_nodes:
            return "max_nodes"
        if s.max_seconds is not None and time.monotonic() - session_start >= s.max_seconds:
            return "max_seconds"
        return None

    def request_stop(self) -> None:
        """Stop after the current expansion (safe to call from a signal handler)."""
        self.stop_requested = True

    def run(self) -> Result:
        session_start = last_save = time.monotonic()
        seconds_before = self.stats.seconds
        stop = None
        try:
            while self.root.status is Status.UNKNOWN:
                stop = "interrupted" if self.stop_requested else self._budget_exhausted(session_start)
                if stop:
                    break
                leaf, board = self._select_leaf()
                self._expand(leaf, board)
                self._backpropagate(leaf)
                if self.stats.expansions % self.cfg.search.log_every == 0:
                    log.info(
                        "expansions=%d nodes=%d root pn=%s dn=%s deepest=%d engine_calls=%d",
                        self.stats.expansions, self.stats.nodes, self.root.pn, self.root.dn,
                        self.stats.deepest_ply, self.engine.calls,
                    )
                out = self.cfg.output
                if out.checkpoint and time.monotonic() - last_save >= out.checkpoint_every_seconds:
                    self.stats.seconds = seconds_before + time.monotonic() - session_start
                    self.save_checkpoint(out.checkpoint)
                    last_save = time.monotonic()
        except KeyboardInterrupt:
            stop = "interrupted"
        except chess.engine.EngineTerminatedError:
            stop = "engine_terminated"
        self.stats.seconds = seconds_before + time.monotonic() - session_start
        self.stats.stop_reason = stop or "solved"
        if self.cfg.output.checkpoint:
            self.save_checkpoint(self.cfg.output.checkpoint)
        return Result(self.root.status, self.root, self.root_board, self.stats)

    # -- checkpoints ---------------------------------------------------------

    def save_checkpoint(self, path: str) -> None:
        state = {
            "version": CHECKPOINT_VERSION,
            "config": self.cfg,
            "root_fen": self.root_board.fen(),
            "root": self.root,
            "stats": self.stats,
            "engine_cache": self.engine.cache,
        }
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 100_000))
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
        log.info("checkpoint saved to %s (%d nodes)", path, self.stats.nodes)


def load_checkpoint(path: str) -> dict:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 100_000))
    with open(path, "rb") as f:
        state = pickle.load(f)
    if state.get("version") != CHECKPOINT_VERSION:
        raise ValueError(f"unsupported checkpoint version {state.get('version')}")
    return state
