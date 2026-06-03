"""
run_cards — orchestrator for eval 004 (self-built knowledge cards).

The question: can the 4B BUILD a usable concept card from a source passage,
or does the +30pp oracle lift only exist for human-authored cards?

Three conditions per problem, all sharing the existing harness:
  C2        — calculator, NO card.                         (floor)
  C3_oracle — calculator + the dataset's hand-written card. (ceiling)
  C3_self   — calculator + a card the 4B built from the     (the test)
              real Hintz passage (lib.card_builder).

C3_self is literally "C3 with oracle_cards swapped for model-built cards" —
same injection path (conditions._oracle_block), same calculator, same
scoring, same code-computed ground truth. Exactly one variable changes:
who wrote the card.

Headline metric (printed + written to summary.md):
    lift_recovered = (C3_self - C2) / (C3_oracle - C2)
~1.0 → the agent can build its own knowledge (green-light Pillar 5).
~0   → ingestion needs rework before investing.

Run:
    cd evals && uv run python -m lib.run_cards --config 004-self-built-cards/config.yaml \
        --problems astro-016,astro-025,astro-027 --n-runs 3
    # or --stub for a no-Ollama wiring check.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import run as runlib  # reuse config/dataset/run-dir helpers
from .card_builder import build_cards
from .conditions import run_condition
from .models import StubClient, make_client
from .run_log import write_run_log
from .scoring import score_answer

CONDITIONS = ["C2", "C3_oracle", "C3_self"]


# ---------------------------------------------------------------------------
# Sources manifest: problem_id -> {passage_path, purpose}.
# ---------------------------------------------------------------------------
def _load_sources(config_path: Path) -> dict[str, dict[str, str]]:
    """Load sources.json sitting next to the config. Passage paths in it are
    resolved relative to the config dir."""
    src_path = config_path.resolve().parent / "sources.json"
    if not src_path.exists():
        raise SystemExit(f"missing sources manifest: {src_path}")
    raw = json.loads(src_path.read_text())
    out: dict[str, dict[str, str]] = {}
    base = src_path.parent
    for pid, spec in raw.items():
        if pid.startswith("_"):
            continue
        passage_file = (base / spec["passage"]).resolve()
        if not passage_file.exists():
            raise SystemExit(f"passage file not found for {pid}: {passage_file}")
        out[pid] = {
            "passage": passage_file.read_text(),
            "passage_file": str(passage_file.relative_to(base)),
            "purpose": spec["purpose"],
        }
    return out


# ---------------------------------------------------------------------------
# Stub policy: builds a valid card on the build call, solves on the rest.
# Lets `make smoke` exercise the full wiring with no Ollama.
# ---------------------------------------------------------------------------
def _stub_policy(messages, model, ctx):
    sys_txt = messages[0]["content"] if messages else ""
    # Card-build call: the card_builder system prompt mentions "knowledge-extraction".
    if "knowledge-extraction" in sys_txt:
        return json.dumps({"cards": [{
            "name": "stub-card",
            "equation": "x = a * b",
            "variables": {"x": "result", "a": "input a", "b": "input b"},
            "when": "stub card for wiring tests",
        }]})
    # Otherwise it's a solve turn — reuse the main runner's stub policy.
    return runlib._stub_policy(messages, model, ctx)


def _make_solver_stub(problem: dict[str, Any], condition_base: str) -> StubClient:
    c = StubClient(policy=_stub_policy)
    c._state["problem"] = problem
    c._state["condition"] = condition_base   # C2 or C3 — drives stub correctness
    c._state["is_frontier"] = False
    c._state["step"] = 0
    return c


# ---------------------------------------------------------------------------
# Record assembly.
# ---------------------------------------------------------------------------
def _record(*, problem, condition, run_idx, model_name, run, score,
            built: Optional[Any], source_file: Optional[str]) -> dict[str, Any]:
    rec = {
        "id": problem["id"],
        "topic": problem["topic"],
        "structure": problem["structure"],
        "knowledge_label": problem.get("knowledge_label"),
        "condition": condition,
        "run_idx": run_idx,
        "model": model_name,
        "n_tool_calls": run.n_tool_calls,
        "n_turns": run.n_turns,
        "latency_s": round(run.latency_s, 4),
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "total_tokens": run.total_tokens,
        "tokens_per_second": round(run.tokens_per_second, 2),
        "stopped_reason": run.stopped_reason,
        "error": run.error,
        "raw_output": run.final_text,
        "full_trace": run.full_trace,
    }
    rec.update(score.to_dict())
    # C3_self carries the card-build provenance so analysis can correlate
    # parse failures / bad cards with solve failures.
    if condition == "C3_self" and built is not None:
        rec["source_file"] = source_file
        rec["card_build"] = {
            "parse_ok": built.parse_ok,
            "n_cards": len(built.cards),
            "prompt_tokens": built.prompt_tokens,
            "completion_tokens": built.completion_tokens,
            "latency_s": round(built.latency_s, 4),
            "error": built.error,
        }
        rec["built_cards"] = built.cards
        rec["card_build_raw"] = built.raw_response
    return rec


# ---------------------------------------------------------------------------
# Analysis: per-condition success + lift recovered.
# ---------------------------------------------------------------------------
def _summarize(records: list[dict[str, Any]]) -> str:
    from collections import defaultdict
    agg: dict[tuple[str, str], list[bool]] = defaultdict(list)
    by_cond: dict[str, list[bool]] = defaultdict(list)
    parse_fail = 0
    parse_total = 0
    for r in records:
        ok = bool(r.get("answer_correct"))
        agg[(r["id"], r["condition"])].append(ok)
        by_cond[r["condition"]].append(ok)
        if r["condition"] == "C3_self":
            parse_total += 1
            if not (r.get("card_build") or {}).get("parse_ok"):
                parse_fail += 1

    def rate(xs: list[bool]) -> float:
        return 100.0 * sum(xs) / len(xs) if xs else 0.0

    lines = ["# Eval 004 — self-built knowledge cards", ""]
    lines.append(f"Records: {len(records)}  ·  conditions: {', '.join(CONDITIONS)}")
    lines.append("")
    lines.append("## Overall success rate by condition")
    lines.append("")
    lines.append("| Condition | success | n |")
    lines.append("|---|---|---|")
    for c in CONDITIONS:
        xs = by_cond.get(c, [])
        lines.append(f"| {c} | {rate(xs):.0f}% | {len(xs)} |")
    lines.append("")

    c2, ora, slf = (rate(by_cond.get(c, [])) for c in CONDITIONS)
    denom = ora - c2
    recovered = (slf - c2) / denom if abs(denom) > 1e-9 else float("nan")
    lines.append("## Headline: lift recovered by self-built cards")
    lines.append("")
    lines.append(f"- C2 (no card): **{c2:.0f}%**")
    lines.append(f"- C3_oracle (human card): **{ora:.0f}%**")
    lines.append(f"- C3_self (4B-built card): **{slf:.0f}%**")
    lines.append(f"- oracle lift (oracle − C2): **{denom:+.0f} pp**")
    if denom != denom or abs(denom) <= 1e-9:  # nan or ~0
        lines.append("- **lift recovered: N/A** (oracle gave no lift on this set — "
                     "pick harder knowledge=absent problems)")
    else:
        lines.append(f"- **lift recovered: {recovered*100:.0f}%** of the oracle's gain")
    lines.append("")
    lines.append(f"Card-build JSON parse failures: {parse_fail}/{parse_total}")
    lines.append("")

    lines.append("## Per-problem breakdown (success rate)")
    lines.append("")
    lines.append("| Problem | C2 | C3_oracle | C3_self |")
    lines.append("|---|---|---|---|")
    pids = sorted({r["id"] for r in records})
    for pid in pids:
        cells = " | ".join(
            f"{rate(agg.get((pid, c), [])):.0f}%" for c in CONDITIONS
        )
        lines.append(f"| {pid} | {cells} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Eval 004 — self-built knowledge cards.")
    ap.add_argument("--config", required=True, help="Path to 004 config.yaml.")
    ap.add_argument("--problems", default=None, help="Comma-separated problem ids.")
    ap.add_argument("--n-runs", type=int, default=None)
    ap.add_argument("--stub", action="store_true", help="No-Ollama wiring check.")
    ap.add_argument("--max-tokens", type=int, default=None, help="Override solve max_tokens.")
    ap.add_argument("--no-logs", action="store_true")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    cfg = runlib._load_config(config_path)          # sets runlib._CONFIG_DIR
    problems_all = runlib._load_dataset(cfg)
    constants = runlib._load_dataset_constants(cfg)
    sources = _load_sources(config_path)

    n_runs = args.n_runs if args.n_runs is not None else cfg.get("n_runs", 3)
    wanted = (
        [p.strip() for p in args.problems.split(",") if p.strip()]
        if args.problems else list(sources.keys())
    )
    problems = [p for p in problems_all if p["id"] in set(wanted)]
    missing_src = [p["id"] for p in problems if p["id"] not in sources]
    if missing_src:
        raise SystemExit(f"no source passage mapped for: {missing_src} (add to sources.json)")
    if not problems:
        raise SystemExit(f"no problems matched {wanted}")

    local = cfg["local_model"]
    model_name = local["model_name"]
    provider = (local.get("provider") or "ollama").lower()
    solve_disable_think = bool(local.get("disable_thinking", True))   # single-step → no-think
    build_disable_think = bool((cfg.get("card_build") or {}).get("disable_thinking", False))  # extraction → think
    solve_max_tokens = args.max_tokens or local.get("max_tokens")
    build_max_tokens = (cfg.get("card_build") or {}).get("max_tokens") or solve_max_tokens
    temperature = cfg.get("temperature", 0.2)

    # Run-history dir (reuses the standard timestamped layout + latest symlink).
    run_id = runlib._make_run_id()
    run_dir = runlib._make_run_dir(cfg, run_id)
    out_path = run_dir / "runs.jsonl"
    logs_dir = None if args.no_logs else (run_dir / "logs")
    if logs_dir:
        logs_dir.mkdir(parents=True, exist_ok=True)

    client = None if args.stub else make_client(local)

    print(f"eval 004 — self-built cards  ·  {len(problems)} problems × {len(CONDITIONS)} conditions × N={n_runs}")
    print(f"model: {'stub' if args.stub else model_name}  ·  solve think={'OFF' if solve_disable_think else 'ON'}"
          f"  ·  build think={'OFF' if build_disable_think else 'ON'}")
    # Load + pin the model once up front so the first cell isn't a cold load
    # and it stays resident (keep_alive) for the whole sweep — no per-cell reloads.
    if client is not None and hasattr(client, "warmup"):
        w = client.warmup(model_name)
        if w.get("loaded"):
            print(f"warmup: {model_name} loaded in {w['latency_s']}s · keep_alive={w['keep_alive']}")
        else:
            print(f"warmup: skipped ({w.get('error')}) — first call will pay the load")
    print(f"run dir: {run_dir}")
    print("-" * 70)

    records: list[dict[str, Any]] = []
    cell = 0
    total_cells = len(problems) * len(CONDITIONS) * n_runs
    t0 = time.monotonic()
    fh = out_path.open("a")
    try:
        for problem in problems:
            src = sources[problem["id"]]
            for condition in CONDITIONS:
                base = "C2" if condition == "C2" else "C3"
                for run_idx in range(n_runs):
                    cell += 1
                    built = None
                    solve_problem = problem
                    # C3_self: build a card from the passage, swap it in as the oracle.
                    if condition == "C3_self":
                        if args.stub:
                            bclient = _make_solver_stub(problem, base)
                        else:
                            bclient = client
                        built = build_cards(
                            client=bclient,
                            model_name="stub" if args.stub else model_name,
                            passage=src["passage"],
                            purpose=src["purpose"],
                            temperature=temperature,
                            max_tokens=build_max_tokens,
                            disable_thinking=build_disable_think,
                        )
                        solve_problem = {**problem, "oracle_cards": built.cards}

                    solver = _make_solver_stub(solve_problem, base) if args.stub else client
                    run = run_condition(
                        client=solver,
                        model_name="stub" if args.stub else model_name,
                        problem=solve_problem,
                        condition=base,
                        temperature=temperature,
                        max_tool_turns=cfg.get("max_tool_turns", 8),
                        max_total_turns=cfg.get("max_total_turns", 16),
                        max_tokens=solve_max_tokens,
                        disable_thinking=solve_disable_think,
                        calculator_extras=constants,
                    )
                    score = score_answer(
                        run.final_text,
                        expected_value=problem["expected"]["value"],
                        expected_unit=problem["expected"]["unit"],
                        tolerance_rel=problem.get("tolerance_rel", cfg.get("default_tolerance_rel", 0.01)),
                        full_trace=run.full_trace,
                    )
                    rec = _record(
                        problem=problem, condition=condition, run_idx=run_idx,
                        model_name=("stub" if args.stub else model_name),
                        run=run, score=score, built=built,
                        source_file=src["passage_file"],
                    )
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
                    records.append(rec)
                    if logs_dir is not None:
                        write_run_log(
                            logs_dir=logs_dir, problem=problem, condition=condition,
                            run_idx=run_idx, model_name=("stub" if args.stub else model_name),
                            provider=("stub" if args.stub else provider),
                            temperature=temperature, max_tokens=solve_max_tokens,
                            run=run, score=score,
                        )
                    mark = "✓" if score.answer_correct else "✗"
                    extra = ""
                    if condition == "C3_self" and built is not None:
                        extra = f"  card[{len(built.cards)} {'ok' if built.parse_ok else 'PARSE-FAIL'}]"
                    print(f"[{cell:>3}/{total_cells}] {problem['id']:<11} {condition:<10} "
                          f"r{run_idx} {mark}  tok={run.completion_tokens:<6} "
                          f"{run.stopped_reason:<14}{extra}")
    finally:
        fh.close()
        if client is not None and hasattr(client, "close"):
            client.close()

    summary = _summarize(records)
    (run_dir / "summary.md").write_text(summary)
    runlib._update_latest_symlink(runlib._runs_root(cfg), run_dir)

    dt = time.monotonic() - t0
    print("-" * 70)
    print(f"wrote {len(records)} runs in {dt:.1f}s -> {out_path}")
    print()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
