# Doba — Design Document (Draft 3)

> Local-first personal assistant built on a small model and a strong harness.
> A private Jarvis for daily life, plus a **knowledge layer** that lets it
> close knowledge gaps by retrieving curated, structured knowledge and using
> tools — without ever retraining the model.

**Status:** post-experiment · **Owner:** Felipe · **Date:** 2026-06-03
**This draft supersedes:** `draft_2.md`
**Evals folded in:** `001-astro-failure-modes` (mode-a/b sweep), `002`/`003-thinking-with-oracle` (thinking-mode isolation)

---

## 0. What changed from draft_2 (and why)

`draft_2.md` was a re-scoped, evidence-gated design. It made one big claim
and one big bet:

- **Claim:** a small local model has *decent reasoning* but *unreliable
  knowledge*; tools + retrieval can close most of the gap, at near-zero
  marginal cost.
- **Bet:** before building the retrieval system, run an experiment
  (`evals/001-astro-failure-modes`) where injecting the perfect knowledge
  (an "oracle" formula card) into context proxies for the *upper bound* of
  what retrieval could ever do. If oracle injection doesn't close the gap,
  knowledge isn't the bottleneck and retrieval won't help.

**The experiment ran.** This draft records what it actually showed, what
that means for the design in draft_2, and where the build order now diverges.

Headline findings, in one breath:

1. **Mode-(a) is real and big.** On problems the model demonstrably doesn't
   know, oracle injection adds **+30 percentage points** over calculator
   alone. Retrieval is worth building.
2. **Mode-(b) is real too — but it's entangled with token budget.** The
   first sweep saw a 3-burn bi-elliptic transfer (`astro-028`) fail 0/10,
   which read as a hard reasoning ceiling. Later runs showed that wall was
   partly a *budget* artifact: at 8192 tokens (vs the original 4096) and
   with thinking on, `astro-028` recovers to 3/3. The genuine depth wall in
   the expanded data is `astro-031` (combined plane-change + circularize),
   which fails **0/3 in both thinking and no-think under the oracle**. The
   ceiling is real; the exemplar moved (see §4.3).
3. **Frontier models *also* benefit from oracle injection** (+30 pp on the
   same axis). This isn't "compensating for being small" — knowledge breadth
   is a retrieval problem for everyone.
4. **Oracle injection has a cost when used indiscriminately**: on problems
   the model *already* knows, it slightly *hurts* (−10 pp). The probe step
   isn't optional plumbing — it's a load-bearing gate on whether to inject.
5. **Thinking mode is not uniformly good or bad — it flips sign by problem
   structure.** With the oracle always on, Qwen3's `<think>` mode is worth
   **+24 pp on multi-step problems** (it prevents tool-turn exhaustion) and
   **−22 pp on single-step problems** (it overthinks a lookup the model
   already nails cheaply), at ~1.9× the token cost. Like injection, thinking
   wants a *gate*, not an always-on switch (see §4.6).

In short: **draft_2's thesis holds**, with two refinements that rhyme:
*conditional* injection (gated on a probe) beats always-on injection, and
*conditional* thinking (gated on problem structure) beats always-on
thinking. Both are "decide whether to spend the extra context, don't spend
it by default."

---

## 1. The experiment (what was actually built)

`evals/001-astro-failure-modes/` implements draft_2's Suite B, scaled to
**36 astrodynamics problems** (18 single-formula, 18 multi-step
composition; `astro-035`/`036` were added for the thinking evals), each
with:

- a `given` dict with named SI inputs,
- an `expected` SI value computed deterministically in `ground_truth.py`
  (asserted match via `make gt-check`),
- a per-problem `tolerance_rel` (default 1%),
- one or more **typed concept cards** (`oracle_cards[]` — formula, variable
  semantics, preconditions, source citation to Hintz §X.Y),
- a `canonical_method` (ordered list of steps) used by the LLM-as-judge,
- a `knowledge_label` populated by the probe (`present` / `absent`).

Five conditions per problem (run N=5 each):

| ID | Model | Tools | Oracle |
|---|---|---|---|
| C1 | local 4B (qwen3.5:4b) | none | none |
| C2 | local 4B | calculator | none |
| C3 | local 4B | calculator | **oracle injected** |
| REF | frontier (Gemini 2.5 Flash) | none | none |
| REF_C3 | frontier | calculator | **oracle injected** |

REF_C3 was added after the initial sweep. It's the single most informative
addition — see §4 below.

A small "probe" pass runs first: the 4B is shown only the *topic* of each
problem and asked for the formula. A frontier judge decides if the answer
is consistent with the canonical method, and stamps `knowledge_label` on
each problem.

---

## 2. What the data showed (quick run, 10 curated problems × N=2 = 100 cells, 38 min)

| | **C1** raw 4B | **C2** +calc | **C3** +calc +oracle | **REF** frontier raw | **REF_C3** frontier +oracle |
|---|---|---|---|---|---|
| overall      | 70% | 65% | 75% | 60% | **90%** |
| singles      | 90% | 80% | 90% | 50% | 100% |
| multi        | 50% | 50% | 60% | 70% |  80% |
| knowledge=absent | 50% | 50% | **80%** | 30% | 80% |
| knowledge=present | 90% | 80% | 70% | 90% | 100% |

The headline rankings are unsurprising. The decisive numbers live in
the rows below:

| Δ | Result | Interpretation |
|---|---|---|
| **C3 − C2 on knowledge=absent** | **+30 pp** | The mode-(a) gap. Oracle reduces it dramatically. |
| C3 − C2 on knowledge=present | **−10 pp** | Oracle distracts when the model already knows. Probe-gating is needed. |
| C2 − C1 on knowledge=absent | 0 pp | Calculator alone doesn't help if you don't know the formula. Expected. |
| **REF_C3 − REF** | **+30 pp** | Even the frontier is knowledge-bottlenecked on rare formulas. |
| REF_C3 − C3 | +15 pp | The residual gap is reasoning depth — exactly mode-(b). |

**One problem (`astro-028`, bi-elliptic transfer Δv) failed 0/10 across
every condition including REF_C3** *at the 4096-token budget this run used.*
Four vis-viva calls + three burn-difference sums in one composition. It read
at the time as the cleanest evidence that no harness fixes reasoning depth
past a certain composition size — but the follow-up evals showed most of
that 0/10 was *truncation*, not reasoning: at 8192 tokens `astro-028`
recovers (3/3 with thinking). The durable mode-(b) exemplar is `astro-031`
(0/3 even at the higher budget); see §4.3 for the disentangling. The
ceiling draft_2 §1 acknowledged is real — it just sits one problem deeper
than this run suggested.

(Earlier full sweep — 34 × N=5 = 680 cells, 3h 40m — gave the same
direction-of-effect with smaller magnitudes because it pre-dated the
prompt-compliance fix and the probe step. The 100-cell run is what's quoted
above because its design matches the conclusions.)

---

## 3. What's confirmed from draft_2

These were predictions; the experiment now backs them with numbers.

### 3.1 The two-failure-mode framing is the right axis
Draft_2 §1.2 split failures into mode-(a) (missing knowledge / unverified
computation) and mode-(b) (reasoning depth). The data cleanly separates:

- **Oracle moves the needle on absent-knowledge problems** (mode-a fix).
- **Oracle does not move the needle on multi-step composition past a
  certain depth** (mode-b ceiling).

`astro-031` (combined plane-change + circularize) is the cleanest mode-(b)
signal in the dataset: **0/3 under the oracle in both thinking and no-think**
(eval 003). Neither extra knowledge nor extra deliberation cracks it — the
hallmark of a depth ceiling rather than a knowledge or budget gap.
(`astro-028` looked like this signal in the first sweep but turned out to be
budget-bound — see §4.3.)

### 3.2 The concept card (structured, typed, applicable) is the right unit
Draft_2 §5 called this "the most important design decision." The experiment
operationalized it via `oracle_cards[]` and got the +30 pp lift on absent.
Naive chunk-RAG would have retrieved *paragraphs* mentioning a formula;
typed cards hand the model the formula in directly-applicable form, with
variable semantics and preconditions. That structural choice is what makes
small-model retrieval *usable*, not just *retrievable*.

**Action:** keep the `ConceptCard` schema from draft_2 §5 (formula |
procedure | event | fact | personal), make it canonical. The
`oracle_cards[]` schema in `astrodynamics_v1.json` is the working template.

### 3.3 Tools (calculator) kill the verification spiral
Draft_2 §1.3 reasoned that the long "thinking" trace of an ungrounded model
is the *bug, not a feature* — tools should make traces *shorter*. The
experiment showed this directly: the harness fix that made the model
actually call the tool (rather than hallucinate `RESULT:` lines for
itself — `evals §step-fix`) immediately produced shorter, more correct
traces on multi-step problems. The C3 token count *dropped* when the model
actually used the calculator instead of inventing intermediates.

### 3.4 A tool-equipped 4B + oracle is competitive with the frontier
| | overall | gap to REF_C3 ceiling |
|---|---|---|
| C3 (local + oracle) | 75% | 15 pp |
| REF (frontier raw) | 60% | 30 pp |

**The local 4B with the right scaffolding actually beats the frontier model
without it.** That's the strongest possible vindication of the
"strong harness > raw weights" thesis from draft_2 §1.

---

## 4. What's NEW (not in draft_2)

### 4.1 Indiscriminate oracle injection has a measurable cost
**Finding:** C3 − C2 on `knowledge=present` problems = **−10 pp**.

Two plausible mechanisms (both probably contributing):

- **Context distraction.** Oracle cards add ~500 prompt tokens of formula
  descriptions, variable definitions, "applies when" clauses. The model
  re-reads them instead of just applying the formula it already had in
  weights.
- **Overthinking the preconditions.** The "applies when" clauses give the
  model an opportunity to question whether the formula applies here — and
  sometimes it talks itself out of using it correctly.

Either way, the design implication is the same: **the probe (knows / does
not know) is not optional plumbing for analysis. It's a gate on whether to
retrieve at all.** Draft_2 §7.3 implied this as a Phase-2 nicety; the data
elevates it to a v1 requirement.

**Design update (supersedes draft_2 §5):**
- The recall step the agent runs early — "should I recall, and from where?" — should *first* check whether the model knows this topic. If yes, skip retrieval; trust the weights. If no, retrieve.
- Implementation: a tiny "self-confidence" check before retrieval, OR a periodically-refreshed `known_topics` map derived from the offline probe. The first is more flexible; the second is cheaper at inference time. Probably both.

### 4.2 Frontier models also need retrieval
**Finding:** REF_C3 − REF = +30 pp. On problems the bare frontier doesn't
know, oracle injection helps it just as much as it helps the 4B.

This reframes the entire Doba pitch. Draft_2 framed retrieval as "what
compensates for being small." The data says: **retrieval is what
compensates for knowledge breadth, period — for every model size.** A 4B
with retrieval recovers more frontier-ceiling than the bare frontier does.

**Design implication:** the knowledge layer is the differentiator, not the
crutch. The same retrieval pipeline (offline ingestion → cards → query
routing → rerank → inject) would help any model size; it just happens to be
the *only affordable* path to broad knowledge for the local 4B that defines
Doba's cost profile (draft_2 §1.4).

### 4.3 The reasoning ceiling has a concrete depth — but check the token budget first
**Finding (revised):** In the first sweep, `astro-028` (bi-elliptic
transfer, 4 vis-viva composes + 3 burn sums) failed 0/10 and looked like a
hard ceiling. It wasn't — or not entirely. Two follow-up evals isolated the
cause:

- Raising the completion budget from 4096 → 8192 tokens, `astro-028`
  recovers (eval 002 saw a raw 4B with no thinking go 2/2; eval 003 saw
  `C3_T` go 3/3). The original 0/10 was a *truncation* failure as much as a
  reasoning one — the model ran out of tokens mid-composition.
- The genuine depth wall is `astro-031` (combined plane-change +
  circularize, law-of-cosines on the burn triangle): **0/3 under the oracle
  in both thinking and no-think** (eval 003), at 8192 tokens with no
  truncation. No amount of knowledge, deliberation, or budget cracked it.

So mode-(b) isn't binary "the model can / can't reason," and it isn't a
clean step at "~5 formulas" either. It's a **depth budget entangled with a
token budget**: composition of ~2-3 formulas works with the oracle;
deeper compositions fail in two distinguishable ways — *truncation* (fixable
with budget, `astro-028`) and *genuine collapse* (not, `astro-031`). The
design lesson is to separate them: always rule out truncation (raise the
budget, count the tokens) before concluding "the model can't reason this."

**Design implication:** plan for *decomposition*, not just retrieval.
Draft_2 §3 endorses ReAct precisely because each turn is short ("what
next?") — but the agent itself currently has no explicit "decompose this
into sub-problems" affordance beyond the optional `make_plan` tool. For
real engineering tasks (the long horizon Jarvis use case), the agent should
plan a sub-task tree first, executing pieces one at a time. The `astro-031`
result (0/3 even with the oracle, both thinking modes, no truncation) is a
small-dataset analog of what this looks like at scale: **no amount of
retrieval or deliberation substitutes for breaking the problem down before
solving.** And decomposition pays a second dividend — each leaf becomes a
single-step task that can run cheaper, no-think (§4.6).

**Action:** elevate `make_plan` from optional capability (draft_2 §3) to
default behavior on any task with ≥3 named sub-steps in the user's request.
Plus add a `decompose_problem(problem)` tool that emits a sub-problem
tree the agent then walks.

### 4.4 The harness will silently degrade if the tool protocol is too loose
**Finding:** before a specific fix, ~30 % of C3 runs had **0 calculator
calls** despite using the calculator tool. The model wrote both
`CALC: ...` AND `RESULT: ...` lines itself in one turn, then committed
`FINAL_ANSWER` based on hallucinated intermediates. The harness saw
FINAL_ANSWER first and exited.

This was caught because per-run logs surface "calculator calls: N" in the
header. Without that, the C3 results would have looked fine (model
"used" the calculator) but quietly drawn from hallucinated values.

**Design implication for the Doba harness (extends draft_2 §3 / §6):**

- Every tool invocation must be **structurally distinct** in the trace — not
  just a text marker the model can fake. Either:
  - Use the model's native function-calling format (OpenAI tools / Anthropic
    tool_use blocks), which the SDK validates, OR
  - Keep a text protocol but require the model to **wait** for the system
    message before continuing (loop logic must enforce this — see the patch
    in `src/conditions.py:run_condition` where CALC takes priority over a
    same-turn FINAL_ANSWER).
- Every tool-using condition should report **n_tool_calls** in its log
  header. If it ever shows 0 when you expect ≥1, you have a Potemkin
  trace.

This isn't a draft_2 vs draft_3 disagreement — it's a meta-lesson: trust
no claim of tool use that you didn't *count*.

### 4.5 Format compliance is a separate, real failure mode
**Finding:** ~20 % of REF runs in the initial full sweep failed because the
model computed the answer correctly and then *kept talking* instead of
emitting `FINAL_ANSWER:`. The fix was a strengthened system prompt + an
extraction-from-trace-tail salvage pass.

For Doba this means: **the user-facing contract must be strict and the
harness must be forgiving on the back end.** Tell the model the format is
load-bearing; if it forgets, salvage rather than fail. Both layers earn
their place.

### 4.6 Thinking mode flips sign by problem structure (evals 002 / 003)
Draft_2 §1.3 framed the long "thinking" trace of an ungrounded model as
*the bug, not the feature*. That's true on shallow problems and **false on
deep ones** — a distinction the first sweep couldn't see because it never
isolated thinking from everything else.

Evals 002 and 003 vary exactly one thing: Qwen3's `<think>` mode on/off,
with the **oracle always on** (so this is a pure reasoning-process
question, not a knowledge one). The decisive run is 003 long — 10 problems
(7 multi, 3 single) × 2 conditions × N=3 = 60 cells:

| | `C3_NT` (no-think) | `C3_T` (think) | Δ |
|---|---|---|---|
| **multi** (21 cells) | 9/21 (43%) | 14/21 (67%) | **+24 pp** |
| **single** (9 cells) | 8/9 (89%) | 6/9 (67%) | **−22 pp** |
| overall | 17/30 (57%) | 20/30 (67%) | +10 pp* |

\* the overall +10 pp is an artifact of this set being multi-heavy; the
honest reading is the per-structure split, not the average.

![thinking flips sign by structure](assets/003_thinking_by_structure.png)

The mechanism is visible in the per-run logs:

- **On multi, no-think fails by running out of road.** All 5 of `C3_NT`'s
  `max_tool_turns` failures were multi-step problems — it can't hold the
  plan together across calculator round-trips and gets cut off mid-
  composition. Thinking front-loads the plan inside `<think>` and then
  commits: **0/30 non-commits** for `C3_T` (every cell emitted
  `FINAL_ANSWER`). Thinking buys *robustness on composition*.
- **On single, thinking talks itself out of the answer.** On rare-formula
  one-shots (`astro-016` sphere-of-influence, `astro-027` turn-angle),
  no-think nails it in 3–6k tokens; thinking burns 8–16k and flips one run
  each to wrong. Classic overthinking of a lookup-and-apply.

**Cost:** thinking ran ~1.9× the tokens-per-correct (12.4k vs 6.6k) and
~1.75× the latency-per-correct (174 s vs 99 s). It is *never free*; on
single-step problems you pay double for a worse answer.

**Design update (rhymes with §4.1):** thinking mode wants a **gate**, the
same shape as conditional injection. The decision variable is problem
structure / expected step count, which the agent already needs to estimate
for decomposition (§4.3):

- **Default `<think>` ON** for tasks the planner marks as multi-step /
  decomposed (≥3 named sub-steps) — the +24 pp robustness is worth the
  token cost there.
- **Default `<think>` OFF** for single lookup-and-apply tasks — let the
  weights (or the injected card) answer directly; deliberation only adds
  cost and overthinking risk.
- This composes cleanly with §4.3's decomposition affordance: once a problem
  is broken into sub-steps, **each leaf is a single-step task** and should
  run no-think, while the planning/composition level runs with thinking.
  Decomposition is what lets you get the multi-step robustness *and* the
  single-step cheapness at once.

**Caveat (don't over-read this):** 002 and 003 disagreed on the *overall*
sign at small N (002's 5-problem set was single-heavy and made thinking look
strictly harmful). The structure split is what reconciles them and is stable
at N=3; the flat overall number is not. Per-formula / per-leaf gating, like
the injection probe (§6), is the surgical version still to be validated.

---

## 5. The decision rule, applied

Draft_2 §8 stated:

> Decision rule: do not build the graph or the online loop until C3 beats
> C2 with evidence. If flat-RAG + calculator already closes the gap, the
> elaborate machinery is unjustified for this use case.

Evidence is now in:

- C3 − C2 on knowledge=absent: **+30 pp** — flat oracle-RAG decisively
  closes the gap. **Build retrieval.**
- C3 − REF_C3 residual on multi: −15 pp — there's a real reasoning gap
  that retrieval doesn't fix; the *graph* over the store (draft_2 §5)
  won't change that part of the gap either. Graph remains TBD on a separate
  experiment specifically about cross-domain analogy retrieval.
- `astro-031` 0/3 under the oracle, both thinking and no-think: a genuine
  reasoning collapse that no knowledge, deliberation, or budget fixed.
  Deep compositions need **decomposition / planning, not optional**. (And
  `astro-028` taught the prior lesson: rule out token-budget truncation
  before calling something a reasoning ceiling — see §4.3.)
- Thinking mode: +24 pp on multi / −22 pp on single (§4.6). A second
  spend-only-when-it-pays knob alongside injection. **Gate it on structure.**

So the build order from draft_2 §10 updates as:

```
1. ✓  Eval Suite A + B  (DONE: this is what produced these conclusions)
2. ✓  pi-spike / harness  (in progress: see evals/001 ground truth)
3. ✓  Pick the 4B (DONE: qwen3.5:4b)
4. -  MVP assistant (next)
5. -  Personal memory
6. -- Offline ingestion + flat RAG + calculator  ←  EVIDENCE-CLEARED, build it
7. -  Knowledge probe + conditional injection  ←  ELEVATED to v1 (was draft_2 deferred)
8. -  Decomposition / planning by default      ←  NEW from astro-031 finding
9. -  Structure-gated thinking mode            ←  NEW from eval 003 (§4.6)
10. - Graph RAG — defer to a SECOND experiment focused on cross-domain analogy
11. - Toolboxes + computer use  (Jarvis capability)
12. - Online continual + curation
13. - Trace-distillation fine-tune (Phase 2 portfolio piece)
```

The changes from draft_2 §10:

- **Step 7 (conditional injection)** was implicit in draft_2 §5's "auto-
  inject vs explicit recall" section but treated as a polish concern. The
  experiment promoted it to a v1 requirement because indiscriminate
  injection measurably hurts.
- **Step 8 (decomposition)** was implicit in draft_2 §3 (the `make_plan`
  tool) but treated as opt-in. The `astro-031` 0/3 result (and `astro-028`'s
  truncation failures before the budget fix) says it's the difference
  between "agent can solve deep problems" and "agent fails silently on
  them." Make it default.
- **Step 9 (structure-gated thinking)** is new in draft_3, from eval 003's
  +24/−22 pp structure split. It's cheap to implement (the `<think>` toggle
  already exists per-condition in the harness) and rides on the same step-
  count estimate that step 8 produces — turn thinking on for multi-step /
  planning levels, off for single-step leaves.
- **Step 10 (graph)** is still deferred but now needs its *own* experiment
  before getting built — the current data says nothing about graph
  traversal value because oracle injection bypasses retrieval entirely.

---

## 6. What's still open

These are honest gaps the experiment did not close, in priority order:

| Open question | Why it matters | How to close |
|---|---|---|
| **Does graph traversal surface useful cross-domain analogy?** | Draft_2 §7.4 calls this the scoped version of the "agent gets smarter" dream. The oracle experiment said nothing about it. | Suite-B-style experiment with two ingestion modes: flat store vs graph-with-`prerequisite_of`/`analogous_to` edges, on problems that *require* analogy (e.g., "entropy in information theory" answered using physics priors). |
| **What's the right probe granularity — per topic? per formula? per problem?** | The current probe labels problems as `present`/`absent`. But "knows Tsiolkovsky" and "knows Tsiolkovsky-with-staging" are different. Per-formula probing would let conditional injection be more surgical. | Re-probe at the oracle-card level rather than the problem level. |
| **Does ranking the oracle cards matter when ≥3 are relevant?** | C3 currently injects all matching cards. If the model is distracted on `knowledge=present`, it'll be more distracted by 3 cards than by 1. | Add a synthetic "distractor card" condition: inject 1 right + 2 wrong cards, measure degradation. |
| **At what dataset scale does the offline → online cutover stop being premature?** | Draft_2 §7.2 makes online the forever-project. We don't know the size at which the offline store stops covering daily-life queries. | Operate Doba in daily-life mode for a month with offline-only, log retrieval-miss queries, count. |
| **Does qwen3.5:4b's variance shrink at N=10 or N=20?** | Several quick-run cells split 1/2; at N=2 that's a coin flip. We don't yet know whether the variance is from temperature sampling or from genuine instability in the model on borderline problems. | Re-run the curated 10 at N=10 (still finishable in ~2.5 h). |
| **Is the thinking gate better keyed on step-count than on a binary single/multi label?** | §4.6 gates thinking on `structure ∈ {single, multi}`, but the real driver looks like composition depth (multi `astro-023` at 2 steps behaves more like a single). A continuous step-count threshold may gate more precisely and would slot into the decompose step's output directly. | Re-label the dataset with an explicit `n_steps` and re-run 003's split bucketed by step-count instead of the binary structure flag. |

---

## 7. Things draft_2 said that the experiment did NOT challenge

Recording for fairness — draft_2 made a lot of design choices and most of
them stood up:

- **Single agent + ephemeral sub-tasks** (§4): the multi-agent coordination
  problem never surfaced in this experiment because every cell was a single
  question. But the harness design (one persistent loop, calculator as
  sub-tool) is exactly the single-agent shape and it worked fine.
- **Tiered tools** (§6): not stressed yet; only one tool active. The pattern
  ("core always; lazy-load toolboxes; vector tool-search as fallback") is
  unchanged.
- **Two namespaces** (§5): not stressed — no personal memory in this
  experiment. Architecture stands.
- **Fine-tuning is the wrong tool for knowledge** (§7.6): every C3 oracle
  success is an argument for *not* fine-tuning facts in. Confirmed
  implicitly.

---

## 8. The honest summary, in one paragraph

The experiment confirmed the central thesis of draft_2 — a small local
model paired with structured retrieval and a calculator closes most of the
gap to a frontier model on lookup-and-apply tasks, at near-zero marginal
cost — and refined it in two directions that rhyme: **spend extra context
only when it pays.** Retrieve only when the model doesn't already know
(indiscriminate injection hurts as much as it helps, −10 pp on
known topics); deliberate only when the problem is deep (thinking mode is
+24 pp on multi-step but −22 pp on single-step, eval 003). The reasoning
ceiling acknowledged in draft_2 is real but must be disentangled from token
budget — `astro-028` looked like a wall until we doubled the budget;
`astro-031` is the wall that survived. That ceiling elevates explicit
*decomposition* from optional to default, and decomposition is also what
lets the thinking gate work (deep planning level thinks, single-step leaves
don't). Build retrieval now. Build the conditional-injection and structure-
gated-thinking gates alongside it. Build the graph only after a separate
experiment justifies it.

---

## Appendix: cross-reference to draft_2

| draft_2 section | draft_3 status |
|---|---|
| §1 vision | unchanged |
| §1.2 two failure modes | **confirmed with numbers** |
| §1.3 thinking-as-bug | **refined** (§4.6): a bug on single-step problems, a robustness gain on multi-step — gate it, don't ban it |
| §2 architecture overview | unchanged |
| §3 harness / ReAct | unchanged; tool-protocol robustness emphasized (§4.4) |
| §4 single agent | unchanged |
| §5 memory / concept card | **confirmed**; conditional injection added (§4.1) |
| §5 auto-inject vs recall | **elevated**: probe-gated injection is v1 |
| §6 tools | unchanged |
| §7 offline ingestion | **build it**; this is the next 3-month workstream |
| §7.2 online continual | unchanged (still forever-project) |
| §7.4 cross-domain transfer / graph | deferred pending its own experiment |
| §7.6 fine-tuning | confirmed (trace-distillation only, Phase 2) |
| §8 evaluation | this experiment IS Suite B; pattern is reusable for future domains |
| §9 open questions | most resolved; new ones added in §6 above |
| §10 build order | **updated** (see §5 above) — adds structure-gated thinking (step 9) |
