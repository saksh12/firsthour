# Judge Q&A

Prepared answers for questions judges are likely to ask. Every claim here has to match how the system actually works. Quote only latency numbers we measured (see the Latency table in `AGENTS.md`).

## "Why not use an ElevenLabs conversational agent from the start?"

**Short answer:** "Scripted and local where it has to be reliable, conversational AI where it makes the wait better. The first minute is a safety system; the next ten are a companion."

The reasons behind it:

1. **The first minute can't depend on the internet.** The opening check uses pre-recorded audio and speech-to-text on the device itself (whisper.cpp on the Pi), with no cloud call. If the internet or ElevenLabs goes down, the box still asks, listens and escalates. A cloud agent can't promise that. We bring the agent in only once the caregiver alert is already out, when an outage costs some comfort but not safety.
2. **Triage has to be predictable.** The caregiver's card needs the same five facts every time: conscious, head strike, pain, can move, bleeding. A fixed script gets clean, comparable answers. An open conversation can wander off or skip the bleeding question.
3. **No AI improvising in the riskiest moment.** In the first seconds after a fall, we don't want a language model choosing questions or sounding reassuring ("you're probably fine"). The fixed script can't give medical advice. The agent comes in later, with limits enforced in code: it can't unlock the door, never sees the door code, and can only flag things as worse, never better.
4. **Privacy.** Her answers about injuries are transcribed on the device in the room, not sent to a cloud speech service. She talks to a cloud agent only after help has been called.
5. **Speed.** "Are you okay?" plays instantly from a pre-generated cache. Starting a cloud agent session takes a few seconds, and a first question shouldn't wait on a network handshake.

## How the AI middleman works (for follow-up questions)

- **Fall and first check:** fixed, pre-recorded questions; on-device speech-to-text; rule-based triage first, refined by StepFun in the background.
- **Caregiver side (Photon texts):** the alert asks for an ETA. A clear "on my way" claims the incident and gets the one-time door code by text. Questions are answered from incident facts only. With no reply at all, an ElevenLabs agent phones the caregiver.
- **Person side after the script:** an ElevenLabs agent keeps her company. It can read incident facts (ETA, who is coming) and decides on its own whether something she says is important enough to text to the caregiver who claimed the incident, or to everyone if nobody has. Small talk stays between her and the agent. Caregiver updates are fed into the conversation as they arrive.
- **Never AI-controlled:** the door unlock (enrolled number plus the one-time code, signed command, and the Pi checks its own open incident), detecting the fall, and the alarm.
