# comms-gateway

The Photon wrapper for FirstHour (Role C). **Caregiver alert text out, reply in.**
Built on **Photon Beta (2.0)** — `@photon-ai/api`, host `https://api.photon.codes/v1`,
Bearer `pho_sk_...` key. (Not Stable / `spectrum-ts`.)

Docs: <https://photon.codes/docs/beta/get-started/imessage>

## Run

```bash
cd comms-gateway
npm install
npm run dev        # starts on 127.0.0.1:8787, loads the root .env
```

Node 22+ (Photon's SDK requires it). In stub mode it needs no keys and no network.

| Endpoint | What it does |
| --- | --- |
| `GET /health` | `{ ok, stub }` |
| `POST /send` `{ to, body }` | Sends via Photon in `to`'s conversation with the agent line; stub records to an in-memory outbox. `409` if `to` hasn't texted the line yet |

Inbound: in the live path a poller pulls received iMessages every 2 s and forwards
caregiver replies to the backend's `POST /comms/inbound` as `{ sender, text }`, with
`Authorization: Bearer $SHARED_SECRET` (the backend refuses inbound without it when
`STUB_PHOTON=0`). The gateway listens on localhost only.

The backend calls `/send` itself when `STUB_PHOTON=0` (`COMMS_GATEWAY_URL`).

## Stub vs. live

- `STUB_PHOTON=1` (default): no creds, no network. The backend never calls the
  gateway; the **caregiver simulator** page (`dashboard/caregiver-simulator.html`)
  plays the caregiver's phone.
- `STUB_PHOTON=0`: real API through `@photon-ai/api` (pinned exactly: the Beta API
  changes between releases). Needs `PHOTON_TOKEN`, `PHOTON_PROJECT_ID`,
  `PHOTON_AGENT_HANDLE` in `.env`.

## Open question for the Photon table (Q3)

A **shared** iMessage line **cannot send the first message**
(`SHARED_LINE_INBOUND_REQUIRED`). Demo path: the caregiver texts the agent line
once before the demo; the gateway remembers that conversation and the alert is
sent into it. Ask the table whether a **dedicated line** lets us text first.
`TODO(verify)`, marked in `src/photon.ts`.

Inbound replies **do** carry the sender's number (`sentBy.handle`, E.164).

## Tests

```bash
npm test           # vitest: pure message logic (no network/SDK)
npm run build      # tsc, strict
```

The message mapping (`src/messages.ts`) is pure and fully tested. The Photon
client (`src/photon.ts`) and HTTP server are thin IO around it.
