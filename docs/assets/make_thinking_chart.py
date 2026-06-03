"""Generate the multi-vs-single thinking-mode breakdown chart for draft_3 §4.6.

Reads the 003 long run (C3_NT vs C3_T, N=3) and plots success rate by
condition × structure, annotated with the per-structure deltas. Run:

    cd evals && uv run python ../docs/assets/make_thinking_chart.py
"""
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RUN = Path(__file__).resolve().parents[2] / (
    "evals/003-thinking-with-oracle/results/2026-06-03T03-02-27/runs.jsonl"
)
OUT = Path(__file__).resolve().parent / "003_thinking_by_structure.png"

rows = [json.loads(l) for l in RUN.open()]

# success rate by (condition, structure)
agg = defaultdict(lambda: [0, 0])  # key -> [correct, total]
for r in rows:
    k = (r["condition"], r["structure"])
    agg[k][0] += int(bool(r["answer_correct"]))
    agg[k][1] += 1

conditions = ["C3_NT", "C3_T"]
structures = ["single", "multi"]
labels = {"C3_NT": "no-think (C3_NT)", "C3_T": "think (C3_T)"}
colors = {"single": "#4C78A8", "multi": "#F58518"}

x = np.arange(len(conditions))
width = 0.36

fig, ax = plt.subplots(figsize=(7, 4.5))
for i, s in enumerate(structures):
    ys = [100 * agg[(c, s)][0] / agg[(c, s)][1] for c in conditions]
    ns = [agg[(c, s)][1] for c in conditions]
    bars = ax.bar(x + (i - 0.5) * width, ys, width, label=s, color=colors[s])
    for b, y, n in zip(bars, ys, ns):
        ax.text(b.get_x() + b.get_width() / 2, y + 1.5, f"{y:.0f}%\nn={n}",
                ha="center", va="bottom", fontsize=8)

# delta annotations — one per structure, placed clear of the bars
for s, xpos in (("single", 0.0), ("multi", 1.0)):
    nt = 100 * agg[("C3_NT", s)][0] / agg[("C3_NT", s)][1]
    t = 100 * agg[("C3_T", s)][0] / agg[("C3_T", s)][1]
    sign = "+" if t >= nt else "−"
    txt = f"think {sign}{abs(t-nt):.0f}pp on {s}\n({nt:.0f}% → {t:.0f}%)"
    ax.annotate(txt, xy=(xpos, 108), ha="center", va="center", fontsize=8.5,
                color=colors[s], fontweight="bold")

ax.set_xticks(x)
ax.set_xticklabels([labels[c] for c in conditions])
ax.set_ylim(0, 115)
ax.set_ylabel("success rate (%)")
ax.set_title("003 (oracle always on, N=3): thinking flips sign by problem structure")
ax.legend(title="structure", loc="lower right", framealpha=0.9)
ax.axhline(0, color="black", lw=0.5)
fig.tight_layout()
fig.savefig(OUT, dpi=120)
print(f"wrote {OUT}")

# print the numbers it encodes for the doc
for s in structures:
    for c in conditions:
        cor, tot = agg[(c, s)]
        print(f"{c} {s}: {cor}/{tot} = {100*cor/tot:.0f}%")
