# Restaurant Voice Agent — Pipecat

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
server — no Daily, no XML webhook for Exotel itself. Pipecat's FastAPI
WebSocket transport (`pipecat.transports.websocket.fastapi`) speaks that
protocol via the `ExotelFrameSerializer`, auto-detected from the connection
handshake. Twilio, Telnyx, Plivo, and Vobiz are also wired (see "Run"
below) — Vobiz auto-detects as "plivo" on the wire and is routed to its own
transport builder (`_create_vobiz_transport` in `app/main.py`) since it
needs `keepCallAlive="false"` behavior Plivo's own serializer doesn't
provide. STT recognizes English, Hindi, and Telugu speech (Sarvam STT
auto-detects and code-switches), but Telugu is not a supported *reply*
language — the system prompt (see `_LANGUAGE_INSTRUCTION` in
`app/pipeline/prompts.py`) deliberately treats an apparent Telugu transcript
as a misheard English/Hindi/Hinglish utterance rather than switching into
it. TTS is Rumik (`mulberry` model, see "TTS & STT" below), which handles
Hindi/English code-mixed replies natively — no separate voice-matching step
needed.

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

`.env.example` is the source of truth for every env var this app reads —
each one has its own comment there explaining what it's for and why. In
short, at minimum you need:

- `OPENAI_API_KEY` / `OPENAI_MODEL` — the conversational LLM. See
  TROUBLESHOOTING.md's "Conversational LLM model choice" before changing
  the model — this isn't an arbitrary pick, it fixes real reliability bugs
  in older models.
- `SARVAM_API_KEY` — speech-to-text (STT only now; see "TTS & STT" below).
- `RUMIK_API_KEY` — text-to-speech.
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` / `GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY` /
  `GOOGLE_SHEET_ID` — used directly by the live-call tools and by `/admin`.
  The spreadsheet needs three tabs, each with just its header row already in
  place (see "Reservations & logging" below for the exact columns — writes
  silently drop any field not in a tab's header row, so a tab missing a
  column loses that data rather than erroring): `Sheet1` (interaction log),
  `Bookings` (reservations), and `Recordings` (call recordings/transcripts/
  summaries/post-call confidence — optional at first, `/admin` degrades
  gracefully if it doesn't exist yet).
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — gate for `/admin` and `/live`.
  **Rotate off any default before this is reachable on a public URL.**

Everything else in `.env.example` is optional and self-contained: Groq as
an alternate LLM (off by default, see its own comment for why), Twilio SMS
escalation alerts, Google Drive OAuth for call recordings (with Cloudflare
R2 as the parked longer-term backend), Langfuse call tracing, and
`admin_service/`'s own separate per-restaurant dashboard credentials (that's
a different deployment — see "Admin" below).

No Exotel-specific env vars are needed: the call/stream identifiers
(`stream_sid`, `call_sid`) arrive in the WebSocket handshake itself, and
`create_transport()` reads them off that automatically.

## TTS & STT

- **STT: Sarvam**, streaming WebSocket, `codemix` mode (auto-detects and
  switches between English/Hindi/Telugu mid-call) — `app/services/
  resilient_stt.py` wraps it with connect retries.
- **TTS: Rumik** (`mulberry` model), streaming WebSocket — one connection
  minted per call and reused for every turn (`app/services/rumik_tts.py`).
  Chosen over Sarvam TTS primarily on cost (~6x cheaper per character) with
  latency measured close to parity once the socket is warm; see that file's
  own module docstring for the full measured numbers and history — TTS has
  moved between OpenAI, Sarvam, and Rumik more than once as real latency/
  cost data came in, so check that docstring rather than assuming this is
  settled forever. No cross-provider fallback on a TTS failure — that
  utterance (or the call) just ends; see the same docstring for why a
  fallback doesn't fit either provider's persistent-connection design.
- `/admin/health` (see "Admin" below) includes a live check that actually
  exercises both providers with a real tiny synthesis/transcription, not
  just a key-presence check — the fastest way to confirm a deployed
  environment's credentials actually work.

## Admin

`/admin` (HTTP Basic auth, same credentials as `/live`) shows one row per
call — joining `Sheet1`, `Bookings`, and `Recordings` by `CallSessionId`
(see `app/admin/sheets_reader.py`), not one row per logged interaction.
There's no local database in this project, so this reads the Google Sheet
live via the Sheets API on every request. It's registered on Pipecat's own
dev-runner FastAPI app (`pipecat.runner.run` exports `app` specifically so
other modules can add routes before calling `main()`), so it's the same
process and same URL as the voice agent itself.

**`/admin/health`** (also admin-gated) runs live functional checks — not
just "is a key present" — against every provider this pipeline depends on:
a real tiny LLM completion, a real tool-call probe (catches the specific
failure mode where a model silently narrates a fake tool call as text
instead of calling it — see TROUBLESHOOTING.md), a real STT transcription,
a real synthesis from both TTS providers, and the live Drive-recording
credential path. Every check makes a real, billed API call — check this
first before guessing at a production issue.

**`admin_service/`** is a separate, standalone multi-tenant dashboard app
(own `Dockerfile`, own deployment, port 8081) — per-restaurant dashboards
plus a superadmin view, config-driven (`admin_service/config.yaml`) rather
than hardcoded, so adding a client's dashboard doesn't need a code change.
Deploys and scales independently of the voice agent above; see
`.env.example`'s own section for its (separate) credentials.

A background loop (`app/pipeline/idle_post_processor.py`, on by default —
`IDLE_POST_PROCESSING_ENABLED`) picks up finished calls once the bot goes
idle and backfills a `PostConfidence`/`Escalated` verdict onto the
`Recordings` sheet via a forced-tool-call OpenAI classification pass over
the transcript — both admin dashboards surface this (red "Follow-up
needed" outcomes, confidence and escalation columns/filters), falling back
to the live self-reported (noisier, mid-call) values until it's run.

## Reservations & logging

Three tools, all calling Google Sheets directly (`app/services/sheets_client.py`)
from this same process — never through n8n, since a live in-call webhook to
n8n risks dead air if Railway's free tier cold-starts it mid-call:

- `logInteraction` (`app/tools/log_interaction.py`) — appends a row to the
  `Sheet1` tab: `Timestamp, CallDate, CallSessionId, CallerName,
  CallerPhone, Topic, Resolved, Details, GuestsCount, Drift,
  CallConfidence`. Not called live by the model anymore — see below.
  Also texts `Restaurant.owner_phone` (per-client, `app/config/
  restaurants/*.py`) the moment a topic logs `resolved=false`, if
  `TWILIO_SMS_FROM_NUMBER` is configured — real-time human-escalation
  alerting, independent of whichever provider carries the call.
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
  CallerPhone, DurationSecs, RecordingURL, Transcript, Summary` (plus
  `PostConfidence`/`Escalated`/`PostProcessedAt`, backfilled later — see
  "Admin" above).

`logInteraction` is **not** a live tool the model calls mid-turn anymore —
a real trace showed the model splitting every turn into a silent
tool-call-only round followed by a separate spoken-reply round, doubling
LLM round-trips per turn. `app/pipeline/logging_enforcer.py`'s
`LogInteractionEnforcer` backfills it in the background instead, off the
call's critical path, tracking `log_interaction` specifically across a
transaction's silent tool-call rounds (`check_availability`/`book_table`/
`end_call` each land in their own round first) so it doesn't fire a
redundant classification call once the real one has already landed.

The system prompt (`app/pipeline/prompts.py`) hard-gates the reservation
flow: the model is told never to speak a reservation confirmation without a
`booked: true` result from `book_table` in the same conversation. It's also
given the current date/time (in the restaurant's timezone) so it can
resolve relative dates like "tomorrow" — explicitly instructed to use that
*only* for date resolution, never to judge whether a requested time is "too
late" relative to when the call is happening. A call at 3 AM asking for a
table at 9 PM that same day is normal; the only thing that decides
bookability is whether the time falls inside the posted operating hours.

`google-api-python-client` is a blocking client, not asyncio-native — every
Sheets call from these tools goes through `asyncio.to_thread(...)` so it
can't stall audio on the live call while it's in flight.

## Run

Hitting a real failure — deploy crash-loop, bot apologizing and hanging up,
wrong restaurant's facts, missing admin columns — check
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) first; it also documents what the
`CALL_HEALTH*` log lines mean, and is kept current with real production
incidents (with commit hashes), not just hypotheticals.

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

**`/live` is a browser WebRTC test client** (HTTP Basic auth, same
credentials as `/admin`) — open it, click Connect, and talk to this exact
bot from your mic without placing a real (billed) Exotel/Vobiz call. It
talks directly to pipecat's `POST /api/offer`, not the standard prebuilt
client flow — see `app/live_client.py` and TROUBLESHOOTING.md's "Testing
without a real (billed) phone call" for why. Requires the `webrtc` extra
(`aiortc`, already in `requirements.txt`). Otherwise, testing this pipeline
means placing (or forwarding) a real call through Exotel/Twilio/Vobiz, or
driving `/ws` directly with a script that speaks the provider's own Media
Streams JSON protocol.

**Provider is swappable, not hardcoded to Exotel.** `transport_params` in
`app/main.py` registers exotel/twilio/telnyx/plivo identically — pipecat
auto-detects whichever one actually connects and picks the matching
serializer, so no code changes are needed to switch between those four.
Vobiz needs one extra step since it auto-detects on the wire as "plivo"
but needs different call-ending behavior (`_create_vobiz_transport` in
`app/main.py` intercepts it before `create_transport()` builds a real
Plivo serializer). Exotel is the production target, but it needs TRAI DLT
lead time before it's fully live; `-t twilio` (or `telnyx`/`plivo`) plus a
free-trial/pay-as-you-go number is a drop-in stand-in for testing/dev in
the meantime — just note that, unlike Exotel, those connect via an XML
webhook (`POST /`) rather than a raw WebSocket URL, so also pass
`--proxy <your-ngrok-or-railway-host>` when using one of them. In the
Docker image this is controlled by the `TELEPHONY_TRANSPORT` env var
(defaults to `exotel`) instead of `-t` directly — see `Dockerfile`.

## Eval suite

`eval_scenarios/manifest.yaml` lists 29 behavioral scenarios (text and
audio) run against a fresh `app/main.py -t eval` bot per scenario, judged by
an ensemble (`eval_scenarios/services.py`'s `ensemble_judge_llm` — a
gpt-4o-mini primary with a gpt-5-mini second opinion before trusting a
"no" verdict; a single judge model flip-flopped on identical input across
repeat runs, see that file for the specifics). This costs real OpenAI/
Sarvam API calls and writes real rows to the configured Google Sheet —
every scenario's caller name/phone is prefixed `ZZ-EVALTEST` so they're
easy to find and delete afterward.

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

- A handful of scenarios hardcode an absolute reservation date, which
  necessarily rots as real time passes it by — the eval YAML format has no
  templating for "tomorrow." If a scenario fails on a date/past-date
  complaint, check today's date against the hardcoded one before assuming
  a regression; hand-bump the date in that scenario's own YAML comment.
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
- Run-to-run judge noise is real and separate from the above — rerun a
  lone failure before treating a single red scenario as a regression; a
  reproducible failure across 3+ identical runs is the actual signal.

## Explicitly out of scope for this phase (flagged, not built)

- Sarvam STT accuracy hasn't been separately re-validated against Exotel's
  8kHz phone audio (vs. the higher sample rates STT is usually tuned on) —
  worth confirming under real call conditions, not just assumed at parity.
- **No seat/order cap.** `check_availability` validates operating hours only
  — every in-hours slot reports available, on purpose, per current scope.
- **No phone-order-taking flow.** The prompt/tools only handle reservations;
  `book_table`/`Bookings` don't cover takeout/delivery orders.
- **No missed-call → outbound-callback detection**, and outbound calling
  shouldn't be enabled at all until TRAI DLT registration is complete.
- **No rebuilt end-of-day digest.** n8n's `2a`-`2e` Summary Branch still
  runs at 9 PM (not ~10 PM), builds its summary with hand-rolled string
  logic rather than an LLM call, and tries to place an outbound call via
  Vapi's API — a platform this project no longer uses. Needs a rebuild once
  the delivery channel (WhatsApp/SMS/email) is decided. (Real-time,
  per-call escalation alerting — as opposed to this end-of-day digest — is
  built; see "Reservations & logging" above.)
- **No verbatim-transcript-driven outcome taxonomy beyond confidence/
  escalation.** `logInteraction` logs a short topic summary, not the full
  transcript, for the live per-interaction log; full-call transcripts are
  captured separately in `Recordings` (see above) and idle post-processing
  now derives a confidence/escalation verdict from them, but there's no
  finer-grained FAQ/booking/order/missed-call categorization yet.
- Full-call audio recording lands in Google Drive via OAuth as an actual
  Google account (`app/services/drive_oauth_client.py`) — the service
  account has had zero Drive storage quota since 2021, confirmed live via
  `storageQuotaExceeded` on an actual upload attempt. That OAuth refresh
  token expires ~7 days unless the OAuth consent screen is published to
  production in Google Cloud Console — see TROUBLESHOOTING.md if
  recordings silently stop appearing. `app/services/r2_client.py`
  (Cloudflare R2) is the intended longer-term backend once R2 is actually
  activated on the Cloudflare account — see `.env.example` for both setups.

## A note on Pipecat API stability

This is a fast-moving library. Every import path and constructor signature
in this codebase was checked against the actual `pipecat-ai` source on
GitHub (`main` branch) at build time, not from memory or docs prose — but if
`pip install` pulls a version where something's shifted, the fastest way to
resync is the telephony examples under `examples/` in the
[pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat) repo (search for
`ExotelFrameSerializer` / `FastAPIWebsocketTransport`), which are
structurally closest to this app (OpenAI LLM + TTS, function calling,
WebSocket telephony transport).
