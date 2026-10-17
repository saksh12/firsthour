"""Voice output: fixed-phrase cache + playback.

Role C (Comms and voice). The person on the floor hears short, pre-written
phrases. We pre-generate audio for every fixed phrase at startup so the first
question plays instantly instead of waiting on a TTS round-trip.

STUB_ELEVENLABS=1 (default): no network, no key. ``warm_cache`` fills the
cache with placeholder bytes and ``say`` just records what was spoken.
STUB_ELEVENLABS=0: generate real audio with ElevenLabs and cache the bytes.

Safety line kept per IMPLEMENTATION.md Phase 3: the assistant always
identifies itself ("this is an assistant, not your daughter").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# Phrase key -> spoken text. These are the only lines the voice ever speaks
# on its own; a caregiver note is read via comms.spoken_form at runtime.
FIXED_PHRASES: dict[str, str] = {
    "identify": "Hello, this is a safety assistant, not your daughter. "
    "I'm here to help.",
    "check_ok": "I noticed a fall. Are you okay? You can just talk to me.",
    "ask_conscious": "Can you tell me your name?",
    "ask_pain": "Where does it hurt?",
    "ask_can_move": "Can you move your arms and legs?",
    "ask_head": "Did you hit your head?",
    "ask_bleeding": "Are you bleeding anywhere?",
    "reassure_help_coming": "Okay. I've told your family and help is coming. "
    "Stay still and keep talking to me.",
    "confirm_fine": "Okay, I'm glad you're alright. I'll stand down now.",
    "door_unlocked": "The front door is unlocked for your family.",
}


def _stub_elevenlabs() -> bool:
    return os.environ.get("STUB_ELEVENLABS", "1") != "0"


@dataclass
class SpokenLine:
    key: Optional[str]  # phrase key, or None for an ad-hoc line (caregiver note)
    text: str
    stubbed: bool


class VoiceEngine:
    """Caches fixed-phrase audio and plays lines on the laptop speaker.

    One instance per process. Call ``warm_cache()`` once at startup.
    """

    def __init__(self) -> None:
        self._cache: dict[str, bytes] = {}
        self._warmed = False
        # Everything spoken this run, for the dashboard / tests.
        self.spoken: list[SpokenLine] = []

    # -- cache ---------------------------------------------------------------

    def warm_cache(self) -> None:
        """Pre-generate audio for every fixed phrase so the first one is
        instant. Idempotent."""
        for key, text in FIXED_PHRASES.items():
            if key not in self._cache:
                self._cache[key] = self._synthesize(text)
        self._warmed = True

    @property
    def is_warmed(self) -> bool:
        return self._warmed

    def cached_keys(self) -> list[str]:
        return sorted(self._cache)

    def _synthesize(self, text: str) -> bytes:
        """Text -> audio bytes. Stub returns a deterministic placeholder so
        the cache behaves the same shape as the real path."""
        if _stub_elevenlabs():
            return b"STUB_AUDIO:" + text.encode("utf-8")

        from elevenlabs.client import ElevenLabs  # lazy: real path only

        client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
        audio = client.text_to_speech.convert(
            voice_id=os.environ["ELEVENLABS_VOICE_ID"],
            model_id="eleven_turbo_v2_5",
            text=text,
        )
        return b"".join(audio)

    # -- playback ------------------------------------------------------------

    def say(self, key: str) -> SpokenLine:
        """Speak a fixed phrase by key. Warms the cache lazily if needed."""
        if key not in FIXED_PHRASES:
            raise KeyError(f"unknown phrase key: {key!r}")
        if key not in self._cache:
            self._cache[key] = self._synthesize(FIXED_PHRASES[key])
        line = SpokenLine(key=key, text=FIXED_PHRASES[key], stubbed=_stub_elevenlabs())
        self._play(self._cache[key])
        self.spoken.append(line)
        return line

    def say_text(self, text: str) -> SpokenLine:
        """Speak an ad-hoc line (e.g. a caregiver note already shortened by
        comms.spoken_form). Not cached -- it is one-off."""
        audio = self._synthesize(text)
        line = SpokenLine(key=None, text=text, stubbed=_stub_elevenlabs())
        self._play(audio)
        self.spoken.append(line)
        return line

    def _play(self, audio: bytes) -> None:
        """Play audio on the laptop speaker. In stub mode this is a no-op;
        real playback is wired when STUB_ELEVENLABS=0 so tests never need an
        audio device."""
        if _stub_elevenlabs():
            return
        # Real playback hook (Phase 3): write to a temp file and hand to the
        # OS player, or stream via the elevenlabs play() helper. Kept minimal
        # until we have a confirmed voice + device at the table.
        try:
            from elevenlabs import play  # type: ignore

            play(audio)
        except Exception:  # pragma: no cover - device/codec issues at demo
            pass
