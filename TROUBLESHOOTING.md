# Troubleshooting

Real failures this project has actually hit, with the real cause and the real
fix — plus a reference for the CALL_HEALTH logging added to handle provider
outages mid-call. Ordered roughly by how likely you are to hit each one.

---

## Conversational LLM model choice (OPENAI_MODEL)

**Cause for the switch away from gpt-5-mini.** Investigating turn latency
(see the next section) led to directly benchmarking gpt-5-mini against
newer OpenAI models on this account (gpt-4.1-nano, gpt-5-nano, gpt-5.4-nano,
gpt-5.4-mini) using this app's actual system prompt and tool schemas, not a
synthetic prompt. Two independent findings, both reproducible across 6 
repeat trials each:

- **Relative-date resolution.** Asked "what's the exact date this Saturday"
  with the real current-date instruction in the prompt (today =
  Fri 2026-09-18, so "this Saturday" = 2026-09-19): gpt-5-mini answered
  wrong (picked the *following* Saturday, 2026-09-24) 4 times out of 5.
  gpt-5.4-nano got it right 5/5, then 6/6 on a second pass.
  check_availability/book_table trust whatever date the model resolves —
  the only thing that ever caught this was the read-back-and-confirm
  requirement in `_RESERVATION_TOOL_INSTRUCTION`, which only helps if the
  caller notices the wrong date being read back to them and objects.
- **logInteraction reliability.** Asked a plain one-sentence factual
  question ("what time do you close tonight") with the real tools
  attached: gpt-5-mini answered the question correctly but called the
  actual `log_interaction` tool 0 times out of 6 — every time, it wrote a
  fake `logInteraction({...})` call into its spoken reply as literal text
  instead (silently non-fatal in production only because
  `LogInteractionEnforcer`'s background backfill catches the miss — see
  `app/pipeline/logging_enforcer.py`). This is pre-existing, not caused by
  any prompt trimming done alongside this — confirmed by running the exact
  same test against the original, untouched prompt from before that
  session's edits, which also scored 0/6. gpt-5.4-nano scored 6/6 on the
  identical test.
- **Speed.** Time-to-first-token, same prompt: gpt-5-mini ~1.33s avg,
  gpt-5.4-nano ~1.15s avg — comparable, gpt-5.4-nano if anything faster.
  gpt-5-nano (the non-".4" nano) is a reasoning model that burns several
  seconds on hidden reasoning tokens before answering — ~10s+ TTFT,
  disqualifying for a live call.

**Check.** `OPENAI_MODEL` in `.env`/`.env.example`; `app/config/settings.py`
also defaults to `gpt-5.4-nano` if the env var is ever unset.

**Fix / if this needs revisiting.** OpenAI's available model lineup moves
fast — re-run the same kind of test (real system prompt + real tools, ask
for an exact relative date, ask a plain factual question with tools
attached and check whether log_interaction actually gets called) against
whatever nano/mini-tier models exist at the time, rather than trusting a
model name to still mean the same thing it did here. Don't pick a model
based on benchmarks/reputation alone — this specific date-math failure
would never show up in a generic "is this model good" test, only in one
built around what this app actually asks the model to do.

**2026-09-19 re-test: switched to gpt-5.6-luna.** OpenAI's lineup had moved
on again (gpt-5.6-sol/terra/luna now exist above the 5.4 series). Re-ran
the exact same two tests, 6 trials each, against gpt-5.4-nano (then-current)
and candidates found via web research (gpt-5-nano, gpt-5.6-luna):

- **gpt-5-nano** (web research suggested it as "cheaper, same nano tier"):
  6/6 correct on both tests, but 10-29s latency (median ~13.3s) — it's a
  reasoning model that burns time on hidden reasoning tokens before
  answering, same disqualifying issue as gpt-5-nano's earlier appearance in
  the very first round of this investigation. Web pricing tables don't
  surface this; only running the actual test does. Rejected.
- **gpt-5.6-luna** (web research suggested it as "same price as
  gpt-5.4-nano, newer generation"): 400s on `/v1/chat/completions` with
  function tools attached unless `reasoning_effort="none"` is explicitly
  set (confirmed by testing every documented value — only `"none"` works
  with tools on this endpoint; `"minimal"` isn't a valid value for this
  model, `"low"` hits the same 400). Once set: 6/6 correct on the
  date-resolution test, 6/6 on the log_interaction reliability test,
  ~1.18s median latency (statistically the same as gpt-5.4-nano's), and
  marginally cheaper output tokens ($1.20 vs $1.25/1M; input price
  unchanged at $0.20/1M).

**Switched** `OPENAI_MODEL` to `gpt-5.6-luna` in `.env`/`.env.example`/
`app/config/settings.py`'s default, and added
`extra={"reasoning_effort": "none"}` to `OpenAILLMService.Settings` in
`app/main.py`'s `_build_llm` (pipecat's documented passthrough for
provider-specific params — see `OpenAILLMService.Settings.extra`). Honest
caveat: this is a marginal win (same latency, ~4% cheaper output, one
extra untested-in-production param), not a breakthrough, validated with 6
trials on a brand-new model family. If OPENAI_MODEL is ever reverted to a
non-reasoning model, `reasoning_effort="none"` is harmless there too
(confirmed against gpt-5.4-nano) — but re-check that assumption if
pointing this at some other future model.

**Two more call sites broke on the switch, found live, not by testing
ahead of time** — every direct `chat.completions.create` call using
`settings.openai_model` needs checking individually, `main.py`'s
`_build_llm` fix does not cover them:

- `app/pipeline/logging_enforcer.py`'s `_backfill_log_interaction` (the
  background log-classification call) also attaches `tools` and hit the
  identical 400 — needed its own `reasoning_effort="none"`, added
  directly on that `create()` call.
- `app/pipeline/transcript.py`'s `generate_call_summary` used the legacy
  `max_tokens=120` param, which gpt-5.6-luna rejects outright
  ("Unsupported parameter: 'max_tokens' ... Use 'max_completion_tokens'
  instead"). Fixed to `max_completion_tokens=120` — confirmed working
  against both gpt-5.6-luna and gpt-5.4-nano, so this is safe either way
  regardless of which model `OPENAI_MODEL` points at.

Both were caught from real error tracebacks in a live `/live` test run,
not by testing ahead of time — a reminder that swapping `OPENAI_MODEL`
means grepping for every `chat.completions.create` call using it, not
just the one in `_build_llm`.

---

## `/live`: "Data channel not established within 10s" warning

**Cause.** `app/live_client.py`'s hand-written JS client never called
`pc.createDataChannel(...)`. Pipecat's `SmallWebRTCTransport` passively
waits for the *client* to open a data channel (see
`connection.py`'s `@self._pc.on("datachannel")` — a channel created by
either peer fires this on the other side) and after 10s with none, logs
this warning and disables message queueing for that channel. Confirmed
via two independent from-scratch test runs (a separate server instance on
a spare port, never sharing state with a real dev session) that this is
**100% reproducible, not a one-off** — it fired both times.

**Does it matter?** No — confirmed via real end-to-end test calls (fake
mic audio, real Sarvam STT/TTS, real OpenAI LLM) that the actual
conversation works fine despite it: the data channel is a separate
sub-system from the audio media track, and this client only ever needed
the audio track. The data channel is used for pipecat app-messages this
minimal test client doesn't send anyway.

**Separately:** the "Timeout: No audio frame received" warnings that
sometimes follow are a *different* mechanism entirely (`transport.py`'s
`read_audio_frame`, a plain 2s-per-frame recv timeout, unrelated to the
data channel) — in testing these tracked exactly with a finite
pre-recorded fake-mic WAV file running out partway through the call, not
a real problem. A live human caller's mic doesn't stop producing frames
the way a fixed test clip does.

**Fix.** Added `pc.createDataChannel('events')` in `live_client.py` right
after creating the `RTCPeerConnection`. Confirmed fixed: re-ran the same
from-scratch test setup, warning no longer appears.

**Scope — this cannot affect real telephony calls.** `SmallWebRTCTransport`
(aiortc, ICE/data-channel semantics) is only used by `/live`. Real calls
go through `FastAPIWebsocketTransport` (Exotel/Twilio/Vobiz), a plain
WebSocket with no ICE negotiation or data channel concept at all.

---

## Every turn feels slow (~10s+ between caller and bot)

**Cause.** Found via `/live` testing: `LogInteractionEnforcer` used to force a
second, synchronous LLM completion (a "developer" nudge + a fresh
`LLMRunFrame`) through the *same* `OpenAILLMService` instance whenever a
reply didn't call `logInteraction` itself — which, in practice, was nearly
every reply (the prompt-only instruction to call it inline wasn't reliable
enough on its own). Pipecat's LLM service processes `LLMContextFrame`s
strictly in arrival order, one at a time — so that nudge completion sat in
the *same queue* the caller's next real turn needed. If the caller finished
speaking while the nudge was still in flight, their actual question waited
behind pure bookkeeping. Two full LLM round-trips back to back, on almost
every single turn.

**Fix (already applied).** `logInteraction` only feeds `/admin` — nothing in
the live call depends on it landing before the next turn starts. A missed
`logInteraction` call is now backfilled by a separate, independent OpenAI
call (forced via `tool_choice` to call `log_interaction`, given the
conversation so far) fired as a background task and never awaited — see
`LogInteractionEnforcer._backfill_log_interaction` in
`app/pipeline/logging_enforcer.py`. The caller's next turn is never queued
behind it, and *every* topic still gets logged (not a sampled subset) — the
fix was decoupling logging from the conversational queue, not logging less.

The one case that still forces a synchronous nudge: a reply that already
sounds like a goodbye (`_sounds_like_ending`) without `end_call` having been
called. That stays foreground/blocking on purpose — a call that should end
but doesn't is worse than a log row landing a couple seconds late, and it's
rare enough not to matter for overall pacing.

**Check.** Logs should now show `LogInteractionEnforcer: no tool call this
turn — backfilling logInteraction in the background` immediately followed by
the *next* turn's activity, with `LogInteractionEnforcer: background-logged
(ok=True) topic=...` landing a couple seconds later, out of order relative to
the conversation. If you instead see a real synchronous gap before the next
turn starts, check whether that reply sounded like a goodbye — that's the one
remaining case that still blocks.

**Separately, still real but unrelated to this:** the *original* Rumik
integration didn't stream — `app/services/rumik_tts.py` called Rumik's
one-shot `POST /v1/tts` endpoint, which waits for the full utterance to
synthesize before any audio plays. For longer replies that's a second or
more of avoidable silence before the caller hears anything, on top of the
above. (Update 2026-09-18: TTS moved to Sarvam bulbul:v3, which does stream
— see `_build_tts` in app/main.py. Update 2026-09-22: turns out this
wasn't a Rumik limitation, just an implementation gap — Rumik does have a
streaming WebSocket endpoint (`POST /v1/tts/ws-connect` mints a session,
then the socket delivers raw PCM chunks as they're generated — see
https://docs.rumik.ai/streaming.md). First pass minted a fresh session per
utterance (still slow: ~0.8-1.8s TTFA — cheaper than a full one-shot buffer
but still paying a connect round trip every turn). Found Rumik's own
maintained pipecat package (github.com/ira-rumik/pipecat-rumik) keeps ONE
session open for the whole call instead; `rumik_tts.py` now does the same.
Measured for real (10 utterances through an actual pipecat pipeline, real
RUMIK_API_KEY): turn 1 (pays the one-time mint+handshake) 609ms, turns
2-10 on the reused socket median 313ms — close to Sarvam's own ~260ms.
`_build_tts` in app/main.py now returns `RumikTTSService` again (Sarvam
stays as STT, untouched). NOT yet tested against a real phone call
end-to-end — this was direct-to-API through a minimal test pipeline, not a
live telephony call, so pronunciation/audio quality and real barge-in
behavior still need a real call before fully trusting this. Checked
mulberry-1.6 too as a possible alternative — female voices only, no
Hindi/English code-mixed support, not usable here, stay on plain
`mulberry`.)

**Also checked and ruled out: system prompt size.** Tempting to assume a
smaller prompt speaks faster. Measured directly: a 52-token prompt and the
real ~3,700-token system prompt showed the *same* time-to-first-token
distribution (~0.8-2.3s) against gpt-5-mini/gpt-5.4-nano — OpenAI's
serving latency here isn't dominated by prompt size, especially once
automatic prompt caching kicks in (confirmed via
`usage.prompt_tokens_details.cached_tokens` — ~98% of a repeated system
prompt gets served from cache after the first call, no code needed). Prompt
trimming (see app/pipeline/dynamic_prompt.py and prompts.py) is worth doing
for cost and clarity, not for latency — don't expect it to move TTFA.

---

## Testing without a real (billed) phone call

`/live` (`app/live_client.py`, gated behind the same admin credentials as
`/admin`) is a browser WebRTC test client — connect from a browser and talk
to the exact deployed bot without placing a real Vobiz/Exotel call. It talks
directly to pipecat's `POST /api/offer`, not pipecat's `/start` route or its
prebuilt client UI (`pipecat-ai-prebuilt`) — confirmed live: `/start` refuses
any transport other than the one this process was launched with (`-t exotel`
in production), so a client using the standard `startBotAndConnect()` flow
(what the prebuilt UI and `@pipecat-ai/client-js` both use) gets a 400
`Transport 'webrtc' is not allowed` before the call even begins. `/api/offer`
has no such restriction. If `/live` ever needs replacing with a fancier
client, keep it calling `/api/offer` directly rather than `/start`.

Requires the `webrtc` extra (`aiortc`) — see `requirements.txt`; without it,
`/api/offer` never registers and `/live` will fail after the offer is sent.
STUN-only ICE (no TURN), so it connects fine from a normal home/office
network but may not from a restrictive corporate NAT.

---

## Reading a CALL_HEALTH log line

STT (Sarvam), the LLM (OpenAI), and TTS (Rumik, with an OpenAI TTS fallback)
each retry transient failures on their own before anything gets escalated —
see `app/services/resilient_stt.py`, `app/main.py`'s `retry_on_timeout=True`,
and `app/services/rumik_tts.py`. `app/pipeline/call_health.py`'s
`CallHealthMonitor` is what runs once all of a stage's own retries/fallbacks
are exhausted. Two log shapes to know:

- `CALL_HEALTH stage=<stt|llm|tts> ...` — a single provider attempt (or, for
  the LLM, a whole failed turn) failed. Not necessarily fatal on its own; the
  LLM specifically tolerates one failed turn before escalating (see below).
- `CALL_HEALTH_DEVELOPER_ALERT call_session_id=... caller=... stage=...
  speak_apology=<bool> detail=... — ending call` — logged at **CRITICAL**,
  always. This is the line to alert on. It means the call actually ended
  early because a stage ran out of options:
  - `stage=stt`: Sarvam failed to connect 3 times in a row at call start (see
    `_MAX_CONNECT_ATTEMPTS` in `resilient_stt.py`). `speak_apology` is always
    `true` here — TTS is presumed fine, so the bot says a short apology
    ("we're having a technical issue... please try calling back") and hangs
    up instead of sitting in silence for the whole call.
  - `stage=llm`: OpenAI failed **2 separate turns** in the same call (each one
    already survived the OpenAI SDK's own retries plus pipecat's
    `retry_on_timeout`). One failed turn alone doesn't end the call — the
    caller just gets silence for that one turn and the conversation
    continues; a second failed turn means this isn't a blip. `speak_apology`
    is always `true`.
  - `stage=tts`: Rumik *and* the OpenAI TTS fallback both failed for the same
    utterance (four total attempts — see `_MAX_ATTEMPTS_PER_PROVIDER` in
    `rumik_tts.py`). `speak_apology` is always `false` here — there is no
    third TTS vendor, so if the bot genuinely cannot synthesize speech there
    is nothing left to say. This is the one failure mode where the caller
    gets **no signal at all**, just a dropped call. If you need this to page
    someone, alert on `speak_apology=False` specifically, since a caller
    would never think to report "the call just went dead."

Every `CALL_HEALTH*` line also includes `call_session_id` and `caller` so you
can cross-reference `/admin` or the Recordings sheet for that call.

**What this does NOT do**: swap Sarvam for OpenAI STT mid-call. Sarvam is a
persistent WebSocket stream; OpenAI's STT is request-per-utterance batch
transcription. Splicing one into an already-linked pipecat pipeline stage
without a live test call risks a subtly broken swap (dropped audio,
duplicated VAD frames) that's worse than apologizing and ending the call — see
the docstring in `app/services/resilient_stt.py`. If you build this, it needs
verification against a real call, not just a code review.

---

## Switching STT/TTS vendor per restaurant (2026-09-23)

`Restaurant.stt_provider` / `.tts_provider` (`app/config/restaurants/*.py`)
are the one place to change either, per client — see
`app/services/stt_factory.py` / `tts_factory.py`. Both raise `ValueError` at
call-setup time on an unrecognized value rather than falling back silently.

**Rumik has no STT product** — checked docs.rumik.ai and its llms.txt index
directly (2026-09-23): TTS only ("Silk" API), no ASR/transcription endpoint
anywhere in their docs. `STT_PROVIDERS` in `stt_factory.py` is Sarvam and
Deepgram only for this reason, not an oversight.

**Deepgram config**: `nova-3` with `language="multi"` — Deepgram's own
multilingual code-switching mode (docs.deepgram.com/docs/
multilingual-code-switching), one of ~10 languages including Hindi. Verified
for real (2026-09-23): fed the same Hindi/English code-mixed audio through
both Sarvam (codemix mode) and Deepgram (nova-3/multi) via
`pipecat.tests.utils.run_test` driving the actual factory-built services —
Sarvam transliterates everything into Devanagari (including the English
words, phonetically); Deepgram actually switches script mid-transcript
(`"मैं table book करना चाहता हूं Saturday को सात बजे के लिए"`), which is a
different but equally usable shape for downstream code that just needs the
text. `"multi"` isn't a `pipecat.transcriptions.language.Language` enum
member — passed as the raw string `STTSettings.language` already accepts.

**Gotcha hit while testing** (not a factory bug, but will bite anyone
writing a similar throwaway script): `DeepgramSTTService._connect()` only
launches the WebSocket handshake as a background task and returns
immediately — `StartFrame` does NOT wait for the socket to actually open.
Audio sent in the first ~1-1.5s after pipeline start gets silently dropped
(`run_stt`: `if self._connection: send_media(...)` — no error, no frame,
just gone). Real calls never hit this (caller audio doesn't start within
~1.5s of pipeline start); a burst test that queues all its audio frames
immediately after `StartFrame` does. Sleep past the connect before sending
audio in any standalone test.

**TTS**: `RumikTTSService` (existing), `SarvamTTSService`
(`bulbul:v3`/`EN_IN`), and pipecat's `OpenAITTSService`
(`gpt-4o-mini-tts`) — all three read Hindi/English code-mixed reply text
straight from the script. `TTSLanguageSwitcher` (`app/pipeline/
tts_language_switcher.py`) stays wired regardless of provider; it's a no-op
for Rumik and OpenAI (neither reads `Settings.language` at synthesis time)
and does real work for Sarvam (`target_language_code`). Same "no
retry/fallback, a TTS failure just ends the call" posture as before,
now covering whichever provider is active — `on_pipeline_error` in
`app/main.py` checks `frame.processor is tts` (identity, not
`isinstance` against one vendor's class) so this stays true across the
switch.

---

## Deploy OOM-killed under real call traffic (2026-09-30)

**Cause.** `AudioBufferProcessor(auto_start_recording=True)` (`app/main.py`)
defaulted to `buffer_size=0`, which means it holds a call's **entire** raw
PCM audio (both user and bot tracks) in memory for the whole call and only
flushes once, at call end. `app/pipeline/recording.py` then built a second
full in-memory WAV copy (`io.BytesIO`), and both upload backends
(`drive_oauth_client.py`, `r2_client.py`) took that full byte blob into
memory a third time. Confirmed live: Railway's Memory graph showed a
sawtooth climbing to 800MB+ per call cycle, and the actual deploy logs for
the crashed container ended in a bare `Killed` line right before the
process was replaced — a container-level OOM kill, not an application
exception. Real caller impact: calls failing mid-conversation (reported via
Plivo).

**Check.** Railway → service → Metrics → Memory, "Last day" view — a
climbing sawtooth pattern that resets on each restart, not a flat baseline,
is the signature. In deploy logs, a `Killed` line with no preceding
traceback (the OS killing the process, not the app crashing on its own)
confirms it.

**Fix.** `app/pipeline/recording.py`'s `CallRecorder` now streams flushed
audio chunks straight to a temp `.wav` file on disk (`wave.Wave_write`,
incremental `writeframes`) instead of accumulating the whole call in a
Python `bytes` object. `AudioBufferProcessor` is now given
`buffer_size=_RECORDING_FLUSH_BYTES` (320,000 bytes — roughly 10-20s of
telephony audio) in `app/main.py` so it actually flushes periodically
instead of once at call end. Both upload backends now take a file path and
stream from disk (`MediaFileUpload` for Drive, boto3's `upload_file` for
R2) instead of a full in-memory blob.

**Gotcha hit while testing** (not obvious from reading pipecat's source
without a live call): `_call_event_handler` in
`pipecat/utils/base_object.py` does **not** await registered event handlers
— unless registered with `sync=True`, each handler call is wrapped in
`asyncio.create_task(...)` and never awaited by the caller. `on_audio_data`
and the new `on_recording_stopped` handler are therefore independent,
unordered background tasks, not a guaranteed sequence — `finalize()`
(triggered by `on_recording_stopped`) could close the wave file while a
still-in-flight `on_audio_data` chunk write was mid-write on a different
thread (`asyncio.to_thread`). Caught live via a real crash: `AudioBuffer
Processor#0: uncaught exception in event handler 'on_audio_data'
(...wave.py:571): 'NoneType' object has no attribute 'write'`, surfaced
while eval-testing scenario 17 against the Deepgram TTS switch below — not
a Deepgram-specific bug, just the first time enough concurrent chunk
traffic hit it. Fixed with an `asyncio.Lock` in `CallRecorder` shared
between `add_chunk` and `finalize`, plus a `_closed` flag so a chunk that
somehow arrives after finalize is dropped (logged) instead of reopening a
file finalize already uploaded and deleted.

---

## Same OOM kill, still happening after the fix above — real root cause was sheets_client (2026-09-30)

**The recording fix above was real but incomplete.** Production OOM-killed
twice more the same afternoon, after that fix was live, with **zero real
call traffic** in either case (confirmed via `railway get-logs --types
deploy,http`: `http: []` for the deployment's entire ~11-minute life, only
`idle_post_processor` activity in the deploy stream). Whatever was leaking
had nothing to do with calls or recordings at all.

**First two patches, both real but not the actual cause.** Chased two
unclosed-`AsyncOpenAI`-client leaks in sequence — `idle_post_processor.py`
(one leaked client per stuck call, every 60s tick, forever — see this
module's own docstring on why the same 5 calls keep retrying: the
Recordings sheet is missing `PostConfidence`/`Escalated`/`PostProcessedAt`)
and then `logging_enforcer.py`/`transcript.py`/`admin/routes.py` (one
leaked client per real call or health-check). Both fixes were correct and
worth keeping, but production still OOM-killed a third time after both
landed — proof they weren't the dominant leak. **Lesson: "found an unclosed
client, fixed it, memory looked stable for N minutes" is not confirmation
— Python's allocator plateaus after warm-up regardless of whether the real
leak is fixed, and this exact pattern (looks stable, then climbs again over
the next hour) repeated three times before the actual cause was found.**

**Root-caused by reproducing the exact deployed code locally**, not by more
code reading: `import app.pipeline.idle_post_processor as ipp`, then
`gc.disable()` + `gc.set_debug(gc.DEBUG_SAVEALL)` before calling
`await ipp.run_idle_post_processing_once()` once. One tick (6 calls into
`sheets_client` — one `read_rows` + up to 5 `update_cells`) left **~10,494
unreachable objects** needing cyclic GC to reclaim: `Resource`,
`ResourceMethodParameters`, `Schemas`, `Http`, `AuthorizedHttp`,
`Credentials`, `SSLSocket`, `HTTPResponse` — all `googleapiclient`/
`httplib2` internals. `sheets_client._client()` called
`googleapiclient.discovery.build()` fresh on **every single call**. A built
`Resource` is a dynamically-constructed web of bound methods that reference
the `Resource` itself back — a deliberate, if GC-unfriendly, part of how
`google-api-python-client` works. Simple refcounting can't free a
self-referential cycle; only `gc.collect()` can, and it only runs
automatically once an allocation-count threshold is crossed. Confirmed the
mechanism directly: the identical first tick left **68,618 net new
objects** when GC ran normally vs. only ~35MB/~108MB RSS when a
`gc.collect()` was forced immediately after — production's low, sparse
call volume (one tick every 60s, a handful of calls each) apparently
doesn't cross that automatic threshold fast enough to keep up, so the
cyclic garbage from `sheets_client` just accumulated tick after tick until
the container hit its memory limit.

**Why this wasn't caught by an earlier, naive test of `sheets_client`
itself**: an initial local repro called `_client()` + `read_rows()` in a
loop with `gc.collect()` forced after every iteration — which is exactly
what masked the leak (forcing collection every time reclaims the cycles
immediately, so nothing appears to accumulate). Only removing the forced
collection and letting Python's normal, threshold-based automatic GC
schedule apply reproduced the real behavior. If re-investigating a memory
issue like this again: reproduce with the *actual* GC behavior the
process will really run under, not a forced-clean-after-every-step
version of the same code — a test that manually cleans up after itself is
not testing what production does.

**Fix.** `sheets_client._client()` now caches the built `Resource` in a
`threading.local()` instead of rebuilding it every call. The seemingly
obvious fix — just cache it as one shared module-level object — was
deliberately avoided years earlier per this file's own comment: a built
`Resource` wraps one `httplib2.Http` connection, and sharing that across
concurrent threads caused real SSL errors before. `threading.local()` gets
both: every call from the same thread reuses one `Resource` (no more
rebuild-every-call cycle churn), while different threads still never share
a connection (`sheets_client`'s callers mix direct synchronous calls —
`idle_post_processor.py` always calls from the event loop thread — with
`asyncio.to_thread` callers drawn from a bounded, reused thread pool, so
this stays a small, fixed number of cached `Resource` objects either way,
never one-per-call and never one-shared-across-everything).

**Verified empirically, before and after, against the real code path**
(not just "looks stable for a few minutes" — see the lesson above):
steady-state per-tick object churn (`gc.collect()`'s reclaimed count,
measured with `gc.disable()` so nothing gets cleaned up automatically
mid-measurement) dropped from 68,618 to a flat, non-growing 4,173 objects
per tick — a 94% reduction, and confirmed the cached `Resource`'s `id()`
stays identical across repeated ticks (proving reuse, not just a smaller
leak). The remaining ~4,150/tick is normal OpenAI/httpx per-call object
lifecycle (already properly closed via the earlier `async with` fixes),
bounded and non-accumulating.

**Underlying issue still worth fixing separately**: `idle_post_processor.py`
calls `sheets_client.read_rows`/`update_cells` directly, not via
`asyncio.to_thread` like every other caller in this codebase (see this
module's docstring: "every public function here must be awaited via
`asyncio.to_thread(...)`") — meaning these blocking network calls run
directly on the event loop thread during every idle tick, which could
stall real call audio processing if a real call happened to be starting at
the exact moment a tick fires. Not yet fixed (out of scope for the memory
investigation), and gated in practice by `active_calls.is_idle()` only
firing once genuinely idle, but worth closing since it's a footgun for
whoever adds a shorter `_MIN_IDLE_SECS` later.

**Update, same day: the feature itself was removed.** Once the leak was
actually understood, the whole point of this investigation evaporated —
`idle_post_processor.py` and `active_calls.py` were deleted outright
(along with their wiring in `app/main.py` and the
`IDLE_POST_PROCESSING_ENABLED` setting) rather than kept around now-fixed.
The real-time per-call logging (`logging_enforcer.py`) already covers what
actually matters; the idle-triggered whole-transcript backfill verdict was
a nice-to-have that stopped being worth the complexity it turned out to
carry. `app/admin/sheets_reader.py` and `admin_service/sheets_reader.py`
still read `PostConfidence`/`Escalated`/`PostProcessedAt` if a row happens
to have them (harmless dead code — nothing writes those columns anymore,
so this always falls back to the live self-reported values). The
`sheets_client.py` thread-local caching fix stays regardless — it's a
real, general fix independent of what removed feature exposed it.

---

## Rumik TTS wallet ran dry mid-call → switched default TTS to Deepgram Flux (2026-09-30)

**Cause.** Rumik TTS (the default `tts_provider` for both restaurants)
started failing with `Rumik TTS error: {'error': True, 'code':
'INSUFFICIENT_BALANCE', 'message': 'Insufficient wallet balance'}` —
confirmed live in deploy logs and reproduced running the eval suite (8 of
29 scenarios failed with this exact error once the wallet ran out partway
through the run). There is no retry/fallback wrapper for TTS (see "Call
ends abruptly with no apology" above) — an insufficient-balance error ends
the call outright, same as any other TTS failure.

**Fix.** Switched both restaurants' `tts_provider` to `"deepgram"` in
`app/config/restaurants/{zero40,spice_route_kitchen}.py`. Deepgram's
stable Aura-2 voice line has **no Indian English (en-IN) voices** — only
American, British, Australian, Irish, and Filipino accents
(developers.deepgram.com/docs/tts-models, checked directly). Their
early-access **Flux** line does: `flux-naveen-en` ("Naveen"), an
Indian-accented male voice (developers.deepgram.com/docs/flux-tts/voices).
Wired via `pipecat.services.deepgram.flux.tts.DeepgramFluxTTSService` — a
separate class/protocol from the stable `DeepgramTTSService` (Aura-2,
`wss://api.deepgram.com/v1/speak`) already used nowhere in this repo. Flux
uses `wss://api.deepgram.com/v2/speak` and has no persona/description knob
like Rumik/OpenAI's `_VOICE_PERSONA` — the fixed voice is the whole steer.

**Flux is early access** per pipecat's own `DeepgramFluxTTSService`
docstring — "the voice catalog and parts of the protocol may change before
general availability." If `flux-naveen-en` ever 404s or behaves
differently, check Deepgram's current Flux voice list before assuming a
code regression.

**Check.** `DEEPGRAM_API_KEY` must be set on whichever Railway service runs
the bot — it was **not** set at all on `voice_pipecat` in production when
this switch was made (only used for STT before this, and no restaurant had
`stt_provider="deepgram"` either, so it had never actually been exercised
live). Deploying this TTS switch without adding the key first would have
broken every call immediately on TTS auth — worse than the Rumik issue it
was meant to fix. `app/services/tts_factory.py`'s `describe_tts` /
`_TTS_LABELS` surface which TTS actually ran on a given call via the
`/admin` dashboard's provider-details dialog — check there before assuming
which vendor is live for a given restaurant.

**Verified for real** (not just code review): `eval_scenarios/manifest.yaml`
scenarios 16 (`audio_english_stays_english`) and 17 (`audio_hindi_switch`)
both pass against `flux-naveen-en`, run twice — once isolated, once after
merging in unrelated upstream commits. Not yet verified: real telephony
audio quality (8kHz, real caller mic) — the eval suite's audio scenarios
run through pipecat's synthetic test harness, not an actual phone call.
Place one real test call before fully trusting pronunciation/barge-in
behavior on this voice.

---

## Deploy crash-loops (or 502s) right after a push

**Cause.** Two real incidents, same shape: something worked locally but was
never actually committed/deployed correctly.

- A module `main.py` imports (`app/pipeline/tracing.py`) existed only as an
  untracked file in the working tree — `git status` would have shown it, but
  it was never `git add`ed. The next push shipped `main.py`'s import with
  nothing behind it, crash-looping the deploy (`0b90b2d`).
- A module read `settings.langfuse_public_key` / `langfuse_secret_key` /
  `langfuse_host`, but those fields were only ever added to `Settings` in the
  local working tree, mixed in with unrelated uncommitted work — never
  actually committed. Every deployed request hit `AttributeError: 'Settings'
  object has no attribute 'langfuse_public_key'` at import time (`14cb8fb`).

**Check.** Before pushing anything that touches imports or `Settings`:
```bash
git status                                  # anything untracked that's imported?
git ls-files --others --exclude-standard    # same check, script-friendly
python -c "import app.main"                 # would catch both incidents above locally
```

**Fix.** Commit the missing file / field, or revert the change that
introduced the now-broken import/attribute reference. Once deployed, Railway
logs will show the exact `ModuleNotFoundError` or `AttributeError` — either
one means "something referenced here was never actually pushed."

---

## Bot apologizes and hangs up right after the greeting

**Cause.** STT, LLM, or TTS failed at the very start of the call, before any
real conversation happened — almost always a bad/expired/rotated API key, a
provider outage, or an unset env var on this specific deployment (remember
`app/config/settings.py`'s `_restaurant_env` convention: some Drive OAuth
vars are suffixed `_<RESTAURANT_ID>` per restaurant — a plain, unsuffixed name
in the platform's env vars silently does nothing for a multi-restaurant
setup).

**Check.**
```
grep CALL_HEALTH_DEVELOPER_ALERT <your log source>
```
The `stage=` field says which provider. For `stage=stt`, look for preceding
`STT (Sarvam): connect attempt N/3 failed` warnings and the error they carry.

**Fix.** Rotate/verify the relevant API key (`SARVAM_API_KEY`,
`OPENAI_API_KEY`, `RUMIK_API_KEY`), check the provider's own status page, and
confirm the env var is actually set on *this* deployment — a fresh restaurant
onboarded via `admin_service/` runs as a separate Railway service with its
own environment (see `.env.example`'s `admin_service/` section); the bot
process's own vars don't carry over.

---

## Caller hears silence for one turn, then the bot answers normally on the next

**Cause.** A single transient OpenAI failure that already survived the OpenAI
SDK's own retries and `retry_on_timeout` — rare, but not impossible (a
momentary rate limit or a slow upstream blip). This is deliberately **not**
treated as "the LLM is down": ending the whole call over one recoverable turn
would be worse than letting the caller just repeat themselves.

**Check.** `grep "CALL_HEALTH stage=llm failure 1/2"` in the logs around that
call's timestamp. If you only ever see `1/2` entries that don't repeat within
the same `call_session_id`, this is working as intended, not a bug.

**Fix.** Nothing to fix unless `2/2` (and the resulting
`CALL_HEALTH_DEVELOPER_ALERT`) shows up — that means it happened twice in one
call and is a real outage, not a blip. See the section above.

---

## Call ends abruptly with no apology, no warning, nothing in the transcript

**Cause.** Total TTS failure — Rumik and the OpenAI TTS fallback both failed
for the same utterance. There is no third TTS vendor, so the bot has nothing
left to say and just ends the call. This is the one degradation path with
**zero caller-facing signal** — from the caller's side it just looks like the
call dropped.

**Check.** `grep "stage=tts speak_apology=False"` in
`CALL_HEALTH_DEVELOPER_ALERT` lines. If you're setting up alerting on these
logs at all, alert on this one specifically — a caller will report "the call
just cut off," not "the bot had a technical issue," so you won't hear about
this from them.

**Fix.** Verify `RUMIK_API_KEY` and `OPENAI_API_KEY` are both valid and that
neither provider is down. If this fires more than rarely, it's worth adding a
synthetic/canary call check rather than waiting for a customer complaint.

---

## A fresh restaurant's `/admin` call log is missing columns that other calls have

**Cause.** `sheets_client.append_row` (and `sheets_reader.py` on the read
side) trust the target sheet tab's own header row as the source of truth for
column order — any key in the row dict that isn't in the header is **silently
dropped**, no error. A Sheet set up by copying an old template without every
column from the README's Sheet1/Bookings/Recordings lists (in particular
`CallSessionId`, which is what joins the three tabs together on `/admin`)
will quietly lose data with no exception anywhere (`81aa259`).

**Check.** Compare the actual header row of each tab against the column list
in README.md exactly, including exact spelling/casing.

**Fix.** Add the missing header column(s) to the sheet. No code change
needed — this is purely a spreadsheet setup issue per restaurant.

---

## Bot is answering with the wrong restaurant's facts/hours

**Cause.** `RESTAURANT_ID` doesn't match any key in
`app/config/restaurants/__init__.py`'s `_RESTAURANTS` dict — this silently
fell back to Spice Route Kitchen until `81aa259` added a startup warning.

**Check.** Startup logs for:
```
RESTAURANT_ID '<value>' doesn't match any configured restaurant (...) —
falling back to spice_route_kitchen.
```

**Fix.** Fix the `RESTAURANT_ID` env var on that deployment, or add the new
restaurant to `_RESTAURANTS` if it's genuinely new.

---

## Groq LLM (GROQ_ENABLED) — measured too slow to turn on yet

**Cause.** Tried Groq (Llama/gpt-oss/Qwen on LPU inference hardware) as a
faster conversational LLM than OpenAI, since gpt-5-mini's own 1-3s
completion time is the biggest remaining lever on turn latency (everything
else — TTS buffering, the logging-enforcer background fix — was already
addressed; see the first section of this file). Two real problems found
testing against this project's actual system prompt/tools (not synthetic
prompts):

- pipecat's `GroqLLMService` default model, `llama-3.3-70b-versatile`, 404s
  — not in this account's `/v1/models` list any more. Groq's available
  lineup shifts; `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, and
  `qwen/qwen3.8-27b` were live as of 2026-09-18.
- Far bigger problem: this account's Groq tier rate-limits at **8000
  tokens/minute**. This bot's system prompt alone (`build_system_prompt`)
  is ~19K chars, ~4-5K tokens — so the budget is nearly exhausted by the
  *first* turn alone. Once used up, real turns measured **47-58 seconds
  each** (not a typo) instead of Groq's advertised low latency — the
  `openai` Python client retries a 429 with silent exponential backoff by
  default, so this doesn't fail fast, it just hangs. That's worse than the
  10-18s gaps this was meant to fix, and long enough to trip
  `CallHealthMonitor`'s LLM-failure threshold and end the call.
  `openai/gpt-oss-120b` also burns a large chunk of its own output budget
  on hidden reasoning tokens per turn (confirmed via `usage.completion_
  tokens_details.reasoning_tokens`), which independently adds latency;
  `qwen/qwen3.8-27b` did not show this and was the fastest/most reliable of
  the three on tool-calling once not rate-limited.

**Check.** `GROQ_ENABLED` in `.env`/`.env.example` — must be the literal
string `true`; merely having `GROQ_API_KEY` set does **not** activate Groq
(see `app/config/settings.py`'s `groq_enabled` and `app/main.py`'s
`_build_llm`). This is deliberate: a stray key left in `.env` should never
silently degrade a live call.

**Fix.** Not a code fix — upgrade the Groq account's billing tier at
`console.groq.com/settings/billing` (Dev Tier or higher) for enough TPM
headroom, then re-verify actual per-turn latency against the real system
prompt and tools (not a short synthetic prompt — this bot's prompt size is
exactly what exhausts the free tier's budget) before flipping
`GROQ_ENABLED=true`. `GROQ_MODEL` defaults to `qwen/qwen3.8-27b` based on
the results above; re-check model availability if it 404s again, Groq's
lineup isn't stable.

---

## Dependency versions

`requirements.txt` is pinned to exact versions (`==`), not left floating —
this project already lost time once to an untracked-file deploy break, and an
unpinned `pip install -r requirements.txt` picking up a new `pipecat-ai`
release on a routine redeploy is the same failure shape (works today, breaks
on the next unrelated push) with a much harder-to-spot cause. Bump versions
deliberately, one at a time, not as a side effect of deploying something
else.
