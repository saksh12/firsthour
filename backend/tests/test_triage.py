import pytest

from app.profile import DEMO_PROFILE
from app.triage import QUESTIONS, find_body_part, is_clear_fine, parse_rules, yes_no


@pytest.mark.parametrize("text", ["I'm fine", "im fine", "I am okay", "i’m ok", "Fine", "I'm alright, thanks"])
def test_clear_fine_cancels(text):
    assert is_clear_fine(text)


@pytest.mark.parametrize("text", [
    "", "I'm not fine", "I need help", "I'm fine but my hip hurts", "I fell", "um",
    "I'm okay I think, I can't get up", "fine... help",
])
def test_anything_unclear_is_not_fine(text):
    assert not is_clear_fine(text)


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yeah I did", True), ("no", False), ("I didn't", False),
    ("I can't", False), ("yes I can", True), ("I don't know", None), ("", None), ("hmm", None),
])
def test_yes_no(text, expected):
    assert yes_no(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("my right hip", "right hip"), ("Left knee hurts", "left knee"), ("my back", "back"),
    ("everywhere", None), ("both legs", "leg"),
])
def test_body_part(text, expected):
    assert find_body_part(text) == expected


def test_demo_script_answers():
    answers = [
        ("ok", "Help, I slipped and can't move my leg"),
        ("head", "No"),
        ("pain", "My right hip"),
        ("move", "No I can't"),
        ("bleeding", "No"),
    ]
    rec = parse_rules(answers, DEMO_PROFILE)
    assert rec.conscious is True
    assert rec.head_strike is False
    assert rec.pain_location == "right hip"
    assert rec.can_move is False
    assert rec.bleeding is False
    assert rec.on_blood_thinner is True
    assert rec.done and rec.next_question is None
    assert rec.summary == "Conscious. Right hip pain. Cannot move. No head strike. No bleeding. On a blood thinner."


def test_first_answer_alone_fills_what_it_says_and_nothing_else():
    rec = parse_rules([("ok", "Help, I slipped and can't move my leg")], DEMO_PROFILE)
    assert rec.can_move is False
    assert rec.pain_location == "leg"
    assert rec.head_strike is None and rec.bleeding is None  # never guessed
    assert rec.next_question == QUESTIONS[1][1] and not rec.done


def test_silence_is_unknown_not_unconscious():
    rec = parse_rules([], DEMO_PROFILE)
    assert rec.conscious is None
    assert rec.summary.startswith("No answer yet.")
    assert rec.next_question == QUESTIONS[0][1]


def test_direct_answer_beats_free_text_cue():
    rec = parse_rules([("ok", "I hit my head"), ("head", "no actually")], DEMO_PROFILE)
    assert rec.head_strike is False


def test_free_text_head_strike_and_bleeding():
    rec = parse_rules([("ok", "help I banged my head and there's blood")], DEMO_PROFILE)
    assert rec.head_strike is True and rec.bleeding is True


def test_not_bleeding_in_free_text():
    rec = parse_rules([("ok", "help, my hip hurts but I'm not bleeding")], DEMO_PROFILE)
    assert rec.bleeding is False and rec.pain_location == "hip"


def test_blood_thinner_comes_from_profile_only():
    rec = parse_rules([("ok", "I'm on a blood thinner")], {"on_blood_thinner": False})
    assert rec.on_blood_thinner is False
