"""Tests for FINAL_ANSWER parsing, SI normalization, and scoring."""

from __future__ import annotations

import pytest

from lib.scoring import (
    ParsedAnswer,
    parse_final_answer,
    score_answer,
    to_si,
)


class TestParse:
    def test_basic(self):
        p = parse_final_answer("blah blah\nFINAL_ANSWER: 7668.6 m/s\n")
        assert p is not None and p.value == pytest.approx(7668.6)
        assert p.unit == "m/s"

    def test_scientific(self):
        p = parse_final_answer("FINAL_ANSWER: 7.668e3 m/s")
        assert p.value == pytest.approx(7668.0)
        assert p.unit == "m/s"

    def test_negative(self):
        p = parse_final_answer("FINAL_ANSWER: -2.94e7 J/kg")
        assert p.value == pytest.approx(-2.94e7)
        assert p.unit == "J/kg"

    def test_no_unit(self):
        p = parse_final_answer("FINAL_ANSWER: 5553")
        assert p.value == 5553
        assert p.unit is None

    def test_returns_none_when_absent(self):
        assert parse_final_answer("nothing relevant here") is None

    def test_returns_unparsed_when_no_number(self):
        p = parse_final_answer("FINAL_ANSWER: I don't know")
        assert p is not None
        assert p.value is None

    def test_takes_last_occurrence(self):
        p = parse_final_answer(
            "FINAL_ANSWER: 100 m/s\n... revised ...\nFINAL_ANSWER: 200 m/s"
        )
        assert p.value == 200.0

    def test_handles_markdown_bolding(self):
        p = parse_final_answer("**FINAL_ANSWER:** 42 m/s")
        assert p.value == 42 and p.unit == "m/s"

    def test_case_insensitive(self):
        p = parse_final_answer("Final_Answer: 42 m/s")
        assert p.value == 42 and p.unit == "m/s"


class TestSI:
    def test_km_per_s_to_m_per_s(self):
        v, u = to_si(7.668, "km/s")
        assert v == pytest.approx(7668.0) and u == "m/s"

    def test_hours_to_seconds(self):
        v, u = to_si(2.0, "hours")
        assert v == 7200 and u == "s"

    def test_unknown_unit_passes_through(self):
        v, u = to_si(1.0, "parsec")
        assert v == 1.0 and u is None

    def test_none_unit(self):
        v, u = to_si(1.0, None)
        assert v == 1.0 and u is None


class TestScore:
    def test_correct(self):
        s = score_answer(
            "Working...\nFINAL_ANSWER: 7668.6 m/s",
            expected_value=7668.6,
            expected_unit="m/s",
            tolerance_rel=0.01,
        )
        assert s.parsed
        assert s.answer_correct is True
        assert s.relative_error < 1e-9
        assert s.canonical_unit == "m/s"

    def test_within_tolerance(self):
        s = score_answer(
            "FINAL_ANSWER: 7700 m/s",
            expected_value=7668.6,
            expected_unit="m/s",
            tolerance_rel=0.01,
        )
        assert s.answer_correct is True
        assert s.relative_error < 0.01

    def test_outside_tolerance(self):
        s = score_answer(
            "FINAL_ANSWER: 8500 m/s",
            expected_value=7668.6,
            expected_unit="m/s",
            tolerance_rel=0.01,
        )
        assert s.answer_correct is False
        assert s.relative_error > 0.01

    def test_unit_conversion(self):
        # km/s should be normalized to m/s, then compared.
        s = score_answer(
            "FINAL_ANSWER: 7.6686 km/s",
            expected_value=7668.6,
            expected_unit="m/s",
            tolerance_rel=0.01,
        )
        assert s.answer_correct is True
        assert s.si_value == pytest.approx(7668.6)
        assert s.canonical_unit == "m/s"

    def test_right_magnitude_wrong_unit(self):
        # Same magnitude, no unit at all — should still match raw, flag the unit.
        s = score_answer(
            "FINAL_ANSWER: 7668.6",
            expected_value=7668.6,
            expected_unit="m/s",
            tolerance_rel=0.01,
        )
        assert s.right_magnitude_wrong_unit is True
        assert s.answer_correct is False
        assert s.unit_unknown is True

    def test_unparsed(self):
        s = score_answer(
            "I don't know.",
            expected_value=7668.6,
            expected_unit="m/s",
            tolerance_rel=0.01,
        )
        assert s.parsed is False
        assert s.answer_correct is False

    def test_dimensionally_wrong_unit_fails(self):
        # Says "s" when we wanted "m/s" — even if number matches, fail.
        s = score_answer(
            "FINAL_ANSWER: 7668.6 s",
            expected_value=7668.6,
            expected_unit="m/s",
            tolerance_rel=0.01,
        )
        assert s.answer_correct is False
