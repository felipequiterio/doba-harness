"""
run_derive — orchestrator for eval 005 (deriving formulas with a symbolic tool).

The passages state relations in non-closed form, so building a usable card
requires a real derivation. Three build modes, each then solves numerically:

  build_text    — derive in-head (no tool).            [baseline]
  build_sympy   — model drives the SymPy `SYMB:` tool.  [tool-assisted derive]
  build_verify  — derive in-head, then verify/correct with the tool.

TWO independent scores per cell:
  - answer_correct  : the numeric solve (existing harness).
  - formula_status  : SymPy equivalence of the built card to the canonical
                      formula (lib.symbolic.check_formula) — the metric that
                      tells correct-derivation from lucky-number.

Run:
    cd evals && uv run python -m lib.run_derive --config 005-symbolic-derivation/config.yaml \
        --problems astro-025,astro-037,astro-038 --n-runs 3
    # or --stub for a no-Ollama wiring check.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Optional

from . import run as runlib
from .card_builder_symbolic import build_cards_x
from .conditions import run_condition
from .models import StubClient, make_client
from .run_log import write_run_log
from .scoring import score_answer
from .symbolic import EquivSpec, FormulaCheck, check_formula

# condition label -> build mode
CONDITIONS = {"build_text": "text", "build_sympy": "sympy", "build_verify": "verify"}
ORDER = ["build_text", "build_sympy", "build_verify"]


# ---------------------------------------------------------------------------
# Canonical formula specs (the equivalence judge's reference). mu is treated
# as a known constant so the check is stable whether the model keeps it
# symbolic or plugs the number.
# ---------------------------------------------------------------------------
def _specs(mu: float) -> dict[str, EquivSpec]:
    return {
        "astro-025": EquivSpec(
            target=["beta", "gamma", "phi", "fpa", "flight_path_angle", "flightpathangle"],
            inputs={"e": ["e", "ecc", "eccentricity"],
                    "theta": ["theta", "nu", "true_anomaly", "trueanomaly", "f"]},
            canonical=lambda e, theta: math.atan(e * math.sin(theta) / (1 + e * math.cos(theta))),
            domains={"e": (0.01, 0.7), "theta": (0.3, 2.8)},
        ),
        "astro-037": EquivSpec(
            target=["a", "sma", "semi_major_axis", "semimajor_axis", "semimajoraxis"],
            inputs={"T": ["T", "period", "tau", "P"]},
            constants={"mu": (["mu", "gm", "mu_earth", "GM"], mu)},
            canonical=lambda T, mu: (mu * T ** 2 / (4 * math.pi ** 2)) ** (1 / 3),
            domains={"T": (3000.0, 40000.0)},
        ),
        "astro-038": EquivSpec(
            target=["v", "speed", "velocity"],
            inputs={"r": ["r", "radius"],
                    "a": ["a", "sma", "semi_major_axis", "semimajor_axis"]},
            constants={"mu": (["mu", "gm", "mu_earth", "GM"], mu)},
            canonical=lambda r, a, mu: math.sqrt(mu * (2 / r - 1 / a)),
            domains={"r": (6.6e6, 9.0e6), "a": (7.0e6, 1.2e7)},
        ),
    }


_STATUS_RANK = {"equivalent": 0, "not_equivalent": 1, "unmapped": 2,
                "unparseable": 3, "no_equation": 4, "no_card": 5}


def _best_formula_check(cards: list[dict[str, Any]], spec: EquivSpec) -> FormulaCheck:
    """Check every built card; the set is 'equivalent' if ANY card matches the
    canonical formula. Otherwise return the most-informative failure."""
    if not cards:
        return FormulaCheck("no_card", "no card produced")
    best: Optional[FormulaCheck] = None
    for c in cards:
        fc = check_formula(c.get("equation", ""), spec)
        if fc.status == "equivalent":
            return fc
        if best is None or _STATUS_RANK[fc.status] < _STATUS_RANK[best.status]:
            best = fc
    return best  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Stub: build calls return a card; solve calls reuse the main stub policy.
# ---------------------------------------------------------------------------
def _stub_policy(messages, model, ctx):
    sys_txt = messages[0]["content"] if messages else ""
    if "knowledge-extraction" in sys_txt:
        return json.dumps({"cards": [{
            "name": "stub", "equation": "y = a*b",
            "variables": {"y": "out", "a": "in", "b": "in"}, "when": "stub"}]})
    return runlib._stub_policy(messages, model, ctx)


def _stub(problem, base):
    c = StubClient(policy=_stub_policy)
    c._state.update({"problem": problem, "condition": base, "is_frontier": False, "step": 0})
    return c


def _record(*, problem, condition, run_idx, model_name, run, score, built, fc) -> dict[str, Any]:
    rec = {
        "id": problem["id"], "topic": problem["topic"], "structure": problem["structure"],
        "condition": condition, "build_mode": built.mode, "run_idx": run_idx, "model": model_name,
        "answer_correct": score.answer_correct,
        "formula_status": fc.status, "formula_detail": fc.detail,
        "formula_correct": fc.ok,
        "n_symb_calls": built.n_symb_calls,
        "changed_by_verify": built.changed_by_verify,
        "build_prompt_tokens": built.prompt_tokens,
        "build_completion_tokens": built.completion_tokens,
        "build_latency_s": round(built.latency_s, 3),
        "card_parse_ok": built.parse_ok,
        "solve_completion_tokens": run.completion_tokens,
        "solve_latency_s": round(run.latency_s, 3),
        "stopped_reason": run.stopped_reason,
        "built_cards": built.cards,
        "initial_cards": built.initial_cards,
        "build_error": built.error,
    }
    rec.update(score.to_dict())
    return rec


def _summarize(records: list[dict[str, Any]]) -> str:
    from collections import defaultdict
    ans: dict[str, list[bool]] = defaultdict(list)
    frm: dict[str, list[bool]] = defaultdict(list)
    per: dict[tuple[str, str], dict[str, list[bool]]] = defaultdict(lambda: {"a": [], "f": []})
    symb: dict[str, list[int]] = defaultdict(list)
    for r in records:
        ans[r["condition"]].append(bool(r["answer_correct"]))
        frm[r["condition"]].append(bool(r["formula_correct"]))
        per[(r["id"], r["condition"])]["a"].append(bool(r["answer_correct"]))
        per[(r["id"], r["condition"])]["f"].append(bool(r["formula_correct"]))
        symb[r["condition"]].append(r["n_symb_calls"])

    def rate(xs): return f"{100*sum(xs)/len(xs):.0f}%" if xs else "–"

    L = ["# Eval 005 — deriving formulas with a symbolic tool", ""]
    L.append(f"Records: {len(records)}", )
    L.append("")
    L.append("## Headline: formula-correctness vs answer-correctness by condition")
    L.append("")
    L.append("| Condition | formula correct | answer correct | median SYMB calls | n |")
    L.append("|---|---|---|---|---|")
    for c in ORDER:
        if not ans.get(c):
            continue
        sc = sorted(symb[c]); med = sc[len(sc)//2] if sc else 0
        L.append(f"| {c} | {rate(frm[c])} | {rate(ans[c])} | {med} | {len(ans[c])} |")
    L.append("")
    L.append("*formula correct = SymPy-verified equivalence of the built card to the "
             "canonical formula (the real measure of derivation). answer correct = the "
             "numeric solve (can pass on a lucky number even if the formula is wrong).*")
    L.append("")
    L.append("## Per-problem formula-correctness")
    L.append("")
    L.append("| Problem | " + " | ".join(ORDER) + " |")
    L.append("|---|" + "---|" * len(ORDER))
    for pid in sorted({r["id"] for r in records}):
        cells = " | ".join(rate(per[(pid, c)]["f"]) for c in ORDER)
        L.append(f"| {pid} | {cells} |")
    L.append("")
    # formula-correct-but-answer-wrong and vice versa — the interesting disagreements
    fy_aw = sum(1 for r in records if r["formula_correct"] and not r["answer_correct"])
    fw_ay = sum(1 for r in records if (not r["formula_correct"]) and r["answer_correct"])
    L.append(f"Disagreements: formula-right/answer-wrong = {fy_aw}  ·  "
             f"formula-wrong/answer-right (lucky number) = {fw_ay}")
    L.append("")
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Eval 005 — deriving formulas with SymPy.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--problems", default=None)
    ap.add_argument("--n-runs", type=int, default=None)
    ap.add_argument("--conditions", default=None, help="Subset of build_text,build_sympy,build_verify.")
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--no-logs", action="store_true")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    cfg = runlib._load_config(config_path)
    problems_all = runlib._load_dataset(cfg)
    constants = runlib._load_dataset_constants(cfg)
    mu = float(constants.get("mu_earth", 3.986004418e14))
    specs = _specs(mu)

    # sources.json next to config
    src_path = config_path.resolve().parent / "sources.json"
    raw_src = json.loads(src_path.read_text())
    base_dir = src_path.parent
    sources = {pid: {"passage": (base_dir / s["passage"]).read_text(), "purpose": s["purpose"],
                     "passage_file": s["passage"]}
               for pid, s in raw_src.items() if not pid.startswith("_")}

    conditions = ([c.strip() for c in args.conditions.split(",")] if args.conditions else list(ORDER))
    n_runs = args.n_runs if args.n_runs is not None else cfg.get("n_runs", 3)
    wanted = ([p.strip() for p in args.problems.split(",")] if args.problems else list(sources.keys()))
    problems = [p for p in problems_all if p["id"] in set(wanted)]
    for p in problems:
        if p["id"] not in specs or p["id"] not in sources:
            raise SystemExit(f"{p['id']} missing a spec or source passage")
    if not problems:
        raise SystemExit(f"no problems matched {wanted}")

    local = cfg["local_model"]
    model_name = local["model_name"]
    provider = (local.get("provider") or "ollama").lower()
    solve_dt = bool(local.get("disable_thinking", True))         # solve: no-think
    build_dt = bool((cfg.get("card_build") or {}).get("disable_thinking", False))  # build: think
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
        print(f"warmup: {model_name} loaded in {w.get('latency_s','?')}s · keep_alive={w.get('keep_alive')}"
              if w.get("loaded") else f"warmup: skipped ({w.get('error')})")

    print(f"eval 005 — symbolic derivation · {len(problems)} problems × {len(conditions)} conditions × N={n_runs}")
    print(f"run dir: {run_dir}")
    print("-" * 78)

    records: list[dict[str, Any]] = []
    fh = out_path.open("a")
    cell = 0
    total = len(problems) * len(conditions) * n_runs
    t0 = time.monotonic()
    try:
        for problem in problems:
            spec = specs[problem["id"]]
            src = sources[problem["id"]]
            for condition in conditions:
                mode = CONDITIONS[condition]
                for run_idx in range(n_runs):
                    cell += 1
                    bclient = _stub(problem, "C3") if args.stub else client
                    built = build_cards_x(
                        client=bclient, model_name=("stub" if args.stub else model_name),
                        passage=src["passage"], purpose=src["purpose"], mode=mode,
                        temperature=temperature, max_tokens=build_max, disable_thinking=build_dt)
                    fc = _best_formula_check(built.cards, spec)

                    solve_problem = {**problem, "oracle_cards": built.cards}
                    solver = _stub(solve_problem, "C3") if args.stub else client
                    run = run_condition(
                        client=solver, model_name=("stub" if args.stub else model_name),
                        problem=solve_problem, condition="C3", temperature=temperature,
                        max_tool_turns=cfg.get("max_tool_turns", 8),
                        max_total_turns=cfg.get("max_total_turns", 16),
                        max_tokens=solve_max, disable_thinking=solve_dt,
                        calculator_extras=constants)
                    score = score_answer(
                        run.final_text, expected_value=problem["expected"]["value"],
                        expected_unit=problem["expected"]["unit"],
                        tolerance_rel=problem.get("tolerance_rel", cfg.get("default_tolerance_rel", 0.01)),
                        full_trace=run.full_trace)
                    rec = _record(problem=problem, condition=condition, run_idx=run_idx,
                                  model_name=("stub" if args.stub else model_name),
                                  run=run, score=score, built=built, fc=fc)
                    fh.write(json.dumps(rec) + "\n"); fh.flush()
                    records.append(rec)
                    if logs_dir is not None:
                        write_run_log(logs_dir=logs_dir, problem=problem, condition=condition,
                                      run_idx=run_idx, model_name=("stub" if args.stub else model_name),
                                      provider=("stub" if args.stub else provider),
                                      temperature=temperature, max_tokens=solve_max, run=run, score=score)
                    fmark = {"equivalent": "F✓"}.get(fc.status, f"F✗:{fc.status[:6]}")
                    amark = "A✓" if score.answer_correct else "A✗"
                    print(f"[{cell:>3}/{total}] {problem['id']:<11} {condition:<12} r{run_idx} "
                          f"{amark} {fmark:<12} symb={built.n_symb_calls} "
                          f"buildtok={built.completion_tokens}")
    finally:
        fh.close()
        if client is not None and hasattr(client, "close"):
            client.close()

    summary = _summarize(records)
    (run_dir / "summary.md").write_text(summary)
    runlib._update_latest_symlink(runlib._runs_root(cfg), run_dir)
    print("-" * 78)
    print(f"wrote {len(records)} runs in {time.monotonic()-t0:.1f}s -> {out_path}")
    print()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
