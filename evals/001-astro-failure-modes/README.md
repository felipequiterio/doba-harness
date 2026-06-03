# 001 — Astrodynamics: which failure mode dominates a 4B?

## Hypothesis

A small local LLM (~4B, OpenAI-compatible endpoint) fails astrodynamics problems
for one of two reasons:

- **(a) Knowledge / verification failure** — it doesn't know the formula, or it
  knows it but can't trust its own arithmetic. Fixable with **retrieval + a
  calculator tool**.
- **(b) Reasoning-depth failure** — it can't *select* the right approach,
  *map* the problem onto it, or *compose* multiple steps. Not fixable by
  the harness.

If injecting the perfect knowledge (oracle formula card) + a calculator closes
the gap to a frontier model, **(a) dominates** → building a retrieval system is
worth it. If it doesn't, **(b) dominates** → retrieval won't help; don't build it.

This experiment is the only thing that justifies (or kills) the Doba knowledge
layer before code gets written.

## Conditions

| ID  | Setup                                                      |
|-----|------------------------------------------------------------|
| C1  | Raw 4B. No tools, no injected knowledge.                   |
| C2  | 4B + calculator tool (text protocol — see `src/calculator.py`). |
| C3  | 4B + calculator + **oracle formula card(s)** injected.     |
| REF | Frontier model on C1. Ceiling reference.                   |

REF on C3 is also wired up (one line in `config.yaml`) in case you want to
confirm the ceiling isn't itself knowledge-limited.

## What this is NOT

- Not a retriever. No vector DB, no RAG, no embedding model. C3 uses
  **oracle injection** (the perfect card, hand-labeled in the dataset) as the
  *upper bound* of what retrieval could ever do.
- Not an agent framework. No tool-use SDK. Just a small loop around an
  OpenAI-compatible chat endpoint and a text-based `CALC:` / `RESULT:` protocol.

## Layout

```
evals/001-astro-failure-modes/
  Makefile                      # uv-backed targets for everything below
  pyproject.toml                # deps (managed by uv)
  config.yaml                   # endpoints, N runs, tolerances
  dataset/astrodynamics_v1.json # problems + oracle cards + expected answers
  src/
    ground_truth.py             # computes expected answers IN CODE
    calculator.py               # safe AST evaluator + CALC/RESULT protocol
    scoring.py                  # answer parsing, SI normalization, tolerance
    models.py                   # OpenAI/OpenRouter/Anthropic clients + stub
    conditions.py               # C1/C2/C3/REF prompt + tool assembly
    probe.py                    # labels each problem knowledge=present/absent
    run.py                      # orchestrates all conditions × N
    run_log.py                  # per-run human-readable log writer
    analyze.py                  # runs.jsonl → summary.md + charts
  tests/                        # scoring, calculator safety, ground truth
  results/
    runs.jsonl                  # one JSON line per run (for analyze.py)
    logs/<id>__<cond>__run<i>.log   # per-run text log (full trace + tokens)
    summary.md                  # markdown summary
    charts/                     # matplotlib PNGs
```

## Setup

Uses [uv](https://docs.astral.sh/uv/) (much faster than pip; manages the
venv automatically).

```bash
cd evals/001-astro-failure-modes
make install                 # uv sync — creates .venv from pyproject.toml
```

`config.yaml` ships pre-wired for **Qwen3.5 4B in Ollama** (local) and
**Gemini 2.5 Flash via OpenRouter** (frontier — REF + method judge). Get
the model + keys in place:

```bash
make ollama-pull             # ollama pull qwen3.5:4b
make ollama-up               # background `ollama serve` if not already running

export LOCAL_API_KEY=ollama                # any string; Ollama ignores it
export OPENROUTER_API_KEY=sk-or-v1-...     # your OpenRouter key
```

If you switch providers later, flip `frontier_model.provider` (`openai` /
`openrouter` / `anthropic`) and adjust `base_url` / `api_key_env`.
`make_client` picks the right adapter from that field.

## Running

Everything goes through the Makefile (`make help` lists targets):

```bash
make smoke-tiny      # 1 problem × 1 run × stub clients — no keys, sanity wiring
make probe           # label every problem knowledge=present|absent
make run             # full sweep — 10 problems × 4 conditions × N runs
make analyze         # results/summary.md + results/charts/*.png
```

Knob-style overrides on `make run`:

```bash
make run-cheap                                # N=1 (single run per cell)
make run N=3 CONDITIONS=C1,C2,C3              # narrow the matrix
make run PROBLEMS=astro-001,astro-005         # narrow the dataset
make run-stub N=2                             # no keys, but full pipeline
```

Per-run text logs land in `results/logs/<id>__<cond>__run<i>.log` — each
file contains the full message history, every assistant turn annotated
with prompt/completion tokens + tk/s, the final score, and the judge's
rationale. `make logs` cats a random one for quick spot-checking. Pass
`--no-logs` to `src.run` if you want to skip them.

## Tests

```bash
make test
```

## Troubleshooting

**`AuthenticationError: 401 Missing Authentication header`** during REF or the
method judge → the frontier API key env var isn't exported. `make run` now
pre-flights this and fails fast with the variable name to set. Export it and
re-run.

**All C1 runs `stopped=no_final_answer`, exact same completion-token count
(e.g. `2048` capped) every time** → Qwen3 is generating inside `<think>…
</think>` and burning the whole completion budget before emitting the
answer. Two levers, both in `config.yaml → local_model`:

- `disable_thinking: true` (default) — appends `/no_think` to the system
  prompt. Measures the model's direct-answer quality. Faster, fits cleanly
  in 8 GB VRAM.
- `disable_thinking: false` + `max_tokens: 8192` and `extra_body.options.num_ctx: 16384`
  — honest test of the model's reasoning trace, but ~3 × slower and needs
  the bigger context window.

**System RAM hits 100% during runs, model crawls at 20–30 tk/s** → Ollama
is spilling KV cache to host RAM because the default context window for
Qwen3 is huge and your 8 GB VRAM can't hold it. The shipped config sets
`num_ctx: 8192` and `num_gpu: 99` in `local_model.extra_body.options`,
which fixes this on RTX 3070-class cards. Verify with:

```bash
ollama ps     # SIZE column should be ~3 GB, PROCESSOR should say "100% GPU"
```

If you still see `CPU` listed under PROCESSOR, lower `num_ctx` (try 4096) or
use a smaller quantization of the model. You can also create a Modelfile
that pins these settings into the tag itself — useful if other tools also
hit the same model:

```dockerfile
# Modelfile.qwen3-doba
FROM qwen3.5:4b
PARAMETER num_ctx 8192
PARAMETER num_gpu 99
```

```bash
ollama create qwen3-doba -f Modelfile.qwen3-doba
# then set model_name: "qwen3-doba" in config.yaml
```

**Live monitoring** while a sweep is running:

```bash
watch -n1 'ollama ps; echo; nvidia-smi | head -20'
```

The tests verify:

- `scoring.py` parses `FINAL_ANSWER:` correctly, handles unit conversions,
  and applies relative tolerance.
- `calculator.py` rejects unsafe input (`import`, attribute access, subscripts,
  function defs) and computes the allowed math correctly.
- `ground_truth.py` reproduces every expected answer in the dataset JSON
  exactly. **If you change a problem, this test will fail until you re-run
  the ground-truth recompute.**

## Dataset

`dataset/astrodynamics_v1.json` ships with **10 problems** spanning circular
orbits, vis-viva, Kepler-3, escape velocity, Hohmann transfer (delta-v and
time-of-flight), plane change, C3/v∞, and specific energy. Split:

- **single** (one formula, direct application): 6
- **multi** (composition of ≥2 formulas / definitions): 4

**Review and expand to ~24–30 problems before drawing strong conclusions.**
Ten is enough to wire the pipeline and see direction-of-effect; not enough to
publish. Add problems by appending to the JSON and running:

```bash
python -m src.ground_truth --check   # asserts JSON matches computed answers
```

## Decision rule

After the run, `results/summary.md` reports four numbers:

1. **C2 − C1 on knowledge=absent problems.** How much does the calculator
   alone close? (Mode-a, verification half.)
2. **C3 − C2 on knowledge=absent problems.** How much does oracle knowledge
   add on top of the calculator? (Mode-a, knowledge half.)
3. **C3 vs REF on structure=multi problems.** Does the reasoning ceiling show
   even with perfect knowledge?
4. **% of REF performance recovered by C3.**

Rule of thumb:

- If `C3 ≈ REF` overall → **mode-(a) dominates**. Build retrieval.
- If `C3 ≪ REF` and the gap is concentrated on structure=multi → **mode-(b)
  dominates**. Retrieval won't close this; reconsider model size.
- If `C2 ≈ C3` → the calculator alone is doing the work, and the oracle isn't
  adding value at this difficulty. Probably means the dataset is too easy
  knowledge-wise; expand with harder/rarer-formula problems.

## Assumptions baked in

- Equatorial Earth radius **6 378 137 m** (WGS-84), `μ_⊕ = 3.986 004 418 × 10¹⁴
  m³/s²`. GEO altitude taken as 35 786 km. Documented in `src/ground_truth.py`.
- All expected answers are in **SI units**. Models can answer in any unit; the
  scorer normalizes before comparing.
- Default `tolerance_rel = 0.01` (1 %). Override per-problem in the JSON.
- Calculator and ground-truth use stdlib `math` only — no `numpy`, no
  `astropy`. Keeps the eval reproducible across environments.
