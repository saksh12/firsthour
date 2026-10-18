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
caregiver replies to the backend's `POST /comms/inbound` as `{ sender, text, messageId }`,
signed with `X-Signature`/`X-Timestamp` (the backend refuses unsigned inbound when
`STUB_PHOTON=0`). The gateway listens on localhost only.

Durable (issue #23): the inbound cursor and each caregiver's conversation live in
`GATEWAY_STATE_FILE` (default `comms-gateway/.state/gateway.json`, gitignored, holds
phone numbers). The cursor moves only after the backend accepts a reply, so a reply
that lands while the gateway (or backend) is down is forwarded on the next poll after
restart, once: a replay the backend already has returns 409 and counts as done.
Alerts still reach a caregiver who texted the shared line before a restart.
TODO: Photon's Socket.IO stream (`/v1/projects/{id}/events/ws`, `subscribe {startSeq}`)
would replace the poll; it needs `socket.io-client` and was left out of #23's PR.

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

On a shared plan Photon assigns each caregiver a shared line when they first text, and it is
not always the number the console shows (Oct 18: `.env` said `+1...8199`, every reply landed
on `+1...4622`). The poller therefore accepts replies to **any** of the project's agent lines
and answers on the line the caregiver used; `PHOTON_AGENT_HANDLE` is only the number you tell
caregivers to text. A reply on another line logs one `[poller] reply received on line ...` warning.

## Tests

```bash
npm test           # vitest: pure message logic (no network/SDK)
npm run build      # tsc, strict
```

The message mapping (`src/messages.ts`) is pure and fully tested. The Photon
client (`src/photon.ts`) and HTTP server are thin IO around it.
