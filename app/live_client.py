"""Browser-based WebRTC test client at /live — place a real call against
this exact deployed bot without per-minute telephony charges.

Gated behind /admin's HTTP Basic auth since this hits real STT/LLM/TTS
providers. Talks directly to pipecat's POST /api/offer instead of its
prebuilt client UI, since that one refuses any transport other than the
one the process was launched with. Non-trickle ICE.

Uses Twilio TURN credentials (via /live/ice-servers, fetched fresh per page
load) rather than STUN alone — see twilio_client.fetch_ice_servers_async's
docstring for why: on Railway, plain STUN isn't enough, since the bot's
server-side aiortc peer has no publicly reachable address to be reflexive
about in the first place. app/main.py patches the matching server-side ICE
servers onto pipecat's SmallWebRTCRequestHandler at startup.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from app.admin.auth import require_admin
from app.services.twilio_client import fetch_ice_servers_async

_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Live test call</title>
<style>
  body {
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    max-width: 640px;
    margin: 2rem auto;
    padding: 0 1rem;
    color: #1a1a1a;
  }
  h2 { margin-bottom: 0.25rem; }
  p.meta { color: #6b7280; font-size: 0.9rem; margin-top: 0; }
  button {
    font-size: 1rem;
    padding: 10px 20px;
    border-radius: 8px;
    border: 1px solid #d1d5db;
    background: #111827;
    color: #fff;
    cursor: pointer;
  }
  button:disabled { opacity: 0.5; cursor: default; }
  #status { font-weight: 600; margin: 1rem 0; }
  #log {
    background: #0b1021;
    color: #b8c4ff;
    padding: 12px;
    border-radius: 8px;
    height: 220px;
    overflow-y: auto;
    font-size: 0.8rem;
    white-space: pre-wrap;
  }
</style>
</head>
<body>
  <h2>Live test call</h2>
  <p class="meta">
    Talks to this exact deployed bot over WebRTC — no phone call, no
    per-minute telephony charges. Uses your browser's microphone.
  </p>
  <button id="connect-btn">Connect</button>
  <p id="status">Not connected</p>
  <audio id="bot-audio" autoplay></audio>
  <div id="log"></div>

<script>
  const connectBtn = document.getElementById('connect-btn');
  const statusEl = document.getElementById('status');
  const audioEl = document.getElementById('bot-audio');
  const logEl = document.getElementById('log');
  let pc = null;
  let localStream = null;

  function log(msg) {
    const line = document.createElement('div');
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function waitForIceGathering(peerConnection) {
    if (peerConnection.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      function check() {
        if (peerConnection.iceGatheringState === 'complete') {
          peerConnection.removeEventListener('icegatheringstatechange', check);
          resolve();
        }
      }
      peerConnection.addEventListener('icegatheringstatechange', check);
      // Some networks never report "complete" (no reachable STUN server) --
      // proceed with whatever candidates gathered so far rather than hang.
      // 5s, not 3: a TURN allocation round-trip is slower than plain STUN.
      setTimeout(resolve, 5000);
    });
  }

  async function fetchIceServers() {
    try {
      const response = await fetch('/live/ice-servers');
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (Array.isArray(data.iceServers) && data.iceServers.length) {
        return data.iceServers;
      }
    } catch (e) {
      log('Could not fetch TURN credentials, falling back to STUN-only: ' + e.message);
    }
    return [{ urls: 'stun:stun.l.google.com:19302' }];
  }

  function disconnect() {
    if (pc) {
      pc.close();
      pc = null;
    }
    if (localStream) {
      localStream.getTracks().forEach((t) => t.stop());
      localStream = null;
    }
    connectBtn.textContent = 'Connect';
    connectBtn.disabled = false;
    statusEl.textContent = 'Not connected';
  }

  async function connect() {
    connectBtn.disabled = true;
    statusEl.textContent = 'Requesting microphone...';

    try {
      localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      statusEl.textContent = 'Microphone access denied: ' + e.message;
      connectBtn.disabled = false;
      return;
    }

    const iceServers = await fetchIceServers();
    pc = new RTCPeerConnection({ iceServers });
    // pipecat's transport waits for the client to open a data channel and
    // logs a noisy warning otherwise; not otherwise needed here.
    pc.createDataChannel('events');
    // One bidirectional audio transceiver: addTrack alone is enough to both
    // send our mic and receive the bot's reply on the same m-line.
    localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));

    pc.ontrack = (event) => {
      log('Bot audio track received');
      audioEl.srcObject = event.streams[0];
    };
    pc.onconnectionstatechange = () => {
      log('Connection state: ' + pc.connectionState);
      if (pc.connectionState === 'connected') {
        statusEl.textContent = 'Connected — talk normally, the bot should greet you first.';
      } else if (['failed', 'closed', 'disconnected'].includes(pc.connectionState)) {
        disconnect();
      }
    };

    try {
      statusEl.textContent = 'Negotiating...';
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);

      const response = await fetch('/api/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sdp: pc.localDescription.sdp,
          type: pc.localDescription.type,
        }),
      });

      if (!response.ok) {
        statusEl.textContent = 'Server rejected the call: HTTP ' + response.status;
        log(await response.text());
        disconnect();
        return;
      }

      const answer = await response.json();
      await pc.setRemoteDescription(answer);
    } catch (e) {
      statusEl.textContent = 'Connection failed: ' + e.message;
      log(e.stack || String(e));
      disconnect();
      return;
    }

    connectBtn.textContent = 'Hang up';
    connectBtn.disabled = false;
  }

  connectBtn.addEventListener('click', () => {
    if (pc) {
      disconnect();
    } else {
      connect();
    }
  });
</script>
</body>
</html>
"""


def register_live_test_client(app: FastAPI) -> None:
    @app.get("/live", dependencies=[Depends(require_admin)], include_in_schema=False)
    async def live_test_client() -> HTMLResponse:
        return HTMLResponse(_PAGE)

    @app.get("/live/ice-servers", dependencies=[Depends(require_admin)], include_in_schema=False)
    async def live_ice_servers() -> JSONResponse:
        return JSONResponse({"iceServers": await fetch_ice_servers_async()})
