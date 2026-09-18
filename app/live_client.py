"""Browser-based WebRTC test client at /live — place a real call against
this exact deployed bot for testing without per-minute Vobiz/telephony
charges.

Gated behind the same HTTP Basic auth as /admin (see app/admin/auth.py):
this hits the real STT/LLM/TTS providers just like a phone call would, so an
open /live on a public URL would let anyone spend API credits for free, not
just view a page.

Talks directly to pipecat's own POST /api/offer (registered automatically by
pipecat.runner.run once the `webrtc` extra — aiortc — is installed; see
requirements.txt and the "webrtc" entry in main.py's transport_params)
instead of pipecat's prebuilt client UI (`pipecat-ai-prebuilt`) or its
client-js `/start` flow: `_setup_unified_start_route` refuses any transport
other than the one this process was launched with (`-t exotel` in
production), so every WebRTC session would get rejected before it began.
`/api/offer` has no such restriction — a plain RTCPeerConnection talking to
it directly works regardless of which telephony transport this deployment
is pinned to.

Non-trickle ICE (wait for gathering to finish, then send one offer) rather
than streaming candidates over the PATCH endpoint — simpler to get right
without a real device to test against, at the cost of a ~1-3s connect delay.
Fine for a test tool; a STUN-only ICE config (no TURN) is also fine here for
the same reason, but means this won't connect from behind every restrictive
corporate NAT — it will from a normal home/office network.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from app.admin.auth import require_admin

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
      setTimeout(resolve, 3000);
    });
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

    pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    });
    // pipecat's SmallWebRTCTransport passively waits for the *client* to
    // open a data channel (see connection.py's `@self._pc.on("datachannel")`)
    // and logs "Data channel not established within 10s" if none ever shows
    // up -- confirmed live 2026-09-19, harmless to audio (the transport's
    // separate audio-track path is unaffected) but noisy. This client
    // doesn't otherwise need a data channel; opening one just satisfies
    // that wait.
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
