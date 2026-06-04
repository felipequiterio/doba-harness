# doba-harness

> A local-first personal assistant **and** self-learning agent. A small (~4B)
> model paired with a strong harness — built on the bet that *a small model
> with a strong harness can outperform a big model with a weak harness, for the
> right scope.* Knowledge doesn't have to live in the model's weights if it
> lives in a memory the agent can search, traverse, and reason over.

This repository holds the project's **design** and the **evaluation harness**
that de-risks it. The guiding rule: every risky design bet is settled by an
experiment *before* it is built. The harness is therefore both the thing being
designed and the instrument that measures it.

**Deep docs:** [`docs/PROJECT.md`](docs/PROJECT.md) (vision + what the evals
showed) · [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) (the master build
plan). This README synthesizes both.

---

## 1. What Doba is

Doba does two things:

1. **A daily-life assistant** — reminders, lookups, computer/terminal/browser
   use, explanations on request. Local and private by default.
2. **A self-learning agent** — it accumulates structured understanding across
   all areas of knowledge so it becomes broadly more capable over time. The
   agent learns *for itself*; the user benefits because a better-read agent
   reasons better about everything.

"Learning" has a load-bearing definition: **understanding and interpreting
relevant information while forgetting and discarding the useless.** The memory
system is a *learning pipeline*, not a storage layer; forgetting is first-class;
and knowledge in one area should help reasoning in others (transfer).

It runs on a consumer GPU (RTX 3070-class, 8 GB) using a ~4B model via Ollama,
and compensates for the model's limited base knowledge by being exceptionally
good at **using tools, building memory, and processing knowledge into genuine
understanding.**

### In scope
- Single-user, local-first, consumer-GPU.
- A harness (agent loop, tools, memory, learning loop) as the performance lever.
- A web chat frontend + a streaming API that wrap the harness.
- An eval runner that drives the *same* harness core to measure every policy.

### Out of scope
- **Tutoring** — no curriculum, no spaced repetition, no tracking of the
  *user's* knowledge. The agent explains from memory; it doesn't teach.
- **Frontier-grade creative work** — novel research, large original code gen.
- **Multi-user / multi-tenant.** This is one person's assistant.
- **Cloud dependence by default** — nothing leaves the machine unless a tool
  the user invoked needs it.

---

## 2. Why a small model can carry this

Small models fail at long reasoning chains and rare-knowledge recall, but
they're *good enough* at the calls the harness can shape for them: following a
clear instruction with the right context already loaded, choosing among a few
well-defined options, summarizing/transforming text in context, and extracting
structured information from unstructured text. The harness's job is to **shape
every model call into the kind small models are good at** — short, focused, with
the relevant memory and tools pre-selected — and to use the model's
"extract & interpret" strength as the engine that grows memory into
understanding.

---

## 3. The core principle: the harness is the product *and* the experiment

Every performance-critical behaviour (knowledge injection, thinking mode,
decomposition, retrieval depth) is a **swappable policy** on the harness core.
The eval runner sets these per condition; the product ships them as defaults —
one code path. You never build a behaviour you can't measure, and you never
build the measurement and the product twice.

A handful of non-negotiable principles, learned the hard way in the evals:

| Principle | Why |
|---|---|
| **Ground truth in code, never the LLM** | the rule the experiment is measured against must be deterministic |
| **Count tool calls** | a tool use must be *structural*, not a text marker the model can fake (~30% of early runs faked it) |
| **Separate truncation from reasoning failure** | a "ceiling" turned out to be the token budget; always rule out running-out-of-tokens first |
| **Spend extra context only when it pays** | retrieve only when the model doesn't already know; deliberate only when the problem is deep |
| **Secrets in env vars only** | config holds the env-var *name*, never the key |
| **Per-run observability** | every call logs tokens, tk/s, latency, tool-call count, stop reason |

And a meta-lesson the evals kept teaching: **the first headline number was
usually a metric artifact, not a finding.** Every result got a skeptical second
pass (re-read traces, re-score from stored data) that changed the conclusion.

---

## 4. Architecture

One agent. Three subsystems — **Memory** (two namespaces: personal and
knowledge), **Tooling** (tiered), **Reasoner** (the model) — plus a fourth
process, the **Learning Loop**, turning encountered information into structured
understanding.

```
User query
   │
   ▼
Single agent (ReAct loop, optional planning + decomposition)
   ├── Memory ── Personal NS (working / episodic / semantic)
   │          └─ Knowledge NS (concepts + typed relations, graph + vector)
   ├── Tooling (core always-loaded; toolboxes lazy-loaded; discovery fallback)
   ├── Reasoner (4B; thinking mode gated on problem structure)
   └── Learning Loop (encounter → understand → interpret → integrate → forget)
   │
   ▼
Response / action
```

**The five pillars and where the evidence stands:**

| Pillar | Design | Eval status |
|---|---|---|
| **1. Harness** (ReAct + planning) | short, focused calls; decompose deep tasks | ✅ confirmed; decomposition promoted to default |
| **2. Single agent + sub-tasks** | one persistent loop; isolated ephemeral sub-tasks | not yet tested |
| **3. Memory** (2 namespaces, typed cards) | recall is conditional/agent-decided; tiny auto-inject | ✅ typed cards + conditional injection confirmed; real retrieval routing partly tested (006) |
| **4. Tools** (tiered) | <10 core; toolboxes on demand | not tested at scale; tool-call counting is a hard rule |
| **5. Learning Loop** (the load-bearing one) | the agent *builds* its world model | ✅ **ingest validated** — single cards (004), derivation (005), multi-card sets (007); full online pipeline + graph + forgetting still to build |

The validated surface now covers both knowledge-**application** and the
**production** of cards (single and multi-card). The open surface — the *online*
Learning Loop (graph, forgetting), retrieval at scale, cross-domain transfer,
and the daily-life half — is what the remaining evals and build phases address.

---

## 5. What the experiments have shown

The first experiments used an **oracle** — a hand-written perfect knowledge card
injected into context — as a proxy for the *upper bound* of what retrieval could
ever achieve, on a set of astrodynamics problems with code-computed ground
truth. Findings (full per-eval detail in
[`PROJECT_PLAN.md §9`](docs/PROJECT_PLAN.md)):

| Eval | Question | Headline result |
|---|---|---|
| **001** | Is failure missing *knowledge* or *reasoning depth*? | Oracle adds **+30pp** where the model doesn't know; a tool+oracle 4B **beats the raw frontier**; over-injecting known topics **hurts −10pp** |
| **002** | Does `<think>` mode help? | It **hurts** the bare model (gets stuck without a tool round-trip); and a suspected reasoning "ceiling" turned out to be a **token-budget** limit |
| **003** | Does thinking pay *with* the oracle? | The effect **flips by structure: +24pp multi-step / −22pp single-step**; one problem is a genuine 0/3 reasoning ceiling |
| **004** *(the gate)* | Can the 4B build its **own** cards? | **Yes** — recovers the oracle lift, leakage-free, and even *synthesizes* a closed form the passage never stated → Learning Loop is viable |
| **005** | Does a symbolic (CAS) tool help derivation? | Derivation is **~100% correct in-head**; the CAS isn't needed at this difficulty (kept as an offline validator) |
| **006** | How good must retrieval be? | Generic distractor noise is **harmless**; **near-miss** (sibling-concept) cards are the real risk, and they bite via the *selection* step |
| **007** | Can it build a **multi-card** set and compose it? | **Yes** — builds correct 4–8-card sets that it composes *better* than terse hand cards; **card clarity beats canonical-ness** (a composed "official" formula misled the model where its own granular cards didn't) |

Two refinements to the original thesis fell out of this: **conditional
injection** (retrieve only when needed) and **conditional thinking** (deliberate
only on deep problems) — both "decide whether to spend the extra context, don't
spend it by default."

---

## 6. The build plan (eval-gated phases)

Each phase ships a working system and is justified by a real limitation the
previous phase hit. Behaviours whose value is uncertain stay behind an eval.

```
Phase 0  Foundations & decisions        (stack locked, framework spike)
Phase 1  Harness skeleton + MVP assistant         [gate: parity with 001–003]
Phase 2  Personal memory + tiered tools           [gate: daily-life slice]
Phase 3  Retrieval + conditional injection        [gate: 004 + 006 on real retrieval]
Phase 4  Decomposition + reasoning-depth handling [gate: crack the ceiling]
Phase 5  Learning Loop (full) + forgetting        [gate: pruning helps]   (only if 004 passed ✓)
Phase 6  Cross-domain transfer + graph traversal  [gate: graph beats flat → graph-DB decision]
Phase 7  Jarvis capabilities (computer/terminal/browser)
Phase 8  Online continual learning + self-directed study
```

The gate questions are answered ahead of time by the Python **research evals**
(this repo) and re-run on the TypeScript harness per phase.

---

## 7. Tech stack (planned product)

The product will be a TypeScript monorepo (`frontend/`, `api/`, `harness/`),
where `harness` is a shared library the API and the eval runner both drive.

| Layer | Choice | Rationale |
|---|---|---|
| Language / runtime | **TypeScript on Bun** | one runtime, native TS, fast |
| Monorepo | Bun workspaces + Turborepo | shared harness library |
| Agent framework | **pi** (spike-gated; thin-custom fallback) | the ReAct loop is ~150 LOC if needed |
| Model serving | **Ollama** native `/api/chat` (`qwen3.5:4b`) | thinking toggle + GPU control the OpenAI shim drops |
| Database | **Postgres + pgvector** (one store) | relational + vector, local-first |
| Graph | edges-in-Postgres now; **Kùzu/Neo4j deferred** | gated behind the transfer eval |
| API | **Hono** + SSE streaming | Bun-native |
| Frontend | **Vite + React + Tailwind + shadcn/ui** | local SPA, owned components |
| Validation / ORM / tests | Zod · Drizzle · `bun test` | |

Everything performance-relevant lives in one **`HarnessConfig`** policy object —
the historical eval conditions (oracle on/off, think on/off) are literally
config values, which is what makes the product and the experiment the same code.

---

## 8. Repository layout

```
docs/      design documents
  PROJECT.md        vision + eval findings (start here for the "why")
  PROJECT_PLAN.md   the master build plan (stack, phases, eval roadmap)
  draft.md, draft_3.md   earlier design iterations (history)

evals/     the Python evaluation harness
  lib/         shared core — agent loop, model client (Ollama native),
               calculator, card builder, symbolic tools, scoring, logging
  datasets/    problem sets + code-computed ground truth
  NNN-*/       individual experiments (each: README, config, results/)
```

> The TypeScript product (`frontend/`, `api/`, `harness/`) is planned, not yet
> built — see `PROJECT_PLAN.md`.

---

## 9. Running the evals

Requires [uv](https://docs.astral.sh/uv/) and a local
[Ollama](https://ollama.com/) serving `qwen3.5:4b`.

```bash
cd evals
make help                 # shared targets (test, gt-check, list-evals, …)

cd 004-self-built-cards
make smoke                # stub wiring check — no model, <1s
make quick                # a real run on the local 4B
```

Every run writes a timestamped `results/<timestamp>/` with per-cell logs, a
JSONL record, and a `summary.md`. Result dirs are gitignored (regenerable).

---

## 10. Status

Pre-product. The design and the research harness exist; the central bet (the
Learning Loop can build its own knowledge) has **passed its gate**. Next up on
the research track: multi-card building and decomposition. The TypeScript
product is planned and unstarted.

### Source material note
The evals reference Hintz, *Orbital Mechanics and Astrodynamics* for the
astrodynamics problem set. The textbook itself is **not** included (copyright);
only the code-computed ground truth and short transcribed formula passages the
experiments need are kept.
