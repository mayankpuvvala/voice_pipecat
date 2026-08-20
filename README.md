# Restaurant Voice Agent — Pipecat Prototype Phase

Self-owned replacement for the Vapi-based `restaurant_voice_bot` receptionist
(see `../restaurant_voice_bot/vapi_assistant.json`) — same restaurant facts,
reservation rules, and `logInteraction` behavior, running on our own Pipecat
pipeline instead of Vapi's platform, with the existing n8n logging + end-of-day
summary workflow left completely unchanged.

**This phase**: browser/WebRTC test calls only, no real phone number yet.
English + Hindi + Telugu (Sarvam STT auto-detects and code-switches; replies
are read aloud in an English TTS voice for now — see "Deferred" below).
Single restaurant (Spice Route Kitchen), but the config layout is shaped for
adding more restaurants later without restructuring.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then fill in the keys below
```

Env vars needed in `.env`:
- `OPENAI_API_KEY` — same key `ai-receptionist` already uses works fine here.
- `SARVAM_API_KEY` — a Sarvam key already exists in `ai-receptionist/.env`
  under `SARVAM_API` (different var name, same value) — reuse it or grab a
  fresh one from the Sarvam dashboard.
- `N8N_WEBHOOK_URL` — the same `restaurant-log-interaction` webhook URL the
  existing Vapi workflow already posts to.
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` / `GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY` — for
  `/admin` (see below). Reuses the same service account already granted
  Editor on the sheet for n8n's own Sheets credential — no separate sharing
  step needed.
- `GOOGLE_SHEET_ID` — the spreadsheet ID from the sheet's URL (the string
  between `/d/` and `/edit`).
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — gate for `/admin`. Defaults to
  `admin`/`admin` to match `ai-receptionist`'s local-dev precedent — **change
  this** once deployed off localhost, since that default is not meant to
  survive being on a public URL.

## Admin

`/admin` (HTTP Basic auth) shows every logged interaction — same columns as
the sheet (`Timestamp`, `CallDate`, `CallerName`, `CallerPhone`, `Topic`,
`Resolved`, `Details`, `GuestsCount`, `Drift`, `CallConfidence`), newest
first. There's no local database in this project (unlike `ai-receptionist`),
so this reads the Google Sheet live via the Sheets API on every request
rather than a cached copy — see `app/admin/`.

It's registered on Pipecat's own dev-runner FastAPI app (`pipecat.runner.run`
exports `app` specifically so other modules can add routes before calling
`main()` — see that module's docstring), so it's the same process and same
URL as the voice agent itself, not a separate service.

## Run

```bash
python -m app.main
```

(Run as a module, not `python app/main.py` — running it as a plain script
doesn't put the project root on `sys.path`, so the `app.config...` imports
fail with `ModuleNotFoundError`.)

This starts a local dev server at `http://localhost:7860` with a prebuilt
browser test client (redirects there automatically) — this is the WebRTC
equivalent of Vapi's "Talk to Assistant" test call. Open it, allow mic
access, and talk.

## What's ported vs. what's new

- `app/config/restaurants/spice_route_kitchen.py` — the restaurant facts and
  system prompt are a **verbatim port** of `vapi_assistant.json`'s
  `model.messages[0].content`. Nothing about the business logic, reservation
  rules, or `logInteraction` timing was changed.
- `app/pipeline/prompts.py` — the one real addition: a language instruction
  appended after the ported prompt, telling the model to reply in whatever
  language/mix the caller used. Vapi's transcriber was pinned to
  `language: "en"`; this pipeline's Sarvam STT isn't, so the model actually
  needs telling.
- `app/tools/log_interaction.py` — same `logInteraction` schema
  (`callerName`, `callerPhone`, `topic`, `resolved`, `details`,
  `guestsCount`), but serialized into the same Vapi-shaped webhook envelope
  n8n's `1b. Parse Vapi Tool Call` node already expects
  (`message.toolCalls[0].function.arguments`). **Zero changes** to
  `n8n/restaurant_reception_workflow.json` — it's copied here unmodified.

## Deviations from the original plan doc (found while building, not guesses)

- **TTS is OpenAI (`voice=shimmer`, matching the Vapi config), not edge-tts.**
  Telugu/Hindi-matched voices are deferred — replies are correct-language
  text from the LLM, but always spoken in an English TTS voice for now. When
  Telugu/Hindi voice quality gets picked up, Sarvam TTS is the natural next
  thing to evaluate, since Sarvam is already doing STT here.
- **No `app/services/edge_tts_service.py` and no `app/transport/webrtc_dev.py`.**
  The original directory sketch assumed hand-wiring `SmallWebRTCTransport`
  and a custom TTS wrapper. Current Pipecat (checked against the actual
  `pipecat-ai` source on GitHub, not docs prose) has a built-in dev runner
  (`pipecat.runner.run.main()`) that serves the browser WebRTC test client
  and picks the transport from `transport_params` itself — `app/main.py`
  only needs a `bot(runner_args)` entry point, nothing custom to build for
  either piece.

## Explicitly out of scope for this phase (flagged, not built)

- Real phone number / telephony. When that's next: Pipecat's runner already
  lists `twilio`, `telnyx`, `plivo`, `exotel` as supported transports
  alongside `webrtc`, so this is a transport swap in `app/main.py`, not a
  rewrite — but 8kHz phone audio vs. today's WebRTC audio quality should be
  re-tested against Sarvam STT accuracy before assuming parity.
- Multi-restaurant runtime config loading (structure allows it, only Spice
  Route Kitchen is wired up).
- The "call owner" leg of the n8n Summary Branch (`2e. Call Owner via Vapi`)
  — still needs a real number and a calling platform; left as-is in n8n.
- Telugu/Hindi TTS voice matching (see "Deviations" above).

## A note on Pipecat API stability

This is a fast-moving library. Every import path and constructor signature
in this scaffold was checked against the actual `pipecat-ai` source on
GitHub (`main` branch) at build time, not from memory or docs prose — but if
`pip install` pulls a version where something's shifted, the fastest way to
resync is `examples/getting-started/07-function-calling.py` in the
[pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat) repo, which is
structurally the closest official example to this app (OpenAI LLM + TTS,
function calling, WebRTC transport).
