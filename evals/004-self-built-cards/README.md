# 004 — Self-built knowledge cards

## The question (the project's gate)

Evals 001–003 proved that **if** a perfect, hand-written concept card is in
context, the 4B applies it and beats the raw frontier (+30 pp on
`knowledge=absent` problems). But the oracle card was authored by a human —
it stood in for the entire **Learning Loop** (PROJECT.md Pillar 5).

This eval removes the human. Does a card the **4B builds itself** from the
real source passage recover the oracle's lift?

- **Yes** → the Learning Loop is buildable as designed → green-light Phase 5.
- **No** → ingestion needs rework *before* investing weeks in the pipeline.

This is the cheapest possible prototype of Pillar 5: a single
`passage → card` model call (`lib/card_builder.py`), with understand,
interpret, and integrate collapsed into one step.

## Design

Three conditions per problem, all on the existing harness. Exactly one
variable changes — who wrote the card:

| Condition | Card | Role |
|---|---|---|
| `C2`        | none | floor |
| `C3_oracle` | the dataset's hand-written card | ceiling (the known +30 pp) |
| `C3_self`   | a card the 4B built from the Hintz passage | **the test** |

`C3_self` is literally `C3` with `oracle_cards` swapped for model-built
cards — same injection (`conditions._oracle_block`), same calculator, same
scoring, same **code-computed** ground truth. The build step reads the real
passage from `sources/`; the solve step never sees the passage, only the
card (just like the oracle path).

**Thinking policy** (from 003): the **build** step runs thinking ON
(extraction/interpretation is reasoning); the **solve** step runs no-think
(these are single-formula problems, where no-think wins and is cheaper).

## Problems & sources

All `knowledge=absent` single-formula problems (where the card creates the
lift), each mapped to the exact Hintz section the oracle card cites:

| Problem | Formula | Source passage (Hintz) |
|---|---|---|
| `astro-016` | sphere of influence | §4.5, pp. 188–191 |
| `astro-025` | flight-path angle | §2.3, pp. 45–47 |
| `astro-027` | hyperbolic turn angle | §3.5, pp. 83–86 |

Passages live in `sources/*.txt` (faithful text transcriptions — the model
gets no figures). `sources.json` maps each problem to its passage and a
`purpose` string (the topic key the card is built for; it names the target
quantity but **not** the problem's numbers).

Note on difficulty: the flight-path-angle passage gives only the *components*
(`v sinβ = (μ/h)e sinθ`), not the closed `tan(γ)` form — so the model must
*synthesize* the usable formula, not copy it. That's a genuine "interpret"
test.

## Metric

```
lift_recovered = (C3_self − C2) / (C3_oracle − C2)
```

~1.0 → self-built cards are as good as human cards. ~0 → they're useless.
The summary also reports per-problem success and the card-build JSON
**parse-failure rate** (a self-built card that won't parse injects nothing,
collapsing C3_self toward C2 — a distinct failure mode from a *wrong* card).

## Running it

```bash
cd evals/004-self-built-cards

make smoke    # stub, no Ollama, <1s — verifies wiring
make quick    # 3 × {C2, C3_oracle, C3_self} × N=3 = 27 cells (~20 min)
```

Results land in `results/<timestamp>/` with `runs.jsonl`, per-run `logs/`,
and `summary.md`; `results/latest` is updated. Each `C3_self` record stores
the built card(s) and the build-call cost for auditing.

## What to look for

1. **Lift recovered** — the headline. Even ~60–70% would justify Phase 5.
2. **Parse-failure rate** — if cards often won't parse, the fix is output
   formatting, not knowledge. Cheap to address.
3. **Per-problem split** — does `astro-025` (must synthesize the closed form)
   lag `astro-016`/`astro-027` (formula stated outright)? That isolates
   *interpret* difficulty from *extract* difficulty.
4. **Built-card inspection** — read `built_cards` in the JSONL: are wrong
   answers from a wrong card, a missing intermediate, or a parse failure?

## Harness note

This runs on the **Python** harness (001–003 lineage) for speed — it answers
the gate question now, before the TypeScript build. The finding transfers;
PROJECT_PLAN.md schedules a re-run of 004 on the TS harness in Phase 3.
