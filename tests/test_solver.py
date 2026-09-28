from pathlib import Path

import chess
import pytest

from chess_solver import verify
from chess_solver.config import Config, load_config
from chess_solver.engine import Engine, MoveEval
from chess_solver.search import Solver, select_moves
from chess_solver.tree import Node, Status
from chess_solver.verify import build_verifiers

ROOT = Path(__file__).resolve().parent.parent
needs_engine = pytest.mark.skipif(not (ROOT / "bin/stockfish").exists(), reason="bin/stockfish missing")

# 1.Nf6+ gxf6 2.Bxf7# (Legal's mate pattern)
MATE_IN_2 = "r2qkb1r/pp2nppp/3p4/2pNN1B1/2BnP3/3P4/PPP2PPP/R2bK2R w KQkq - 1 1"
KQ_VS_K = "8/8/8/4k3/8/8/8/KQ6 w - - 0 1"
BLACK_UP_A_QUEEN = "3qk3/8/8/8/8/8/8/4K3 w - - 0 1"


# -- config -------------------------------------------------------------------

def test_overrides_parse_types_and_none():
    cfg = load_config(None, [
        "leaf.win_threshold=150", "moves.white_max_moves=none",
        'search.moves=["e2e4", "e7e5"]', "leaf.metric=win_prob",
        "leaf.loss_threshold=0.2", "leaf.win_threshold=0.9",
    ])
    assert cfg.leaf.win_threshold == 0.9
    assert cfg.moves.white_max_moves is None
    assert cfg.search.moves == ["e2e4", "e7e5"]
    assert cfg.leaf.metric == "win_prob"


def test_unknown_key_rejected():
    with pytest.raises(KeyError):
        load_config(None, ["leaf.win_treshold=100"])


def test_none_rejected_for_required_value():
    with pytest.raises(ValueError):
        load_config(None, ["search.max_ply=none"])


def test_example_config_loads():
    load_config(ROOT / "config.example.toml")


# -- move selection -------------------------------------------------------------

def _evals(*values):
    moves = [chess.Move.from_uci(u) for u in ("e2e4", "d2d4", "c2c4", "g1f3", "b2b3")]
    return [MoveEval(m, v, None) for m, v in zip(moves, values)]


def test_white_selection_margin_and_cap():
    picked = select_moves(_evals(30, 25, 5, 0, -50), True, margin=30, max_moves=2)
    assert [e.value for e in picked] == [30, 25]
    picked = select_moves(_evals(30, 25, 5, 0, -50), True, margin=30, max_moves=None)
    assert [e.value for e in picked] == [30, 25, 5, 0]


def test_black_selection_is_from_blacks_side():
    picked = select_moves(_evals(30, 25, 5, 0, 200), False, margin=30, max_moves=None)
    assert [e.value for e in picked] == [0, 5, 25, 30]


# -- proof numbers ----------------------------------------------------------------

def test_or_and_numbers():
    root = Node(None, None, True, 0, Status.UNKNOWN, None, 0)
    a = Node(None, root, False, 1, Status.UNKNOWN, None, 0)
    b = Node(None, root, False, 1, Status.DISPROVEN, "x", 0)
    root.children = [a, b]
    a.children = [Node(None, a, True, 2, Status.PROVEN, "x", 0),
                  Node(None, a, True, 2, Status.UNKNOWN, None, 0)]
    a.update_numbers()
    root.update_numbers()
    assert (a.pn, a.dn) == (1, 1)
    assert (root.pn, root.dn) == (1, 1)
    a.children[1].status = Status.PROVEN
    a.children[1].update_numbers()
    a.update_numbers()
    root.update_numbers()
    assert root.status is Status.PROVEN


# -- engine-backed searches ----------------------------------------------------------

def _config(fen, *overrides):
    return load_config(None, [
        f'search.fen="{fen}"', "engine.depth=10", "engine.threads=2",
        "engine.hash_mb=64", "search.max_nodes=5000", *overrides,
    ])


def _solve(cfg):
    with Engine(cfg.engine, cfg.leaf.metric) as engine:
        return Solver(cfg, engine, build_verifiers(engine, cfg)).run()


@needs_engine
def test_mate_in_two_is_proven_by_checkmate_alone():
    result = _solve(_config(MATE_IN_2, "leaf.min_ply=99"))
    assert result.status is Status.PROVEN
    assert set(result.stats.leaf_reasons) == {"checkmate"}


@needs_engine
def test_lost_position_is_disproven_at_root():
    result = _solve(_config(BLACK_UP_A_QUEEN))
    assert result.status is Status.DISPROVEN
    assert result.root.reason == "eval_not_winning"


@needs_engine
@pytest.mark.parametrize("metric,win,loss", [("win_prob", 0.9, 0.1), ("expectation", 0.9, 0.5)])
def test_wdl_metrics(metric, win, loss):
    result = _solve(_config(KQ_VS_K, f'leaf.metric="{metric}"',
                            f"leaf.win_threshold={win}", f"leaf.loss_threshold={loss}"))
    assert result.status is Status.PROVEN


@needs_engine
def test_verifier_veto_keeps_leaf_open(monkeypatch):
    calls = []

    class RejectAll:
        def verify(self, board, verdict, value):
            calls.append(board.fen())
            return False

    monkeypatch.setitem(verify.VERIFIERS, "reject_all", lambda engine, config: RejectAll())
    cfg = _config(KQ_VS_K, "search.max_nodes=50")
    cfg.verify.leaf_verifiers = [{"name": "reject_all", "apply_to": ["win"]}]
    result = _solve(cfg)
    # Without the veto the root closes immediately as eval_win.
    assert calls and result.root.expanded
    assert result.stats.vetoes["reject_all"] == len(calls)
    assert "eval_win" not in result.stats.leaf_reasons


@needs_engine
def test_max_ply_closes_unresolved_nodes():
    cfg = _config(chess.STARTING_FEN, "search.max_ply=1", "leaf.min_ply=5")
    result = _solve(cfg)
    assert result.status is Status.DISPROVEN
    assert set(result.stats.leaf_reasons) == {"max_ply"}


# -- repetition and checkpoints ---------------------------------------------------------

@needs_engine
def test_repeated_position_is_not_a_win():
    # 1.Nf3 Nf6 2.Ng1 Ng8 returns to the start position.
    cfg = _config(chess.STARTING_FEN, 'search.moves=["g1f3", "g8f6", "f3g1", "f6g8"]')
    result = _solve(cfg)
    assert result.status is Status.DISPROVEN
    assert result.root.reason == "repetition"


@needs_engine
def test_repetition_check_can_be_disabled():
    cfg = _config(chess.STARTING_FEN, 'search.moves=["g1f3", "g8f6", "f3g1", "f6g8"]',
                  "leaf.repetition_count=0", "search.max_nodes=1")
    assert _solve(cfg).root.reason != "repetition"


@needs_engine
def test_checkpoint_resume_continues_search(tmp_path):
    from chess_solver.search import load_checkpoint

    ckpt = str(tmp_path / "run.ckpt")
    cfg = _config(chess.STARTING_FEN, "engine.depth=6", "search.max_nodes=60",
                  f'output.checkpoint="{ckpt}"')
    first = _solve(cfg)
    assert first.stats.stop_reason == "max_nodes"

    state = load_checkpoint(ckpt)
    cfg2 = load_config(None, ["search.max_nodes=150"], base=state["config"])
    with Engine(cfg2.engine, cfg2.leaf.metric) as engine:
        solver = Solver(cfg2, engine, [], state)
        cache_size = len(engine.cache)
        second = solver.run()
    assert cache_size > 0
    assert second.stats.nodes >= 150 > first.stats.nodes
    assert second.stats.expansions > first.stats.expansions


@needs_engine
def test_stop_request_ends_search_cleanly():
    cfg = _config(chess.STARTING_FEN, "engine.depth=6")
    with Engine(cfg.engine, cfg.leaf.metric) as engine:
        solver = Solver(cfg, engine)
        solver.request_stop()
        result = solver.run()
    assert result.stats.stop_reason == "interrupted"
    assert result.stats.expansions == 0


@needs_engine
def test_no_progress_closes_lines_that_do_not_gain():
    # Demanding +10 pawns every 2 plies closes every line at ply 2.
    cfg = _config(chess.STARTING_FEN, "leaf.progress_window=2", "leaf.progress_min_gain=1000")
    result = _solve(cfg)
    assert result.status is Status.DISPROVEN
    assert result.stats.leaf_reasons["no_progress"] > 0
    assert result.stats.deepest_ply == 2


def test_progress_window_must_be_even():
    with pytest.raises(ValueError):
        load_config(None, ["leaf.progress_window=3"])
