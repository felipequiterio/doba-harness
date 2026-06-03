"""
Per-run text log writer.

One file per (problem, condition, run_idx) at
`results/logs/<id>__<condition>__run<i>.log`. Plain text, human-skimmable:

  - header: problem, condition, run, model, timestamp
  - config slice: temperature, max_tokens, tolerance
  - tokens & cost: per-turn + run total + overall tk/s
  - score: answer_correct, method_correct, parsed value, relative error
  - full message history with each assistant turn annotated with tk/s

The JSONL record in runs.jsonl is the analysis input; these logs are for
human inspection — when you want to *read* what the model actually did.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from .conditions import RunResult, TurnRecord
from .scoring import Score


_BAR = "=" * 78
_THIN = "-" * 78

# One-line description per experimental condition. Surfaced in every log
# header so a log is self-explanatory without reading the README.
CONDITION_DESCRIPTIONS: dict[str, str] = {
    "C1":     "raw local model — NO calculator, NO oracle knowledge injected",
    "C2":     "local model + calculator tool (CALC/RESULT) — NO oracle knowledge",
    "C3":     "local model + calculator + ORACLE formula card(s) injected",
    "C3_oracle": "local model + calculator + ORACLE (human-written) formula card(s) — the ceiling",
    "C3_self": "local model + calculator + SELF-BUILT card(s): the 4B read the source passage and wrote the card",
    "REF":    "frontier model = ceiling reference (default: C1 prompt; configurable via ref_condition)",
    "REF_C3": "frontier model + calculator + ORACLE — ceiling WITH knowledge",
}


def describe_condition(name: str) -> str:
    """Return a one-line description for any condition code, including the
    thinking-suffix variants `_T` (force thinking ON) and `_NT` (force OFF)."""
    suffix_note = ""
    base = name
    if base.endswith("_NT"):
        base = base[:-3]
        suffix_note = "  [thinking OFF — /no_think + think:false]"
    elif base.endswith("_T"):
        base = base[:-2]
        suffix_note = "  [thinking ON — Qwen3 reasoning mode]"
    base_desc = CONDITION_DESCRIPTIONS.get(base, "(unknown condition)")
    return base_desc + suffix_note


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe(s: str) -> str:
    # Make a string safe to embed in a filename (problem ids already are, but
    # be defensive against future renames).
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def log_path(logs_dir: Path, problem_id: str, condition: str, run_idx: int) -> Path:
    return logs_dir / f"{_safe(problem_id)}__{_safe(condition)}__run{run_idx}.log"


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_float(x: float, places: int = 2) -> str:
    return f"{x:.{places}f}"


def _format_turn_block(idx: int, turn: TurnRecord) -> str:
    annot = (
        f"  [t={_fmt_float(turn.latency_s, 2)}s  "
        f"prompt={_fmt_int(turn.prompt_tokens)}  "
        f"completion={_fmt_int(turn.completion_tokens)}  "
        f"tk/s={_fmt_float(turn.tokens_per_second, 1)}]"
    )
    return f"[assistant #{idx}]{annot}\n{turn.text.rstrip()}\n"


def _format_message(role: str, content: str) -> str:
    return f"[{role}]\n{content.rstrip()}\n"


def write_run_log(
    *,
    logs_dir: Path,
    problem: dict[str, Any],
    condition: str,
    run_idx: int,
    model_name: str,
    provider: str,
    temperature: float,
    max_tokens: int | None,
    run: RunResult,
    score: Score,
) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = log_path(logs_dir, problem["id"], condition, run_idx)

    expected = problem.get("expected", {})
    tol = problem.get("tolerance_rel")

    overall_tps = run.tokens_per_second
    sum_call_latency = sum(t.latency_s for t in run.turn_records)

    lines: list[str] = []
    desc = describe_condition(condition)
    lines.append(_BAR)
    lines.append(f"PROBLEM:    {problem['id']}  ({problem.get('topic')}, {problem.get('structure')})")
    lines.append(f"CONDITION:  {condition}  —  {desc}")
    lines.append(f"RUN:        {run_idx}")
    lines.append(f"MODEL:      {model_name}  (provider: {provider})")
    lines.append(f"KNOWLEDGE:  {problem.get('knowledge_label')}")
    lines.append(f"TIMESTAMP:  {_ts()}")
    lines.append(_BAR)
    lines.append(f"STATEMENT:  {problem.get('statement','')[:300]}")
    if expected:
        lines.append(f"EXPECTED:   {expected.get('value')} {expected.get('unit')}  (tolerance_rel={tol})")
    lines.append(f"TEMPERATURE: {temperature}  ·  MAX_TOKENS: {max_tokens}")
    lines.append(_THIN)
    lines.append(
        f"TOKENS:     prompt={_fmt_int(run.prompt_tokens)}  "
        f"completion={_fmt_int(run.completion_tokens)}  "
        f"total={_fmt_int(run.total_tokens)}"
    )
    lines.append(
        f"LATENCY:    {_fmt_float(run.latency_s, 2)}s wall  ·  "
        f"{_fmt_float(sum_call_latency, 2)}s in model calls  ·  "
        f"overall {_fmt_float(overall_tps, 1)} tk/s (completion)"
    )
    lines.append(
        f"TURNS:      {run.n_turns} assistant turn(s)  ·  {run.n_tool_calls} calculator call(s)"
    )
    lines.append(f"STOPPED:    {run.stopped_reason}")
    if run.error:
        lines.append(f"ERROR:      {run.error}")
    lines.append(_THIN)
    lines.append(
        f"SCORE:      answer_correct={score.answer_correct}  "
        f"method_correct={score.method_correct}  "
        f"rel_err={_fmt_float(score.relative_error or 0.0, 4) if score.relative_error is not None else '–'}"
    )
    lines.append(
        f"PARSED:     {score.parsed_value} {score.parsed_unit}  "
        f"-> {score.si_value} {score.canonical_unit} (SI)"
    )
    if score.right_magnitude_wrong_unit:
        lines.append("            ⚠ right magnitude, wrong/missing unit")
    if score.method_judge_rationale:
        lines.append(f"JUDGE:      {score.method_judge_rationale}")
    lines.append(_BAR)
    lines.append("MESSAGES")
    lines.append(_BAR)

    # Walk the raw messages and inline TurnRecord stats for each assistant turn.
    assistant_idx = 0
    for m in run.raw_messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if role == "assistant" and assistant_idx < len(run.turn_records):
            lines.append(_format_turn_block(assistant_idx, run.turn_records[assistant_idx]))
            assistant_idx += 1
        else:
            lines.append(_format_message(role, content))
        lines.append("")

    path.write_text("\n".join(lines))
    return path
