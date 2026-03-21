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
  const SCROLL_THRESHOLD_PX = 80; // auto-scroll if within this distance of bottom
  const RENDER_BATCH_MS = 32;     // ~2 frames at 60fps for batched DOM updates

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
    ttsVolume:        document.getElementById('tts-volume'),
    connQuality:      document.getElementById('conn-quality'),
    connQualityText:  document.getElementById('conn-quality-text'),
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
    wsMessageBuffer: [],            // backpressure buffer
    wsBufferProcessing: false,
    thinkingMsgEl: null,            // "MERLIN is thinking..." indicator
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
    return el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_THRESHOLD_PX;
  }

  function scrollChatIfNeeded() {
    if (!state.isUserScrolledUp) {
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

  // Cache telemetry value elements for fast lookup (avoid querySelectorAll per update)
  const _telemValueCache = new Map();

  function buildTelemetryDOM() {
    dom.telemetryContent.innerHTML = '';
    _telemValueCache.clear();
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
    // where data contains the raw bridge JSON with nested objects.
    // Flatten it into the display keys expected by TELEM_SECTIONS.
    const d = msg.data || msg;
    if (!d || typeof d !== 'object') return null;

    const pos = d.position || {};
    const spd = d.speeds || {};
    const att = d.attitude || {};
    const eng = d.engines || {};
    const ap  = d.autopilot || {};
    const env = d.environment || {};
    const fuel = d.fuel || {};
    const surf = d.surfaces || {};

    // Get first engine data
    const e1 = (eng.engines && eng.engines[0]) || {};

    const flat = {};
    flat.aircraft_type = d.aircraft || '---';
    flat.flight_phase  = d.flight_phase || '---';

    flat.latitude  = pos.latitude  != null ? pos.latitude.toFixed(4) + '°' : '---';
    flat.longitude = pos.longitude != null ? pos.longitude.toFixed(4) + '°' : '---';
    flat.altitude  = pos.altitude_msl != null ? Math.round(pos.altitude_msl) + ' ft' : '---';
    flat.agl       = pos.altitude_agl != null ? Math.round(pos.altitude_agl) + ' ft' : '---';

    flat.ias     = spd.indicated_airspeed != null ? Math.round(spd.indicated_airspeed) + ' kt' : '---';
    flat.tas     = spd.true_airspeed != null ? Math.round(spd.true_airspeed) + ' kt' : '---';
    flat.gs      = spd.ground_speed != null ? Math.round(spd.ground_speed) + ' kt' : '---';
    flat.vs      = spd.vertical_speed != null ? Math.round(spd.vertical_speed) + ' fpm' : '---';
    flat.heading = att.heading_magnetic != null ? Math.round(att.heading_magnetic) + '°' : '---';

    flat.rpm       = e1.rpm != null ? Math.round(e1.rpm) : '---';
    flat.manifold  = e1.manifold_pressure != null ? e1.manifold_pressure.toFixed(1) + ' inHg' : '---';
    flat.fuel_flow = e1.fuel_flow_gph != null ? e1.fuel_flow_gph.toFixed(1) + ' gph' : '---';
    flat.oil_temp  = e1.oil_temp != null ? Math.round(e1.oil_temp) + '°' : '---';

    flat.ap_status = ap.master ? 'ENGAGED' : 'OFF';
    flat.ap_hdg    = ap.heading != null ? Math.round(ap.heading) + '°' : '---';
    flat.ap_alt    = ap.altitude != null ? Math.round(ap.altitude) + ' ft' : '---';
    flat.ap_vs     = ap.vertical_speed != null ? Math.round(ap.vertical_speed) + ' fpm' : '---';

    flat.wind        = env.wind_speed_kts != null ? Math.round(env.wind_direction) + '°/' + Math.round(env.wind_speed_kts) + 'kt' : '---';
    flat.visibility  = env.visibility_sm != null ? env.visibility_sm.toFixed(1) + ' sm' : '---';
    flat.temperature = env.temperature_c != null ? Math.round(env.temperature_c) + '°C' : '---';
    flat.qnh         = env.barometer_inhg != null ? env.barometer_inhg.toFixed(2) + ' inHg' : '---';

    flat.fuel_total = fuel.total_gallons != null ? fuel.total_gallons.toFixed(1) + ' gal' : '---';

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
        // Flash animation — remove and re-add class
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
    _telemValueCache.clear();
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
      if (!state.telemetryReconnecting) {
        addSystemMessage('Telemetry link established.');
      } else {
        // Reconnect succeeded — single quiet message
        addSystemMessage('Telemetry link restored.');
      }
      state.telemetryReconnecting = false;
      updateConnectionQuality();
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
    const sender = `<span class="sender-merlin">MERLIN:</span> `;
    msg.innerHTML = ts + sender + `<span class="msg-text-merlin thinking-dots">thinking</span>`;
    dom.chatMessages.appendChild(msg);
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
    const sender = `<span class="sender-merlin">MERLIN:</span> `;
    msg.innerHTML = ts + sender + `<span class="msg-text-merlin" data-streaming></span><span class="typing-cursor"></span>`;
    dom.chatMessages.appendChild(msg);
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
        addSystemMessage('MERLIN terminal online.');
      } else {
        addSystemMessage('MERLIN terminal reconnected.');
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
        addSystemMessage('MERLIN terminal disconnected. Reconnecting...');
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
        addChatMessage('MERLIN', raw);
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
        addChatMessage(msg.sender || 'MERLIN', msg.text || '');
        break;

      case 'transcription':
        addChatMessage('CAPTAIN', msg.text || '', {
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
          addChatMessage(msg.sender || 'MERLIN', msg.text || msg.content);
        }
    }
  }

  function sendChatText(text) {
    if (!text.trim()) return;
    if (!state.chatWs || state.chatWs.readyState !== WebSocket.OPEN) {
      addSystemMessage('Cannot send: MERLIN terminal offline.');
      return;
    }
    // Barge-in: stop any playing TTS when user sends text
    bargeIn();
    addChatMessage('CAPTAIN', text);
    setVoiceMode('thinking');
    showThinkingIndicator();
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

  // Barge-in on typing while TTS is playing
  dom.chatInput.addEventListener('input', () => {
    if (state.isPlayingAudio || state.audioQueue.length > 0) {
      bargeIn();
    }
  });

  // ═══════════════════════════════════════════════════════
  //  VOICE INPUT SYSTEM
  // ═══════════════════════════════════════════════════════

  function setVoiceMode(mode) {
    state.voiceMode = mode;
    const btn = dom.pttButton;
    const txt = dom.voiceStatusText;

    btn.classList.remove('recording', 'processing', 'speaking', 'thinking');
    txt.classList.remove('recording', 'processing', 'speaking', 'thinking');

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
      case 'thinking':
        btn.classList.add('thinking');
        txt.classList.add('thinking');
        txt.textContent = 'MERLIN THINKING...';
        break;
      case 'speaking':
        btn.classList.add('speaking');
        txt.classList.add('speaking');
        txt.textContent = 'MERLIN SPEAKING';
        break;
      default:
        txt.textContent = 'IDLE';
    }
  }

  async function startRecording() {
    if (state.voiceMode === 'recording') return;

    // Barge-in: stop TTS if MERLIN is speaking
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
  //  AUDIO PLAYBACK (with volume normalization)
  // ═══════════════════════════════════════════════════════

  // Shared AudioContext for decoding and normalized playback
  let _playbackCtx = null;
  let _playbackGain = null;
  const TARGET_RMS = 0.18; // Target RMS level for normalization

  function getPlaybackContext() {
    if (!_playbackCtx || _playbackCtx.state === 'closed') {
      _playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
      _playbackGain = _playbackCtx.createGain();
      _playbackGain.gain.value = state.ttsVolume;
      _playbackGain.connect(_playbackCtx.destination);
    }
    if (_playbackCtx.state === 'suspended') {
      _playbackCtx.resume();
    }
    return _playbackCtx;
  }

  function measureRMS(audioBuffer) {
    // Measure RMS across all channels
    let sumSq = 0;
    let count = 0;
    for (let ch = 0; ch < audioBuffer.numberOfChannels; ch++) {
      const data = audioBuffer.getChannelData(ch);
      for (let i = 0; i < data.length; i++) {
        sumSq += data[i] * data[i];
      }
      count += data.length;
    }
    return Math.sqrt(sumSq / (count || 1));
  }

  function queueAudioBlob(blob) {
    state.audioQueue.push(blob);
    if (!state.isPlayingAudio) playNextAudio();
  }

  function queueTTS(text) {
    // Fallback: fetch TTS via REST if not streamed over WebSocket
    if (!text || !text.trim()) return;
    fetch(`${API_BASE}/api/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
      .then(r => r.blob())
      .then(blob => queueAudioBlob(blob))
      .catch(err => console.warn('TTS fetch error:', err));
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

      // Measure RMS and compute normalization gain
      const rms = measureRMS(audioBuffer);
      const gain = rms > 0.001 ? TARGET_RMS / rms : 1.0;
      // Clamp gain to avoid blowing out quiet clips or clipping loud ones
      const clampedGain = Math.min(Math.max(gain, 0.5), 4.0);

      // Update the master volume from slider
      _playbackGain.gain.value = state.ttsVolume;

      // Create a per-clip gain node for normalization
      const clipGain = ctx.createGain();
      clipGain.gain.value = clampedGain;
      clipGain.connect(_playbackGain);

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
      console.warn('Audio decode/playback error:', err);
      playNextAudio();
    }
  }

  // ── Volume control ─────────────────────────────────────

  if (dom.ttsVolume) {
    dom.ttsVolume.addEventListener('input', (e) => {
      state.ttsVolume = parseFloat(e.target.value);
      // Apply to master gain node immediately
      if (_playbackGain) {
        _playbackGain.gain.value = state.ttsVolume;
      }
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
      // Server returns: sim_connected, whisper_available, chromadb_available, elevenlabs_configured
      setLed(dom.statusSim,     data.sim_connected       ? 'green' : 'red');
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
    addSystemMessage('Spacebar = push-to-talk (when chat input not focused).');
    updateConnectionQuality();
  }

  // Start when DOM is ready (it already is since script is at end of body)
  init();

})();
