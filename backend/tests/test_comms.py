"""Tests for the caregiver comms logic (Role C).

Pure functions over strings -- no server, no network. The send path is
exercised only in STUB_PHOTON mode so nothing here touches Photon.
"""

import asyncio

import pytest

from app import comms
from app.comms import AlertContext


# -- outbound alert format (section 6.6) ------------------------------------


def _ctx(**over):
    base = dict(
        name="Margaret",
        location="Bathroom",
        down_seconds=32,
        triage_summary="Conscious. Right hip pain, cannot move leg. No head strike.",
        on_blood_thinner=True,
        unlock_code="4821",
    )
    base.update(over)
    return AlertContext(**base)


def test_format_alert_matches_template():
    text = comms.format_alert(_ctx())
    assert text == (
        "FALL ALERT: Margaret (Bathroom). Down 00:32.\n"
        "Conscious. Right hip pain, cannot move leg. No head strike.\n"
        "NOTE: on a blood thinner.\n"
        "Reply UNLOCK 4821 to open the front door.\n"
        "Or reply with a message and we'll read it to her."
    )


def test_format_alert_omits_blood_thinner_note_when_false():
    text = comms.format_alert(_ctx(on_blood_thinner=False))
    assert "blood thinner" not in text
    assert "Reply UNLOCK 4821" in text


@pytest.mark.parametrize(
    "secs,expected",
    [(0, "00:00"), (5, "00:05"), (32, "00:32"), (60, "01:00"), (615, "10:15"), (6000, "100:00")],
)
def test_format_down_time(secs, expected):
    assert comms.format_down_time(secs) == expected


# -- inbound parsing (section 6.6) ------------------------------------------


@pytest.mark.parametrize(
    "body,code",
    [
        ("UNLOCK 4821", "4821"),
        ("unlock 4821", "4821"),
        ("  UNLOCK   4821  ", "4821"),
        ("Unlock 0000", "0000"),
    ],
)
def test_parse_inbound_unlock(body, code):
    msg = comms.parse_inbound(body)
    assert msg.kind == "unlock"
    assert msg.code == code


@pytest.mark.parametrize(
    "body",
    [
        "On my way, 5 minutes.",
        "UNLOCK 482",       # 3 digits -> not an unlock
        "UNLOCK 48210",     # 5 digits -> not an unlock
        "UNLOCK abcd",      # not digits
        "please unlock the door",
        "",
    ],
)
def test_parse_inbound_message(body):
    msg = comms.parse_inbound(body)
    assert msg.kind == "message"
    assert msg.text == body.strip()


def test_spoken_form_caps_at_20_words():
    long = " ".join(["word"] * 40)
    out = comms.spoken_form(long)
    assert out.endswith("...")
    assert len(out.split()) == 20  # kept to 20 words; "..." appended to the last


def test_spoken_form_leaves_short_text():
    assert comms.spoken_form("On my way, 5 minutes.") == "On my way, 5 minutes."


# -- caregiver allowlist ----------------------------------------------------


def test_is_caregiver_format_insensitive():
    allow = ["+13105550101"]
    assert comms.is_caregiver("+1 (310) 555-0101", allow)
    assert comms.is_caregiver("+13105550101", allow)
    assert not comms.is_caregiver("+13105550999", allow)


def test_caregiver_numbers_from_env(monkeypatch):
    monkeypatch.setenv("CAREGIVER_NUMBERS", "+13105550101, +13105550102 ")
    assert comms.caregiver_numbers() == ["+13105550101", "+13105550102"]


# -- send path (stub only) --------------------------------------------------


def test_send_alert_stub_records_outbox(monkeypatch):
    monkeypatch.setenv("STUB_PHOTON", "1")
    comms.clear_outbox()
    sent = asyncio.run(comms.send_alert("+13105550101", "FALL ALERT: ..."))
    assert sent.stubbed is True
    assert sent.message_id is None
    assert len(comms.outbox()) == 1
    assert comms.outbox()[0].to == "+13105550101"
    comms.clear_outbox()
