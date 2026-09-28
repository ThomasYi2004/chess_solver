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
**UNKNOWN** (the node or time budget ran out first).

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

Every setting is described under [Configuration](#configuration) below. The example config is kept small
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

## Configuration

Settings are read in this order, with later sources winning: the defaults in
`chess_solver/config.py`, then the TOML file given with `--config`, then each
`--set section.key=value`. Values use TOML syntax. TOML has no null, so an
optional value is switched off with the string `"none"`.

In the tables, **Default** is the built-in value from `config.py`, and
**Example** is the value in `config.example.toml` where it differs.

Thresholds and margins are in the unit of `leaf.metric`, always from White's
point of view:

| `leaf.metric` | Unit |
|---|---|
| `cp` | centipawns (100 = one pawn) |
| `win_prob` | the engine's probability that White wins, 0 to 1 |
| `expectation` | the engine's expected score for White (win + draw/2), 0 to 1 |

### `[engine]`: the UCI engine

| Key | Default | Example | Meaning |
|---|---|---|---|
| `path` | `"bin/stockfish"` | | Path to the engine binary. |
| `threads` | `4` | `8` | Engine `Threads` option. |
| `hash_mb` | `1024` | `2048` | Engine hash table size in MB. |
| `depth` | `18` | `10` | Search depth for each engine call. |
| `nodes` | none | | Node limit for each engine call. |
| `time` | none | | Time limit in seconds for each engine call. |
| `white_depth` | none | | Depth of the MultiPV search that picks White's moves; none means `depth`. |
| `black_depth` | none | `8` | Depth of the MultiPV search that picks Black's replies; none means `depth`. Black nodes look at every reply, so this is the main speed setting. |
| `options` | `{}` | | Extra UCI options passed through unchanged, e.g. `{ SyzygyPath = "/tb" }`. |

At least one of `depth`, `nodes` and `time` must be set. The engine stops at
whichever limit it reaches first.

### `[leaf]`: when a position counts as settled

| Key | Default | Example | Meaning |
|---|---|---|---|
| `metric` | `"cp"` | | Evaluation unit: `cp`, `win_prob` or `expectation` (see above). |
| `win_threshold` | `100` | | Eval at or above this counts as a White win. |
| `loss_threshold` | `0` | | Eval at or below this counts as "White does not win". Must be lower than `win_threshold`. |
| `min_ply` | `0` | | The two eval cutoffs and the no-progress rule are ignored before this ply, so the first plies are always expanded. Checkmate, stalemate and repetition still count. |
| `repetition_count` | `2` | | A position that has occurred this many times on the line from the start counts as "White does not win". `2` means any repeat, `3` is the threefold rule, `0` turns the check off. |
| `progress_window` | `0` | `8` | No-progress rule: if the eval has not risen by at least `progress_min_gain` compared with this many plies earlier on the line, the position counts as "White does not win". Must be even so both evals are for the same side to move. `0` turns it off. |
| `progress_min_gain` | `0` | `10` | Eval gain required over `progress_window` plies. |

### `[moves]`: which moves are searched

| Key | Default | Example | Meaning |
|---|---|---|---|
| `white_margin` | `30` | | Only White moves within this distance of White's best move are tried. none means no margin. |
| `white_max_moves` | `3` | `2` | At most this many White moves are tried. none means no limit. |
| `black_margin` | `150` | | Only Black replies within this distance of Black's best reply are searched. Replies outside it are **assumed to lose for Black**, which is the unsound part, so keep this wide. |
| `black_max_moves` | none | | At most this many Black replies are searched. The rest are also assumed to lose for Black. |
| `white_multipv` | none | | Number of lines requested from the engine at White nodes. none means `white_max_moves`, or all legal moves if that is none too. Moves outside these lines are never tried. |
| `black_multipv` | none | | The same as `white_multipv`, for Black nodes. |
| `child_eval` | `"multipv"` | | How a new position gets its eval. `multipv` reuses the score from the parent's MultiPV search, which is fast. `separate` runs a fresh engine search on the new position. |

Pruning White's moves can only miss wins. Pruning Black's replies can produce
false wins.

### `[search]`: the starting position and budgets

| Key | Default | Example | Meaning |
|---|---|---|---|
| `fen` | start position | | Root position. `--fen "<fen>"` is a shortcut for this. |
| `moves` | `[]` | | UCI moves played from `fen` before the search starts, e.g. `["e2e4", "e7e5"]`. |
| `max_nodes` | `100000` | | Stop after creating this many positions. none means no limit. |
| `max_seconds` | none | | Stop after this many seconds. When resuming, the count restarts with each run. |
| `max_ply` | `80` | `40` | Positions still open at this depth count as "White does not win". |
| `log_every` | `100` | | Log a progress line every this many expansions. |

The search stops at whichever budget it reaches first. The result is then
UNKNOWN unless the root was already decided.

### `[verify]`: second opinions on eval cutoffs

| Key | Default | Meaning |
|---|---|---|
| `leaf_verifiers` | `[]` | List of verifiers to run on positions closed by an eval cutoff. They run in order, and any of them can reject the verdict so the position stays open. |

Each entry is a table with a `name` (registered in `chess_solver/verify.py`),
`apply_to` (`["win"]`, `["not_winning"]` or both; default `["win"]`), and the
verifier's own parameters:

```toml
[[verify.leaf_verifiers]]
name = "lookahead"
apply_to = ["win"]
plies = 20
```

No verifiers are implemented yet, so this list must stay empty for now.

### `[output]`: report and files

| Key | Default | Meaning |
|---|---|---|
| `print_depth` | `6` | How many plies of the proof, disproof or open tree to print in the report. |
| `tree_json` | none | Write the whole search tree as JSON to this path. |
| `checkpoint` | none | Save the search state to this path periodically and on exit. Continue with `--resume`. |
| `checkpoint_every_seconds` | `300` | How often the checkpoint is saved. |

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
