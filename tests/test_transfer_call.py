"""Offline tests for live call transfer (transfer_call + its provider client
and XML routes). No network: provider HTTP goes through httpx.MockTransport,
and the escalation row-write (Sheets + owner SMS/WhatsApp) is always stubbed
out — .env may hold real credentials, so nothing here may reach them.

Run: .venv\\Scripts\\python.exe -m pytest tests -q
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    EndWorkerFrame,
    TTSSpeakFrame,
)
from pipecat.serializers.plivo import PlivoFrameSerializer

from app.config.restaurants import SPICE_ROUTE_KITCHEN, transfer_target
from app.pipeline.bot_speech_tracker import BotSpeechTracker
from app.pipeline.prompts import build_system_prompt
from app.services import call_transfer
from app.services.call_transfer import (
    TransferLine,
    build_dial_status_xml,
    request_transfer,
    set_plivo_auto_hang_up,
    transfer_endpoint,
)
from app.tools import transfer_call as transfer_call_module
from app.tools.transfer_call import FAILURE_APOLOGY, transfer_call
from app.transfer_routes import register_transfer_routes

FAKE_CREDS = SimpleNamespace(
    plivo_auth_id="MAPLIVOID",
    plivo_auth_token="plivo-token",
    vobiz_auth_id="MA_VOBIZID",
    vobiz_auth_token="vobiz-token",
)


# --- fakes -----------------------------------------------------------------


class FakeGuard:
    def __init__(self, spoken: bool) -> None:
        self._spoken = spoken

    def has_spoken_this_turn(self) -> bool:
        return self._spoken


class FakeWorker:
    def __init__(self) -> None:
        self.frames: list = []
        self.finished = False

    async def queue_frames(self, frames) -> None:
        self.frames.extend(frames)

    def has_finished(self) -> bool:
        return self.finished


class Recorder:
    """Async callable that records what it was called with."""

    def __init__(self, return_value=None) -> None:
        self.calls: list = []
        self._return_value = return_value

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._return_value


def make_params(*, spoken: bool, transfer_line: TransferLine | None):
    worker = FakeWorker()
    hang_up_toggles: list[bool] = []
    transcript_captures: list[bool] = []
    if transfer_line is not None and transfer_line.provider == "plivo":
        transfer_line.set_auto_hang_up = hang_up_toggles.append
    result = Recorder()
    params = SimpleNamespace(
        result_callback=result,
        app_resources={
            "call_session_id": "session-1",
            "caller_phone": "+919000000000",
            "worker_handle": SimpleNamespace(worker=worker),
            "capture_transcript": lambda: transcript_captures.append(True),
            "turn_taking_guard": FakeGuard(spoken),
            "transfer_line": transfer_line,
            # Already quiet: wait_until_quiet returns after start_timeout.
            "bot_speech_tracker": None,
        },
    )
    return SimpleNamespace(
        params=params,
        result=result,
        worker=worker,
        hang_up_toggles=hang_up_toggles,
        transcript_captures=transcript_captures,
    )


def plivo_line(**overrides) -> TransferLine:
    fields = dict(
        provider="plivo",
        target_number="+919876543210",
        call_uuid="call-uuid-123",
        aleg_url="https://bot.example.com/transfer-dial?caller_id=%2B918000000000",
    )
    fields.update(overrides)
    return TransferLine(**fields)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Fake credentials, a stubbed escalation write, and no teardown wait."""
    monkeypatch.setattr(call_transfer, "settings", FAKE_CREDS)
    escalation = Recorder(return_value=True)
    monkeypatch.setattr(transfer_call_module, "write_interaction_row", escalation)
    monkeypatch.setattr(transfer_call_module, "_PROVIDER_TEARDOWN_GRACE_SECS", 0)
    return escalation


@pytest.fixture
def mock_http(monkeypatch):
    """Route call_transfer's own httpx.AsyncClient through a MockTransport.

    Set `.response` (an httpx.Response) or `.error` (an exception) before
    the call; every request lands in `.requests`.
    """
    state = SimpleNamespace(requests=[], response=httpx.Response(202, json={"message": "call transferred"}), error=None)

    def handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        if state.error is not None:
            raise state.error
        return state.response

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(call_transfer.httpx, "AsyncClient", client_factory)
    return state


async def drain_background_tasks() -> None:
    for _ in range(5):
        pending = list(transfer_call_module._background_tasks)
        if not pending:
            return
        await asyncio.gather(*pending)


# --- the tool --------------------------------------------------------------


async def test_refuses_when_nothing_spoken_yet(mock_http, offline):
    ctx = make_params(spoken=False, transfer_line=plivo_line())

    await transfer_call(ctx.params, reason="asked for the manager")

    (result,), _ = ctx.result.calls[0]
    assert result["transferred"] is False
    assert "nothing has been said" in result["reason"]
    assert mock_http.requests == []
    assert offline.calls == []  # no escalation alert for a refused attempt
    assert ctx.hang_up_toggles == []


async def test_no_transfer_number_before_handoff_line(mock_http):
    ctx = make_params(spoken=False, transfer_line=plivo_line(target_number=""))

    await transfer_call(ctx.params, reason="complaint")

    (result,), _ = ctx.result.calls[0]
    assert result["transferred"] is False
    assert "no transfer number" in result["reason"]
    assert "offer to take" in result["next_step"]
    # Nothing spoken yet, so the LLM's own continuation will apologise —
    # the tool mustn't talk over it.
    assert not any(isinstance(f, TTSSpeakFrame) for f in ctx.worker.frames)
    assert mock_http.requests == []


async def test_no_transfer_number_after_handoff_line_speaks_apology(mock_http):
    ctx = make_params(spoken=True, transfer_line=plivo_line(target_number=""))

    await transfer_call(ctx.params, reason="complaint")

    (result,), _ = ctx.result.calls[0]
    assert result["transferred"] is False
    spoken = [f for f in ctx.worker.frames if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in spoken] == [FAILURE_APOLOGY]
    assert mock_http.requests == []


async def test_no_transfer_line_at_all(mock_http):
    ctx = make_params(spoken=False, transfer_line=None)

    await transfer_call(ctx.params, reason="asked for a human")

    (result,), _ = ctx.result.calls[0]
    assert result["transferred"] is False
    assert "isn't available" in result["reason"]


async def test_plivo_success(mock_http, offline):
    ctx = make_params(spoken=True, transfer_line=plivo_line())

    await transfer_call(ctx.params, reason="asked for the manager", caller_name="Ramesh")
    await drain_background_tasks()

    (result,), _ = ctx.result.calls[0]
    assert result == {"transferred": True}

    (request,) = mock_http.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.plivo.com/v1/Account/MAPLIVOID/Call/call-uuid-123/"
    expected_auth = base64.b64encode(b"MAPLIVOID:plivo-token").decode()
    assert request.headers["authorization"] == f"Basic {expected_auth}"
    assert json.loads(request.content) == {
        "legs": "aleg",
        "aleg_url": "https://bot.example.com/transfer-dial?caller_id=%2B918000000000",
        "aleg_method": "POST",
    }

    # Disarmed before the request and never re-armed: a later pipeline
    # teardown must not hang up the transferred caller.
    assert ctx.hang_up_toggles == [False]
    assert ctx.transcript_captures == [True]
    # Counted as an escalation via the shared row-write (owner SMS/WhatsApp).
    (_, kwargs), = offline.calls
    assert kwargs["resolved"] is False
    assert kwargs["details"] == "asked for the manager"
    assert kwargs["caller_name"] == "Ramesh"
    # Fallback teardown fires since the fake provider never closed the stream.
    assert any(isinstance(f, EndWorkerFrame) for f in ctx.worker.frames)
    assert not any(isinstance(f, TTSSpeakFrame) for f in ctx.worker.frames)


async def test_fallback_teardown_skipped_once_provider_closed_stream(mock_http):
    ctx = make_params(spoken=True, transfer_line=plivo_line())
    ctx.worker.finished = True  # on_client_disconnected already ended it

    await transfer_call(ctx.params, reason="complaint")
    await drain_background_tasks()

    assert not any(isinstance(f, EndWorkerFrame) for f in ctx.worker.frames)


async def test_provider_rejects_transfer(mock_http, offline):
    mock_http.response = httpx.Response(404, json={"error": "call not found"})
    ctx = make_params(spoken=True, transfer_line=plivo_line())

    await transfer_call(ctx.params, reason="asked for the owner")
    await drain_background_tasks()

    (result,), _ = ctx.result.calls[0]
    assert result["transferred"] is False
    assert "HTTP 404" in result["reason"]
    assert "don't repeat" in result["next_step"]
    # Apology spoken directly (no dead air), call kept up, hang-up re-armed.
    assert [f.text for f in ctx.worker.frames if isinstance(f, TTSSpeakFrame)] == [FAILURE_APOLOGY]
    assert not any(isinstance(f, EndWorkerFrame) for f in ctx.worker.frames)
    assert ctx.hang_up_toggles == [False, True]
    assert ctx.transcript_captures == []
    # Still an escalation: the owner needs to follow up either way.
    assert len(offline.calls) == 1


async def test_provider_unreachable(mock_http):
    mock_http.error = httpx.ConnectTimeout("timed out")
    ctx = make_params(spoken=True, transfer_line=plivo_line())

    await transfer_call(ctx.params, reason="complaint")

    (result,), _ = ctx.result.calls[0]
    assert result["transferred"] is False
    assert "ConnectTimeout" in result["reason"]
    assert ctx.hang_up_toggles == [False, True]


async def test_simulated_line_never_dials(mock_http):
    line = TransferLine(provider="simulated", target_number="+910000000000")
    ctx = make_params(spoken=True, transfer_line=line)

    await transfer_call(ctx.params, reason="asked for the manager")

    (result,), _ = ctx.result.calls[0]
    assert result == {"transferred": True}
    assert mock_http.requests == []
    assert any(isinstance(f, EndWorkerFrame) for f in ctx.worker.frames)


async def test_waits_for_handoff_line_before_requesting_transfer(mock_http):
    tracker = BotSpeechTracker()
    tracker.observe(BotStartedSpeakingFrame())
    ctx = make_params(spoken=True, transfer_line=plivo_line())
    ctx.params.app_resources["bot_speech_tracker"] = tracker

    task = asyncio.create_task(transfer_call(ctx.params, reason="complaint"))
    await asyncio.sleep(0.2)
    assert mock_http.requests == []  # still "playing" the handoff line
    tracker.observe(BotStoppedSpeakingFrame())
    await asyncio.wait_for(task, 5)
    assert len(mock_http.requests) == 1


# --- provider client -------------------------------------------------------


async def test_vobiz_endpoint_and_headers(mock_http):
    result = await request_transfer("vobiz", "vobiz-uuid", "https://bot.example.com/transfer-dial")

    assert result.ok
    (request,) = mock_http.requests
    assert str(request.url) == "https://api.vobiz.ai/api/v1/Account/MA_VOBIZID/Call/vobiz-uuid/"
    assert request.headers["X-Auth-ID"] == "MA_VOBIZID"
    assert request.headers["X-Auth-Token"] == "vobiz-token"
    assert "authorization" not in request.headers
    assert json.loads(request.content)["legs"] == "aleg"


def test_endpoint_per_provider():
    assert transfer_endpoint("plivo", "A", "U") == "https://api.plivo.com/v1/Account/A/Call/U/"
    assert transfer_endpoint("vobiz", "A", "U") == "https://api.vobiz.ai/api/v1/Account/A/Call/U/"


async def test_missing_credentials_never_sends(mock_http, monkeypatch):
    monkeypatch.setattr(call_transfer, "settings", replace_ns(FAKE_CREDS, vobiz_auth_token=""))

    result = await request_transfer("vobiz", "uuid", "https://x/transfer-dial")

    assert not result.ok
    assert "credentials" in result.reason
    assert mock_http.requests == []


async def test_unsupported_provider(mock_http):
    result = await request_transfer("twilio", "uuid", "https://x/transfer-dial")
    assert not result.ok
    assert mock_http.requests == []


def replace_ns(ns: SimpleNamespace, **changes) -> SimpleNamespace:
    return SimpleNamespace(**{**vars(ns), **changes})


# --- Plivo auto hang-up toggle (pins a pipecat private) --------------------


async def test_disarmed_plivo_serializer_does_not_hang_up(monkeypatch):
    serializer = PlivoFrameSerializer(
        stream_id="s", call_id="c", auth_id="a", auth_token="t",
        params=PlivoFrameSerializer.InputParams(auto_hang_up=True),
    )
    hang_ups = Recorder()
    monkeypatch.setattr(serializer, "_hang_up_call", hang_ups)

    set_plivo_auto_hang_up(serializer, False)
    await serializer.serialize(EndFrame())
    assert hang_ups.calls == []

    set_plivo_auto_hang_up(serializer, True)
    await serializer.serialize(EndFrame())
    assert len(hang_ups.calls) == 1


# --- XML routes -------------------------------------------------------------


def make_client(restaurant) -> TestClient:
    app = FastAPI()
    register_transfer_routes(app, restaurant)
    return TestClient(app)


def test_dial_xml_route():
    client = make_client(replace(SPICE_ROUTE_KITCHEN, transfer_number="+919876543210"))

    response = client.post(
        "/transfer-dial?caller_id=%2B918000000000", headers={"host": "bot.example.com"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert response.text == (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Dial timeout="30" callerId="+918000000000" '
        'action="https://bot.example.com/transfer-dial-status" method="POST">'
        "<Number>+919876543210</Number></Dial></Response>"
    )


def test_dial_xml_ignores_bad_caller_id_and_never_dials_from_query():
    client = make_client(replace(SPICE_ROUTE_KITCHEN, transfer_number="+919876543210"))

    response = client.post("/transfer-dial?caller_id=<script>&number=%2B1900555")

    assert "callerId" not in response.text
    assert "<Number>+919876543210</Number>" in response.text
    assert "1900555" not in response.text


def test_dial_xml_falls_back_to_owner_phone():
    restaurant = replace(SPICE_ROUTE_KITCHEN, transfer_number="", owner_phone="+919111111111")
    assert transfer_target(restaurant) == "+919111111111"
    assert "<Number>+919111111111</Number>" in make_client(restaurant).post("/transfer-dial").text


def test_dial_xml_without_any_number_speaks_instead_of_dialing():
    restaurant = replace(SPICE_ROUTE_KITCHEN, transfer_number="", owner_phone="")
    response = make_client(restaurant).post("/transfer-dial")
    assert "<Dial" not in response.text
    assert "<Speak>" in response.text


def test_dial_status_route():
    client = make_client(replace(SPICE_ROUTE_KITCHEN, transfer_number="+919876543210"))

    completed = client.post("/transfer-dial-status", data={"DialStatus": "completed"})
    no_answer = client.post("/transfer-dial-status", data={"DialStatus": "no-answer"})

    assert "<Speak>" not in completed.text
    assert "<Speak>" in no_answer.text
    assert build_dial_status_xml("busy") == no_answer.text


# --- prompt + speech tracker -----------------------------------------------


def test_transfer_rules_only_in_prompt_when_enabled():
    assert "transfer_call" not in build_system_prompt(SPICE_ROUTE_KITCHEN)
    assert "transfer_call" in build_system_prompt(SPICE_ROUTE_KITCHEN, live_transfer=True)


async def test_tracker_returns_quickly_when_nothing_plays():
    tracker = BotSpeechTracker()
    assert await asyncio.wait_for(tracker.wait_until_quiet(start_timeout=0.05), 2) is True


async def test_tracker_waits_through_a_pause_between_sentences():
    tracker = BotSpeechTracker()
    tracker.observe(BotStartedSpeakingFrame())

    async def speak():
        await asyncio.sleep(0.05)
        tracker.observe(BotStoppedSpeakingFrame())
        await asyncio.sleep(0.05)  # shorter than settle: same line
        tracker.observe(BotStartedSpeakingFrame())
        await asyncio.sleep(0.05)
        tracker.observe(BotStoppedSpeakingFrame())

    loop = asyncio.get_running_loop()
    started = loop.time()
    speaker = asyncio.create_task(speak())
    assert await tracker.wait_until_quiet(settle=0.2, max_wait=3) is True
    assert loop.time() - started >= 0.15 + 0.2 - 0.05
    await speaker


async def test_tracker_gives_up_after_max_wait():
    tracker = BotSpeechTracker()
    tracker.observe(BotStartedSpeakingFrame())
    assert await tracker.wait_until_quiet(max_wait=0.1) is False


def test_tool_schema_is_valid_direct_function():
    """pipecat builds the LLM-facing schema from the signature + docstring."""
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.adapters.services.open_ai_adapter import OpenAILLMAdapter

    (tool,) = OpenAILLMAdapter().to_provider_tools_format(ToolsSchema(standard_tools=[transfer_call]))
    function = tool["function"]
    assert function["name"] == "transfer_call"
    assert function["parameters"]["required"] == ["reason"]
    assert set(function["parameters"]["properties"]) == {"reason", "caller_name"}
