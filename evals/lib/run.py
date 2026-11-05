"""
Orchestrator: cross product of (problem × condition × run_idx) → runs.jsonl.

Usage:

    python -m src.run                              # default: from config.yaml
    python -m src.run --conditions C1,C2,C3,REF
    python -m src.run --conditions C1,C2,C3 --n-runs 2
    python -m src.run --stub                       # canned model, for wiring tests
    python -m src.run --no-judge                   # skip the LLM-as-judge method scoring
    python -m src.run --problems astro-001,astro-005
    python -m src.run -o results/runs.jsonl        # output path override

Each run appends a JSON line with:
  id, condition, run_idx, model, n_tool_calls, n_turns, latency_s,
  stopped_reason, error, parsed_value, parsed_unit, si_value,
  canonical_unit, expected_value, expected_unit, answer_correct,
  relative_error, right_magnitude_wrong_unit, unit_unknown,
  method_correct, method_judge_rationale, raw_output, full_trace.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Optional

from .conditions import run_condition
from .models import StubClient, make_client
from .progress import ProgressTracker
from .run_log import write_run_log
from .scoring import judge_method, score_answer


def _here() -> Path:
    return Path(__file__).resolve().parent.parent


# Resolved when --config is passed; otherwise defaults to 001's own root.
# Relative paths in cfg["paths"] resolve against this.
_CONFIG_DIR: Path = _here()


def _load_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    global _CONFIG_DIR
    cfg_path = config_path if config_path else (_here() / "config.yaml")
    _CONFIG_DIR = cfg_path.resolve().parent
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError("PyYAML required: pip install pyyaml") from e
    return yaml.safe_load(cfg_path.read_text())


def _resolve_path(rel: str) -> Path:
    """Resolve a path from cfg["paths"] against the loaded config's
    directory. Absolute paths pass through unchanged."""
    p = Path(rel)
    return p if p.is_absolute() else (_CONFIG_DIR / p).resolve()


# ---------------------------------------------------------------------------
# Run history layout.
#
# Every `make run` creates a fresh subdirectory under cfg.paths.runs_root:
#
#     results/
#       2026-06-01T14-30-22/        ← this run
#         runs.jsonl
#         logs/
#         summary.md
#         charts/
#       2026-06-01T16-45-09/        ← a later run
#         ...
#       latest -> 2026-06-01T16-45-09/   (symlink updated atomically)
#
# `analyze.py` defaults to `results/latest/` so the most recent run is
# always one command away. Older runs stay around for historical comparison.
# ---------------------------------------------------------------------------
import datetime as _dt


def _make_run_id() -> str:
    """Filesystem-safe timestamp (local time). `:` is reserved on Windows
    so we use `-` in the time portion."""
    return _dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def _runs_root(cfg: dict[str, Any]) -> Path:
    """Root directory under which timestamped run dirs are created."""
    rel = (cfg.get("paths") or {}).get("runs_root", "results")
    return _resolve_path(rel)


def _make_run_dir(cfg: dict[str, Any], run_id: str) -> Path:
    """Create and return `<runs_root>/<run_id>/`."""
    run_dir = _runs_root(cfg) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _update_latest_symlink(runs_root: Path, run_dir: Path) -> None:
    """Atomically point `<runs_root>/latest` at `run_dir`.

    Atomicity: write a temp symlink alongside, then `replace()`. On Windows
    the rename across a symlink may fail; we fall back to a plain unlink +
    symlink, which is non-atomic but acceptable for local-dev workflows.
    """
    latest = runs_root / "latest"
    target = run_dir.name   # relative — keeps the symlink portable if you move the tree
    tmp = runs_root / f".latest.{os.getpid()}.tmp"
    try:
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(target)
        tmp.replace(latest)
    except OSError:
        # Windows / non-symlink-capable FS — best effort.
        try:
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(target)
        except OSError:
            pass


def _load_dataset(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    p = _resolve_path(cfg["paths"]["dataset"])
    return json.loads(p.read_text())


def _load_dataset_constants(cfg: dict[str, Any]) -> dict[str, Any]:
    """If a `ground_truth.py` sits next to the dataset JSON, load its
    CONSTANTS dict and return it. These are passed to the calculator as
    `extras` so the agent can reference dataset-specific physical
    constants (e.g. `mu_earth`) inside CALC expressions.

    Returns {} if there's no ground_truth.py — datasets without numeric
    constants (text/eval suites, etc.) work fine with just math names.
    """
    dataset_path = _resolve_path(cfg["paths"]["dataset"])
    gt_path = dataset_path.parent / "ground_truth.py"
    if not gt_path.exists():
        return {}
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"_dataset_gt_{dataset_path.parent.name}", gt_path
    )
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(getattr(mod, "CONSTANTS", {}))


# ---------------------------------------------------------------------------
# Stub policy used when --stub is passed. It pretends to be a small model.
# It produces correct answers for half the dataset (single-formula problems)
# and incorrect answers for the rest, so analyze.py has interesting bars.
# ---------------------------------------------------------------------------
def _stub_policy(messages, model, ctx):
    """Single-call policy.

    - REF (frontier-ish): emit a number close to the expected via CALC, then
      FINAL_ANSWER.
    - 4B in C1: emit a plausible-but-wrong number.
    - 4B in C2/C3: one CALC, then FINAL_ANSWER.

    The policy reads `ctx["problem"]` and `ctx["condition"]` that the
    orchestrator stamps before calling.
    """
    problem = ctx.get("problem")
    condition = ctx.get("condition", "C1")
    is_frontier = ctx.get("is_frontier", False)

    if problem is None:
        # Method judge call from scoring.judge_method.
        return '{"method_correct": true, "rationale": "stub: assume correct"}'

    exp = problem["expected"]["value"]
    unit = problem["expected"]["unit"]
    step = ctx.get("step", 0)
    ctx["step"] = step + 1

    if condition == "C1":
        # Raw 4B: wrong-ish unless it's the very simplest topic.
        # Make "single" usually right, "multi" usually wrong, with some noise.
        wrong = problem.get("structure") == "multi"
        # frontier is much more reliable
        if is_frontier:
            wrong = False
        value = exp * (1.1 if wrong else 1.0)
        return f"After some reasoning, the answer is approximately {value:.4g}.\nFINAL_ANSWER: {value:.6g} {unit}"

    # C2 / C3 path: small model with a calculator. Single-step problems get
    # solved cleanly; multi-step problems still trip up in C2 without oracle.
    if step == 0:
        return f"Let me compute.\nCALC: {exp:.6g}"
    # After RESULT, commit. In C2 multi-step: commit to a wrong final.
    is_multi = problem.get("structure") == "multi"
    if condition == "C2" and is_multi and not is_frontier:
        value = exp * 1.2
    else:
        value = exp
    return f"FINAL_ANSWER: {value:.6g} {unit}"


def _make_stub_for(condition: str, problem: dict[str, Any], is_frontier: bool) -> StubClient:
    c = StubClient(policy=_stub_policy)
    c._state["problem"] = problem
    c._state["condition"] = condition
    c._state["is_frontier"] = is_frontier
    return c


# ---------------------------------------------------------------------------
# Run one (problem, condition, run_idx).
# ---------------------------------------------------------------------------
def _record_for_run(
    *,
    problem: dict[str, Any],
    condition: str,
    run_idx: int,
    model_name: str,
    run,
    score,
) -> dict[str, Any]:
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
        # Largest single-call context; at/above the model's num_ctx Ollama
        # silently truncated the prompt (see conditions.RunResult).
        "peak_context_tokens": getattr(run, "peak_context_tokens", 0),
        "raw_output": run.final_text,
        "full_trace": run.full_trace,
    }
    rec.update(score.to_dict())
    return rec


def _resolve_condition(
    condition: str,
    *,
    ref_condition: str,
    local_default_disable_think: bool,
) -> tuple[str, bool, Optional[bool]]:
    """Map a condition code to (base, is_frontier, disable_thinking_override).

    Suffixes:
      `_T`   → force thinking ON  (disable_thinking = False)
      `_NT`  → force thinking OFF (disable_thinking = True)
      none   → inherit local_model.disable_thinking

    Bases:
      C1 / C2 / C3        → local 4B
      REF                 → frontier on `ref_condition` prompt
      REF_C3              → frontier on C3 prompt

    Examples:
      C1        → ("C1",  False, None)        # uses local default
      C1_T      → ("C1",  False, False)       # force thinking ON
      C3_NT     → ("C3",  False, True)        # force thinking OFF
      REF       → (ref_condition, True, False)  # frontier never thinks
      REF_C3_T  → ("C3",  True,  False)       # accepted but irrelevant for frontier
    """
    # Strip thinking suffix if any. Look for the longest match first so
    # `_NT` beats `_T`.
    override: Optional[bool] = None
    if condition.endswith("_NT"):
        override = True
        condition = condition[:-3]
    elif condition.endswith("_T"):
        override = False
        condition = condition[:-2]

    if condition == "REF":
        return ref_condition, True, override
    if condition == "REF_C3":
        return "C3", True, override
    if condition in {"C1", "C2", "C3"}:
        return condition, False, override
    raise ValueError(f"unknown condition: {condition!r}")


def _preflight(cfg: dict[str, Any], stub: bool, use_judge: bool, conditions: list[str]) -> None:
    """Fail fast on the common preventable errors:
       - missing API-key env vars
       - Ollama-style local endpoint unreachable
    Stub mode skips everything."""
    if stub:
        return
    sections: list[tuple[str, dict[str, Any]]] = [("local_model", cfg["local_model"])]
    def _is_front(c: str) -> bool:
        for suf in ("_NT", "_T"):
            if c.endswith(suf):
                c = c[: -len(suf)]
                break
        return c in {"REF", "REF_C3"}
    if any(_is_front(c) for c in conditions) or use_judge:
        sections.append(("frontier_model", cfg["frontier_model"]))
    for label, sec in sections:
        env = sec.get("api_key_env")
        if env and not os.environ.get(env):
            raise SystemExit(
                f"missing env var ${env} for {label}. Export it before running, e.g.\n"
                f"    export {env}=<your-key>"
            )
    # Local Ollama smoke. Endpoint depends on the provider:
    #  - "ollama"        → /api/tags
    #  - "openai"-compat → /models  (i.e. /v1/models when base_url ends with /v1)
    local = cfg["local_model"]
    provider = (local.get("provider") or "openai").lower()
    base = (local.get("base_url") or "").rstrip("/")
    if "localhost" in base or "127.0.0.1" in base:
        if provider == "ollama":
            probe_url = base.removesuffix("/v1") + "/api/tags"
        elif provider in {"openai", "openrouter"}:
            probe_url = base + "/models"
        else:
            probe_url = None
        if probe_url:
            try:
                import urllib.request
                with urllib.request.urlopen(probe_url, timeout=2) as r:
                    r.read(1)
            except Exception as e:
                raise SystemExit(
                    f"local endpoint {base} unreachable ({type(e).__name__}).\n"
                    f"Is Ollama running?  `make ollama-up` or `ollama serve` then retry."
                )


def run_all(
    *,
    cfg: dict[str, Any],
    conditions: list[str],
    n_runs: int,
    problem_ids: Optional[Iterable[str]] = None,
    stub: bool = False,
    use_judge: bool = True,
    out_path: Path,
    logs_dir: Optional[Path] = None,
    max_tokens_override: Optional[int] = None,
    run_dir: Optional[Path] = None,
) -> int:
    rng = random.Random(cfg.get("seed", 1729))
    problems = _load_dataset(cfg)
    if problem_ids:
        wanted = set(problem_ids)
        problems = [p for p in problems if p["id"] in wanted]
        if not problems:
            print(f"no problems matched {sorted(wanted)}", file=sys.stderr)
            return 1

    # Resolve REF (frontier on `ref_condition`) and REF_C3 (frontier on C3
    # prompt unconditionally — the ceiling-with-oracle test).
    ref_condition = cfg.get("ref_condition", "C1")
    if ref_condition not in {"C1", "C2", "C3"}:
        raise ValueError(f"ref_condition must be C1/C2/C3, got {ref_condition!r}")

    _preflight(cfg, stub=stub, use_judge=use_judge, conditions=conditions)

    local_name = cfg["local_model"]["model_name"]
    frontier_name = cfg["frontier_model"]["model_name"]
    local_disable_think = bool(cfg["local_model"].get("disable_thinking", False))

    # Load dataset-specific constants for the calculator's namespace. This
    # is what makes `mu_earth`, `R_earth`, etc. callable from CALC lines
    # without baking astro names into the harness itself.
    dataset_constants = _load_dataset_constants(cfg)

    # Build the real clients ONCE per provider. Per-(problem, condition)
    # construction (the old behavior) created ~40 fresh httpx connection
    # pools per sweep, which leaked file descriptors and prevented Ollama's
    # keep_alive from working. The stub path still builds per-call clients
    # because each one carries problem-specific canned-response state.
    def _is_frontier_cond(c: str) -> bool:
        base = c
        for suf in ("_NT", "_T"):
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        return base in {"REF", "REF_C3"}

    real_local_client = None
    real_frontier_client = None
    if not stub:
        real_local_client = make_client(cfg["local_model"])
        # Load + pin the local model once before the sweep so the first cell
        # isn't a cold load and it stays resident (keep_alive) — no per-cell
        # reloads. Best-effort: never blocks the run.
        if hasattr(real_local_client, "warmup"):
            w = real_local_client.warmup(local_name)
            if w.get("loaded"):
                print(f"warmup: {local_name} loaded in {w['latency_s']}s · keep_alive={w['keep_alive']}")
            else:
                print(f"warmup: skipped ({w.get('error')})")
        needs_frontier = use_judge or any(_is_frontier_cond(c) for c in conditions)
        if needs_frontier:
            real_frontier_client = make_client(cfg["frontier_model"])

    # The judge always runs on the frontier (or its stub equivalent).
    judge_client = None
    judge_model = None
    if use_judge:
        if stub:
            judge_client = StubClient(policy=_stub_policy)
            judge_model = "stub-judge"
        else:
            judge_client = real_frontier_client
            judge_model = frontier_name

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate at start of a run; each invocation produces a fresh corpus.
    out_path.write_text("")

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)

    # Live progress tracker. Header up front so the user sees what's coming.
    all_problems = _load_dataset(cfg)
    n_singles = sum(1 for p in all_problems if p.get("structure") == "single")
    n_multi = sum(1 for p in all_problems if p.get("structure") == "multi")
    tracker = ProgressTracker(
        total=len(problems) * len(conditions) * n_runs,
        conditions=conditions,
    )
    tracker.header(
        n_problems_total=len(all_problems),
        n_problems_run=len(problems),
        n_singles=n_singles,
        n_multi=n_multi,
        n_runs=n_runs,
        ref_condition=ref_condition,
        local_label=(
            "stub"
            if stub
            else f"{cfg['local_model']['model_name']}  via  {cfg['local_model'].get('provider','openai')}"
        ),
        frontier_label=(
            None if stub or ("REF" not in conditions and not use_judge)
            else f"{cfg['frontier_model']['model_name']}  via  {cfg['frontier_model'].get('provider','openai')}"
        ),
        judge_enabled=use_judge,
        logs_dir=str(logs_dir) if logs_dir else None,
        stub=stub,
    )

    n_written = 0
    t_start = time.monotonic()
    try:
        fh = out_path.open("a")
        for problem in problems:
            tracker.problem_header(problem)
            for condition in conditions:
                resolved, is_frontier, think_override = _resolve_condition(
                    condition,
                    ref_condition=ref_condition,
                    local_default_disable_think=local_disable_think,
                )
                model_name = frontier_name if is_frontier else local_name
                model_cfg = cfg["frontier_model"] if is_frontier else cfg["local_model"]
                provider = (model_cfg.get("provider") or "openai").lower() if not stub else "stub"
                if stub:
                    client = _make_stub_for(resolved, problem, is_frontier=is_frontier)
                else:
                    client = real_frontier_client if is_frontier else real_local_client
                # disable_thinking resolution:
                #   frontier: always False (Qwen-specific token; no-op anyway)
                #   local + override:   use override (True/False)
                #   local + no override: inherit local_model.disable_thinking
                if is_frontier:
                    disable_think = False
                elif think_override is not None:
                    disable_think = think_override
                else:
                    disable_think = local_disable_think
                for run_idx in range(n_runs):
                    if stub:
                        # Reset per-run step so each stub call is fresh.
                        client._state["step"] = 0
                    run = run_condition(
                        client=client,
                        model_name=model_name,
                        problem=problem,
                        condition=resolved,
                        temperature=cfg.get("temperature", 0.2),
                        max_tool_turns=cfg.get("max_tool_turns", 8),
                        max_total_turns=cfg.get("max_total_turns", 16),
                        max_tokens=max_tokens_override or model_cfg.get("max_tokens"),
                        disable_thinking=disable_think,
                        calculator_extras=dataset_constants,
                        max_total_completion_tokens=cfg.get("max_completion_tokens"),
                    )
                    score = score_answer(
                        run.final_text,
                        expected_value=problem["expected"]["value"],
                        expected_unit=problem["expected"]["unit"],
                        tolerance_rel=problem.get("tolerance_rel", cfg.get("default_tolerance_rel", 0.01)),
                        full_trace=run.answer_trace,
                    )
                    if use_judge and run.full_trace:
                        mc, rationale = judge_method(
                            judge_client=judge_client,
                            judge_model=judge_model,
                            problem_statement=problem["statement"],
                            canonical_method=problem["canonical_method"],
                            model_trace=run.full_trace,
                        )
                        score.method_correct = mc
                        score.method_judge_rationale = rationale
                    rec = _record_for_run(
                        problem=problem,
                        condition=condition,
                        run_idx=run_idx,
                        model_name=model_name,
                        run=run,
                        score=score,
                    )
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
                    n_written += 1
                    if logs_dir is not None:
                        write_run_log(
                            logs_dir=logs_dir,
                            problem=problem,
                            condition=condition,
                            run_idx=run_idx,
                            model_name=model_name,
                            provider=provider,
                            temperature=cfg.get("temperature", 0.2),
                            max_tokens=model_cfg.get("max_tokens"),
                            run=run,
                            score=score,
                        )
                    tracker.emit_run(
                        problem=problem,
                        condition=condition,
                        run_idx=run_idx,
                        score=score,
                        run=run,
                    )
        fh.close()
    finally:
        for c in (real_local_client, real_frontier_client):
            if c is not None and hasattr(c, "close"):
                c.close()
        tracker.footer()

    dt = time.monotonic() - t_start
    print(f"wrote {n_written} runs in {dt:.1f}s -> {out_path}")

    # Successful run → point `<runs_root>/latest` at this run's directory so
    # `make analyze` / future invocations of analyze.py find it without
    # having to know the timestamp. Skipped if the user overrode --out or
    # otherwise wrote outside the standard run_dir layout.
    if run_dir is not None and out_path.is_relative_to(run_dir):
        _update_latest_symlink(_runs_root(cfg), run_dir)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run all conditions × N runs over the dataset.")
    ap.add_argument("--conditions", default=None, help="Comma-separated subset of C1,C2,C3,REF.")
    ap.add_argument("--n-runs", type=int, default=None, help="Override n_runs from config.")
    ap.add_argument("--problems", default=None, help="Comma-separated problem ids to restrict to.")
    ap.add_argument("--stub", action="store_true", help="Use stub clients; no API keys needed.")
    ap.add_argument("--no-judge", action="store_true", help="Skip LLM-as-judge method scoring.")
    ap.add_argument("-o", "--out", default=None, help="Output path for runs.jsonl.")
    ap.add_argument("--logs-dir", default=None, help="Directory for per-run text logs. Defaults to cfg.paths.logs.")
    ap.add_argument("--no-logs", action="store_true", help="Skip per-run text log files.")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="Override max_tokens for all model calls. Useful for thinking-mode runs.")
    ap.add_argument("--config", default=None,
                    help="Path to an alternate config.yaml. Relative paths in that file's "
                         "`paths:` section resolve relative to its directory.")
    args = ap.parse_args(argv)

    cfg = _load_config(Path(args.config) if args.config else None)
    conditions = (args.conditions or ",".join(cfg.get("conditions", ["C1", "C2", "C3", "REF"]))).split(",")
    conditions = [c.strip() for c in conditions if c.strip()]
    n_runs = args.n_runs if args.n_runs is not None else cfg.get("n_runs", 5)
    problem_ids = (args.problems or "").split(",") if args.problems else None
    if problem_ids:
        problem_ids = [p.strip() for p in problem_ids if p.strip()]

    # Timestamped run-history layout:
    #   <runs_root>/<run_id>/{runs.jsonl, logs/, summary.md, charts/}
    # `--out` / `--logs-dir` still override individually when set (useful for
    # ad-hoc runs that shouldn't pollute the history).
    run_id = _make_run_id()
    run_dir = _make_run_dir(cfg, run_id)

    out_path = Path(args.out) if args.out else (run_dir / "runs.jsonl")
    if args.no_logs:
        logs_dir = None
    elif args.logs_dir:
        logs_dir = Path(args.logs_dir)
    else:
        logs_dir = run_dir / "logs"

    return run_all(
        cfg=cfg,
        conditions=conditions,
        n_runs=n_runs,
        problem_ids=problem_ids,
        stub=args.stub,
        use_judge=not args.no_judge,
        out_path=out_path,
        logs_dir=logs_dir,
        run_dir=run_dir,
        max_tokens_override=args.max_tokens,
    )


if __name__ == "__main__":
    raise SystemExit(main())
