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
| 2 | **No genuine reasoning ceiling found** (mode-b *unconfirmed*) | Every "ceiling" exemplar fell to budget + tool-turns: `astro-028` recovered at 8192 tokens (002), and `astro-031` — long cited as the hard 0/3 wall — went **3/3** at 16k tokens + a higher tool-turn cap (008). The wall was a **truncation/turn-cap** artifact each time | The bottleneck is **harness budget/turns + the thinking-loop runaway**, not reasoning depth; decomposition's motivation is weaker than thought |
| 3 | **Frontier models *also* need retrieval** | Oracle adds **+30 pp** to the frontier too, on the same axis | The knowledge layer is a *differentiator*, not a crutch for being small |
| 4 | **Indiscriminate knowledge injection hurts** | On problems the model *already* knows, the oracle costs **−10 pp** | Retrieve *conditionally* — gate on whether the model already knows |
| 5 | **Thinking mode flips sign by problem structure** | `<think>` is **+24 pp on multi-step / −22 pp on single-step**, at ~1.9× token cost | Deliberation is a *gated* spend, like retrieval — on for deep, off for shallow |

Two methodological lessons that shape the harness:

- **Count tool calls.** ~30% of "tool-using" runs once made *zero* real
  calls — the model hallucinated `RESULT:` lines and answered from invented
  intermediates. Every tool invocation must be structurally distinct in the
  trace, and the harness must *count* it. Trust no claim of tool use you
  didn't count.
- **Separate truncation from reasoning failure.** This one bit *three times*:
  `astro-028` looked like a 0/10 wall until the token budget doubled (002);
  `astro-031` looked like a 0/3 wall until the budget went to 16k and the
  tool-turn cap was raised (008). Every apparent "reasoning ceiling" in this
  dataset has been a token/turn limit. Always rule out *running out of tokens
  or turns* before concluding *can't reason* — and **cap the thinking loop**,
  because the opposite failure (a 30k+-token runaway that never commits) is the
  *actual* recurring bug (005/007/008).

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
- **Promotion (with a caveat).** Planning/decomposition was promoted to
  *default for deep tasks* on the strength of the `astro-031` 0/3 "ceiling" —
  but **008 overturned that**: `astro-031` solves 3/3 with adequate budget +
  tool-turns, so there's no ceiling for decomposition to *crack*. A first-cut
  decompose-then-solve harness also *underperformed* the monolithic solve (a
  broken-leaf execution, not a clean test). So decomposition is **demoted from
  "evidence-backed default" to "unproven"** — its remaining motivation is
  reliability/cost (no-think leaves, no dropped sub-steps), not cracking a
  ceiling. The higher-priority, evidence-backed fix is the **harness budget +
  tool-turn cap + thinking-runaway cap** (008's real lesson).

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

**Eval status — INGEST HALF VALIDATED (gate passed); full pipeline still to
build.** This was the crux, and evals 004/005/007 answered it. The 4B **can
build its own cards** from a source passage and recover the oracle lift, leakage-
free (**004**, the gate); it **derives** unstated closed forms reliably in-head,
no CAS needed (**005**); and it builds correct **multi-card sets** for multi-step
problems and composes them at least as well as hand cards (**007**). One
sharp refinement fell out: **card *clarity* beats *canonical-ness*** — a terse
human-authored composed formula can *mislead* the small model where its own
granular decomposition doesn't, so the Learning Loop should favour granular,
clearly-named cards. **Still untested:** the *online* pipeline (graph
integration, consolidation) and **forgetting**. The headline risk has moved from
"can it build knowledge?" (answered: yes) to harness robustness (a thinking-loop
token runaway recurred in 005/007) and composition depth at the reasoning
ceiling. (Detailed per-eval record: `PROJECT_PLAN.md` §9.)

---

## 5. What's validated vs still open

| Claim from draft 1 | Status | Source |
|---|---|---|
| Small model + structured knowledge in context beats raw frontier | **Confirmed** (75% vs 60%) | 001 |
| Structured typed cards are the right knowledge unit | **Confirmed** (+30 pp vs paragraph-RAG would give) | 001 |
| Recall should be conditional / agent-decided | **Confirmed & elevated** (−10 pp when over-injected) | 001 |
| A reasoning-depth ceiling exists | **Not confirmed** — `astro-031` (cited as the 0/3 wall) solved **3/3** at adequate budget+turns; no genuine ceiling found | 003 → overturned by 008 |
| The real failure mode on "hard" multi-step is *truncation/runaway*, not depth | **Confirmed** — apparent ceilings = token/turn limits (002/008); a 30k+-token thinking runaway is the recurring bug | 002/005/007/008 |
| ReAct keeps small-model calls short and correct | **Confirmed** | 001 |
| The agent can *build* its own knowledge (Learning Loop ingest) | **Confirmed** — single cards (004), derivation (005), multi-card sets (007); favours *granular* cards | 004/005/007 |
| The 4B can *derive* unstated formulas (no CAS needed) | **Confirmed** (~100% in-head) | 005 |
| Real retrieval survives distractors | **Partly confirmed** — generic noise harmless; *near-miss* sibling-concept cards are the real risk, via selection | 006 |
| Cross-domain transfer (graph traversal surfaces analogies) | **UNTESTED** | — |
| Forgetting/pruning improves the knowledge layer | **UNTESTED** | — |
| Tiered tools beat all-tools-in-context at scale | **UNTESTED** | — |
| Sub-task context isolation works | **UNTESTED** | — |
| The daily-life assistant half works end-to-end | **UNTESTED** | — |

The pattern has shifted: **knowledge-*application* and knowledge-*production*
(the Learning Loop's ingest half) are now both validated under the research
evals**; what remains open is the *online* pipeline (graph, forgetting),
retrieval *at scale*, cross-domain transfer, and the entire daily-life half.
The blocking risks are now harness robustness (the thinking-loop runaway) and
composition depth, not "can the agent build knowledge?"

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
8. -  Harness budget + tool-turn + thinking-runaway cap ← EVIDENCE-CLEARED (008): the real bottleneck on "hard" problems
9. -  Structure-gated thinking mode                    ← from finding #5 (cheap; toggle already exists)
   -  Decomposition / planning                         ← DEMOTED to unproven (no ceiling to crack; first harness underperformed)
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

> **Note (post-007):** this section is the *original* planned roadmap, written
> before execution. The evals were built in a different order and the numbers
> diverged (the actual research evals are `001`–`007`: oracle/thinking, then
> self-built cards, symbolic derivation, distractor retrieval, multi-card
> building). **The authoritative, up-to-date eval record is
> [`PROJECT_PLAN.md` §9](PROJECT_PLAN.md).** What follows is kept for the
> *reasoning* behind each test; ignore the eval numbers here.

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
knowledge beats raw frontier — is now backed by numbers. A 4B with the right
card in context beats the bare frontier; knowledge injection should be
conditional (over-injecting hurts); deliberation should be gated on problem
structure. And the defining question — *can the agent build its own structured
knowledge?* — has been answered **yes**: the 4B builds correct single cards
(004), derives unstated formulas without a CAS (005), survives retrieval noise
but must dodge near-misses (006), and builds multi-card sets it composes better
than terse hand cards (007). Two beliefs were overturned along the way: there
is **no genuine reasoning ceiling** (every "wall" was a token/turn-cap
artifact — 008), and decomposition is **unproven** (its first harness was
broken, and with no ceiling its motivation is now reliability, not depth). The
real remaining risk is **harness robustness** — a thinking-loop token runaway
and the budget/turn caps are what actually decide the hard multi-step problems.
What's still untested is the *online* Learning Loop (graph, forgetting),
retrieval at scale, cross-domain transfer, and the entire daily-life half.
