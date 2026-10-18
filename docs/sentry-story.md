# Sentry story

Running notes for the Sentry challenge: "at least two products beyond error monitoring,
and how observability shaped the build." Add findings here as they happen: what you saw,
what you changed. Only write numbers you measured.

## What we use

| Product | How |
| --- | --- |
| **Tracing** | One trace per incident (`fall_incident`, tagged `incident_id`, `final_state`). Spans: `sensor_event` (with `edge_to_backend_ms`), `voice_check` (fall → first answer or silence), `speech_to_text` (clip queued → the Pi's whisper.cpp transcript), `stepfun_triage` / `stepfun_rewrite` / `stepfun_answer` (each says `result` model or fallback, and why), `photon_send`, `unlock_decision` (allowed + reason), `command_to_pi` (signed command queued → picked up by the Pi's poll), `unlock_to_latch` (unlock granted → the Pi reports `local_unlock`, or `unlock_failed` when the latch never answered). A span still open when the incident closes ends as `cancelled` with `ended_by`, so a missing report never looks like success. Pi polling, heartbeats and `/health` aren't traced, which would be noise. |
| **AI Agent Monitoring** | Every StepFun HTTP call is a `gen_ai.chat` span (`chat step-5-preview`) under its stage span, with the attributes from Sentry's [AI Agents instrumentation guide](https://docs.sentry.io/platforms/python/tracing/instrumentation/custom-instrumentation/ai-agents-module/): `gen_ai.operation.name`, `gen_ai.provider.name` (`stepfun`), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens` / `output_tokens` / `total_tokens` from the response's `usage`, `gen_ai.response.finish_reasons`. Never the prompt or the completion: those are the person's answers. sentry-sdk 2.71 sends these spans as their own span items in the same trace. TODO(verify): open Insights → AI Agents in the project after a live run. |
| **Logs** | Every INFO+ log line: state changes, every unlock attempt with its reason, StepFun latency (`stepfun call=… ms=… ok=…`). This is the audit trail. |
| **Uptime Monitoring** | Monitor the tunneled `GET /health`. With `STUB_EDGE=0` it returns 503 when the Pi hasn't sent a heartbeat in 30 s, so a red monitor means the box is down, not just the laptop. Still needs a human to create the monitor (steps below). |
| **Session Replay** | On the dashboard. The page fetches `GET /config/public` (the DSN only, never anything else from `.env`) and loads the pinned Sentry browser bundle with replay from the CDN. All text and inputs masked, all media blocked, `sendDefaultPii: false`, no console breadcrumbs, no browser tracing. Offline Wi-Fi or a blocked CDN just means no replay. Checked in headless Chrome against a fake DSN (SDK and replay start, mask options set); TODO(verify) a replay arriving in the real project. |

## Setup (D)

1. Create the Sentry project and put the DSN in `.env` as `SENTRY_DSN=…` (never commit it).
2. Start the backend. The log should say `sentry enabled`.
3. Expose `/health`: `cloudflared tunnel --url http://localhost:8000` (or ngrok). The tunnel publishes the whole port, so the backend answers 404 to every tunneled request except `/health` (it spots the `X-Forwarded-For` / `Cf-Connecting-IP` headers). `/config/public` is not reachable through it either.
4. In Sentry, create an Uptime monitor on `https://<tunnel>/health`.
5. Open the dashboard on the laptop (`http://localhost:5173`); with a DSN set it starts recording a masked replay.
6. Run `python3 scripts/simulate_fall.py`, then open Traces → `fall_incident`.

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

### 3. The slowest stage is StepFun, so nothing waits on it
Oct 18 live run with every stub off (real StepFun, ElevenLabs, Photon and Sentry). The
numbers come from the backend logs and the spans that wrap the same calls.

- The slowest span in every incident trace was `stepfun_triage`: 8-22 s per call that
  morning (5-38 s on Saturday). The caregiver-reply rewrite took 6-12 s and the caregiver
  Q&A answer 7.4 s. With the old 3 s / 6 s deadlines, every one of those calls fell back
  to the rules.
- `step-5-preview` has no "no reasoning" setting (`reasoning_effort` accepts only low,
  medium or high; we use low), and the flash models were no faster.
- **Change:** triage runs in the background after an instant rule-based record, which
  drives escalation and the alert, so a slow model never delays help. PR #39 raised the
  rewrite and Q&A deadlines to 10 s and 12 s and kept the fallbacks.
- The same trace put the rest of the path in perspective: fall event → "Are you okay?"
  played took 4.7-4.8 s on a fresh backend (the ElevenLabs line generated, not cached) and
  0.7 s for a cached line. ALERTING → Photon accepted the alert text: about 0.3 s. A real
  phone reply → `RESPONDING`: 3.5 s from the phone's send (the gateway polls Photon every
  2 s). `UNLOCK <code>` → backend grant: 3 ms, and the signed command went out on the
  Pi's next poll.
- The tracing also exposed a gateway bug, fixed in PR #39: real replies were being
  dropped because Photon gives each caregiver their own shared line.
- TODO(measure): unlock reply → latch confirmed, now that `unlock_to_latch` exists; and
  fall → caregiver text delivered (Photon reports "accepted", not "delivered").

### 4. The unlock span stopped at the poll, not at the door
Reviewing the trace layout, we saw `command_to_pi` ended when the Pi picked the signed `unlock` up. It
said nothing about whether the latch opened, which is the number the demo claims.
**Change:** a new `unlock_to_latch` span runs from the grant until the Pi reports
`local_unlock` (the Arduino answered `ACK,U`) or `unlock_failed` (no `ACK,U` in time,
status `deadline_exceeded`). If the Pi never reports, the span ends with the incident as
`cancelled`. In the same pass every StepFun call got the `gen_ai.*` attributes above, so
token use and the model's latency sit in Sentry's AI Agents views next to the fallback
reason, without any of the person's words. Both are covered by
`backend/tests/test_telemetry.py` and `backend/tests/test_stepfun.py`.
