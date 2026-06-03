# 006 — Distractor retrieval

## The question

Evals 001–005 always handed the model the **exactly-right** card. Real
retrieval returns a top-k with **wrong** cards mixed in. So: when the correct
card is buried among plausible distractors, does the 4B still solve — and does
a **select-the-card** step recover what's lost?

This is the one pipeline stage we've never tested (the retrieval half), and it
directly sizes how good retrieval has to be before the conditional-injection
gate pays (PROJECT_PLAN Phase 3). It also extends the −10pp "over-injection"
finding from 001: there it was extra *right* cards; here it's *wrong* ones.

## Conditions

| Condition | Cards injected |
|---|---|
| `D0`        | the correct card(s) only — the clean baseline (= existing C3) |
| `D2`        | correct + 2 distractors |
| `D4`        | correct + 4 distractors |
| `D4_select` | correct + 4 distractors, but the model first **selects** the relevant card(s), then solves with only those |

**Distractors** are real oracle cards from *other* problems, ranked
**most-confusable-first** by shared variable symbols (so a Hohmann problem
gets velocity/energy cards, not an obviously-unrelated one). A card whose
formula equals a correct card is excluded (that's a duplicate, not a
distractor). The candidate set is **shuffled** (seeded) so the correct card
isn't always first. Everything is deterministic.

## Problems

| Problem | knowledge | why it's here |
|---|---|---|
| `astro-016` sphere of influence | absent | card is *essential* (C2 = 0%): if distractors crowd it out, the answer fails — a clean signal |
| `astro-027` hyperbolic turn angle | absent | needs **two** correct cards among the noise — harder selection |
| `astro-004` escape velocity | present | single-formula, the model already knows it, and *every* distractor is genuinely unneeded — measures **pure distraction cost** with no relevance confound |

## Metrics (in `summary.md`)

- **answer-correct vs distractor load** (D0 → D2 → D4): the degradation curve.
- **Distractor cost** (D4 − D0): how much 4 wrong cards hurt vs the clean card.
- **Selection recovery** (D4_select − D4): does picking the card claw it back?
- **Selection accuracy**: how often the model's pick included the correct card.

## Two run modes

`make quick` is the easy probe (low load, generic distractors). `make run` is
the **breaking-point sweep** — it cranks the load and adds the dangerous kind
of distractor:

| Mode | Conditions | What it isolates |
|---|---|---|
| `make quick` | D0, D2, D4, D4_select | generic distractors at low load (≤4) |
| `make run` | D0, **D8**, **NM**, **NM8**, **NM8_select** | high load + **near-miss** cards |

**New conditions in `make run`:**
- **D8** — correct + 8 generic distractors. Isolates the *load* axis (does sheer volume hurt?).
- **NM** — correct + **1 near-miss** only. Isolates the dangerous axis: a *subtly-wrong same-quantity* formula sitting next to the correct one.
- **NM8** — near-miss buried in 8 generic distractors (realistic worst case).
- **NM8_select** — same + the select step: does selection *reject* the near-miss?

**Near-miss cards** (`nearmiss.json`) are hand-authored to be genuinely
confusable — same variables, plausible name/wording, wrong formula:

| Problem | Correct | Near-miss (wrong) |
|---|---|---|
| `astro-004` escape velocity | `v = √(2μ/r)` | `v = √(μ/r)` (circular speed — missing the 2) |
| `astro-016` SOI | `r = a·(m₁/m₃)^(2/5)` | **Hill sphere** `r = a·(m₁/3m₃)^(1/3)` (a real, commonly-confused alternative) |
| `astro-027` turn angle | `δ = 2·asin(1/e)` | `δ = 2·acos(1/e)` (wrong inverse function) |

`make run` also reports **near-miss cost** (NM − D0), **near-miss-under-load**
(NM8 − D8), and **how often select grabbed the near-miss** (lower = better).

## Running it

```bash
cd evals/006-distractor-retrieval
make smoke      # stub, <1s — quick conditions
make smoke-run  # stub, <1s — run conditions (high load + near-miss)
make quick      # 3 × {D0,D2,D4,D4_select} × N=3 = 36 cells (~10 min)
make run        # 3 × {D0,D8,NM,NM8,NM8_select} × N=5 = 75 cells (~25 min)
```

## What to look for

1. **Does D4 drop below D0 on the card-essential problems?** If wrong cards
   crowd out the needed one, retrieval precision is load-bearing.
2. **Does selection recover it?** If `D4_select ≈ D0`, a cheap rerank/select
   step is enough and flat top-k retrieval is fine. If not, retrieval quality
   itself has to be high.
3. **astro-005 (present):** if distractors barely hurt here, the model's own
   knowledge is robust to noise — the risk is concentrated on absent-knowledge
   problems, exactly where retrieval matters most.

## Harness note

Runs on the Python harness. New shared component: `lib/run_distract.py`
(distractor pool construction + the select step). The select step is a
prototype of the Phase-3 retrieval **rerank/probe** before injection.
