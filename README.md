# Restaurant Voice Agent — Pipecat Prototype Phase

Self-owned replacement for the Vapi-based `restaurant_voice_bot` receptionist
(see `../restaurant_voice_bot/vapi_assistant.json`) — same restaurant facts
and reservation rules, running on our own Pipecat pipeline instead of Vapi's
platform. `logInteraction`, `check_availability`, and `book_table` all write
straight to Google Sheets from this process — nothing time-critical touches
n8n mid-call anymore (see "Reservations & logging" below); n8n's role is
limited to the non-time-critical end-of-day summary (its live-webhook branch
in `n8n/restaurant_reception_workflow.json` is now dead and due for removal).

**Call path**: an existing Jio number forwards unanswered calls into Exotel,
which opens a bidirectional WebSocket ("Media Streams") straight into this
server — no Daily, no WebRTC, no XML webhook. Pipecat's FastAPI WebSocket
transport (`pipecat.transports.websocket.fastapi`) speaks that protocol via
the `ExotelFrameSerializer`, auto-detected from the connection handshake.
STT recognizes English, Hindi, and Telugu speech (Sarvam STT auto-detects
and code-switches), but Telugu is not a supported *reply* language — the
system prompt (see `_LANGUAGE_INSTRUCTION` in `app/pipeline/prompts.py`)
deliberately treats an apparent Telugu transcript as a misheard
English/Hindi/Hinglish utterance rather than switching into it, since this
call path only supports replying in English or Hindi/Hinglish and TTS is
English-voice-only for now (see "Deferred" below).
One restaurant active per deployment, picked via the `RESTAURANT_ID` env var
(`app/config/restaurants/`) — currently Spice Route Kitchen and Zero40
Brewing are configured; add a new client the same way without restructuring.

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
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` / `GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY` — used
  directly by the live-call tools and by `/admin` (see below). Reuses the
  same service account already granted Editor on the sheet for n8n's own
  Sheets credential — no separate sharing step needed.
- `GOOGLE_SHEET_ID` — the spreadsheet ID from the sheet's URL (the string
  between `/d/` and `/edit`). The spreadsheet needs three tabs, each with
  just its header row already in place (see "Reservations & logging" below
  for the exact columns each one needs — `sheets_client.append_row` silently
  drops any field not in a tab's header row, so a tab missing a column loses
  that data on every write rather than erroring): `Sheet1` (interaction
  log), `Bookings` (reservations), and `Recordings` (call recordings/
  transcripts/summaries — optional at first, `/admin` degrades gracefully
  if it doesn't exist yet, but recordings won't show up until it does).
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — gate for `/admin`. Defaults to
  `admin`/`admin` to match `ai-receptionist`'s local-dev precedent — **change
  this** once deployed off localhost, since that default is not meant to
  survive being on a public URL.

No Exotel-specific env vars are needed: the call/stream identifiers
(`stream_sid`, `call_sid`) arrive in the WebSocket handshake itself, and
`create_transport()` reads them off that automatically.

## Admin

`/admin` (HTTP Basic auth) shows one row per call — joining `Sheet1`,
`Bookings`, and `Recordings` by `CallSessionId` (see
`app/admin/sheets_reader.py`), not one row per logged interaction. There's
no local database in this project (unlike `ai-receptionist`), so this reads
the Google Sheet live via the Sheets API on every request rather than a
cached copy — see `app/admin/`.

It's registered on Pipecat's own dev-runner FastAPI app (`pipecat.runner.run`
exports `app` specifically so other modules can add routes before calling
`main()` — see that module's docstring), so it's the same process and same
URL as the voice agent itself, not a separate service.

## Reservations & logging

Three tools, all calling Google Sheets directly (`app/services/sheets_client.py`)
from this same process — never through n8n, since a live in-call webhook to
n8n risks dead air if Railway's free tier cold-starts it mid-call:

- `logInteraction` (`app/tools/log_interaction.py`) — appends a row to the
  `Sheet1` tab: `Timestamp, CallDate, CallSessionId, CallerName,
  CallerPhone, Topic, Resolved, Details, GuestsCount, Drift,
  CallConfidence`.
- `check_availability` (`app/tools/reservations.py`) — the model must call
  this before confirming any reservation. Validates the requested date
  isn't in the past and the requested time falls inside the active
  restaurant's posted hours (`app/config/restaurants/`, checked via
  `app/pipeline/hours.py`) and returns `available: true/false`. No seat cap
  is enforced yet, by design — every in-hours, non-past slot is available.
- `book_table` — only called after `check_availability` returns available.
  Re-validates hours itself (never trusts the model to have checked first)
  and appends a row to the `Bookings` tab: `Timestamp, BookingId,
  CallSessionId, Date, Time, GuestsCount, CallerName, CallerPhone, Status`.
- Call recordings (`app/pipeline/recording.py`) — saved separately at call
  end, appends a row to the `Recordings` tab: `Timestamp, CallSessionId,
  CallerPhone, DurationSecs, RecordingURL, Transcript, Summary`.

The system prompt (`app/pipeline/prompts.py`) hard-gates this: the model is
told never to speak a reservation confirmation without a `booked: true`
result from `book_table` in the same conversation. It's also given the
current date/time (in the restaurant's timezone) so it can resolve relative
dates like "tomorrow" — explicitly instructed to use that *only* for date
resolution, never to judge whether a requested time is "too late" relative
to when the call is happening. A call at 3 AM asking for a table at 9 PM
that same day is normal; the only thing that decides bookability is whether
the time falls inside the posted operating hours.

`google-api-python-client` is a blocking client, not asyncio-native — every
Sheets call from these tools goes through `asyncio.to_thread(...)` so it
can't stall audio on the live call while it's in flight.

## Run

```bash
python -m app.main -t exotel
```

(Run as a module, not `python app/main.py` — running it as a plain script
doesn't put the project root on `sys.path`, so the `app.config...` imports
fail with `ModuleNotFoundError`.)

`-t exotel` pins the dev runner to the telephony transport: it starts a
local FastAPI server and registers a WebSocket route at `/ws`. Exotel
doesn't use an XML webhook (unlike Twilio/Telnyx/Plivo) — instead, the
WebSocket URL itself is configured directly as the "Voicebot Applet" in
Exotel's App Bazaar. For local testing, expose `/ws` with a tunnel (e.g.
ngrok: `ngrok http 7860`, then set the Voicebot Applet's URL to
`wss://<your-ngrok-domain>/ws`); in production this is Railway's own
public `wss://` URL.

There's no browser test client anymore — testing this pipeline means
placing (or forwarding) a real call through Exotel, or driving `/ws`
directly with a script that speaks Exotel's Media Streams JSON protocol
(`event: "start" | "media" | "dtmf"`, base64 PCM payloads).

**Provider is swappable, not hardcoded to Exotel.** `transport_params` in
`app/main.py` registers exotel/twilio/telnyx/plivo identically — pipecat
auto-detects whichever one actually connects and picks the matching
serializer, so no code changes are needed to switch. Exotel is the
production target, but it needs TRAI DLT lead time before it's fully live;
`-t twilio` (or `telnyx`/`plivo`) plus a free-trial/pay-as-you-go number is
a drop-in stand-in for testing/dev in the meantime — just note that,
unlike Exotel, those three connect via an XML webhook (`POST /`) rather
than a raw WebSocket URL, so also pass `--proxy <your-ngrok-or-railway-host>`
when using one of them. In the Docker image this is controlled by the
`TELEPHONY_TRANSPORT` env var (defaults to `exotel`) instead of `-t`
directly — see `Dockerfile`.

## Eval suite

`eval_scenarios/manifest.yaml` lists 23 behavioral scenarios (text and
audio) run against a fresh `app/main.py -t eval` bot per scenario, judged by
`gpt-4o-mini`. This costs real OpenAI/Sarvam API calls and writes real rows
to the configured Google Sheet — every scenario's caller name/phone is
prefixed `ZZ-EVALTEST` so they're easy to find and delete afterward.

```bash
export OPENAI_API_KEY=...   # the harness's own judge client reads this
                              # directly from the shell env, NOT from .env —
                              # it never imports app.config.settings, so
                              # load_dotenv() never runs for it. The spawned
                              # bot subprocess loads .env itself and doesn't
                              # need this exported separately.
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
  python -m pipecat.cli.main eval suite eval_scenarios/manifest.yaml
```

Two things confirmed the hard way, not guessed:

- **Use `python -m pipecat.cli.main`, not the installed `pipecat`/`pc`
  console-script wrapper.** The wrapper's `sys.path[0]` is its own install
  location, not this repo — so the 6 audio scenarios (which load
  `eval_scenarios.services` via pipecat's `factory:` mechanism) fail with
  `ModuleNotFoundError: No module named 'eval_scenarios'` when run through
  it. Module-mode (`-m`) always puts the current directory on `sys.path[0]`,
  which fixes this.
- **`PYTHONUTF8=1 PYTHONIOENCODING=utf-8` are required on Windows.** Without
  them, the harness's own progress output crashes with
  `UnicodeEncodeError: 'charmap' codec can't encode character '✗'` the
  moment a scenario fails — the same reason the Dockerfile sets both.

**Known fixture staleness, not a bot bug if you see these fail:**

- A handful of scenarios (`03`, `10`, `12`, `13`, `19`) hardcode an absolute
  reservation date. These necessarily rot as real time passes them by —
  the eval YAML format has no templating for "tomorrow," so there's no way
  to keep them evergreen short of hand-bumping the dates periodically (or
  building a preprocessing step that does it). If one of these fails on a
  date/past-date complaint, check today's date against the hardcoded one
  before assuming a regression.
- `01_hours_question` and `04_reservation_outside_hours` assert Spice Route
  Kitchen-specific facts (e.g. "closed on Mondays"). They'll fail if
  `RESTAURANT_ID` isn't `spice_route_kitchen` when the suite runs — set it
  explicitly for a full-suite run rather than trusting whatever `.env`
  happens to have active locally.
- In text-modality scenarios, the `response`/`llm_response` event observes
  the model's **raw, pre-`SecondParagraphFilter` output** — not what
  actually reaches TTS (see `pipecat/evals/harness.py`'s own event-taxonomy
  docstring: `tts_response` is audio-modality only). A scenario asserting
  on caller-facing cleanliness can fail here even when a real caller would
  never hear the offending text — cross-check against the raw `aggregate`
  in the scenario's `.eval.log` before treating it as a live bug.

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
  `guestsCount`). Originally POSTed a Vapi-shaped envelope to n8n's webhook
  (matching `1b. Parse Vapi Tool Call`'s expected shape) so
  `n8n/restaurant_reception_workflow.json` needed zero edits; now writes
  directly to Sheets instead (see "Reservations & logging" above) — n8n's
  `1a`-`1f` live-webhook branch is unused as a result and should be deleted
  from that workflow next time it's touched.

## Deviations from the original plan doc (found while building, not guesses)

- **TTS is OpenAI (`voice=shimmer`, matching the Vapi config), not edge-tts.**
  Telugu/Hindi-matched voices are deferred — replies are correct-language
  text from the LLM, but always spoken in an English TTS voice for now. When
  Telugu/Hindi voice quality gets picked up, Sarvam TTS is the natural next
  thing to evaluate, since Sarvam is already doing STT here.
- **No `app/services/edge_tts_service.py` and no custom transport module.**
  The original directory sketch assumed hand-wiring a transport and a custom
  TTS wrapper. Pipecat's dev runner (`pipecat.runner.run.main()`) already
  picks the transport from `transport_params` itself — `app/main.py` only
  needs a `bot(runner_args)` entry point, nothing custom to build.
- **WebRTC (browser test client) was dropped once real telephony landed.**
  The prototype initially ran on `SmallWebRTCTransport` for browser-based dev
  calls (with a Metered.ca TURN relay so it worked off localhost). That's
  gone now — `app/main.py` wires only Exotel's `FastAPIWebsocketTransport`,
  since a real phone number is the actual call path and keeping WebRTC around
  as a second transport had no purpose beyond dev convenience.

## Explicitly out of scope for this phase (flagged, not built)

- Sarvam STT accuracy hasn't been separately re-validated against Exotel's
  8kHz phone audio (vs. the higher sample rates STT is usually tuned on) —
  worth confirming under real call conditions, not just assumed at parity.
- Multi-restaurant runtime config loading (structure allows it, only Spice
  Route Kitchen is wired up).
- Telugu/Hindi TTS voice matching (see "Deviations" above).
- **No seat/order cap.** `check_availability` validates operating hours only
  — every in-hours slot reports available, on purpose, per current scope.
- **No escalation-specific flag.** The prompt already has the bot collect
  name/phone/reason on anything it can't resolve and say the owner will call
  back, and `logInteraction`'s `resolved: false` captures that it happened —
  but there's no distinct "this one's a complaint/unusual-request escalation"
  category separate from an ordinary "let me take a message," which is what
  a future immediate staff-alert trigger would need to fire on.
- **No immediate escalation alert / no rebuilt end-of-day digest.** n8n's
  `2a`-`2e` Summary Branch still runs at 9 PM (not ~10 PM), builds its
  summary with hand-rolled string logic rather than an LLM call, and tries
  to place an outbound call via Vapi's API — a platform this project no
  longer uses. Needs a rebuild once the delivery channel (WhatsApp/SMS/email)
  is decided.
- **No missed-call → outbound-callback detection**, and outbound calling
  shouldn't be enabled at all until TRAI DLT registration is complete.
- **No transcript persistence or outcome database.** `logInteraction` logs a
  short topic summary, not a verbatim transcript, and there's no FAQ/
  booking/order/escalation/missed outcome taxonomy yet. Full-call audio
  recording is built and live (`app/pipeline/recording.py`), landing in a
  "Call Recordings" folder in Drive via `app/services/drive_oauth_client.py`
  — OAuth as an actual Google account, not the service account (which has
  had zero Drive storage quota since 2021, confirmed live via
  `storageQuotaExceeded` on an actual upload attempt). `app/services/r2_client.py`
  (Cloudflare R2) is the intended longer-term backend once R2 is actually
  activated on the Cloudflare account — see `GOOGLE_OAUTH_*` / `R2_*` in
  `.env.example` for both setups.
- **No phone-order-taking flow.** The prompt/tools only handle reservations;
  `book_table`/`Bookings` don't cover takeout/delivery orders.

## A note on Pipecat API stability

This is a fast-moving library. Every import path and constructor signature
in this scaffold was checked against the actual `pipecat-ai` source on
GitHub (`main` branch) at build time, not from memory or docs prose — but if
`pip install` pulls a version where something's shifted, the fastest way to
resync is the telephony examples under `examples/` in the
[pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat) repo (search for
`ExotelFrameSerializer` / `FastAPIWebsocketTransport`), which are
structurally closest to this app (OpenAI LLM + TTS, function calling,
WebSocket telephony transport).
