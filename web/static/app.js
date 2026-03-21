// ═══════════════════════════════════════════════════════════
//  MERLIN // AI CO-PILOT — Main Application
// ═══════════════════════════════════════════════════════════

(() => {
  'use strict';

  // ── Configuration ──────────────────────────────────────
  const WS_HOST = window.location.hostname || 'localhost';
  const WS_PORT = window.location.port || 3838;
  const WS_BASE = `ws://${WS_HOST}:${WS_PORT}`;
  const API_BASE = `http://${WS_HOST}:${WS_PORT}`;
  const STATUS_POLL_MS = 10_000;
  const RECONNECT_BASE_MS = 1000;
  const RECONNECT_MAX_MS = 30_000;
  const TYPEWRITER_CHAR_MS = 18;

  // ── DOM References ─────────────────────────────────────
  const dom = {
    telemetryContent: document.getElementById('telemetry-content'),
    telemetryLed:     document.getElementById('telemetry-led'),
    chatMessages:     document.getElementById('chat-messages'),
    chatContent:      document.getElementById('chat-content'),
    chatLed:          document.getElementById('chat-led'),
    chatInput:        document.getElementById('chat-input'),
    pttButton:        document.getElementById('ptt-button'),
    waveformCanvas:   document.getElementById('waveform-canvas'),
    voiceStatus:      document.getElementById('voice-status'),
    voiceStatusText:  document.querySelector('.voice-status-text'),
    ttsAudio:         document.getElementById('tts-audio'),
    statusSim:        document.querySelector('#status-simconnect .led'),
    statusWhisper:    document.querySelector('#status-whisper .led'),
    statusChroma:     document.querySelector('#status-chromadb .led'),
    statusClaude:     document.querySelector('#status-claude .led'),
  };

  // ── State ──────────────────────────────────────────────
  const state = {
    telemetryWs: null,
    chatWs: null,
    telemetryReconnectAttempts: 0,
    chatReconnectAttempts: 0,
    lastTelemetry: {},
    voiceMode: 'idle', // idle | recording | processing | speaking
    mediaRecorder: null,
    audioStream: null,
    audioContext: null,
    analyser: null,
    audioChunks: [],
    isSpaceHeld: false,
    streamingMsgEl: null,
    streamingText: '',
    streamingIndex: 0,
    streamingTimer: null,
    audioQueue: [],
    isPlayingAudio: false,
  };

  // ═══════════════════════════════════════════════════════
  //  UTILITIES
  // ═══════════════════════════════════════════════════════

  function timestamp() {
    const d = new Date();
    return d.toLocaleTimeString('en-GB', { hour12: false });
  }

  function setLed(el, status) {
    el.className = 'led';
    if (status === 'green')  el.classList.add('led-green');
    else if (status === 'amber') el.classList.add('led-amber');
    else el.classList.add('led-red');
  }

  function setTerminalLed(el, connected) {
    el.classList.toggle('connected', connected);
  }

  function reconnectDelay(attempts) {
    return Math.min(RECONNECT_BASE_MS * Math.pow(2, attempts), RECONNECT_MAX_MS);
  }

  // ═══════════════════════════════════════════════════════
  //  TELEMETRY TERMINAL
  // ═══════════════════════════════════════════════════════

  const TELEM_SECTIONS = [
    {
      header: 'AIRCRAFT',
      fields: [
        { key: 'aircraft_type', label: 'TYPE' },
        { key: 'flight_phase',  label: 'PHASE' },
      ],
    },
    {
      header: 'POSITION',
      fields: [
        { key: 'latitude',  label: 'LAT' },
        { key: 'longitude', label: 'LON' },
        { key: 'altitude',  label: 'ALT' },
        { key: 'agl',       label: 'AGL' },
      ],
    },
    {
      header: 'SPEEDS',
      fields: [
        { key: 'ias',      label: 'IAS' },
        { key: 'tas',      label: 'TAS' },
        { key: 'gs',       label: 'GS' },
        { key: 'vs',       label: 'VS' },
        { key: 'heading',  label: 'HDG' },
      ],
    },
    {
      header: 'ENGINE',
      fields: [
        { key: 'rpm',       label: 'RPM' },
        { key: 'manifold',  label: 'MP' },
        { key: 'fuel_flow', label: 'FF' },
        { key: 'oil_temp',  label: 'OIL' },
      ],
    },
    {
      header: 'AUTOPILOT',
      fields: [
        { key: 'ap_status', label: 'STATUS' },
        { key: 'ap_hdg',    label: 'HDG' },
        { key: 'ap_alt',    label: 'ALT' },
        { key: 'ap_vs',     label: 'VS' },
      ],
    },
    {
      header: 'ENVIRONMENT',
      fields: [
        { key: 'wind',        label: 'WIND' },
        { key: 'visibility',  label: 'VIS' },
        { key: 'temperature', label: 'TEMP' },
        { key: 'qnh',         label: 'QNH' },
      ],
    },
    {
      header: 'FUEL',
      fields: [
        { key: 'fuel_total', label: 'TOTAL' },
      ],
    },
  ];

  function buildTelemetryDOM() {
    dom.telemetryContent.innerHTML = '';
    const frag = document.createDocumentFragment();

    for (const section of TELEM_SECTIONS) {
      const headerEl = document.createElement('div');
      headerEl.className = 'telem-section-header';
      const pad = 34 - section.header.length - 6;
      headerEl.textContent = `\u2550\u2550\u2550 ${section.header} ` + '\u2550'.repeat(Math.max(pad, 4));
      frag.appendChild(headerEl);

      const row = document.createElement('div');
      row.className = 'telem-row';

      for (const field of section.fields) {
        const span = document.createElement('span');
        span.className = 'telem-field';
        span.innerHTML = `<span class="telem-label">${field.label}: </span><span class="telem-value" data-key="${field.key}">---</span>`;
        row.appendChild(span);
      }

      frag.appendChild(row);
    }

    dom.telemetryContent.appendChild(frag);
  }

  function updateTelemetryValues(data) {
    // data is a flat object with key-value pairs
    for (const [key, value] of Object.entries(data)) {
      const el = dom.telemetryContent.querySelector(`[data-key="${key}"]`);
      if (!el) continue;

      const strVal = String(value ?? '---');
      if (el.textContent !== strVal) {
        el.textContent = strVal;
        // Flash animation
        el.classList.remove('flash');
        // Force reflow to restart animation
        void el.offsetWidth;
        el.classList.add('flash');
      }
    }
    state.lastTelemetry = { ...state.lastTelemetry, ...data };
  }

  function showAwaitingTelemetry() {
    dom.telemetryContent.innerHTML = '<div class="awaiting-link">AWAITING TELEMETRY LINK...</div>';
  }

  // ── Telemetry WebSocket ────────────────────────────────

  function connectTelemetry() {
    if (state.telemetryWs && state.telemetryWs.readyState <= WebSocket.OPEN) return;

    const ws = new WebSocket(`${WS_BASE}/ws/telemetry`);
    state.telemetryWs = ws;

    ws.addEventListener('open', () => {
      state.telemetryReconnectAttempts = 0;
      setTerminalLed(dom.telemetryLed, true);
      buildTelemetryDOM();
      addSystemMessage('Telemetry link established.');
    });

    ws.addEventListener('message', (evt) => {
      try {
        const data = JSON.parse(evt.data);
        updateTelemetryValues(data);
      } catch (e) {
        console.warn('Telemetry parse error:', e);
      }
    });

    ws.addEventListener('close', () => {
      setTerminalLed(dom.telemetryLed, false);
      showAwaitingTelemetry();
      state.telemetryReconnectAttempts++;
      const delay = reconnectDelay(state.telemetryReconnectAttempts);
      addSystemMessage(`Telemetry link lost. Reconnecting in ${(delay / 1000).toFixed(0)}s...`);
      setTimeout(connectTelemetry, delay);
    });

    ws.addEventListener('error', () => {
      ws.close();
    });
  }

  // ═══════════════════════════════════════════════════════
  //  CHAT TERMINAL
  // ═══════════════════════════════════════════════════════

  function addChatMessage(sender, text, opts = {}) {
    const msg = document.createElement('div');
    const isMerlin = sender === 'MERLIN';
    msg.className = `chat-msg ${isMerlin ? 'merlin-msg' : sender === 'SYSTEM' ? 'system-msg' : 'captain-msg'}`;

    if (sender === 'SYSTEM') {
      msg.textContent = text;
    } else {
      const ts = `<span class="timestamp">[${timestamp()}]</span> `;
      const senderSpan = isMerlin
        ? `<span class="sender-merlin">MERLIN:</span> `
        : `<span class="sender-captain">CAPTAIN:</span> `;
      const textClass = isMerlin ? 'msg-text-merlin' : 'msg-text-captain';
      const textSpan = `<span class="${textClass}">${escapeHtml(text)}</span>`;
      msg.innerHTML = ts + senderSpan + textSpan;
    }

    dom.chatMessages.appendChild(msg);
    scrollChat();
    return msg;
  }

  function addSystemMessage(text) {
    addChatMessage('SYSTEM', text);
  }

  function startStreamingMessage() {
    const msg = document.createElement('div');
    msg.className = 'chat-msg merlin-msg';
    const ts = `<span class="timestamp">[${timestamp()}]</span> `;
    const sender = `<span class="sender-merlin">MERLIN:</span> `;
    msg.innerHTML = ts + sender + `<span class="msg-text-merlin" data-streaming></span><span class="typing-cursor"></span>`;
    dom.chatMessages.appendChild(msg);
    state.streamingMsgEl = msg;
    state.streamingText = '';
    state.streamingIndex = 0;
    scrollChat();
    return msg;
  }

  function appendStreamingChunk(text) {
    state.streamingText += text;
    // Start typewriter if not already running
    if (!state.streamingTimer) {
      typewriterTick();
    }
  }

  function typewriterTick() {
    if (!state.streamingMsgEl) return;
    if (state.streamingIndex >= state.streamingText.length) {
      state.streamingTimer = null;
      return;
    }

    const el = state.streamingMsgEl.querySelector('[data-streaming]');
    if (!el) return;

    el.textContent += state.streamingText[state.streamingIndex];
    state.streamingIndex++;
    scrollChat();

    state.streamingTimer = setTimeout(typewriterTick, TYPEWRITER_CHAR_MS);
  }

  function finishStreamingMessage() {
    if (state.streamingMsgEl) {
      // Flush remaining text immediately
      const el = state.streamingMsgEl.querySelector('[data-streaming]');
      if (el) {
        el.textContent = state.streamingText;
      }
      // Remove cursor
      const cursor = state.streamingMsgEl.querySelector('.typing-cursor');
      if (cursor) cursor.remove();

      state.streamingMsgEl = null;
      state.streamingText = '';
      state.streamingIndex = 0;
      if (state.streamingTimer) {
        clearTimeout(state.streamingTimer);
        state.streamingTimer = null;
      }
    }
    scrollChat();
  }

  function scrollChat() {
    dom.chatContent.scrollTop = dom.chatContent.scrollHeight;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Chat WebSocket ─────────────────────────────────────

  function connectChat() {
    if (state.chatWs && state.chatWs.readyState <= WebSocket.OPEN) return;

    const ws = new WebSocket(`${WS_BASE}/ws/chat`);
    state.chatWs = ws;

    ws.addEventListener('open', () => {
      state.chatReconnectAttempts = 0;
      setTerminalLed(dom.chatLed, true);
      addSystemMessage('MERLIN terminal online.');
    });

    ws.addEventListener('message', (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        handleChatMessage(msg);
      } catch (e) {
        // Treat as plain text from MERLIN
        addChatMessage('MERLIN', evt.data);
      }
    });

    ws.addEventListener('close', () => {
      setTerminalLed(dom.chatLed, false);
      finishStreamingMessage();
      state.chatReconnectAttempts++;
      const delay = reconnectDelay(state.chatReconnectAttempts);
      addSystemMessage(`MERLIN terminal disconnected. Reconnecting in ${(delay / 1000).toFixed(0)}s...`);
      setTimeout(connectChat, delay);
    });

    ws.addEventListener('error', () => {
      ws.close();
    });
  }

  function handleChatMessage(msg) {
    // Expected message formats:
    // { type: "stream_start" }
    // { type: "stream_chunk", text: "..." }
    // { type: "stream_end", audio_url?: "..." }
    // { type: "message", sender: "MERLIN"|"CAPTAIN", text: "..." }
    // { type: "transcription", text: "..." }
    // { type: "audio", url: "..." }
    // { type: "error", text: "..." }

    switch (msg.type) {
      case 'stream_start':
        startStreamingMessage();
        break;

      case 'stream_chunk':
        if (!state.streamingMsgEl) startStreamingMessage();
        appendStreamingChunk(msg.text || '');
        break;

      case 'stream_end':
        finishStreamingMessage();
        if (msg.audio_url) {
          queueAudio(msg.audio_url);
        }
        setVoiceMode('idle');
        break;

      case 'message':
        finishStreamingMessage();
        addChatMessage(msg.sender || 'MERLIN', msg.text || '');
        if (msg.audio_url) {
          queueAudio(msg.audio_url);
        }
        break;

      case 'transcription':
        addChatMessage('CAPTAIN', msg.text || '');
        setVoiceMode('processing');
        break;

      case 'audio':
        if (msg.url) queueAudio(msg.url);
        break;

      case 'error':
        addSystemMessage(`ERROR: ${msg.text || 'Unknown error'}`);
        setVoiceMode('idle');
        break;

      default:
        // Fallback: if there's text, show it as a MERLIN message
        if (msg.text) {
          addChatMessage(msg.sender || 'MERLIN', msg.text);
        }
    }
  }

  function sendChatText(text) {
    if (!text.trim()) return;
    if (!state.chatWs || state.chatWs.readyState !== WebSocket.OPEN) {
      addSystemMessage('Cannot send: MERLIN terminal offline.');
      return;
    }
    addChatMessage('CAPTAIN', text);
    state.chatWs.send(JSON.stringify({ type: 'text', text }));
  }

  // ── Chat Input Handling ────────────────────────────────

  dom.chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const text = dom.chatInput.value.trim();
      if (text) {
        sendChatText(text);
        dom.chatInput.value = '';
      }
    }
  });

  // ═══════════════════════════════════════════════════════
  //  VOICE INPUT SYSTEM
  // ═══════════════════════════════════════════════════════

  function setVoiceMode(mode) {
    state.voiceMode = mode;
    const btn = dom.pttButton;
    const txt = dom.voiceStatusText;

    btn.classList.remove('recording', 'processing', 'speaking');
    txt.classList.remove('recording', 'processing', 'speaking');

    switch (mode) {
      case 'recording':
        btn.classList.add('recording');
        txt.classList.add('recording');
        txt.textContent = 'RECORDING';
        break;
      case 'processing':
        btn.classList.add('processing');
        txt.classList.add('processing');
        txt.textContent = 'PROCESSING...';
        break;
      case 'speaking':
        btn.classList.add('speaking');
        txt.classList.add('speaking');
        txt.textContent = 'MERLIN SPEAKING...';
        break;
      default:
        txt.textContent = 'IDLE';
    }
  }

  async function startRecording() {
    if (state.voiceMode === 'recording') return;

    try {
      if (!state.audioStream) {
        state.audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      }

      // Set up audio analysis
      if (!state.audioContext) {
        state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (state.audioContext.state === 'suspended') {
        await state.audioContext.resume();
      }

      const source = state.audioContext.createMediaStreamSource(state.audioStream);
      state.analyser = state.audioContext.createAnalyser();
      state.analyser.fftSize = 256;
      source.connect(state.analyser);

      // Start recording
      state.audioChunks = [];
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';

      state.mediaRecorder = new MediaRecorder(state.audioStream, { mimeType });

      state.mediaRecorder.addEventListener('dataavailable', (e) => {
        if (e.data.size > 0) state.audioChunks.push(e.data);
      });

      state.mediaRecorder.addEventListener('stop', () => {
        sendAudioRecording();
      });

      state.mediaRecorder.start(100); // collect in 100ms chunks
      setVoiceMode('recording');
      drawWaveform();

    } catch (err) {
      console.error('Mic access error:', err);
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        addSystemMessage('Microphone access denied. Enable mic permissions to use voice input.');
      } else {
        addSystemMessage(`Mic error: ${err.message}`);
      }
    }
  }

  function stopRecording() {
    if (state.mediaRecorder && state.mediaRecorder.state === 'recording') {
      state.mediaRecorder.stop();
      setVoiceMode('processing');
    }
  }

  async function sendAudioRecording() {
    if (state.audioChunks.length === 0) {
      setVoiceMode('idle');
      return;
    }

    const blob = new Blob(state.audioChunks, { type: state.mediaRecorder.mimeType });
    state.audioChunks = [];

    // Send via chat WebSocket as binary
    if (state.chatWs && state.chatWs.readyState === WebSocket.OPEN) {
      // Send a header message first so backend knows audio is incoming
      state.chatWs.send(JSON.stringify({ type: 'audio_start', mime: blob.type }));
      state.chatWs.send(blob);
    } else {
      addSystemMessage('Cannot send audio: MERLIN terminal offline.');
      setVoiceMode('idle');
    }
  }

  // ── Waveform Visualization ─────────────────────────────

  function drawWaveform() {
    const canvas = dom.waveformCanvas;
    const ctx = canvas.getContext('2d');
    const WIDTH = canvas.width;
    const HEIGHT = canvas.height;

    function draw() {
      if (state.voiceMode !== 'recording') {
        // Draw flat line when not recording
        ctx.clearRect(0, 0, WIDTH, HEIGHT);
        ctx.strokeStyle = 'rgba(240, 192, 64, 0.2)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, HEIGHT / 2);
        ctx.lineTo(WIDTH, HEIGHT / 2);
        ctx.stroke();
        return;
      }

      requestAnimationFrame(draw);

      if (!state.analyser) return;

      const bufferLength = state.analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      state.analyser.getByteTimeDomainData(dataArray);

      ctx.clearRect(0, 0, WIDTH, HEIGHT);

      // Draw glow pass
      ctx.strokeStyle = 'rgba(240, 192, 64, 0.15)';
      ctx.lineWidth = 6;
      ctx.beginPath();
      const sliceWidth = WIDTH / bufferLength;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = (v * HEIGHT) / 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        x += sliceWidth;
      }
      ctx.stroke();

      // Draw crisp pass
      ctx.strokeStyle = '#f0c040';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      x = 0;
      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = (v * HEIGHT) / 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        x += sliceWidth;
      }
      ctx.stroke();
    }

    draw();
  }

  // Draw initial flat waveform
  function drawIdleWaveform() {
    const canvas = dom.waveformCanvas;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, canvas.height / 2);
    ctx.lineTo(canvas.width, canvas.height / 2);
    ctx.stroke();
  }

  // ── PTT Button Handlers ────────────────────────────────

  function pttDown(e) {
    e.preventDefault();
    startRecording();
  }

  function pttUp(e) {
    e.preventDefault();
    stopRecording();
  }

  dom.pttButton.addEventListener('mousedown', pttDown);
  dom.pttButton.addEventListener('mouseup', pttUp);
  dom.pttButton.addEventListener('mouseleave', (e) => {
    if (state.voiceMode === 'recording') stopRecording();
  });
  dom.pttButton.addEventListener('touchstart', pttDown);
  dom.pttButton.addEventListener('touchend', pttUp);
  dom.pttButton.addEventListener('touchcancel', pttUp);

  // ── Spacebar PTT ──────────────────────────────────────

  document.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !state.isSpaceHeld && document.activeElement !== dom.chatInput) {
      e.preventDefault();
      state.isSpaceHeld = true;
      startRecording();
    }
  });

  document.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && state.isSpaceHeld) {
      e.preventDefault();
      state.isSpaceHeld = false;
      stopRecording();
    }
  });

  // ═══════════════════════════════════════════════════════
  //  AUDIO PLAYBACK
  // ═══════════════════════════════════════════════════════

  function queueAudio(url) {
    // Make URL absolute if relative
    const audioUrl = url.startsWith('http') ? url : `${API_BASE}${url}`;
    state.audioQueue.push(audioUrl);
    if (!state.isPlayingAudio) {
      playNextAudio();
    }
  }

  function playNextAudio() {
    if (state.audioQueue.length === 0) {
      state.isPlayingAudio = false;
      if (state.voiceMode === 'speaking') setVoiceMode('idle');
      return;
    }

    state.isPlayingAudio = true;
    setVoiceMode('speaking');

    const url = state.audioQueue.shift();
    dom.ttsAudio.src = url;
    dom.ttsAudio.play().catch((err) => {
      console.warn('Audio playback error:', err);
      playNextAudio();
    });
  }

  dom.ttsAudio.addEventListener('ended', playNextAudio);
  dom.ttsAudio.addEventListener('error', () => {
    console.warn('Audio element error');
    playNextAudio();
  });

  // ═══════════════════════════════════════════════════════
  //  STATUS POLLING
  // ═══════════════════════════════════════════════════════

  async function pollStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Expected: { simconnect: bool, whisper: bool, chromadb: bool, claude: bool }
      setLed(dom.statusSim,     data.simconnect ? 'green' : 'red');
      setLed(dom.statusWhisper,  data.whisper    ? 'green' : 'red');
      setLed(dom.statusChroma,   data.chromadb   ? 'green' : 'red');
      setLed(dom.statusClaude,   data.claude     ? 'green' : 'red');
    } catch {
      setLed(dom.statusSim,     'red');
      setLed(dom.statusWhisper,  'red');
      setLed(dom.statusChroma,   'red');
      setLed(dom.statusClaude,   'red');
    }
  }

  // ═══════════════════════════════════════════════════════
  //  CANVAS SIZING
  // ═══════════════════════════════════════════════════════

  function resizeWaveformCanvas() {
    const container = dom.waveformCanvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    dom.waveformCanvas.width = container.clientWidth * dpr;
    dom.waveformCanvas.height = container.clientHeight * dpr;
    const ctx = dom.waveformCanvas.getContext('2d');
    ctx.scale(dpr, dpr);
    drawIdleWaveform();
  }

  // ═══════════════════════════════════════════════════════
  //  INITIALIZATION
  // ═══════════════════════════════════════════════════════

  function init() {
    resizeWaveformCanvas();
    window.addEventListener('resize', resizeWaveformCanvas);
    drawIdleWaveform();
    showAwaitingTelemetry();

    // Connect WebSockets
    connectTelemetry();
    connectChat();

    // Start status polling
    pollStatus();
    setInterval(pollStatus, STATUS_POLL_MS);

    addSystemMessage('MERLIN AI Co-Pilot initializing...');
  }

  // Start when DOM is ready (it already is since script is at end of body)
  init();

})();
