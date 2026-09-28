"""Leaf verification hook.

Before the solver closes a leaf on the strength of the engine eval alone
(an ``eval_win`` or ``eval_not_winning`` cutoff), every enabled verifier gets
a second look. A verifier can accept the verdict or veto it; a vetoed leaf
stays open and is expanded like any other node.

This is where a "play N moves deeper with best play and compare the eval"
check belongs: the verifier receives the position, the tentative verdict and
its eval, and has the engine available to play the line out.

Adding a verifier:

1. Write a class with a ``verify`` method matching ``LeafVerifier``.
2. Register a factory in ``VERIFIERS``; it receives the engine, the full
   config and the verifier's own parameters from the config table.
3. Enable it in the config::

       [[verify.leaf_verifiers]]
       name = "my_verifier"
       apply_to = ["win"]        # "win", "not_winning", or both
       some_param = 20           # passed to the factory as **params
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

import chess

from .config import Config
from .engine import Engine
from .tree import Status

APPLY_TO = {"win": Status.PROVEN, "not_winning": Status.DISPROVEN}


class LeafVerifier(Protocol):
    def verify(self, board: chess.Board, verdict: Status, value: float) -> bool:
        """Return True to accept `verdict` for `board`, False to keep the node open.

        `board` carries the full move history from the start position; it must
        be left as it was received (push/pop is fine).
        """
        ...


VerifierFactory = Callable[..., LeafVerifier]

# name -> factory(engine, config, **params)
VERIFIERS: dict[str, VerifierFactory] = {}


@dataclass
class ActiveVerifier:
    name: str
    applies_to: frozenset[Status]
    verifier: LeafVerifier


def build_verifiers(engine: Engine, config: Config) -> list[ActiveVerifier]:
    active = []
    for spec in config.verify.leaf_verifiers:
        params: dict[str, Any] = dict(spec)
        name = params.pop("name")
        apply_to = params.pop("apply_to", ["win"])
        if name not in VERIFIERS:
            raise KeyError(f"unknown leaf verifier '{name}' (registered: {sorted(VERIFIERS)})")
        bad = set(apply_to) - set(APPLY_TO)
        if bad:
            raise ValueError(f"verifier '{name}': apply_to has unknown values {sorted(bad)}")
        active.append(
            ActiveVerifier(
                name=name,
                applies_to=frozenset(APPLY_TO[a] for a in apply_to),
                verifier=VERIFIERS[name](engine, config, **params),
            )
        )
    return active
