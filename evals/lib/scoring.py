"""
Answer extraction and scoring.

Two outputs per run:
  - `answer_correct`: parsed numeric value, SI-normalized, within `tolerance_rel`
    of expected. PRIMARY metric.
  - `method_correct`: did the model use a method consistent with
    `canonical_method`? Judged by the frontier model from the full trace.
    SECONDARY metric.

A separate flag `right_magnitude_wrong_unit` is recorded when the magnitude
matches expected (in *some* unit) but the unit string is missing or wrong —
useful for distinguishing "doesn't know the formula" from "skipped the unit".
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Parsing FINAL_ANSWER.
# ---------------------------------------------------------------------------
# We're permissive about format because small models love to add trailing
# commentary. Accept: scientific notation, signed numbers, optional unit.
# Examples this should parse:
#   "FINAL_ANSWER: 7668.6 m/s"
#   "FINAL_ANSWER: 7.668e3 m/s"
#   "FINAL_ANSWER: 7.67 km/s"
#   "FINAL_ANSWER: 7668"           (no unit; flagged)
#   "**FINAL_ANSWER:** 7668 m/s"   (markdown bolding)
_FINAL_LINE_RE = re.compile(
    r"(?:\*\*)?FINAL_ANSWER(?:\*\*)?\s*:\s*(?P<rest>.+?)(?:$|\n)",
    re.IGNORECASE,
)
# Number can be: optional sign, digits, optional decimal, optional exponent.
_NUMBER_RE = re.compile(
    r"(?P<num>[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)"
)


@dataclass
class ParsedAnswer:
    raw_line: str
    value: Optional[float]
    unit: Optional[str]

    def is_parsed(self) -> bool:
        return self.value is not None


def parse_final_answer(text: str) -> Optional[ParsedAnswer]:
    """Return the LAST FINAL_ANSWER occurrence parsed, or None if absent.

    We take the last one because some models restate the line. Returning
    a ParsedAnswer with value=None signals "line found but couldn't parse a
    number" so the caller can record it as `unparseable` rather than `absent`.
    """
    matches = list(_FINAL_LINE_RE.finditer(text))
    if not matches:
        return None
    rest = matches[-1].group("rest").strip()
    m = _NUMBER_RE.search(rest)
    if not m:
        return ParsedAnswer(raw_line=rest, value=None, unit=None)
    value = float(m.group("num"))
    # Unit = whatever is left after stripping the number. Strip punctuation
    # and bracketing.
    leftover = (rest[: m.start()] + rest[m.end():]).strip()
    leftover = leftover.strip("[]()*_`~ ,.\t")
    unit = leftover or None
    return ParsedAnswer(raw_line=rest, value=value, unit=unit)


# ---------------------------------------------------------------------------
# Unit normalization to SI. Conservative whitelist; anything not handled is
# returned as-is so the comparator can flag it.
# ---------------------------------------------------------------------------
# (canonical_form, multiplier_to_SI) keyed by lowercased + whitespace-stripped
# unit string. The canonical_form is what we compare against the JSON's
# expected unit.
_UNIT_TABLE: dict[str, tuple[str, float]] = {
    # speed
    "m/s": ("m/s", 1.0),
    "ms-1": ("m/s", 1.0),
    "meterspersecond": ("m/s", 1.0),
    "km/s": ("m/s", 1000.0),
    "kms-1": ("m/s", 1000.0),
    "kilometerspersecond": ("m/s", 1000.0),
    # length
    "m": ("m", 1.0),
    "meter": ("m", 1.0),
    "meters": ("m", 1.0),
    "km": ("m", 1000.0),
    "kilometer": ("m", 1000.0),
    "kilometers": ("m", 1000.0),
    # time
    "s": ("s", 1.0),
    "sec": ("s", 1.0),
    "second": ("s", 1.0),
    "seconds": ("s", 1.0),
    "min": ("s", 60.0),
    "minute": ("s", 60.0),
    "minutes": ("s", 60.0),
    "h": ("s", 3600.0),
    "hr": ("s", 3600.0),
    "hour": ("s", 3600.0),
    "hours": ("s", 3600.0),
    "day": ("s", 86400.0),
    "days": ("s", 86400.0),
    # energy per mass
    "j/kg": ("J/kg", 1.0),
    "m2/s2": ("J/kg", 1.0),
    "m^2/s^2": ("J/kg", 1.0),
    # angle
    "rad": ("rad", 1.0),
    "radian": ("rad", 1.0),
    "radians": ("rad", 1.0),
    "deg": ("rad", math.pi / 180.0),
    "degree": ("rad", math.pi / 180.0),
    "degrees": ("rad", math.pi / 180.0),
    "°": ("rad", math.pi / 180.0),
    # angular rate
    "rad/s": ("rad/s", 1.0),
    "rads-1": ("rad/s", 1.0),
    "radianspersecond": ("rad/s", 1.0),
    "deg/s": ("rad/s", math.pi / 180.0),
    "degrees/s": ("rad/s", math.pi / 180.0),
    "deg/day": ("rad/s", math.pi / 180.0 / 86400.0),
    "degrees/day": ("rad/s", math.pi / 180.0 / 86400.0),
    # specific angular momentum
    "m^2/s": ("m^2/s", 1.0),
    "m2/s": ("m^2/s", 1.0),
    "km^2/s": ("m^2/s", 1.0e6),
    "km2/s": ("m^2/s", 1.0e6),
    # dimensionless — accept the common spellings the model might use
    "dimensionless": ("dimensionless", 1.0),
    "-": ("dimensionless", 1.0),
    "unitless": ("dimensionless", 1.0),
    "none": ("dimensionless", 1.0),
}


def _normalize_unit_key(unit: str) -> str:
    return unit.lower().replace(" ", "").replace("·", "*")


def to_si(value: float, unit: Optional[str]) -> tuple[float, Optional[str]]:
    """Return (value_in_SI, canonical_unit) or (value, None) if unit unknown."""
    if unit is None:
        return value, None
    key = _normalize_unit_key(unit)
    if key in _UNIT_TABLE:
        canon, mult = _UNIT_TABLE[key]
        return value * mult, canon
    return value, None


# ---------------------------------------------------------------------------
# Compare to expected.
# ---------------------------------------------------------------------------
@dataclass
class Score:
    parsed: bool = False
    answer_correct: bool = False
    relative_error: Optional[float] = None
    parsed_value: Optional[float] = None
    parsed_unit: Optional[str] = None
    si_value: Optional[float] = None
    canonical_unit: Optional[str] = None
    expected_value: Optional[float] = None
    expected_unit: Optional[str] = None
    right_magnitude_wrong_unit: bool = False
    unit_unknown: bool = False
    # True when answer_correct was reached via the salvage fallback (no
    # FINAL_ANSWER line in the trace, but the last numeric value matched).
    # Reported separately by analyze.py so methodology stays auditable.
    salvaged: bool = False
    method_correct: Optional[bool] = None
    method_judge_rationale: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_answer(
    model_output: str,
    expected_value: float,
    expected_unit: str,
    tolerance_rel: float,
    *,
    full_trace: Optional[str] = None,
    allow_salvage: bool = True,
) -> Score:
    salvaged = False
    parsed = parse_final_answer(model_output)
    if parsed is None or not parsed.is_parsed():
        # Try salvage from the trace tail when allowed. Without a fresh
        # parsed.value we'd otherwise drop a correct numerical answer just
        # because the model didn't say "FINAL_ANSWER:".
        if allow_salvage:
            haystack = full_trace if full_trace is not None else model_output
            recovered = salvage_answer(haystack, expected_unit)
            if recovered is not None and recovered.is_parsed():
                parsed = recovered
                salvaged = True
        if parsed is None or not parsed.is_parsed():
            return Score(
                parsed=False,
                parsed_value=None if parsed is None else None,
                parsed_unit=None if parsed is None else parsed.unit,
                expected_value=expected_value,
                expected_unit=expected_unit,
            )

    si_value, canon = to_si(parsed.value, parsed.unit)
    rel = _relative_error(si_value, expected_value)

    # If the expected answer is dimensionless, accept *missing* unit as a
    # match too — small models often just print a number for ratios.
    if expected_unit == "dimensionless" and parsed.unit is None:
        canon = "dimensionless"

    # If the unit string is unknown to us, we still compare the raw number to
    # the expected number — small models often skip units entirely. If
    # *that* matches, we flag right_magnitude_wrong_unit.
    answer_ok = False
    right_mag_wrong_unit = False
    unit_unknown = False
    if canon == expected_unit:
        answer_ok = rel is not None and rel <= tolerance_rel
    elif canon is None:
        unit_unknown = True
        # Compare raw value as a fallback.
        raw_rel = _relative_error(parsed.value, expected_value)
        if raw_rel is not None and raw_rel <= tolerance_rel:
            right_mag_wrong_unit = True
    else:
        # Unit canonicalizes but to a different dimension (e.g. expected m/s,
        # got "s"). Not correct.
        pass

    return Score(
        parsed=True,
        answer_correct=answer_ok,
        relative_error=rel,
        parsed_value=parsed.value,
        parsed_unit=parsed.unit,
        si_value=si_value,
        canonical_unit=canon,
        expected_value=expected_value,
        expected_unit=expected_unit,
        right_magnitude_wrong_unit=right_mag_wrong_unit,
        unit_unknown=unit_unknown,
        salvaged=salvaged,
    )


def _relative_error(value: float, expected: float) -> Optional[float]:
    if expected == 0:
        return None if value != 0 else 0.0
    try:
        return abs(value - expected) / abs(expected)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Salvage: when the model produced no FINAL_ANSWER line but the trace
# clearly contains a final numeric answer (Gemini Flash's most common
# failure mode — it solves the problem and chats past the format
# requirement). Scan the tail of the trace for a number-with-unit and
# return it. Marked `salvaged=True` in the Score so methodology stays
# auditable in analyze.py.
# ---------------------------------------------------------------------------
# Most permissive number regex with an immediately-following unit (allowing
# whitespace, markdown bolding, asterisks, parens).
_TAIL_NUMBER_WITH_UNIT_RE = re.compile(
    r"(?P<num>[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)"
    r"\s*"
    r"(?P<unit>[A-Za-z][A-Za-z0-9/^*·²³\-_]{0,12})?",
)


def salvage_answer(trace: str, expected_unit: str, tail_chars: int = 1200) -> Optional[ParsedAnswer]:
    """Best-effort recovery of a final numeric answer from the trace tail.

    Strategy:
      1. Take the last `tail_chars` characters of the trace.
      2. Find all (number, optional-unit) matches.
      3. Prefer the LAST match whose unit canonicalizes to `expected_unit`.
      4. Fallback to the LAST numeric match if none have a matching unit
         (model dropped the unit on a pure-numeric ratio, etc).

    Returns ParsedAnswer or None.
    """
    tail = trace[-tail_chars:] if len(trace) > tail_chars else trace
    matches = list(_TAIL_NUMBER_WITH_UNIT_RE.finditer(tail))
    if not matches:
        return None

    # First pass: prefer last match whose unit matches expected (after canon).
    for m in reversed(matches):
        try:
            value = float(m.group("num"))
        except ValueError:
            continue
        unit = (m.group("unit") or "").strip() or None
        if unit is None:
            continue
        _, canon = to_si(value, unit)
        if canon == expected_unit:
            return ParsedAnswer(raw_line=m.group(0), value=value, unit=unit)

    # Second pass: any LAST numeric value (no unit attached).
    for m in reversed(matches):
        try:
            value = float(m.group("num"))
        except ValueError:
            continue
        return ParsedAnswer(raw_line=m.group(0), value=value, unit=None)

    return None


# ---------------------------------------------------------------------------
# LLM-as-judge for method correctness.
# ---------------------------------------------------------------------------
_METHOD_JUDGE_SYSTEM = (
    "You are an astrodynamics grader. You are given the canonical method for "
    "solving a problem and a student's full work. Decide whether the student "
    "used a method *consistent* with the canonical one. Algebraically "
    "equivalent rearrangements count as consistent. Reaching the right "
    "number by a wrong method (e.g. coincidence, dimensional luck) does NOT "
    "count as consistent. Respond with strict JSON only:\n"
    '  {"method_correct": true|false, "rationale": "<one sentence>"}'
)


def judge_method(
    *,
    judge_client,
    judge_model: str,
    problem_statement: str,
    canonical_method: list[str],
    model_trace: str,
) -> tuple[Optional[bool], Optional[str]]:
    """Ask the frontier model to judge method correctness.

    Returns (method_correct, rationale). Either may be None on failure to
    parse — never raises; the caller continues with method_correct=None.
    """
    if judge_client is None:
        return None, None
    user = (
        f"PROBLEM:\n{problem_statement}\n\n"
        f"CANONICAL METHOD:\n- " + "\n- ".join(canonical_method) + "\n\n"
        f"STUDENT WORK (full trace):\n{model_trace}\n\n"
        f"Reply with JSON only."
    )
    try:
        resp = judge_client.chat(
            model=judge_model,
            messages=[
                {"role": "system", "content": _METHOD_JUDGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
        )
        text = resp.text
    except Exception as e:  # never let the judge kill a run
        return None, f"judge error: {e!r}"
    # Find the first JSON object in the response.
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None, f"unparseable judge response: {text[:200]!r}"
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, f"unparseable judge JSON: {m.group(0)[:200]!r}"
    mc = obj.get("method_correct")
    rationale = obj.get("rationale")
    if not isinstance(mc, bool):
        return None, f"missing method_correct in judge response: {obj!r}"
    return mc, rationale if isinstance(rationale, str) else None
