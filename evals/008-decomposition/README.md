# 008 — Does decomposition add value?

## The question

007 showed monolithic solves **drop sub-steps** on multi-step transfers
(`astro-014`: computed burn 1, *forgot* burn 2 — even with a "total = burn1 +
burn2" card). 001–003 found a real reasoning ceiling (`astro-031`, 0/3). So:
**does an explicit decompose-then-solve harness add any value on the hard
multi-step problems** — bi-elliptic, the small Hohmann, the combined-plane-
change wall?

Knowledge is held **constant** (the oracle cards are present in both
conditions). The *only* variable is the solve strategy.

## Conditions

| Condition | How it solves |
|---|---|
| `mono`   | one monolithic ReAct loop, oracle cards, **thinking ON** (= 007's C3_oracle; how it's done now) |
| `decomp` | **plan → execute each leaf → compose** (`lib/decompose.py`) |

**Decompose-then-solve:**
1. **Plan** (thinking ON) — the model breaks the problem into an ordered list of
   *single-quantity* sub-steps; the last produces the answer. (Plans only — no
   numbers.)
2. **Execute each leaf** (no-think) — a focused calculator solve that computes
   *exactly one* quantity, with all prior results supplied as known values. It
   **can't drop a planned sub-step**, and each leaf is a single-step task where
   003 showed no-think + the calculator is reliable.
3. **Compose** — the last leaf's value is the answer.

## Problems — the hard multi-step cases

| Problem | what | why |
|---|---|---|
| `astro-028` | bi-elliptic Δv | near the ceiling; 3 burns to compose |
| `astro-031` | combined plane-change + circularize | the **0/3 wall** — does planning crack it? |
| `astro-014` | small Hohmann (~217 m/s) | 007's drop-a-burn failure; rounding-sensitive |
| `astro-005`, `astro-035` *(run only)* | Hohmann LEO→GEO; Hohmann-vs-bi-elliptic | broaden the signal |

## Metrics (in `summary.md`)

- **Decomposition effect** (decomp − mono answer rate), overall and per problem.
- **Cost ratio** (decomp / mono tokens) — decomposition may be *cheaper*
  (no-think leaves) even if it only ties on accuracy.
- **Plan quality** — % of plans that parsed, median #steps.

## Reading it

- `decomp > mono` on a problem → decomposition **rescued a composition** the
  monolithic solve dropped (the hypothesis).
- both still `0` (e.g. `astro-031`) → a **genuine ceiling** that planning
  doesn't crack — knowledge *and* structure aren't enough; the model can't
  execute the sub-steps even when handed them.

## Running it

```bash
cd evals/008-decomposition
make smoke    # stub, <1s
make quick    # 3 hard problems × {mono,decomp} × N=3 = 18 cells (~30-50 min)
make run      # 5 problems × N=5 = 50 cells (~80-110 min)
```

Each `decomp` record stores the plan, every leaf's computed value, and the
final composition, so a wrong answer can be traced to the exact step that
failed.
