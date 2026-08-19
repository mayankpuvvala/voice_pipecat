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

# Railway injects $PORT at runtime; --host 0.0.0.0 is required to be reachable
# from outside the container (the runner's default, localhost, is not).
CMD python -m app.main --host 0.0.0.0 --port ${PORT:-7860}
