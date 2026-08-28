FROM python:3.12-slim

ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

EXPOSE 8080

CMD python -m app.main --host 0.0.0.0 --port ${PORT:-7860} -t ${TELEPHONY_TRANSPORT:-twilio} --proxy "${PUBLIC_PROXY_HOST:-$RAILWAY_PUBLIC_DOMAIN}"
