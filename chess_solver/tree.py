"""Search tree nodes and proof-number bookkeeping.

The tree is an AND/OR tree for the question "can White force a win?":
White-to-move nodes are OR nodes (one winning move suffices), Black-to-move
nodes are AND nodes (every considered reply must lose).
"""

from __future__ import annotations

import math
from enum import Enum

import chess

INF = math.inf


class Status(Enum):
    UNKNOWN = "unknown"
    PROVEN = "proven"  # White wins
    DISPROVEN = "disproven"  # White does not win


class Node:
    __slots__ = (
        "move", "parent", "children", "white_to_move", "ply",
        "pn", "dn", "status", "reason", "value", "pruned",
    )

    def __init__(
        self,
        move: chess.Move | None,
        parent: Node | None,
        white_to_move: bool,
        ply: int,
        status: Status,
        reason: str | None,
        value: float | None,
    ):
        self.move = move
        self.parent = parent
        self.children: list[Node] = []
        self.white_to_move = white_to_move
        self.ply = ply
        self.status = status
        self.reason = reason  # why the node was closed, for leaves
        self.value = value  # engine eval, White POV, in the configured metric
        self.pruned = 0  # legal moves not turned into children
        self.pn = self.dn = 1.0
        self.update_numbers()

    @property
    def expanded(self) -> bool:
        return bool(self.children)

    def update_numbers(self) -> None:
        """Recompute pn/dn from the children (or the leaf status)."""
        if not self.children:
            if self.status is Status.PROVEN:
                self.pn, self.dn = 0.0, INF
            elif self.status is Status.DISPROVEN:
                self.pn, self.dn = INF, 0.0
            else:
                self.pn, self.dn = 1.0, 1.0
            return
        if self.white_to_move:
            self.pn = min(c.pn for c in self.children)
            self.dn = sum(c.dn for c in self.children)
        else:
            self.pn = sum(c.pn for c in self.children)
            self.dn = min(c.dn for c in self.children)
        if self.pn == 0:
            self.status = Status.PROVEN
        elif self.dn == 0:
            self.status = Status.DISPROVEN

    def most_proving_child(self) -> Node:
        if self.white_to_move:
            return min(self.children, key=lambda c: c.pn)
        return min(self.children, key=lambda c: c.dn)

    def path(self) -> list[chess.Move]:
        moves = []
        node = self
        while node.parent is not None:
            moves.append(node.move)
            node = node.parent
        return moves[::-1]
