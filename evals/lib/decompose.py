"""
decompose — explicit decompose-then-solve for multi-step problems (eval 008).

Instead of solving a multi-step problem in one monolithic ReAct loop, this:
  1. PLAN  — ask the model (thinking ON) to break the problem into an ordered
             list of SINGLE-QUANTITY sub-steps; the last one produces the answer.
  2. EXECUTE each leaf — a focused, NO-THINK calculator solve that computes
             exactly one quantity, with all prior results supplied as knowns.
  3. COMPOSE — the last leaf's value is the final answer.

Hypothesis (003 + 007): walking every planned sub-step prevents the
"compute one burn, forget the rest" failure that monolithic solves hit on
multi-step transfers, and each leaf is a single-step task where no-think + the
calculator is reliable. Knowledge is held constant (the oracle cards are given
in both the plan and every leaf), so the only variable is the solve strategy.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .calculator import Calculator, evaluate_text, extract_calc_calls

_PLAN_SYS = (
    "You are decomposing a multi-step astrodynamics problem into a sequence of "
    "SINGLE-QUANTITY sub-steps that will each be computed one at a time, in "
    "order. You PLAN only — you do not compute any numbers here."
)


def _plan_user(statement: str, cards_block: str) -> str:
    return (
        f"PROBLEM:\n{statement}\n\n"
        + (f"REFERENCE FORMULAS (use these):\n{cards_block}\n\n" if cards_block else "")
        + "Break this into an ordered list of sub-steps. Each sub-step computes "
        "EXACTLY ONE numeric quantity (e.g. \"circular speed at the inner "
        "radius\", \"transfer-ellipse semi-major axis\", \"first burn "
        "magnitude\"). Later steps may use the results of earlier ones. The "
        "LAST step must produce the final answer the problem asks for, in the "
        "requested unit.\n\n"
        "Output ONLY a JSON array, nothing else:\n"
        "[{\"id\": 1, \"compute\": \"<one quantity>\"}, "
        "{\"id\": 2, \"compute\": \"<one quantity>\"}, ...]"
    )


_LEAF_SYS = (
    "You compute EXACTLY ONE quantity for a larger problem, using a calculator. "
    "Write `CALC: <expression>` lines for arithmetic and wait for the `RESULT:`. "
    "Do not solve the whole problem — only the one quantity asked. When you have "
    "it, end your message with exactly one line:\n"
    "    STEP_RESULT: <number>\n"
    "in SI units (just the number, e.g. `7672.5`). Nothing after that line."
)


def _leaf_user(statement: str, cards_block: str, known_block: str, step: dict) -> str:
    return (
        f"PROBLEM (context only):\n{statement}\n\n"
        + (f"REFERENCE FORMULAS:\n{cards_block}\n\n" if cards_block else "")
        + f"KNOWN VALUES (already computed — use these, don't recompute):\n{known_block}\n\n"
        f"COMPUTE ONLY THIS ONE QUANTITY:\n  Step {step['id']}: {step['compute']}\n\n"
        "Use `CALC:` for the arithmetic, then end with `STEP_RESULT: <number>` (SI)."
    )


_STEP_RE = re.compile(
    r"STEP_RESULT[ \t]*[:=][ \t]*([-+]?[0-9][0-9_,]*\.?[0-9]*(?:[eE][-+]?[0-9]+)?)"
)
_CALC_HAS = re.compile(r"^[ \t]*[-*]?[ \t]*CALC[ \t]*:", re.MULTILINE)


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "").replace("_", ""))
    except ValueError:
        return None


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_plan(text: str) -> list[dict[str, Any]]:
    t = _strip_think(text)
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", t, flags=re.DOTALL)
    if fence:
        t = fence.group(1)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        arr = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return []
    steps = []
    for i, item in enumerate(arr if isinstance(arr, list) else []):
        if isinstance(item, dict) and item.get("compute"):
            steps.append({"id": int(item.get("id", i + 1)), "compute": str(item["compute"])})
        elif isinstance(item, str):
            steps.append({"id": i + 1, "compute": item})
    return steps


@dataclass
class Leaf:
    id: int
    compute: str
    value: Optional[float]
    n_calc: int
    text: str


@dataclass
class DecompResult:
    final_text: str                 # synthetic FINAL_ANSWER line for scoring
    final_value: Optional[float]
    plan: list[dict[str, Any]]
    plan_ok: bool
    leaves: list[Leaf]
    n_steps: int
    n_tool_calls: int
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    full_trace: str
    stopped_reason: str             # decomposed | no_answer | plan_failed | error
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def tokens_per_second(self) -> float:
        return self.completion_tokens / self.latency_s if self.latency_s > 0 and self.completion_tokens else 0.0


@dataclass
class _Stats:
    prompt: int = 0
    completion: int = 0
    latency: float = 0.0
    calls: int = 0


def _execute_leaf(*, client, model_name, statement, cards_block, step, known,
                  calc, temperature, max_tokens, max_calc_turns, disable_thinking,
                  stats: _Stats) -> Leaf:
    known_block = "\n".join(
        f"  - {k.compute} = {k.value:.10g}" for k in known if k.value is not None
    ) or "  (none yet)"
    messages = [
        {"role": "system", "content": _LEAF_SYS},
        {"role": "user", "content": _leaf_user(statement, cards_block, known_block, step)},
    ]
    texts: list[str] = []
    n_calc = 0
    value: Optional[float] = None
    prodded = False
    for _ in range(max_calc_turns + 3):
        resp = client.chat(model=model_name, messages=messages, temperature=temperature,
                           max_tokens=max_tokens, disable_thinking=disable_thinking)
        stats.prompt += resp.prompt_tokens; stats.completion += resp.completion_tokens
        stats.latency += resp.latency_s; texts.append(resp.text)
        messages.append({"role": "assistant", "content": resp.text})

        calc_exprs = extract_calc_calls(resp.text)
        if calc_exprs:                      # tool use wins over a same-turn STEP_RESULT
            n_calc += len(calc_exprs); stats.calls += len(calc_exprs)
            if n_calc > max_calc_turns:
                messages.append({"role": "user", "content":
                                 "Calculation cap reached — output STEP_RESULT: <number> now."})
                resp = client.chat(model=model_name, messages=messages, temperature=temperature,
                                   max_tokens=max_tokens, disable_thinking=disable_thinking)
                stats.prompt += resp.prompt_tokens; stats.completion += resp.completion_tokens
                stats.latency += resp.latency_s; texts.append(resp.text)
                mm = _STEP_RE.search(resp.text)
                value = _to_float(mm.group(1)) if mm else None
                break
            _, results_msg = evaluate_text(resp.text, calc)
            messages.append({"role": "user", "content": results_msg})
            prodded = False
            continue

        m = _STEP_RE.search(resp.text)
        if m:
            value = _to_float(m.group(1)); break
        if prodded:
            break
        prodded = True
        messages.append({"role": "user", "content":
                         "Use `CALC: <expr>` to compute, then output `STEP_RESULT: <number>`."})
    return Leaf(step["id"], step["compute"], value, n_calc, "\n".join(texts))


def solve_decomposed(*, client, model_name, problem, cards_block, expected_unit,
                     calculator_extras=None, temperature=0.2,
                     plan_max_tokens=None, leaf_max_tokens=None,
                     plan_disable_thinking=False, leaf_disable_thinking=True,
                     max_steps=12, max_calc_turns=6) -> DecompResult:
    """Plan → execute each leaf → compose. Knowledge (cards_block) is given in
    both phases so only the solve STRATEGY differs from a monolithic solve."""
    stats = _Stats()
    t0 = time.monotonic()
    statement = problem["statement"]
    calc = Calculator.default(calculator_extras)
    try:
        # 1. PLAN
        pmsgs = [{"role": "system", "content": _PLAN_SYS},
                 {"role": "user", "content": _plan_user(statement, cards_block)}]
        presp = client.chat(model=model_name, messages=pmsgs, temperature=temperature,
                            max_tokens=plan_max_tokens, disable_thinking=plan_disable_thinking)
        stats.prompt += presp.prompt_tokens; stats.completion += presp.completion_tokens
        stats.latency += presp.latency_s
        plan = _parse_plan(presp.text)[:max_steps]
        plan_ok = bool(plan)
        if not plan_ok:
            plan = [{"id": 1, "compute": "the final answer the problem asks for"}]

        # 2. EXECUTE leaves
        leaves: list[Leaf] = []
        for step in plan:
            leaf = _execute_leaf(
                client=client, model_name=model_name, statement=statement,
                cards_block=cards_block, step=step, known=leaves, calc=calc,
                temperature=temperature, max_tokens=leaf_max_tokens,
                max_calc_turns=max_calc_turns, disable_thinking=leaf_disable_thinking,
                stats=stats)
            leaves.append(leaf)

        # 3. COMPOSE
        final_value = leaves[-1].value if leaves else None
        if final_value is None:
            stopped = "no_answer"
            final_text = "(no STEP_RESULT on the final leaf)"
        else:
            stopped = "decomposed"
            final_text = f"FINAL_ANSWER: {final_value:.10g} {expected_unit}"

        trace = "PLAN:\n" + presp.text.strip() + "\n\n" + "\n\n".join(
            f"[step {lf.id}] {lf.compute}\n{lf.text.strip()}\n=> {lf.value}" for lf in leaves)
        return DecompResult(
            final_text=final_text, final_value=final_value, plan=plan, plan_ok=plan_ok,
            leaves=leaves, n_steps=len(plan), n_tool_calls=stats.calls,
            prompt_tokens=stats.prompt, completion_tokens=stats.completion,
            latency_s=time.monotonic() - t0, full_trace=trace, stopped_reason=stopped)
    except Exception as e:
        return DecompResult(
            final_text=f"(error: {e})", final_value=None, plan=[], plan_ok=False,
            leaves=[], n_steps=0, n_tool_calls=stats.calls, prompt_tokens=stats.prompt,
            completion_tokens=stats.completion, latency_s=time.monotonic() - t0,
            full_trace="", stopped_reason="error", error=repr(e))
