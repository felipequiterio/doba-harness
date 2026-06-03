# 002 — Thinking vs Oracle on the hardest problems

## Hypothesis

Draft 3 §4.3 claims the 4B model has a hard **mode-(b) ceiling** at a
specific composition depth — `astro-028` (bi-elliptic transfer, 4 vis-viva
+ 3 burn sums) failed 0/10 across **every** condition in eval 001
including the frontier model with oracle injection.

But all of those runs had Qwen3's **thinking mode disabled** (`/no_think`
+ `think: false`), because thinking burned the completion budget without
producing answers in our format. That made the comparison clean for
mode-(a) signal, but it left a question on the table:

> Does Qwen3's reasoning mode (`<think>...</think>`) crack the depth
> ceiling, or is the wall real even with thinking enabled?

This eval tests exactly that, on the 5 hardest problems from eval 001.

## Conditions

Four conditions per problem (N=3 each → 60 cells, ~30–60 min wall time):

| ID | Setup |
|---|---|
| **C1_NT** | 4B raw, **thinking OFF** (`/no_think` + `think: false`) |
| **C1_T**  | 4B raw, **thinking ON**  (`<think>...</think>` reasoning) |
| **C3_NT** | 4B + calculator + oracle, **thinking OFF** |
| **C3_T**  | 4B + calculator + oracle, **thinking ON** |

The 2×2 cleanly separates:

- **Thinking effect**: C1_T − C1_NT  (does raw reasoning help?)
- **Oracle effect**: C3_NT − C1_NT  (replicating 001's main finding, expecting +30 pp)
- **Thinking × Oracle interaction**: (C3_T − C3_NT) vs (C1_T − C1_NT)
- **Best small-model recipe**: max(C3_T, C3_NT) vs the 001 REF_C3 ceiling (90%)

## The 5 problems

| ID | Why it's here |
|---|---|
| `astro-005` | Hohmann LEO→GEO Δv. Multi. Local 4B fails consistently (C1=1/2, C3=0/2 in 001). |
| `astro-014` | Small Hohmann 400→800 km. Calculator hurt the most here (C2=0/5 in 001 full sweep). |
| `astro-016` | Sphere of influence. Rare formula — C1 = 2/5 (low recall) but C3 = 5/5 (oracle saves). |
| `astro-028` | Bi-elliptic transfer. **0/10 across every condition in 001 including REF_C3.** The mode-(b) wall. |
| `astro-031` | Combined plane change + circularize. C1=0/5, C2=2/5, C3=1/5. Law-of-cosines stumbling. |

## How it's wired

This eval reuses 001's harness end-to-end. It is just:

- a fixed list of 5 problem IDs,
- a fixed list of 4 conditions (the thinking-suffix variants `_T` / `_NT`),
- its own config.yaml (bigger `max_tokens` + `num_ctx` to accommodate the
  thinking conditions),
- a Makefile that points 001's runner at *this* directory's config + paths.

There is **no duplicated code or duplicated dataset** — the source of truth
is 001's `dataset/astrodynamics_v1.json` + `src/`. This eval is essentially
a saved query.

## Running it

```bash
cd evals/002-thinking-vs-oracle

# Real run, ~30–60 min wall time, requires Ollama + qwen3.5:4b loaded.
make run

# Stub-smoke wiring (no model calls; <1 sec).
make smoke

# Aggregate runs.jsonl into summary.md + charts.
make analyze
```

Outputs land in `evals/002-thinking-vs-oracle/results/` (separate from 001's).

## What to look for in the summary

- **C3_T vs C3_NT on `astro-028`**: if both are still 0/3, the wall is real
  even with thinking; mode-(b) ceiling holds and decomposition (not
  thinking) is the lever. If C3_T succeeds, thinking IS the lever and the
  conclusion in draft 3 §4.3 needs revision.
- **C1_T vs C1_NT overall**: how much does thinking buy without retrieval?
- **C3_T vs C3_NT overall**: does the oracle still help when the model is
  already reasoning at length, or does thinking subsume retrieval?
- **Token / latency cost**: thinking mode roughly 4–8× the tokens of
  non-thinking. Worth tracking whether the accuracy gains justify the
  inference cost.
