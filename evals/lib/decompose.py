"""
decompose — decompose-then-EXECUTE for multi-step problems (eval 008+).

Reworked after 008's first cut failed: there, the model planned *and* let
no-think "leaf" model calls do the arithmetic, which produced garbage (a
10-step plan returned a value 5000× too big; leaves returned None; errors
cascaded). The plan *reasoning* was fine; the *execution* was broken.

New design — the model writes a little PROGRAM, the harness runs it:
  1. PLAN  — one model call emits an ordered list of named steps, each a
             calculator EXPRESSION over the constants, the problem's `given`
             values, earlier step names, and math functions.
  2. EXECUTE — the harness evaluates each expression deterministically with the
             calculator (no model arithmetic), threading results forward.
  3. The last step's value is the answer.

This separates "decompose correctly" (the model) from "do arithmetic"
(the deterministic calculator), removing the garbage-leaf failure mode, and
costs a single model call instead of one per step.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .calculator import Calculator, CalculatorError

_PLAN_SYS = (
    "You decompose a multi-step astrodynamics problem into a short PROGRAM: an "
    "ordered list of named steps, each computing ONE quantity with a calculator "
    "EXPRESSION. The system evaluates the expressions for you — you do NOT "
    "compute any numbers yourself."
)


def _plan_user(statement: str, cards_block: str, given: dict[str, Any],
               const_names: list[str]) -> str:
    given_lines = "\n".join(f"    {k} = {v}" for k, v in given.items()) or "    (none)"
    consts = ", ".join(const_names)
    return (
        f"PROBLEM:\n{statement}\n\n"
        + (f"REFERENCE FORMULAS (use these):\n{cards_block}\n\n" if cards_block else "")
        + "AVAILABLE VARIABLES for your expressions:\n"
        f"  Given values:\n{given_lines}\n"
        f"  Constants: {consts}\n"
        "  Math functions: sqrt, abs, sin, cos, tan, asin, acos, atan, atan2, "
        "exp, log, log10, pow, radians, degrees, min, max, pi, e.\n\n"
        "Write an ordered JSON list of steps. Each step is "
        "{\"name\": \"<short_var>\", \"expr\": \"<calculator expression>\"}. "
        "An expression may use the given values, the constants, the math "
        "functions, and the NAMES of any EARLIER steps. The LAST step's value "
        "is the final answer the problem asks for.\n\n"
        "Example shape (not the answer):\n"
        "[{\"name\": \"r1\", \"expr\": \"R_earth + start_altitude_m\"}, "
        "{\"name\": \"v_c1\", \"expr\": \"sqrt(mu_earth / r1)\"}, "
        "{\"name\": \"dv_total\", \"expr\": \"dv1 + dv2\"}]\n\n"
        "Output ONLY the JSON array — no prose, no numbers computed."
    )


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_plan(text: str) -> list[dict[str, str]]:
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
        if isinstance(item, dict) and item.get("name") and item.get("expr"):
            steps.append({"name": str(item["name"]).strip(), "expr": str(item["expr"]).strip()})
    return steps


@dataclass
class Step:
    id: int
    compute: str          # the step name (kept as `compute` for record compat)
    expr: str
    value: Optional[float]
    error: Optional[str] = None


@dataclass
class DecompResult:
    final_text: str                 # synthetic FINAL_ANSWER line for scoring
    final_value: Optional[float]
    plan: list[dict[str, str]]
    plan_ok: bool
    leaves: list[Step]              # the executed steps (name `leaves` for record compat)
    n_steps: int
    n_tool_calls: int               # = expressions evaluated
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    full_trace: str
    stopped_reason: str             # decomposed | no_answer | plan_failed | exec_error | error
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def solve_decomposed(*, client, model_name, problem, cards_block, expected_unit,
                     calculator_extras=None, temperature=0.2,
                     plan_max_tokens=None, plan_disable_thinking=False,
                     max_steps=16, retry_on_parse_fail=True) -> DecompResult:
    """Plan a program of calculator expressions, then execute it deterministically."""
    t0 = time.monotonic()
    statement = problem["statement"]
    given = {k: v for k, v in (problem.get("given") or {}).items() if isinstance(v, (int, float))}
    constants = dict(calculator_extras or {})
    # Namespace the calculator evaluates against: constants + given values
    # (+ each step's result as we go).
    calc = Calculator.default({**constants, **given})
    const_names = sorted(constants.keys())

    prompt_tok = compl_tok = 0
    plan_texts: list[str] = []

    def _plan_call(extra_user: Optional[str] = None, force_no_think: bool = False):
        nonlocal prompt_tok, compl_tok
        msgs = [{"role": "system", "content": _PLAN_SYS},
                {"role": "user", "content": _plan_user(statement, cards_block, given, const_names)}]
        if extra_user:
            msgs.append({"role": "user", "content": extra_user})
        resp = client.chat(model=model_name, messages=msgs, temperature=temperature,
                           max_tokens=plan_max_tokens,
                           disable_thinking=True if force_no_think else plan_disable_thinking)
        prompt_tok += resp.prompt_tokens; compl_tok += resp.completion_tokens
        plan_texts.append(resp.text)
        return _parse_plan(resp.text)

    try:
        plan = _plan_call()
        if not plan and retry_on_parse_fail:
            # The thinking-on plan can run away without emitting JSON (008 saw
            # this on bi-elliptic). Retry NO-THINK for a clean emission.
            plan = _plan_call("Output ONLY the JSON array of "
                              "{\"name\":..., \"expr\":...} steps — nothing else.",
                              force_no_think=True)
        plan_ok = bool(plan)
        plan = plan[:max_steps]
        if not plan_ok:
            return DecompResult(
                final_text="(plan did not parse)", final_value=None, plan=[], plan_ok=False,
                leaves=[], n_steps=0, n_tool_calls=0, prompt_tokens=prompt_tok,
                completion_tokens=compl_tok, latency_s=time.monotonic() - t0,
                full_trace="\n---\n".join(plan_texts), stopped_reason="plan_failed")

        # EXECUTE the program deterministically.
        steps: list[Step] = []
        n_eval = 0
        for i, st in enumerate(plan):
            name, expr = st["name"], st["expr"]
            try:
                val = calc.eval(expr)
                calc.names[name] = val          # make it referenceable by later steps
                steps.append(Step(i + 1, name, expr, val))
                n_eval += 1
            except CalculatorError as e:
                steps.append(Step(i + 1, name, expr, None, error=str(e)))
            except Exception as e:
                steps.append(Step(i + 1, name, expr, None, error=f"{type(e).__name__}: {e}"))

        final_value = steps[-1].value if steps else None
        if final_value is None:
            stopped = "exec_error" if any(s.error for s in steps) else "no_answer"
            final_text = "(final step did not evaluate)"
        else:
            stopped = "decomposed"
            final_text = f"FINAL_ANSWER: {final_value:.10g} {expected_unit}"

        trace = ("PLAN:\n" + "\n---\n".join(plan_texts).strip() + "\n\nEXECUTION:\n"
                 + "\n".join(f"  [{s.id}] {s.compute} = {s.expr}  =>  "
                             f"{s.value if s.error is None else 'ERROR: ' + s.error}" for s in steps))
        return DecompResult(
            final_text=final_text, final_value=final_value, plan=plan, plan_ok=True,
            leaves=steps, n_steps=len(plan), n_tool_calls=n_eval, prompt_tokens=prompt_tok,
            completion_tokens=compl_tok, latency_s=time.monotonic() - t0,
            full_trace=trace, stopped_reason=stopped)
    except Exception as e:
        return DecompResult(
            final_text=f"(error: {e})", final_value=None, plan=[], plan_ok=False,
            leaves=[], n_steps=0, n_tool_calls=0, prompt_tokens=prompt_tok,
            completion_tokens=compl_tok, latency_s=time.monotonic() - t0,
            full_trace="\n---\n".join(plan_texts), stopped_reason="error", error=repr(e))
