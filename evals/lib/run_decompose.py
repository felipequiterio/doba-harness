"""
run_decompose — orchestrator for eval 008 (does decomposition add value?).

Holds KNOWLEDGE constant (oracle cards present in both conditions) and varies
only the SOLVE STRATEGY on hard multi-step transfer problems:

  mono     monolithic solve — oracle cards, thinking ON. (= how it's done now,
           == 007's C3_oracle.) The baseline.
  decomp   decompose-then-solve (lib.decompose): plan the sub-steps (thinking),
           compute each leaf as a focused no-think calculator solve, compose.

The question (yours): does decomposition add any value on the hard multi-step
problems — bi-elliptic, the small Hohmann, the 0/3 combined-plane-change wall?
007 showed the monolithic solve drops sub-steps ("computed burn 1, forgot burn
2"); decomposition can't drop a planned leaf.

Metrics: answer-correctness mono vs decomp (per problem + overall), the cost
(tokens) of each, and specifically whether decomp cracks the cases mono can't.

Run:
    cd evals && uv run python -m lib.run_decompose --config 008-decomposition/config.yaml \
        --problems astro-028,astro-031,astro-014 --n-runs 3
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Optional

from . import run as runlib
from .conditions import _oracle_block, run_condition
from .decompose import solve_decomposed
from .models import StubClient, make_client
from .run_log import write_run_log
from .scoring import score_answer

CONDITIONS = ["mono", "decomp"]


def _stub_policy(messages, model, ctx):
    sys = messages[0]["content"] if messages else ""
    if "PROGRAM" in sys:                      # PLAN call → a one-step program
        return f'[{{"name": "answer", "expr": "{ctx.get("expected", 0.0):.10g}"}}]'
    return runlib._stub_policy(messages, model, ctx)   # MONO solve


def _stub(problem):
    c = StubClient(policy=_stub_policy)
    c._state.update({"problem": problem, "condition": "C3", "is_frontier": False,
                     "step": 0, "expected": problem["expected"]["value"]})
    return c


def _summarize(records: list[dict[str, Any]]) -> str:
    from collections import defaultdict
    import statistics as st
    ans = defaultdict(list); per = defaultdict(lambda: defaultdict(list))
    tok = defaultdict(list)
    for r in records:
        ans[r["condition"]].append(bool(r["answer_correct"]))
        per[r["id"]][r["condition"]].append(bool(r["answer_correct"]))
        tok[r["condition"]].append(r["completion_tokens"])

    def rate(xs): return f"{100*sum(xs)/len(xs):.0f}%" if xs else "–"
    def pct(c): return 100*sum(ans[c])/len(ans[c]) if ans.get(c) else None

    L = ["# Eval 008 — does decomposition add value?", "", f"Records: {len(records)}", ""]
    L.append("## Answer-correctness & cost by condition")
    L.append("")
    L.append("| Condition | answer correct | median completion tok | n |")
    L.append("|---|---|---|---|")
    for c in CONDITIONS:
        if ans.get(c):
            L.append(f"| {c} | {rate(ans[c])} | {st.median(tok[c]):.0f} | {len(ans[c])} |")
    L.append("")
    if pct("mono") is not None and pct("decomp") is not None:
        L.append(f"- **Decomposition effect (decomp − mono): {pct('decomp')-pct('mono'):+.0f} pp**")
        mt = st.median(tok["mono"]); dt = st.median(tok["decomp"])
        L.append(f"- **Cost ratio (decomp / mono tokens): {dt/mt:.2f}×** (median {dt:.0f} vs {mt:.0f}).")
    # plan quality
    dec = [r for r in records if r["condition"] == "decomp"]
    if dec:
        pok = 100*sum(1 for r in dec if r.get("plan_ok"))/len(dec)
        ns = st.median([r.get("n_steps", 0) for r in dec])
        L.append(f"- **Plan: {pok:.0f}% parsed, median {ns:.0f} steps.**")
    L.append("")
    L.append("## Per-problem answer-correctness (mono → decomp)")
    L.append("")
    L.append("| Problem | know | mono | decomp |")
    L.append("|---|---|---|---|")
    for pid in sorted({r["id"] for r in records}):
        kl = next((r["knowledge_label"] for r in records if r["id"] == pid), None)
        L.append(f"| {pid} | {kl} | {rate(per[pid]['mono'])} | {rate(per[pid]['decomp'])} |")
    L.append("")
    L.append("**Read:** decomp > mono on a problem ⇒ decomposition rescued a "
             "composition the monolithic solve dropped; both still 0 ⇒ a genuine "
             "ceiling that planning doesn't crack.")
    L.append("")
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Eval 008 — decomposition value.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--problems", default=None)
    ap.add_argument("--n-runs", type=int, default=None)
    ap.add_argument("--conditions", default=None)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--no-logs", action="store_true")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    cfg = runlib._load_config(config_path)
    problems_all = runlib._load_dataset(cfg)
    constants = runlib._load_dataset_constants(cfg)

    conditions = ([c.strip() for c in args.conditions.split(",")] if args.conditions else list(CONDITIONS))
    n_runs = args.n_runs if args.n_runs is not None else cfg.get("n_runs", 3)
    wanted = ([p.strip() for p in args.problems.split(",")] if args.problems else None)
    problems = [p for p in problems_all if (wanted is None or p["id"] in set(wanted)) and p.get("oracle_cards")]
    if not problems:
        raise SystemExit("no problems with oracle_cards matched")

    local = cfg["local_model"]
    model_name = local["model_name"]
    provider = (local.get("provider") or "ollama").lower()
    mono_dt = bool(local.get("disable_thinking", False))           # mono solve → thinking ON
    dcfg = cfg.get("decompose") or {}
    plan_dt = bool(dcfg.get("plan_disable_thinking", False))       # plan → thinking ON
    solve_max = local.get("max_tokens")
    max_compl = cfg.get("max_completion_tokens")   # caps the thinking runaway
    plan_max = dcfg.get("plan_max_tokens") or solve_max
    temperature = cfg.get("temperature", 0.2)
    # (decomp leaves are now DETERMINISTIC calculator execution — no leaf model calls.)

    run_id = runlib._make_run_id()
    run_dir = runlib._make_run_dir(cfg, run_id)
    out_path = run_dir / "runs.jsonl"
    logs_dir = None if args.no_logs else (run_dir / "logs")
    if logs_dir:
        logs_dir.mkdir(parents=True, exist_ok=True)

    client = None if args.stub else make_client(local)
    if client is not None and hasattr(client, "warmup"):
        w = client.warmup(model_name)
        print(f"warmup: {model_name} loaded in {w.get('latency_s','?')}s" if w.get("loaded")
              else f"warmup: skipped ({w.get('error')})")

    print(f"eval 008 — decomposition · {len(problems)} problems × {len(conditions)} conditions × N={n_runs}")
    print(f"mono think={'OFF' if mono_dt else 'ON'} · plan think={'OFF' if plan_dt else 'ON'} · leaves=deterministic calc")
    print(f"run dir: {run_dir}")
    print("-" * 78)

    records: list[dict[str, Any]] = []
    fh = out_path.open("a")
    cell = 0
    total = len(problems) * len(conditions) * n_runs
    t0 = time.monotonic()
    try:
        for problem in problems:
            cards_block = _oracle_block(problem)
            for condition in conditions:
                for run_idx in range(n_runs):
                    cell += 1
                    solver = _stub(problem) if args.stub else client
                    rec: dict[str, Any] = {
                        "id": problem["id"], "topic": problem["topic"], "structure": problem["structure"],
                        "knowledge_label": problem.get("knowledge_label"),
                        "condition": condition, "run_idx": run_idx,
                        "model": ("stub" if args.stub else model_name),
                    }
                    if condition == "mono":
                        run = run_condition(
                            client=solver, model_name=("stub" if args.stub else model_name),
                            problem=problem, condition="C3", temperature=temperature,
                            max_tool_turns=cfg.get("max_tool_turns", 12),
                            max_total_turns=cfg.get("max_total_turns", 24),
                            max_tokens=solve_max, disable_thinking=mono_dt, calculator_extras=constants,
                            max_total_completion_tokens=max_compl)
                        score = score_answer(run.final_text, expected_value=problem["expected"]["value"],
                                             expected_unit=problem["expected"]["unit"],
                                             tolerance_rel=problem.get("tolerance_rel", cfg.get("default_tolerance_rel", 0.01)),
                                             full_trace=run.full_trace)
                        rec.update({"completion_tokens": run.completion_tokens, "n_tool_calls": run.n_tool_calls,
                                    "stopped_reason": run.stopped_reason})
                        if logs_dir is not None:
                            write_run_log(logs_dir=logs_dir, problem=problem, condition=condition,
                                          run_idx=run_idx, model_name=("stub" if args.stub else model_name),
                                          provider=("stub" if args.stub else provider), temperature=temperature,
                                          max_tokens=solve_max, run=run, score=score)
                    else:  # decomp
                        dr = solve_decomposed(
                            client=solver, model_name=("stub" if args.stub else model_name),
                            problem=problem, cards_block=cards_block, expected_unit=problem["expected"]["unit"],
                            calculator_extras=constants, temperature=temperature,
                            plan_max_tokens=plan_max, plan_disable_thinking=plan_dt,
                            max_steps=dcfg.get("max_steps", 16))
                        score = score_answer(dr.final_text, expected_value=problem["expected"]["value"],
                                             expected_unit=problem["expected"]["unit"],
                                             tolerance_rel=problem.get("tolerance_rel", cfg.get("default_tolerance_rel", 0.01)),
                                             full_trace=dr.full_trace)
                        rec.update({"completion_tokens": dr.completion_tokens, "n_tool_calls": dr.n_tool_calls,
                                    "stopped_reason": dr.stopped_reason, "plan_ok": dr.plan_ok,
                                    "n_steps": dr.n_steps, "plan": dr.plan,
                                    "leaf_values": [{"id": lf.id, "name": lf.compute, "expr": lf.expr,
                                                     "value": lf.value, "error": lf.error} for lf in dr.leaves],
                                    "final_value": dr.final_value})
                        if logs_dir is not None:
                            (logs_dir / f"{problem['id']}__decomp__run{run_idx}.log").write_text(
                                f"PROBLEM {problem['id']} · expected {problem['expected']}\n"
                                f"answer_correct={score.answer_correct} final={dr.final_value} "
                                f"stopped={dr.stopped_reason} steps={dr.n_steps} tok={dr.completion_tokens}\n\n"
                                + dr.full_trace)
                    rec.update(score.to_dict())
                    fh.write(json.dumps(rec) + "\n"); fh.flush()
                    records.append(rec)
                    amark = "✓" if score.answer_correct else "✗"
                    extra = f"  steps={rec.get('n_steps')}" if condition == "decomp" else ""
                    print(f"[{cell:>3}/{total}] {problem['id']:<11} {condition:<7} r{run_idx} "
                          f"{amark}  tok={rec['completion_tokens']}{extra}")
    finally:
        fh.close()
        if client is not None and hasattr(client, "close"):
            client.close()

    (run_dir / "summary.md").write_text(_summarize(records))
    runlib._update_latest_symlink(runlib._runs_root(cfg), run_dir)
    print("-" * 78)
    print(f"wrote {len(records)} runs in {time.monotonic()-t0:.1f}s -> {out_path}")
    print()
    print(_summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
