"""Tests for the safe calculator + CALC/RESULT protocol."""

from __future__ import annotations

import math

import pytest

from lib.calculator import (
    Calculator,
    CalculatorError,
    evaluate_text,
    extract_calc_calls,
    format_results,
    has_final_answer,
)
from datasets.astrodynamics.ground_truth import CONSTANTS as ASTRO_CONSTANTS, MU_EARTH, R_EARTH


@pytest.fixture
def calc() -> Calculator:
    """Calculator with the astrodynamics constants injected. Tests that
    historically asserted `mu_earth` and `R_earth` were directly available
    still pass because the dataset constants come in via `extras`."""
    return Calculator.default(extra=ASTRO_CONSTANTS)


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------
class TestEval:
    def test_basic_arithmetic(self, calc):
        assert calc.eval("1 + 2") == 3
        assert calc.eval("2 ** 10") == 1024
        assert calc.eval("-3 + 4") == 1
        assert calc.eval("10 / 4") == 2.5
        assert calc.eval("17 % 5") == 2

    def test_math_funcs(self, calc):
        assert math.isclose(calc.eval("sqrt(2)"), math.sqrt(2))
        assert math.isclose(calc.eval("sin(pi/2)"), 1.0)
        assert math.isclose(calc.eval("log(e)"), 1.0)
        assert math.isclose(calc.eval("radians(180)"), math.pi)
        assert math.isclose(calc.eval("atan2(1, 1)"), math.pi / 4)

    def test_constants(self, calc):
        assert calc.eval("mu_earth") == MU_EARTH
        assert calc.eval("R_earth") == R_EARTH

    def test_real_vis_viva_expression(self, calc):
        # The whole point of the calculator: end-to-end astro expression.
        got = calc.eval("sqrt(mu_earth * (2/(R_earth + 200e3) - 1/(R_earth + 600e3)))")
        # cross-check with stdlib math.
        r = R_EARTH + 200e3
        a = R_EARTH + 600e3
        expected = math.sqrt(MU_EARTH * (2/r - 1/a))
        assert math.isclose(got, expected, rel_tol=1e-12)

    def test_extras_can_override_constants(self):
        c = Calculator.default(extra={"mu_earth": 1.0})
        assert c.eval("mu_earth") == 1.0


# ---------------------------------------------------------------------------
# Safety. Every one of these MUST raise.
# ---------------------------------------------------------------------------
class TestSafety:
    @pytest.mark.parametrize("expr", [
        "__import__('os').system('echo pwned')",
        "(1).__class__",
        "[].__class__",
        "().__class__.__bases__",
        "open('/etc/passwd')",
        "exec('print(1)')",
        "eval('1+1')",
        "compile('1', 'x', 'eval')",
        "globals()",
        "locals()",
        "vars()",
        "getattr(1, 'real')",
        "setattr(1, 'x', 2)",
        "lambda x: x",
        "x := 5",
        "import os",
        "[1,2,3][0]",          # subscript
        "{'a': 1}",            # dict literal
        "{'a', 'b'}",          # set literal
        "[x for x in [1,2]]",  # comprehension
        "(x for x in [1,2])",  # genexpr
        "1; 2",                # multiple statements
        "1 if True else 2",    # ternary (we don't allow this; keep surface tiny)
        "True and False",      # boolean ops (not whitelisted)
        "1 < 2",               # comparisons (not whitelisted)
        "min(*[1,2,3])",       # starred
        "min(key=1)",          # kwargs
        "print(1)",            # builtin not in whitelist
        "math.sqrt(2)",        # attribute access
        "sqrt.__call__(2)",    # attribute access
        "input()",
        "help",
    ])
    def test_rejects_unsafe(self, calc, expr):
        with pytest.raises(CalculatorError):
            calc.eval(expr)

    def test_rejects_unknown_name(self, calc):
        with pytest.raises(CalculatorError):
            calc.eval("nope + 1")

    def test_rejects_unknown_function(self, calc):
        with pytest.raises(CalculatorError):
            calc.eval("forbidden(1)")

    def test_rejects_string_constants(self, calc):
        with pytest.raises(CalculatorError):
            calc.eval("'abc'")

    def test_does_not_swallow_value_to_bool(self, calc):
        # Just being defensive: True/False can't ride out as numbers.
        with pytest.raises(CalculatorError):
            calc.eval("True")


# ---------------------------------------------------------------------------
# Protocol extraction.
# ---------------------------------------------------------------------------
class TestProtocol:
    def test_extract_single_calc(self):
        text = "let me think\nCALC: sqrt(2)\ndone"
        assert extract_calc_calls(text) == ["sqrt(2)"]

    def test_extract_multiple_calc_in_order(self):
        text = "CALC: 1+1\nsome words\nCALC: 2*3"
        assert extract_calc_calls(text) == ["1+1", "2*3"]

    def test_tolerates_leading_dash_or_star(self):
        text = "- CALC: 1\n*  CALC: 2"
        assert extract_calc_calls(text) == ["1", "2"]

    def test_ignores_inline_calc(self):
        # Only "CALC:" at start-of-line counts. Inline mentions don't.
        text = "We will compute (CALC: not this) but then\nCALC: 5"
        assert extract_calc_calls(text) == ["5"]

    def test_has_final_answer(self):
        assert has_final_answer("blah\nFINAL_ANSWER: 12 m/s\n")
        assert has_final_answer("**FINAL_ANSWER:** 12 m/s")  # markdown still parses
        assert not has_final_answer("FINAL_ANSWER without colon")

    def test_format_results_single(self):
        assert format_results([1.5]) == "RESULT: 1.5"

    def test_format_results_multiple(self):
        msg = format_results([1.0, 2.5])
        assert "RESULT[1]: 1" in msg
        assert "RESULT[2]: 2.5" in msg

    def test_format_results_error(self):
        msg = format_results(["unknown name: nope"])
        assert msg.startswith("ERROR: unknown name: nope")

    def test_evaluate_text_end_to_end(self, calc):
        exprs, msg = evaluate_text("CALC: sqrt(4)\nCALC: 2+3", calc)
        assert exprs == ["sqrt(4)", "2+3"]
        assert "RESULT[1]: 2" in msg
        assert "RESULT[2]: 5" in msg

    def test_evaluate_text_records_error_inline(self, calc):
        exprs, msg = evaluate_text("CALC: nope + 1\nCALC: 1+1", calc)
        assert len(exprs) == 2
        assert "ERROR[1]" in msg
        assert "RESULT[2]: 2" in msg

    def test_comma_separated_line_returns_every_value(self, calc):
        # Seen in eval 003 (astro-028): the model batches radii on one line.
        # Rejecting the tuple cost a tool round-trip per occurrence.
        exprs, msg = evaluate_text("CALC: 1 + 1, 2 * 3, sqrt(16)", calc)
        assert exprs == ["1 + 1, 2 * 3, sqrt(16)"]
        assert msg == "RESULT: 2, 6, 4"

    def test_comma_line_mixes_with_other_lines(self, calc):
        _, msg = evaluate_text("CALC: 1, 2\nCALC: 3", calc)
        assert "RESULT[1]: 1, 2" in msg and "RESULT[2]: 3" in msg

    def test_function_call_commas_are_not_split(self, calc):
        _, msg = evaluate_text("CALC: atan2(1, 1)", calc)
        assert msg == f"RESULT: {math.atan2(1, 1):.10g}"

    def test_eval_still_rejects_tuples(self, calc):
        # Programmatic callers (decompose) need exactly one number.
        with pytest.raises(CalculatorError):
            calc.eval("1, 2")
