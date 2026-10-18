"""M2 voice output: cached ElevenLabs TTS + ordered playback on the laptop.

Adapted from c/comms-stub-slice backend/app/voice.py (Manya's earlier work):
same idea — pre-generate fixed phrases so the first question plays instantly —
rewritten for a disk cache and one playback worker per this branch's spec.

- STUB_ELEVENLABS=1 (default): no network. Falls back to macOS `say`, else logs.
- STUB_ELEVENLABS=0 + key + voice id: ElevenLabs HTTP TTS, audio cached on disk
  under backend/audio_cache/ keyed by sha256(voice_id, model, text).
- One worker thread drains a queue so lines play in order and never overlap.
- Any failure (missing key, HTTP error, >4 s timeout, no player) falls back to
  `say`, else just logs. speak() always returns immediately and never raises.
- VOICE_PLAYBACK=0 disables all audio (CI, tests). Spoken text is NEVER logged
  (a caregiver's words are private); a hash of the line is logged instead.

ElevenLabs endpoint verified against docs (2026-10):
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128
headers: xi-api-key, Content-Type: application/json
body: {"text": ..., "model_id": ...}
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("firsthour.voice")

# ELEVENLABS_BASE_URL can point at another OpenAI-shaped endpoint (dev/proxy).
TTS_BASE_URL = "https://api.elevenlabs.io"
TTS_URL = "{base}/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
DEFAULT_MODEL = "eleven_turbo_v2_5"  # low-latency; override with ELEVENLABS_MODEL
ASSISTANT_LINE = "This is an automated assistant."
TIMEOUT_S = 4.0
CACHE_DIR = Path(__file__).resolve().parents[1] / "audio_cache"
_STOP = object()


def line_ref(text: str) -> str:
    """Short hash of a spoken line for logs — the text itself never hits logs."""
    return hashlib.sha256(text.encode()).hexdigest()[:10]


class Voice:
    """speak() -> queue -> one worker that synthesizes (cached) and plays."""

    def __init__(self, *, playback: bool = True, stub: bool = True,
                 api_key: str = "", voice_id: str = "", model: str = DEFAULT_MODEL,
                 base_url: str = TTS_BASE_URL, cache_dir=CACHE_DIR, transport=None, player=None,
                 afplay=None, say=None, timeout: float = TIMEOUT_S, span=None):
        self.playback = playback
        self.stub = stub
        self.api_key, self.voice_id, self.model = api_key, voice_id, model
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.real = not stub and bool(api_key and voice_id)
        self._transport = transport or self._http_post
        self._player = player            # injected in tests; runs argv
        self._afplay = afplay if afplay is not None else shutil.which("afplay")
        self._say = say if say is not None else shutil.which("say")
        self._span = span or (lambda **kw: _nullctx())
        self._q: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._drain, daemon=True, name="voice")
        self._worker.start()
        self._prewarm_thread: threading.Thread | None = None

    # -- public --------------------------------------------------------------

    def speak(self, text: str) -> None:
        """Queue a line; returns immediately, never raises."""
        if not self.playback or not text:
            return
        self._q.put(text)

    def prewarm(self, texts) -> None:
        """Synthesize every fixed phrase in the background so the first
        question plays instantly. No-op where there is no real TTS."""
        if not (self.playback and self.real):
            return
        self._prewarm_thread = threading.Thread(
            target=self._prewarm_all, args=(list(texts),), daemon=True, name="voice-prewarm")
        self._prewarm_thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._q.put(_STOP)
        self._worker.join(timeout)
        if self._prewarm_thread is not None:
            self._prewarm_thread.join(timeout)

    # -- internals -----------------------------------------------------------

    def _prewarm_all(self, texts) -> None:
        for t in texts:
            try:
                self._audio(t)
            except Exception as e:  # a cold cache must never take the app down
                log.warning("prewarm failed ref=%s error=%s", line_ref(t), e)

    def _drain(self) -> None:
        while True:
            item = self._q.get()
            if item is _STOP:
                return
            try:
                self._speak_one(item)
            except Exception as e:  # playback must never crash the incident flow
                log.warning("voice line failed ref=%s error=%s", line_ref(item), e)

    def _speak_one(self, text: str) -> None:
        t0 = time.monotonic()
        audio, cached, reason = self._audio(text)
        ms = (time.monotonic() - t0) * 1000
        with self._span(cached=cached):
            played = self._play(audio, text)
        log.info("voice line ref=%s cached=%s played=%s ms=%.0f%s",
                 line_ref(text), cached, played, ms,
                 f" fallback={reason}" if reason else "")

    def _audio(self, text: str):
        """-> (path|None, cached, fallback_reason)."""
        if not self.real:
            return None, False, "stub"
        path = self._cache_path(text)
        if path.exists():
            return path, True, ""
        audio = self._fetch(text)
        if audio is None:
            return None, False, "tts-failed"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(path)  # atomic: a crashed write never leaves a half file
        return path, False, ""

    def _fetch(self, text: str):
        """TTS HTTP call in a bounded thread so a hung API can't stall playback."""
        url = TTS_URL.format(base=self.base_url, voice_id=self.voice_id)
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        body = json.dumps({"text": text, "model_id": self.model}).encode()
        result: dict = {}
        def call():
            try:
                result["audio"] = self._transport(url, headers, body, self.timeout)
            except Exception as e:
                result["error"] = e
        t = threading.Thread(target=call, daemon=True)
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            log.warning("tts timeout ref=%s after=%.1fs", line_ref(text), self.timeout)
            return None
        if "error" in result:
            log.warning("tts error ref=%s error=%s", line_ref(text), result["error"])
            return None
        return result["audio"]

    @staticmethod
    def _http_post(url, headers, body, timeout):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def _cache_path(self, text: str) -> Path:
        key = hashlib.sha256(f"{self.voice_id}|{self.model}|{text}".encode()).hexdigest()[:20]
        return self.cache_dir / f"{key}.mp3"

    def _play(self, audio, text) -> bool:
        try:
            if audio is not None and self._afplay:
                return self._run([self._afplay, str(audio)])
            if self._say:
                return self._run([self._say, text])
        except Exception as e:
            log.warning("player failed ref=%s error=%s", line_ref(text), e)
        return False

    def _run(self, argv) -> bool:
        if self._player is not None:
            self._player(argv)
        else:
            subprocess.run(argv, check=True, timeout=60, capture_output=True)
        return True


class _nullctx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
