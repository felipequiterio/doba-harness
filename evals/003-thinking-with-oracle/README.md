# 003 — Thinking mode with oracle: does it pay off?

## Hypothesis

Eval 002 ran a 2×2 — `{thinking ON, OFF} × {oracle ON, OFF}` — and found,
at N=2 on the 5 hardest problems:

- **C3_NT** (oracle + calc, no think): 7/10 (70 %)
- **C3_T**  (oracle + calc, with think): 6/10 (60 %)

Plus thinking burned ~1.5× the tokens and ~2.5× the wall time. So 002's
finding was: *"when the oracle is already provided, Qwen3's `<think>` mode
appears to slightly hurt — but at N=2 we can't say for sure."*

This eval pins that question down: **with the oracle always on, does
thinking earn its tokens?**

## Conditions

Exactly two — the comparison is the whole point:

| ID | Setup |
|---|---|
| `C3_NT` | local 4B + calculator + oracle, `<think>` mode OFF (`/no_think` + `think:false`) |
| `C3_T`  | local 4B + calculator + oracle, `<think>` mode ON   |

Nothing else varies. Same prompt, same oracle cards, same calculator,
same temperature, same seed.

## Two runs: quick and long

### `make quick` — 20 cells (~20 min wall time)

5 problems × 2 conditions × N=2 = 20 cells. Curated for **maximum
discrimination potential** based on 002's signal:

| ID | Why it's here |
|---|---|
| `astro-005` | Hohmann LEO→GEO. Multi, present knowledge. Baseline. |
| `astro-016` | Sphere of influence. Rare formula. **002 showed C3_NT=2/2, C3_T=1/2** — thinking overthought. |
| `astro-027` | Hyperbolic turn angle. Rare formula, requires asin + e_hyp setup. |
| `astro-028` | Bi-elliptic transfer. **Mode-(b) wall in 001**; 002 budget-fix showed C1_NT=2/2 but C3 conditions split 1/2. Sign traps. |
| `astro-031` | Combined plane change. Law of cosines, intermediate-rounding sensitive. |

### `make run` — 60 cells (~70 min wall time)

10 problems × 2 conditions × N=3 = 60 cells. Adds 5 more problems to
broaden the signal, including the **2 new problems specifically designed
for this eval**:

| ID | Why it's added |
|---|---|
| `astro-014` | Small Hohmann (400→800 km). Tiny answer (~217 m/s), rounding-sensitive. 002 saw C2=0/5 here. |
| `astro-023` | Heliocentric Hohmann Earth→Mars TOF. Cross-domain — uses μ_sun, not μ_earth. |
| `astro-029` | J2 nodal regression rate. Rare formula with several multiplicative terms (easy to drop the 3/2 or get cos(i) units wrong). |
| `astro-035` (NEW) | "Hohmann vs bi-elliptic — pick the cheaper." Multi-step + decision. The decision (Hohmann wins at r2/r1 = 8) might tempt thinking into computing both carefully. |
| `astro-036` (NEW) | Hyperbolic flyby periapsis from energy conservation. The trap: `v_∞` is quoted in km/s; the model must convert to m/s before squaring. Thinking might catch the unit; non-thinking might miss it. |

## What to look for

Per-condition success rate is the headline, but the more informative
numbers are:

1. **C3_T − C3_NT overall**: does thinking help, hurt, or wash out?
2. **C3_T − C3_NT on `astro-035` and `astro-036`**: the new problems are
   designed for thinking to help. If thinking doesn't help even here,
   the conclusion ("thinking is a bad bet under oracle") strengthens.
3. **Token cost ratio**: with the `run.py` per-call timestamps, we can
   compute the cost-per-correct-answer for both conditions.
4. **`no_final_answer` rate for `C3_T`**: 002 showed C3 always commits
   (because calculator round-trips force turn boundaries). If 003 stays
   the same, thinking is *safe* (won't drop answers) but maybe still not
   *helpful*.

## Running it

```bash
cd evals/003-thinking-with-oracle

# Verify wiring (no model calls).
make smoke

# Quick — 20 cells, ~20 min.
make quick

# Long — 60 cells, ~70 min.
make run
```

Results land in `results/<timestamp>/` and `results/latest -> ...` is
updated atomically.
