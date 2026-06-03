"""
A small safe expression evaluator + the CALC/RESULT text protocol the agent
uses to call it.

Why this exists (and not just `eval`):
- The model can emit arbitrary text in any answer. We can't ever pass that to
  `eval`. The sandbox here whitelists ops, names, and function calls, and
  rejects every Python escape hatch we could think of (imports, attribute
  access, subscripts, function defs, lambdas, comprehensions, walrus,
  starred args, name binding, ...).

Why a custom evaluator (not asteval / sympy):
- We need exactly one feature: parse a math expression, evaluate it against a
  fixed namespace, return a float. Zero deps keeps this reproducible across
  Linux / macOS / Windows boxes.

Protocol (text, not function calling):
  - Model output may contain one or more lines beginning with `CALC:`.
  - The harness extracts them in order, evaluates each, and replies with a
    single user message containing one `RESULT[i]: <value>` line per call,
    *in the same order*.
  - The loop ends when the model emits a line beginning with `FINAL_ANSWER:`
    or when the turn cap is hit.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

# Math-only constants. Dataset-specific physical constants (e.g. mu_earth)
# are NOT defined here — each dataset's ground_truth.py owns its own
# CONSTANTS dict, and `run.py` injects them via `Calculator.default(extras=)`.
# Keep this dict tight: every name is a possible distractor in the model's
# CALC line, and a clash between a math name and a physics name would be
# confusing for everyone.
CONSTANTS: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
}

# ---------------------------------------------------------------------------
# Safe AST evaluator.
# ---------------------------------------------------------------------------
# Math functions exposed to the model. Add sparingly; each one is a new attack
# surface (every function gets its args evaluated by the same sandbox).
_ALLOWED_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "exp": math.exp,
    "log": math.log,      # natural log; for log10 the model passes base.
    "log10": math.log10,
    "log2": math.log2,
    "pow": math.pow,
    "floor": math.floor,
    "ceil": math.ceil,
    "radians": math.radians,
    "degrees": math.degrees,
    "min": min,
    "max": max,
    "round": round,
}

_ALLOWED_BINOPS = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


class CalculatorError(Exception):
    """Raised when a CALC expression is invalid or unsafe."""


def _eval_node(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, names)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalculatorError(f"unsupported constant: {node.value!r}")

    if isinstance(node, ast.Name):
        if node.id not in names:
            raise CalculatorError(f"unknown name: {node.id!r}")
        return names[node.id]

    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise CalculatorError(f"operator not allowed: {type(node.op).__name__}")
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        return _apply_binop(node.op, left, right)

    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise CalculatorError(f"unary operator not allowed: {type(node.op).__name__}")
        operand = _eval_node(node.operand, names)
        return +operand if isinstance(node.op, ast.UAdd) else -operand

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalculatorError("only direct function calls are allowed (no method calls)")
        if node.keywords:
            raise CalculatorError("keyword arguments are not allowed")
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise CalculatorError("starred arguments are not allowed")
        fname = node.func.id
        if fname not in _ALLOWED_FUNCS:
            raise CalculatorError(f"function not allowed: {fname!r}")
        args = [_eval_node(a, names) for a in node.args]
        return _ALLOWED_FUNCS[fname](*args)

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(elt, names) for elt in node.elts)

    raise CalculatorError(f"node type not allowed: {type(node).__name__}")


def _apply_binop(op: ast.AST, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Add): return left + right
    if isinstance(op, ast.Sub): return left - right
    if isinstance(op, ast.Mult): return left * right
    if isinstance(op, ast.Div): return left / right
    if isinstance(op, ast.FloorDiv): return left // right
    if isinstance(op, ast.Mod): return left % right
    if isinstance(op, ast.Pow): return left ** right
    raise CalculatorError(f"unexpected op: {type(op).__name__}")


@dataclass
class Calculator:
    """Stateless wrapper holding the namespace exposed to the model."""

    names: dict[str, Any]

    @classmethod
    def default(cls, extra: dict[str, Any] | None = None) -> "Calculator":
        names = dict(CONSTANTS)
        if extra:
            names.update(extra)
        return cls(names)

    def eval(self, expr: str) -> float:
        # Pre-screen for surface patterns that *should* never reach the AST
        # walker, so we can give clearer error messages.
        if any(bad in expr for bad in ("__", "import ", "lambda", ":=", ";")):
            raise CalculatorError("forbidden construct in expression")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise CalculatorError(f"syntax error: {e.msg}") from e
        value = _eval_node(tree, self.names)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CalculatorError(f"expression did not evaluate to a number: {value!r}")
        return float(value)


# ---------------------------------------------------------------------------
# CALC / RESULT text protocol.
# ---------------------------------------------------------------------------
# `CALC:` must be at start of a line; everything after the colon up to the
# end of the line is the expression. We tolerate any leading whitespace and
# common bullet markers ("- ", "* ").
_CALC_RE = re.compile(r"^[ \t]*[-*]?[ \t]*CALC[ \t]*:[ \t]*(?P<expr>.+?)[ \t]*$", re.MULTILINE)
_FINAL_RE = re.compile(r"(?:\*\*)?FINAL_ANSWER(?:\*\*)?[ \t]*:[ \t]*(?P<rest>.+?)(?:$|\n)", re.MULTILINE)


def extract_calc_calls(text: str) -> list[str]:
    """Return the list of expressions emitted as `CALC: <expr>` lines, in order."""
    return [m.group("expr") for m in _CALC_RE.finditer(text)]


def has_final_answer(text: str) -> bool:
    return _FINAL_RE.search(text) is not None


def format_results(values: Iterable[float | str]) -> str:
    """Format calculator outputs as a single message back to the model.

    Single call → `RESULT: <value>`.
    Multiple calls → `RESULT[1]: <value>` lines in order.
    Errors are returned as `ERROR: <msg>` (matching index).
    """
    values = list(values)
    if not values:
        return "RESULT: (no CALC lines found)"
    if len(values) == 1:
        return _format_one(values[0], prefix="RESULT")
    return "\n".join(_format_one(v, prefix=f"RESULT[{i+1}]") for i, v in enumerate(values))


def _format_one(value: float | str, *, prefix: str) -> str:
    if isinstance(value, str):
        # An error message; surface it explicitly.
        return f"{prefix.replace('RESULT', 'ERROR')}: {value}"
    # Plenty of precision for any astrodynamics use; small models tend to
    # truncate aggressively, so we give them headroom.
    return f"{prefix}: {value:.10g}"


def evaluate_text(text: str, calc: Calculator) -> tuple[list[str], str]:
    """Run every CALC call found in `text`. Return (expressions, results_message).

    On per-expression failure, the result for that slot is the error string.
    Never raises.
    """
    exprs = extract_calc_calls(text)
    results: list[float | str] = []
    for e in exprs:
        try:
            results.append(calc.eval(e))
        except CalculatorError as err:
            results.append(str(err))
    return exprs, format_results(results)
