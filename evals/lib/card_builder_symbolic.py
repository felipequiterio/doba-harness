"""
card_builder_symbolic — eval 005's card builders, in three modes.

All three read a passage and emit concept card(s) in the same schema as
lib.card_builder (name / equation / variables / when), injected downstream
through the identical oracle path. They differ only in HOW the model derives
the closed-form formula the passage doesn't state outright:

  - "text"   : derive in-head, single call (== lib.card_builder.build_cards).
  - "sympy"  : the model drives a SymPy `SYMB:` tool (returns FORMULAS) to
               derive/verify, then emits the card.
  - "verify" : derive in-head first (no tool), THEN get a SYMB round to
               check the formula and correct it. Captures both the initial
               and the final card so we can see if verification changed it.

The SYMB tool and the formula-equivalence judge live in lib.symbolic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .card_builder import _SYS as _TEXT_SYS, _extract_json, _normalize_cards, _user_prompt
from .symbolic import evaluate_symb_text, extract_symb_calls

_SYMB_SYS = (
    "You are a knowledge-extraction assistant building reusable reference "
    "cards from an astrodynamics textbook passage. The passage often states a "
    "relation in a form that is NOT yet solved for the quantity you need, so "
    "you must DERIVE the closed-form formula.\n"
    "\n"
    "You have a SYMBOLIC ALGEBRA tool (a computer algebra system). To use it, "
    "write a line starting with `SYMB:` followed by ONE SymPy expression. The "
    "system replies with `SYMB_RESULT:` containing the verified result as a "
    "FORMULA (not a number). Examples:\n"
    "  SYMB: simplify((mu/h*e*sin(theta))/(mu/h*(1+e*cos(theta))))\n"
    "  SYMB: solve(Eq(T, 2*pi*sqrt(a^3/mu)), a)\n"
    "  SYMB: solve(Eq(v^2/2 - mu/r, -mu/(2*a)), v)\n"
    "Available: simplify, solve, Eq, diff, integrate, trigsimp, sqrt, sin, "
    "cos, tan, asin, acos, atan, exp, log, pi. Use `^` or `**` for powers.\n"
    "\n"
    "RULES:\n"
    " 1. After a `SYMB:` line, STOP and wait for the `SYMB_RESULT`. Never write "
    "your own result — anything you write is unverified.\n"
    " 2. Use the tool to do the algebra (dividing, simplifying, solving for the "
    "target) instead of doing it in your head.\n"
    " 3. When you have the formula, output the final card(s) as JSON of the "
    "form {\"cards\": [{\"name\":..., \"equation\":..., \"variables\":{...}, "
    "\"when\":...}]}. Use plain ASCII math. Output ONLY the JSON when done."
)


@dataclass
class BuiltCardsX:
    cards: list[dict[str, Any]]              # final card(s)
    initial_cards: list[dict[str, Any]]      # pre-verification (== cards for text/sympy)
    mode: str
    parse_ok: bool
    n_symb_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    trace: str = ""
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def changed_by_verify(self) -> bool:
        return self.mode == "verify" and self.initial_cards != self.cards


# ---------------------------------------------------------------------------
# Shared SYMB-enabled loop: model emits SYMB: lines (run them, feed results
# back) until it emits a JSON card. Mirrors the calculator loop's priority —
# a SYMB line always wins over a same-turn card (the card was premature).
# ---------------------------------------------------------------------------
@dataclass
class _LoopStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    n_symb_calls: int = 0
    parts: list[str] = field(default_factory=list)


def _symb_loop(*, client, model_name, messages, max_turns, max_tokens,
               temperature, disable_thinking, stats: _LoopStats):
    """Drive the conversation until a card JSON appears. Returns cards list."""
    prodded = False
    for _ in range(max_turns):
        resp = client.chat(model=model_name, messages=messages,
                           temperature=temperature, max_tokens=max_tokens,
                           disable_thinking=disable_thinking)
        stats.prompt_tokens += resp.prompt_tokens
        stats.completion_tokens += resp.completion_tokens
        stats.latency_s += resp.latency_s
        stats.parts.append(resp.text)
        messages.append({"role": "assistant", "content": resp.text})

        symb_exprs = extract_symb_calls(resp.text)
        if symb_exprs:                       # tool use wins over a premature card
            stats.n_symb_calls += len(symb_exprs)
            _, results_msg = evaluate_symb_text(resp.text)
            messages.append({"role": "user", "content": results_msg})
            prodded = False
            continue

        parsed = _extract_json(resp.text)
        cards = _normalize_cards(parsed) if parsed is not None else []
        if cards:
            return cards
        if prodded:
            return []
        prodded = True
        messages.append({"role": "user", "content": (
            "Emit either a `SYMB:` line to compute, or the final card(s) as a "
            "JSON object {\"cards\": [...]}. Output only one of those.")})
    return []


# ---------------------------------------------------------------------------
# Public builder.
# ---------------------------------------------------------------------------
def build_cards_x(*, client, model_name, passage, purpose, mode,
                  temperature=0.2, max_tokens=None, disable_thinking=False,
                  max_symb_turns=8) -> BuiltCardsX:
    """Build card(s) in one of the three modes. disable_thinking defaults to
    False (thinking ON) — deriving a formula is a reasoning task."""
    try:
        if mode == "text":
            messages = [
                {"role": "system", "content": _TEXT_SYS},
                {"role": "user", "content": _user_prompt(passage, purpose)},
            ]
            resp = client.chat(model=model_name, messages=messages,
                               temperature=temperature, max_tokens=max_tokens,
                               disable_thinking=disable_thinking)
            parsed = _extract_json(resp.text)
            cards = _normalize_cards(parsed) if parsed is not None else []
            return BuiltCardsX(cards=cards, initial_cards=cards, mode=mode,
                               parse_ok=bool(cards), prompt_tokens=resp.prompt_tokens,
                               completion_tokens=resp.completion_tokens,
                               latency_s=resp.latency_s, trace=resp.text)

        if mode == "sympy":
            messages = [
                {"role": "system", "content": _SYMB_SYS},
                {"role": "user", "content": _user_prompt(passage, purpose)
                 + "\n\nDerive the formula(s) with the SYMB tool, then emit the card(s)."},
            ]
            stats = _LoopStats()
            cards = _symb_loop(client=client, model_name=model_name, messages=messages,
                               max_turns=max_symb_turns, max_tokens=max_tokens,
                               temperature=temperature, disable_thinking=disable_thinking,
                               stats=stats)
            return BuiltCardsX(cards=cards, initial_cards=cards, mode=mode,
                               parse_ok=bool(cards), n_symb_calls=stats.n_symb_calls,
                               prompt_tokens=stats.prompt_tokens,
                               completion_tokens=stats.completion_tokens,
                               latency_s=stats.latency_s, trace="\n---\n".join(stats.parts))

        if mode == "verify":
            # Phase 1: in-head card, tool present but told not to use it yet.
            messages = [
                {"role": "system", "content": _SYMB_SYS},
                {"role": "user", "content": _user_prompt(passage, purpose)
                 + "\n\nSTEP 1: Derive the formula in your head and output your "
                   "candidate card(s) as JSON now. Do NOT use the SYMB tool yet."},
            ]
            stats = _LoopStats()
            initial = _symb_loop(client=client, model_name=model_name, messages=messages,
                                 max_turns=3, max_tokens=max_tokens, temperature=temperature,
                                 disable_thinking=disable_thinking, stats=stats)
            # Phase 2: verify/correct with the tool.
            messages.append({"role": "user", "content": (
                "STEP 2: Now VERIFY your formula(s) using the SYMB tool — derive "
                "each one from the passage's equations. If a formula is wrong, "
                "emit a CORRECTED card; if it is right, re-emit the same card. "
                "End with the final JSON card(s).")})
            final = _symb_loop(client=client, model_name=model_name, messages=messages,
                               max_turns=max_symb_turns, max_tokens=max_tokens,
                               temperature=temperature, disable_thinking=disable_thinking,
                               stats=stats)
            final = final or initial   # if phase 2 produced nothing, keep phase 1
            return BuiltCardsX(cards=final, initial_cards=initial, mode=mode,
                               parse_ok=bool(final), n_symb_calls=stats.n_symb_calls,
                               prompt_tokens=stats.prompt_tokens,
                               completion_tokens=stats.completion_tokens,
                               latency_s=stats.latency_s, trace="\n---\n".join(stats.parts))

        raise ValueError(f"unknown mode: {mode!r}")
    except Exception as e:  # never crash the sweep
        return BuiltCardsX(cards=[], initial_cards=[], mode=mode, parse_ok=False,
                           error=repr(e))
