"""
Uncertainty for eval results — stdlib only.

Why not a plain binomial CI over runs: the N runs of one problem are not
independent (same prompt, temperature 0.2), so a problem tends to pass or
fail as a block. Treating 10 problems × 3 runs as n=30 independent trials
overstates precision ~√3×. The unit of evidence is the *problem*, so:

  - rates get a **cluster bootstrap** CI (resample problems, keep each
    problem's runs together);
  - condition comparisons are **paired by problem** (same problem under A and
    B), with a bootstrap CI on the mean per-problem difference and an exact
    **sign test** over the problems where A and B differ.

CLI, for any eval's runs.jsonl:

    python -m lib.stats results/latest/runs.jsonl                 # rate + CI per condition
    python -m lib.stats results/latest/runs.jsonl C3 C2           # paired C3 − C2
    python -m lib.stats runs.jsonl C3 C2 --where knowledge_label=absent
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_N_BOOT = 4000
DEFAULT_SEED = 1729


def _correct(run: dict[str, Any], metric: str) -> float:
    return 1.0 if run.get(metric) else 0.0


def per_problem_rates(
    runs: Iterable[dict[str, Any]], *, cluster: str = "id", metric: str = "answer_correct",
) -> dict[Any, float]:
    """Mean success per problem (cluster)."""
    acc: dict[Any, list[float]] = defaultdict(list)
    for r in runs:
        acc[r.get(cluster)].append(_correct(r, metric))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _percentile(sorted_xs: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted list, q in [0, 1]."""
    if not sorted_xs:
        raise ValueError("empty")
    pos = q * (len(sorted_xs) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def _bootstrap_mean_ci(values: list[float], *, n_boot: int, seed: int, alpha: float) -> tuple[float, float]:
    rng = random.Random(seed)
    k = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(k)) / k for _ in range(n_boot))
    return _percentile(means, alpha / 2), _percentile(means, 1 - alpha / 2)


@dataclass
class RateCI:
    rate: Optional[float]
    lo: Optional[float]
    hi: Optional[float]
    n_runs: int
    n_problems: int

    def fmt(self) -> str:
        if self.rate is None:
            return "–"
        return f"{self.rate*100:.0f}% [{self.lo*100:.0f}–{self.hi*100:.0f}]"


def rate_ci(
    runs: list[dict[str, Any]], *, cluster: str = "id", metric: str = "answer_correct",
    n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED, alpha: float = 0.05,
) -> RateCI:
    """Success rate with a cluster-bootstrap CI. The point estimate is the
    mean of per-problem rates (equal weight per problem), which equals the
    pooled rate when every problem has the same number of runs."""
    if not runs:
        return RateCI(None, None, None, 0, 0)
    rates = list(per_problem_rates(runs, cluster=cluster, metric=metric).values())
    point = sum(rates) / len(rates)
    lo, hi = _bootstrap_mean_ci(rates, n_boot=n_boot, seed=seed, alpha=alpha)
    return RateCI(point, lo, hi, len(runs), len(rates))


def sign_test_p(wins: int, losses: int) -> float:
    """Exact two-sided sign test (ties dropped). p=1 when there is no data."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


@dataclass
class Paired:
    a: str
    b: str
    n_problems: int
    delta: Optional[float]          # mean over problems of rate_a − rate_b
    lo: Optional[float]
    hi: Optional[float]
    wins: int                       # problems where A > B
    losses: int                     # problems where A < B
    ties: int
    p_sign: float

    def fmt(self) -> str:
        if self.delta is None:
            return f"{self.a} − {self.b}: no shared problems"
        return (f"{self.a} − {self.b}: {self.delta*100:+.0f} pp "
                f"[{self.lo*100:+.0f}, {self.hi*100:+.0f}] over {self.n_problems} problems · "
                f"better/worse/tie {self.wins}/{self.losses}/{self.ties} · sign-test p={self.p_sign:.2f}")


def paired(
    runs: list[dict[str, Any]], a: str, b: str, *, condition_key: str = "condition",
    cluster: str = "id", metric: str = "answer_correct",
    n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED, alpha: float = 0.05,
) -> Paired:
    """Compare condition A against B on the problems both were run on."""
    ra = per_problem_rates([r for r in runs if r.get(condition_key) == a], cluster=cluster, metric=metric)
    rb = per_problem_rates([r for r in runs if r.get(condition_key) == b], cluster=cluster, metric=metric)
    shared = sorted(set(ra) & set(rb), key=str)
    if not shared:
        return Paired(a, b, 0, None, None, None, 0, 0, 0, 1.0)
    diffs = [ra[p] - rb[p] for p in shared]
    wins = sum(d > 0 for d in diffs)
    losses = sum(d < 0 for d in diffs)
    lo, hi = _bootstrap_mean_ci(diffs, n_boot=n_boot, seed=seed, alpha=alpha)
    return Paired(a, b, len(shared), sum(diffs) / len(diffs), lo, hi,
                  wins, losses, len(diffs) - wins - losses, sign_test_p(wins, losses))


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Problem-level CIs and paired tests for a runs.jsonl.")
    ap.add_argument("runs", type=Path)
    ap.add_argument("conditions", nargs="*", help="two conditions A B → paired A − B")
    ap.add_argument("--where", action="append", default=[], metavar="KEY=VALUE",
                    help="filter runs, e.g. knowledge_label=absent (repeatable)")
    ap.add_argument("--metric", default="answer_correct")
    ap.add_argument("--condition-key", default="condition")
    args = ap.parse_args(argv)

    runs = _load(args.runs)
    for cond in args.where:
        k, _, v = cond.partition("=")
        runs = [r for r in runs if str(r.get(k)) == v]
    if not runs:
        print("no runs match", file=sys.stderr)
        return 1

    if len(args.conditions) == 2:
        print(paired(runs, *args.conditions, condition_key=args.condition_key, metric=args.metric).fmt())
        return 0
    if args.conditions:
        ap.error("pass exactly two conditions for a paired comparison, or none")

    by_cond: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_cond[str(r.get(args.condition_key))].append(r)
    for c in sorted(by_cond):
        ci = rate_ci(by_cond[c], metric=args.metric)
        print(f"{c:<14} {ci.fmt():<16} ({ci.n_problems} problems, {ci.n_runs} runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
