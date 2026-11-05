# Doba evaluation framework

```
evals/
  pyproject.toml          # one shared venv (uv-managed)
  uv.lock
  Makefile                # cross-cutting targets — see `make help`
  README.md               # ← you are here

  lib/                    # the harness. Dataset-agnostic.
    calculator.py         # safe AST eval + CALC/RESULT protocol
    conditions.py         # C1/C2/C3 + thinking-suffix codes (_T / _NT)
    models.py             # Ollama / OpenAI-compat / Anthropic / Stub clients
    progress.py           # live header / per-cell line / footer
    run.py                # orchestrator — `python -m lib.run --config <eval>/config.yaml`
    run_log.py            # per-cell human-readable log files
    scoring.py            # FINAL_ANSWER parsing, SI normalize, tolerance, salvage
    analyze.py            # runs.jsonl → summary.md + charts
    stats.py              # problem-level CIs + paired tests (`python -m lib.stats runs.jsonl A B`)
    probe.py              # knowledge=present/absent labeling

  datasets/               # reusable across experiments.
    astrodynamics/
      v1.json             # 38 problems (20 single / 18 multi) with oracle cards + canonical methods
      ground_truth.py     # functions to compute expected answers + CONSTANTS dict

  tests/                  # shared unit suite
    test_calculator.py    # 28 safety vectors + protocol parsing
    test_scoring.py       # FINAL_ANSWER + SI conversion + tolerance
    test_ground_truth.py  # asserts JSON expected matches computed
    test_conditions.py    # solve loop acts on the answer, never <think>; tool-turn cap
    test_stats.py         # cluster bootstrap, paired comparison, sign test

  001-astro-failure-modes/   # an EXPERIMENT — thin wrapper.
    config.yaml             # which dataset, which conditions, sampling knobs
    Makefile                # `make run`, `make quick`, `make analyze`, ...
    README.md               # hypothesis + how to read the results
    results/                # timestamped run history (see below)
      2026-06-01T14-30-22/  # ←  one folder per `make run`
        runs.jsonl
        logs/
        summary.md
        charts/
      latest -> 2026-06-01T14-30-22/   # symlink to most recent run

  002-thinking-vs-oracle/   # another experiment, same shape
    ...
```

## How an experiment is structured

An experiment is **just a folder** containing:

1. `config.yaml` — points at a dataset, declares the conditions, sets the sampling knobs.
2. `Makefile` — wraps `python -m lib.run --config $(CONFIG)` with the experiment-specific CLI args.
3. `README.md` — hypothesis, what's being tested, how to interpret the results.
4. `results/` — generated outputs. Gitignored apart from `.gitkeep`.

There is no source code inside an experiment folder. All the harness logic lives in `lib/`.

## Adding a new experiment

```bash
mkdir 003-my-new-eval
cd 003-my-new-eval
mkdir results
touch results/.gitkeep README.md config.yaml Makefile
```

Then:

- **`config.yaml`** — copy from `001-astro-failure-modes/config.yaml` and edit. The only mandatory key under `paths:` is `dataset:` (relative to *this* config file). Choose conditions from `C1 / C2 / C3 / REF / REF_C3` with optional `_T` (force thinking ON) or `_NT` (force OFF) suffixes.
- **`Makefile`** — copy from `001-astro-failure-modes/Makefile`; drop targets you don't need.
- **`README.md`** — explain *why* this experiment exists and what would falsify the hypothesis.

If your experiment needs a new dataset:

```bash
mkdir -p datasets/mydata
# Drop your problems.json (or whatever format) and ground_truth.py here.
# ground_truth.py should expose a CONSTANTS dict and a `compute(problem_id, given)` function.
```

`lib/run.py` will auto-discover `datasets/mydata/ground_truth.py` if it sits next to the dataset file, and pass its `CONSTANTS` to the calculator. No `lib/` code change needed.

## Adding a new condition

Edit `lib/conditions.py:build_messages` and `lib/run.py:_resolve_condition`. Add the suffix-stripping logic if the new code interacts with thinking-mode. The `progress.py` header + `run_log.py` description string will pick it up automatically as long as you add it to the relevant tables.

## Cross-cutting commands (from `evals/` root)

| Command | Effect |
|---|---|
| `make sync` | `uv sync` — install/refresh deps into `evals/.venv` |
| `make test` | run all unit tests in `tests/` |
| `make gt-check` | assert the astro dataset's "expected" matches `ground_truth.py` |
| `make gt-regen` | overwrite the dataset's "expected" from `ground_truth.py` |
| `make verify` | `gt-check` + `test` — must be green before any real run |
| `make smoke` | stub end-to-end on 001's config (no model calls) |
| `make list-evals` | print every experiment folder + its README's first line |
| `make clean` | wipe pytest caches, leave results untouched |

## Per-experiment commands (from inside `NNN-*/`)

Each experiment exposes its own `make help`. The most common:

| Command | Effect |
|---|---|
| `make smoke` | stub run — sanity-check wiring |
| `make run` | real run with the experiment's locked-in config — writes to `results/<timestamp>/` and updates the `results/latest` symlink |
| `make analyze` | regenerate `summary.md` + charts for `results/latest` |
| `make list-runs` | list historical runs under `results/` |
| `make clean-latest` | wipe only the most recent run dir |
| `make clean-all-runs` | wipe ALL historical runs (asks for confirmation) |

## Run history

Each `make run` creates a fresh `results/<YYYY-MM-DDThh-mm-ss>/` subdirectory
containing `runs.jsonl` + `logs/` + (after `make analyze`) `summary.md` +
`charts/`. The `results/latest` symlink is updated atomically once the run
finishes, so `make analyze` and `make logs` always operate on the newest
run by default.

To analyze an older run:

```bash
make analyze RUN_ID=2026-06-01T14-30-22   # not implemented in Make yet, but lib.analyze supports it:
cd .. && uv run python -m lib.analyze \
    --config 001-astro-failure-modes/config.yaml \
    --run-id 2026-06-01T14-30-22
```

To compare runs, point `--run-dir` at any historical directory; it doesn't
have to live under `runs_root` — useful for comparing results across
different machines or branches.
