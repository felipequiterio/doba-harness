"""
run_multicard — orchestrator for eval 007 (multi-card / multi-step building).

004 showed the 4B builds *single* cards well. This pushes ingest to MULTI-step
problems (Hohmann / bi-elliptic transfers) that need a whole *set* of cards,
built from one method passage, then composed.

The mode-b confound (these problems live near the reasoning ceiling) is
controlled by running three conditions and reading them together:

  C2          no cards.                      (floor)
  C3_oracle   the dataset's hand multi-cards. (ceiling for COMPOSITION ability)
  C3_self     the 4B's own built card set.    (the test)

Reading: C3_oracle high + C3_self low → a *card-building* gap. Both low →
the *reasoning ceiling* (cards aren't the problem). So the answer metric is
disambiguated, not confounded.

Two scores per cell:
  - answer_correct : the numeric solve (composition included).
  - card_coverage  : did the built set contain the two physics building blocks
                     (circular speed + vis-viva), SymPy-checked. A robust proxy
                     for "did it extract the right formulas" — note a model that
                     writes the *composed* dv formula directly can still solve
                     while scoring low coverage, so coverage is a floor, not a
                     verdict.

Solve runs THINKING ON — these are multi-step, where 003 showed thinking pays
(+24pp). Build runs thinking ON too (extraction is reasoning).

Run:
    cd evals && uv run python -m lib.run_multicard --config 007-multicard-building/config.yaml \
        --problems astro-005,astro-014,astro-028 --n-runs 3
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Optional

from . import run as runlib
from .card_builder import build_cards
from .conditions import run_condition
from .models import StubClient, make_client
from .run_log import write_run_log
from .scoring import score_answer
from .symbolic import EquivSpec, check_formula

CONDITIONS = ["C2", "C3_oracle", "C3_self"]


# ---------------------------------------------------------------------------
# Coverage: the two physics building blocks every transfer card set needs.
# (Circular speed and vis-viva are disambiguated by their input set: r alone
# vs r AND a.) The transfer-semi-major and the burn-sum are arithmetic the
# model does in the solve, so they're not part of the coverage check.
# ---------------------------------------------------------------------------
_V_ALIASES = ["v", "v_c", "vc", "v_circ", "v_circular", "v1", "v2", "v_p", "v_a",
              "v_t", "v_peri", "v_apo", "speed", "v_b", "vp", "va"]


def _coverage_specs(mu: float) -> dict[str, EquivSpec]:
    return {
        "circular_speed": EquivSpec(
            target=_V_ALIASES,
            inputs={"r": ["r", "radius", "r1", "r2", "r_b", "rb", "r_i", "r_f", "ri", "rf"]},
            constants={"mu": (["mu", "gm", "mu_earth", "GM"], mu)},
            canonical=lambda r, mu: math.sqrt(mu / r),
            domains={"r": (6.6e6, 5.0e7)},
        ),
        "vis_viva": EquivSpec(
            target=_V_ALIASES,
            inputs={"r": ["r", "radius", "r1", "r2", "r_b", "rb", "r_i", "r_f", "ri", "rf"],
                    "a": ["a", "a_t", "at", "a_t1", "a_t2", "sma", "semi_major_axis", "a_transfer"]},
            constants={"mu": (["mu", "gm", "mu_earth", "GM"], mu)},
            canonical=lambda r, a, mu: math.sqrt(mu * (2 / r - 1 / a)),
            domains={"r": (6.6e6, 5.0e7), "a": (1.0e7, 6.0e7)},
        ),
    }


def _coverage(cards: list[dict[str, Any]], specs: dict[str, EquivSpec]) -> dict[str, bool]:
    """For each building-block spec, does ANY built card match it?"""
    out = {}
    for name, spec in specs.items():
        out[name] = any(check_formula(c.get("equation", ""), spec).ok for c in cards)
    return out


# ---------------------------------------------------------------------------
# Stub.
# ---------------------------------------------------------------------------
def _stub_policy(messages, model, ctx):
    sys_txt = messages[0]["content"] if messages else ""
    if "knowledge-extraction" in sys_txt:
        return json.dumps({"cards": [
            {"name": "circular-speed", "equation": "v_c = sqrt(mu/r)", "variables": {}, "when": "stub"},
            {"name": "vis-viva", "equation": "v = sqrt(mu*(2/r - 1/a))", "variables": {}, "when": "stub"},
        ]})
    return runlib._stub_policy(messages, model, ctx)


def _stub(problem, base):
    c = StubClient(policy=_stub_policy)
    c._state.update({"problem": problem, "condition": base, "is_frontier": False, "step": 0})
    return c


def _record(*, problem, condition, run_idx, model_name, run, score, built, cov):
    rec = {
        "id": problem["id"], "topic": problem["topic"], "structure": problem["structure"],
        "knowledge_label": problem.get("knowledge_label"),
        "condition": condition, "run_idx": run_idx, "model": model_name,
        "answer_correct": score.answer_correct,
        "solve_completion_tokens": run.completion_tokens, "n_tool_calls": run.n_tool_calls,
        "n_turns": run.n_turns, "stopped_reason": run.stopped_reason,
    }
    rec.update(score.to_dict())
    if condition == "C3_self" and built is not None:
        rec["n_cards"] = len(built.cards)
        rec["card_parse_ok"] = built.parse_ok
        rec["card_coverage"] = cov
        rec["coverage_frac"] = (sum(cov.values()) / len(cov)) if cov else 0.0
        rec["build_completion_tokens"] = built.completion_tokens
        rec["build_latency_s"] = round(built.latency_s, 3)
        rec["built_cards"] = built.cards
    return rec


def _summarize(records: list[dict[str, Any]]) -> str:
    from collections import defaultdict
    ans = defaultdict(list)
    per = defaultdict(lambda: defaultdict(list))
    for r in records:
        ans[r["condition"]].append(bool(r["answer_correct"]))
        per[r["id"]][r["condition"]].append(bool(r["answer_correct"]))

    def rate(xs): return f"{100*sum(xs)/len(xs):.0f}%" if xs else "–"
    def pct(c): return 100*sum(ans[c])/len(ans[c]) if ans.get(c) else None

    L = ["# Eval 007 — multi-card / multi-step building", "", f"Records: {len(records)}", ""]
    L.append("## Answer-correctness by condition")
    L.append("")
    L.append("| Condition | answer correct | n |")
    L.append("|---|---|---|")
    for c in CONDITIONS:
        if ans.get(c):
            L.append(f"| {c} | {rate(ans[c])} | {len(ans[c])} |")
    L.append("")
    if pct("C2") is not None and pct("C3_oracle") is not None and pct("C3_self") is not None:
        c2, ora, slf = pct("C2"), pct("C3_oracle"), pct("C3_self")
        denom = ora - c2
        L.append(f"- **Oracle lift (C3_oracle − C2): {ora-c2:+.0f} pp** — do the cards matter here?")
        if abs(denom) > 1e-9:
            L.append(f"- **Lift recovered by self-built cards: {100*(slf-c2)/denom:.0f}%**")
        L.append(f"- **Card-building gap (C3_oracle − C3_self): {ora-slf:+.0f} pp** — "
                 f"if positive, self-built sets are weaker than hand cards (a *building* gap, "
                 f"not the ceiling).")
    # coverage
    cov = [r for r in records if r["condition"] == "C3_self" and "coverage_frac" in r]
    if cov:
        avg = 100*sum(r["coverage_frac"] for r in cov)/len(cov)
        cs = 100*sum(1 for r in cov if r["card_coverage"].get("circular_speed"))/len(cov)
        vv = 100*sum(1 for r in cov if r["card_coverage"].get("vis_viva"))/len(cov)
        ncards = sum(r["n_cards"] for r in cov)/len(cov)
        L.append(f"- **Building-block coverage (self): {avg:.0f}%** "
                 f"(circular-speed {cs:.0f}%, vis-viva {vv:.0f}%; avg {ncards:.1f} cards built).")
    L.append("")
    L.append("## Per-problem answer-correctness")
    L.append("")
    L.append("| Problem | know | " + " | ".join(CONDITIONS) + " |")
    L.append("|---|---|" + "---|"*len(CONDITIONS))
    for pid in sorted({r["id"] for r in records}):
        kl = next((r["knowledge_label"] for r in records if r["id"] == pid), None)
        cells = " | ".join(rate(per[pid][c]) for c in CONDITIONS)
        L.append(f"| {pid} | {kl} | {cells} |")
    L.append("")
    # mode-b read: which problems are card-gaps vs ceilings
    L.append("**Mode separation:** a problem with C3_oracle high & C3_self low is a "
             "*card-building* gap; one where both are low is the *reasoning ceiling*.")
    L.append("")
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Eval 007 — multi-card building.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--problems", default=None)
    ap.add_argument("--n-runs", type=int, default=None)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--no-logs", action="store_true")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    cfg = runlib._load_config(config_path)
    problems_all = runlib._load_dataset(cfg)
    constants = runlib._load_dataset_constants(cfg)
    mu = float(constants.get("mu_earth", 3.986004418e14))
    cov_specs = _coverage_specs(mu)

    src_path = config_path.resolve().parent / "sources.json"
    raw_src = json.loads(src_path.read_text())
    base_dir = src_path.parent
    sources = {pid: {"passage": (base_dir / s["passage"]).read_text(), "purpose": s["purpose"]}
               for pid, s in raw_src.items() if not pid.startswith("_")}

    n_runs = args.n_runs if args.n_runs is not None else cfg.get("n_runs", 3)
    wanted = ([p.strip() for p in args.problems.split(",")] if args.problems else list(sources.keys()))
    problems = [p for p in problems_all if p["id"] in set(wanted)]
    for p in problems:
        if p["id"] not in sources:
            raise SystemExit(f"{p['id']} has no source passage in sources.json")
    if not problems:
        raise SystemExit(f"no problems matched {wanted}")

    local = cfg["local_model"]
    model_name = local["model_name"]
    provider = (local.get("provider") or "ollama").lower()
    solve_dt = bool(local.get("disable_thinking", False))   # multi-step → thinking ON
    build_dt = bool((cfg.get("card_build") or {}).get("disable_thinking", False))
    solve_max = args.max_tokens or local.get("max_tokens")
    build_max = (cfg.get("card_build") or {}).get("max_tokens") or solve_max
    temperature = cfg.get("temperature", 0.2)

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

    print(f"eval 007 — multi-card building · {len(problems)} problems × {len(CONDITIONS)} conditions × N={n_runs}")
    print(f"solve think={'OFF' if solve_dt else 'ON'} · build think={'OFF' if build_dt else 'ON'}")
    print(f"run dir: {run_dir}")
    print("-" * 78)

    records: list[dict[str, Any]] = []
    fh = out_path.open("a")
    cell = 0
    total = len(problems) * len(CONDITIONS) * n_runs
    t0 = time.monotonic()
    try:
        for problem in problems:
            src = sources[problem["id"]]
            for condition in CONDITIONS:
                base = "C2" if condition == "C2" else "C3"
                for run_idx in range(n_runs):
                    cell += 1
                    built = None
                    cov = None
                    solve_problem = problem
                    if condition == "C3_self":
                        bclient = _stub(problem, base) if args.stub else client
                        built = build_cards(
                            client=bclient, model_name=("stub" if args.stub else model_name),
                            passage=src["passage"], purpose=src["purpose"],
                            temperature=temperature, max_tokens=build_max, disable_thinking=build_dt)
                        cov = _coverage(built.cards, cov_specs)
                        solve_problem = {**problem, "oracle_cards": built.cards}

                    solver = _stub(solve_problem, base) if args.stub else client
                    run = run_condition(
                        client=solver, model_name=("stub" if args.stub else model_name),
                        problem=solve_problem, condition=base, temperature=temperature,
                        max_tool_turns=cfg.get("max_tool_turns", 8),
                        max_total_turns=cfg.get("max_total_turns", 16),
                        max_tokens=solve_max, disable_thinking=solve_dt, calculator_extras=constants)
                    score = score_answer(
                        run.final_text, expected_value=problem["expected"]["value"],
                        expected_unit=problem["expected"]["unit"],
                        tolerance_rel=problem.get("tolerance_rel", cfg.get("default_tolerance_rel", 0.01)),
                        full_trace=run.full_trace)
                    rec = _record(problem=problem, condition=condition, run_idx=run_idx,
                                  model_name=("stub" if args.stub else model_name),
                                  run=run, score=score, built=built, cov=cov)
                    fh.write(json.dumps(rec) + "\n"); fh.flush()
                    records.append(rec)
                    if logs_dir is not None:
                        write_run_log(logs_dir=logs_dir, problem=problem, condition=condition,
                                      run_idx=run_idx, model_name=("stub" if args.stub else model_name),
                                      provider=("stub" if args.stub else provider),
                                      temperature=temperature, max_tokens=solve_max, run=run, score=score)
                    amark = "✓" if score.answer_correct else "✗"
                    extra = ""
                    if condition == "C3_self" and built is not None:
                        cstr = f"{sum(cov.values())}/{len(cov)}" if cov else "?"
                        extra = f"  cards={len(built.cards)} cov={cstr}"
                    print(f"[{cell:>3}/{total}] {problem['id']:<11} {condition:<10} r{run_idx} "
                          f"{amark}  tok={run.completion_tokens}{extra}")
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
