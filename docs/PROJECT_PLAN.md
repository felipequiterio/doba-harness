# Doba — Project Plan (Master Build Plan)

> The engineering master plan for building Doba: a local-first personal
> assistant + self-learning agent. Small model (~4B) + strong harness.
> TypeScript across frontend, API, and harness. Eval-driven at every phase.

**Owner:** Felipe · **Date:** 2026-06-03 · **Status:** planning (no product code yet)
**Reads with:** `archive/draft.md` (vision), `PROJECT.md` (vision + eval findings),
`archive/draft_3.md` (eval detail). This document is the *how-to-build*; those are
the *what* and the *why*.

---

## 0. How to read this document

- **§1–§3** fix scope and the non-negotiable principles. Read once, hold throughout.
- **§4** is the "decide before building" section — the stack, with rationale,
  leans, and explicitly-open items. This is what you asked to pin down first.
- **§5–§7** are the technical spine: repo layout, the harness-core design that
  unifies product and evals, and the data model.
- **§8** is the phased build (0→8), each phase gated by an eval where the
  component's value is uncertain.
- **§9** is the eval roadmap mapped onto the phases.
- **§10–§12** are risks, sequencing, and the Phase-0 decision checklist.

The spine of the whole plan: **the harness is the product *and* the
experiment.** Every performance-critical behaviour is a swappable policy that
the eval runner toggles as a condition and the product ships as a default.
You never build a behaviour you can't measure, and you never build the
measurement and the product twice.

---

## 1. Scope

### In scope
- A **single-user, local-first** assistant that runs on a consumer GPU (RTX
  3070-class, 8 GB) using a ~4B model via Ollama.
- **Two capabilities**: (1) daily-life assistant (lookups, reminders,
  computer/terminal/browser use, explanation-on-request); (2) self-learning
  agent that accumulates structured knowledge over time *for its own benefit*.
- A **harness** (agent loop, tools, memory, learning loop) that is the
  performance lever and the unit of experimentation.
- A **web frontend** (chat UI) and an **HTTP/streaming API** that wrap the harness.
- An **eval runner** that drives the same harness core to measure every policy.

### Out of scope (carried from draft 1 non-goals)
- **Tutoring**: no curriculum, no spaced repetition, no tracking of the *user's*
  knowledge. The agent explains from memory on request; it does not teach.
- **Frontier-grade creative work**: novel research, large original code
  generation, original design. Doba routes these out or declines.
- **Multi-user / multi-tenant**: this is *one* person's assistant.
- **Sub-second latency everywhere**: correctness > speed. Streaming hides latency.
- **Cloud dependence by default**: no data leaves the machine unless the user
  invokes an explicitly external tool (web search, optional frontier judge).

### Scope discipline note
The knowledge layer exists so the **agent** reasons better, not to produce
user-facing learning material. Keep the knowledge namespace's organization
(domains, graph) an internal asset, never a product surface.

---

## 2. Guiding principles (engineering non-negotiables)

These encode the hard lessons from evals 001–003. They are constraints, not
preferences.

1. **Eval-shaped harness.** Every performance-critical behaviour (knowledge
   injection, thinking mode, decomposition, retrieval depth) is a *config
   flag* on the harness core. The eval runner sets them per condition; the
   product sets defaults. One code path. (This is the bridge that makes
   "build vs eval" a false choice.)
2. **Ground truth in code, never the LLM.** Expected answers/labels are
   computed deterministically. The model is never the judge of its own
   correctness. (LLM-as-judge is allowed only for *method* assessment, and
   only a frontier model, clearly separated.)
3. **Count tool calls.** A tool invocation must be *structurally* distinct in
   the trace and counted by the harness — never a text marker the model can
   fake. If a "tool-using" run shows 0 real calls, that's a Potemkin trace.
   (~30% of early C3 runs hallucinated `RESULT:` lines. Never again.)
4. **Separate truncation from reasoning failure.** Always run with token
   headroom and *log token usage per call*. `astro-028` looked like a
   reasoning wall until the budget doubled. Rule out "ran out of tokens"
   before concluding "can't reason."
5. **Spend extra context only when it pays.** Retrieve only when the model
   doesn't already know (over-injection cost −10 pp). Deliberate (thinking)
   only when the problem is deep (+24 pp multi / −22 pp single). Both are
   *gates*, not always-on switches.
6. **Secrets in env vars only.** Never hardcode a key in config or code. (An
   OpenRouter key was once committed to a config field — it had to be
   rotated.) Config holds the *env var name*, the process holds the value.
7. **Local and private by default.** No network egress unless a tool the user
   invoked needs it. Make egress visible.
8. **Per-run observability.** Carry the eval logging discipline into the
   product: every model call logs tokens, tk/s, latency, tool-call count,
   stop reason, and (in debug) the trace. You cannot improve what you do not
   measure.

---

## 3. The core thesis, restated as a build constraint

Small model + strong harness beats big model + weak harness *for the right
scope*. The experiments confirmed the **application** half (a 4B with a
perfect card in context beats the raw frontier). The **production** half (can
the agent build and retrieve that card itself?) is the project's open risk
and the reason the Learning Loop (Phase 5) is hard-gated behind eval 004.

Every phase must leave a **working system** and be **justified by a real
limitation hit by the previous phase** — never speculative machinery.

---

## 4. Tech stack & decisions to lock before building

Each item: the decision, the rationale, the lean, and what stays open. Items
marked **[LOCK IN PHASE 0]** must be settled before code; items marked
**[DEFER — eval-gated]** are intentionally postponed until an eval justifies them.

### 4.1 Language & runtime — **DECIDED**
- **Decision:** TypeScript everywhere (frontend, API, harness, eval runner).
- **Runtime: Bun**, across all packages. Native TS execution (no build step in
  dev), fast startup, built-in test runner, built-in package manager, and
  Bun-first ergonomics for Hono and the eval runner. One runtime, no split.
- **Compatibility caveat (verify in the Phase-0 spike):** confirm **pi**, the
  Postgres driver, and pgvector access all work under Bun. Most of the stack is
  Bun-native or Node-compatible (`postgres.js`, Drizzle, Hono are all
  Bun-friendly); the only real unknown is pi. **Node LTS is the fallback for a
  single offending package only** if a hard incompatibility surfaces — not a
  general retreat.
- **Module system:** ESM throughout. `tsconfig` strict mode on; `bun` resolves TS directly.

### 4.2 Monorepo & tooling — **DECIDED**
- **Decision:** single repo, **Bun workspaces** (root `package.json`
  `workspaces` field) + **Turborepo** for task orchestration and caching.
- **Why:** the harness is a library shared by the API and the eval runner; Bun
  workspaces give first-class local linking with no publish step and fast
  installs. Turbo caches builds/tests so CI and local loops stay fast. (Turbo
  runs fine over Bun scripts.)
- **Lint/format:** ESLint (typescript-eslint) + Prettier, enforced in CI.
- **Validation:** **Zod** for *all* runtime schemas — tool I/O, concept cards,
  memory records, API request/response, eval condition configs. Zod schemas are
  the single source of truth; TS types are inferred from them (`z.infer`).

### 4.3 Agent framework (harness engine) — **spike-gated [LOCK IN PHASE 0]**
- **Candidate:** **pi** (the `pi-mono` framework the project identified). Use
  it for the ReAct loop, tool registration, streaming, and context hooks.
- **What we need from it** (the spike's acceptance criteria):
  - Async + streaming token output.
  - Clean tool registration with typed args + structured results.
  - **Pluggable context-management hooks** (we need custom memory-injection and
    compaction logic — non-negotiable).
  - A loop we can wrap so that **tool calls are counted by us** (principle #3).
  - Native or adaptable **function-calling** so tool use is structural, not textual.
  - Active maintenance; works on Ollama's native API.
- **Fallback:** if pi fails any acceptance criterion, the ReAct loop itself is
  ~150 lines — implement a **thin custom loop** over the model client. The loop
  is not where the difficulty lives; memory, policies, and the learning loop
  are. Do not let framework lock-in block the project.
- **Decision rule:** 1–2 day spike. Build a toy 3-tool ReAct loop on pi *and* a
  thin custom loop. Pick whichever cleanly satisfies the criteria above; bias
  to the one with better context-hook ergonomics.
- **Alternatives surveyed (for the spike):** LangGraph.js (heavier, graph-based),
  Mastra, VoltAgent, custom. Document the comparison in the spike's writeup.

### 4.4 Model serving — **[LOCK IN PHASE 0]**
- **Local model:** **Ollama**, native `/api/chat` endpoint (NOT the OpenAI-compat
  `/v1` shim — the shim silently dropped `num_ctx`, `num_gpu`, and `think:false`,
  which caused KV-cache spill to host RAM and broke the thinking toggle).
- **Model:** `qwen3.5:4b` (already chosen). Keep the client pluggable.
- **Thinking toggle:** the client must support per-call `think: true|false`
  (Qwen3 `<think>` mode). This is a first-class harness policy (principle #5).
- **GPU hygiene:** pass `num_ctx`, `num_gpu: 99`, `keep_alive` explicitly; log
  tokens/tk/s every call. (Lessons from the RAM/GPU 100% incident.)
- **Embeddings:** a local embedding model for vectors. **Lean:** an Ollama embed
  model (e.g. `nomic-embed-text`) so there's one serving stack, or
  `transformers.js` if we want in-process embeddings with no extra service.
  Decide alongside the first retrieval need (Phase 2).
- **Frontier (optional, eval-only):** OpenRouter (e.g. Gemini 2.5 Flash) for the
  REF condition and the method-judge. Key from `OPENROUTER_API_KEY` env var
  only. Never used in the default product path.

### 4.5 Databases — partly **[LOCK IN PHASE 0]**, graph **[DEFER — eval-gated]**
The data has three shapes: **relational** (structured personal facts, tool
logs, run records), **vector** (fuzzy recall over episodic memory + knowledge
cards), and **graph** (typed relations between concepts).

- **Relational + vector → one store: PostgreSQL + `pgvector`. [LOCK IN PHASE 0]**
  - **Why one store:** local-first, single user. Running Postgres with the
    `pgvector` extension covers relational *and* embeddings in one container —
    far simpler than adding a dedicated vector DB (Qdrant/Pinecone) early.
  - **Migrations:** **Drizzle ORM** (TS-native, typed, lightweight migrations)
    or Prisma. Lean **Drizzle** for closeness to SQL and pgvector friendliness.
- **Graph → start *inside* Postgres, defer Neo4j. [DEFER — eval-gated]**
  - The knowledge graph (concepts + typed directional relations) can live as a
    **`concepts` table + `relations` (edges) table** in Postgres, traversed with
    recursive CTEs, for the first knowledge phases. Draft 1 already leaned
    "SQLite-edges for personal; reassess when the graph grows."
  - **Introduce a dedicated graph DB only when an eval proves traversal value
    and the edges-table approach hits a wall.** The trigger is **eval 007**
    (cross-domain transfer / graph traversal) plus graph size > ~10k nodes or
    multi-hop traversal latency becoming a problem.
  - **When triggered, candidates:** **Neo4j** (mature, Cypher, heavy Docker
    footprint) vs **Kùzu** (embedded, no server, columnar, strong for analytic
    traversal — best fit for a local single-user app). **Lean Kùzu** for
    local-first; Neo4j only if we need its tooling/ecosystem.
  - **Rationale for deferring:** adding Neo4j now is operational weight for a
    capability (graph traversal payoff) that is *itself unproven* (PROJECT.md
    §5). Don't pay for the graph before eval 007 says the graph pays.

### 4.6 API layer — **[LOCK IN PHASE 0]**
- **Framework:** **Hono** — modern, tiny, first-class streaming, and **runs
  natively on Bun** (`Bun.serve`), which matches our runtime choice. Fastify was
  the alternative; Hono wins on Bun-native streaming ergonomics.
- **Streaming:** **Server-Sent Events (SSE)** for token streaming to the
  frontend (simpler than WebSockets for one-way model output; upgrade to WS only
  if we need bidirectional control like cancel/interject).
- **Responsibilities:** thin transport over the harness core — `POST /chat`
  (stream), memory endpoints, tool-permission prompts, run/trace inspection.
  **No business logic in the API**; it composes the harness.

### 4.7 Frontend — **DECIDED**
- **Stack:** **Vite + React + TypeScript + Tailwind + shadcn/ui**. A single-page
  chat UI talking to the local API over SSE.
- **shadcn/ui:** components are copied into the repo (Radix primitives +
  Tailwind), not installed as a black-box dependency — full control over styling
  and behaviour, which suits a bespoke single-user tool. Requires Tailwind
  (already in the stack) and React; composes cleanly with Vite. Run via Bun
  (`bunx shadcn@latest add …`).
  - *Open:* **Next.js** only if we later want SSR/file-routing — shadcn supports
    both, so this stays a no-cost future option. For now, **Vite SPA**: lighter,
    faster HMR, no server-rendering complexity for a local app.
- **Not performance-critical** (one user, local). Prioritize fast iteration and
  a clean streaming chat experience: message stream, tool-call visualization,
  memory/learning inspector panel (debug), thinking-trace toggle — all built
  from shadcn primitives (card, scroll-area, collapsible, badge, dialog).
- *Optional later:* a **CLI/TUI** front for terminal-native use (Jarvis is often
  CLI-shaped). Same API underneath. Defer to Phase 7.

### 4.8 Containerization & local infra — **[LOCK IN PHASE 0]**
- **`docker-compose.yml`** brings up: **Postgres (pgvector image)**, optionally
  **Ollama** (or run Ollama host-native for GPU access — often simpler on
  Windows/WSL), and later the graph DB if triggered.
- **App services** (api, frontend) run **host-native in dev** (fast HMR, easy
  debugging); containerize for a reproducible "run the whole thing" target.
- **GPU note (WSL/RTX 3070):** Ollama generally runs best host-native with GPU
  passthrough; keep it out of Docker unless GPU-in-container is verified. The
  compose file should make the Ollama endpoint configurable (host vs container).

### 4.9 Testing, CI, observability — **DECIDED**
- **Unit/integration tests:** **`bun test`** (built-in, fast, Jest-compatible
  API — no extra test-runner dependency, native TS). Vitest only if a package
  needs a feature `bun test` lacks.
- **CI:** GitHub Actions on `oven-sh/setup-bun` — typecheck, lint, test, build
  via Turbo; cache aware.
- **Logging:** **Pino** structured logs. The harness emits a per-run log
  (tokens, tk/s, latency, tool-call count, stop reason) — the same shape the
  Python eval logs used, carried into the product.
- **Tracing (later):** optional OpenTelemetry once there are multiple services.

### 4.10 What we are explicitly NOT adopting yet
- No dedicated vector DB (pgvector suffices at this scale).
- No Neo4j until eval 007 (§4.5).
- No message queue / job runner until the async Learning Loop needs one
  (Phase 5 — then a lightweight option like pg-boss on the existing Postgres).
- No Kubernetes, no cloud. Local-first.

---

## 5. Repository structure

Honors the frontend / api / harness split you described, as a Bun-workspaces monorepo.
`harness` is a library; `api` and `evals` depend on it.

```
doba/
├── package.json                # Bun workspace root (workspaces: [...])
├── bunfig.toml                 # Bun config (test, install)
├── turbo.json
├── tsconfig.base.json
├── docker-compose.yml
│
├── frontend/                   # Vite + React chat UI
│   ├── src/
│   │   ├── components/         # chat stream, tool-call view, memory inspector
│   │   │   └── ui/             # shadcn/ui components (copied in, owned by us)
│   │   ├── lib/                # API client (SSE), utils (cn)
│   │   └── main.tsx
│   ├── components.json         # shadcn config
│   └── package.json
│
├── api/                        # Hono server — thin transport over harness
│   ├── src/
│   │   ├── routes/             # /chat (stream), /memory, /tools, /runs
│   │   ├── server.ts
│   │   └── deps.ts             # wires a harness instance with product defaults
│   └── package.json
│
├── harness/                    # THE CORE LIBRARY (product + evals share this)
│   ├── src/
│   │   ├── agent/              # ReAct loop, turn control, stop conditions
│   │   ├── reasoner/           # model client (Ollama native), thinking gate
│   │   ├── tools/              # registry, tiering, core tools, tool protocol
│   │   ├── memory/             # personal + knowledge namespaces, interfaces
│   │   ├── learning/           # encounter→understand→interpret→integrate→forget
│   │   ├── policies/           # injection gate, thinking gate, decomposition
│   │   ├── config.ts           # HarnessConfig (the eval-shaped policy object)
│   │   └── index.ts
│   └── package.json
│
├── shared/                     # cross-package Zod schemas + inferred TS types
│   ├── src/
│   │   ├── schemas/            # ConceptCard, Relation, Memory*, Tool*, ChatTurn
│   │   └── index.ts
│   └── package.json
│
├── db/                         # Drizzle schema, migrations, repositories
│   ├── src/
│   │   ├── schema/             # postgres tables (+ pgvector, edges table)
│   │   ├── repositories/       # typed data access used by harness/memory
│   │   └── client.ts
│   ├── migrations/
│   └── package.json
│
├── evals/                      # eval runner (TS) — DRIVES harness/ via configs
│   ├── lib/                    # runner, scoring, analysis, logging
│   ├── datasets/
│   │   └── astrodynamics/      # v1.json (ported from Python), ground_truth.ts
│   ├── 004-self-built-cards/   # per-eval: README, config, results/
│   ├── 005-distractor-retrieval/
│   ├── 006-decomposition/
│   └── ...
│
├── infra/                      # compose overrides, seed scripts, db init
└── docs/                       # PROJECT.md, this file, archive/ (drafts 1–3)
```

Note: the Python `evals/001…003` are **archived** — their findings live in
`PROJECT.md`/`archive/draft_3.md`. The TS `evals/` is the going-forward runner. The
dataset JSON ports as-is; `ground_truth.py` becomes `ground_truth.ts`.

---

## 6. Architecture: the harness core and the eval-shaped policy interface

This is the technical heart of the plan — the design that makes the product
and the experiment the same code.

### 6.1 The `HarnessConfig` object
Every performance-critical behaviour is a field here. The eval runner sets it
per condition; the API sets product defaults. *Nothing* performance-relevant
lives outside this object.

```ts
// harness/src/config.ts  (schema in shared/, validated with Zod)
interface HarnessConfig {
  reasoner: {
    provider: 'ollama';
    model: string;                 // 'qwen3.5:4b'
    maxTokens: number;             // headroom — see principle #4
    temperature: number;
    thinking: 'off' | 'on' | 'gated-by-depth';   // principle #5 / eval 003
  };
  policies: {
    injection: 'off' | 'always' | 'gated';        // C2 / C3 / conditional (probe)
    decomposition: 'off' | 'on' | 'gated';        // eval 006
    retrieval: { topK: number; rerank: boolean }; // eval 005
  };
  tools: { core: string[]; toolboxes: string[] }; // tiering — eval 009
  memory: { personal: boolean; knowledge: boolean };
  limits: { maxToolTurns: number; maxTotalTurns: number };
}
```

The historical eval conditions map directly onto this:
- `C1` = injection off, no tools. `C2` = injection off, calculator on.
- `C3` = injection `always`. `C3` + probe = injection `gated`.
- `_T`/`_NT` = `thinking: 'on' | 'off'`.

So **the conditions we already ran are just `HarnessConfig` literals**, and
future evals are new literals over the *same* harness — exactly the
unification we want.

### 6.2 The agent loop (Pillar 1)
ReAct: `build_context → reason → (final? stop : act) → observe → loop`, with:
- **Explicit memory step first** ("should I recall, and from where?").
- **Explicit memory-write step last** (lessons-learned).
- **Counted tool calls** (principle #3) — the loop, not the model's text,
  decides a tool ran.
- **Turn caps** that force action/`ask_user` to prevent think-loops.
- **Decomposition hook**: when `policies.decomposition` fires, the loop plans a
  sub-step tree and walks leaves (each leaf a single-step call → no-think; the
  plan level → think). This is where eval 006 plugs in.

### 6.3 Memory interface (Pillar 3)
Two namespaces behind one interface: `recallPersonal`, `recallKnowledge`,
`traverse(node, relation)`, `write*`. Backed by `db/` (Postgres + pgvector +
edges table). The **conditional-injection gate** (probe: does the model already
know this topic? skip retrieval if yes) lives in `policies/` and is invoked by
the memory step. Eval 004/005 test the *production* and *routing* of this.

### 6.4 Learning loop (Pillar 5) — built last, gated first
`encounter → understand → interpret → integrate → forget`, async by default.
**Not built until eval 004 confirms a 4B can produce usable cards.** When
built, it writes to the same knowledge store the application path reads from.

### 6.5 How the two consumers attach
```
api/  ──┐                         product defaults (gated injection,
        ├──►  new Harness(config) ◄── gated thinking, decomposition on)
evals/ ─┘                         eval conditions (per-cell config literals)
```
Same constructor, same loop, different `HarnessConfig`. The eval runner adds
scoring + logging around it; the API adds transport + streaming.

---

## 7. Data model (initial schemas)

Defined as Zod in `shared/`, persisted via Drizzle in `db/`. Sketches:

```ts
// Knowledge namespace — the unit that produced the +30pp lift
ConceptCard = {
  id, name, domainTags: string[],
  kind: 'formula'|'procedure'|'event'|'fact'|'personal',
  body: string,                 // directly-applicable form (formula + var semantics)
  preconditions: string[],      // "applies when…"
  examples: string[],
  provenance: { source, citation, addedAt, addedBy: 'oracle'|'agent'|'user' },
  confidence: number,           // 0..1, decays if unreconfirmed
  mastery: number,              // composite of #examples, #relations, #uses
  embedding: vector,
}

Relation = {
  from: ConceptId, to: ConceptId,
  type: 'prerequisite_of'|'generalizes'|'specializes'|'contrasts_with'
      | 'example_of'|'applies_to'|'contradicts'|'analogous_to',
  confidence: number, provenance,
}

// Personal namespace
EpisodicMemory = { id, content, embedding, timestamp, ttl, lastAccessed }
SemanticFact   = { id, subject, predicate, object, embedding, confidence, provenance }
LessonLearned  = { id, taskKind, whatWorked, whatDidnt, embedding }

// Harness/runtime
ToolDef    = { name, description, argsSchema, resultSchema, tier, embedding }
RunRecord  = { id, config: HarnessConfig, turns, tokens, toolCalls, stopReason, latency }
```

`RunRecord` is shared by product and evals — the product's per-run log and the
eval's per-cell record are the same shape (principle #8).

---

## 8. Development phases

Each phase: **goal · components · stack touched · deliverable · eval gate ·
exit criteria.** A phase ships a working system and is justified by the prior
phase's limitation. Estimates are rough effort, not calendar.

### Phase 0 — Foundations & decisions (~1 week)
- **Goal:** lock §4 decisions; stand up the skeleton repo and infra.
- **Components:** Bun-workspaces/turbo monorepo; tsconfig/eslint/prettier; `docker-compose`
  (Postgres+pgvector, Ollama endpoint configurable); CI; **pi spike** (§4.3);
  **Ollama native client** with thinking toggle + token logging; port `v1.json`
  + `ground_truth.ts`; empty `harness/`, `api/`, `frontend/`, `shared/`, `db/`,
  `evals/` packages that build.
- **Deliverable:** `bun dev` brings up empty stack; `bun test` green; spike
  writeup picks the agent framework.
- **Exit:** every §4 **[LOCK IN PHASE 0]** item decided and recorded; framework
  chosen; model client streams tokens from qwen3.5:4b with `think` toggle proven.

### Phase 1 — Harness skeleton + MVP assistant (~1–2 weeks)
- **Goal:** an end-to-end local assistant for simple tasks, on the real core.
- **Components:** ReAct loop (`agent/`); tool registry + protocol with **counted
  calls** (`tools/`); ~5 core tools (`recall` stub, `web_search`, `make_plan`,
  `ask_user`, `note`, `calculator`); working memory + compaction; `HarnessConfig`
  with the **thinking gate** wired (cheap, evidence-backed); `api/` `/chat`
  stream; `frontend/` streaming chat UI.
- **Deliverable:** chat with Doba in the browser; it uses tools; traces visible.
- **Eval gate — *parity regression*:** re-run a TS port of the
  knowledge-application conditions (C1/C2/C3 × think/no-think) on the new
  harness; confirm it reproduces the 001–003 *direction* (oracle +30pp on
  absent; thinking +24/−22 by structure). This proves the TS harness is a
  faithful substrate before we build on it.
- **Exit:** parity reproduced; tool-call counting verified (no Potemkin traces).

### Phase 2 — Personal memory + tiered tools (~1–2 weeks)
- **Goal:** coherence across sessions; tool selection that scales.
- **Components:** Postgres schemas for **episodic (vector, TTL)** and **personal
  semantic (edges + vector)**; embedding model decided; `recall_personal` + the
  end-of-turn **memory-write** step; basic **forgetting** (TTL + consolidation);
  **tiered tools** (`load_toolbox`, `find_tool`) once tool count > ~10.
- **Deliverable:** Doba remembers prior sessions and user facts; loads toolboxes
  on demand.
- **Eval gate:** a slice of **eval 009** (daily-life) focused on
  memory-coherence + tool-selection accuracy as the toolset grows.
- **Exit:** measured tool-selection accuracy doesn't collapse past 10+ tools;
  recall demonstrably surfaces prior-session facts.

### Phase 3 — Knowledge application: retrieval + conditional injection (~2 weeks)
- **Goal:** the evidence-cleared knowledge layer (PROJECT steps 6–7) on real
  retrieval, not an oracle.
- **Components:** `ConceptCard` store (Postgres + pgvector); **flat retrieval**
  (`recall_knowledge`, top-k); the **knowledge probe** + **conditional-injection
  gate** (skip retrieval when the model already knows); retrieval routing.
- **Deliverable:** Doba answers knowledge-application questions by retrieving its
  own cards and injecting them *only when needed*.
- **Eval gate — the big two:**
  - **Eval 004 (self-built cards)** — *the project gate*. Cards the 4B builds
    from source vs the oracle. Does self-built recover the oracle's +30pp? Run
    here, on the real harness, *before* the full Learning Loop (Phase 5).
  - **Eval 005 (distractor retrieval)** — does success survive 1 correct + N
    wrong cards? Does a rerank/probe step recover it?
- **Exit:** **decision point.** If 004 shows self-built cards recover most of the
  lift → green-light Phase 5. If not → redesign ingestion before investing.

### Phase 4 — Harness caps + (reworked) decomposition (~1 week)
- **Goal:** make hard multi-step problems reliable. 008 showed the bottleneck
  is *not* a reasoning ceiling (there is none at adequate budget) but **harness
  robustness** — so this phase is primarily the cap fix.
- **Components (priority order):** (1) a **completion-token cap + forced-commit**
  on the thinking loop (kills the 30k+-token runaway), plus an adequate token
  budget and tool-turn cap; (2) *optionally* a **reworked** `decompose_problem`
  affordance — model emits a plan of **calculator expressions** the harness
  executes deterministically (008's first cut let no-think leaves do the
  arithmetic and they produced garbage).
- **Deliverable:** hard multi-step problems (`astro-028`/`031`) solve reliably;
  no runaways.
- **Eval gate:** budget/turn/cap success on `astro-028`/`031`; if decomposition
  is built, does deterministic-execution decomposition add reliability/cost
  value over the (now-capped) monolithic solve?
- **Exit:** no runaways; measured reliability on the hard multi-step cases.

### Phase 5 — Learning Loop: full ingestion + graph + forgetting (~3–4 weeks)
- **Goal:** the load-bearing pillar — the agent *builds* its world model.
  **Hard-gated by eval 004 passing in Phase 3.**
- **Components:** the async pipeline `encounter → understand → interpret →
  integrate → forget`; graph (still **edges-in-Postgres**) with typed relations;
  consolidation; **knowledge pruning** (low-usage + low-connectivity);
  contradiction handling; a job runner (pg-boss) for async learning.
- **Deliverable:** Doba reads material and its knowledge graph grows with
  provenance, confidence, and mastery — measurably improving later task success.
- **Eval gate — Eval 008 (forgetting):** does pruning improve retrieval/task
  success vs an unpruned, noisy graph?
- **Exit:** a populated graph that demonstrably lifts task success over Phase 3's
  flat store; forgetting shown net-positive.

### Phase 6 — Cross-domain transfer & graph traversal (~2–3 weeks)
- **Goal:** the differentiator — "learning one area helps another."
- **Components:** `traverse(node, relation)` over `analogous_to` edges;
  retrieval that follows the graph, not just vector match; **possible graph-DB
  migration** (§4.5 — Kùzu/Neo4j) *if and only if* this eval justifies it and
  edges-in-Postgres hits limits.
- **Eval gate — Eval 007 (transfer):** does graph traversal surface a card from
  domain A that solves a domain-B problem flat retrieval would miss? (Hardest to
  design — needs problems that genuinely require the analogy.)
- **Exit:** measured transfer lift; graph-DB decision made on evidence.

### Phase 7 — Jarvis capabilities (~ongoing)
- **Goal:** the daily-life half at full strength.
- **Components:** `computer_use.*`, `browser.*`, `terminal.*`, `files.*`
  toolboxes; permission prompts in the API/frontend; optional **CLI/TUI** front.
- **Eval gate — Eval 009 (daily-life suite):** ~30 representative tasks; success
  rate, turns-to-success, tool errors. Runs continuously as a regression set.
- **Exit:** reliable on the daily-life suite; safe tool-permission UX.

### Phase 8 — Online continual learning & self-directed study (~later)
- **Goal:** the forever-project — Doba fills its own knowledge gaps when idle.
- **Components:** bounded self-directed study (time/rate/scope caps) through the
  Phase-5 pipeline; continual curation; lessons-learned loop closure.
- **Eval gate:** longitudinal — does day-100 Doba outperform day-1 Doba on a
  held-out task set? (The ultimate test of the thesis.)
- **Exit:** measured capability growth over time without retraining.

---

## 9. Eval roadmap

Two tracks. The **research track** (Python, `evals/NNN-*`) answers the risky
gates *now*, ahead of the build — its findings transfer to the design (just
like 001–003 did). The **product track** (TS, on the real harness) re-runs the
load-bearing ones per phase. Eval numbers below are *creation order*, which
diverges from the phase-logical labels used earlier in this doc.

### 9.1 Research track (Python) — what each eval tested, found, and changed

> **Re-run pending (2026-11).** Two harness bugs were found and fixed after
> these evals ran: (1) with thinking ON, `conditions.run_condition` parsed and
> replayed the raw `<think>` text — CALCs the model only considered were
> executed and counted, a `FINAL_ANSWER` inside `<think>` was scored, and
> every turn's reasoning was re-fed as history (006's select step had the same
> leak, as did 005's SYMB build loop); (2) `max_tool_turns` counted CALC expressions, not round-trips, and
> the capping turn's CALCs were never answered. Treat the thinking-ON results
> (**002, 003, 007, 008**), 005's SymPy modes and 006's selection numbers as provisional until
> re-run; 003's "no-think runs out of tool turns on multi-step" may be partly
> bug (2). Runs now log `peak_context_tokens` to rule out silent `num_ctx`
> truncation, and `lib/stats.py` gives problem-level CIs — most deltas below
> rest on 1–10 problems at N=3.

> A recurring lesson worth stating up front: **the first headline number was
> usually a metric artifact, not a finding.** Every eval got a skeptical
> second pass (re-read the traces / re-score from stored data) that materially
> changed the conclusion. That discipline is now encoded as principles #2 and
> #8 — *trust no number you didn't verify against the underlying runs.*

#### `001` — astro failure modes: is the gap knowledge or reasoning? ✅
- **Tested:** the founding question — when a small model fails a problem, is it
  missing *knowledge* (mode-a, fixable by retrieval) or out of *reasoning
  depth* (mode-b, a model ceiling)? Five conditions on 36 astrodynamics
  problems: C1 (raw 4B), C2 (+calculator), C3 (+oracle card = the *upper bound*
  of retrieval), REF (raw frontier), REF_C3 (frontier+oracle). The oracle is a
  human-written perfect card standing in for retrieval.
- **Result:** on `knowledge=absent` problems, **C3 − C2 = +30pp** (oracle
  closes the gap) while **C2 − C1 = 0pp** (a calculator alone is useless if you
  don't know the formula). The tool+oracle 4B (**75%**) **beat the raw frontier
  (60%)**. And **REF_C3 − REF = +30pp** — the frontier benefits from the oracle
  *just as much*. On `knowledge=present`, **C3 − C2 = −10pp**.
- **Expected?** The +30pp and the harness-beats-raw-weights result *were* the
  thesis — confirmed with numbers. Two genuine surprises: (1) over-injection
  **hurting** on problems the model already knew; (2) the **frontier needing
  retrieval too** — which reframes the knowledge layer from "a crutch for being
  small" to "a breadth solution for every model size."
- **Shaped:** established the **oracle-as-upper-bound** methodology the whole
  research track runs on; promoted **conditional (probe-gated) injection** from
  a Phase-2 nicety to a **v1 requirement** (because −10pp); confirmed the
  **two-failure-mode axis** as the right frame. Left one open thread:
  `astro-028` failed 0/10 here and *looked* like a hard reasoning ceiling — 002
  later showed that was wrong.

#### `002` — does `<think>` mode help? (and the astro-028 correction) ✅
- **Tested:** whether Qwen3's `<think>` reasoning mode is worth its tokens. A
  2×2 — {thinking, no-think} × {oracle, no-oracle} — on the 5 hardest problems.
- **Result:** **thinking *hurt* the bare model** — `C1_T` 10% vs `C1_NT` 40%:
  with no tool round-trip to force a commit, the model gets stuck *inside* the
  `<think>` block and never emits an answer. With the oracle it looked mildly
  harmful too (`C3_NT` 70% vs `C3_T` 60%, N=2). Separately, **`astro-028`
  recovered to 2/2 once the token budget went 4096 → 8192** — proving 001's
  "0/10 ceiling" was **truncation, not reasoning**.
- **Expected?** No on both counts. Thinking is usually *sold* as strictly
  helpful; here it was a liability without a harness affordance. And the
  astro-028 budget discovery was an accident that overturned a headline
  conclusion from 001. *(Methodology scar: the thinking toggle silently didn't
  fire at first — a config `think:false` overrode the per-condition flag, giving
  identical token counts. Fixed by making `disable_thinking` a per-call lever.)*
- **Shaped:** made "**separate truncation from reasoning failure**" a hard rule
  (principle #4) and added token-headroom + per-call token logging. The "N=2,
  single-heavy, thinking looks bad" result was too shaky to act on directly —
  so it **motivated 003** to pin the thinking question down properly.

#### `003` — thinking *with* the oracle: when does it pay? ✅
- **Tested:** with the oracle always on (so it's a pure reasoning-process
  question, not a knowledge one), does thinking earn its tokens? `C3_NT` vs
  `C3_T`, on a balanced set (10 problems: 7 multi + 3 single, N=3 = 60 cells).
- **Result:** the effect **flips sign by problem structure**: **multi-step
  +24pp** (NT 43% → T 67%) but **single-step −22pp** (NT 89% → T 67%). The
  mechanism is visible in the traces — on multi, no-think *runs out of tool
  turns* (all 5 `max_tool_turns` failures were multi); thinking front-loads the
  plan and commits (**0/30 dropped answers**). On single, thinking *overthinks*
  a lookup it nails cheaply. Cost: ~1.9× tokens and ~1.75× latency per correct.
  At this run's 8k budget `astro-031` failed **0/3** and `astro-028` went 3/3 —
  so `astro-031` *looked* like the genuine ceiling. **(008 later overturned
  this: `astro-031` solves 3/3 at 16k tokens + a higher tool-turn cap — it was
  budget-bound too.)**
- **Expected?** The clean **sign-flip** was the real surprise — and it
  *reconciled* 002, whose "thinking hurts" was just a single-heavy sample. The
  honest read is the per-structure split, never the flat average.
- **Shaped:** added **structure-gated thinking** (build step 9) — default off,
  on for multi-step. The `astro-031`-is-the-ceiling reassignment made here was
  **wrong** (see 008); the durable lesson is the opposite — *raise the budget /
  turn cap before calling anything a ceiling*, and cap the thinking runaway.

#### `004` — self-built cards (THE GATE) ✅ PASSED
- **Tested:** can the 4B *build its own* card from a source passage and recover
  the oracle's lift — i.e. is the Learning Loop's ingest half real, or was
  +30pp an artifact of *human*-authored cards? (C2 / C3_oracle / C3_self.)
- **Result:** on `astro-016` (the only clean mode-a problem, C2 = **0/3**) the
  self-built card matched the oracle (**3/3**), was the correct formula,
  **leakage-free** (did not copy the passage's worked numbers), and on
  `astro-025` the model **synthesized** the closed form
  `tan β = e·sinθ/(1+e·cosθ)` that the passage never stated. 0/9 parse fails.
- **Expected?** Extracting a *stated* formula — yes. **Synthesizing** an
  unstated closed form — better than hoped. The headline "**200% lift
  recovered / self beats oracle**" was **not** real (small-N + the oracle's own
  −10pp distraction); the skeptical pass reduced it to the honest claim "self
  = oracle exactly where the card is load-bearing."
- **Shaped:** **green-lit Phase 5** — the Learning Loop is buildable as
  designed. Surfaced the build cost (~4k tokens / ~1 min per card, but it
  **amortizes to ~0** since cards are cached). Flagged `astro-025` as
  effectively mislabeled (the model already knows it) → the thin `absent` pool
  is an open follow-up.

#### `005` — symbolic derivation (does a CAS tool help?) ✅
- **Tested:** on problems whose passage gives the *ingredients but not the
  closed form*, does a local SymPy `SYMB:` tool make the 4B's derivations more
  correct? Three build modes (text / sympy / verify), **dual-scored** by SymPy
  formula-equivalence *and* the numeric answer.
- **Result:** after a scoring fix, **derivation is ~100% correct in-head** —
  across 24 parseable cards, **zero wrong formulas** in any mode. The CAS added
  **no accuracy** and introduced failure modes (a 32k-token runaway verify
  loop). `astro-025` confirmed **not luck** (100% formula-correct).
- **Expected?** No — the hypothesis (the "astro-025 was luck" worry) was that
  ungrounded derivation is risky and a CAS would help; both were disproved.
  The headline "**SymPy hurts, 89→56%**" was a **metric artifact**: the checker
  rejected mathematically-correct *bare-expression* cards (`atan(...)` with no
  `target =`). Fixed and re-scored from stored cards → all modes ~89%.
- **Shaped:** **do not build a model-facing CAS derivation tool** — no evidence
  it's needed at lookup-and-apply difficulty (scoped: harder *multi-step*
  derivations remain untested). **Keep `check_formula` as an offline card-QA
  validator** in Phase 5 — the verifier role earns its place even though the
  tool didn't. Reinforced: **multi-turn build loops need a hard token/turn cap
  + forced-card fallback.**

#### `006` — distractor retrieval (how good must retrieval be?) ✅
- **Tested:** every prior eval handed over the exact right card; this injects
  the correct card + N wrong ones. `make quick` = generic distractors at low
  load (D0–D4 + a select step). `make run` = the breaking-point sweep: high
  load (D8) + **near-miss** cards — subtly-wrong *same-quantity* formulas
  (Hill-sphere vs SOI, `acos` vs `asin`, circular- vs escape-speed).
- **Result:** **generic distractors are harmless even at 8–10 cards**
  (D8 = 100%). **Near-misses are a real but low-rate threat, and the channel is
  *selection under load*:** the select step picked the **Hill-sphere** card
  instead of SOI and computed it flawlessly → wrong answer (the parsed value
  matched the Hill-sphere formula *exactly* — a clean capture). Near-miss
  **wrongly selected 27%** of the time, but selecting it ≠ a wrong answer (the
  model recovered when the correct card was *also* picked, or when it knew the
  answer).
- **Expected?** Generic-noise robustness — expected, a bit stronger than feared.
  The near-miss failure was the *hypothesized* danger, **confirmed but rarer**
  than the −10pp over-injection result implied. Both headline deltas
  ("D8 > D0", "NM > D0") were **noise** — `astro-027` fails 2/5 with *zero*
  distractors (a chronically flaky cell) which deflated the D0 baseline.
- **Shaped:** retrieval precision matters against **same-quantity competitors,
  not card count** — top-k size is cheap; sibling-concept formulas are the
  risk. A **naive single-card select is unsafe** against near-misses (accuracy
  100%→80% once one is present). Two Phase-3 injection notes: **surface the
  card `when`/preconditions during selection** (the literal SOI-vs-Hill
  disambiguator), and **keep multiple candidates** rather than forcing one.

#### `007` — multi-card / multi-step building ✅
- **Tested:** can the 4B build a *correct multi-card set* (Hohmann / bi-elliptic
  Δv, 4–5 cards) from one method passage and **compose** it? Three conditions
  (C2 / C3_oracle / C3_self) read together to separate a *card-building* gap
  from the reasoning *ceiling*. Two scores: the numeric answer, and SymPy
  **coverage** of the two physics building blocks (circular speed + vis-viva).
  Solve runs **thinking ON** (multi-step, per 003).
- **Result:** **card-building holds at scale** — 100% building-block coverage,
  4–8 correct cards built per problem. And the eval's own assumption inverted:
  **C3_oracle (56%) came in *below* both C2 (67%) and C3_self (78%)**. On
  `astro-014` the hand cards scored 1/3 with both failures committing
  **Δv1 alone (110.9 m/s) — the model computed the first burn and stopped**,
  *despite* an explicit `total = burn1 + burn2` card; its own 6-card granular
  set solved it 3/3. `astro-028` (bi-elliptic) ground everything down
  (C2/oracle/self ≈ 1/3, 1/3, 2/3) — the hardest case here, though 008 later
  showed its failures are token/turn-cap runaways, not a reasoning wall.
- **Expected?** The *building works* result — yes (extends 004 to multi-card).
  The **oracle being the worst condition** was the surprise, and unlike 004/006
  it was *real, not a scoring artifact*: a terse composed-formula card with
  cryptic subscripts (`v_t1, v_c1, …`) **misled** the small model where its own
  granular decomposition didn't. (Caveat: N=3; "self > oracle" is driven mainly
  by the `astro-014` oracle collapse + small N — not a universal claim. Several
  near-misses were tolerance-boundary, e.g. an oracle run 1.4% off.)
- **Shaped:** for Phase 5, **card *clarity* beats *canonical-ness*** — let the
  model build its **own granular cards**; if we author canonical cards, author
  them **granularly (one formula, clear names)**, never as composed subscripted
  expressions. The `astro-014` failure (computed burn 1, *forgot* burn 2) is the
  direct mandate for **008**: decompose-then-solve can't drop a sub-step. And it
  **confirmed the runaway-token bug recurs** (a self solve hit 32k tokens) →
  the token/turn cap + forced-commit fix is now a blocker for thinking-on runs.

#### `008` — decomposition vs the ceiling ✅ (inconclusive on decomposition; killed the "ceiling")
- **Tested:** holding knowledge constant (oracle cards in both), does
  **decompose-then-solve** beat a **monolithic** solve on the hard multi-step
  transfers (`astro-028`, `astro-031`, `astro-014`)? Decompose = plan into
  single-quantity sub-steps (thinking) → execute each as a no-think calculator
  leaf → compose.
- **Result — two findings, one of them invalidating the headline:**
  - **Decomposition is *unproven*, not refuted.** It scored 11% vs mono's 67%
    (−56pp), but that's a **broken first-cut harness**, not a verdict: plans
    parsed only 44% of the time, and when they did, the no-think leaves computed
    **garbage** (a 10-step Hohmann plan produced a final value 5000× too big;
    one leaf returned `None`; errors cascaded). The *plan reasoning* was correct
    — the *execution* was broken.
  - **The big real finding: there is no genuine reasoning ceiling.** `astro-031`
    — the doc-enshrined 0/3 wall — went **mono 3/3** at 16k tokens + a 12 tool-
    turn cap. Combined with `astro-028`'s budget recovery, **every "mode-b
    ceiling" has been a truncation/turn-cap artifact.** And mono's own failures
    were all runaways/caps (a 36,773-token runaway → garbage; a `max_tool_turns`
    cap), not reasoning.
- **Expected?** No on both. We expected decomposition to *help* (it couldn't be
  fairly tested) and `astro-031` to be a *wall* (it isn't). This is the
  "first headline is an artifact" pattern again — this time the artifact was a
  harness I wrote, with a genuine surprise underneath.
- **Shaped:** **(1)** retired the "reasoning ceiling" claim across the docs —
  the dataset has none at adequate budget. **(2)** Made the **harness budget +
  tool-turn cap + thinking-runaway cap** the evidence-backed priority (it's what
  actually caps the solver). **(3)** Demoted decomposition from "default" to
  "unproven"; if pursued, the rework is *reliable plan emission* + *deterministic
  leaf execution* (have the model emit calculator expressions the harness
  evaluates, instead of letting no-think leaves do arithmetic).

#### `009` — decomposition, reworked ⏳ ON RADAR
- **Will test:** with a fixed harness (model emits a plan of calculator
  *expressions*; the harness executes them deterministically), does
  decomposition add reliability/cost value? Motivation is now reliability, not
  cracking a ceiling (there isn't one).

**Open follow-ups logged in the runs:** **(priority, in progress)** add a
completion-token cap + forced-commit to the multi-turn thinking loops — the
30k+-token runaway has recurred in **005, 007, and 008** (a 36,773-token mono
solve), and 008 showed it (plus the tool-turn cap) is the *actual* bottleneck
on "hard" problems, not reasoning; rework the decomposition harness (deterministic
expression execution) before re-testing it; author multi-step oracle/canonical
cards **granularly** (007 showed composed cards mislead); re-run 004 with more
genuinely-`absent` single-formula problems at N≥5 (thin pool); `astro-027` is too
flaky (~40% base failure) to be a clean probe — repair or retire it.

### 9.2 Product track (TS — on the real harness, per phase)

| Eval | Phase | Question | Metric |
|---|---|---|---|
| **Parity** | 1 | Does the TS harness reproduce 001–003? | direction match on oracle/thinking deltas |
| **daily-life (slice)** | 2 | Memory coherence + tool selection at scale | recall hit-rate, tool-selection accuracy |
| **self-built + distractor** | 3 | 004 + 006 re-run on real retrieval | % lift recovered; success vs #distractors |
| **harness caps** | 4 | Do budget + tool-turn + runaway caps lift "hard" multi-step? | success on `astro-028`/`031` at adequate budget |
| **decomposition (reworked)** | 4 | Does deterministic-execution decomposition add reliability/cost value? | success + cost vs monolithic |
| **forgetting** | 5 | Does pruning help? | retrieval precision, task success pruned vs not |
| **transfer / graph** | 6 | Does graph traversal surface analogies? | transfer-task success, graph vs flat |
| **daily-life (full)** | 7 | Daily-life reliability | success, turns, tool errors |
| **Longitudinal** | 8 | Does Doba get smarter over time? | day-100 vs day-1 on held-out tasks |

**Critical path:** 004 (gate ✓) → 005 (derivation solid ✓) → 006 (distractor ✓)
→ 007 (multi-card ✓) → 008 (no ceiling ✓; decomposition unproven) → harness-cap
fix → product re-runs per phase. The cumulative verdict: the Learning Loop's
**ingest is buildable and robust** — the 4B builds correct single cards (004),
derives reliably without a CAS (005), shrugs off generic retrieval noise but
must disambiguate near-misses (006), and builds correct *multi-card* sets it
composes better than terse hand cards (007). And 008 **dissolved the
"reasoning ceiling"** — there is none at adequate budget. So the open risk is
no longer "can it build knowledge or reason deeply?" (both yes) but **harness
robustness**: the thinking-loop runaway and the budget/tool-turn caps are what
actually decide the hard multi-step problems.

---

## 10. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **4B can't build usable cards** (Learning Loop unviable as designed) | Medium | High | Eval 004 *before* Phase 5; if it fails, fall back to user/assisted card authoring or a stronger ingestion model just for the offline pipeline |
| **pi framework doesn't fit** | Medium | Low | Phase-0 spike with a thin-custom-loop fallback; the loop is ~150 LOC |
| **Bun incompatibility** (pi or a driver) | Low–Medium | Low | Spike verifies pi + Postgres driver on Bun; Node fallback for the single offending package only — most of the stack (Hono, Drizzle, postgres.js) is Bun-native |
| **Graph traversal payoff doesn't materialize** | Medium | Medium | Defer graph DB (Kùzu/Neo4j) behind eval 007; edges-in-Postgres until proven |
| **GPU/RAM pressure on 8 GB** (recurrence of the KV-spill bug) | Medium | Medium | Native Ollama API, explicit `num_ctx`/`num_gpu`, token logging, headroom (principle #4) |
| **Over-injection / context bloat degrades small model** | High if naive | Medium | Conditional-injection gate is v1, not polish (principle #5); ruthless auto-inject budget |
| **Secret leakage** | Low | High | Env-var-only secrets (principle #6); pre-commit secret scan in CI |
| **Building uncertain pillars blind** | Medium | High | Hard eval gates: no Phase 5 before 004, no graph DB before 007 |
| **Scope creep into tutoring/multi-user** | Medium | Medium | §1 out-of-scope list is binding |

---

## 11. Sequencing summary

```
Phase 0  Foundations & decisions            ──► stack locked, spike done
Phase 1  Harness skeleton + MVP assistant   ──► [GATE: parity with 001–003]
Phase 2  Personal memory + tiered tools     ──► [GATE: daily-life slice]
Phase 3  Retrieval + conditional injection  ──► [GATE: 004 + 006 on real retrieval]
Phase 4  Harness caps + reworked decomp     ──► [GATE: hard multi-step reliable; decomposition re-test]
Phase 5  Learning Loop (full) + forgetting  ──► [GATE: pruning helps]   (only if 004 passed ✓)
Phase 6  Cross-domain transfer + graph DB?  ──► [GATE: graph beats flat ← graph-DB decision]
Phase 7  Jarvis capabilities                ──► [GATE: daily-life full, continuous]
Phase 8  Online continual + self-study      ──► [GATE: longitudinal]
```

Gate names match the product-track table in §9.2 (the eval *numbers* used in
earlier drafts of this summary predate the research evals' creation order).

Each `[GATE]` is a `HarnessConfig` experiment on the real core. Phases 6–9 of
draft 1's build order are intentionally *behind* their evals, not ahead.

---

## 12. Phase-0 decision checklist (lock these first)

- [x] Runtime: **Bun** across all packages; pi + Postgres-driver Bun-compat verified in spike (Node fallback only for a single offending package).
- [x] Monorepo: **Bun workspaces** + Turbo scaffolded; Zod adopted repo-wide.
- [ ] Agent framework: **pi spike vs thin-custom** — decision recorded with criteria (spike also confirms Bun compat).
- [ ] Model client: Ollama native `/api/chat`, thinking toggle + token logging proven.
- [ ] Embedding model: chosen (Ollama embed vs transformers.js).
- [ ] DB: Postgres + pgvector + Drizzle migrations stood up; graph = edges-in-Postgres.
- [x] Graph DB (Kùzu/Neo4j): **explicitly deferred** to eval 007 — recorded.
- [x] API: **Hono** + SSE streaming (Bun-native).
- [x] Frontend: **Vite + React + Tailwind + shadcn/ui**.
- [ ] Docker: compose for Postgres/Ollama; GPU placement (host vs container) verified on the RTX 3070/WSL box.
- [ ] CI: GitHub Actions (typecheck/lint/test/build) green.
- [ ] Secrets: env-var-only convention + CI secret scan in place.
- [ ] Dataset: `v1.json` ported; `ground_truth.ts` reproduces the Python expected values.
- [ ] Eval runner: minimal TS runner that constructs a `HarnessConfig` and scores against `ground_truth.ts`.

When every box is checked, Phase 1 starts on solid ground.

---

## Appendix — open questions to resolve as we build

These are intentionally unresolved; flagged so they're not forgotten.

- **Bun compatibility:** runtime is **decided (Bun)**; the only open item is verifying **pi** + the Postgres driver run cleanly under Bun in the Phase-0 spike. Fall back to Node for a single package only on a hard incompatibility.
- **Probe granularity:** per-topic vs per-formula knowledge probe (draft_3 §6). Affects how surgical conditional injection can be. Revisit in Phase 3.
- **Thinking gate signal:** binary single/multi vs continuous step-count (draft_3 §6). Tune in Phase 4 with eval 006 data.
- **Embedding-in-process vs service:** transformers.js (no extra service) vs Ollama embed (one stack). Decide at first retrieval need.
- **Graph DB trigger metrics:** exact node-count / latency thresholds that flip us from edges-in-Postgres to Kùzu/Neo4j. Define before Phase 6.
- **Async learning infra:** pg-boss vs a dedicated queue when the Learning Loop goes async. Decide in Phase 5.
- **Permission UX for computer use:** how much to prompt vs trust. Design in Phase 7.
```
