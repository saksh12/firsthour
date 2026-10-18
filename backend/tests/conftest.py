import os
import sys
from pathlib import Path

import pytest

# app.main builds a module-level app at import, which needs a secret.
os.environ.setdefault("SHARED_SECRET", "test-secret")
# No audio on CI/dev machines: speak() becomes a no-op unless a test opts in.
os.environ.setdefault("VOICE_PLAYBACK", "0")

# Make both `app` (backend) and `edge` importable, so one pytest run covers
# both sides of the unlock contract.
BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
for p in (BACKEND, REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

SECRET = "test-secret"
CAREGIVER = "+13105550101"
STRANGER = "+13105559999"


class FakeClock:
    """Seconds-based clock the tests can move forward."""

    def __init__(self, start: float = 1_760_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def ms(self) -> int:
        return int(self.now * 1000)

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
