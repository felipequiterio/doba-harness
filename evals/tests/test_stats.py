from __future__ import annotations

import pytest

from lib import analyze, stats


def _runs(cond: str, outcomes: dict[str, list[bool]], **extra) -> list[dict]:
    return [
        {"id": pid, "condition": cond, "answer_correct": ok, **extra}
        for pid, oks in outcomes.items() for ok in oks
    ]


def test_sign_test_exact_values():
    assert stats.sign_test_p(0, 0) == 1.0
    assert stats.sign_test_p(5, 0) == pytest.approx(0.0625)      # 2 · 0.5^5
    assert stats.sign_test_p(10, 0) == pytest.approx(2 / 1024)
    assert stats.sign_test_p(3, 3) == 1.0


def test_rate_ci_clusters_by_problem():
    # 10 problems × 3 runs, each problem all-pass or all-fail: 5 pass.
    runs = _runs("C3", {f"p{i}": [i < 5] * 3 for i in range(10)})
    ci = stats.rate_ci(runs)
    assert ci.rate == pytest.approx(0.5)
    assert ci.n_problems == 10 and ci.n_runs == 30
    assert ci.lo < 0.5 < ci.hi
    # Resampling problems (n=10), not runs (n=30): the interval stays wide.
    assert ci.hi - ci.lo > 0.4


def test_rate_ci_degenerate_when_every_problem_agrees():
    ci = stats.rate_ci(_runs("C2", {f"p{i}": [True, True] for i in range(4)}))
    assert (ci.rate, ci.lo, ci.hi) == (1.0, 1.0, 1.0)


def test_paired_uses_only_shared_problems():
    runs = (_runs("C3", {"p1": [True] * 3, "p2": [True] * 3, "p3": [True] * 3, "only_a": [True]})
            + _runs("C2", {"p1": [False] * 3, "p2": [True] * 3, "p3": [False] * 3, "only_b": [False]}))
    res = stats.paired(runs, "C3", "C2")
    assert res.n_problems == 3
    assert (res.wins, res.losses, res.ties) == (2, 0, 1)
    assert res.delta == pytest.approx(2 / 3)
    assert res.p_sign == pytest.approx(0.5)      # 2 wins, 0 losses: far from significant


def test_paired_consistent_win_has_ci_above_zero():
    a = _runs("C3", {f"p{i}": [True] * 3 for i in range(8)})
    b = _runs("C2", {f"p{i}": [False] * 3 for i in range(8)})
    res = stats.paired(a + b, "C3", "C2")
    assert res.lo > 0 and res.p_sign == pytest.approx(2 / 256)


def test_summary_includes_problem_level_section():
    runs = (_runs("C2", {"p1": [False], "p2": [True]}, knowledge_label="absent", structure="single",
                  latency_s=1.0, n_tool_calls=1, peak_context_tokens=900)
            + _runs("C3", {"p1": [True], "p2": [True]}, knowledge_label="absent", structure="single",
                    latency_s=1.0, n_tool_calls=1, peak_context_tokens=1200))
    md = analyze._summary_md(runs, [])
    assert "## 4b. Paired comparisons" in md
    assert "C3 − C2: +50 pp" in md
    assert "| C3 |" in md and "1200" in md
