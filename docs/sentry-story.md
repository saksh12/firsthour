# Sentry story

Running notes for the Sentry challenge: "at least two products beyond error monitoring,
and how observability shaped the build." Add findings here as they happen: what you saw,
what you changed. Only write numbers you measured.

## What we use

| Product | How |
| --- | --- |
| **Tracing** | One trace per incident (`fall_incident`, tagged `incident_id`, `final_state`). Spans: `sensor_event` (with `edge_to_backend_ms`), `voice_check` (fall → first answer or silence), `stepfun_triage`, `photon_send`, `unlock_decision` (allowed + reason), `command_to_pi` (queued → picked up by the Pi). Pi polling, heartbeats and `/health` aren't traced, which would be noise. |
| **Logs** | Every INFO+ log line: state changes, every unlock attempt with its reason, StepFun latency (`stepfun call=… ms=… ok=…`). This is the audit trail. |
| **Uptime Monitoring** | Monitor the tunneled `GET /health`. With `STUB_EDGE=0` it returns 503 when the Pi hasn't sent a heartbeat in 30 s, so a red monitor means the box is down, not just the laptop. |

## Setup (D)

1. Create the Sentry project and put the DSN in `.env` as `SENTRY_DSN=…` (never commit it).
2. Start the backend. The log should say `sentry enabled`.
3. Expose `/health`: `cloudflared tunnel --url http://localhost:8000` (or ngrok).
4. In Sentry, create an Uptime monitor on `https://<tunnel>/health`.
5. Run `python3 scripts/simulate_fall.py`, then open Traces → `fall_incident`.

## Findings

### 1. The unlock code was about to be sent to Sentry in request bodies
While writing tests that capture exactly what the SDK would send, we found the FastAPI
integration attaches **request bodies** to request traces by default, even with
`send_default_pii=False`. A caregiver's `UNLOCK 4821` reply would have left the box.
**Change:** `max_request_body_size="never"`, plus a scrubber for `UNLOCK <4 digits>`.

### 2. Phone numbers were leaking through log parameters
After masking phone numbers in log messages, the same test still found them: Sentry
Logs also sends each call's raw format arguments as attributes
(`sentry.message.parameter.N`).
**Change:** the scrubber now covers every log attribute. Numbers are kept as `+***0101`
so the audit trail still shows *which* enrolled number acted. Guarded by
`backend/tests/test_telemetry.py`.

### 3. (next) Slowest stage
TODO(D): after a real run with the real keys, open a `fall_incident` trace, note the
slowest span and its measured duration, and what (if anything) we changed.
