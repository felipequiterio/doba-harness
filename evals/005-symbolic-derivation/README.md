# 005 — Deriving formulas with a symbolic tool

## Why this exists

Eval 004 showed the 4B builds correct cards — and on `astro-025` it
*synthesized* a closed form (`tan β = e·sinθ/(1+e·cosθ)`) the passage never
stated, by dividing the velocity components. But that derivation happened in
the model's head, **unverified** — the numeric calculator can't catch a bad
derivation, so a wrong card would mint a wrong formula and we'd only know via
ground truth (which production won't have).

This eval asks: **on problems where the passage gives the ingredients but not
the closed form, does a local symbolic-algebra tool (SymPy) make the 4B's
derivations more correct?** SymPy plays two roles:

1. A **derivation tool the model drives** (`SYMB:` → divide / simplify /
   solve → a *formula* back). The local, no-egress answer to "like Wolfram."
2. A **judge of the built card** — `check_formula` decides whether the card's
   formula is equivalent to the canonical one, independent of the numeric
   solve. This is the metric that tells *correct derivation* from *lucky
   number*.

## Conditions (the build step differs; all then solve numerically)

| Condition | How the card is derived |
|---|---|
| `build_text`   | in-head, no tool (= eval 004's approach; the baseline) |
| `build_sympy`  | the model drives the `SYMB:` tool to derive the formula |
| `build_verify` | derive in-head, **then** a SYMB round to check & correct it |

## Two scores per cell

- **formula_correct** — SymPy equivalence of the built card to the canonical
  formula (numeric probing at random points; robust to symbolic form and to
  the model renaming variables). *The decisive metric.*
- **answer_correct** — the existing numeric solve. Can pass on a lucky number
  even when the formula is wrong — which is exactly why we also score the
  formula.

The summary reports both, plus the **disagreements** (formula-right but
answer-wrong; formula-wrong but answer-right) — the lucky-number cases are
the whole reason this eval exists.

## Problems — each needs a genuine derivation

The passage states the relation in **non-closed form**, so the model must
manipulate it (the closed form is never given to copy):

| Problem | Passage gives | Must derive | Symbolic op |
|---|---|---|---|
| `astro-025` flight-path angle | the two velocity *components* | `tan β = e·sinθ/(1+e·cosθ)` | divide + simplify |
| `astro-037` semi-major axis | `τ = 2π√(a³/μ)` (period of `a`) | `a = (μτ²/4π²)^(1/3)` | solve for `a` |
| `astro-038` orbital speed | the energy equation `v²/2 − μ/r = −μ/2a` | `v = √(μ(2/r − 1/a))` | solve for `v` |

Passages are faithful Hintz excerpts in `sources/` (cut **before** the book
states the closed form — e.g. the energy-equation passage stops just before
the vis-viva equation). `astro-037`/`astro-038` are new dataset problems
(`ground_truth.py`, code-verified).

## Running it

```bash
cd evals/005-symbolic-derivation
make smoke    # stub, no Ollama, <1s — verifies wiring
make quick    # 27 cells; build_sympy/build_verify are multi-turn → ~40-60 min
```

Results land in `results/<timestamp>/` (`runs.jsonl`, `logs/`, `summary.md`);
each record stores the built cards, the formula verdict, and the SYMB-call
count for auditing.

## What to look for

1. **formula_correct: build_sympy/verify − build_text.** Does the tool make
   derivations more correct? If `build_text` is already ~100%, astro-025
   wasn't luck and the CAS isn't needed for these.
2. **Lucky numbers** (formula-wrong / answer-right). If common, the numeric-
   only scoring in 004 was over-counting — and the CAS judge matters even if
   the CAS *tool* doesn't.
3. **Did `build_verify` change the card?** (`changed_by_verify`). Self-
   correction firing = the model can catch its own bad derivations given the
   tool.
4. **Can the 4B drive a CAS at all?** (`n_symb_calls`, and whether SYMB usage
   correlates with correctness). If it can't form valid `SYMB:` calls, the
   tool's value is as a *verifier* (build_verify), not a derivation engine.

## Harness note

Runs on the **Python** harness (001–004 lineage). New shared components:
`lib/symbolic.py` (SymPy `SYMB:` tool + `check_formula` judge) and
`lib/card_builder_symbolic.py` (the three build modes). These are prototypes
of a Phase-5 **card validator** for the TS Learning Loop.
