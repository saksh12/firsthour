# Manual test: caregiver coordinator (issue #23) on the Mac + Pi + BOX-3

Run on the Mac that has the Pi (QNX) and the ESP32-S3-BOX-3 connected, following
`connect.md` for the network, SSH and reader. Commands run from the Mac repo root.
`B` is the backend you test against (the hardware backend is on 8001 in `connect.md`).

```bash
export B=http://127.0.0.1:8001
git fetch && git checkout comms/caregiver-coordinator
cd backend && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest -q && cd ..
cd comms-gateway && npm ci && npm test && npm run typecheck && cd ..
```

## 0. Config for the test (root `.env`, no real values in git)

```dotenv
CAREGIVER_NUMBERS=+1AAA,+1BBB        # two real phones you hold; the first is called first
CAREGIVER_NAMES=+1AAA:Sarah,+1BBB:Tom
VOICE_CALL_AFTER_SECONDS=20
STUB_PHOTON=1                        # part A; 0 in parts B and C
STUB_ELEVENLABS=1                    # part A; 0 in part C
```

Restart the backend after editing `.env`. Check `curl $B/health` shows a fresh Pi
heartbeat, and open the dashboard (`connect.md` section 3). It now has a **Caregivers** panel.

## A. Stubs only (no credentials): coordination, Q&A, escalation

Start an incident on the real hardware: press **BOOT** on the BOX-3 (or do the fall
gesture). Expect the Pi alarm and `TIME DOWN`, dashboard `CHECKING`, then `ALERTING`
after 8 s of silence. Then play both caregivers with the stub inbound:

```bash
say_as() { curl -s -X POST $B/comms/inbound -H 'content-type: application/json' -d "{\"sender\":\"$1\",\"text\":\"$2\"}"; echo; }
outbox() { curl -s $B/comms/stub/outbox | python3 -m json.tool | tail -30; }
```

| # | Do | Expect |
| --- | --- | --- |
| A0 | Read the alert in `outbox` | "How soon can you be here?", and no `UNLOCK` code in it |
| A1 | Wait 20 s with no reply | Log: `Voice call dialing · Sarah · no reply for 20 s`, then `placed (stub)`. 20 s later the same for Tom. No more calls after that. (Any reply, even a question, stops the calls) |
| A2 | `say_as +1AAA "is she still talking?"` | `{"type":"question"}`. `outbox` has an `answer` text to +1AAA built from the incident only. Nothing read aloud. No 4-digit code in it |
| A3 | `say_as +1BBB "I might come"` | Read aloud as a message. Caregivers panel: Tom `REPLIED`, not on the way |
| A4 | `say_as +1AAA "on my way, 10 min"` | Box says "Sarah is about 10 minutes away." Panel: Sarah `ASSIGNED · ETA 10 min`. `outbox`: `code` to +1AAA ("You said 10 minutes… reply UNLOCK <code>"), `stand_by` to +1BBB (no code). Dashboard log: "Door code texted to +1AAA", without the code |
| A5 | `say_as +1AAA "I'm here"` | Box says "Sarah is at the door." Panel: at the door |
| A6 | `say_as +13105559999 "on my way"` then `say_as +13105559999 "UNLOCK 0000"` | Refused, not read aloud, unlock attempt logged as refused |
| A7 | Read the code from the A4 reply in `outbox`, then `say_as +1AAA "UNLOCK <code>"` | Dashboard says "Unlocking…" until the Pi reports `local_unlock`; BOX-3 latch opens, relocks after the hold |
| A8 | New incident with "I hit my head" typed as the first answer | A call is placed on the next tick, reason `head strike` |

## B. Live Photon text + restart (STUB_PHOTON=0, gateway running)

1. Each phone texts the agent line once (shared line). Start the gateway: `cd comms-gateway && npm run dev`.
2. Start an incident (BOOT). Both phones get the alert. Dashboard log: `alert sent → … (Photon accepted)`. It must never say delivered/read.
3. Phone A texts `is she ok?` and gets an answer in the same thread. Phone A texts `omw 5 min`: phone B gets the stand-by text.
4. **Restart test**: stop the gateway (Ctrl-C). From phone B text `are you there?`. Start the gateway again. Expect the startup line `resuming from <timestamp>` and phone B's text forwarded once (answered). `comms-gateway/.state/gateway.json` exists.
5. Phone A texts `UNLOCK <code>`: Pi opens the latch as in A7.

## C. Live voice escalation (STUB_ELEVENLABS=0)

Needs `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID` (the imported
Photon SIP number), `ELEVENLABS_WEBHOOK_SECRET` and `VOICE_TOOL_TOKEN` in `.env`. Rotate the
Photon outbound SIP password first (TODOS.md).

1. Tunnel the backend: `cloudflared tunnel --url $B`. Only `/health`, `/voice/postcall` and `/voice/incident` answer through it; check `curl https://<tunnel>/incident` is 404.
2. In the ElevenLabs agent: post-call webhook (transcription) → `https://<tunnel>/voice/postcall` with the HMAC secret; a webhook tool `GET https://<tunnel>/voice/incident` with header `Authorization: Bearer <VOICE_TOOL_TOKEN>`. Prompt uses `{{patient_name}}`, `{{room}}`, `{{time_down}}`, `{{triage_summary}}`, `{{on_blood_thinner}}`, `{{last_answer}}`, `{{eta_question}}`, and says the door can only be opened by texting the code.
3. Start an incident and don't text. After 20 s phone A rings. The agent knows the triage summary and time down and asks for an ETA. Say "I'll be there in 5 minutes". Also say "unlock the door".
4. Expect: the agent refuses to unlock and tells you to text the code; the door stays locked. After hang-up (webhook): log `Voice call completed`, box says "Sarah is about 5 minutes away.", phone A gets "You said 5 minutes on the call. We told them. At the front door, reply UNLOCK <code>."
5. Phone A texts `UNLOCK <code>`: latch opens only after the Pi confirms.

TODO(verify) on this first real call: the `ElevenLabs-Signature` format (`t=…,v0=…`) and
that `conversation_id` in the outbound response matches the webhook's. If the webhook returns
401, check the secret first.

## Reset

`BACKEND_URL=$B bash scripts/demo_reset.sh` (connect.md section 5). Pi shows `READY`,
latch locked, Caregivers panel empty.
