"""Shared setup for the offline unit tests (no network, no paid APIs).

Run:  .venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt
      .venv\\Scripts\\python.exe -m pytest tests -q

Never import app.main here: it probes OpenAI and fetches Twilio ICE servers
at import time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Must be set before app.config.settings is first imported (it reads env at
# import, and calls load_dotenv() which never overrides vars already set).
os.environ["RESTAURANT_ID"] = "spice_route_kitchen"
for _key in (
    "OPENAI_API_KEY",
    "GOOGLE_SHEET_ID",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "WHATSAPP_CALLMEBOT_APIKEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
):
    os.environ[_key] = ""
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "s3cret"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeParams:
    """Stands in for pipecat's FunctionCallParams in tool tests."""

    def __init__(self, messages=None, app_resources=None):
        self.context = type("Ctx", (), {"messages": messages or []})()
        self.app_resources = app_resources or {}
        self.results: list[dict] = []

    async def result_callback(self, result):
        self.results.append(result)
