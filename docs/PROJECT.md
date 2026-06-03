# Doba — Project Overview

> Local-first personal assistant *and* self-learning agent. Small model
> (~4B) + strong harness. A private Jarvis for daily life, with a memory
> system designed to genuinely *learn* — accumulate structured understanding
> over time without ever retraining the model.

**Owner:** Felipe · **Date:** 2026-06-03
**Sources:** vision from `draft.md` (draft 1); evidence from
`evals/001-astro-failure-modes` and `evals/002`/`003-thinking-with-oracle`;
design refinements tracked in `draft_3.md`.

This document is the single source of truth: it restates the project as
designed in draft 1, annotated with what the experiments actually showed.
Where an eval confirmed, refined, or has not yet touched a design decision,
it says so explicitly.

---

## 1. The thesis (unchanged)

A small model with a strong harness can outperform a big model with a weak
harness — *for the right scope*. Knowledge does not need to live in the
model's weights if it lives in a memory the agent can search, traverse, and
reason over. Like a human: we don't memorize the encyclopedia, we read,
understand, link, remember what matters, and forget what doesn't.

Doba does two things:

1. **Daily-life assistant** — reminders, lookups, computer/terminal/browser
   use, explanations on request.
2. **Self-learning agent** — accumulates structured understanding across all
   areas of knowledge so it becomes broadly more capable over time. The
   agent learns *for itself*; the user benefits because a better-read agent
   reasons better about everything. **Not a tutor** — no curriculum, no
   spaced repetition, no tracking of the *user's* knowledge.

"Learning" has a load-bearing definition: **understanding and interpreting
relevant information while forgetting and discarding useless information.**
The memory system is a learning *pipeline*, not a storage layer; forgetting
is first-class; and knowledge in one area should help reasoning in others
(transfer).

> **Evidence status of the thesis itself:** the experiments confirm the
> *conditional* form of it. A 4B with the right structured knowledge in
> context (C3) reached **75% vs the frontier's 60% raw** on a knowledge-
> application benchmark — the harness genuinely beats raw weights *when the
> knowledge is there*. What's **not yet proven** is that the agent can
> *produce* that knowledge itself (the Learning Loop). See §4 and §7.

---

## 2. What the experiments established

The first three evals were a controlled study on one sub-problem:
**knowledge-application** (look up a formula, apply it, compute an answer),
on 36 astrodynamics problems. They used an **oracle** — a hand-written,
perfectly-correct structured "concept card" injected into context — as a
*proxy for the upper bound of what retrieval could ever achieve*. No real
retrieval, graph, or ingestion was involved.

Five findings, each with a design consequence:

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| 1 | **Missing-knowledge is a real, large failure mode** (mode-a) | Oracle adds **+30 pp** over calculator-alone on problems the model demonstrably doesn't know | The knowledge layer is worth building — retrieval pays |
| 2 | **A reasoning-depth ceiling is real too** (mode-b) | `astro-031` (combined plane-change) fails **0/3 under the oracle, both thinking and no-think** | Retrieval alone is not enough; **decomposition** is required for deep problems |
| 3 | **Frontier models *also* need retrieval** | Oracle adds **+30 pp** to the frontier too, on the same axis | The knowledge layer is a *differentiator*, not a crutch for being small |
| 4 | **Indiscriminate knowledge injection hurts** | On problems the model *already* knows, the oracle costs **−10 pp** | Retrieve *conditionally* — gate on whether the model already knows |
| 5 | **Thinking mode flips sign by problem structure** | `<think>` is **+24 pp on multi-step / −22 pp on single-step**, at ~1.9× token cost | Deliberation is a *gated* spend, like retrieval — on for deep, off for shallow |

Two methodological lessons that shape the harness:

- **Count tool calls.** ~30% of "tool-using" runs once made *zero* real
  calls — the model hallucinated `RESULT:` lines and answered from invented
  intermediates. Every tool invocation must be structurally distinct in the
  trace, and the harness must *count* it. Trust no claim of tool use you
  didn't count.
- **Separate truncation from reasoning failure.** `astro-028` looked like a
  hard reasoning wall (0/10) until the token budget was doubled, at which
  point it recovered to 3/3. Always rule out *running out of tokens* before
  concluding *can't reason*.

The unifying refinement to draft 1: **spend extra context only when it
pays.** Retrieve only when the model doesn't already know; deliberate only
when the problem is deep. Draft 1 leaned this way already ("auto-inject only
a tiny pinned slice; everything else is tool-accessed; the agent decides
whether to recall"). The data turns that lean into a hard requirement and
adds a second, parallel gate for thinking mode.

---

## 3. Architecture (unchanged shape)

One agent. Three subsystems — **Memory** (two namespaces: personal and
knowledge), **Tooling** (tiered), **Reasoner** (the model) — plus a fourth
process, the **Learning Loop**, running alongside to turn encountered
information into structured understanding.

```
User Query
   │
   ▼
Single Agent (ReAct loop, optional planning + decomposition)
   ├── Memory ── Personal NS (working / episodic / semantic)
   │          └─ Knowledge NS (concepts + typed relations, graph + vector)
   ├── Tooling (core always-loaded; toolboxes lazy-loaded; discovery fallback)
   ├── Reasoner (4B; thinking mode gated on problem structure)
   └── Learning Loop (encounter → understand → interpret → integrate → forget)
   │
   ▼
Response / Action
```

The architecture is unchanged from draft 1. The experiments touched only the
**Reasoner + a stub of the Knowledge NS** (via the oracle). Everything else
in this diagram is designed but not yet evidence-tested.

---

## 4. The five pillars, with eval status

### Pillar 1 — Harness (ReAct + optional planning)
**Design (unchanged):** ReAct loop by default; a `make_plan` tool invoked
when the agent judges a task needs it.

**Eval status — partially validated, one promotion:**
- The ReAct + calculator loop worked: forcing real tool round-trips made
  traces *shorter and more correct* on multi-step problems, exactly as draft
  1 predicted ("shape every call into the kind small models are good at").
- **Promotion:** planning/decomposition moves from *optional* to *default
  for deep tasks*. The `astro-031` 0/3 result shows no amount of retrieval
  or deliberation substitutes for breaking a deep problem into steps. Add a
  `decompose_problem` affordance, not just `make_plan`. (Bonus: each leaf of
  a decomposed problem is a single-step task → run it no-think and cheap,
  per finding #5.)

### Pillar 2 — Single agent + ephemeral sub-tasks
**Design (unchanged):** one persistent agent; isolated ephemeral sub-tasks
for narrow operations that return a single condensed observation.

**Eval status — not yet tested.** Every eval cell was a single question;
multi-agent coordination and sub-task context isolation never came up. The
single-agent shape (one loop, calculator as sub-tool) held up fine as far as
it went. Sub-task isolation remains a design bet.

### Pillar 3 — Memory (two namespaces, four layers)
**Design (unchanged):** personal NS (working / episodic / semantic) and
knowledge NS (concepts + typed relations, graph + vector). Auto-inject only
a tiny pinned slice; everything else tool-accessed; **the agent decides
whether to recall**.

**Eval status — the core retrieval bet is validated; the mechanism is not:**
- **Validated:** structured, typed concept cards are the right unit. Handing
  the model a formula *in directly-applicable form* (with variable semantics
  and preconditions) is what produced the +30 pp lift. Naive paragraph-RAG
  would not have.
- **Validated and elevated:** "should I recall, and from where?" is not
  optional plumbing — it's a **load-bearing gate**. Because injecting known
  knowledge *hurts* (−10 pp), the agent must first check whether it already
  knows the topic and skip retrieval if so. Draft 1's "auto-inject vs
  explicit recall" section is promoted from polish to v1 requirement.
- **NOT tested:** the oracle *hand-picked the one correct card*. Real
  retrieval has to *find* the right card among many, tolerate distractors,
  and rank. The graph, the vector store, traversal, and forgetting were all
  bypassed. This is the biggest untested surface in the project.

### Pillar 4 — Tools (tiered access)
**Design (unchanged):** <10 core tools always loaded; toolboxes lazy-loaded;
vector tool-discovery as fallback. Structured `{ok}`/`{err}` results;
observation-shaped output.

**Eval status — not yet tested at scale.** Only one tool (the calculator)
was ever active. The "tool selection degrades past ~20 tools in context"
premise that motivates tiering is assumed, not measured here. The one hard
lesson that *did* land: tool invocations must be structurally countable
(see §2).

### Pillar 5 — Learning Loop (encounter → understand → interpret → integrate → forget)
**Design (unchanged):** the explicitly load-bearing pillar. Without it the
agent has the same capability on day 100 as day 1, plus clutter. With it,
every interaction deepens the world model.

**Eval status — UNTESTED, and this is the crux.** The oracle *replaced* this
entire pillar with a human-authored output. The +30 pp lift proves that *if*
a perfect card exists in memory, the small model can use it — it says
**nothing** about whether the 4B can *build* that card from raw source text
(understand → interpret → integrate), whether self-built cards recover the
same lift, or whether forgetting helps or hurts. **This is the single most
important thing left to validate**, because it's the difference between "the
agent gets smarter over time" being a mechanism and being an aspiration.

---

## 5. What's validated vs still open

| Claim from draft 1 | Status | Source |
|---|---|---|
| Small model + structured knowledge in context beats raw frontier | **Confirmed** (75% vs 60%) | 001 |
| Structured typed cards are the right knowledge unit | **Confirmed** (+30 pp vs paragraph-RAG would give) | 001 |
| Recall should be conditional / agent-decided | **Confirmed & elevated** (−10 pp when over-injected) | 001 |
| A reasoning-depth ceiling exists | **Confirmed** (`astro-031` 0/3) | 003 |
| ReAct keeps small-model calls short and correct | **Confirmed** | 001 |
| The agent can *build* its own knowledge (Learning Loop) | **UNTESTED** | — |
| Real retrieval finds the right card among distractors | **UNTESTED** | — |
| Cross-domain transfer (graph traversal surfaces analogies) | **UNTESTED** | — |
| Forgetting/pruning improves the knowledge layer | **UNTESTED** | — |
| Tiered tools beat all-tools-in-context at scale | **UNTESTED** | — |
| Sub-task context isolation works | **UNTESTED** | — |
| The daily-life assistant half works end-to-end | **UNTESTED** | — |

The pattern is clear: **the knowledge-*application* sub-problem is well
validated under an oracle; the knowledge-*production* and *retrieval-routing*
sub-problems — i.e. most of the Learning Loop and Memory pillars — are not.**

---

## 6. Build order (draft 1 §10 + eval refinements)

```
1. ✓  Eval harness + knowledge-application study       (DONE — 001/002/003)
2. ✓  Pick the 4B model                                (DONE — qwen3.5:4b)
3. -  Framework spike (pi-mono vs alternatives)
4. -  MVP assistant: single agent, ReAct, ~5 core tools, working memory
5. -  Personal memory: episodic vectors + semantic graph
6. -  Offline ingestion + flat retrieval + calculator  ← EVIDENCE-CLEARED, build it
7. -  Knowledge probe + conditional injection          ← ELEVATED to v1 (over-injection hurts)
8. -  Decomposition / planning by default              ← from astro-031 ceiling
9. -  Structure-gated thinking mode                    ← from finding #5 (cheap; toggle already exists)
10. - Knowledge graph + Learning Loop (the big one)    ← MUST be eval-gated first (§7, eval 004)
11. - Toolboxes + computer use (Jarvis capabilities)
12. - Cross-domain transfer / graph traversal          ← gated on its own eval (§7, eval 007)
13. - Online continual learning + self-directed study
```

Steps 6–9 are evidence-cleared and small. Step 10 (the Learning Loop) is the
heart of the project and should **not** be built blind — it needs eval 004
(below) to confirm the 4B can produce usable cards before investing weeks in
the ingestion pipeline.

---

## 7. Next evals (the validation roadmap)

The through-line: the oracle proved the *ceiling*. The next evals test
whether the real system can *reach* it. Ordered by how much each de-risks the
build.

**Eval 004 — Can the agent build its own cards? (Learning Loop, ingest half)**
*The most load-bearing eval left.* Replace the oracle with cards the 4B
constructs itself from source passages (e.g. Hintz PDF text). Compare:
`C3_oracle` (hand card) vs `C3_self` (4B-built card from the right passage)
vs `C3_self_noisy` (4B-built card from a passage buried in distractor text).
**Metric:** fraction of the oracle's +30 pp lift recovered by self-built
cards. If self-built cards recover most of it → the Learning Loop is viable,
build step 10. If they don't → the ingestion design needs rework *before*
any pipeline investment.

**Eval 005 — Retrieval routing under distractors (Memory, retrieval half)**
The oracle hand-picks the one right card; real retrieval returns top-k with
wrong cards mixed in. Inject 1 correct + N distractor cards and measure
degradation as N grows, with and without a rerank/probe step. **Metric:**
success rate vs #distractors; how often the model picks the right card.
Directly tests draft 1's "why not auto-inject everything" claim and draft 3's
open "does card ranking matter" question.

**Eval 006 — Does decomposition crack the reasoning ceiling? (Harness)**
Add an explicit decompose-then-solve harness (break into sub-steps, solve
each leaf no-think, compose) and re-run the multi-step failures, especially
`astro-031`. **Metric:** multi-step and `astro-031` success with vs without
decomposition. Validates the step-8 promotion cheaply.

**Eval 007 — Cross-domain transfer via graph traversal (the payoff claim)**
The most *differentiating* and the hardest to design. Build a tiny 2-domain
graph with `analogous_to` edges (e.g. entropy in physics ↔ information
theory) and test whether traversal surfaces a card from domain A that helps
solve a problem in domain B — vs flat retrieval that wouldn't reach it.
**Metric:** task success on transfer-requiring problems, graph vs flat. This
is the scoped test of draft 1's central "learning one area helps another"
thesis. Depends on a working knowledge graph, so it comes after 004/005.

**Eval 008 — Does forgetting help? (Learning Loop, prune half)**
Once a graph can be populated (incl. noise), measure retrieval quality and
task success with vs without pruning low-connectivity / low-usage nodes.
**Metric:** retrieval precision and task success, pruned vs unpruned.
Validates the first-class-forgetting claim. Lowest priority — needs a
populated graph first.

**Eval 009 — Daily-life task suite + tiered tools (the assistant half)**
The whole personal-assistant side is unmeasured. Write ~30 daily-life tasks
(draft 1 §8/§10 step 1) and run them through a harness with >10 tools across
toolboxes. **Metric:** success rate, turns-to-success, tool errors, tool-
selection accuracy. Tests Pillars 2 and 4. More an integration eval than a
research eval; can run in parallel with the knowledge-side evals.

**Recommended order:** 004 → 006 (cheap, parallelizable) → 005 → 007 → 008,
with 009 running alongside whenever the assistant MVP (step 4) exists. 004 is
the gate: it decides whether the Learning Loop is buildable as designed.

---

## 8. The honest one-paragraph summary

Draft 1's vision is intact and its central bet — small model + structured
knowledge beats raw frontier — is now backed by numbers, *for the
knowledge-application half*. A 4B with a perfect card in context beats the
bare frontier, knowledge injection should be conditional (over-injecting
hurts), deliberation should be gated on problem depth, and a real reasoning
ceiling exists that only decomposition addresses. But everything proven so
far rode on an **oracle** that stood in for the Learning Loop and the
retrieval system. The defining question of the project — *can the agent
build and retrieve its own structured knowledge well enough to reach that
ceiling?* — is exactly what the next evals (004 first) must answer before the
Learning Loop is worth building at scale.
