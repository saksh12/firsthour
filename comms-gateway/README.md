# comms-gateway

The Photon wrapper for FirstHour (Role C). **Caregiver alert text out, reply in.**
Built on **Photon Beta (2.0)** — `@photon-ai/api`, host `https://api.photon.codes/v1`,
Bearer `pho_sk_...` key. (Not Stable / `spectrum-ts`.)

Docs: <https://photon.codes/docs/beta/get-started/imessage>

## Run

```bash
cd comms-gateway
npm install
npm run dev        # starts on :8787
```

Reads the shared root `.env`. In stub mode it needs no keys and no network.

| Endpoint | What it does |
| --- | --- |
| `GET /health` | `{ ok, stub }` |
| `POST /send` `{ to, body, spaceId?, senderId?, parentMessageId? }` | Sends via Photon; stub records to an in-memory outbox |
| `POST /inbound` (a Photon received-message, or `{ from, body }`) | Forwards to the backend's `POST /comms/inbound` |

In the live path a background poller also pulls received iMessages every 2s and
forwards caregiver replies to the backend, so a public webhook is optional.

## Stub vs. live

- `STUB_PHOTON=1` (default): no SDK, no creds, no network. `/send` writes to an
  outbox and returns a fake id; the poller never starts. The dashboard
  **caregiver simulator** page drives inbound for the demo.
- `STUB_PHOTON=0`: lazy-loads `@photon-ai/api` and uses the real API. Needs
  `PHOTON_TOKEN`, `PHOTON_PROJECT_ID`, `PHOTON_AGENT_HANDLE` in `.env`.

## ⚠️ Open question for the Photon table (Q3)

A **shared** iMessage line **cannot send an unsolicited first message** — the
caregiver must message the agent line first. A fall alert wants to text the
caregiver *first*, so before we rely on this end to end we must confirm whether
a **dedicated line** (or another mechanism) allows outbound-first alerts.
Until that's confirmed, the demo path is: caregiver texts the agent once →
we can then alert + reply in that conversation, with the simulator as fallback.
`TODO(verify)` — marked in `src/photon.ts`.

Inbound replies **do** carry the sender's number (`sentBy.handle`, E.164), which
answers the other half of Q3.

## Tests

```bash
npm test           # vitest: pure message logic (no network/SDK)
npm run build      # tsc, strict
```

The message mapping (`src/messages.ts`) is pure and fully tested. The Photon
client (`src/photon.ts`) and HTTP server are thin IO around it.
