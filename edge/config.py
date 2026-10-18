"""Edge config. Stdlib only: this runs on the Pi (QNX).

Every value can be overridden from the environment or the repo-root .env.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    # Tiny .env reader so the Pi needs no python-dotenv. Real env vars win.
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #")[0].strip()
        os.environ.setdefault(key.strip(), value)


_load_dotenv(REPO / ".env")


def _f(key, default):
    return float(os.getenv(key, default))


DEVICE_ID = os.getenv("DEVICE_ID", "bath-01")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")  # on the Pi: the laptop's LAN IP
SHARED_SECRET = os.getenv("SHARED_SECRET", "")
SERIAL_PORT = os.getenv("SERIAL_PORT", "")  # TODO(verify): device path on QNX
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))

# Fusion thresholds. PLACEHOLDERS until the section 10.2 calibration run;
# replace them with the measured numbers and say where they came from.
SOUND_THRESHOLD = _f("SOUND_THRESHOLD", "400")
ACCEL_THRESHOLD_G = _f("ACCEL_THRESHOLD_G", "1.0")
FUSION_WINDOW_MS = int(os.getenv("FUSION_WINDOW_MS", "400"))
COOLDOWN_S = _f("FALL_COOLDOWN_SECONDS", "30")

# Local incident stays open this long unless the backend sends a signed alarm_off.
INCIDENT_WINDOW_SECONDS = _f("INCIDENT_WINDOW_SECONDS", "600")

POLL_S = _f("POLL_SECONDS", "1")
HEARTBEAT_S = _f("HEARTBEAT_SECONDS", "5")  # backend /health goes 503 after 30 s of silence
OFFLINE_BACKOFF_S = _f("OFFLINE_BACKOFF_SECONDS", "5")
HTTP_TIMEOUT_S = _f("HTTP_TIMEOUT_SECONDS", "1")

LOG_DIR = Path(os.getenv("EDGE_LOG_DIR", str(REPO / "edge" / "logs")))
