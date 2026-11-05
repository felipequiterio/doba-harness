"""
Condition assembly: turns a (problem, condition, client) tuple into a chat
exchange and returns the trace + a parsed FINAL_ANSWER.

Conditions:
  - C1  raw 4B, no tools, no oracle.
  - C2  4B + calculator (CALC/RESULT protocol).
  - C3  4B + calculator + oracle formula card(s) injected.
  - REF frontier model, by default on C1 prompt. Configurable.

The loop is intentionally small. It's the harness for an experiment, not an
agent framework.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .calculator import (
    Calculator,
    evaluate_text,
    extract_calc_calls,
    has_final_answer,
)


# ---------------------------------------------------------------------------
# System prompts. Stable contract: "end with FINAL_ANSWER: <value> <unit>".
# ---------------------------------------------------------------------------
_SYS_BASE = (
    "You are solving an astrodynamics problem. "
    "Reason step by step in plain text.\n"
    "\n"
    "CRITICAL OUTPUT REQUIREMENT — read carefully:\n"
    "  The last line of your message MUST be exactly:\n"
    "      FINAL_ANSWER: <number> <SI unit>\n"
    "  Nothing after this line. No commentary, no sanity checks, no "
    "restatement, no extra blank lines. Stop generating immediately after "
    "the unit. If you skip this line your answer is graded as wrong even "
    "if your reasoning was correct.\n"
    "\n"
    "Use SI units (m, m/s, s, rad, J/kg, etc.). Do not include "
    "uncertainties or ranges. One number, one unit."
)

_SYS_WITH_CALC = (
    _SYS_BASE
    + "\n\n"
    "You have a calculator. To use it, write a line starting with `CALC:` "
    "followed by a Python-style math expression on one line. Examples:\n"
    "  CALC: sqrt(3.986e14 / 6778137)\n"
    "  CALC: 2 * 7669 * sin(radians(15)/2)\n"
    "Available functions: sqrt, abs, sin, cos, tan, asin, acos, atan, atan2, "
    "exp, log, log10, log2, pow, floor, ceil, radians, degrees, min, max, round. "
    "Available constants: mu_earth (Earth's GM, m^3/s^2), R_earth (m), G, pi, e, "
    "mu_mars, R_mars, mu_sun, AU, a_earth_helio, a_mars_helio, a_moon_geo, "
    "sidereal_day, sidereal_year, g0, J2_earth, m_earth_over_m_sun.\n"
    "\n"
    "RULES — important:\n"
    " 1. After a `CALC:` line, STOP your turn and wait. The system will reply "
    "with a `RESULT:` line containing the real numeric value. Do NOT write "
    "your own `RESULT:` line — anything you write is just text, not a real "
    "computation, and will be ignored.\n"
    " 2. Do NOT emit `FINAL_ANSWER` in the same turn as a `CALC:` — your "
    "answer will be based on numbers you guessed, not numbers the calculator "
    "produced. Wait for the RESULT, then commit on the next turn.\n"
    " 3. Once you have your numeric answer FROM REAL RESULT LINES, emit "
    "`FINAL_ANSWER: <value> <unit>` and stop."
)


def _oracle_block(problem: dict[str, Any]) -> str:
    cards = problem.get("oracle_cards") or []
    if not cards:
        return ""
    out = ["REFERENCE FORMULAS (use these; they are correct):"]
    for c in cards:
        out.append(f"- {c['name']}")
        out.append(f"  equation: {c['equation']}")
        if c.get("variables"):
            out.append("  where:")
            for sym, desc in c["variables"].items():
                out.append(f"    {sym} = {desc}")
        if c.get("when"):
            out.append(f"  applies when: {c['when']}")
    return "\n".join(out)


def build_messages(
    problem: dict[str, Any],
    condition: str,
    *,
    disable_thinking: bool = False,
) -> list[dict[str, str]]:
    """Build the initial chat messages for a (problem, condition) pair.

    `disable_thinking=True` appends Qwen3's `/no_think` token to the end of
    the user message — Qwen's chat template only honors it there, NOT in
    system. (System-prompt placement was a previous attempt that the model
    silently ignored.) The Ollama-side `think: false` request flag in
    config.yaml is the primary lever; this is the in-prompt fallback for
    models that don't recognize the flag yet.
    No effect on non-Qwen models (the literal token is benign).
    """
    if condition == "C1":
        sys = _SYS_BASE
        user = problem["statement"]
    elif condition == "C2":
        sys = _SYS_WITH_CALC
        user = problem["statement"]
    elif condition == "C3":
        sys = _SYS_WITH_CALC
        oracle = _oracle_block(problem)
        user = (
            problem["statement"]
            + ("\n\n" + oracle if oracle else "")
        )
    elif condition == "REF":
        # Configurable in `run.py`; defaults to C1 prompt. We rebuild the
        # underlying condition's messages there.
        raise ValueError("Use build_messages with the resolved ref_condition (C1 or C3).")
    else:
        raise ValueError(f"unknown condition: {condition!r}")
    if disable_thinking:
        user = user + "\n\n/no_think"
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# The loop.
# ---------------------------------------------------------------------------
@dataclass
class TurnRecord:
    """One assistant turn's metadata. Kept alongside the message itself so
    the per-run log can annotate every assistant message with tk/s + cost.

    `text` is the full turn as logged (may include a `<think>` block);
    `answer` is the reasoning-free content the harness actually parsed."""
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    answer: str = ""

    @property
    def tokens_per_second(self) -> float:
        if self.latency_s <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / self.latency_s


@dataclass
class RunResult:
    condition: str
    problem_id: str
    raw_messages: list[dict[str, str]]
    final_text: str               # the last assistant message's answer (reasoning stripped) — what gets scored
    full_trace: str               # concatenation of all assistant messages incl. <think> (for logs + the judge)
    n_tool_calls: int
    n_turns: int                  # assistant turns
    stopped_reason: str           # "final_answer" | "max_turns" | "max_tool_turns" | "error"
    latency_s: float              # wall-clock for the whole loop
    turn_records: list[TurnRecord] = field(default_factory=list)
    prompt_tokens: int = 0        # summed across all turns
    completion_tokens: int = 0
    error: Optional[str] = None
    # Concatenated answers only (no <think>) — the haystack for salvage, so a
    # number the model merely considered while thinking is never scored.
    answer_trace: str = ""
    # Largest prompt+completion of any single call. Compare against the
    # model's num_ctx: at or above it, Ollama silently truncated the context.
    peak_context_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def tokens_per_second(self) -> float:
        """Overall completion tk/s across all assistant turns."""
        total_gen_time = sum(t.latency_s for t in self.turn_records)
        if total_gen_time <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / total_gen_time


def run_condition(
    *,
    client,
    model_name: str,
    problem: dict[str, Any],
    condition: str,
    temperature: float,
    max_tool_turns: int,
    max_total_turns: int,
    calculator_extras: Optional[dict[str, Any]] = None,
    max_tokens: Optional[int] = None,
    disable_thinking: bool = False,
    max_total_completion_tokens: Optional[int] = None,
) -> RunResult:
    """Drive a single (problem, condition) run to completion.

    `max_total_completion_tokens` bounds the thinking-loop runaway: thinking-on
    multi-step solves burn ~3k tokens/turn and can accumulate to 30k+ across
    turns without committing (eval 008 saw a 36,773-token run produce garbage).
    When the cumulative completion budget is hit, the harness forces ONE clean
    NO-THINK commit and stops. None = no cap (back-compatible)."""
    # Resolve REF → underlying condition before we get here.
    if condition not in {"C1", "C2", "C3"}:
        raise ValueError(f"run_condition expects C1/C2/C3, got {condition!r}")

    use_calc = condition in {"C2", "C3"}
    calc = Calculator.default(calculator_extras) if use_calc else None

    messages = build_messages(problem, condition, disable_thinking=disable_thinking)
    turns: list[TurnRecord] = []
    n_tool_calls = 0
    stopped = "max_turns"
    error: Optional[str] = None
    prodded = False

    def call_model(force_no_think: bool = False) -> str:
        """Run one chat call, append a TurnRecord, return the reply's answer.

        Only the answer (reasoning stripped) is parsed and fed back as
        history: a CALC/FINAL_ANSWER written inside `<think>` is not acted on,
        and old reasoning is not re-injected into the context every turn
        (which grew the prompt by ~3k tokens/turn on thinking-on solves).
        `force_no_think` overrides thinking OFF — used for the forced commit so
        the budget-exhausted turn doesn't spend its tokens thinking again."""
        resp = client.chat(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            disable_thinking=True if force_no_think else disable_thinking,
        )
        answer = resp.answer
        turns.append(TurnRecord(
            text=resp.text,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            latency_s=resp.latency_s,
            answer=answer,
        ))
        messages.append({"role": "assistant", "content": answer})
        return answer

    def _total_completion() -> int:
        return sum(t.completion_tokens for t in turns)

    t0 = time.monotonic()
    try:
        for _ in range(max_total_turns):
            reply = call_model()

            # Token-budget guard: bound the thinking runaway. If the cumulative
            # completion budget is hit, force one clean no-think commit and stop.
            if (max_total_completion_tokens
                    and _total_completion() >= max_total_completion_tokens
                    and not (not extract_calc_calls(reply) and has_final_answer(reply))):
                messages.append({"role": "user", "content": (
                    "You have reached your token budget. Stop reasoning now. Using the "
                    "RESULT values you already have, output ONLY `FINAL_ANSWER: <value> "
                    "<unit>` on the next line — no thinking, no calculation.")})
                reply = call_model(force_no_think=True)
                stopped = "final_answer" if has_final_answer(reply) else "token_budget"
                break

            # IMPORTANT: when a turn has BOTH CALC and FINAL_ANSWER, the
            # model usually wrote fake `RESULT:` lines for itself and
            # committed an answer based on those hallucinated intermediates.
            # We do NOT trust that. Run the real calculator first; the model
            # has to commit on a later turn from REAL results.
            calc_exprs = extract_calc_calls(reply) if use_calc else []

            if not calc_exprs and has_final_answer(reply):
                stopped = "final_answer"
                break

            if not use_calc:
                stopped = "no_final_answer"
                break

            if not calc_exprs:
                if prodded:
                    stopped = "no_progress"
                    break
                prodded = True
                messages.append({
                    "role": "user",
                    "content": (
                        "I don't see a CALC: line or a FINAL_ANSWER: line. "
                        "Either emit `CALC: <expression>` to compute, or "
                        "`FINAL_ANSWER: <value> <unit>` to finish."
                    ),
                })
                continue

            n_tool_calls += len(calc_exprs)
            if n_tool_calls > max_tool_turns:
                messages.append({
                    "role": "user",
                    "content": (
                        "Tool-call cap reached. Stop computing and emit "
                        "FINAL_ANSWER on the next line."
                    ),
                })
                reply = call_model()
                stopped = "final_answer" if has_final_answer(reply) else "max_tool_turns"
                break

            _, results_msg = evaluate_text(reply, calc)
            # If the model also emitted a FINAL_ANSWER in this turn, it was
            # almost certainly based on hallucinated RESULT lines. Tell the
            # model to discard it and recompute from these real numbers.
            if has_final_answer(reply):
                results_msg = (
                    results_msg
                    + "\n\nNOTE: ignore the FINAL_ANSWER line you wrote last "
                    "turn — it was based on numbers you guessed, not the "
                    "calculator's real output. Recompute using the RESULT "
                    "values above, then emit a new FINAL_ANSWER."
                )
            messages.append({"role": "user", "content": results_msg})
    except Exception as e:
        stopped = "error"
        error = repr(e)

    latency = time.monotonic() - t0
    final_text = turns[-1].answer if turns else ""
    full_trace = "\n\n---\n\n".join(t.text for t in turns)
    answer_trace = "\n\n---\n\n".join(t.answer for t in turns)
    prompt_total = sum(t.prompt_tokens for t in turns)
    completion_total = sum(t.completion_tokens for t in turns)
    peak_context = max((t.prompt_tokens + t.completion_tokens for t in turns), default=0)
    return RunResult(
        condition=condition,
        problem_id=problem["id"],
        raw_messages=messages,
        final_text=final_text,
        full_trace=full_trace,
        n_tool_calls=n_tool_calls,
        n_turns=len(turns),
        stopped_reason=stopped,
        latency_s=latency,
        turn_records=turns,
        prompt_tokens=prompt_total,
        completion_tokens=completion_total,
        error=error,
        answer_trace=answer_trace,
        peak_context_tokens=peak_context,
    )
