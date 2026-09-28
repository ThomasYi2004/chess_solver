"""Human-readable report and JSON export of a search result."""

from __future__ import annotations

import json
from typing import Any

import chess

from .config import Config
from .engine import MATE_CP
from .search import Result
from .tree import INF, Node, Status


def _fmt_num(x: float) -> str:
    return "inf" if x == INF else str(int(x))


def _fmt_value(value: float | None, metric: str) -> str:
    if value is None:
        return "-"
    if metric == "cp" and abs(value) > MATE_CP - 1000:
        return f"{'+' if value > 0 else '-'}#{int(MATE_CP - abs(value))}"
    return f"{value / 100:+.2f}" if metric == "cp" else f"{value:.3f}"


def _shown_children(node: Node) -> list[Node]:
    """Children needed to justify the node's status: the one winning (or
    refuting) move where one suffices, all children where all are needed."""
    if node.status is Status.PROVEN and node.white_to_move:
        return [next(c for c in node.children if c.pn == 0)]
    if node.status is Status.DISPROVEN and not node.white_to_move:
        return [next(c for c in node.children if c.dn == 0)]
    return node.children


def format_tree(result: Result, metric: str, depth: int) -> str:
    lines: list[str] = []

    def walk(node: Node, board: chess.Board, indent: int) -> None:
        for child in _shown_children(node):
            num = board.fullmove_number
            prefix = f"{num}." if board.turn == chess.WHITE else f"{num}..."
            san = board.san(child.move)
            info = f"{child.status.value}"
            if child.reason:
                info += f" ({child.reason})"
            info += f"  eval {_fmt_value(child.value, metric)}"
            if child.status is Status.UNKNOWN:
                info += f"  pn={_fmt_num(child.pn)} dn={_fmt_num(child.dn)}"
            if child.pruned:
                side = "White" if child.white_to_move else "Black"
                info += f"  [{child.pruned} {side} moves pruned]"
            lines.append(f"{'  ' * indent}{prefix}{san}  {info}")
            if child.expanded and indent + 1 < depth:
                board.push(child.move)
                walk(child, board, indent + 1)
                board.pop()

    walk(result.root, result.root_board.copy(), 0)
    return "\n".join(lines)


def format_report(result: Result, config: Config, engine_name: str, engine_calls: int, cache_hits: int) -> str:
    s = result.stats
    root = result.root
    verdict = {
        Status.PROVEN: "PROVEN   - White forces a win (under the heuristic assumptions)",
        Status.DISPROVEN: "DISPROVEN - White cannot force a win (under the heuristic assumptions)",
        Status.UNKNOWN: "UNKNOWN  - stopped before the root was resolved",
    }[result.status]
    out = [
        f"Root:        {result.root_board.fen()}",
        f"Engine:      {engine_name}",
        f"Result:      {verdict}",
        f"Stopped:     {s.stop_reason}",
        f"Root pn/dn:  {_fmt_num(root.pn)} / {_fmt_num(root.dn)}",
        f"Nodes:       {s.nodes}   expansions: {s.expansions}   deepest ply: {s.deepest_ply}",
        f"Engine:      {engine_calls} searches, {cache_hits} cache hits",
        f"Time:        {s.seconds:.1f}s",
        f"Leaves:      " + (", ".join(f"{k}={v}" for k, v in s.leaf_reasons.most_common()) or "none"),
        f"Pruned:      white moves={s.pruned_white}  black replies assumed losing={s.pruned_black}",
    ]
    if s.vetoes:
        out.append("Vetoes:      " + ", ".join(f"{k}={v}" for k, v in s.vetoes.items()))
    if root.expanded:
        title = {
            Status.PROVEN: "Proof tree",
            Status.DISPROVEN: "Disproof tree",
            Status.UNKNOWN: "Search tree",
        }[result.status]
        out += ["", f"{title} (first {config.output.print_depth} plies):",
                format_tree(result, config.leaf.metric, config.output.print_depth)]
    return "\n".join(out)


def tree_to_dict(node: Node) -> dict[str, Any]:
    return {
        "move": node.move.uci() if node.move else None,
        "status": node.status.value,
        "reason": node.reason,
        "value": node.value,
        "pn": None if node.pn == INF else node.pn,
        "dn": None if node.dn == INF else node.dn,
        "pruned": node.pruned,
        "children": [tree_to_dict(c) for c in node.children],
    }


def write_tree_json(result: Result, path: str) -> None:
    with open(path, "w") as f:
        json.dump({"root_fen": result.root_board.fen(), "tree": tree_to_dict(result.root)}, f)
