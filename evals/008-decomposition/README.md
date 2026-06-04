# 008 — Does decomposition add value?

## The question

007 showed monolithic solves **drop sub-steps** on multi-step transfers
(`astro-014`: computed burn 1, *forgot* burn 2 — even with a "total = burn1 +
burn2" card). So: **does an explicit decompose-then-solve harness add value on
the hard multi-step problems** — bi-elliptic, the small Hohmann, the
combined-plane-change problem? Knowledge is held **constant** (oracle cards in
both conditions); the *only* variable is the solve strategy.

> **First-run findings (see `PROJECT_PLAN.md` §9):** two things, one humbling.
> **(1)** `astro-031` — long cited as the 0/3 "reasoning ceiling" — solved
> **mono 3/3** at a 16k budget + a higher tool-turn cap. There is **no genuine
> ceiling**; every "wall" was a token/turn artifact. **(2)** The first
> decomposition harness was *broken* (no-think leaves did the arithmetic and
> produced garbage), so its −56pp was an artifact, not a verdict. This eval was
> then **reworked** (below) and the harness given a **token-runaway cap**.

## Conditions

| Condition | How it solves |
|---|---|
| `mono`   | one monolithic ReAct loop, oracle cards, **thinking ON**, with a completion-token cap (= 007's C3_oracle, runaway-capped) |
| `decomp` | **plan a program of expressions → execute deterministically** (`lib/decompose.py`) |

**Decompose-then-execute (reworked):**
1. **Plan** (one model call, thinking ON) — the model writes a little PROGRAM:
   an ordered JSON list of `{name, expr}` steps, each a **calculator
   expression** over the constants, the problem's `given` values, earlier step
   names, and math functions. The last step's value is the answer. *The model
   computes no numbers.*
2. **Execute** — the harness evaluates each expression **deterministically**
   with the calculator, threading results forward. (The old version let
   no-think model "leaves" do the arithmetic; they produced garbage. Now the
   calculator does it — exactly.)

This separates **decompose correctly** (the model) from **do arithmetic** (the
deterministic calculator), and costs *one* model call instead of one per step.

## Problems — the hard multi-step cases

| Problem | what | why |
|---|---|---|
| `astro-028` | bi-elliptic Δv | 3 burns to compose; the hardest |
| `astro-031` | combined plane-change + circularize | the ex-"0/3 wall" (now known budget-bound) |
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

Each `decomp` record stores the plan (the `{name, expr}` program), every
step's evaluated value, and the final composition, so a wrong answer can be
traced to the exact step that
failed.
