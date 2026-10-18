"""Voice tests: no network, no audio — transport and player are fakes."""

import logging
import threading
import time

import pytest

from app.triage import QUESTIONS
from app.voice import ASSISTANT_LINE, Voice


def wait_for(fn, seconds=3.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.01)
    return False


class FakeTTS:
    """Recorded HTTP transport: returns fixed mp3 bytes, optional delay/error."""

    def __init__(self, delay=0.0, error=None):
        self.calls = []
        self.delay = delay
        self.error = error

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if self.error:
            raise self.error
        if self.delay:
            time.sleep(self.delay)
        return b"ID3-fake-mp3"


@pytest.fixture
def voice_factory(tmp_path):
    made = []

    def make(**kw):
        kw.setdefault("cache_dir", tmp_path / "cache")
        kw.setdefault("afplay", "/usr/bin/afplay")
        kw.setdefault("say", "/usr/bin/say")
        v = Voice(**kw)
        made.append(v)
        return v

    yield make
    for v in made:
        v.stop()


def real(**kw):
    return dict(stub=False, api_key="sk-test", voice_id="voice-1", **kw)


def test_cache_miss_writes_file_then_hit_skips_api(voice_factory):
    tts, played = FakeTTS(), []
    v = voice_factory(**real(), transport=tts, player=lambda argv: played.append(argv))
    v.speak("Are you okay?")
    assert wait_for(lambda: played), "first line never played"
    assert len(list(v.cache_dir.glob("*.mp3"))) == 1  # cache miss wrote a file
    v.speak("Are you okay?")
    assert wait_for(lambda: len(played) == 2)
    assert len(tts.calls) == 1  # second play was a cache hit, no API call
    assert played[0][0].endswith("afplay")


def test_prewarm_covers_all_five_questions_plus_assistant(voice_factory):
    tts = FakeTTS()
    v = voice_factory(**real(), transport=tts, player=lambda argv: None)
    v.prewarm([t for _, t in QUESTIONS] + [ASSISTANT_LINE])
    assert wait_for(lambda: len(tts.calls) == len(QUESTIONS) + 1)
    seen = {tts.calls[i]["body"] for i in range(len(tts.calls))}
    for _, text in QUESTIONS:
        assert any(text.encode() in b for b in seen)


def test_lines_play_in_order_through_one_worker(voice_factory):
    order = []
    tts = FakeTTS()
    v = voice_factory(**real(), transport=tts, afplay=None,
                      player=lambda argv: order.append(argv[1]))
    lines = [f"line {i}" for i in range(5)]
    for line in lines:
        v.speak(line)
    assert wait_for(lambda: len(order) == 5)
    assert order == lines  # `say` fallback arg is the text itself


def test_failing_api_falls_back_to_say(voice_factory):
    tts = FakeTTS(error=OSError("connection refused"))
    played = []
    v = voice_factory(**real(), transport=tts, afplay=None,
                      player=lambda argv: played.append(argv))
    v.speak("Did you hit your head?")
    assert wait_for(lambda: played)
    assert played[0][0].endswith("say")  # fell back to macOS say


def test_slow_api_falls_back_within_timeout(voice_factory):
    tts = FakeTTS(delay=30)  # hangs far past the timeout
    played = []
    v = voice_factory(**real(timeout=0.2), transport=tts, afplay=None,
                      player=lambda argv: played.append(argv))
    t0 = time.time()
    v.speak("Can you move your legs?")
    assert wait_for(lambda: played)
    assert time.time() - t0 < 4.0, "fallback took longer than 4 s"
    assert played[0][0].endswith("say")


def test_stub_mode_never_calls_network(voice_factory):
    def bad_transport(*a):
        raise AssertionError("network called in stub mode")

    played = []
    v = voice_factory(stub=True, transport=bad_transport, afplay=None,
                      player=lambda argv: played.append(argv))
    v.prewarm([t for _, t in QUESTIONS])  # no-op in stub
    v.speak("hello")
    assert wait_for(lambda: played)
    assert played[0][0].endswith("say")


def test_stub_without_say_only_logs(voice_factory, caplog):
    v = voice_factory(stub=True, afplay=None, say=None)
    with caplog.at_level(logging.INFO, "firsthour.voice"):
        v.speak("hi")
        assert wait_for(lambda: any("voice line" in r.getMessage() for r in caplog.records))


def test_playback_disabled_plays_nothing(voice_factory):
    tts = FakeTTS()
    played = []
    v = voice_factory(**real(), playback=False, transport=tts,
                      player=lambda argv: played.append(argv))
    v.speak("Are you okay?")
    v.prewarm(["x"])
    time.sleep(0.3)
    assert not played and not tts.calls
    assert not list(v.cache_dir.glob("*.mp3"))


def test_speak_returns_immediately_when_player_hangs(voice_factory):
    hung = threading.Event()

    def hanging_player(argv):
        hung.wait(30)

    v = voice_factory(stub=True, player=hanging_player)
    t0 = time.time()
    for _ in range(50):
        v.speak("Are you okay?")
    assert time.time() - t0 < 1.0, "speak() blocked on a hung player"
    hung.set()


def test_caregiver_text_never_logged(voice_factory, caplog):
    secret_text = "the door code is actually 9876"
    v = voice_factory(**real(), transport=FakeTTS(), afplay=None,
                      player=lambda argv: None)
    with caplog.at_level(logging.DEBUG):
        v.speak(secret_text)
        assert wait_for(lambda: any("voice line" in r.getMessage() for r in caplog.records))
    all_logs = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_text not in all_logs


def test_real_requires_key_and_voice_id(voice_factory):
    played = []
    v = voice_factory(stub=False, api_key="", voice_id="",
                      transport=FakeTTS(), afplay=None,
                      player=lambda argv: played.append(argv))
    assert not v.real  # missing creds degrades to stub path, no network
    v.speak("hi")
    assert wait_for(lambda: played)
    assert played[0][0].endswith("say")
