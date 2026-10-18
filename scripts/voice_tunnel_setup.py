#!/usr/bin/env python3
"""Point the ElevenLabs voice agent at this backend's public tunnel, in one command.

The caregiver call runs on ElevenLabs over the Photon SIP number. Two things on ElevenLabs
have to know where this backend is reachable: the post-call webhook (POST /voice/postcall)
and the agent's read-only `get_incident` tool (GET /voice/incident). A cloudflared quick
tunnel gets a new hostname every time it starts, so this script redoes both, enables
webhook retries (a real call's webhook was lost to a flapping tunnel on Oct 18 05:00) and
checks the three tunneled routes.

    cloudflared tunnel --url http://127.0.0.1:8000          # in another terminal, note the URL
    python3 scripts/voice_tunnel_setup.py https://<name>.trycloudflare.com --write-env
    # or let it start the tunnel itself and keep it up:
    python3 scripts/voice_tunnel_setup.py --tunnel --write-env

Then restart the backend so it reads the new ELEVENLABS_WEBHOOK_SECRET / VOICE_TOOL_TOKEN.
Stdlib only. Reads ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, VOICE_TOOL_TOKEN, BACKEND_URL
from the environment or the repo's .env. Never prints the API key.

APIs (checked Oct 18): POST/GET/PATCH /v1/workspace/webhooks, PATCH /v1/convai/settings,
GET /v1/convai/agents/{id}, GET/PATCH /v1/convai/tools/{id}.
  https://elevenlabs.io/docs/api-reference/webhooks/create
  https://elevenlabs.io/docs/api-reference/workspace/update
  https://elevenlabs.io/docs/api-reference/tools/update
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_PATH = REPO / ".env"
API = "https://api.elevenlabs.io"
WEBHOOK_NAME = "FirstHour post-call transcription"


def env(key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ[key]
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].split(" #")[0].strip()
    return default


def api(key: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:400]


def http_get(url: str, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:200].decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode(errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def start_tunnel(backend: str) -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(["cloudflared", "tunnel", "--url", backend, "--no-autoupdate"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stderr.readline()
        if not line and proc.poll() is not None:
            break
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            return proc, m.group(0)
    proc.kill()
    sys.exit("cloudflared did not print a trycloudflare.com URL within 60 s")


def write_env(updates: dict) -> None:
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    for key, value in updates.items():
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                comment = line.split(" #", 1)[1] if " #" in line else ""
                lines[i] = f"{key}={value}" + (f" #{comment}" if comment else "")
                break
        else:
            lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("public_url", nargs="?", help="https://<name>.trycloudflare.com (or any https origin)")
    p.add_argument("--tunnel", action="store_true", help="start `cloudflared tunnel --url BACKEND` and keep it running")
    p.add_argument("--backend", default=env("BACKEND_URL", "http://127.0.0.1:8000"), help="local backend the tunnel fronts")
    p.add_argument("--write-env", action="store_true", help="write the new webhook secret and tool token into .env")
    p.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    args = p.parse_args()
    if bool(args.public_url) == args.tunnel:
        p.error("give the public URL, or --tunnel to start one")

    key, agent = env("ELEVENLABS_API_KEY"), env("ELEVENLABS_AGENT_ID")
    if not key or not agent:
        sys.exit("ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID must be set (env or .env)")
    token, new_token = env("VOICE_TOOL_TOKEN"), False

    tunnel = None
    public = args.public_url
    if args.tunnel:
        tunnel, public = start_tunnel(args.backend)
        print(f"tunnel up: {public} -> {args.backend}")
    public = public.rstrip("/")
    if not public.startswith("https://"):
        sys.exit("the public URL must be https (ElevenLabs only calls https webhooks)")

    status, hooks = api(key, "GET", "/v1/workspace/webhooks?include_usages=true")
    if status != 200:
        sys.exit(f"cannot list webhooks: {status} {hooks}")
    ours = [h for h in hooks["webhooks"] if h["name"].startswith("FirstHour")]
    current = next((h for h in ours if h["webhook_url"] == public + "/voice/postcall" and not h["is_disabled"]), None)
    print(f"webhooks named FirstHour*: {len(ours)}; one already on this URL: {'yes' if current else 'no'}")

    status, tool_ids = api(key, "GET", f"/v1/convai/agents/{agent}")
    if status != 200:
        sys.exit(f"cannot read the agent: {status} {tool_ids}")
    ids = (tool_ids.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("tool_ids") or [])
    incident_tools = []
    for tid in ids:
        st, tool = api(key, "GET", f"/v1/convai/tools/{tid}")
        cfg = tool.get("tool_config", {}) if st == 200 else {}
        url = (cfg.get("api_schema") or {}).get("url", "")
        if cfg.get("type") == "webhook" and url.endswith("/voice/incident"):
            incident_tools.append((tid, cfg))
    print(f"agent '{tool_ids.get('name')}': {len(ids)} tool(s), {len(incident_tools)} pointing at /voice/incident")

    if args.dry_run:
        print("dry run: would create a webhook on", public + "/voice/postcall", "and repoint", [t for t, _ in incident_tools])
        return

    secret = None
    if current is None:
        status, created = api(key, "POST", "/v1/workspace/webhooks", {"settings": {
            "auth_type": "hmac", "name": WEBHOOK_NAME, "webhook_url": public + "/voice/postcall"}})
        if status != 200 or not created.get("webhook_secret"):
            sys.exit(f"webhook creation failed: {status} {created}")
        webhook_id, secret = created["webhook_id"], created["webhook_secret"]
        print(f"created webhook {webhook_id} -> {public}/voice/postcall")
    else:
        webhook_id = current["webhook_id"]
        print(f"keeping webhook {webhook_id}; its secret is the ELEVENLABS_WEBHOOK_SECRET already in .env")
    status, r = api(key, "PATCH", f"/v1/workspace/webhooks/{webhook_id}",
                    {"is_disabled": False, "name": WEBHOOK_NAME, "retry_enabled": True})
    print(f"retries on transient failures: {'enabled' if status == 200 else f'NOT enabled ({status} {r})'}")

    status, settings = api(key, "GET", "/v1/convai/settings")
    hooks_cfg = (settings.get("webhooks") or {}) if status == 200 else {}
    body = {"webhooks": {"post_call_webhook_id": webhook_id,
                         "events": hooks_cfg.get("events") or ["transcript"],
                         "transcript_format": hooks_cfg.get("transcript_format") or "json",
                         "exclude_transcript": bool(hooks_cfg.get("exclude_transcript", False))}}
    status, r = api(key, "PATCH", "/v1/convai/settings", body)
    print(f"workspace post-call webhook -> {webhook_id}: {'ok' if status == 200 else f'FAILED {status} {r}'}")

    for h in ours:
        if h["webhook_id"] != webhook_id and not h["is_disabled"]:
            st, _ = api(key, "PATCH", f"/v1/workspace/webhooks/{h['webhook_id']}",
                        {"is_disabled": True, "name": h["name"]})
            print(f"disabled stale webhook {h['webhook_id']} ({h['webhook_url']}): {st}")

    for tid, cfg in incident_tools:
        schema = dict(cfg.get("api_schema") or {})
        schema["url"] = public + "/voice/incident"
        headers = schema.get("request_headers")
        plain = isinstance(headers, dict) and all(isinstance(v, str) for v in headers.values())
        if plain:
            if not token:  # the header is ours to set, so a fresh token is fine
                token, new_token = secrets.token_urlsafe(24), True
            schema["request_headers"] = {**headers, "Authorization": f"Bearer {token}"}
        else:
            # The team keeps the bearer token in ElevenLabs' secret store (seen Oct 18); it cannot be
            # read back, so VOICE_TOOL_TOKEN in .env must be that same value.
            print(f"tool {tid}: Authorization comes from the ElevenLabs secret store; keeping it. "
                  f"VOICE_TOOL_TOKEN in .env must equal that stored secret"
                  + ("" if token else " (it is EMPTY right now)"))
        status, r = api(key, "PATCH", f"/v1/convai/tools/{tid}", {"tool_config": {**cfg, "api_schema": schema}})
        print(f"tool {tid} -> {public}/voice/incident: {'ok' if status == 200 else f'FAILED {status} {r}'}")

    updates = {}
    if secret:
        updates["ELEVENLABS_WEBHOOK_SECRET"] = secret
    if new_token:
        updates["VOICE_TOOL_TOKEN"] = token
    if updates:
        if args.write_env:
            write_env(updates)
            print(f"wrote {', '.join(updates)} to {ENV_PATH}. Restart the backend to pick them up.")
        else:
            print("add to .env, then restart the backend:")
            for k, v in updates.items():
                print(f"  {k}={v}")

    print("checks through the tunnel:")
    st, body = http_get(public + "/health")
    print(f"  GET /health -> {st} {'ok' if st in (200, 503) else body}")
    st, body = http_get(public + "/voice/incident", {"Authorization": f"Bearer {token}"})
    note = "" if st == 200 else " (401 until the backend restarts with the new VOICE_TOOL_TOKEN)" if st == 401 else f" {body}"
    print(f"  GET /voice/incident with the tool token -> {st}{note}")
    st, _ = http_get(public + "/incident")
    print(f"  GET /incident -> {st} {'(blocked through the tunnel, as it should be)' if st == 404 else 'EXPECTED 404'}")

    if tunnel:
        print("tunnel stays up; Ctrl-C to stop it")
        try:
            tunnel.wait()
        except KeyboardInterrupt:
            tunnel.terminate()


if __name__ == "__main__":
    main()
