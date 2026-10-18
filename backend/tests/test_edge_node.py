"""Edge node (IMPLEMENTATION.md sections 6.1, 10.1, 10.3). No hardware, no backend."""

import json

import pytest

from app.security import make_command, verify_event
from conftest import SECRET
from edge.sensor_node import AccelDelta, Fusion, Node, Outbox, parse_line

QUIET = "S,{ms},20,0.00,0.00,1.00,120"
THUD = "S,{ms},850,0.80,0.50,2.60,28"     # sound + vibration together
CLAP = "S,{ms},850,0.00,0.00,1.00,120"    # sound only
STOMP = "S,{ms},20,0.80,0.50,2.60,120"    # vibration only


# --- parse_line --------------------------------------------------------------

def test_parse_sample():
    assert parse_line("S,1200,812,0.1,0.2,1.9,28\n") == {
        "kind": "S", "ms": 1200, "sound": 812, "ax": 0.1, "ay": 0.2, "az": 1.9, "dist": 28}


@pytest.mark.parametrize("line,kind", [("B", "B"), ("ACK,U", "ACK")])
def test_parse_other_kinds(line, kind):
    assert parse_line(line)["kind"] == kind


@pytest.mark.parametrize("line", ["", "garbage", "S,1,2,3", "S,a,b,c,d,e,f", "N,04A1B2C3D4", "S,1,2,3,4,5,6,7", "\x00\xff"])
def test_noise_is_ignored(line):
    assert parse_line(line) is None


# --- fusion --------------------------------------------------------------------

def test_accel_delta_is_zero_at_rest_and_jumps_on_impact():
    a = AccelDelta()
    for _ in range(50):
        assert a.update(0, 0, 1.0) < 0.01
    assert a.update(0.8, 0.5, 2.6) > 1.5


def fusion():
    return Fusion(sound_t=400, accel_t=1.0, window_ms=400, cooldown_ms=30_000)


def test_both_signals_together_fire_once():
    f = fusion()
    assert f.update(1000, 850, 1.8)
    assert not f.update(1050, 850, 1.8)  # same fall, inside cooldown


def test_sound_alone_or_vibration_alone_never_fires():
    f = fusion()
    assert not any(f.update(ms, 850, 0.0) for ms in range(0, 5000, 50))
    f = fusion()
    assert not any(f.update(ms, 0, 1.8) for ms in range(0, 5000, 50))


def test_signals_within_window_fire_and_outside_do_not():
    f = fusion()
    assert not f.update(1000, 850, 0.0)
    assert f.update(1350, 0, 1.8)
    f = fusion()
    assert not f.update(1000, 850, 0.0)
    assert not f.update(1450, 0, 1.8)


def test_cooldown_then_fires_again():
    f = fusion()
    assert f.update(1000, 850, 1.8)
    assert not f.update(30_900, 850, 1.8)
    assert f.update(31_001, 850, 1.8)


def test_arduino_reset_clears_cooldown():
    f = fusion()
    assert f.update(100_000, 850, 1.8)
    assert f.update(500, 850, 1.8)  # millis() restarted


# --- node ---------------------------------------------------------------------

class FakeHttp:
    def __init__(self, calls, online=True):
        self.calls, self.online = calls, online
        self.commands = []
        self.status = 200

    def post(self, path, raw, headers):
        self.calls.append(("POST", path, raw, headers))
        if not self.online:
            raise OSError("network down")
        return self.status, {"ok": True}

    def get(self, path, headers=None):
        self.calls.append(("GET", path, headers))
        if not self.online:
            raise OSError("network down")
        cmds, self.commands = self.commands, []
        return 200, {"commands": cmds}


@pytest.fixture
def calls():
    return []  # serial writes and HTTP calls, interleaved, in order


@pytest.fixture
def http(calls):
    return FakeHttp(calls)


@pytest.fixture
def node(tmp_path, clock, calls, http):
    return Node(device_id="bath-01", secret=SECRET,
                write_serial=lambda c: calls.append(("SERIAL", c)), http=http,
                outbox_path=tmp_path / "outbox.jsonl", clock=clock,
                sound_t=400, accel_t=1.0, window_ms=400, cooldown_s=30,
                incident_window_s=600, poll_s=1, heartbeat_s=5, backoff_s=5)


def step(node):
    """One pass of both loops: the network thread, then the sensor loop."""
    node.network_step(node._clock())
    node.tick()


def serial(calls):
    return [c[1] for c in calls if c[0] == "SERIAL"]


def posted(calls):
    return [json.loads(c[2]) for c in calls if c[0] == "POST"]


def settle(node, ms=0):
    for i in range(20):
        node.handle_line(QUIET.format(ms=ms + i * 50))


def fall(node, ms=2000):
    settle(node, ms - 1000)
    node.handle_line(THUD.format(ms=ms))


def test_boot_shows_ready(node, calls):
    assert serial(calls) == ["D,READY"]


def test_fall_raises_local_alarm_before_any_network_call(node, calls):
    fall(node)
    assert serial(calls)[-2:] == ["A1", "D,TIME DOWN 00:00"]
    node.tick()  # the sensor loop alone never touches the network
    assert not any(c[0] in ("POST", "GET") for c in calls)  # nothing sent yet
    step(node)
    events = posted(calls)
    assert events[0]["type"] == "fall_suspected"
    assert events[0]["sensors"]["sound_peak"] == 850 and events[0]["sensors"]["accel_delta_g"] > 1.0
    assert events[0]["local_incident_id"] == node.incident["id"]


def test_events_are_signed_so_the_backend_accepts_them(node, calls):
    fall(node)
    step(node)
    _, _, raw, headers = next(c for c in calls if c[0] == "POST")
    assert verify_event(SECRET, raw, headers["X-Signature"]) == (True, "ok")


def test_clap_or_stomp_does_not_open_an_incident(node, calls):
    settle(node)
    node.handle_line(CLAP.format(ms=2000))
    node.handle_line(STOMP.format(ms=5000))
    assert node.incident is None and "A1" not in serial(calls)


def test_clock_counts_on_the_display(node, calls, clock):
    fall(node)
    clock.advance(65)
    step(node)
    assert "D,TIME DOWN 01:05" in serial(calls)


def test_offline_alarm_still_works_and_events_wait_in_the_outbox(node, calls, http, tmp_path, clock):
    http.online = False
    fall(node)
    step(node)
    assert "A1" in serial(calls) and node.incident
    assert len(node.outbox) == 1
    # persisted, so a restart doesn't lose it
    assert len(Outbox(tmp_path / "outbox.jsonl")) == 1

    # backoff: no hammering the dead network every tick
    n = len(calls)
    clock.advance(1)
    step(node)
    assert not any(c[0] in ("POST", "GET") for c in calls[n:])

    # help button while offline: still queued, in order
    node.handle_line("B")

    # network returns: everything replays in order, then the outbox is empty
    http.online = True
    clock.advance(5)
    step(node)
    types = [e["type"] for e in posted(calls) if e["type"] != "heartbeat"]
    assert types[-2:] == ["fall_suspected", "help_button"]
    assert len(node.outbox) == 0


def test_heartbeats_are_not_queued_while_offline(node, http, clock):
    http.online = False
    for _ in range(5):
        clock.advance(6)
        step(node)
    assert len(node.outbox) == 0


def test_rejected_event_is_dropped_not_retried_forever(node, http, calls):
    http.status = 401
    fall(node)
    step(node)
    step(node)
    assert len(node.outbox) == 0
    assert sum(1 for e in posted(calls) if e["type"] == "fall_suspected") == 1


def cmd(clock, action, incident_id, secret=SECRET):
    return make_command(secret, action, incident_id, now_ms=clock.ms()).model_dump()


def types(calls):
    return [e["type"] for e in posted(calls)]


def test_unlock_is_reported_only_after_the_latch_acks(node, calls, http, clock):
    fall(node)
    http.commands = [cmd(clock, "unlock", node.incident["id"])]
    step(node)
    assert serial(calls)[-1] == "U"
    clock.advance(1)
    step(node)
    # The dashboard shows the door open only once the Arduino confirms the latch.
    assert "local_unlock" not in types(calls)
    node.handle_line("ACK,U")
    clock.advance(1)
    step(node)
    assert "local_unlock" in types(calls) and "unlock_failed" not in types(calls)


def test_no_ack_reports_unlock_failed_never_local_unlock(node, calls, http, clock):
    fall(node)
    http.commands = [cmd(clock, "unlock", node.incident["id"])]
    step(node)
    clock.advance(2.5)  # past UNLOCK_ACK_TIMEOUT_S
    step(node)
    step(node)
    node.handle_line("ACK,U")  # too late: logged, not reported
    clock.advance(1)
    step(node)
    assert "unlock_failed" in types(calls) and "local_unlock" not in types(calls)


def test_poll_is_signed_with_a_rising_ts(node, calls, http, clock):
    from app.security import verify_poll

    step(node)
    clock.advance(1)
    step(node)
    gets = [c for c in calls if c[0] == "GET"]
    assert len(gets) == 2
    last = 0
    for _, path, headers in gets:
        ts = int(path.split("ts=")[1])
        assert path.startswith("/edge/commands?device_id=bath-01&")
        assert verify_poll(SECRET, "bath-01", ts, headers["X-Signature"], last, now_ms=clock.ms()) == (True, "ok")
        last = ts


def test_rejected_poll_is_logged_not_treated_as_offline(node, http, clock, caplog):
    http.get = lambda path, headers=None: (401, {"detail": "bad signature"})
    with caplog.at_level("ERROR", logger="firsthour.edge"):
        step(node)
    assert node.online is True and "command poll rejected status=401" in caplog.text


def test_unlock_without_local_incident_refused(node, calls, http, clock):
    http.commands = [make_command(SECRET, "unlock", "inc-1", now_ms=clock.ms()).model_dump()]
    step(node)
    assert "U" not in serial(calls)


def test_forged_and_replayed_commands_refused(node, calls, http, clock):
    fall(node)
    good = cmd(clock, "unlock", node.incident["id"])
    forged = cmd(clock, "unlock", node.incident["id"], secret="wrong")
    http.commands = [forged]
    step(node)
    assert "U" not in serial(calls)
    http.commands = [good, good]
    clock.advance(1)
    step(node)
    assert serial(calls).count("U") == 1


def test_alarm_off_closes_the_local_incident(node, calls, http, clock):
    fall(node)
    http.commands = [cmd(clock, "alarm_off", node.incident["id"])]
    step(node)
    assert node.incident is None
    assert serial(calls)[-2:] == ["A0", "D,READY"]


def test_commands_for_a_stale_backend_incident_are_ignored(node, calls, http, clock):
    # Issue #13 bug 2, reproduced live: resolving a stale backend incident silenced a new fall.
    fall(node)
    http.commands = [cmd(clock, "alarm_off", "bath-01-1"), cmd(clock, "unlock", "bath-01-1")]
    step(node)
    assert node.incident is not None
    assert "A0" not in serial(calls) and "U" not in serial(calls)


def test_non_json_response_counts_as_offline_not_a_crash(node, calls, clock, monkeypatch):
    # A captive portal answers 200 with HTML.
    from edge.sensor_node import UrlHttp

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<html>Sign in to Wi-Fi</html>"

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: Resp())
    node.http = UrlHttp("http://backend", 1)
    fall(node)
    step(node)
    assert node.online is False and len(node.outbox) == 1 and node.incident


def test_alarm_outlives_the_window_but_unlocks_do_not(node, calls, http, clock):
    fall(node)
    clock.advance(601)
    http.commands = [cmd(clock, "unlock", node.incident["id"])]
    step(node)
    assert node.incident is not None
    assert "A0" not in serial(calls) and "D,READY" not in serial(calls)[1:]
    assert serial(calls)[-1] == "D,TIME DOWN 10:01"
    assert "U" not in serial(calls)


def test_reset_with_no_incident_locks_and_clears_the_cooldown(node, calls, http, clock):
    fall(node, ms=2000)
    http.commands = [cmd(clock, "alarm_off", node.incident["id"]), cmd(clock, "reset", "")]
    step(node)
    assert node.incident is None
    assert serial(calls)[-3:] == ["L", "A0", "D,READY"]
    fall(node, ms=6000)  # 4 s later: inside the old 30 s cooldown
    assert node.incident is not None


def test_reset_is_refused_while_a_local_incident_is_open(node, calls, http, clock):
    fall(node)
    http.commands = [cmd(clock, "reset", "")]
    step(node)
    assert node.incident is not None and "L" not in serial(calls)


def test_stdin_source_fakes_the_latch_ack():
    from edge.sensor_node import StdinSource

    src = StdinSource()
    src.write("U")
    assert src.readline(0) == "ACK,U"
    dead = StdinSource(auto_ack=False)
    dead.write("U")
    assert dead._replies == []


def test_slow_backend_does_not_stall_the_sensor_loop(tmp_path):
    """Issue #21.5: every HTTP call hangs 0.5 s; the sensor loop keeps its cadence."""
    import threading
    import time

    class SlowHttp:
        def __init__(self):
            self.calls = 0

        def post(self, path, raw, headers):
            self.calls += 1
            time.sleep(0.5)
            return 200, {}

        def get(self, path, headers=None):
            self.calls += 1
            time.sleep(0.5)
            return 200, {"commands": []}

    writes, http = [], SlowHttp()
    node = Node(device_id="bath-01", secret=SECRET, write_serial=writes.append, http=http,
                outbox_path=tmp_path / "o.jsonl", sound_t=400, accel_t=1.0, poll_s=0.1, heartbeat_s=0.1)
    node.start_network(interval_s=0.01)
    try:
        settle(node)
        node.handle_line(THUD.format(ms=2000))
        worst, t_end, ms = 0.0, time.time() + 1.5, 3000
        while time.time() < t_end:
            t0 = time.time()
            node.handle_line(QUIET.format(ms=ms))
            node.tick()
            worst = max(worst, time.time() - t0)
            ms += 50
            time.sleep(0.01)
    finally:
        node.stop_network()
    assert http.calls >= 2  # the network thread really was stuck in slow calls
    assert worst < 0.05, f"sensor loop blocked for {worst:.2f} s"
    assert "D,TIME DOWN 00:01" in writes


def test_help_button_with_no_incident_opens_one_and_asks_for_help(node, calls):
    node.handle_line("B")
    assert node.incident and "A1" in serial(calls)
    step(node)
    assert [e["type"] for e in posted(calls)][:2] == ["fall_suspected", "help_button"]


def test_second_fall_during_incident_keeps_the_same_incident(node, clock):
    fall(node, ms=2000)
    first = node.incident["id"]
    clock.advance(40)
    fall(node, ms=45_000)
    assert node.incident["id"] == first


def test_missing_secret_refuses_to_start(tmp_path):
    with pytest.raises(ValueError):
        Node(device_id="x", secret="", write_serial=print, http=None,
             outbox_path=tmp_path / "o.jsonl")
