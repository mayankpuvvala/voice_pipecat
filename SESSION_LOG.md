# Session log — 2026-08-19/20

Written for a cold read when you're back. Newest/most-important first.

## Still open — needs your action

1. **Daily transport for real cloud WebRTC connectivity** — branch
   `daily-transport-attempt`, NOT merged to `main`. Explained in detail
   below under "Railway WebRTC connectivity." Needs: a free Daily.co
   signup (10,000 free min/month), `DAILY_API_KEY` set in Railway, and
   confirmation the Linux build actually succeeds — none of which I could
   do or verify myself.
2. **n8n live workflow still needs the auto-map paste** — same manual step
   flagged earlier tonight, still outstanding: replace `1b. Parse Vapi Tool
   Call`'s code with the flattened version and switch `1c. Append
   Interaction Row`'s Column Mapping to "Map Automatically." Local copy in
   `n8n/restaurant_reception_workflow.json` already has this; the live n8n
   editor doesn't yet.
3. Once (1) is sorted: a real browser test call through the public Railway
   URL, to confirm this actually fixes the ICE timeout in practice, not
   just in theory.

## What got fixed and verified tonight

### logInteraction wasn't reliably firing for short factual answers

Found via isolated eval-transport testing (fresh bot process per scenario,
not the shared-context batch from earlier which masked this). Prompt
instructions alone — even made explicit and absolute across two rewrite
iterations — reliably worked for reservation-style replies but not for
one-sentence factual answers (hours, allergen questions): the model would
literally say "I'll log that" and then not call the tool.

Fixed structurally, not with more prompt wording: `app/pipeline/
logging_enforcer.py` adds a `LogInteractionEnforcer` frame processor. On
any LLM turn that ends without a tool call, it captures the reply's own
text as it streams past (positioned right after `llm` in the pipeline —
confirmed the frames it needs don't reliably survive past `tts`/
`assistant_aggregator`) and pushes one self-contained follow-up completion
that quotes the reply directly and asks the model to decide whether to log
it. Guards against an infinite nudge-loop (a model correctly staying
silent in response to its own nudge would otherwise trigger another
nudge, forever).

**Verified 5/5** on isolated scenarios (reservation multi-turn, reservation
all-at-once, in-scope question, out-of-scope question, Hindi reservation) —
each run gets its own fresh bot process so conversations don't bleed
context into each other. Committed and pushed to `main` (commit `1abd18e`).

Two dead ends on the way there, kept in the commit message for context:
positioning the processor *after* `assistant_aggregator` seemed like the
obvious fix (context ordering) but broke because the frames it needs get
consumed for aggregation rather than forwarded that far; referencing
context state instead of quoting the reply directly broke because the
developer nudge could land in context *before* the assistant's own reply
did, producing a confusing ordering the model couldn't reason about.

### Same-language comparison: restaurant-voice-agent vs ai-receptionist on Hindi

Sent the identical Hindi text to both tonight. ai-receptionist explicitly
asked the caller to repeat in English (matches its own stated "English
only" policy). restaurant-voice-agent replied *in Hindi*, correctly using
the caller's name — direct validation of the language-aware prompt added
earlier this session.

### Railway deploy itself (separate from the WebRTC issue below)

Was 502 for ~25 minutes. Root cause was two things, not one:
1. `EXPOSE 8080` was missing from the Dockerfile — confirmed via a live
   deploy log that the container was actually healthy and listening
   correctly the whole time; this was a routing/discovery issue, not an
   app crash.
2. The bigger one: the entire admin dashboard, restaurant content update,
   n8n auto-map changes, and eval-transport addition had been built and
   verified locally but **never actually committed** — Railway was
   building the very first commit only. Both fixed and pushed; `/admin`
   confirmed working on the live Railway URL with real Sheet data.

### ai-receptionist: real bug found, not just restaurant-voice-agent

10 scripted calls (synthetic WebSocket caller, edge-tts-generated audio) —
in 2 of 5 reservation scenarios, the conversation ended before an explicit
final confirmation, so no `create_reservation` tool call ever fired — but
the post-call summary confidently claimed the reservation was "confirmed"
anyway. That's `agent.summarise()` hallucinating success rather than
reflecting what happened. Not fixed tonight (out of scope for this
project's work, flagged for ai-receptionist separately). Full transcripts
in the scratchpad if wanted later — ask me and I'll regenerate/relocate
them, since scratchpad contents don't persist as durably as this repo.

### Restaurant content

Replaced placeholder facts (including a literal unfilled "your city" in
the address) with fuller reference content — real-sounding address,
categorized menu, dietary note. Still explicitly placeholder/reference —
swap for the real client's actual details before this goes live.

## Railway WebRTC connectivity — the deeper issue

A real test call against the public Railway URL timed out establishing
the peer connection ("Timeout establishing the connection to the remote
peer"). Root cause: raw WebRTC (`SmallWebRTCTransport`, aiortc-based) needs
a TURN relay server to cross NAT between the browser and the server. Works
flawlessly on localhost because there's no NAT to cross there. Railway
doesn't provide a TURN server, and — this is the part that closed off the
easy fix — **pipecat's dev runner hardcodes `ice_servers=None` for the
webrtc transport with no CLI flag or env var to override it.** Getting a
custom TURN server working on the existing "webrtc" transport would mean
reimplementing the runner's internal WebRTC route handling, which is real
framework-internals surgery I don't have full visibility into.

Checked for a zero-signup free TURN option first (Open Relay / Metered.ca
used to offer this) — as of now it requires a free account + API key, same
as every other option (Cloudflare Calls, Twilio). I can't create accounts
on your behalf, so "fully free, zero action from you" doesn't exist right
now for real TURN infrastructure.

Lower-risk path: pipecat has first-class Daily transport support already,
Daily's SDK handles TURN/NAT-traversal itself, and the dev runner already
auto-creates the room/token given `DAILY_API_KEY` — much smaller code
change than custom ICE plumbing (see `daily-transport-attempt` branch).
**Could not verify this actually works**: `daily-python` (the native
dependency) has no Windows wheel, so it won't even install on this dev
machine, let alone run. It should resolve fine on Railway's Linux
container (wheels exist for `manylinux_x86_64`), but that's unconfirmed —
which is exactly why this sat on a branch instead of going to `main`
sight-unseen against a currently-working deployment.

## Scaling considerations (documented, not built — still explicitly a
single-call use case per this project's own scoping)

Not touched tonight beyond writing this down, since actually building for
concurrency isn't something this project has asked for — but worth having
on record:

- Pipecat's worker/runner model (`PipelineWorker#0` in every log tonight)
  appears designed for multiple concurrent workers within one runner
  process — the numbering and registry-broadcast pattern suggest more than
  one can coexist. **Unverified** — every test tonight was strictly one
  call at a time, sequential, never simultaneous. Don't assume concurrent
  calls work correctly without actually testing two overlapping connections.
- If concurrency is ever needed: Railway's container CPU/memory limits
  would need reviewing under real concurrent load, and OpenAI/Sarvam's API
  rate limits would need checking against expected concurrent call volume
  — neither was a concern tonight at 1 call at a time.
- The n8n logging path (single webhook, Google Sheets append) should
  handle low concurrent volume fine as-is — Sheets API and n8n's webhook
  handling aren't going to be the bottleneck at restaurant-call scale.

## Repo / branch state as of writing

- `main`: everything above except the Daily transport — deployed to
  Railway, verified working (webrtc on localhost, admin dashboard on both
  localhost and the public Railway URL).
- `daily-transport-attempt`: the untested Daily transport addition, pushed
  but not merged.
