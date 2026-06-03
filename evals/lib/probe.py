"""
Knowledge probe.

For each problem, ask the local 4B for the underlying formula(s) *without*
showing it the problem's numbers. The frontier model then judges whether
the 4B produced a formula consistent with the canonical method. The label
goes back into the dataset JSON as `knowledge_label = "present" | "absent"`.

This label is what lets `analyze.py` answer the core question:

> On problems the 4B doesn't know, does C3 (oracle + calculator) close the
> gap that C2 (calculator alone) cannot?

Usage:

    python -m src.probe                  # uses local + frontier from config.yaml
    python -m src.probe --stub           # canned responses for wiring tests
    python -m src.probe --in-place       # writes labels back into dataset JSON
    python -m src.probe -o probe.json    # write a sidecar file instead
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from .models import StubClient, make_client


_PROBE_PROMPT = (
    "You are given the *topic* of an astrodynamics problem, NOT the problem "
    "itself. State the formula(s) you would use, plus the meaning of each "
    "symbol, in plain text. Do not invent numbers. Do not solve anything.\n\n"
    "Topic: {topic}\n\n"
    "Reply concisely. Format: bullet list of formula(s) and definitions."
)


_JUDGE_PROMPT_SYSTEM = (
    "You are grading whether a student knows the formula(s) for an "
    "astrodynamics topic. You are given the canonical method (list of "
    "formula names) and the student's reply. Decide whether the student "
    "produced *at least* the core formula(s) of the canonical method. "
    "Reasonable rearrangements / equivalent forms count. Wrong physics or "
    "missing the core formula does NOT count. Reply with strict JSON only:\n"
    '  {"present": true|false, "rationale": "<one sentence>"}'
)


def _judge_prompt_user(canonical_method: list[str], student_reply: str) -> str:
    return (
        "CANONICAL METHOD:\n- " + "\n- ".join(canonical_method) + "\n\n"
        f"STUDENT REPLY:\n{student_reply}\n\n"
        "Reply with JSON only."
    )


def probe_problem(
    *,
    problem: dict[str, Any],
    probe_client,
    probe_model: str,
    judge_client,
    judge_model: str,
    temperature: float = 0.2,
) -> dict[str, Any]:
    student_reply = probe_client.chat(
        model=probe_model,
        messages=[
            {"role": "system", "content": "You are a concise physics tutor."},
            {"role": "user", "content": _PROBE_PROMPT.format(topic=problem["topic"])},
        ],
        temperature=temperature,
    ).text

    judge_text = judge_client.chat(
        model=judge_model,
        messages=[
            {"role": "system", "content": _JUDGE_PROMPT_SYSTEM},
            {"role": "user", "content": _judge_prompt_user(problem["canonical_method"], student_reply)},
        ],
        temperature=0.0,
    ).text

    m = re.search(r"\{.*?\}", judge_text, re.DOTALL)
    if not m:
        return {"label": None, "rationale": f"unparseable judge: {judge_text[:200]!r}", "reply": student_reply}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"label": None, "rationale": f"bad JSON: {m.group(0)[:200]!r}", "reply": student_reply}
    present = obj.get("present")
    rationale = obj.get("rationale")
    if not isinstance(present, bool):
        return {"label": None, "rationale": f"missing present in {obj!r}", "reply": student_reply}
    return {
        "label": "present" if present else "absent",
        "rationale": rationale if isinstance(rationale, str) else None,
        "reply": student_reply,
    }


def _stub_probe_policy(messages, model, ctx):
    """Stub: alternates 'student' and 'judge' replies for offline runs."""
    sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
    if "grading" in sys_content:
        # Judge call.
        return '{"present": true, "rationale": "stub: assume present"}'
    # Probe call.
    return "- formula F = M / r (stub)\n- v = sqrt(mu/r) (stub)"


def _load_config(config_path: Optional[Path] = None) -> tuple[dict[str, Any], Path]:
    """Load YAML config; return (cfg_dict, cfg_dir). Relative paths in the
    config resolve against `cfg_dir`."""
    here = Path(__file__).resolve().parent.parent
    cfg_path = (config_path.resolve() if config_path else (here / "config.yaml"))
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError("PyYAML required: pip install pyyaml") from e
    return yaml.safe_load(cfg_path.read_text()), cfg_path.parent


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Label each problem as knowledge=present|absent.")
    ap.add_argument("--stub", action="store_true", help="Use a canned-response client; no API keys needed.")
    ap.add_argument("--in-place", action="store_true", help="Write labels back into dataset JSON.")
    ap.add_argument("-o", "--out", default=None, help="Write sidecar JSON to this path.")
    ap.add_argument("--config", default=None, help="Path to an alternate config.yaml.")
    args = ap.parse_args(argv)

    cfg, cfg_dir = _load_config(Path(args.config) if args.config else None)
    rel = cfg["paths"]["dataset"]
    dataset_path = Path(rel) if Path(rel).is_absolute() else (cfg_dir / rel).resolve()
    problems = json.loads(dataset_path.read_text())

    if args.stub:
        probe_client = StubClient(policy=_stub_probe_policy)
        judge_client = probe_client
        probe_model = "stub-local"
        judge_model = "stub-judge"
    else:
        probe_client = make_client(cfg["local_model"])
        judge_client = make_client(cfg["frontier_model"])
        probe_model = cfg["local_model"]["model_name"]
        judge_model = cfg["frontier_model"]["model_name"]

    results: list[dict[str, Any]] = []
    for p in problems:
        print(f"  probing {p['id']} ({p['topic']}) ...", file=sys.stderr)
        info = probe_problem(
            problem=p,
            probe_client=probe_client,
            probe_model=probe_model,
            judge_client=judge_client,
            judge_model=judge_model,
            temperature=cfg.get("temperature", 0.2),
        )
        info["id"] = p["id"]
        results.append(info)
        if args.in_place:
            p["knowledge_label"] = info["label"]

    if args.in_place:
        dataset_path.write_text(json.dumps(problems, indent=2) + "\n")
        print(f"updated {dataset_path} (in-place).", file=sys.stderr)

    out_path = args.out
    if out_path is None and not args.in_place:
        out_path = str(dataset_path.parent / "probe_results.json")
    if out_path:
        Path(out_path).write_text(json.dumps(results, indent=2) + "\n")
        print(f"wrote {out_path}", file=sys.stderr)

    n_present = sum(1 for r in results if r["label"] == "present")
    n_absent = sum(1 for r in results if r["label"] == "absent")
    n_unk = sum(1 for r in results if r["label"] is None)
    print(f"summary: present={n_present}  absent={n_absent}  unknown={n_unk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
