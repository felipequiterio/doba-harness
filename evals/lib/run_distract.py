"""
run_distract — orchestrator for eval 006 (distractor retrieval / card selection).

Every eval so far handed the model the EXACTLY-right card. Real retrieval
returns a top-k with WRONG cards mixed in. This eval injects the correct
oracle card(s) plus N plausible-but-wrong distractors (the most confusable
cards from OTHER problems — ranked by shared variable symbols) and asks:

  - how fast does answer-correctness degrade as N grows?
  - does a SELECT step (model picks the relevant card, then solves with only
    that) recover the loss?

Conditions:
  D0          correct card(s) only.                  (== existing C3, baseline)
  D2          correct + 2 distractors.
  D4          correct + 4 distractors.
  D4_select   correct + 4 distractors, but the model first SELECTS which
              card(s) are relevant, then solves with only those.

All injection goes through the existing oracle path; ground truth is still
code-computed. Distractor set and card order are deterministic (seeded).

Run:
    cd evals && uv run python -m lib.run_distract --config 006-distractor-retrieval/config.yaml \
        --problems astro-016,astro-027,astro-005 --n-runs 3
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import run as runlib
from .conditions import run_condition
from .models import StubClient, make_client
from .run_log import write_run_log
from .scoring import score_answer

# condition -> (n_distractors, with_select, n_nearmiss)
#   quick set: D0..D4_select  (generic distractors, low load)
#   run set:   D8, NM*        (high load + near-miss — the breaking-point sweep)
CONDITIONS: dict[str, tuple[int, bool, int]] = {
    "D0": (0, False, 0),
    "D2": (2, False, 0),
    "D4": (4, False, 0),
    "D4_select": (4, True, 0),
    "D8": (8, False, 0),            # high generic load (isolates the load axis)
    "NM": (0, False, 1),           # correct + 1 near-miss only (isolates the near-miss)
    "NM8": (8, False, 1),          # near-miss buried in high load (realistic worst case)
    "NM8_select": (8, True, 1),    # + select: does it reject the near-miss?
}
ORDER = ["D0", "D2", "D4", "D4_select", "D8", "NM", "NM8", "NM8_select"]
DEFAULT_CONDS = ["D0", "D2", "D4", "D4_select"]    # `make quick`; `make run` passes the rest


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _card_vars(card: dict[str, Any]) -> set[str]:
    return {_norm(k) for k in (card.get("variables") or {}).keys()}


def _build_distractor_pool(target: dict[str, Any], all_problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cards from OTHER problems, ranked most-confusable first (by shared
    variable symbols with the target's correct cards). A card whose equation
    matches a correct card is excluded — that's a duplicate, not a distractor."""
    correct = target.get("oracle_cards") or []
    correct_eqs = {_norm(c["equation"]) for c in correct}
    target_vars: set[str] = set().union(*[_card_vars(c) for c in correct]) if correct else set()
    scored: list[tuple[int, str, dict]] = []
    seen_eqs: set[str] = set(correct_eqs)                       # dedupe by formula
    seen_names: set[str] = {_norm(c.get("name", "")) for c in correct}  # and by name
    for p in all_problems:
        if p["id"] == target["id"]:
            continue
        for c in p.get("oracle_cards") or []:
            eq, nm = _norm(c["equation"]), _norm(c.get("name", ""))
            if eq in seen_eqs or nm in seen_names:   # no dup formulas, no dup/colliding names
                continue
            seen_eqs.add(eq); seen_names.add(nm)
            overlap = len(_card_vars(c) & target_vars)
            scored.append((overlap, p["id"] + "/" + c.get("name", ""), c))
    # most overlap first; tie-break by stable key for determinism
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in scored]


def _assemble_cards(target: dict[str, Any], pool: list[dict[str, Any]], n_d: int,
                    nearmiss: list[dict[str, Any]], n_nm: int, seed: str) -> list[dict[str, Any]]:
    correct = list(target.get("oracle_cards") or [])
    cards = correct + list(nearmiss[:n_nm]) + pool[:n_d]
    random.Random(seed).shuffle(cards)   # don't let the correct card sit first
    return cards


def _load_nearmiss(config_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Near-miss distractor cards (subtly-wrong same-quantity formulas), from
    nearmiss.json next to the config. Empty if the file is absent."""
    p = config_path.resolve().parent / "nearmiss.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Select step: model picks the relevant card name(s) from the candidate set.
# ---------------------------------------------------------------------------
_SELECT_SYS = (
    "You are selecting reference formulas. You are given a problem and several "
    "candidate formula cards, some relevant and some not. Identify ONLY the "
    "card(s) actually needed to solve the problem."
)


def _select_cards(*, client, model_name, problem, cards, temperature, disable_thinking):
    listing = "\n".join(f"- {c.get('name','?')}: {c.get('equation','')}" for c in cards)
    user = (
        f"PROBLEM:\n{problem['statement']}\n\n"
        f"CANDIDATE FORMULA CARDS:\n{listing}\n\n"
        "List the name(s) of ONLY the card(s) needed to solve this problem, one "
        "per line, exactly as written above. Do not solve the problem."
    )
    resp = client.chat(model=model_name, messages=[
        {"role": "system", "content": _SELECT_SYS},
        {"role": "user", "content": user},
    ], temperature=temperature, max_tokens=512, disable_thinking=disable_thinking)
    reply_n = _norm(resp.text)
    selected = [c for c in cards if _norm(c.get("name", "")) and _norm(c["name"]) in reply_n]
    return selected, resp


def _stub_policy(messages, model, ctx):
    sys_txt = messages[0]["content"] if messages else ""
    if "selecting reference formulas" in sys_txt:
        # stub: pick the first card name listed (correct-ish behaviour varies)
        m = re.search(r"- (\S+):", messages[-1]["content"])
        return m.group(1) if m else ""
    return runlib._stub_policy(messages, model, ctx)


def _stub(problem):
    c = StubClient(policy=_stub_policy)
    c._state.update({"problem": problem, "condition": "C3", "is_frontier": False, "step": 0})
    return c


def _correct_names(target):
    return {_norm(c.get("name", "")) for c in (target.get("oracle_cards") or [])}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Eval 006 — distractor retrieval.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--problems", default=None)
    ap.add_argument("--n-runs", type=int, default=None)
    ap.add_argument("--conditions", default=None)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--no-logs", action="store_true")
    args = ap.parse_args(argv)

    config_path = Path(args.config)
    cfg = runlib._load_config(config_path)
    all_problems = runlib._load_dataset(cfg)
    constants = runlib._load_dataset_constants(cfg)
    nearmiss = _load_nearmiss(config_path)

    conditions = ([c.strip() for c in args.conditions.split(",")] if args.conditions else list(DEFAULT_CONDS))
    n_runs = args.n_runs if args.n_runs is not None else cfg.get("n_runs", 3)
    wanted = [p.strip() for p in args.problems.split(",")] if args.problems else None
    problems = [p for p in all_problems if (wanted is None or p["id"] in set(wanted))]
    problems = [p for p in problems if p.get("oracle_cards")]
    if not problems:
        raise SystemExit("no problems with oracle_cards matched")

    local = cfg["local_model"]
    model_name = local["model_name"]
    provider = (local.get("provider") or "ollama").lower()
    solve_dt = bool(local.get("disable_thinking", True))
    solve_max = args.max_tokens or local.get("max_tokens")
    temperature = cfg.get("temperature", 0.2)
    seed = str(cfg.get("seed", 1729))

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

    pools = {p["id"]: _build_distractor_pool(p, all_problems) for p in problems}
    print(f"eval 006 — distractor retrieval · {len(problems)} problems × {len(conditions)} conditions × N={n_runs}")
    print(f"run dir: {run_dir}")
    print("-" * 78)

    records: list[dict[str, Any]] = []
    fh = out_path.open("a")
    cell = 0
    total = len(problems) * len(conditions) * n_runs
    t0 = time.monotonic()
    try:
        for problem in problems:
            pool = pools[problem["id"]]
            correct_names = _correct_names(problem)
            nm_cards = nearmiss.get(problem["id"], [])
            for condition in conditions:
                n_d, with_select, n_nm = CONDITIONS[condition]
                if n_nm > 0 and len(nm_cards) < n_nm:
                    raise SystemExit(f"{condition} needs {n_nm} near-miss card(s) for {problem['id']}; "
                                     f"add them to nearmiss.json")
                nm_names = {_norm(c["name"]) for c in nm_cards[:n_nm]}
                for run_idx in range(n_runs):
                    cell += 1
                    cset = _assemble_cards(problem, pool, n_d, nm_cards, n_nm,
                                           seed=f"{seed}-{problem['id']}-{condition}-{run_idx}")
                    solver = _stub(problem) if args.stub else client

                    selected_names = None
                    selection_correct = None
                    selected_nearmiss = None
                    sel_tokens = 0
                    inject = cset
                    if with_select:
                        selected, sresp = _select_cards(
                            client=solver, model_name=("stub" if args.stub else model_name),
                            problem=problem, cards=cset, temperature=temperature, disable_thinking=solve_dt)
                        sel_tokens = sresp.completion_tokens
                        selected_names = [c.get("name") for c in selected]
                        inject = selected if selected else []   # solve with what it picked
                        sel_norm = {_norm(n) for n in selected_names}
                        selection_correct = correct_names.issubset(sel_norm)
                        selected_nearmiss = bool(nm_names & sel_norm)   # did it wrongly pick a near-miss?

                    solve_problem = {**problem, "oracle_cards": inject}
                    run = run_condition(
                        client=solver, model_name=("stub" if args.stub else model_name),
                        problem=solve_problem, condition="C3", temperature=temperature,
                        max_tool_turns=cfg.get("max_tool_turns", 8),
                        max_total_turns=cfg.get("max_total_turns", 16),
                        max_tokens=solve_max, disable_thinking=solve_dt, calculator_extras=constants)
                    score = score_answer(
                        run.final_text, expected_value=problem["expected"]["value"],
                        expected_unit=problem["expected"]["unit"],
                        tolerance_rel=problem.get("tolerance_rel", cfg.get("default_tolerance_rel", 0.01)),
                        full_trace=run.full_trace)

                    rec = {
                        "id": problem["id"], "topic": problem["topic"], "structure": problem["structure"],
                        "knowledge_label": problem.get("knowledge_label"),
                        "condition": condition, "n_distractors": n_d, "n_nearmiss": n_nm,
                        "with_select": with_select,
                        "run_idx": run_idx, "model": ("stub" if args.stub else model_name),
                        "answer_correct": score.answer_correct,
                        "n_cards_injected": len(inject), "n_cards_candidate": len(cset),
                        "selected_names": selected_names, "selection_correct": selection_correct,
                        "selected_nearmiss": selected_nearmiss, "select_tokens": sel_tokens,
                        "solve_completion_tokens": run.completion_tokens, "n_tool_calls": run.n_tool_calls,
                        "stopped_reason": run.stopped_reason,
                        "candidate_card_names": [c.get("name") for c in cset],
                    }
                    rec.update(score.to_dict())
                    fh.write(json.dumps(rec) + "\n"); fh.flush()
                    records.append(rec)
                    if logs_dir is not None:
                        write_run_log(logs_dir=logs_dir, problem=problem, condition=condition,
                                      run_idx=run_idx, model_name=("stub" if args.stub else model_name),
                                      provider=("stub" if args.stub else provider),
                                      temperature=temperature, max_tokens=solve_max, run=run, score=score)
                    amark = "✓" if score.answer_correct else "✗"
                    smark = ""
                    if with_select:
                        smark = f"  select={'ok' if selection_correct else 'MISS'}({len(inject)}/{len(cset)})"
                    print(f"[{cell:>3}/{total}] {problem['id']:<11} {condition:<10} r{run_idx} "
                          f"{amark}  cards={len(cset)}{smark}")
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


def _summarize(records: list[dict[str, Any]]) -> str:
    from collections import defaultdict
    byc = defaultdict(list)
    per = defaultdict(list)
    for r in records:
        byc[r["condition"]].append(bool(r["answer_correct"]))
        per[(r["id"], r["condition"])].append(bool(r["answer_correct"]))

    def rate(xs): return f"{100*sum(xs)/len(xs):.0f}%" if xs else "–"

    conds = [c for c in ORDER if byc.get(c)]

    def pct(c): return 100*sum(byc[c])/len(byc[c]) if byc.get(c) else None

    L = ["# Eval 006 — distractor retrieval", "", f"Records: {len(records)}", ""]
    L.append("## Answer-correctness by condition")
    L.append("")
    L.append("| Condition | cards mixed in | answer correct | n |")
    L.append("|---|---|---|---|")
    for c in conds:
        nd, ws, nm = CONDITIONS[c]
        tag = f"{nd} generic" + (f" + {nm} near-miss" if nm else "") + (" + select" if ws else "")
        L.append(f"| {c} | {tag} | {rate(byc[c])} | {len(byc[c])} |")
    L.append("")
    # deltas, only where both sides ran
    def delta(a, b, label):
        if pct(a) is not None and pct(b) is not None:
            L.append(f"- **{label} ({a} − {b}): {pct(a)-pct(b):+.0f} pp**")
    delta("D4", "D0", "Distractor cost — 4 generic wrong cards")
    delta("D8", "D0", "Distractor cost — 8 generic wrong cards (load axis)")
    delta("NM", "D0", "Near-miss cost — 1 subtly-wrong same-quantity card")
    delta("NM8", "D8", "Near-miss-under-load — adding the near-miss to 8 generic")
    delta("D4_select", "D4", "Selection recovery (D4)")
    delta("NM8_select", "NM8", "Selection recovery (near-miss under load)")
    # selection quality
    sel = [r for r in records if r["with_select"] and r["selection_correct"] is not None]
    if sel:
        acc = 100*sum(1 for r in sel if r["selection_correct"])/len(sel)
        L.append(f"- **Selection accuracy: {acc:.0f}%** — picked the needed card(s) ({len(sel)} select cells).")
    nmsel = [r for r in records if r["with_select"] and r.get("selected_nearmiss") is not None and r["n_nearmiss"]]
    if nmsel:
        grabbed = 100*sum(1 for r in nmsel if r["selected_nearmiss"])/len(nmsel)
        L.append(f"- **Near-miss wrongly selected: {grabbed:.0f}%** — how often select grabbed the near-miss ({len(nmsel)} cells; lower is better).")
    L.append("")
    L.append("## Per-problem answer-correctness")
    L.append("")
    L.append("| Problem | know | " + " | ".join(conds) + " |")
    L.append("|---|---|" + "---|" * len(conds))
    for pid in sorted({r["id"] for r in records}):
        kl = next((r["knowledge_label"] for r in records if r["id"] == pid), None)
        cells = " | ".join(rate(per[(pid, c)]) for c in conds)
        L.append(f"| {pid} | {kl} | {cells} |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
