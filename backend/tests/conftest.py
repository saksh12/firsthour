import os
import sys
from pathlib import Path

import pytest

# app.main builds a module-level app at import, which needs a secret.
os.environ.setdefault("SHARED_SECRET", "test-secret")
# No audio on CI/dev machines: speak() becomes a no-op unless a test opts in.
os.environ.setdefault("VOICE_PLAYBACK", "0")
# Never report to the real Sentry project: the module-level app reads the root .env, and
# its global SDK client would turn every log.error a test provokes into an issue email.
# Forced, not setdefault: load_dotenv never overrides a variable that is already set.
os.environ["SENTRY_DSN"] = ""

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


_last_poll_ts = [0]


def signed_poll(client, device_id: str = "bath-01"):
    """GET /edge/commands the way the Pi does it: signed, with a strictly newer ts."""
    import time

    from edge.security import sign_poll

    ts = _last_poll_ts[0] = max(int(time.time() * 1000), _last_poll_ts[0] + 1)
    return client.get("/edge/commands", params={"device_id": device_id, "ts": ts},
                      headers={"X-Signature": sign_poll(SECRET, ts, device_id)})


from fastapi.testclient import TestClient  # noqa: E402


class LaptopClient(TestClient):
    """Requests from 127.0.0.1, like the dashboard in a browser on the demo laptop."""

    def __init__(self, app, **kw):
        kw.setdefault("client", ("127.0.0.1", 50000))
        super().__init__(app, **kw)
