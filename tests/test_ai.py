"""Tests for the AI wrapper's safety logic, with a fake Google client (no internet)."""
import json

import ai

CONFIG = json.load(open("clients/real_estate.json", encoding="utf-8"))


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes, self.tried = outcomes, []

    def generate_content(self, model, contents, config):
        self.tried.append(model)
        result = self.outcomes[model]
        if isinstance(result, Exception):
            raise result
        return result


class FakeResponse:
    def __init__(self, text):
        self.text, self.parsed = text, None


def make(monkeypatch, outcomes, models):
    fake = FakeModels(outcomes)
    monkeypatch.setattr(ai.genai, "Client", lambda **kw: type("C", (), {"models": fake})())
    monkeypatch.setattr(ai, "pick_models", lambda key: models)
    ai.SWITCHED_OFF.clear(); ai.RESTING.clear()
    return ai.make_ai("key"), fake


GOOD = FakeResponse('{"details": {"intent": "buy"}, "wants_human": false, "reply": "Hi!"}')
HISTORY = [{"direction": "in", "text": "I want to buy"}]


def test_switched_off_model_is_skipped_forever(monkeypatch):
    ask, fake = make(monkeypatch, {"old": RuntimeError("404 NOT_FOUND no longer available"), "new": GOOD}, ["old", "new"])
    answer, model = ask(CONFIG, HISTORY, {}, ["intent"])
    assert model == "new" and answer["details"]["intent"] == "buy"
    ask(CONFIG, HISTORY, {}, ["intent"])
    assert fake.tried == ["old", "new", "new"]                  # 'old' was not tried the second time


def test_model_out_of_quota_rests_and_the_next_answers(monkeypatch):
    ask, fake = make(monkeypatch, {"busy": RuntimeError("429 RESOURCE_EXHAUSTED retryDelay': '40s'"), "lite": GOOD}, ["busy", "lite"])
    assert ask(CONFIG, HISTORY, {}, [])[1] == "lite"
    assert "busy" in ai.RESTING


def test_when_every_model_fails_the_error_says_why(monkeypatch):
    ask, fake = make(monkeypatch, {"a": RuntimeError("404 NOT_FOUND"), "b": RuntimeError("500 INTERNAL")}, ["a", "b"])
    try:
        ask(CONFIG, HISTORY, {}, [])
        assert False, "should have failed"
    except RuntimeError as error:
        assert "a: 404" in str(error) and "b: 500" in str(error)


def test_backup_lite_model_is_always_tried(monkeypatch):
    busy = RuntimeError("429 RESOURCE_EXHAUSTED")
    outcomes = {"f1": busy, "f2": busy, "f3": busy, "f4": busy, "f5": busy, "f6-lite": GOOD}
    ask, fake = make(monkeypatch, outcomes, ["f1", "f2", "f3", "f4", "f5", "f6-lite"])
    assert ask(CONFIG, HISTORY, {}, [])[1] == "f6-lite"
    assert fake.tried == ["f1", "f2", "f6-lite"]
