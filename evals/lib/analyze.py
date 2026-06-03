"""
Aggregate runs.jsonl into summary.md + charts.

Decisive readouts:
  1. Success rate per condition (overall + by knowledge_label × structure).
  2. C2 − C1 on knowledge=absent (calculator alone closing the verification gap).
  3. C3 − C2 on knowledge=absent (oracle adding on top of calculator).
  4. Performance on structure=multi by condition (does the reasoning ceiling
     show even with oracle?).
  5. % of REF performance recovered by C1 / C2 / C3.

Matplotlib charts: one bar chart per readout into results/charts/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


def _here() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_runs(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Pivots.
# ---------------------------------------------------------------------------
@dataclass
class Cell:
    n: int = 0
    n_correct: int = 0
    n_correct_strict: int = 0      # correct via real FINAL_ANSWER only (no salvage)
    n_salvaged: int = 0            # correct only because of salvage extraction
    n_method_correct: int = 0
    n_judged: int = 0              # denominator for method_correct (judge may return None)

    def add(self, run: dict[str, Any]) -> None:
        self.n += 1
        ok = bool(run.get("answer_correct"))
        salvaged = bool(run.get("salvaged"))
        if ok:
            self.n_correct += 1
            if salvaged:
                self.n_salvaged += 1
            else:
                self.n_correct_strict += 1
        mc = run.get("method_correct")
        if mc is True:
            self.n_method_correct += 1
        if mc is not None:
            self.n_judged += 1

    @property
    def rate(self) -> Optional[float]:
        return self.n_correct / self.n if self.n else None

    @property
    def strict_rate(self) -> Optional[float]:
        return self.n_correct_strict / self.n if self.n else None

    @property
    def method_rate(self) -> Optional[float]:
        return self.n_method_correct / self.n_judged if self.n_judged else None


def _pivot(runs: list[dict[str, Any]], *keys: str) -> dict[tuple, Cell]:
    out: dict[tuple, Cell] = defaultdict(Cell)
    for r in runs:
        k = tuple(r.get(k) for k in keys)
        out[k].add(r)
    return dict(out)


def _fmt_rate(rate: Optional[float], n: int) -> str:
    if rate is None:
        return f"– (n={n})"
    return f"{rate*100:5.1f}% (n={n})"


# ---------------------------------------------------------------------------
# Markdown summary.
# ---------------------------------------------------------------------------
def _summary_md(runs: list[dict[str, Any]], chart_paths: list[str]) -> str:
    if not runs:
        return "# 001 — astrodynamics failure modes\n\nNo runs to analyze.\n"

    conditions = sorted({r["condition"] for r in runs})
    knowledge_labels = sorted({r.get("knowledge_label") for r in runs}, key=lambda x: (x is None, x or ""))
    structures = sorted({r.get("structure") for r in runs})

    by_cond = _pivot(runs, "condition")
    by_cond_struct = _pivot(runs, "condition", "structure")
    by_cond_kl = _pivot(runs, "condition", "knowledge_label")
    by_cond_kl_struct = _pivot(runs, "condition", "knowledge_label", "structure")

    lines = []
    lines.append("# 001 — astrodynamics failure modes — summary\n")
    lines.append(f"Total runs: {len(runs)}  · conditions: {', '.join(conditions)}\n")
    lines.append("")

    # 1. Per-condition overall (including strict-vs-salvaged split).
    lines.append("## 1. Overall success rate by condition")
    lines.append("")
    lines.append("| Condition | answer_correct (any) | strict (no salvage) | salvaged | method_correct |")
    lines.append("|---|---|---|---|---|")
    for c in conditions:
        cell = by_cond.get((c,), Cell())
        salvaged_str = f"+{cell.n_salvaged}" if cell.n_salvaged else "0"
        lines.append(
            f"| {c} | {_fmt_rate(cell.rate, cell.n)} | "
            f"{_fmt_rate(cell.strict_rate, cell.n)} | {salvaged_str} | "
            f"{_fmt_rate(cell.method_rate, cell.n_judged)} |"
        )
    lines.append("")
    lines.append(
        "*`strict` = correct AND the model emitted `FINAL_ANSWER:` verbatim. "
        "`salvaged` = the answer was recovered from the trace tail because no "
        "`FINAL_ANSWER` line was present; these count as correct in the "
        "`any` column but are tracked separately for methodology auditing.*"
    )
    lines.append("")

    # 2. Per-condition × structure.
    lines.append("## 2. Success rate by condition × structure")
    lines.append("")
    header = "| Condition | " + " | ".join(str(s) for s in structures) + " |"
    sep = "|---|" + "|".join(["---"] * len(structures)) + "|"
    lines.append(header)
    lines.append(sep)
    for c in conditions:
        row = [c]
        for s in structures:
            cell = by_cond_struct.get((c, s), Cell())
            row.append(_fmt_rate(cell.rate, cell.n))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # 3. Per-condition × knowledge_label.
    lines.append("## 3. Success rate by condition × knowledge_label")
    lines.append("")
    header = "| Condition | " + " | ".join(str(k) for k in knowledge_labels) + " |"
    sep = "|---|" + "|".join(["---"] * len(knowledge_labels)) + "|"
    lines.append(header)
    lines.append(sep)
    for c in conditions:
        row = [c]
        for k in knowledge_labels:
            cell = by_cond_kl.get((c, k), Cell())
            row.append(_fmt_rate(cell.rate, cell.n))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # 4. The four decisive readouts.
    lines.append("## 4. Decisive readouts")
    lines.append("")

    def cell_rate(c: str, k: Optional[str] = None, s: Optional[str] = None) -> Optional[float]:
        if k is None and s is None:
            return by_cond.get((c,), Cell()).rate
        if s is None:
            return by_cond_kl.get((c, k), Cell()).rate
        if k is None:
            return by_cond_struct.get((c, s), Cell()).rate
        return by_cond_kl_struct.get((c, k, s), Cell()).rate

    def fmt(x: Optional[float]) -> str:
        return "–" if x is None else f"{x*100:.1f}%"

    def delta(a: Optional[float], b: Optional[float]) -> str:
        if a is None or b is None:
            return "–"
        return f"{(a-b)*100:+.1f} pp"

    c1 = cell_rate("C1")
    c2 = cell_rate("C2")
    c3 = cell_rate("C3")
    ref = cell_rate("REF")

    c1_abs = cell_rate("C1", "absent")
    c2_abs = cell_rate("C2", "absent")
    c3_abs = cell_rate("C3", "absent")

    c1_multi = cell_rate("C1", s="multi")
    c2_multi = cell_rate("C2", s="multi")
    c3_multi = cell_rate("C3", s="multi")
    ref_multi = cell_rate("REF", s="multi")

    def recovery(x: Optional[float]) -> str:
        if x is None or ref is None or ref == 0:
            return "–"
        return f"{(x/ref)*100:.0f}% of REF"

    lines.append("**a. Calculator's effect on knowledge=absent problems (C2 − C1)**")
    lines.append("Test: does adding a calculator help when the model doesn't know the formula?")
    lines.append(f"- C1 absent: {fmt(c1_abs)}  ·  C2 absent: {fmt(c2_abs)}  ·  Δ = {delta(c2_abs, c1_abs)}")
    lines.append("")
    lines.append("**b. Oracle's marginal effect on knowledge=absent problems (C3 − C2)** ← the decisive number")
    lines.append("Test: does injecting the formula close the gap that the calculator alone can't?")
    lines.append("If yes → mode-(a) dominates → retrieval is worth building.")
    lines.append(f"- C2 absent: {fmt(c2_abs)}  ·  C3 absent: {fmt(c3_abs)}  ·  Δ = {delta(c3_abs, c2_abs)}")
    lines.append("")
    lines.append("**c. Reasoning ceiling on structure=multi**")
    lines.append(f"- C1 multi: {fmt(c1_multi)}  ·  C2 multi: {fmt(c2_multi)}  ·  C3 multi: {fmt(c3_multi)}  ·  REF multi: {fmt(ref_multi)}")
    lines.append("")
    lines.append("**d. % of REF performance recovered (overall)**")
    lines.append(f"- C1: {recovery(c1)}  ·  C2: {recovery(c2)}  ·  C3: {recovery(c3)}")
    lines.append("")

    # REF_C3 ablation, if present — does the frontier with oracle beat itself
    # without oracle? If yes, even the frontier is knowledge-bottlenecked on
    # some problems → mode-(a) is real. If REF_C3 ≈ REF, the bare frontier
    # already has all the knowledge it needs.
    ref_c3 = cell_rate("REF_C3")
    if ref_c3 is not None:
        ref_c3_multi = cell_rate("REF_C3", s="multi")
        ref_c3_abs   = cell_rate("REF_C3", "absent")
        lines.append("**e. REF_C3 ablation — does the FRONTIER benefit from the oracle?**")
        lines.append(f"- REF: {fmt(ref)}  ·  REF_C3: {fmt(ref_c3)}  ·  Δ = {delta(ref_c3, ref)}")
        lines.append(f"  - on multi:           REF {fmt(ref_multi)}  →  REF_C3 {fmt(ref_c3_multi)}  ({delta(ref_c3_multi, ref_multi)})")
        lines.append(f"  - on knowledge=absent: REF {fmt(cell_rate('REF', 'absent'))}  →  REF_C3 {fmt(ref_c3_abs)}  ({delta(ref_c3_abs, cell_rate('REF', 'absent'))})")
        lines.append("")

    # 5. Charts.
    if chart_paths:
        lines.append("## 5. Charts")
        lines.append("")
        for cp in chart_paths:
            lines.append(f"![{Path(cp).name}]({cp})")
        lines.append("")

    # 6. Interpretation placeholders.
    lines.append("## 6. Interpretation (fill in)")
    lines.append("")
    lines.append("- Dominant failure mode (a-knowledge/verification or b-reasoning depth): _TBD by Felipe_")
    lines.append("- Is the dataset big / hard enough to support this conclusion? _TBD by Felipe_")
    lines.append("- Decision: build a retriever, or expand the dataset / try a bigger model? _TBD by Felipe_")
    lines.append("")

    # 7. Stopped-reason breakdown — sanity check.
    lines.append("## 7. Stopped-reason breakdown")
    lines.append("")
    sr = defaultdict(int)
    for r in runs:
        sr[r.get("stopped_reason", "?")] += 1
    for k, v in sorted(sr.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {v}")
    lines.append("")

    # 8. Latency / tool-call summary.
    lines.append("## 8. Latency / tool-call medians")
    lines.append("")
    lines.append("| Condition | median latency (s) | median n_tool_calls |")
    lines.append("|---|---|---|")
    by_cond_runs = defaultdict(list)
    for r in runs:
        by_cond_runs[r["condition"]].append(r)
    for c in conditions:
        rs = by_cond_runs[c]
        lat = [r["latency_s"] for r in rs if r.get("latency_s") is not None]
        tc = [r["n_tool_calls"] for r in rs if r.get("n_tool_calls") is not None]
        lat_m = statistics.median(lat) if lat else None
        tc_m = statistics.median(tc) if tc else None
        lines.append(f"| {c} | {('-' if lat_m is None else f'{lat_m:.2f}')} | {('-' if tc_m is None else f'{tc_m:.1f}')} |")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Charts.
# ---------------------------------------------------------------------------
def _chart_rel(p: Path, rel_root: Optional[Path]) -> str:
    """Render a chart's path relative to `rel_root` (typically the summary
    file's parent dir) so the markdown image link works. Falls back to a
    plain absolute path if the chart isn't under rel_root."""
    if rel_root is None:
        return str(p)
    try:
        return str(p.relative_to(rel_root))
    except ValueError:
        return str(p)


def _try_charts(runs: list[dict[str, Any]], out_dir: Path, *, rel_root: Optional[Path] = None) -> list[str]:
    try:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        print("matplotlib not installed; skipping charts", file=sys.stderr)
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    conditions = sorted({r["condition"] for r in runs})
    paths: list[str] = []

    # Chart 1: overall success rate.
    by_cond = _pivot(runs, "condition")
    rates = [by_cond.get((c,), Cell()).rate or 0.0 for c in conditions]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(conditions, [r * 100 for r in rates])
    ax.set_ylim(0, 100)
    ax.set_ylabel("success rate (%)")
    ax.set_title("Overall success rate by condition")
    p = out_dir / "overall_by_condition.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    paths.append(_chart_rel(p, rel_root))

    # Chart 2: by structure.
    structures = sorted({r.get("structure") for r in runs})
    by_cs = _pivot(runs, "condition", "structure")
    import numpy as np
    x = np.arange(len(conditions))
    width = 0.35
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, s in enumerate(structures):
        ys = [(by_cs.get((c, s), Cell()).rate or 0.0) * 100 for c in conditions]
        ax.bar(x + (i - (len(structures)-1)/2) * width, ys, width, label=str(s))
    ax.set_xticks(x); ax.set_xticklabels(conditions)
    ax.set_ylim(0, 100); ax.set_ylabel("success rate (%)")
    ax.set_title("Success rate by condition × structure")
    ax.legend()
    p = out_dir / "by_structure.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    paths.append(_chart_rel(p, rel_root))

    # Chart 3: by knowledge_label.
    labels = sorted({r.get("knowledge_label") for r in runs}, key=lambda x: (x is None, x or ""))
    by_ck = _pivot(runs, "condition", "knowledge_label")
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, k in enumerate(labels):
        ys = [(by_ck.get((c, k), Cell()).rate or 0.0) * 100 for c in conditions]
        ax.bar(x + (i - (len(labels)-1)/2) * width, ys, width, label=str(k))
    ax.set_xticks(x); ax.set_xticklabels(conditions)
    ax.set_ylim(0, 100); ax.set_ylabel("success rate (%)")
    ax.set_title("Success rate by condition × knowledge_label")
    ax.legend()
    p = out_dir / "by_knowledge.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    paths.append(_chart_rel(p, rel_root))

    return paths


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate runs.jsonl into summary.md + charts.")
    ap.add_argument("-r", "--runs", default=None, help="Path to runs.jsonl.")
    ap.add_argument("-s", "--summary", default=None, help="Path to summary.md output.")
    ap.add_argument("-c", "--charts", default=None, help="Path to charts directory.")
    ap.add_argument("--no-charts", action="store_true", help="Skip chart generation.")
    ap.add_argument("--config", default=None,
                    help="Path to an alternate config.yaml; `paths:` resolves relative to its directory.")
    ap.add_argument("--run-id", default=None,
                    help="Timestamped subdir under runs_root (e.g. 2026-06-01T14-30-22). "
                         "Defaults to the `latest` symlink.")
    ap.add_argument("--run-dir", default=None,
                    help="Absolute path to a run directory containing runs.jsonl. "
                         "Overrides --run-id and config-based defaults.")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config).resolve() if args.config else (_here() / "config.yaml")
    cfg_dir = cfg_path.parent
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(cfg_path.read_text())
    except Exception:
        cfg = {"paths": {"runs_root": "results"}}

    def _resolve(rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (cfg_dir / p).resolve()

    # Resolve which run directory to analyze. Priority:
    #   1. --run-dir <abs path>
    #   2. --run-id <timestamp>  → <runs_root>/<timestamp>
    #   3. <runs_root>/latest (symlink)
    runs_root = _resolve((cfg.get("paths") or {}).get("runs_root", "results"))
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
    elif args.run_id:
        run_dir = runs_root / args.run_id
    else:
        run_dir = runs_root / "latest"

    runs_path = Path(args.runs) if args.runs else (run_dir / "runs.jsonl")
    summary_path = Path(args.summary) if args.summary else (run_dir / "summary.md")
    charts_dir = Path(args.charts) if args.charts else (run_dir / "charts")

    if not runs_path.exists():
        print(f"runs file not found: {runs_path}", file=sys.stderr)
        return 1

    runs = _load_runs(runs_path)
    chart_paths = [] if args.no_charts else _try_charts(runs, charts_dir, rel_root=summary_path.parent)
    md = _summary_md(runs, chart_paths)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(md)
    print(f"wrote {summary_path}  ({len(runs)} runs, {len(chart_paths)} charts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
