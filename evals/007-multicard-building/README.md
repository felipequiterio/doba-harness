# 007 — Multi-card / multi-step building

## The question

004 showed the 4B builds *single* cards well. Real problems need a whole
**set** of cards, built from one method passage, then **composed**. Can it do
that on multi-step transfer problems — Hohmann and bi-elliptic Δv?

These problems live near the **reasoning ceiling** (003 found `astro-028` hard,
`astro-031` a 0/3 wall), so a naive "does C3_self solve it?" is confounded: a
failure could be a *bad card set* or *can't-compose-even-with-perfect-cards*.
This eval **separates the two**.

## Design — three conditions, read together

| Condition | Cards | Role |
|---|---|---|
| `C2`        | none | floor |
| `C3_oracle` | the dataset's hand-written multi-card set | **ceiling for composition ability** |
| `C3_self`   | the 4B's own card set, built from the method passage | the test |

**How the confound is removed:**
- `C3_oracle` high & `C3_self` low → a **card-building** gap (the model's set is
  worse than the hand set).
- both low → the **reasoning ceiling** (cards aren't the problem; it can't
  compose).

So the answer metric is disambiguated by reading oracle and self side by side.

## Two scores per cell

- **answer_correct** — the numeric solve, *including the composition* (summing
  the burns). This is where the ceiling bites.
- **card_coverage** — did the built set contain the two physics building blocks,
  **circular speed** `v_c = √(μ/r)` and **vis-viva** `v = √(μ(2/r − 1/a))`,
  SymPy-checked (`lib.symbolic.check_formula`). It's a *floor*, not a verdict: a
  model that writes the *composed* Δv formula directly can still solve while
  scoring low coverage — so coverage tells "did it extract the right
  ingredients," and the answer tells "did it compose them."

## Solve runs **thinking ON**

Unlike 004/005/006 (single-step → no-think), these are **multi-step**, where
003 found thinking is **+24pp**. Both the build and the solve run thinking on,
with a 16k token budget and a higher tool-call cap (multi-card compositions take
more calculator round-trips).

## Problems

| Problem | transfer | knowledge | cards |
|---|---|---|---|
| `astro-005` | Hohmann LEO→GEO | present | 4 |
| `astro-014` | Hohmann (small, rounding-sensitive) | absent | 4 |
| `astro-028` | bi-elliptic | absent | 4 |
| `astro-035` *(run only)* | Hohmann vs bi-elliptic, pick the cheaper | none | 5 |

Passages (`sources/`) describe the **method** and the building-block formulas
(Hintz §3.4); the *composed* Δv answer is never given, so the model must build
the set and compose it.

## Running it

```bash
cd evals/007-multicard-building
make smoke    # stub, <1s
make quick    # 3 problems × {C2,C3_oracle,C3_self} × N=3 = 27 cells (~30-50 min)
make run      # + astro-035 (5-card) at N=5 = 60 cells (~70-90 min)
```

These are slow (multi-step + thinking + 16k budget); warmup keeps the model
resident. Each `C3_self` record stores the built cards, coverage, and build
cost.

## What to look for

1. **Card-building gap (C3_oracle − C3_self).** ~0 → the 4B builds multi-card
   sets as good as the hand set (004 extends to multi-card). Large → ingest
   degrades with set size.
2. **Per-problem mode split.** Which failures are card-gaps vs the ceiling
   (`astro-014`/`028` are the stress cases).
3. **Coverage vs answer.** High coverage + wrong answer → the *composition*
   failed (the cards were fine) — a clean mode-b signal.
4. **n_cards built.** Does it under-build (miss a burn) or over-build?
