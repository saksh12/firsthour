"""Tests for the voice engine (Role C). Stub mode only -- no audio device,
no ElevenLabs key, no network."""

import pytest

from app.voice import FIXED_PHRASES, VoiceEngine


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setenv("STUB_ELEVENLABS", "1")


def test_warm_cache_fills_every_fixed_phrase():
    eng = VoiceEngine()
    assert not eng.is_warmed
    eng.warm_cache()
    assert eng.is_warmed
    assert eng.cached_keys() == sorted(FIXED_PHRASES)


def test_warm_cache_is_idempotent():
    eng = VoiceEngine()
    eng.warm_cache()
    keys = eng.cached_keys()
    eng.warm_cache()
    assert eng.cached_keys() == keys


def test_identify_phrase_declares_it_is_an_assistant():
    # Safety line required by IMPLEMENTATION.md Phase 3.
    text = FIXED_PHRASES["identify"].lower()
    assert "assistant" in text
    assert "not your daughter" in text


def test_say_known_phrase_records_spoken_line():
    eng = VoiceEngine()
    line = eng.say("ask_pain")
    assert line.key == "ask_pain"
    assert line.text == FIXED_PHRASES["ask_pain"]
    assert line.stubbed is True
    assert eng.spoken == [line]


def test_say_lazily_caches_when_not_warmed():
    eng = VoiceEngine()
    eng.say("check_ok")
    assert "check_ok" in eng.cached_keys()


def test_say_unknown_phrase_raises():
    eng = VoiceEngine()
    with pytest.raises(KeyError):
        eng.say("no_such_phrase")


def test_say_text_is_adhoc_not_cached():
    eng = VoiceEngine()
    line = eng.say_text("On my way, five minutes.")
    assert line.key is None
    assert line.text == "On my way, five minutes."
    assert eng.cached_keys() == []  # ad-hoc line is not cached
