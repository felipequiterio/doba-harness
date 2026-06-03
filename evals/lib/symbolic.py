"""
Symbolic algebra for eval 005 — a local CAS (SymPy) in two roles:

  1. A DERIVATION TOOL the model drives, via a `SYMB:` text protocol that
     mirrors the calculator's `CALC:` — but returns *formulas*, not numbers.
     (The local, no-egress answer to "something like Wolfram".)

  2. A JUDGE of a built card's formula: `check_formula(...)` decides whether
     the model's derived equation is equivalent to the canonical one,
     independent of the numeric solve. This is the metric that distinguishes
     "correct derivation" from "wrong card, lucky number".

Equivalence is tested by NUMERIC PROBING, not symbolic normalization: solve
the model's equation for the target, then evaluate it at several random
points in the input domain and compare to the canonical function. Probing is
robust to differences in symbolic form AND to the model naming variables
however it likes (we map its symbols to roles via an alias table). A fixed
RNG seed makes the verdict deterministic (re-scoring is stable).
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    parse_expr,
    standard_transformations,
)

_TRANSFORMS = standard_transformations + (convert_xor,)  # so `a^3` means a**3

# Force common astro variable names to be plain SYMBOLS, overriding SymPy's
# built-ins. Without this, `beta`/`gamma` parse as the Beta/Gamma *functions*
# and `N`/`S`/`O`/`I`/`E` as singletons — silently breaking the model's
# formulas. Functions (sin, cos, solve, Eq, sqrt, ...) are NOT listed here, so
# they still resolve to SymPy callables from the default namespace.
_SYMBOL_NAMES = (
    "a b c d e f g h i j k l m n p q r s t u v w x y z "
    "mu nu xi pi_ theta phi beta gamma delta epsilon eta lamda lam "
    "omega Omega rho sigma tau psi chi kappa "
    "T E0 M0 "  # uppercase ones that aren't reserved we want as symbols
    "r_p r_a r_b r_0 v_p v_a v_c v_inf v_0 a_t a_1 a_2 "
    "m1 m2 m3 mass_ratio C3 c3"
).split()
DEFAULT_SYMBOLS: dict[str, Any] = {name: sp.Symbol(name) for name in _SYMBOL_NAMES}


class SymbolicError(Exception):
    """Invalid or unsafe SYMB expression."""


def parse_formula(s: str, local_dict: Optional[dict[str, Any]] = None) -> Any:
    """Parse a math string into a SymPy object (expression or Eq).

    Uses SymPy's expression grammar (NOT python eval), so this can't import,
    call dunders, or escape — but we still pre-screen for obvious abuse.
    `^` is treated as exponentiation. `local_dict` pins chosen names to
    Symbols (defaults to DEFAULT_SYMBOLS); unlisted bare names auto-symbolize.
    """
    s = s.strip()
    if any(bad in s for bad in ("__", "import", "lambda", "exec(", "eval(")):
        raise SymbolicError("forbidden construct in expression")
    ld = DEFAULT_SYMBOLS if local_dict is None else local_dict
    try:
        return parse_expr(s, local_dict=ld, transformations=_TRANSFORMS, evaluate=True)
    except Exception as e:  # SympifyError, TokenError, SyntaxError, ...
        raise SymbolicError(f"parse error: {e}") from e


# ---------------------------------------------------------------------------
# SYMB / SYMB_RESULT text protocol (mirrors calculator's CALC / RESULT).
# ---------------------------------------------------------------------------
_SYMB_RE = re.compile(r"^[ \t]*[-*]?[ \t]*SYMB[ \t]*:[ \t]*(?P<expr>.+?)[ \t]*$", re.MULTILINE)


def extract_symb_calls(text: str) -> list[str]:
    return [m.group("expr") for m in _SYMB_RE.finditer(text)]


def _eval_one(expr: str) -> str:
    try:
        result = parse_formula(expr)
        # Light simplification so the model gets a tidy formula back.
        try:
            simplified = sp.simplify(result)
        except Exception:
            simplified = result
        return str(simplified)
    except SymbolicError as e:
        return f"ERROR: {e}"
    except Exception as e:  # defensive: never let a SYMB line crash the loop
        return f"ERROR: {type(e).__name__}: {e}"


def evaluate_symb_text(text: str) -> tuple[list[str], str]:
    """Run every SYMB call in `text`; return (expressions, results_message)."""
    exprs = extract_symb_calls(text)
    results = [_eval_one(e) for e in exprs]
    if not results:
        return [], "SYMB_RESULT: (no SYMB lines found)"
    if len(results) == 1:
        body = (f"SYMB_RESULT: {results[0]}" if not results[0].startswith("ERROR")
                else f"SYMB_{results[0]}")
        return exprs, body
    lines = []
    for i, r in enumerate(results):
        tag = f"SYMB_RESULT[{i+1}]" if not r.startswith("ERROR") else f"SYMB_ERROR[{i+1}]"
        lines.append(f"{tag}: {r.removeprefix('ERROR: ')}")
    return exprs, "\n".join(lines)


# ---------------------------------------------------------------------------
# Formula equivalence by numeric probing.
# ---------------------------------------------------------------------------
@dataclass
class EquivSpec:
    """How to check a built formula for one problem.

    target:    acceptable names for the quantity the card solves for.
    inputs:    role -> acceptable symbol names the model might use.
    canonical: callable(**{role: float}) -> float, the ground-truth formula.
    domains:   role -> (lo, hi) sampling range for probing (pick safe ranges
               away from singularities).
    """
    target: list[str]
    inputs: dict[str, list[str]]
    canonical: Callable[..., float]
    domains: dict[str, tuple[float, float]]
    # Known physical constants (e.g. mu). role -> (aliases, fixed value).
    # The model may keep these symbolic OR plug the number; either parses,
    # and we substitute the fixed value so the equivalence check is stable.
    # canonical is called with constants passed by role name too.
    constants: dict[str, tuple[list[str], float]] = field(default_factory=dict)


@dataclass
class FormulaCheck:
    status: str                      # equivalent | not_equivalent | unparseable | unmapped | no_equation
    detail: str = ""
    probes_passed: int = 0
    probes_total: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "equivalent"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _match_role(sym_name: str, aliases: dict[str, list[str]]) -> Optional[str]:
    n = _norm(sym_name)
    for role, names in aliases.items():
        if n in {_norm(a) for a in names}:
            return role
    return None


def check_formula(equation_str: str, spec: EquivSpec, *, n_probes: int = 12,
                  rel_tol: float = 1e-6, seed: int = 12345) -> FormulaCheck:
    """Is `equation_str` equivalent to spec.canonical? Numeric probing."""
    raw = (equation_str or "").strip()
    if not raw:
        return FormulaCheck("no_card", "empty equation")

    # Pin this problem's target + input + constant names to Symbols (beats
    # SymPy treating e.g. `beta`/`gamma` as the Beta/Gamma functions).
    names = list(spec.target) + [a for al in spec.inputs.values() for a in al]
    names += [a for al, _ in spec.constants.values() for a in al]
    local = {**DEFAULT_SYMBOLS, **{n: sp.Symbol(n) for n in names}}
    target_aliases = {_norm(a) for a in spec.target}

    # Two shapes a card formula can take:
    #   "target = expr"  → solve the equation for the target symbol.
    #   bare "expr"      → the formula FOR the target (what SymPy solve/simplify
    #                      returns, which the model often pastes verbatim). A
    #                      bare expression is a valid card; treat it as the
    #                      target's value directly.
    m = re.search(r"(?<![<>=!])=(?!=)", raw)
    if m:
        lhs_s, rhs_s = raw[: m.start()], raw[m.start() + 1 :]
        try:
            lhs, rhs = parse_formula(lhs_s, local), parse_formula(rhs_s, local)
        except SymbolicError as e:
            return FormulaCheck("unparseable", str(e))
        eq = sp.Eq(lhs, rhs)
        target_sym = next((s for s in eq.free_symbols if _norm(s.name) in target_aliases), None)
        if target_sym is None:
            return FormulaCheck("unmapped", f"no target symbol among {[s.name for s in eq.free_symbols]}")
        try:
            solutions = sp.solve(eq, target_sym)
        except Exception as e:
            return FormulaCheck("unparseable", f"solve failed: {e}")
        if not solutions:
            return FormulaCheck("not_equivalent", "no solution for target")
        symbols_to_map = [s for s in eq.free_symbols if s is not target_sym]
    else:
        try:
            expr = parse_formula(raw, local)
        except SymbolicError as e:
            return FormulaCheck("unparseable", str(e))
        if not hasattr(expr, "free_symbols"):
            return FormulaCheck("unparseable", "not an expression")
        if any(_norm(s.name) in target_aliases for s in expr.free_symbols):
            return FormulaCheck("no_equation", "bare expression still contains the target symbol")
        solutions = [expr]
        symbols_to_map = list(expr.free_symbols)

    # Map symbols to input roles or known constants.
    const_aliases = {role: al for role, (al, _) in spec.constants.items()}
    const_value = {role: val for role, (_, val) in spec.constants.items()}
    role_for: dict[Any, str] = {}
    const_for: dict[Any, str] = {}
    for s in symbols_to_map:
        role = _match_role(s.name, spec.inputs)
        if role is not None:
            role_for[s] = role
            continue
        crole = _match_role(s.name, const_aliases)
        if crole is not None:
            const_for[s] = crole
            continue
        return FormulaCheck("unmapped", f"symbol {s.name!r} maps to no input/constant role")

    rng = random.Random(seed)
    passed = 0
    for _ in range(n_probes):
        role_vals = {role: rng.uniform(*spec.domains[role]) for role in spec.inputs}
        try:
            canon = float(spec.canonical(**role_vals, **const_value))
        except Exception:
            continue  # skip probe points where canonical is undefined
        subs = {s: role_vals[role_for[s]] for s in role_for}
        subs.update({s: const_value[const_for[s]] for s in const_for})
        ok = False
        for sol in solutions:
            try:
                val = complex(sol.subs(subs).evalf())
            except Exception:
                continue
            if abs(val.imag) > 1e-9:
                continue
            denom = max(abs(canon), 1e-12)
            if abs(val.real - canon) / denom <= rel_tol:
                ok = True
                break
        if ok:
            passed += 1

    total = n_probes
    if passed >= total - 0:  # all probes must match
        return FormulaCheck("equivalent", f"matched canonical at {passed}/{total} points",
                            passed, total)
    return FormulaCheck("not_equivalent", f"matched only {passed}/{total} points", passed, total)
