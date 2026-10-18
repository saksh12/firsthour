# Prize admin: human-only steps

Steps an agent cannot do. Put your name on a line before you start it. Devpost is due 11:00 AM Sun Oct 18. Confirm every rule on the live tracks page; requirements can change.

| Done | Step | Owner | Notes |
| --- | --- | --- | --- |
| [ ] | Ask an organizer whether we can enter both **Applied Intelligence** and **Beyond the Digital** | | `docs/devpost-draft.md` marks Applied Intelligence "pending organizer confirmation". Update or delete that line with the answer |
| [ ] | Confirm each prize's rules and forms on `live.lahacks.com/tracks` | | Check the "View Additional Information" page per sponsor (`AGENTS.md`) |
| [ ] | Register the GoDaddy domain and link it | | Replace `<domain>` in `docs/devpost-draft.md` |
| [ ] | Enter the **Espressif "Best Use of Espressif"** prize on Devpost | | Paste from `docs/espressif.md` if the form asks for a description |
| [ ] | Create the **Sentry Uptime** monitor on `https://<tunnel>/health` | | Tunnel: `cloudflared tunnel --url http://localhost:8000`. A quick tunnel gets a new URL each start, so recheck the monitor before judging (`docs/sentry-story.md`) |
| [ ] | Select every prize we are entering on Devpost | | List in `docs/devpost-draft.md`, "Prizes and tracks". Do not select Devin, Paper or Base44 |
