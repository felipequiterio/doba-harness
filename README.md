# doba-harness

A local-first personal assistant **and** self-learning agent: a small (~4B)
model paired with a strong harness, designed to outperform a big model with a
weak harness *for the right scope*. Knowledge lives in a memory the agent can
search, traverse, and reason over — not in the model's weights.

This repo holds the **design** and the **evaluation harness** that de-risks it.
Each risky design bet is settled by an experiment *before* it's built.

## Layout

```
docs/      design documents — start with PROJECT_PLAN.md (the master build plan)
           and PROJECT.md (vision + eval findings); draft.md / draft_3.md are history
evals/     the Python evaluation harness
  lib/         shared harness: agent loop, model client, calculator,
               card builder, symbolic tools, scoring
  datasets/    problem sets + code-computed ground truth
  NNN-*/       individual experiments (each: README, config, results/)
```

## Evaluation track (so far)

The harness is the unit of experimentation — every experiment is a condition
comparison on the same core. Findings to date (full detail in
`docs/PROJECT_PLAN.md` §9):

- **001–003** — a perfect knowledge card ("oracle") adds +30pp on problems the
  4B doesn't know, and a tool+oracle 4B beats the raw frontier; over-injection
  hurts; `<think>` mode helps multi-step but hurts single-step; a real
  reasoning ceiling exists.
- **004** *(the gate)* — the 4B can **build its own** knowledge cards from a
  source passage and recover the lift. Learning Loop is viable.
- **005** — its symbolic derivations are reliable in-head; a CAS tool isn't
  needed at this difficulty (kept as an offline validator).
- **006** — generic retrieval noise is harmless; *near-miss* (sibling-concept)
  cards are the real risk, via the selection step.

## Running the evals

Requires [uv](https://docs.astral.sh/uv/) and a local
[Ollama](https://ollama.com/) serving `qwen3.5:4b`.

```bash
cd evals
make help            # shared targets (test, gt-check, list-evals, …)
cd 004-self-built-cards && make smoke   # stub wiring check, no model
make quick                              # a real run
```

Every run writes a timestamped `results/<timestamp>/` with per-cell logs, a
JSONL record, and a summary. (Result dirs are gitignored.)

## Note on source material

The evals reference Hintz, *Orbital Mechanics and Astrodynamics* for the
astrodynamics problem set. The textbook PDF is **not** included (copyright);
only the code-computed ground truth and short transcribed formula passages
used by the experiments are kept.

## Status

Pre-product: the design and research harness exist; the TypeScript product
(frontend / API / harness) is planned in `docs/PROJECT_PLAN.md` and not yet
built.
