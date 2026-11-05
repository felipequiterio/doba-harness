"""The solve loop must act on the model's answer, never on its reasoning.

Regression tests for the `<think>` leak: with thinking ON, Ollama returns the
reasoning in `message.thinking` and the client used to splice it into the
reply text, so a `CALC:` the model merely considered while thinking was
executed and counted, a `FINAL_ANSWER:` that only appeared inside `<think>`
was scored, and every turn's reasoning was re-injected as history.
"""

from __future__ import annotations

from lib.conditions import run_condition
from lib.models import ChatResponse, strip_think


class ScriptedClient:
    """Returns pre-built ChatResponses in order and records what it was sent."""

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.sent: list[list[dict[str, str]]] = []

    def chat(self, *, model, messages, temperature=0.2, max_tokens=None,
             stop=None, disable_thinking=None):
        self.sent.append([dict(m) for m in messages])
        return self._responses.pop(0)


def _ollama_turn(content: str, thinking: str = "", tokens: int = 10) -> ChatResponse:
    """Mimic OllamaClient's shape for a thinking-ON call."""
    text = f"<think>\n{thinking}\n</think>\n\n{content}" if thinking else content
    return ChatResponse(text=text, prompt_tokens=100, completion_tokens=tokens,
                        latency_s=0.1, content=content, thinking=thinking)


PROBLEM = {
    "id": "t-001",
    "statement": "Compute the circular speed at r = 6778137 m.",
    "oracle_cards": [],
}


def _run(client, **kw):
    params = dict(client=client, model_name="m", problem=PROBLEM, condition="C2",
                  temperature=0.2, max_tool_turns=8, max_total_turns=6,
                  disable_thinking=False)
    params.update(kw)
    return run_condition(**params)


def test_calc_inside_think_is_not_executed():
    client = ScriptedClient([
        _ollama_turn(
            content="CALC: sqrt(3.986e14 / 6778137)",
            thinking="maybe first\nCALC: 1 + 1\nthen the speed",
        ),
        _ollama_turn(content="FINAL_ANSWER: 7668.6 m/s"),
    ])
    run = _run(client)
    assert run.n_tool_calls == 1
    # The calculator only answered the CALC from the content.
    results_msg = client.sent[1][-1]["content"]
    assert results_msg.startswith("RESULT:") and "RESULT[2]" not in results_msg
    assert run.stopped_reason == "final_answer"


def test_final_answer_only_inside_think_is_not_an_answer():
    client = ScriptedClient([
        _ollama_turn(content="Let me double-check the units first.",
                     thinking="so FINAL_ANSWER: 2400 m/s probably"),
        _ollama_turn(content="Still checking."),
    ])
    run = _run(client)
    assert run.stopped_reason == "no_progress"
    assert "FINAL_ANSWER" not in run.final_text
    assert "FINAL_ANSWER" not in run.answer_trace
    # The full trace still keeps the reasoning for logs and the judge.
    assert "FINAL_ANSWER: 2400" in run.full_trace


def test_reasoning_is_not_fed_back_as_history():
    client = ScriptedClient([
        _ollama_turn(content="CALC: sqrt(3.986e14 / 6778137)", thinking="long reasoning " * 50),
        _ollama_turn(content="FINAL_ANSWER: 7668.6 m/s"),
    ])
    _run(client)
    assistant_turns = [m for m in client.sent[1] if m["role"] == "assistant"]
    assert assistant_turns == [{"role": "assistant", "content": "CALC: sqrt(3.986e14 / 6778137)"}]


def test_peak_context_tokens_is_the_largest_single_call():
    client = ScriptedClient([
        _ollama_turn(content="CALC: 2 + 2", tokens=50),
        _ollama_turn(content="FINAL_ANSWER: 4 m/s", tokens=5),
    ])
    run = _run(client)
    assert run.peak_context_tokens == 150


def test_strip_think_for_inline_reasoning():
    # Providers that inline reasoning in `content` (content=None here).
    resp = ChatResponse(text="<think>CALC: 1+1</think>\nFINAL_ANSWER: 3 m/s")
    assert resp.answer == "FINAL_ANSWER: 3 m/s"
    # An unterminated block (budget ran out mid-thought) is all reasoning.
    assert strip_think("<think>still going CALC: 1+1") == ""
    assert strip_think("(only thinking content returned)\n<think>x</think>") == ""
    assert strip_think("no reasoning here") == "no reasoning here"
