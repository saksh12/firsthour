"""Voice escalation through ElevenLabs Agents over the Photon SIP number (issue #23).

Outbound: POST /v1/convai/sip-trunk/outbound-call
  https://elevenlabs.io/docs/eleven-agents/api-reference/sip-trunk/outbound-call
Post-call webhook (HMAC, header ElevenLabs-Signature: t=<unix s>,v0=<hex>):
  https://elevenlabs.io/docs/eleven-agents/workflows/post-call-webhooks
Finished-call lookup when the webhook does not arrive: GET /v1/convai/conversations/{id}
  https://elevenlabs.io/docs/api-reference/conversations/get

The call gets incident facts as dynamic variables, never the unlock code, and
nothing a caller says is ever parsed as an unlock: the door only opens through
the texted UNLOCK <code> path.
"""
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .triage import urllib_transport

log = logging.getLogger("firsthour.calls")

API_BASE = "https://api.elevenlabs.io"
OUTBOUND_PATH = "/v1/convai/sip-trunk/outbound-call"
CONVERSATIONS_PATH = "/v1/convai/conversations/"
SIGNATURE_TOLERANCE_S = 30 * 60  # the SDK's construct_event default
# Conversation `status` values that mean ElevenLabs is finished with the call. Seen on real
# calls Oct 18: "done" (TODO(verify) "failed" on a call nobody answers).
FINISHED = ("done", "failed")

Transport = Callable[[str, dict, dict, float], dict]
Fetch = Callable[[str, dict, float], dict]


def urllib_get(url: str, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class VoiceCalls:
    def __init__(self, *, stub: bool, api_key: str = "", agent_id: str = "", phone_number_id: str = "",
                 base_url: str = "", transport: Transport = urllib_transport, fetch: Fetch = urllib_get,
                 timeout_s: float = 10):
        self.real = not stub and bool(api_key and agent_id and phone_number_id)
        if not stub and not self.real:
            log.warning("STUB_ELEVENLABS=0 but ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID / "
                        "ELEVENLABS_PHONE_NUMBER_ID are not all set; calls stay stubbed")
        self._key, self._agent, self._phone = api_key, agent_id, phone_number_id
        self._base = (base_url or API_BASE).rstrip("/")
        self._url = self._base + OUTBOUND_PATH
        self._transport, self._fetch, self._timeout = transport, fetch, timeout_s
        self.log: list[dict] = []  # stub calls, for tests and the demo
    def place(self, to: str, variables: dict) -> dict:
        """Blocking; callers run it off the incident lock. Returns {"conversation_id", "stubbed"}."""
        assert all(isinstance(v, (str, int, float, bool)) for v in variables.values())
        if not self.real:
            conv = f"stub-call-{len(self.log) + 1}"
            self.log.append({"to": to, "variables": dict(variables), "conversation_id": conv})
            return {"conversation_id": conv, "stubbed": True}
        body = {
            "agent_id": self._agent,
            "agent_phone_number_id": self._phone,
            "to_number": to,
            "conversation_initiation_client_data": {"dynamic_variables": variables},
        }
        r = self._transport(self._url, {"xi-api-key": self._key}, body, self._timeout)
        if r.get("success") is False:
            raise RuntimeError(f"outbound call refused: {r.get('message', '')}"[:200])
        return {"conversation_id": r.get("conversation_id") or "", "stubbed": False}

    def result(self, conversation_id: str) -> Optional[dict]:
        """The finished call, shaped like parse_postcall()'s output plus "status", or None while
        ElevenLabs is still on it. The webhook is the fast path; this is how the result still
        reaches the incident when the tunnel in front of /voice/postcall is down (a real call's
        webhook was lost that way Oct 18)."""
        if not self.real:
            return None
        url = self._base + CONVERSATIONS_PATH + urllib.parse.quote(conversation_id, safe="")
        data = self._fetch(url, {"xi-api-key": self._key}, self._timeout)
        status = data.get("status")
        if status not in FINISHED:
            return None
        call = parse_postcall({"type": "post_call_transcription", "data": data}) or {
            "conversation_id": conversation_id, "incident_id": None, "user_turns": [], "summary": ""}
        call["conversation_id"] = call["conversation_id"] or conversation_id
        return {**call, "status": status}

TOKEN_PATH = "/v1/convai/conversation/token"


def companion_token(api_key: str, agent_id: str, base_url: str = "", timeout_s: float = 10) -> str:
    """A short-lived WebRTC conversation token for the browser, so the API key stays on the laptop
    backend. https://elevenlabs.io/docs/eleven-agents/libraries/java-script (private agents)."""
    url = (base_url or API_BASE).rstrip("/") + TOKEN_PATH + "?" + urllib.parse.urlencode({"agent_id": agent_id})
    req = urllib.request.Request(url, headers={"xi-api-key": api_key})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        body = json.loads(r.read())
    token = body.get("token") or body.get("conversation_token")
    if not token:
        raise RuntimeError("no token in ElevenLabs response")
    return token


def verify_postcall_signature(secret: str, raw: bytes, header: Optional[str],
                              now_s: Optional[float] = None) -> tuple[bool, str]:
    """ElevenLabs-Signature: t=<timestamp>,v0=hex(HMAC_SHA256(secret, "<t>.<raw body>")).
    Verified on a real call's webhook Oct 18 04:26 (accepted, commitment recorded)."""
    if not secret:
        return False, "webhook secret not configured"
    if not header:
        return False, "missing signature"
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, sig = parts.get("t", ""), parts.get("v0", "")
    if not t.isdigit() or not sig:
        return False, "malformed signature"
    now = time.time() if now_s is None else now_s
    if abs(now - int(t)) > SIGNATURE_TOLERANCE_S:
        return False, "stale signature"
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False, "bad signature"
    return True, "ok"


def parse_postcall(event: dict) -> Optional[dict]:
    """The parts of a post_call_transcription event we use, or None for other event types."""
    if not isinstance(event, dict) or event.get("type") != "post_call_transcription":
        return None
    data = event.get("data") or {}
    turns = data.get("transcript") or []
    analysis = data.get("analysis") or {}
    dyn = (data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}
    return {
        "conversation_id": str(data.get("conversation_id") or ""),
        "incident_id": dyn.get("incident_id"),
        "user_turns": [str(t["message"]) for t in turns
                       if isinstance(t, dict) and t.get("role") == "user" and t.get("message")],
        "summary": str(analysis.get("transcript_summary") or "")[:500],
    }
