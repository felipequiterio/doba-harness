"""
card_builder — the ingest half of the Learning Loop, isolated.

Given a source passage (textbook text) and a `purpose` (what the card is
for), ask the model to extract the formula(s) into structured concept
card(s) in the SAME schema the dataset's oracle cards use:

    {"name": str, "equation": str, "variables": {sym: meaning}, "when": str}

The returned cards are injected through the EXACT same path as the oracle
cards (`conditions._oracle_block`), so eval 004 changes exactly one variable:
human-authored card vs model-authored card. Everything downstream — the
calculator protocol, scoring, ground truth — is unchanged.

This is deliberately a single model call (understand + interpret + integrate
collapsed). It is the cheapest possible prototype of Pillar 5; if even this
recovers the oracle's lift, the full pipeline is worth building.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional


_SYS = (
    "You are a knowledge-extraction assistant. You read a passage from an "
    "astrodynamics textbook and distill it into reusable REFERENCE CARDS that "
    "another solver will later use to work problems. You do not solve any "
    "problem yourself — you only extract the relevant formula(s) in a directly "
    "applicable form."
)


def _user_prompt(passage: str, purpose: str) -> str:
    return (
        f"TOPIC (what the card is for):\n{purpose}\n\n"
        f"PASSAGE:\n\"\"\"\n{passage.strip()}\n\"\"\"\n\n"
        "Extract the formula(s) needed for the TOPIC into JSON of the form:\n"
        "{\n"
        '  "cards": [\n'
        "    {\n"
        '      "name": "<short kebab-case name>",\n'
        '      "equation": "<the formula in plain ASCII: use sqrt(), ^, *, /, '
        "sin(), asin(), etc.>\",\n"
        '      "variables": { "<symbol>": "<meaning + SI unit>", ... },\n'
        '      "when": "<one line: when this formula applies / important '
        "caveats>\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Include EVERY formula needed to get from the given quantities to the "
        "target — if an intermediate (e.g. an eccentricity) is required first, "
        "add a card for it too.\n"
        "- If the passage gives the formula only via its components or a "
        "derivation, state the final closed-form equation the solver needs.\n"
        "- Keep equations symbolic (no numbers plugged in). Do NOT solve any "
        "specific problem.\n"
        "- Output ONLY the JSON object. No prose before or after."
    )


@dataclass
class BuiltCards:
    """Result of one card-build call."""
    cards: list[dict[str, Any]]          # injected via conditions._oracle_block
    raw_response: str
    parse_ok: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ---------------------------------------------------------------------------
# Parsing: pull the JSON object out of a possibly-chatty / fenced reply.
# ---------------------------------------------------------------------------
def _strip_think(text: str) -> str:
    """Remove a leading <think>...</think> block if the model emitted one."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort: strip fences/think, then parse the outermost {...}."""
    t = _strip_think(text)
    # Drop ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, flags=re.DOTALL)
    if fence:
        t = fence.group(1)
    # Otherwise take from the first '{' to the last '}'.
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    blob = t[start : end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def _normalize_cards(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Coerce the parsed object into a list of oracle-shaped cards.

    Accepts either {"cards": [...]} or a single bare card dict. Keeps only
    cards that have at least name + equation; fills missing optional fields.
    """
    raw = obj.get("cards") if isinstance(obj.get("cards"), list) else [obj]
    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        eq = c.get("equation")
        if not name or not eq:
            continue
        variables = c.get("variables")
        if not isinstance(variables, dict):
            variables = {}
        out.append({
            "name": str(name),
            "equation": str(eq),
            "variables": {str(k): str(v) for k, v in variables.items()},
            "when": str(c.get("when") or ""),
        })
    return out


def build_cards(
    *,
    client,
    model_name: str,
    passage: str,
    purpose: str,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    disable_thinking: bool = False,   # default: thinking ON for extraction
) -> BuiltCards:
    """Run one passage -> card(s) extraction call. Thinking defaults ON
    because extraction/interpretation is a reasoning task (unlike the
    single-step *application*, which 003 showed is better no-think)."""
    messages = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": _user_prompt(passage, purpose)},
    ]
    try:
        resp = client.chat(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            disable_thinking=disable_thinking,
        )
    except Exception as e:  # network / decode — record, don't crash the sweep
        return BuiltCards(cards=[], raw_response="", parse_ok=False, error=repr(e))

    parsed = _extract_json(resp.text)
    cards = _normalize_cards(parsed) if parsed is not None else []
    return BuiltCards(
        cards=cards,
        raw_response=resp.text,
        parse_ok=bool(cards),
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        latency_s=resp.latency_s,
    )
