"""
Verify the dataset's "expected" values agree with what ground_truth.py
computes, and that every problem in the dataset has a registered fn.

If you add a problem and forget to register a function, this test points it
out. If you change a problem's `given` and forget to update `expected`,
this test fires.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasets.astrodynamics.ground_truth import compute, known_ids


DATASET = Path(__file__).resolve().parent.parent / "datasets" / "astrodynamics" / "v1.json"


def _problems() -> list[dict]:
    return json.loads(DATASET.read_text())


@pytest.fixture(scope="module")
def problems():
    return _problems()


def test_dataset_loads(problems):
    assert len(problems) >= 8, "Seed at least 8 problems."


def test_every_problem_has_required_fields(problems):
    required = {
        "id", "topic", "structure", "statement", "given", "expected",
        "tolerance_rel", "oracle_cards", "canonical_method",
    }
    for p in problems:
        missing = required - p.keys()
        assert not missing, f"{p.get('id')} missing fields: {missing}"
        assert p["expected"].keys() == {"value", "unit"}
        assert p["structure"] in {"single", "multi"}
        for card in p["oracle_cards"]:
            assert "name" in card and "equation" in card


def test_every_problem_has_registered_gt(problems):
    ids = [p["id"] for p in problems]
    registered = set(known_ids())
    missing = [i for i in ids if i not in registered]
    assert not missing, f"No ground_truth fn for: {missing}"


def test_expected_matches_computed(problems):
    """The crown jewel: the dataset's expected values must match ground_truth.py."""
    rel_tol = 1e-9
    bad = []
    for p in problems:
        value, unit = compute(p["id"], p["given"])
        exp_value = p["expected"]["value"]
        exp_unit = p["expected"]["unit"]
        if unit != exp_unit:
            bad.append((p["id"], f"unit mismatch: computed {unit}, JSON {exp_unit}"))
            continue
        rel_err = abs(value - exp_value) / abs(exp_value) if exp_value != 0 else abs(value)
        if rel_err > rel_tol:
            bad.append((p["id"], f"value mismatch: computed {value!r}, JSON {exp_value!r} (rel_err={rel_err:.2e})"))
    assert not bad, "Dataset is out of sync with ground_truth.py:\n" + "\n".join(f"  {pid}: {msg}" for pid, msg in bad)


def test_ids_unique(problems):
    ids = [p["id"] for p in problems]
    assert len(ids) == len(set(ids)), "duplicate problem ids in dataset"


def test_mix_of_single_and_multi(problems):
    structures = {p["structure"] for p in problems}
    assert {"single", "multi"} <= structures, "Need both single- and multi-step problems."
