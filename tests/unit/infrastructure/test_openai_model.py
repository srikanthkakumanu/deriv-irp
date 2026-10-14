from types import SimpleNamespace

import pytest

from irp.application.ports import LLMRequest
from irp.infrastructure.openai_model import OpenAIModel

REQ = LLMRequest(stage="s", messages=({"role": "user", "content": "hi"},), response_schema={"type": "object"},
                 schema_name="s", payload={})


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=out.get("content"), refusal=out.get("refusal")))])


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return OpenAIModel(model="gpt-test")


def attach(model, outcomes):
    completions = FakeCompletions(outcomes)
    model._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return completions


def test_sends_strict_schema_and_parses_json(model):
    comp = attach(model, [{"content": '{"a": 1}'}])
    assert model.complete(REQ) == {"a": 1}
    sent = comp.kwargs[0]
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["temperature"] == 0.0 and sent["seed"] == 7


def test_retries_without_temperature_when_unsupported(model):
    comp = attach(model, [RuntimeError("Unsupported parameter: 'temperature'"), {"content": "{}"}])
    assert model.complete(REQ) == {}
    assert "temperature" not in comp.kwargs[1]


def test_other_errors_propagate(model):
    attach(model, [RuntimeError("401 invalid_api_key")])
    with pytest.raises(RuntimeError, match="401"):
        model.complete(REQ)


def test_refusal_raises(model):
    attach(model, [{"content": None, "refusal": "no"}])
    with pytest.raises(RuntimeError, match="refused"):
        model.complete(REQ)
