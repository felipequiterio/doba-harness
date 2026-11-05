"""
Live progress output for `python -m lib.run`.

What it shows:
  - A header banner before the sweep starts (dataset / conditions / models).
  - A separator line each time we move to a new problem.
  - One annotated line per (problem, condition, run_idx) with a progress
    counter `[done/total]`, ✓/✗ for answer_correct, the stopped_reason, a
    short token/latency summary, the running success rate, and a rolling ETA.
  - A footer with per-condition headline stats and total wall time.

Behavior:
  - ANSI colors when stderr is a TTY. Stripped silently when piped/redirected.
  - All output goes to stderr so the orchestrator's stdout (`wrote N runs…`)
    stays scriptable.
"""

from __future__ import annotations

import collections
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# ANSI colors. Disabled when stderr isn't a terminal.
# ---------------------------------------------------------------------------
_IS_TTY = sys.stderr.isatty()


def _c(code: str) -> str:
    return code if _IS_TTY else ""


RESET   = _c("\033[0m")
BOLD    = _c("\033[1m")
DIM     = _c("\033[2m")
RED     = _c("\033[31m")
GREEN   = _c("\033[32m")
YELLOW  = _c("\033[33m")
BLUE    = _c("\033[34m")
CYAN    = _c("\033[36m")

_BAR = "═" * 78
_SUB = "─" * 78


def _fmt_dur(seconds: float) -> str:
    """Human-friendly duration: '3.2s' | '2m 14s' | '1h 02m'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


def _emit(line: str = "") -> None:
    print(line, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Tracker — passed once to the orchestrator, called per-run.
# ---------------------------------------------------------------------------
@dataclass
class ProgressTracker:
    total: int
    conditions: list[str]
    # rolling window for the ETA estimate — last N call latencies (wall clock).
    _latencies: collections.deque = field(default_factory=lambda: collections.deque(maxlen=20))
    _per_cond: dict[str, dict[str, Any]] = field(default_factory=dict)
    _correct: int = 0
    _done: int = 0
    _t_start: float = field(default_factory=time.monotonic)
    _last_problem_id: Optional[str] = None

    def __post_init__(self) -> None:
        self._cond_width = max(len(c) for c in self.conditions) if self.conditions else 3
        for c in self.conditions:
            self._per_cond[c] = {
                "n": 0,
                "n_correct": 0,
                "latencies": [],
                "tps": [],
                "tokens": [],
            }

    # ---- header / footer ---------------------------------------------------
    def header(
        self,
        *,
        n_problems_total: int,
        n_problems_run: int,
        n_singles: int,
        n_multi: int,
        n_runs: int,
        ref_condition: str,
        local_label: str,
        frontier_label: Optional[str],
        judge_enabled: bool,
        logs_dir: Optional[str],
        stub: bool,
    ) -> None:
        title = "001 — astrodynamics failure modes"
        if stub:
            title += "  [STUB MODE]"
        _emit(f"{CYAN}{_BAR}{RESET}")
        _emit(f" {BOLD}{title}{RESET}")
        _emit(f"{CYAN}{_BAR}{RESET}")
        cond_str = ", ".join(self.conditions)
        sel_str = f"all {n_problems_total}" if n_problems_run == n_problems_total else f"{n_problems_run} selected (of {n_problems_total})"
        _emit(f" {DIM}dataset:   {RESET}{n_problems_total} problems  ({n_singles} single, {n_multi} multi)  →  {sel_str}")
        _emit(f" {DIM}conditions:{RESET}{cond_str}                     →  {len(self.conditions)} × {n_runs} runs = {BOLD}{self.total}{RESET} total cells")
        _emit(f" {DIM}local:     {RESET}{local_label}")
        if frontier_label:
            def _strip_think_suffix(c: str) -> str:
                for suf in ("_NT", "_T"):
                    if c.endswith(suf):
                        return c[: -len(suf)]
                return c
            bases = {_strip_think_suffix(c) for c in self.conditions}
            extras = []
            if "REF" in bases:
                extras.append(f"REF→{ref_condition} prompt")
            if "REF_C3" in bases:
                extras.append("REF_C3→C3 prompt")
            qual = f"  {DIM}({', '.join(extras)}){RESET}" if extras else ""
            _emit(f" {DIM}frontier:  {RESET}{frontier_label}{qual}")
        _emit(f" {DIM}judge:     {RESET}{'enabled (frontier)' if judge_enabled else 'disabled'}")
        if logs_dir:
            _emit(f" {DIM}logs:      {RESET}{logs_dir}/  (one .log per run)")
        _emit(f"{CYAN}{_BAR}{RESET}")
        _emit()

    def footer(self) -> None:
        dt = time.monotonic() - self._t_start
        _emit()
        _emit(f"{CYAN}{_BAR}{RESET}")
        _emit(f" {BOLD}done in {_fmt_dur(dt)}{RESET}  ·  {self._correct}/{self._done} correct overall ({self._pct(self._correct, self._done)})")
        _emit(f"{CYAN}{_BAR}{RESET}")
        for cond in self.conditions:
            s = self._per_cond[cond]
            n = s["n"]
            if n == 0:
                continue
            med_lat = _median(s["latencies"])
            med_tps = _median(s["tps"])
            med_tok = _median(s["tokens"])
            color = GREEN if s["n_correct"] == n else (YELLOW if s["n_correct"] > 0 else RED)
            _emit(
                f"  {BOLD}{cond:>3}{RESET}: "
                f"{color}{s['n_correct']}/{n} correct ({self._pct(s['n_correct'], n)}){RESET}  "
                f"{DIM}·{RESET} median {_fmt_dur(med_lat)}  "
                f"{DIM}·{RESET} median {med_tps:.1f} tk/s  "
                f"{DIM}·{RESET} median {int(med_tok):,} tok"
            )
        _emit(f"{CYAN}{_BAR}{RESET}")

    # ---- per-row callback --------------------------------------------------
    def problem_header(self, problem: dict[str, Any]) -> None:
        if problem["id"] == self._last_problem_id:
            return
        self._last_problem_id = problem["id"]
        topic = problem.get("topic", "?")
        struct = problem.get("structure", "?")
        klabel = problem.get("knowledge_label")
        meta = f"{topic}, {struct}"
        if klabel:
            meta += f", knowledge={klabel}"
        _emit(f"{DIM}{_SUB}{RESET}")
        _emit(f" {BOLD}{problem['id']}{RESET}  {DIM}({meta}){RESET}")

    def emit_run(
        self,
        *,
        problem: dict[str, Any],
        condition: str,
        run_idx: int,
        score,
        run,
    ) -> None:
        self._done += 1
        ok = bool(score.answer_correct)
        if ok:
            self._correct += 1
        self._latencies.append(run.latency_s)

        cell = self._per_cond.setdefault(condition, {"n": 0, "n_correct": 0, "latencies": [], "tps": [], "tokens": []})
        cell["n"] += 1
        if ok:
            cell["n_correct"] += 1
        cell["latencies"].append(run.latency_s)
        cell["tps"].append(run.tokens_per_second)
        cell["tokens"].append(run.total_tokens)

        # ETA from rolling window — wall-clock per cell.
        if self._latencies:
            avg_lat = sum(self._latencies) / len(self._latencies)
            remaining = max(0, self.total - self._done)
            eta_s = avg_lat * remaining
        else:
            eta_s = 0.0

        ok_glyph = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        stop = run.stopped_reason
        stop_color = GREEN if stop == "final_answer" else YELLOW if stop in {"max_tool_turns", "max_turns"} else RED
        progress = f"[{self._done:>{len(str(self.total))}}/{self.total}]"
        running_pct = self._pct(self._correct, self._done)

        _emit(
            f" {DIM}{progress}{RESET} "
            f"{condition:>{self._cond_width}} r{run_idx}  "
            f"{ok_glyph}  "
            f"{stop_color}{stop:<14}{RESET}  "
            f"{DIM}tok={RESET}{run.total_tokens:>6,}  "
            f"{DIM}tk/s={RESET}{run.tokens_per_second:>5.1f}  "
            f"{DIM}lat={RESET}{_fmt_dur(run.latency_s):>7}  "
            f"{DIM}·{RESET} acc {running_pct}  "
            f"{DIM}·{RESET} eta {_fmt_dur(eta_s)}"
        )

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def _pct(num: int, den: int) -> str:
        if den == 0:
            return "–"
        return f"{(num / den) * 100:>4.0f}%"


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    if n % 2:
        return ys[n // 2]
    return 0.5 * (ys[n // 2 - 1] + ys[n // 2])
