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
    v = voice_factory(**real(), transport=tts, afplay="",
                      player=lambda argv: order.append(argv[-1]))
    lines = [f"line {i}" for i in range(5)]
    for line in lines:
        v.speak(line)
    assert wait_for(lambda: len(order) == 5)
    assert order == lines  # `say` fallback arg is the text itself


def test_say_argv_ends_options_with_dash_dash(voice_factory):
    """A caregiver reply starting with '-' must stay one arg after '--',
    or `say -o/tmp/x.aiff` would write a file instead of speaking."""
    played = []
    v = voice_factory(stub=True, afplay="", say="/usr/bin/say",
                      player=lambda argv: played.append(argv))
    v.speak("-o/tmp/x.aiff")
    assert wait_for(lambda: played)
    argv = played[0]
    assert argv == ["/usr/bin/say", "--", "-o/tmp/x.aiff"]
    assert argv.index("--") < argv.index("-o/tmp/x.aiff")


def test_dash_reply_is_single_arg_after_dash_dash(voice_factory):
    played = []
    v = voice_factory(stub=True, afplay="", say="/usr/bin/say",
                      player=lambda argv: played.append(argv))
    v.speak("-v Alex open it")
    assert wait_for(lambda: played)
    argv = played[0]
    assert argv[-1] == "-v Alex open it" and argv[-2] == "--"


def test_app_settings_dict_without_key_falls_back_to_env(tmp_path, monkeypatch):
    """create_app(settings()) has no VOICE_PLAYBACK key: it must honor the
    env var (conftest sets 0) instead of defaulting to on — the regression
    that made the whole suite call `say` 56 times on a Mac."""
    import os
    from app.main import create_app
    from conftest import LaptopClient

    calls = tmp_path / "say_calls"
    fake = tmp_path / "bin"
    fake.mkdir()
    say = fake / "say"
    say.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    say.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    monkeypatch.setenv("VOICE_PLAYBACK", "0")

    client = LaptopClient(create_app({"STUB_EDGE": True, "SHARED_SECRET": "test-secret",
                                    "CAREGIVER_NUMBERS": "+13105550101",
                                    "INCIDENT_WINDOW_SECONDS": 600}))
    import json, time
    from edge.security import sign_event
    ts = int(time.time() * 1000)
    body = json.dumps({"device_id": "bath-01", "ts": ts, "type": "fall_suspected",
                       "sensors": {"sound_peak": 812, "accel_delta_g": 1.9, "dist_cm": 28},
                       "local_incident_id": f"bath-01-{ts}"}).encode()
    with client:
        r = client.post("/edge/event", content=body,
                        headers={"X-Signature": sign_event("test-secret", ts, body)})
        assert r.status_code == 200
        time.sleep(0.3)  # give the worker a beat to (not) play
    assert not calls.exists(), "say was invoked despite VOICE_PLAYBACK=0"


def test_failing_api_falls_back_to_say(voice_factory):
    tts = FakeTTS(error=OSError("connection refused"))
    played = []
    v = voice_factory(**real(), transport=tts, afplay="",
                      player=lambda argv: played.append(argv))
    v.speak("Did you hit your head?")
    assert wait_for(lambda: played)
    assert played[0][0].endswith("say")  # fell back to macOS say


def test_slow_api_falls_back_within_timeout(voice_factory):
    tts = FakeTTS(delay=30)  # hangs far past the timeout
    played = []
    v = voice_factory(**real(timeout=0.2), transport=tts, afplay="",
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
    v = voice_factory(stub=True, transport=bad_transport, afplay="",
                      player=lambda argv: played.append(argv))
    v.prewarm([t for _, t in QUESTIONS])  # no-op in stub
    v.speak("hello")
    assert wait_for(lambda: played)
    assert played[0][0].endswith("say")


def test_stub_without_say_only_logs(voice_factory, caplog):
    v = voice_factory(stub=True, afplay="", say="")
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


def test_on_played_reports_each_line_after_it_plays(voice_factory):
    played, reported = [], []
    v = voice_factory(stub=True, player=lambda argv: played.append(argv[-1]),
                      on_played=lambda text: reported.append((text, list(played))))
    v.speak("Are you okay?")
    assert wait_for(lambda: reported)
    assert reported == [("Are you okay?", ["Are you okay?"])]  # reported after playing


def test_on_played_fires_immediately_when_playback_is_off(voice_factory):
    reported = []
    v = voice_factory(playback=False, on_played=reported.append)
    v.speak("Are you okay?")
    assert reported == ["Are you okay?"]


def test_flush_drops_lines_not_yet_started(voice_factory):
    gate, played = threading.Event(), []

    def player(argv):
        played.append(argv[-1])
        gate.wait(5)

    v = voice_factory(stub=True, player=player)
    v.speak("first")
    assert wait_for(lambda: played)  # "first" is playing
    v.speak("second")
    v.speak("third")
    v.flush()
    gate.set()
    v.speak("after")
    assert wait_for(lambda: played[-1:] == ["after"])
    assert played == ["first", "after"]


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
    v = voice_factory(**real(), transport=FakeTTS(), afplay="",
                      player=lambda argv: None)
    with caplog.at_level(logging.DEBUG):
        v.speak(secret_text)
        assert wait_for(lambda: any("voice line" in r.getMessage() for r in caplog.records))
    all_logs = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_text not in all_logs


def test_real_requires_key_and_voice_id(voice_factory):
    played = []
    v = voice_factory(stub=False, api_key="", voice_id="",
                      transport=FakeTTS(), afplay="",
                      player=lambda argv: played.append(argv))
    assert not v.real  # missing creds degrades to stub path, no network
    v.speak("hi")
    assert wait_for(lambda: played)
    assert played[0][0].endswith("say")


def test_concurrent_fetch_of_one_line_never_drops_it(tmp_path):
    """Prewarm and the playback worker can synthesize the same line at once
    (a fall right after startup). Both must get the file, neither may raise."""
    gate = threading.Barrier(2)

    def tts(url, headers, body, timeout):
        gate.wait(2)  # both fetches return together, so their writes race
        return b"ID3-fake-mp3"

    for i in range(20):
        v = Voice(**real(), cache_dir=tmp_path / str(i), transport=tts, afplay="", say="")
        errors, out = [], []

        def fetch():
            try:
                out.append(v._audio("Are you okay?")[0])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fetch) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        v.stop()
        assert not errors and len(out) == 2 and all(p.exists() for p in out)
