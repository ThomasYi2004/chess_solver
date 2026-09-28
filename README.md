# chess_solver

An approximate answer to **"can White force a win?"**, found with proof-number
search over an AND/OR game tree. A chess engine (Stockfish) decides which
moves are worth searching and when a position counts as settled.

It is not a real solve of chess. The result is only as good as the heuristic
assumptions listed below, and every one of them can be changed in the config.

## How it works

The search builds a tree from the root position (the start position by
default) and always expands the *most-proving* node next.

- **White nodes (OR):** White needs just one winning move. Only moves within
  `moves.white_margin` of the engine's best are tried, at most
  `moves.white_max_moves` of them. Pruning here can only *miss* wins; it
  never creates a false one.
- **Black nodes (AND):** White has to win against every reply. Only replies
  within `moves.black_margin` of Black's best are searched. Replies outside
  the margin are **assumed to lose for Black**. This is the unsound part.
- **Leaves.** A node is closed when one of these applies:

  | Reason | Verdict | Setting |
  |---|---|---|
  | checkmate | exact | – |
  | stalemate | not a win (exact) | – |
  | repeated position | not a win | `leaf.repetition_count` |
  | no progress: eval hasn't risen by `progress_min_gain` over `progress_window` plies | not a win | `leaf.progress_window`, `leaf.progress_min_gain` |
  | eval ≥ win threshold | win | `leaf.win_threshold` |
  | eval ≤ loss threshold | not a win | `leaf.loss_threshold` |
  | maximum depth reached | not a win | `search.max_ply` |

- **Leaf verifiers** (`chess_solver/verify.py`) get a second look at leaves
  closed by an eval cutoff, and can reject the verdict so the node stays open.
  This is where a "play N moves deeper and compare" check is meant to go. No
  verifiers are implemented yet.

Evals can be measured in centipawns, win probability or expected score
(`leaf.metric`), always from White's point of view.

The possible results are **PROVEN** (White wins under the assumptions),
**DISPROVEN** (White cannot force a win under the assumptions), or
**unresolved** (a node, time or ply budget ran out first).

## What decides the result

The cutoff rules decide the verdict far more than the search does. From the
start position, the long run (engine depth 14, 16-ply no-progress window) was
DISPROVEN after about 31 minutes and 149k nodes, and most leaves of its
disproof tree were closed by the no-progress rule. Without that rule, runs
from opening positions never finish, because balanced lines stay between the
win and loss thresholds.

When you change thresholds, look at *which leaf reasons* hold up the proof or
disproof, not only at the verdict. The report prints how often each reason
was used, and `output.tree_json` saves the whole tree so you can inspect it.

## Setup

- Python 3.12 or later (it uses `tomllib`)
- [python-chess](https://pypi.org/project/chess/). Install pytest as well if
  you want to run the tests.
- A UCI engine. The code was developed against Stockfish 19. The binary is not
  in this repository; download it from
  [stockfishchess.org](https://stockfishchess.org/download/) and put it at
  `bin/stockfish`, or point `engine.path` at it.

```sh
conda create -n chess-solver python=3.12
conda activate chess-solver
pip install chess pytest
```

## Usage

```sh
# Start position with the example config (reaches a verdict in about 10 s)
python -m chess_solver -c config.example.toml

# Override any setting with --set / -s (TOML syntax; "none" unsets a value)
python -m chess_solver -c config.example.toml -s engine.depth=14 -s leaf.progress_window=16

# A different position, measured in win probability
python -m chess_solver --fen "<fen>" -s leaf.metric=win_prob \
    -s leaf.win_threshold=0.9 -s leaf.loss_threshold=0.1

# Search from a line of moves played from the start position
python -m chess_solver -c config.example.toml -s 'search.moves=["e2e4","e7e5","g1f3"]'
```

`config.example.toml` lists every setting with a short explanation, and
`chess_solver/config.py` has the defaults. The example config is kept small
so that a run finishes quickly. For deeper runs, raise `engine.depth`,
`engine.black_depth`, `moves.white_max_moves`, `leaf.progress_window` and
`search.max_ply`. Expect run time to grow steeply.

### Long runs

Set `output.checkpoint` to save the search state periodically. Ctrl-C or
SIGTERM finishes the current expansion, saves the checkpoint and prints the
report. To continue a saved run:

```sh
python -m chess_solver --resume run.ckpt -s search.max_seconds=36000
```

The checkpoint's saved config is used as the base, and any `--config` /
`--set` values are applied on top.

## Layout

```
chess_solver/
  __main__.py   command-line entry point
  config.py     every setting, its default and validation
  engine.py     UCI engine wrapper with caching, converting scores to leaf.metric
  search.py     proof-number search, leaf classification, checkpoints
  tree.py       AND/OR tree nodes and proof/disproof numbers
  verify.py     hook for leaf verifiers
  report.py     text report and JSON tree export
tests/          pytest suite (engine tests skip if bin/stockfish is missing)
```

## Tests

```sh
python -m pytest tests
```

## Not implemented yet

- Leaf verifiers, such as playing N moves deeper before trusting an eval
- 50-move rule and endgame tablebases (Syzygy can still be passed to the
  engine through `engine.options`)
