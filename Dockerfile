FROM python:3.12-slim

# Force UTF-8 regardless of the container's locale — Pipecat's startup banner
# and Devanagari/Telugu text in logs will otherwise crash a non-UTF-8 stdout,
# exactly like it did locally on Windows' default console codepage.
ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

# Documentation only — Railway (and most PaaS port-detection) looks at this to
# figure out where to route the public domain. It does not itself change what
# port the app binds to; the actual bind is controlled by $PORT at runtime.
# Confirmed via a live deploy log that Railway injects PORT=8080 here, so this
# is pinned to match rather than left as a guess.
EXPOSE 8080

# Railway injects $PORT at runtime; --host 0.0.0.0 is required to be reachable
# from outside the container (the runner's default, localhost, is not).
# -t pins the runner to one telephony provider (registers the /ws route with
# provider-appropriate startup banner/logging, and — for twilio/telnyx/plivo
# only, not exotel — the XML webhook route those need). Driven by an env var,
# not hardcoded: Exotel is the production target but needs TRAI lead time, so
# TELEPHONY_TRANSPORT lets Railway switch to a free-trial/pay-as-you-go
# provider (twilio/telnyx/plivo) for testing without a rebuild — set it in
# Railway's service variables, default stays exotel.
CMD python -m app.main --host 0.0.0.0 --port ${PORT:-7860} -t ${TELEPHONY_TRANSPORT:-exotel}
