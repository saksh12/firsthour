"""Speech-to-text on the Pi (issue #22): the laptop records, the Pi transcribes with whisper.cpp.

Backend: clip upload, signed one-time clip fetch, transcribe command, speech_text answers, and
the bounded silence hold. Edge: the transcribe command runs off the sensor loop.
"""

import io
import json
import time
import wave

import pytest

from app.incident import IncidentMachine
from app.main import create_app, wav_ms
from app.models import IncidentState as S
from app.profile import DEMO_PROFILE
from app.security import make_command, verify_audio
from app.triage import QUESTIONS
from app.unlock import UnlockGuard
from conftest import CAREGIVER, SECRET, LaptopClient, signed_poll
from edge.security import CommandVerifier, sign_audio, sign_event
from edge.sensor_node import Node, clean_transcript

TS = 1_760_000_000_000
INC = f"bath-01-{TS}"


def wav(ms=1000, rate=16000, channels=1, width=2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (rate * ms // 1000) * channels * width)
    return buf.getvalue()


def app_client(**overrides) -> LaptopClient:
    s = {"STUB_EDGE": True, "SHARED_SECRET": SECRET, "CAREGIVER_NUMBERS": CAREGIVER,
         "INCIDENT_WINDOW_SECONDS": 600, "PI_STT": True, "PI_STT_HOLD_S": 20}
    s.update(overrides)
    return LaptopClient(create_app(s))


@pytest.fixture
def client() -> LaptopClient:
    return app_client()


def post_event(client, body: dict):
    raw = json.dumps(body).encode()
    return client.post("/edge/event", content=raw,
                       headers={"X-Signature": sign_event(SECRET, body["ts"], raw),
                                "Content-Type": "application/json"})


def fall(client):
    assert post_event(client, {"device_id": "bath-01", "ts": TS, "type": "fall_suspected",
                               "sensors": {}, "local_incident_id": INC}).status_code == 200


def upload(client, audio=None):
    return client.post("/person/audio", content=wav(4000) if audio is None else audio,
                       headers={"Content-Type": "audio/wav"})


def fetch(client, clip_id, ts=None, sig=None, device_id="bath-01"):
    ts = int(time.time() * 1000) if ts is None else ts
    sig = sign_audio(SECRET, ts, device_id, clip_id) if sig is None else sig
    return client.get(f"/edge/audio/{clip_id}", params={"device_id": device_id, "ts": ts},
                      headers={"X-Signature": sig})


def speech(client, clip_id, text, ts=TS + 5000, **extra):
    return post_event(client, {"device_id": "bath-01", "ts": ts, "type": "speech_text",
                               "local_incident_id": INC,
                               "speech": {"clip_id": clip_id, "text": text, "stt_ms": 4200,
                                          "audio_ms": 4000, **extra}})


# --- clip format and signatures -------------------------------------------------

def test_wav_ms_accepts_only_16k_mono_16bit():
    assert wav_ms(wav(4000)) == 4000
    assert wav_ms(wav(1000, rate=44100)) is None
    assert wav_ms(wav(1000, channels=2)) is None
    assert wav_ms(wav(1000, width=1)) is None
    assert wav_ms(b"not a wav at all, definitely not one") is None


def test_audio_signature_is_bound_to_device_clip_and_time():
    now = 1_760_000_000_000
    sig = sign_audio(SECRET, now, "bath-01", "cmd-1")
    assert verify_audio(SECRET, "bath-01", "cmd-1", now, sig, now_ms=now) == (True, "ok")
    assert verify_audio(SECRET, "bath-01", "cmd-2", now, sig, now_ms=now)[1] == "bad signature"
    assert verify_audio(SECRET, "bath-02", "cmd-1", now, sig, now_ms=now)[1] == "bad signature"
    assert verify_audio(SECRET, "bath-01", "cmd-1", now, sig, now_ms=now + 11_000)[1] == "stale"
    assert verify_audio(SECRET, "bath-01", "cmd-1", now, None, now_ms=now)[1] == "missing signature"


def test_whisper_noise_markers_are_not_answers():
    assert clean_transcript(" [BLANK_AUDIO]\n") == ""
    assert clean_transcript("(wind blowing) I need help *coughs*") == "I need help"
    assert clean_transcript("\n I'm fine.\n") == "I'm fine."


# --- upload and fetch ------------------------------------------------------------

def test_upload_is_refused_when_pi_stt_is_off():
    c = app_client(PI_STT=False)
    fall(c)
    assert upload(c).status_code == 404


def test_upload_needs_an_open_incident_and_a_valid_clip(client):
    assert upload(client).status_code == 409
    fall(client)
    assert upload(client, wav(1000, rate=48000)).status_code == 422
    assert upload(client, wav(16_000)).status_code == 422  # over the 15 s cap


def test_upload_queues_a_signed_transcribe_for_the_pis_own_incident(client):
    fall(client)
    r = upload(client).json()
    assert r["audio_ms"] == 4000
    cmds = signed_poll(client).json()["commands"]
    assert [(c["action"], c["id"]) for c in cmds] == [("transcribe", r["clip_id"])]
    pi = CommandVerifier(SECRET)
    assert pi.check(cmds[0], local_incident_id=INC) == (True, "ok")
    assert CommandVerifier(SECRET).check(cmds[0], local_incident_id="bath-01-1")[0] is False


def test_clip_is_served_once_and_only_with_a_fresh_signature(client):
    fall(client)
    clip_id = upload(client).json()["clip_id"]
    assert fetch(client, clip_id, sig="0" * 64).status_code == 401
    assert fetch(client, clip_id, ts=int(time.time() * 1000) - 60_000).status_code == 401
    assert fetch(client, clip_id, device_id="bath-02").status_code == 404  # another Pi's valid signature
    r = fetch(client, clip_id)
    assert r.status_code == 200 and r.content == wav(4000)
    assert fetch(client, clip_id).status_code == 404


def test_closing_the_incident_deletes_its_clips(client):
    fall(client)
    clip_id = upload(client).json()["clip_id"]
    client.post("/incident/resolve")
    assert fetch(client, clip_id).status_code == 404


# --- speech_text answers ---------------------------------------------------------

def test_pi_transcript_im_fine_cancels(client):
    fall(client)
    clip_id = upload(client).json()["clip_id"]
    assert speech(client, clip_id, "I'm fine.").status_code == 200
    events = client.app.state.events
    heard = [e.data for e in events if e.type == "speech_heard"]
    assert heard and heard[-1]["text"] == "I'm fine." and heard[-1]["stt_ms"] == 4200
    assert any(e.type == "incident_update" and e.data.get("state") == "CANCELLED" for e in events)


def test_pi_transcript_for_help_escalates(client):
    fall(client)
    clip_id = upload(client).json()["clip_id"]
    speech(client, clip_id, "Help, I slipped and can't move my leg.")
    assert client.get("/incident").json()["state"] == "ALERTING"


def test_unknown_clip_or_replayed_transcript_is_ignored(client):
    fall(client)
    speech(client, "cmd-nope", "I'm fine")
    assert client.get("/incident").json()["state"] == "CHECKING"
    clip_id = upload(client).json()["clip_id"]
    speech(client, clip_id, "my hip hurts")
    assert client.get("/incident").json()["state"] == "ALERTING"
    n = len(client.app.state.machine.incident.answers)
    speech(client, clip_id, "I'm fine", ts=TS + 6000)  # the clip was already answered
    assert len(client.app.state.machine.incident.answers) == n


def test_failed_transcription_is_shown_but_never_answers(client):
    fall(client)
    clip_id = upload(client).json()["clip_id"]
    speech(client, clip_id, "", error="whisper-cli timed out")
    heard = [e.data for e in client.app.state.events if e.type == "speech_heard"]
    assert heard[-1]["error"] == "whisper-cli timed out"
    assert client.app.state.machine.incident.answers == []


def test_recording_holds_silence_only_for_the_recording(client):
    machine = client.app.state.machine
    clock = [time.time()]
    machine._clock = lambda: clock[0]
    fall(client)
    assert client.post("/person/listening", json={"seconds": 20}).status_code == 422
    assert client.post("/person/listening", json={"seconds": 10}).json() == {"ok": True}
    clock[0] += 11.9  # past the 8 s silence timer, still recording + uploading
    machine.tick()
    assert machine.state == S.CHECKING
    clock[0] += 0.2  # the clip never arrived: silence escalates
    machine.tick()
    assert machine.state == S.ALERTING


def test_silent_recording_escalates_without_waiting_for_the_pi(client):
    machine = client.app.state.machine
    clock = [time.time()]
    machine._clock = lambda: clock[0]
    fall(client)
    client.post("/person/listening", json={"seconds": 8})
    clock[0] += 9
    machine.tick()
    assert machine.state == S.CHECKING
    assert client.post("/person/no_speech").json() == {"ok": True}
    machine.tick()
    assert machine.state == S.ALERTING


def test_listening_is_refused_when_pi_stt_is_off():
    c = app_client(PI_STT=False)
    fall(c)
    assert c.post("/person/listening", json={"seconds": 10}).status_code == 404


def test_decoding_loops_on_noise_are_not_answers():
    assert clean_transcript("(to the the the the the the the the the the the the") == ""
    assert clean_transcript("No, no, I'm fine, I'm fine.") == "No, no, I'm fine, I'm fine."


def test_resident_whisper_server_gets_the_clip_and_beam_settings(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from edge.sensor_node import Transcriber

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            out = json.dumps({"text": " I'm fine.\n"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = Transcriber("model.bin", server_binary="", port=srv.server_address[1])

        class Alive:
            pid = 0
            def poll(self):
                return None

        t._server = Alive()
        assert t(wav(4000)) == "I'm fine."
    finally:
        srv.shutdown()
    body = seen["body"]
    assert seen["path"] == "/inference" and wav(4000) in body
    for field in (b'name="beam_size"\r\n\r\n5', b'name="temperature_inc"\r\n\r\n0', b'name="audio_ctx"\r\n\r\n384'):
        assert field in body


def test_whisper_cli_is_the_fallback_when_the_server_is_not_up(tmp_path):
    import sys

    from edge.sensor_node import Transcriber

    fake = tmp_path / "fake-whisper"
    fake.write_text(f"#!{sys.executable}\nimport sys\nassert '-nf' in sys.argv and '-ac' in sys.argv\nprint(' [BLANK_AUDIO] I need help.')\n")
    fake.chmod(0o755)
    t = Transcriber("model.bin", binary=str(fake), server_binary="", port=1)
    assert t.server_cmd is None
    assert t(wav(3000)) == "I need help."


def test_whisper_audio_window_fits_the_clip():
    from edge.sensor_node import Transcriber

    assert Transcriber.audio_ctx(len(wav(4000))) == 384     # measured fast setting
    assert Transcriber.audio_ctx(len(wav(10_000))) == 512   # 10.24 s window
    assert Transcriber.audio_ctx(len(wav(15_000))) == 768
    assert Transcriber.audio_ctx(len(wav(40_000))) == 1500  # whisper's 30 s maximum


def test_dashboard_sees_each_line_as_she_hears_it(client):
    fall(client)
    lines = [e.data for e in client.app.state.events if e.type == "voice_line"]
    assert [(d["text"], d["state"]) for d in lines[-2:]] == [(QUESTIONS[0][1], "playing"), (QUESTIONS[0][1], "played")]
    assert all(d["incident_id"] == INC for d in lines)


# --- the silence hold (state machine) --------------------------------------------

@pytest.fixture
def m(clock):
    events = []
    guard = UnlockGuard(allowed=[CAREGIVER], clock=clock)
    machine = IncidentMachine(guard, DEMO_PROFILE, clock=clock, silence_s=8,
                              emit=lambda t, d: events.append((t, d)), speak=lambda text: None,
                              send_alert=lambda inc_id, text: None)
    machine.events = events
    return machine


def test_question_played_tells_the_dashboard_when_to_record(m):
    m.on_fall("bath-01", "inc-1")
    m.line_played(QUESTIONS[0][1])
    played = [d for t, d in m.events if t == "question_played"]
    assert played == [{"incident_id": "inc-1", "question_index": 0, "question": QUESTIONS[0][1]}]


def test_speech_hold_delays_silence_but_never_stops_it(m, clock):
    m.on_fall("bath-01", "inc-1")
    clock.advance(5)
    m.hold_for_speech(20)
    clock.advance(10)  # 15 s since the question: past the 8 s silence, inside the hold
    m.tick()
    assert m.state == S.CHECKING
    clock.advance(10.1)  # the transcript never came back
    m.tick()
    assert m.state == S.ALERTING


def test_empty_transcript_releases_the_hold(m, clock):
    m.on_fall("bath-01", "inc-1")
    m.hold_for_speech(20)
    clock.advance(9)
    m.tick()
    assert m.state == S.CHECKING
    m.release_speech_hold()
    m.tick()
    assert m.state == S.ALERTING


def test_late_transcript_is_not_filed_under_the_next_question(m):
    m.on_fall("bath-01", "inc-1")
    assert m.on_answer("I need help", question_index=0)
    assert not m.on_answer("I'm fine", question_index=0)  # recorded for question 0, now at 1
    assert m.incident.answers == [("ok", "I need help")]
    assert m.state == S.ALERTING


# --- edge node -------------------------------------------------------------------

class FakeHttp:
    def __init__(self, wav_status=200):
        self.commands, self.posts, self.fetches = [], [], []
        self.wav_status = wav_status

    def post(self, path, raw, headers):
        self.posts.append(json.loads(raw))
        return 200, {"ok": True}

    def get(self, path, headers=None):
        cmds, self.commands = self.commands, []
        return 200, {"commands": cmds}

    def get_bytes(self, path, headers, timeout):
        self.fetches.append((path, headers))
        return self.wav_status, wav(4000) if self.wav_status == 200 else b""


def edge_node(tmp_path, clock, http, stt):
    return Node(device_id="bath-01", secret=SECRET, write_serial=lambda c: None, http=http,
                outbox_path=tmp_path / "outbox.jsonl", clock=clock, sound_t=400, accel_t=1.0,
                poll_s=1, heartbeat_s=5, stt=stt)


def open_incident(node, ms=2000):
    for i in range(20):
        node.handle_line(f"S,{ms - 1000 + i * 50},20,0.00,0.00,1.00,120")
    node.handle_line(f"S,{ms},850,0.80,0.50,2.60,28")
    assert node.incident


def spoken(http):
    return [e["speech"] for e in http.posts if e["type"] == "speech_text"]


def test_transcribe_runs_off_the_sensor_loop_and_reports_text(tmp_path, clock):
    heard, http = [], FakeHttp()
    node = edge_node(tmp_path, clock, http, stt=lambda audio: heard.append(audio) or "I'm fine.")
    open_incident(node)
    cmd = make_command(SECRET, "transcribe", node.incident["id"], now_ms=clock.ms()).model_dump()
    http.commands = [cmd]
    node.network_step(clock())
    node.tick()
    assert heard == [] and http.fetches == []  # the sensor loop only queued the job
    node.stt_step()
    path, headers = http.fetches[0]
    ts = int(path.split("ts=")[1])
    assert path.startswith(f"/edge/audio/{cmd['id']}?device_id=bath-01&")
    assert verify_audio(SECRET, "bath-01", cmd["id"], ts, headers["X-Signature"], now_ms=clock.ms()) == (True, "ok")
    clock.advance(6)
    node.network_step(clock())
    [result] = spoken(http)
    assert result["clip_id"] == cmd["id"] and result["text"] == "I'm fine."
    assert result["audio_ms"] == 4000 and isinstance(result["stt_ms"], int)
    assert http.posts[-1]["local_incident_id"] == node.incident["id"]


def test_transcribe_for_another_incident_is_refused(tmp_path, clock):
    http = FakeHttp()
    node = edge_node(tmp_path, clock, http, stt=lambda audio: "I'm fine")
    open_incident(node)
    http.commands = [make_command(SECRET, "transcribe", "bath-01-1", now_ms=clock.ms()).model_dump()]
    node.network_step(clock())
    node.tick()
    node.stt_step()
    assert http.fetches == [] and node._stt_jobs.empty()


def test_transcribe_without_whisper_reports_an_error_not_a_guess(tmp_path, clock):
    http = FakeHttp()
    node = edge_node(tmp_path, clock, http, stt=None)
    open_incident(node)
    http.commands = [make_command(SECRET, "transcribe", node.incident["id"], now_ms=clock.ms()).model_dump()]
    node.network_step(clock())
    node.tick()
    clock.advance(6)
    node.network_step(clock())
    [result] = spoken(http)
    assert result["error"] and "text" not in result


def test_refused_clip_fetch_and_whisper_failure_report_errors(tmp_path, clock):
    import subprocess

    def broken(audio):
        raise subprocess.TimeoutExpired("whisper-cli", 15)

    for http, stt in ((FakeHttp(wav_status=404), lambda audio: "x"), (FakeHttp(), broken)):
        node = edge_node(tmp_path, clock, http, stt=stt)
        open_incident(node)
        node._start_transcribe("cmd-1", node.incident["id"])
        node.stt_step()
        clock.advance(6)
        node.network_step(clock())
        assert spoken(http)[-1]["error"]
        node._close_incident("test")


# --- end to end over HTTP --------------------------------------------------------

class ClientHttp:
    """The Pi's UrlHttp, pointed at the in-process backend."""

    def __init__(self, client):
        self.c = client

    def post(self, path, raw, headers):
        r = self.c.post(path, content=raw, headers={"Content-Type": "application/json", **headers})
        return r.status_code, r.json()

    def get(self, path, headers=None):
        r = self.c.get(path, headers=headers or {})
        return r.status_code, r.json()

    def get_bytes(self, path, headers, timeout):
        r = self.c.get(path, headers=headers)
        return r.status_code, r.content


def test_recorded_answer_reaches_the_incident_through_the_pi(tmp_path):
    client = app_client()
    node = Node(device_id="bath-01", secret=SECRET, write_serial=lambda c: None, http=ClientHttp(client),
                outbox_path=tmp_path / "o.jsonl", sound_t=400, accel_t=1.0, poll_s=0, heartbeat_s=60,
                stt=lambda audio: "Help, I slipped and can't move my leg.")
    open_incident(node)
    node.network_step(time.time())
    assert client.get("/incident").json()["state"] == "CHECKING"
    upload(client)
    node.network_step(time.time())  # poll: transcribe command
    node.tick()
    node.stt_step()  # fetch the clip, transcribe
    node.network_step(time.time())  # post speech_text
    assert client.get("/incident").json()["state"] == "ALERTING"
    assert client.app.state.machine.incident.answers[0][1] == "Help, I slipped and can't move my leg."
def test_sigterm_stops_the_resident_whisper_server(tmp_path):
    """The Pi reader runs detached (`nohup ... &` leaves SIGINT ignored), so SIGTERM stops it.
    whisper-server has its own session and must not outlive the reader."""
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path

    pidfile = tmp_path / "server.pid"
    server = tmp_path / "whisper-server"
    server.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 60\n")
    server.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"")
    env = {**os.environ, "WHISPER_MODEL": str(model), "WHISPER_BIN": "sh", "WHISPER_SERVER_BIN": str(server),
           "EDGE_LOG_DIR": str(tmp_path / "logs")}
    node = Path(__file__).resolve().parents[2] / "edge" / "sensor_node.py"
    reader = subprocess.Popen([sys.executable, str(node), "--stdin", "--offline"], env=env,
                              stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    pid = None
    try:
        deadline = time.monotonic() + 10
        while not (pidfile.exists() and pidfile.read_text().strip()):
            assert time.monotonic() < deadline, "whisper-server never started"
            time.sleep(0.05)
        pid = int(pidfile.read_text())
        reader.send_signal(signal.SIGTERM)
        assert reader.wait(timeout=10) == 0
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if reader.poll() is None:
            reader.kill()
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
