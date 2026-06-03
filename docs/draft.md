# Doba — Design Document

> Local-first personal assistant *and* self-learning agent. Small model + strong harness. Jarvis for daily life, with a memory system designed to genuinely *learn*.

---

## 1. Vision

**Doba is a private, local AI that does two things:**

1. **Acts as a daily-life assistant** — reminders, lookups, computer use, terminal, browser, explanations on request.
2. **Learns continuously** — accumulates structured understanding across all areas of knowledge (math, physics, biology, history, art, music, …) so that, over time, it becomes broadly more capable. Not a tutor. The agent learns *for itself*; the user benefits because a better-read agent reasons better about everything.

It runs on a consumer GPU using small (~4B) models, and compensates for the model's limited base knowledge by being exceptionally good at **using tools, building memory, and processing knowledge into genuine understanding**.

Core thesis: a small model with a strong harness can outperform a big model with a weak harness — *for the right scope*. Knowledge does not need to live in the model's weights if it lives in a memory the agent can search, traverse, and reason over. Like a human: we don't memorize the encyclopedia, we read, understand, link, and remember what matters — and we forget what doesn't.

### What "learning" means here
Per the user's definition: **knowledge is a process of understanding and interpreting relevant information while forgetting and discarding useless information.** This is load-bearing for the design:
- The memory system is a *learning pipeline*, not a storage layer. Writing happens with understanding and interpretation, not just embedding-and-dump.
- **Forgetting is first-class**, not an afterthought. Pruning the irrelevant is part of *what makes the rest valuable*.
- Knowledge in one area should help reasoning in unrelated areas (transfer). That's the payoff: by learning graph theory, the agent gets better at routing problems, social analysis, dependency thinking — not just graph-theory questions.

### Goals
- Local and private (no data leaves the machine by default).
- Reliable for daily-life tasks: scheduling, reminders, info lookup, computer/terminal/browser operations.
- **The agent gets smarter over time** by accumulating structured, curated knowledge — without retraining the model.
- Coherent over long timeframes — the agent remembers what it learned and why.

### Non-goals
- Frontier-model creative tasks: complex code generation, original design, novel research.
- **Tutoring as a feature** — no curriculum builder, no spaced-repetition for the user, no mastery tracking of the user's knowledge. The agent can explain things from memory on request, but teaching is not what we're building.
- Multi-user / multi-tenant. This is *my* assistant.
- Real-time / sub-second latency for everything. Correctness > speed.

### Why this works
Small models fail at long reasoning chains and rare-knowledge recall, but they're often *good enough* at:
- Following a clear instruction with the right context already loaded.
- Choosing among 3–5 well-defined options.
- Summarizing, interpreting, or transforming text that's already in context.
- Extracting structured information from unstructured text (entities, relations, claims).

The harness's job is to **shape every model call into the kind of call small models are good at** — short, focused, with the relevant memory and tools pre-selected — and to use the model's "extract & interpret" strength as the engine that grows memory into understanding.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                         User Query                           │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│       Single Agent (ReAct loop, optional planning)           │
│                                                              │
│   ┌───────────────────────┐  ┌──────────┐  ┌───────────┐     │
│   │   Memory              │  │  Tooling │  │ Reasoner  │     │
│   │  ┌─────────────────┐  │  │ (tiered) │◄─┤  (model)  │     │
│   │  │ Personal NS     │  │◄─┤          │  │           │     │
│   │  │  - working      │  │  └──────────┘  └───────────┘     │
│   │  │  - episodic     │  │         ▲                        │
│   │  │  - semantic     │  │         │                        │
│   │  ├─────────────────┤  │  ┌──────┴────────────────┐       │
│   │  │ Knowledge NS    │  │◄─┤  Learning Loop        │       │
│   │  │  - by domain    │  │  │  encounter → understand│      │
│   │  │  - graph+vector │  │  │  → interpret → integrate│     │
│   │  └─────────────────┘  │  │  → forget              │      │
│   └───────────────────────┘  └────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
                               ▼
                       Response / Action
```

One agent. Three subsystems: **Memory** (two namespaces — personal and knowledge), **Tooling** (tiered access), **Reasoner** (the model). A fourth process, the **Learning Loop**, runs alongside — turning encountered information into structured understanding inside the knowledge namespace.

---

## 3. Pillar 1: Harness

### Decision: ReAct loop with optional explicit planning

The agent runs a ReAct-style loop (Thought → Action → Observation → repeat) as the default. When it detects a task that needs planning, it calls a `make_plan` *tool* that produces a checklist, and the loop then executes against the checklist.

### Why
- **ReAct fits small models** because each turn is short and focused — the model only has to decide "what next?" given current context, not "solve the whole thing."
- **Pure Plan-and-Execute is brittle** with small models: the plan is generated upfront with limited info, and small models can't replan well when reality diverges.
- **Hybrid wins**: planning becomes a *capability the agent invokes when it judges the task needs it*, not a hardcoded phase. This matches how humans work — most tasks don't need a plan; some do.

### Alternatives considered
- **Pure Plan-and-Execute**: rejected for the brittleness reason above.
- **Reflexion-style self-critique loops**: interesting for self-improvement but adds latency and small models often produce shallow self-critiques. Defer.
- **Tree-of-Thoughts / search**: too expensive for an always-on local assistant. Defer.

### Framework choice
- `pi-mono` is the candidate the user identified. Evaluate it against:
  - Async / streaming support
  - Tool registration ergonomics
  - Pluggable context-management hooks (we'll need custom logic for memory injection)
  - Active maintenance
- Worth a side-by-side spike against alternatives (LangGraph, smolagents, custom) before committing. **Action item:** 1-day evaluation.

### Loop sketch
```
loop:
  context  = build_context(query, recent_memory, pinned_facts)
  thought  = model.reason(context)
  if thought.is_final_answer: return thought.answer
  action   = thought.action          // tool call OR sub-task
  result   = execute(action)
  context += (thought, action, result)
  if context.too_long: compact(context)
```

### Case studies to mine
The brainstorm flagged Claude Code, Codex, OpenClaw, and Hermes Agent. Specifically extract:
- How they manage long-running context (compaction strategies).
- How they handle tool errors and retries.
- How they signal "I'm done" vs "I need more info."

---

## 4. Pillar 2: Agent Architecture

### Decision: Single agent with ephemeral sub-tasks

One persistent agent owns the conversation, the memory, and the high-level loop. For narrow, well-defined operations (e.g. "search the web for X and summarize"), it can spawn an **ephemeral sub-task** with isolated context. The sub-task returns a single condensed observation; the main agent never sees the sub-task's intermediate steps.

### Why
- **Single agent keeps memory and context coherent** — there's one source of truth for "what is happening right now."
- **Multi-agent coordination is the hardest part** of multi-agent systems, and small models are particularly bad at it (delegating, negotiating, merging results). Avoid until proven necessary.
- **Sub-tasks capture most of the multi-agent benefit** (context isolation, focused tool sets) without the coordination overhead.

### Alternatives considered
- **Pure single agent (no sub-tasks)**: simpler, but a long web-search trace pollutes the main agent's context fast.
- **Full multi-agent with specialized roles**: rejected for now. Revisit if a specific problem (e.g. parallel research) clearly demands it.

### How a query flows
1. Receive query.
2. **Memory check** — recall relevant facts, past conversations, lessons learned.
3. **Sufficiency check** — can I answer from memory + reasoning alone?
4. If not, **decide next action**: web search? tool call? clarifying question? plan?
5. Execute, observe, loop back to step 3.
6. Answer + **write any new lessons to memory**.

This is ReAct with two opinionated additions: an explicit memory step at the start, and an explicit memory-write step at the end.

### Open sub-questions
- How does the agent decide a task is complex enough to warrant `make_plan`? Heuristic (token count, multi-step verbs) or self-judgment? **Lean: self-judgment via prompt, with a fallback heuristic.**
- When sub-tasks fail, does the main agent retry or surface the failure? **Lean: surface, let the main agent decide.**

---

## 5. Pillar 3: Memory

### Decision: Four layers, two namespaces, mostly tool-accessed

The agent has **two distinct memory namespaces**, each with its own organization:

**Personal namespace** — about the user's life and the agent's history.
**Knowledge namespace** — about the world, organized by areas of knowledge.

Within each namespace there's a layered structure:

| Layer | Namespace | Contents | Storage | Access |
|---|---|---|---|---|
| **Working** | shared | Current conversation, scratchpad, active plan | In-context | Always present |
| **Episodic** | personal | Past conversations, events, "what happened on Tuesday" | Vector store, timestamped | Tool-recall |
| **Personal semantic** | personal | Stable facts about user, entities, preferences, lessons learned from past tasks | Graph + vector | Tool-recall + thin auto-inject |
| **Knowledge** | knowledge | The agent's growing world model: concepts, definitions, relationships, organized by domain | Graph + vector | Tool-recall |

### Why two namespaces
- **Personal memory is about *me* and the agent's past with me.** Organized by entity (people, projects, places).
- **Knowledge memory is the agent's world model.** Organized by area of knowledge (math, physics, biology, history, art, music, …) with hierarchical sub-domains. This is the user's intentional design choice — the agent learns broadly, and discipline-shaped organization helps it transfer ideas across topics.
- Keeping them separate prevents the contamination problem: when retrieving for a daily-life task, you don't want noise from physics; when reasoning about a concept, you don't want random episodic memories.
- Cross-namespace links are allowed where natural (a user note about a paper they read links to that paper's knowledge node).

### Why "areas of knowledge" as the top-level organization
The user's framing: by accumulating structured understanding across domains, the agent's *general* reasoning gets better — graph theory helps with social analysis, statistical thinking helps with debugging, music theory helps with pattern recognition. Domain-shaped organization makes this transfer easier because:
- Related concepts cluster (so retrieval surfaces analogies).
- The shape of the graph mirrors the shape of how humans organize knowledge — easier to reason about and easier to introspect.
- It naturally supports finding *gaps* in the agent's understanding (which sub-areas are underpopulated?).

It is *not* a tutoring database. The hierarchy is for the agent's benefit, not for generating user-facing curricula.

### Working memory
Just the context window. The harness manages compaction:
- Hard token budget per call.
- Older turns get summarized before they're dropped.
- Pinned facts and the active plan are never compacted.

### Episodic memory (personal)
Answers "have I seen this before?" — vectors are right because queries are fuzzy ("that thing about taxes").
- Timestamped.
- TTL with extension on access (frequently-recalled memories stick).
- Periodic distillation: groups of related episodic memories get summarized into a personal-semantic note, and the originals are pruned.

### Personal semantic memory
Stable, structured facts about the user's world.
- Graph for structured recall ("what projects does Felipe own?").
- Vector for fuzzy recall ("anything related to refactoring?").
- Includes **lessons-learned** entries written after non-trivial tasks: what worked, what didn't, what to do differently. Retrieved when similar tasks start.

### Knowledge memory (the world model)
The new heavy-lift layer. Stores the agent's accumulated understanding:
- **Concept nodes** — the unit of knowledge. Definition, summary, examples, source provenance.
- **Relations** — typed, directional: `prerequisite_of`, `generalizes`, `specializes`, `contrasts_with`, `example_of`, `applies_to`, `contradicts`.
- **Domain tags** — each concept tagged with one or more areas of knowledge (a concept can live in multiple — entropy is in physics *and* information theory, and that link is itself valuable).
- **Confidence and provenance** per node — who/what said this, how reliable, when last verified.
- **Mastery / depth** — how well-understood this concept is by the agent (light skim vs deep understanding with worked examples). This is what tells the agent which areas are weak and worth deepening.

Concepts are *built*, not just stored — see Pillar 5 (Learning Loop).

### Auto-inject vs explicit recall
- **Auto-inject only a tiny "pinned facts" slice**: user identity, hard preferences, current high-priority projects. Too important to risk forgetting to recall.
- **Everything else is tool-accessed** via `recall_personal(query)`, `recall_knowledge(query)`, `traverse(node, relation)`. This keeps context lean — critical for small models.
- The first step of every query is "should I recall something, and from which namespace?" The agent decides.

### Why not auto-inject everything (RAG-style)
- Bloats context, which degrades small models more than large ones.
- Pollutes reasoning with semi-relevant noise.
- Makes the agent's reasoning depend on retrieval quality even when retrieval wasn't needed.

### Forgetting (first-class)
The user's definition of knowledge requires this: pruning the useless is *what makes the rest valuable*. Mechanisms:
- **Usage-based pruning** in episodic memory: TTL with extension on access.
- **Confidence decay** for inferred facts that haven't been reconfirmed.
- **Periodic consolidation** — episodic clusters become semantic notes; the originals are dropped.
- **Knowledge pruning** — concepts with low usage *and* low connectivity in the graph are candidates for removal. A concept that's never linked to and never recalled isn't part of understanding, just clutter.
- **Contradiction resolution** — when new evidence contradicts a stored claim, the agent reconciles (update, downgrade confidence, or mark disputed) rather than letting both coexist silently.
- **Explicit forget tool** the user can invoke.

### Open sub-questions
- Graph backend: Neo4j? SQLite + manual edges? Embedded Kùzu? **Lean: SQLite + edges table for personal; reassess for knowledge once it grows past ~10k nodes (Kùzu is a strong candidate for the bigger graph).**
- Embedding model: local sentence-transformer. Decide alongside main model.
- Knowledge graph schema: start with `Concept` (typed, domain-tagged) and `Relation` (typed, directional, with confidence). Refine empirically as the graph grows.
- Top-level domain taxonomy: lift from an existing source (e.g. Wikipedia's top-level categories, Library of Congress subject headings) rather than invent. **Action item:** pick a starting taxonomy.

---

## 6. Pillar 4: Tools

### Decision: Tiered tool access

| Tier | Examples | Loaded |
|---|---|---|
| **Core** (always in context) | `recall`, `web_search`, `make_plan`, `ask_user`, `note` | Always |
| **Toolboxes** (lazy-loaded) | `computer_use.*`, `browser.*`, `terminal.*`, `files.*` | Loaded on demand via `load_toolbox(name)` |
| **Discovery** (as needed) | `find_tool(query)` returns vector-matched candidates | Called when agent isn't sure what's available |

Aim for **<10 core tools** in default context. A toolbox loads ~5–15 related tools when activated.

### Why
- **All-tools-in-context fails at scale** — for small models, even 20 tools in context noticeably degrades selection quality.
- **Toolboxes preserve focus**: when the agent decides "this is a browser task," it loads only browser tools and reasons within that vocabulary.
- **Vector search for tools** is a fallback for ambiguous cases, not the primary mechanism — it adds latency and can mislead.

### Alternatives considered
- **All tools in context**: rejected at scale.
- **Multi-agent with tool ownership**: rejected (see Pillar 2).
- **Pure vector search over all tools**: too unreliable as the only mechanism — the agent should know its core toolkit by heart.

### Tool design principles
- **Idempotent where possible**: retries shouldn't duplicate side effects.
- **Structured errors**: tools return `{ok, value}` or `{err, reason, retryable}` — small models reason better about structured errors than free-text exceptions.
- **Observation-shaped output**: tool output should be the *exact* form the agent will reason over next. No raw JSON dumps that need parsing.

### Open sub-questions
- Should `load_toolbox` swap toolboxes (free old) or stack them? **Lean: swap, with explicit pin for cross-cutting cases.**
- Tool discovery via vector search needs an embedding-friendly description per tool. **Action item:** define tool-description schema early.

---

## 7. Pillar 5: Learning Loop

### Decision: Explicit pipeline — encounter → understand → interpret → integrate → forget

The agent does not just *store* information it encounters; it processes it. This pillar is what makes "the agent gets smarter over time" a real claim rather than an aspiration. It's the load-bearing mechanism behind the small-model thesis.

### Pipeline stages

1. **Encounter** — agent reads something: web page, paper, file, tool output, or user-provided text. Triggered by:
   - A user task that required new information.
   - The agent flagging something as "worth coming back to."
   - (Optional, see below) self-directed study sessions.

2. **Understand** — extract the *content*: which concepts are mentioned, which claims are made, which definitions are given, which examples are used. Small models do this well — it's structured extraction from text already in context. Output: candidate concept nodes and candidate relations.

3. **Interpret** — link to existing knowledge. This is the step that distinguishes *learning* from *bookmarking*:
   - Does this concept already exist in the graph? (vector + name match)
   - Is this claim consistent with what's stored, or does it conflict?
   - Does this material reveal new relationships between existing concepts?

4. **Integrate** — write to the knowledge graph:
   - New concept nodes with provenance, confidence, domain tags.
   - New relations (typed, directional).
   - Updates to existing nodes: deeper definitions, additional examples, increased confidence on reconfirmation.
   - Conflict markers when new info contradicts old.

5. **Reflect** (periodic) — review recent additions:
   - Are new concepts well-connected, or floating?
   - Floating concepts → either deepen (find more material) or forget.
   - Spot under-developed areas the agent keeps bumping into.

6. **Forget** — discard:
   - Low-connectivity, low-usage concepts.
   - Inferred claims with decayed confidence.
   - Episodic content already distilled into semantic notes.
   - Material that consistently fails to inform reasoning.

### Why this is the load-bearing pillar
Without this loop, the agent has the same capabilities on day 100 as day 1, plus more clutter. With it, every interaction is a chance to deepen the world model. The user's thesis — small model + strong harness compensates — only holds if the harness *actually compounds capability over time*. This is the mechanism.

### Self-directed study (open question, lean: yes, bounded)
When idle, the agent can pick under-developed areas and read material on them through the same pipeline. This is what lets the agent learn *beyond* what immediate tasks demand. It needs to be **bounded** — time/rate/scope caps — so it's a budget, not a free-running loop. Worth deferring until the basic pipeline works on user-driven encounters.

### Open sub-questions
- Mastery signal: how does the agent decide a concept is "deeply understood" vs "surface-skimmed"? **Lean: composite of (#examples, #relations, #times-used-in-reasoning).**
- Conflict resolution when sources disagree: keep both with provenance and disputed flag, or pick by source-reliability score? **Lean: keep both, mark disputed, surface disagreement when recalled.**
- How aggressive is forgetting? **Lean: conservative early, more aggressive as the graph matures.**
- Does the Learning Loop run synchronously (blocking the agent's response) or asynchronously (background job after the response is sent)? **Lean: async by default — the user gets a fast response, learning happens after. Synchronous only when the new info is needed for the current response.**

---

## 8. Cross-cutting Concerns

### Context management
- Hard budget per model call (e.g. 4k–8k tokens of effective input for a 4B model).
- Compaction triggered before budget is hit, not after.
- Compaction strategy: summarize older turns; keep recent turns verbatim; always preserve pinned facts and the active plan.

### Failure modes to design against
- **Tool-call hallucination** (model invents a tool that doesn't exist) — validate against registered tools, return structured error.
- **Memory recall miss** (right info exists but wasn't surfaced) — log misses, periodically review to improve embeddings or graph edges.
- **Context bloat** (too much auto-injected stuff) — keep auto-inject ruthlessly small.
- **Reasoning loops** (agent thinks-thinks-thinks without acting) — turn-count cap; force action or `ask_user`.

### Evaluation
Build a small eval harness early:
- ~30 representative daily-life tasks (set reminder, look up X, run Y command, summarize Z page).
- Run on every harness change.
- Track: success rate, turns-to-success, tool errors, memory hits/misses.

---

## 9. Open Questions (deferred decisions)

| Question | Lean | Decide by |
|---|---|---|
| Framework: pi-mono vs alternatives | Spike pi-mono first | After 1-day evaluation |
| Graph DB (personal): SQLite-edges vs Kùzu vs Neo4j | SQLite-edges | When personal semantic exceeds ~10k nodes |
| Graph DB (knowledge): same options, but graph will grow much larger | Likely Kùzu or similar embedded graph DB | Before knowledge layer goes live |
| Top-level domain taxonomy for knowledge memory | Lift from existing source (Wikipedia categories, LoC headings) | Before first knowledge ingestion |
| Specific 4B model | TBD | Benchmark 3–4 candidates on the eval set |
| Embedding model | Local sentence-transformer | Alongside main model |
| When to introduce a 2nd agent | Never until forced | Only if a specific bottleneck demands it |
| Self-directed study: enabled or not | Defer; ship Learning Loop on user-driven encounters first | After basic pipeline is proven |

---

## 10. Suggested Next Steps

1. **Eval set first** (~1 day) — write 30 daily-life task descriptions with expected outcomes. Without this, every later choice is unmeasurable.
2. **Framework spike** (~1 day) — try pi-mono on a toy ReAct loop with 3 tools.
3. **Pick a model** (~1 day) — benchmark 3–4 small candidates on the eval set with a stub harness.
4. **MVP** (~1 week) — single agent, ReAct loop, 5 core tools, working memory only. End-to-end working assistant for simple tasks.
5. **Personal memory** (~1 week) — episodic vector store + personal semantic graph. Makes the agent coherent across sessions.
6. **Knowledge memory + Learning Loop** (~2 weeks) — this is the big one:
    - 6a. Knowledge graph storage (concepts, relations, domain tags, mastery).
    - 6b. Pipeline: encounter → understand → interpret → integrate.
    - 6c. Forgetting and consolidation mechanisms.
    - 6d. First evaluation: does retrieval from the knowledge graph improve task performance vs no knowledge memory?
7. **Toolboxes and computer use** — Jarvis-shaped capabilities.
8. **Lessons-learned loop** — close the self-improvement loop on the personal/task side (separate from knowledge learning).
9. **Self-directed study** (optional, gated on 6 working well) — the agent fills gaps in its own knowledge during idle time.

Build it in this order so you always have something working, and each layer is justified by a real limitation hit by the previous one. The Knowledge memory + Learning Loop step (6) is where Doba transitions from "useful assistant" to "agent that gets smarter over time."
