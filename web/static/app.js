// ═══════════════════════════════════════════════════════════
//  SUPER HORNET // AI WINGMAN — Main Application
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
  const SCROLL_THRESHOLD_PX = 100; // auto-scroll if within this distance of bottom

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
    statusSim:        document.querySelector('#status-game .led'),
    statusWhisper:    document.querySelector('#status-whisper .led'),
    statusChroma:     document.querySelector('#status-chromadb .led'),
    statusClaude:     document.querySelector('#status-claude .led'),
    ttsVolume:        document.getElementById('tts-volume'),
    volumePct:        document.getElementById('volume-pct'),
    connQuality:      document.getElementById('conn-quality'),
    connQualityText:  document.getElementById('conn-quality-text'),
    scanButton:       document.getElementById('scan-button'),
  };

  // ── State ──────────────────────────────────────────────
  const state = {
    telemetryWs: null,
    chatWs: null,
    telemetryReconnectAttempts: 0,
    chatReconnectAttempts: 0,
    lastTelemetry: {},
    voiceMode: 'idle', // idle | recording | processing | thinking | speaking
    mediaRecorder: null,
    audioStream: null,
    audioContext: null,
    analyser: null,
    micSource: null,
    audioChunks: [],
    isSpaceHeld: false,
    streamingMsgEl: null,
    streamingText: '',
    streamingIndex: 0,
    streamingRafId: null,
    lastStreamingRenderTime: 0,
    audioQueue: [],
    isPlayingAudio: false,
    currentAudio: null,
    ttsVolume: 0.8,
    seenMessageIds: new Set(),      // dedup after reconnect
    messageIdCounter: 0,
    isUserScrolledUp: false,
    telemetryRafPending: false,
    pendingTelemetryData: null,
    chatReconnecting: false,        // suppress duplicate system messages
    telemetryReconnecting: false,
    simWasConnected: false,           // tracks actual game connection status
    wsMessageBuffer: [],            // backpressure buffer
    wsBufferProcessing: false,
    thinkingMsgEl: null,            // "Super Hornet is thinking..." indicator
    _acquiringMic: false,           // mutex for async mic acquisition
  };

  // ═══════════════════════════════════════════════════════
  //  UTILITIES
  // ═══════════════════════════════════════════════════════

  function timestamp() {
    const d = new Date();
    return d.toLocaleTimeString('en-GB', { hour12: false });
  }

  function setLed(el, status) {
    if (!el) return;
    el.className = 'led';
    if (status === 'green')  el.classList.add('led-green');
    else if (status === 'amber') el.classList.add('led-amber');
    else el.classList.add('led-red');
  }

  function setTerminalLed(el, connected) {
    if (!el) return;
    el.classList.toggle('connected', connected);
  }

  function reconnectDelay(attempts) {
    return Math.min(RECONNECT_BASE_MS * Math.pow(2, attempts), RECONNECT_MAX_MS);
  }

  function generateMessageId() {
    return `msg-${Date.now()}-${state.messageIdCounter++}`;
  }

  // ── Scroll management ──────────────────────────────────

  function isNearBottom() {
    const el = dom.chatContent;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_THRESHOLD_PX;
  }

  function scrollChatIfNeeded() {
    if (!state.isUserScrolledUp && dom.chatContent) {
      dom.chatContent.scrollTop = dom.chatContent.scrollHeight;
    }
  }

  // Track user scroll intent
  if (dom.chatContent) {
    dom.chatContent.addEventListener('scroll', () => {
      state.isUserScrolledUp = !isNearBottom();
    }, { passive: true });
  }

  // ── Connection quality ─────────────────────────────────

  function updateConnectionQuality() {
    const telOk = state.telemetryWs && state.telemetryWs.readyState === WebSocket.OPEN;
    const chatOk = state.chatWs && state.chatWs.readyState === WebSocket.OPEN;

    let quality, label;
    if (telOk && chatOk) {
      quality = 'good';
      label = 'CONNECTED';
    } else if (telOk || chatOk) {
      quality = 'degraded';
      label = 'DEGRADED';
    } else {
      quality = 'offline';
      label = 'OFFLINE';
    }

    if (dom.connQuality) {
      dom.connQuality.className = `conn-quality-led conn-${quality}`;
    }
    if (dom.connQualityText) {
      dom.connQualityText.textContent = label;
    }
  }

  // ═══════════════════════════════════════════════════════
  //  TELEMETRY TERMINAL
  // ═══════════════════════════════════════════════════════

  const TELEM_SECTIONS = [
    { header: 'SHIP', fields: [
      { key: 'ship_name', label: 'SHIP' },
      { key: 'activity', label: 'ACTIVITY' },
    ]},
    { header: 'LOCATION', fields: [
      { key: 'system', label: 'SYSTEM' },
      { key: 'body', label: 'BODY' },
      { key: 'zone', label: 'ZONE' },
    ]},
    { header: 'SHIELDS', fields: [
      { key: 'shields_front', label: 'FRONT' },
      { key: 'shields_rear', label: 'REAR' },
      { key: 'shields_left', label: 'LEFT' },
      { key: 'shields_right', label: 'RIGHT' },
    ]},
    { header: 'SYSTEMS', fields: [
      { key: 'hull', label: 'HULL' },
      { key: 'h_fuel', label: 'H-FUEL' },
      { key: 'q_fuel', label: 'Q-FUEL' },
      { key: 'power', label: 'POWER' },
    ]},
    { header: 'WEAPONS', fields: [
      { key: 'weapons', label: 'STATUS' },
      { key: 'missiles', label: 'MISSILES' },
      { key: 'mode', label: 'MODE' },
    ]},
    { header: 'COMBAT', fields: [
      { key: 'hostiles', label: 'HOSTILES' },
      { key: 'crime_stat', label: 'CRIMESTAT' },
      { key: 'target', label: 'TARGET' },
    ]},
  ];

  // Cache telemetry value elements for fast lookup (avoid querySelectorAll per update)
  const _telemValueCache = new Map();

  function buildTelemetryDOM() {
    if (!dom.telemetryContent) return;
    dom.telemetryContent.innerHTML = '';
    _telemValueCache.clear();
    const frag = document.createDocumentFragment();

    for (const section of TELEM_SECTIONS) {
      const headerEl = document.createElement('div');
      headerEl.className = 'telem-section-header';
      headerEl.textContent = section.header;
      frag.appendChild(headerEl);

      const row = document.createElement('div');
      row.className = 'telem-row';

      for (const field of section.fields) {
        const span = document.createElement('span');
        span.className = 'telem-field';
        const labelSpan = document.createElement('span');
        labelSpan.className = 'telem-label';
        labelSpan.textContent = `${field.label}: `;
        const valueSpan = document.createElement('span');
        valueSpan.className = 'telem-value';
        valueSpan.dataset.key = field.key;
        valueSpan.textContent = '---';
        span.appendChild(labelSpan);
        span.appendChild(valueSpan);
        row.appendChild(span);

        // Cache for direct access
        _telemValueCache.set(field.key, valueSpan);
      }

      frag.appendChild(row);
    }

    dom.telemetryContent.appendChild(frag);
  }

  function flattenTelemetry(msg) {
    // The server sends: { type: "telemetry", connected: bool, data: { ... } }
    // where data contains the Star Citizen GameState with nested objects.
    // Flatten it into the display keys expected by TELEM_SECTIONS.
    const d = msg.data || msg;
    if (!d || typeof d !== 'object') return null;

    const ship = d.ship || {};
    const player = d.player || {};
    const combat = d.combat || {};

    const flat = {};
    flat.ship_name = ship.name || '---';
    flat.activity = d.activity || '---';

    flat.system = player.location_system || '---';
    flat.body = player.location_body || '---';
    flat.zone = player.location_zone || '---';

    flat.shields_front = ship.shields_front != null ? Math.round(ship.shields_front) + '%' : '---';
    flat.shields_rear = ship.shields_rear != null ? Math.round(ship.shields_rear) + '%' : '---';
    flat.shields_left = ship.shields_left != null ? Math.round(ship.shields_left) + '%' : '---';
    flat.shields_right = ship.shields_right != null ? Math.round(ship.shields_right) + '%' : '---';

    flat.hull = ship.hull_percent != null ? Math.round(ship.hull_percent) + '%' : '---';
    flat.h_fuel = ship.hydrogen_fuel_percent != null ? Math.round(ship.hydrogen_fuel_percent) + '%' : '---';
    flat.q_fuel = ship.quantum_fuel_percent != null ? Math.round(ship.quantum_fuel_percent) + '%' : '---';
    flat.power = ship.power_on ? 'ON' : 'OFF';

    flat.weapons = ship.weapons_armed ? 'ARMED' : 'SAFE';
    flat.missiles = ship.missiles_remaining != null ? String(ship.missiles_remaining) : '---';
    flat.mode = ship.decoupled_mode ? 'DECOUPLED' : 'COUPLED';

    flat.hostiles = combat.hostile_count != null ? String(combat.hostile_count) : '0';
    flat.crime_stat = player.crime_stat != null ? String(player.crime_stat) : '0';
    flat.target = combat.target_name || '---';

    return flat;
  }

  function updateTelemetryValues(msg) {
    const data = flattenTelemetry(msg);
    if (!data) return;

    // Use rAF to coalesce rapid telemetry updates and avoid layout thrash
    state.pendingTelemetryData = { ...state.pendingTelemetryData, ...data };
    if (!state.telemetryRafPending) {
      state.telemetryRafPending = true;
      requestAnimationFrame(flushTelemetryUpdate);
    }
  }

  function flushTelemetryUpdate() {
    state.telemetryRafPending = false;
    const data = state.pendingTelemetryData;
    if (!data) return;
    state.pendingTelemetryData = null;

    for (const [key, value] of Object.entries(data)) {
      const el = _telemValueCache.get(key);
      if (!el) continue;

      const strVal = String(value ?? '---');
      if (el.textContent !== strVal) {
        el.textContent = strVal;
        el.classList.add('value-updated');
        setTimeout(() => el.classList.remove('value-updated'), 600);
      }
    }
    state.lastTelemetry = { ...state.lastTelemetry, ...data };
  }

  function showAwaitingTelemetry() {
    if (!dom.telemetryContent) return;
    dom.telemetryContent.innerHTML = '<div class="awaiting-link">AWAITING TELEMETRY LINK...</div>';
    _telemValueCache.clear();
  }

  // ── Telemetry WebSocket ────────────────────────────────

  function connectTelemetry() {
    if (state.telemetryWs && state.telemetryWs.readyState <= WebSocket.OPEN) return;

    const ws = new WebSocket(`${WS_BASE}/ws/telemetry`);
    state.telemetryWs = ws;

    ws.addEventListener('open', () => {
      state.telemetryReconnectAttempts = 0;
      buildTelemetryDOM();
      state.telemetryReconnecting = false;
      // Don't claim "established" yet — wait for actual sim data
    });

    ws.addEventListener('message', (evt) => {
      try {
        const data = JSON.parse(evt.data);
        const simActive = data.connected === true;

        // Update game LED based on actual game connection status, not WS status
        setTerminalLed(dom.telemetryLed, simActive);

        if (simActive && !state.simWasConnected) {
          addSystemMessage('Telemetry link established — game connected.');
          state.simWasConnected = true;
        } else if (!simActive && state.simWasConnected) {
          addSystemMessage('Game disconnected — telemetry paused.');
          state.simWasConnected = false;
        }

        updateTelemetryValues(data);
        updateConnectionQuality();
      } catch (_) {
        // Telemetry parse error — silently discard malformed frames
      }
    });

    ws.addEventListener('close', () => {
      setTerminalLed(dom.telemetryLed, false);
      showAwaitingTelemetry();
      state.telemetryReconnectAttempts++;
      const delay = reconnectDelay(state.telemetryReconnectAttempts);

      // Only show disconnect message once, not on every retry
      if (!state.telemetryReconnecting) {
        state.telemetryReconnecting = true;
        addSystemMessage('Telemetry link lost. Reconnecting...');
      }
      updateConnectionQuality();
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
    // Dedup check: if message has an id we have already seen, skip
    if (opts.id && state.seenMessageIds.has(opts.id)) return null;
    if (opts.id) state.seenMessageIds.add(opts.id);

    const msg = document.createElement('div');
    const isHornet = sender === 'HORNET';
    msg.className = `chat-msg ${isHornet ? 'merlin-msg' : sender === 'SYSTEM' ? 'system-msg' : 'captain-msg'}`;

    if (sender === 'SYSTEM') {
      msg.textContent = text;
    } else {
      const ts = `<span class="timestamp">[${timestamp()}]</span> `;
      const senderSpan = isHornet
        ? `<span class="sender-merlin">HORNET:</span> `
        : `<span class="sender-captain">CMDR:</span> `;
      const textClass = isHornet ? 'msg-text-merlin' : 'msg-text-captain';
      const textSpan = `<span class="${textClass}">${escapeHtml(text)}</span>`;
      msg.innerHTML = ts + senderSpan + textSpan;
    }

    if (dom.chatMessages) dom.chatMessages.appendChild(msg);
    scrollChatIfNeeded();
    return msg;
  }

  function addSystemMessage(text) {
    addChatMessage('SYSTEM', text);
  }

  // ── Thinking indicator ─────────────────────────────────

  function showThinkingIndicator() {
    removeThinkingIndicator();
    const msg = document.createElement('div');
    msg.className = 'chat-msg merlin-msg thinking-indicator';
    const ts = `<span class="timestamp">[${timestamp()}]</span> `;
    const sender = `<span class="sender-merlin">HORNET:</span> `;
    msg.innerHTML = ts + sender + `<span class="msg-text-merlin thinking-dots">thinking</span>`;
    if (dom.chatMessages) dom.chatMessages.appendChild(msg);
    state.thinkingMsgEl = msg;
    scrollChatIfNeeded();
  }

  function removeThinkingIndicator() {
    if (state.thinkingMsgEl) {
      state.thinkingMsgEl.remove();
      state.thinkingMsgEl = null;
    }
  }

  // ── Streaming messages ─────────────────────────────────

  function startStreamingMessage() {
    removeThinkingIndicator();
    const msg = document.createElement('div');
    msg.className = 'chat-msg merlin-msg';
    const ts = `<span class="timestamp">[${timestamp()}]</span> `;
    const sender = `<span class="sender-merlin">HORNET:</span> `;
    msg.innerHTML = ts + sender + `<span class="msg-text-merlin" data-streaming></span><span class="typing-cursor"></span>`;
    if (dom.chatMessages) dom.chatMessages.appendChild(msg);
    state.streamingMsgEl = msg;
    state.streamingText = '';
    state.streamingIndex = 0;
    state.lastStreamingRenderTime = 0;
    scrollChatIfNeeded();
    return msg;
  }

  function appendStreamingChunk(text) {
    state.streamingText += text;
    // Start rAF-based rendering if not already running
    if (!state.streamingRafId) {
      state.streamingRafId = requestAnimationFrame(typewriterFrame);
    }
  }

  function typewriterFrame(ts) {
    if (!state.streamingMsgEl) {
      state.streamingRafId = null;
      return;
    }
    if (state.streamingIndex >= state.streamingText.length) {
      state.streamingRafId = null;
      return;
    }

    const el = state.streamingMsgEl.querySelector('[data-streaming]');
    if (!el) {
      state.streamingRafId = null;
      return;
    }

    // Calculate how many characters to render this frame based on elapsed time
    if (!state.lastStreamingRenderTime) state.lastStreamingRenderTime = ts;
    const elapsed = ts - state.lastStreamingRenderTime;
    const charsToRender = Math.max(1, Math.floor(elapsed / TYPEWRITER_CHAR_MS));
    state.lastStreamingRenderTime = ts;

    const endIndex = Math.min(state.streamingIndex + charsToRender, state.streamingText.length);
    // Batch-append multiple characters at once to reduce DOM ops
    el.textContent = state.streamingText.slice(0, endIndex);
    state.streamingIndex = endIndex;
    scrollChatIfNeeded();

    if (state.streamingIndex < state.streamingText.length) {
      state.streamingRafId = requestAnimationFrame(typewriterFrame);
    } else {
      state.streamingRafId = null;
    }
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
      if (state.streamingRafId) {
        cancelAnimationFrame(state.streamingRafId);
        state.streamingRafId = null;
      }
      state.lastStreamingRenderTime = 0;
    }
    removeThinkingIndicator();
    scrollChatIfNeeded();
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ═══════════════════════════════════════════════════════
  //  BARGE-IN: Interrupt TTS when user starts talking/typing
  // ═══════════════════════════════════════════════════════

  function bargeIn() {
    if (!state.isPlayingAudio && state.audioQueue.length === 0) return;

    // Stop current Web Audio source
    if (state.currentAudio) {
      try { state.currentAudio.stop(); } catch (_) { /* may already be stopped */ }
      state.currentAudio = null;
    }

    // Clear pending audio queue
    state.audioQueue.length = 0;
    state.isPlayingAudio = false;

    if (state.voiceMode === 'speaking') {
      setVoiceMode('idle');
    }
  }

  // ── Chat WebSocket ─────────────────────────────────────

  function connectChat() {
    if (state.chatWs && (state.chatWs.readyState === WebSocket.CONNECTING || state.chatWs.readyState === WebSocket.OPEN)) return;

    const ws = new WebSocket(`${WS_BASE}/ws/chat`);
    state.chatWs = ws;

    ws.addEventListener('open', () => {
      state.chatReconnectAttempts = 0;
      setTerminalLed(dom.chatLed, true);
      if (!state.chatReconnecting) {
        addSystemMessage('Super Hornet terminal online.');
      } else {
        addSystemMessage('Super Hornet terminal reconnected.');
      }
      state.chatReconnecting = false;
      updateConnectionQuality();
    });

    ws.binaryType = 'blob';

    ws.addEventListener('message', (evt) => {
      // Binary data = TTS audio chunk
      if (evt.data instanceof Blob) {
        queueAudioBlob(evt.data);
        return;
      }
      // Buffer incoming messages for backpressure handling
      state.wsMessageBuffer.push(evt.data);
      if (!state.wsBufferProcessing) {
        processMessageBuffer();
      }
    });

    ws.addEventListener('close', () => {
      setTerminalLed(dom.chatLed, false);
      finishStreamingMessage();
      state.chatReconnectAttempts++;
      const delay = reconnectDelay(state.chatReconnectAttempts);

      if (!state.chatReconnecting) {
        state.chatReconnecting = true;
        addSystemMessage('Super Hornet terminal disconnected. Reconnecting...');
      }
      updateConnectionQuality();
      setTimeout(connectChat, delay);
    });

    ws.addEventListener('error', () => {
      ws.close();
    });
  }

  // ── Backpressure: process buffered WebSocket messages in batches ──

  function processMessageBuffer() {
    state.wsBufferProcessing = true;

    // Process up to 10 messages per frame to avoid blocking
    const batchSize = 10;
    let processed = 0;

    while (state.wsMessageBuffer.length > 0 && processed < batchSize) {
      const raw = state.wsMessageBuffer.shift();
      try {
        const msg = JSON.parse(raw);
        handleChatMessage(msg);
      } catch (e) {
        // Non-JSON text fallback
        addChatMessage('HORNET', raw);
      }
      processed++;
    }

    if (state.wsMessageBuffer.length > 0) {
      // More messages to process — schedule on next frame
      requestAnimationFrame(processMessageBuffer);
    } else {
      state.wsBufferProcessing = false;
    }
  }

  function handleChatMessage(msg) {
    // Server message formats (actual from server.py):
    //   { type: "text", content: "..." }     — streamed text chunks
    //   { type: "done" }                     — end of response
    //   { type: "transcription", text: "..." }
    //   { type: "tts_audio", size: N }       — precedes binary TTS frame
    //   { type: "error", content: "..." }
    //
    // Also support legacy client-side formats for compatibility:
    //   { type: "stream_start" }
    //   { type: "stream_chunk", text: "..." }
    //   { type: "stream_end" }
    //   { type: "message", sender, text }

    switch (msg.type) {
      // ── Server wire format ──
      case 'text':
        // Server sends { type: "text", content: "..." } for streaming chunks
        if (!state.streamingMsgEl) startStreamingMessage();
        appendStreamingChunk(msg.content || '');
        // Once first token arrives, switch from thinking to idle/default
        if (state.voiceMode === 'thinking') setVoiceMode('idle');
        break;

      case 'done':
        // Server signals end of Claude response
        finishStreamingMessage();
        // Stay in speaking mode if TTS is still playing, otherwise idle
        if (!state.isPlayingAudio && state.audioQueue.length === 0) {
          setVoiceMode('idle');
        }
        break;

      // ── Legacy / compatibility formats ──
      case 'stream_start':
        startStreamingMessage();
        break;

      case 'stream_chunk':
        if (!state.streamingMsgEl) startStreamingMessage();
        appendStreamingChunk(msg.text || '');
        break;

      case 'stream_end':
        finishStreamingMessage();
        if (!state.isPlayingAudio && state.audioQueue.length === 0) {
          setVoiceMode('idle');
        }
        break;

      case 'message':
        finishStreamingMessage();
        addChatMessage(msg.sender || 'HORNET', msg.text || '');
        break;

      case 'transcription':
        addChatMessage('CMDR', msg.text || '', {
          id: `transcription-${msg.text}`,
        });
        setVoiceMode('thinking');
        showThinkingIndicator();
        break;

      case 'audio_url':
        // TTS now streamed as binary chunks — ignore legacy audio_url
        break;

      case 'tts_audio':
        // Marker before binary audio frame — handled by binary message listener
        break;

      case 'listening':
        // Server signals Super Hornet is ready for input after finishing response
        if (!state.isPlayingAudio && state.audioQueue.length === 0) {
          setVoiceMode('idle');
        }
        break;

      case 'interrupted':
        // Server confirms the active response was cancelled (barge-in)
        finishStreamingMessage();
        removeThinkingIndicator();
        break;

      case 'error':
        removeThinkingIndicator();
        finishStreamingMessage();
        const errText = msg.content || msg.text || 'Unknown error';
        // Show transcription errors with more context
        if (errText.toLowerCase().includes('transcri')) {
          addSystemMessage(`STT WARNING: ${errText}`);
        } else {
          addSystemMessage(`ERROR: ${errText}`);
        }
        setVoiceMode('idle');
        break;

      default:
        // Fallback: if there's text or content, show it
        if (msg.text || msg.content) {
          addChatMessage(msg.sender || 'HORNET', msg.text || msg.content);
        }
    }
  }

  function sendChatText(text) {
    if (!text.trim()) return;
    if (!state.chatWs || state.chatWs.readyState !== WebSocket.OPEN) {
      addSystemMessage('Cannot send: Super Hornet terminal offline.');
      return;
    }
    // Barge-in: stop any playing TTS when user sends text
    bargeIn();
    addChatMessage('CMDR', text);
    setVoiceMode('thinking');
    showThinkingIndicator();
    state.chatWs.send(JSON.stringify({ type: 'text', text }));
  }

  // ── Chat Input Handling ────────────────────────────────

  if (dom.chatInput) {
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

    // Barge-in on typing while TTS is playing
    dom.chatInput.addEventListener('input', () => {
      if (state.isPlayingAudio || state.audioQueue.length > 0) {
        bargeIn();
      }
    });
  }

  // ── Scan Button ───────────────────────────────────────

  if (dom.scanButton) {
    dom.scanButton.addEventListener('click', () => {
      if (!state.chatWs || state.chatWs.readyState !== WebSocket.OPEN) {
        addSystemMessage('Cannot scan: Super Hornet terminal offline.');
        return;
      }
      // Show scanning status briefly
      const txt = dom.voiceStatusText;
      if (txt) {
        txt.textContent = 'SCANNING...';
        txt.classList.add('processing');
        setTimeout(() => {
          if (txt.textContent === 'SCANNING...') {
            txt.textContent = 'IDLE';
            txt.classList.remove('processing');
          }
        }, 3000);
      }
      sendChatText('/scan what do you see on my screen?');
    });
  }

  // ═══════════════════════════════════════════════════════
  //  VOICE INPUT SYSTEM
  // ═══════════════════════════════════════════════════════

  function setVoiceMode(mode) {
    state.voiceMode = mode;
    const btn = dom.pttButton;
    const txt = dom.voiceStatusText;

    if (btn) btn.classList.remove('recording', 'processing', 'speaking', 'thinking');
    if (txt) txt.classList.remove('recording', 'processing', 'speaking', 'thinking', 'speech-detected');

    switch (mode) {
      case 'recording':
        if (btn) btn.classList.add('recording');
        if (txt) { txt.classList.add('recording'); txt.textContent = 'RECORDING'; }
        break;
      case 'processing':
        if (btn) btn.classList.add('processing');
        if (txt) { txt.classList.add('processing'); txt.textContent = 'PROCESSING...'; }
        break;
      case 'thinking':
        if (btn) btn.classList.add('thinking');
        if (txt) { txt.classList.add('thinking'); txt.textContent = 'SUPER HORNET THINKING...'; }
        break;
      case 'speaking':
        if (btn) btn.classList.add('speaking');
        if (txt) { txt.classList.add('speaking'); txt.textContent = 'SUPER HORNET SPEAKING'; }
        break;
      default:
        if (txt) txt.textContent = 'IDLE';
    }
  }

  async function startRecording() {
    if (state.voiceMode === 'recording' || state._acquiringMic) return;
    state._acquiringMic = true;

    // Barge-in: stop TTS if Super Hornet is speaking
    bargeIn();

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

      // Disconnect previous mic source node if any (prevent leak)
      if (state.micSource) {
        try { state.micSource.disconnect(); } catch (_) { /* ignore */ }
      }
      state.micSource = state.audioContext.createMediaStreamSource(state.audioStream);
      state.analyser = state.audioContext.createAnalyser();
      state.analyser.fftSize = 256;
      state.micSource.connect(state.analyser);

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
      state._acquiringMic = false;
      drawWaveform();

    } catch (err) {
      state._acquiringMic = false;
      // Mic access error — report to user via system message
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
    // Disconnect mic source node to avoid Web Audio graph leaks
    if (state.micSource) {
      try { state.micSource.disconnect(); } catch (_) { /* ignore */ }
      state.micSource = null;
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
      addSystemMessage('Cannot send audio: Super Hornet terminal offline.');
      setVoiceMode('idle');
    }
  }

  // ── Waveform Visualization ─────────────────────────────

  // Speech energy threshold for the browser-side VAD indicator.
  // This is a UI-only hint -- actual VAD runs server-side via Silero.
  const SPEECH_ENERGY_THRESHOLD = 0.015;

  // Pre-allocated buffers -- avoid allocating in the draw loop
  let _waveTimeDomain = null;   // Uint8Array for time-domain data
  let _waveFreqData = null;     // Uint8Array for frequency data
  let _waveAnimFrameId = null;  // Current rAF id for cancellation

  // Animation phase accumulators (persistent across frames)
  let _idlePhase = 0;
  let _sweepPhase = 0;
  let _speakingPhase = 0;
  let _lastDrawTime = 0;

  function ensureWaveBuffers(analyser) {
    const len = analyser.frequencyBinCount;
    if (!_waveTimeDomain || _waveTimeDomain.length !== len) {
      _waveTimeDomain = new Uint8Array(len);
    }
    if (!_waveFreqData || _waveFreqData.length !== len) {
      _waveFreqData = new Uint8Array(len);
    }
  }

  function computeSpeechEnergy(analyser) {
    // Compute energy from frequency-domain data for a simple speech indicator.
    ensureWaveBuffers(analyser);
    analyser.getByteFrequencyData(_waveFreqData);
    let sum = 0;
    for (let i = 0; i < _waveFreqData.length; i++) {
      const normalized = _waveFreqData[i] / 255.0;
      sum += normalized * normalized;
    }
    return Math.sqrt(sum / _waveFreqData.length);
  }

  function updateVadIndicator(isSpeech) {
    const txt = dom.voiceStatusText;
    if (!txt) return;
    if (state.voiceMode !== 'recording') return;

    if (isSpeech) {
      txt.textContent = 'SPEECH DETECTED';
      txt.classList.add('speech-detected');
    } else {
      txt.textContent = 'LISTENING...';
      txt.classList.remove('speech-detected');
    }
  }

  // ── Master draw loop -- dispatches to mode-specific renderers ──

  function startWaveformLoop() {
    if (_waveAnimFrameId) return; // already running
    _lastDrawTime = performance.now();
    _waveAnimFrameId = requestAnimationFrame(waveformFrame);
  }

  function stopWaveformLoop() {
    if (_waveAnimFrameId) {
      cancelAnimationFrame(_waveAnimFrameId);
      _waveAnimFrameId = null;
    }
  }

  function waveformFrame(ts) {
    _waveAnimFrameId = null;

    // Pause rendering when tab is hidden
    if (document.hidden) return;

    const dt = Math.min((ts - _lastDrawTime) / 1000, 0.1); // seconds, clamped
    _lastDrawTime = ts;

    const canvas = dom.waveformCanvas;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    // Use CSS pixel dimensions for drawing (canvas is scaled by dpr)
    const W = canvas.width / dpr;
    const H = canvas.height / dpr;

    ctx.clearRect(0, 0, W, H);

    switch (state.voiceMode) {
      case 'recording':
        drawRecordingWaveform(ctx, W, H, dt);
        break;
      case 'speaking':
        drawSpeakingWaveform(ctx, W, H, dt);
        break;
      case 'processing':
      case 'thinking':
        drawSweepAnimation(ctx, W, H, dt);
        break;
      default:
        drawIdleOscilloscope(ctx, W, H, dt);
        break;
    }

    _waveAnimFrameId = requestAnimationFrame(waveformFrame);
  }

  // ── 1. Idle: oscilloscope baseline with gentle sine pulse ──

  function drawIdleOscilloscope(ctx, W, H, dt) {
    _idlePhase += dt * 0.8; // slow phase advance

    const cy = H / 2;

    // Dim horizontal reference line
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.08)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, cy);
    ctx.lineTo(W, cy);
    ctx.stroke();

    // Subtle sine wave -- heartbeat-monitor flat line with faint pulse
    const amplitude = 2 + Math.sin(_idlePhase * 0.6) * 1.5; // 0.5-3.5 px
    const freq = 0.015; // cycles per pixel

    // Glow pass
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.06)';
    ctx.lineWidth = 4;
    ctx.beginPath();
    for (let x = 0; x < W; x++) {
      const y = cy + Math.sin(x * freq + _idlePhase) * amplitude;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Crisp pass
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.18)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x < W; x++) {
      const y = cy + Math.sin(x * freq + _idlePhase) * amplitude;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  // ── 2. Recording: dual-pass waveform + frequency bars + baseline ──

  function drawRecordingWaveform(ctx, W, H, dt) {
    if (!state.analyser) return;

    ensureWaveBuffers(state.analyser);

    // Compute speech energy for VAD indicator
    const energy = computeSpeechEnergy(state.analyser);
    updateVadIndicator(energy > SPEECH_ENERGY_THRESHOLD);

    // Fetch time-domain data
    state.analyser.getByteTimeDomainData(_waveTimeDomain);

    const bufferLength = state.analyser.frequencyBinCount;
    const cy = H / 2;

    // Dim horizontal center reference line
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.07)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, cy);
    ctx.lineTo(W, cy);
    ctx.stroke();

    // Line thickness scales with speech energy
    const baseThick = 1.5;
    const energyThick = Math.min(energy * 40, 3.0); // up to 4.5px when loud
    const lineWidth = baseThick + energyThick;
    const glowWidth = lineWidth + 5;

    // Glow pass
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.12)';
    ctx.lineWidth = glowWidth;
    ctx.beginPath();
    const sliceWidth = W / bufferLength;
    let x = 0;
    for (let i = 0; i < bufferLength; i++) {
      const v = _waveTimeDomain[i] / 128.0;
      const y = (v * H) / 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      x += sliceWidth;
    }
    ctx.stroke();

    // Crisp pass
    ctx.strokeStyle = '#f0c040';
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    x = 0;
    for (let i = 0; i < bufferLength; i++) {
      const v = _waveTimeDomain[i] / 128.0;
      const y = (v * H) / 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      x += sliceWidth;
    }
    ctx.stroke();

    // ── Frequency spectrum bars along the bottom ──
    state.analyser.getByteFrequencyData(_waveFreqData);
    const barCount = 32;
    const barWidth = W / barCount;
    const maxBarHeight = H * 0.18; // small, subtle
    const barY = H - 2;
    const binStep = Math.floor(_waveFreqData.length / barCount);

    for (let i = 0; i < barCount; i++) {
      let sum = 0;
      for (let b = 0; b < binStep; b++) {
        sum += _waveFreqData[i * binStep + b];
      }
      const avg = sum / binStep / 255.0;
      const barH = avg * maxBarHeight;

      if (barH < 1) continue;

      const alpha = 0.15 + avg * 0.35;
      ctx.fillStyle = `rgba(240, 192, 64, ${alpha})`;
      ctx.fillRect(i * barWidth + 1, barY - barH, barWidth - 2, barH);
    }
  }

  // ── 3. Speaking/TTS: radar rings + mirrored waveform (cyan) ──

  function drawSpeakingWaveform(ctx, W, H, dt) {
    _speakingPhase += dt;

    const cx = W / 2;
    const cy = H / 2;
    const maxRadius = Math.max(W, H) * 0.6;
    const ringCount = 4;
    const ringInterval = 1.2; // seconds between ring spawns
    const ringLifetime = ringCount * ringInterval;

    // Dim center crosshair
    ctx.strokeStyle = 'rgba(64, 220, 240, 0.08)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx - 15, cy);
    ctx.lineTo(cx + 15, cy);
    ctx.moveTo(cx, cy - 10);
    ctx.lineTo(cx, cy + 10);
    ctx.stroke();

    // Concentric expanding rings
    for (let i = 0; i < ringCount; i++) {
      const age = (_speakingPhase + i * ringInterval) % ringLifetime;
      const t = age / ringLifetime; // 0..1 progress
      const radius = t * maxRadius;
      const alpha = (1 - t) * 0.45;

      if (alpha < 0.01) continue;

      // Glow ring
      ctx.strokeStyle = `rgba(64, 220, 240, ${(alpha * 0.3).toFixed(3)})`;
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.stroke();

      // Crisp ring
      ctx.strokeStyle = `rgba(64, 220, 240, ${alpha.toFixed(3)})`;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Mirrored simulated waveform around center line
    const wavePoints = 64;
    const waveSlice = W / wavePoints;

    ctx.strokeStyle = 'rgba(64, 220, 240, 0.3)';
    ctx.lineWidth = 1;

    // Top half
    ctx.beginPath();
    for (let i = 0; i < wavePoints; i++) {
      const px = i * waveSlice;
      const noise = Math.sin(i * 0.4 + _speakingPhase * 6)
                   * Math.sin(i * 0.15 + _speakingPhase * 2.5)
                   * (0.3 + 0.7 * Math.sin(_speakingPhase * 1.5));
      const amp = H * 0.12 * Math.abs(noise);
      const y = cy - amp;
      if (i === 0) ctx.moveTo(px, y);
      else ctx.lineTo(px, y);
    }
    ctx.stroke();

    // Bottom half (mirror)
    ctx.beginPath();
    for (let i = 0; i < wavePoints; i++) {
      const px = i * waveSlice;
      const noise = Math.sin(i * 0.4 + _speakingPhase * 6)
                   * Math.sin(i * 0.15 + _speakingPhase * 2.5)
                   * (0.3 + 0.7 * Math.sin(_speakingPhase * 1.5));
      const amp = H * 0.12 * Math.abs(noise);
      const y = cy + amp;
      if (i === 0) ctx.moveTo(px, y);
      else ctx.lineTo(px, y);
    }
    ctx.stroke();
  }

  // ── 4. Processing/Thinking: radar sweep line ──

  function drawSweepAnimation(ctx, W, H, dt) {
    _sweepPhase += dt * 0.6;

    const cy = H / 2;
    const progress = _sweepPhase % 1; // 0..1 across canvas

    // Dim horizontal baseline
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.06)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, cy);
    ctx.lineTo(W, cy);
    ctx.stroke();

    // Sweep position
    const sweepX = progress * W;

    // Trailing fade gradient behind the sweep line
    const trailWidth = W * 0.25;
    const trailStart = Math.max(0, sweepX - trailWidth);
    const grad = ctx.createLinearGradient(trailStart, 0, sweepX, 0);
    grad.addColorStop(0, 'rgba(240, 192, 64, 0)');
    grad.addColorStop(1, 'rgba(240, 192, 64, 0.12)');
    ctx.fillStyle = grad;
    ctx.fillRect(trailStart, 0, sweepX - trailStart, H);

    // Bright sweep line
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.7)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sweepX, 0);
    ctx.lineTo(sweepX, H);
    ctx.stroke();

    // Glow around the sweep line
    ctx.strokeStyle = 'rgba(240, 192, 64, 0.15)';
    ctx.lineWidth = 8;
    ctx.beginPath();
    ctx.moveTo(sweepX, 0);
    ctx.lineTo(sweepX, H);
    ctx.stroke();

    // Small pulsing dots along center line
    const dotCount = 5;
    const dotSpacing = W / (dotCount + 1);
    for (let i = 1; i <= dotCount; i++) {
      const dx = i * dotSpacing;
      const dist = Math.abs(dx - sweepX);
      const alpha = Math.max(0, 0.4 - dist / (W * 0.15));
      if (alpha < 0.01) continue;

      ctx.fillStyle = `rgba(240, 192, 64, ${alpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(dx, cy, 2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // ── Legacy entry point (called by startRecording) ──

  function drawWaveform() {
    startWaveformLoop();
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

  if (dom.pttButton) {
    dom.pttButton.addEventListener('mousedown', pttDown);
    dom.pttButton.addEventListener('mouseup', pttUp);
    dom.pttButton.addEventListener('mouseleave', () => {
      if (state.voiceMode === 'recording') stopRecording();
    });
    dom.pttButton.addEventListener('touchstart', pttDown);
    dom.pttButton.addEventListener('touchend', pttUp);
    dom.pttButton.addEventListener('touchcancel', pttUp);
  }

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

  // ── Keyboard Shortcuts Help ─────────────────────────────

  let _helpOverlay = null;
  let _helpTimeout = null;

  function showKeyboardHelp() {
    if (_helpOverlay) return;

    _helpOverlay = document.createElement('div');
    _helpOverlay.className = 'keyboard-help-overlay';
    _helpOverlay.innerHTML = [
      '<div class="keyboard-help-title">KEYBOARD SHORTCUTS</div>',
      '<div class="keyboard-help-row"><kbd>SPACE</kbd> Push-to-talk</div>',
      '<div class="keyboard-help-row"><kbd>ENTER</kbd> Send text message</div>',
      '<div class="keyboard-help-row"><kbd>ESC</kbd> Cancel recording / clear input</div>',
    ].join('');
    document.body.appendChild(_helpOverlay);

    // Force layout then add visible class for CSS transition
    requestAnimationFrame(() => {
      if (_helpOverlay) _helpOverlay.classList.add('visible');
    });

    _helpTimeout = setTimeout(dismissKeyboardHelp, 3000);
  }

  function dismissKeyboardHelp() {
    if (!_helpOverlay) return;
    _helpOverlay.classList.remove('visible');
    const el = _helpOverlay;
    // Remove after fade-out transition
    setTimeout(() => {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 300);
    _helpOverlay = null;
    if (_helpTimeout) {
      clearTimeout(_helpTimeout);
      _helpTimeout = null;
    }
  }

  document.addEventListener('keydown', (e) => {
    // Show help on '?' when chat input is not focused
    if (e.key === '?' && document.activeElement !== dom.chatInput) {
      e.preventDefault();
      showKeyboardHelp();
    }
    // ESC: cancel recording or clear chat input
    if (e.key === 'Escape') {
      if (state.voiceMode === 'recording') {
        stopRecording();
      } else if (document.activeElement === dom.chatInput) {
        dom.chatInput.value = '';
      }
      dismissKeyboardHelp();
    }
  });

  // ═══════════════════════════════════════════════════════
  //  AUDIO PLAYBACK (with volume normalization)
  // ═══════════════════════════════════════════════════════

  // Shared AudioContext for decoding and normalized playback.
  // Audio chain: source → clipGain (normalization) → compressor → masterGain (volume slider) → dest
  let _playbackCtx = null;
  let _playbackGain = null;
  let _compressor = null;
  const TARGET_RMS = 0.15; // Target RMS level for normalization
  const SILENCE_THRESHOLD = 0.01; // Samples below this are silence

  function getPlaybackContext() {
    if (!_playbackCtx || _playbackCtx.state === 'closed') {
      _playbackCtx = new (window.AudioContext || window.webkitAudioContext)();

      // Dynamics compressor to even out volume spikes within each clip
      _compressor = _playbackCtx.createDynamicsCompressor();
      _compressor.threshold.value = -20;  // dB — compress above this
      _compressor.knee.value = 12;        // dB — soft knee for natural sound
      _compressor.ratio.value = 6;        // 6:1 compression — aggressive for speech
      _compressor.attack.value = 0.003;   // 3ms — fast attack catches transients
      _compressor.release.value = 0.15;   // 150ms — smooth release

      // Master volume controlled by slider
      _playbackGain = _playbackCtx.createGain();
      _playbackGain.gain.value = state.ttsVolume;

      _compressor.connect(_playbackGain);
      _playbackGain.connect(_playbackCtx.destination);
    }
    if (_playbackCtx.state === 'suspended') {
      _playbackCtx.resume().catch(() => { /* browser may block autoplay */ });
    }
    return _playbackCtx;
  }

  function measureActiveRMS(audioBuffer) {
    // Measure RMS of only the non-silent portions of the audio.
    // This avoids silence padding in MP3s from skewing the measurement.
    let sumSq = 0;
    let count = 0;
    for (let ch = 0; ch < audioBuffer.numberOfChannels; ch++) {
      const data = audioBuffer.getChannelData(ch);
      for (let i = 0; i < data.length; i++) {
        const abs = Math.abs(data[i]);
        if (abs > SILENCE_THRESHOLD) {
          sumSq += data[i] * data[i];
          count++;
        }
      }
    }
    // If almost entirely silence, use full-buffer RMS as fallback
    if (count < audioBuffer.length * 0.1) {
      const data = audioBuffer.getChannelData(0);
      sumSq = 0;
      count = data.length;
      for (let i = 0; i < data.length; i++) {
        sumSq += data[i] * data[i];
      }
    }
    return Math.sqrt(sumSq / (count || 1));
  }

  function queueAudioBlob(blob) {
    state.audioQueue.push(blob);
    if (!state.isPlayingAudio) playNextAudio();
  }

  async function playNextAudio() {
    if (state.audioQueue.length === 0) {
      state.isPlayingAudio = false;
      state.currentAudio = null;
      if (state.voiceMode === 'speaking') setVoiceMode('idle');
      return;
    }

    state.isPlayingAudio = true;
    setVoiceMode('speaking');

    const blob = state.audioQueue.shift();

    try {
      const ctx = getPlaybackContext();
      const arrayBuf = await blob.arrayBuffer();
      const audioBuffer = await ctx.decodeAudioData(arrayBuf);

      // Measure RMS of active (non-silent) audio and compute gain
      const rms = measureActiveRMS(audioBuffer);
      const gain = rms > 0.005 ? TARGET_RMS / rms : 1.0;
      // Tight clamp: 0.7x-2.5x — keeps volume within a narrow band
      const clampedGain = Math.min(Math.max(gain, 0.7), 2.5);

      // Update the master volume from slider
      _playbackGain.gain.value = state.ttsVolume;

      // Per-clip gain for normalization → feeds into compressor → master gain
      const clipGain = ctx.createGain();
      clipGain.gain.value = clampedGain;
      clipGain.connect(_compressor);

      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(clipGain);

      state.currentAudio = source;

      source.addEventListener('ended', () => {
        clipGain.disconnect();
        playNextAudio();
      }, { once: true });

      source.start(0);
    } catch (err) {
      // Audio decode/playback error — skip to next queued clip
      playNextAudio();
    }
  }

  // ── Volume control ─────────────────────────────────────

  if (dom.ttsVolume) {
    // Set initial volume label
    const initPct = Math.round(state.ttsVolume * 100);
    if (dom.volumePct) dom.volumePct.textContent = `${initPct}%`;

    dom.ttsVolume.addEventListener('input', (e) => {
      state.ttsVolume = parseFloat(e.target.value);
      // Apply to master gain node immediately
      if (_playbackGain) {
        _playbackGain.gain.value = state.ttsVolume;
      }
      const pct = Math.round(state.ttsVolume * 100);
      if (dom.volumePct) dom.volumePct.textContent = `${pct}%`;
    });
  }

  // ═══════════════════════════════════════════════════════
  //  STATUS POLLING
  // ═══════════════════════════════════════════════════════

  async function pollStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Map server response fields to UI LEDs
      // Server returns: game_connected, whisper_available, chromadb_available, elevenlabs_configured
      setLed(dom.statusSim,     data.game_connected      ? 'green' : 'red');
      setLed(dom.statusWhisper,  data.whisper_available   ? 'green' : 'red');
      setLed(dom.statusChroma,   data.chromadb_available  ? 'green' : 'red');
      // Claude is available if we got a valid status response (API key is loaded)
      setLed(dom.statusClaude,   data.claude_model        ? 'green' : 'amber');

      updateConnectionQuality();
    } catch {
      setLed(dom.statusSim,     'red');
      setLed(dom.statusWhisper,  'red');
      setLed(dom.statusChroma,   'red');
      setLed(dom.statusClaude,   'red');
      updateConnectionQuality();
    }
  }

  // ═══════════════════════════════════════════════════════
  //  CANVAS SIZING
  // ═══════════════════════════════════════════════════════

  function resizeWaveformCanvas() {
    if (!dom.waveformCanvas) return;
    const container = dom.waveformCanvas.parentElement;
    if (!container) return;
    const dpr = window.devicePixelRatio || 1;
    dom.waveformCanvas.width = container.clientWidth * dpr;
    dom.waveformCanvas.height = container.clientHeight * dpr;
    const ctx = dom.waveformCanvas.getContext('2d');
    ctx.scale(dpr, dpr);
    startWaveformLoop();
  }

  // ═══════════════════════════════════════════════════════
  //  TAB VISIBILITY — reconnect on wake from sleep
  // ═══════════════════════════════════════════════════════

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      // Tab became visible — check WebSocket health and reconnect if needed
      if (!state.telemetryWs || state.telemetryWs.readyState > WebSocket.OPEN) {
        connectTelemetry();
      }
      if (!state.chatWs || state.chatWs.readyState > WebSocket.OPEN) {
        connectChat();
      }
      // Re-poll status immediately on wake
      pollStatus();

      // Resume AudioContext if it was suspended by the browser
      if (_playbackCtx && _playbackCtx.state === 'suspended') {
        _playbackCtx.resume().catch(() => { /* browser may block autoplay */ });
      }

      // Restart waveform animation loop
      startWaveformLoop();
    } else {
      // Tab hidden — cancel animation frames to save CPU
      stopWaveformLoop();
    }
  });

  // ═══════════════════════════════════════════════════════
  //  INITIALIZATION
  // ═══════════════════════════════════════════════════════

  function showWelcomeMessage() {
    addSystemMessage('Super Hornet AI Wingman ready.');
    addSystemMessage('Voice: hold SPACE or click MIC. Text: type below.');
  }

  function init() {
    resizeWaveformCanvas();
    window.addEventListener('resize', resizeWaveformCanvas);
    startWaveformLoop();
    showAwaitingTelemetry();

    // Show welcome message before connections
    showWelcomeMessage();

    // Connect WebSockets
    connectTelemetry();
    connectChat();

    // Start status polling
    pollStatus();
    setInterval(pollStatus, STATUS_POLL_MS);

    updateConnectionQuality();
  }

  // Start when DOM is ready (it already is since script is at end of body)
  init();

})();
