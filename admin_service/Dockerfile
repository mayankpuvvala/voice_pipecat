FROM python:3.12-slim

ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY admin_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY admin_service/ admin_service/

EXPOSE 8081

CMD python -m uvicorn admin_service.main:app --host 0.0.0.0 --port ${PORT:-8081}
