// ═══════════════════════════════════════════════════════════
//  SUPER HORNET // Transparent Overlay — Electron Main Process
// ═══════════════════════════════════════════════════════════

const { app, BrowserWindow, globalShortcut, screen, ipcMain } = require('electron');
const path = require('path');
const http = require('http');

const OVERLAY_URL = 'http://localhost:3839?overlay=true';
const RETRY_INTERVAL_MS = 3000;

let win = null;
let clickThrough = true;
let visible = true;
let retryTimer = null;

// ── Helpers ────────────────────────────────────────────────

function checkServer(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 400);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(2000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function sendState() {
  if (win && !win.isDestroyed()) {
    win.webContents.send('overlay-state', {
      clickThrough,
      visible,
    });
  }
}

function setClickThrough(enabled) {
  clickThrough = enabled;
  if (win && !win.isDestroyed()) {
    if (clickThrough) {
      win.setIgnoreMouseEvents(true, { forward: true });
    } else {
      win.setIgnoreMouseEvents(false);
    }
  }
  sendState();
}

// ── Window creation ────────────────────────────────────────

function createWindow() {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.bounds;

  win = new BrowserWindow({
    x: 0,
    y: 0,
    width,
    height,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    backgroundColor: '#00000000',
    hasShadow: false,
    resizable: false,
    focusable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Start in click-through mode
  win.setIgnoreMouseEvents(true, { forward: true });

  // Prevent the window from being captured in Alt-Tab on some systems
  win.setAlwaysOnTop(true, 'screen-saver');

  loadWithRetry();
}

function loadWithRetry() {
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }

  checkServer(OVERLAY_URL).then((ok) => {
    if (ok) {
      win.loadURL(OVERLAY_URL);
    } else {
      console.log(`[overlay] Web server not available, retrying in ${RETRY_INTERVAL_MS / 1000}s...`);
      retryTimer = setTimeout(loadWithRetry, RETRY_INTERVAL_MS);
    }
  });
}

// ── App lifecycle ──────────────────────────────────────────

app.whenReady().then(() => {
  createWindow();

  // F12 — toggle visibility
  globalShortcut.register('F12', () => {
    if (!win || win.isDestroyed()) return;
    visible = !visible;
    if (visible) {
      win.show();
    } else {
      win.hide();
    }
    sendState();
  });

  // F11 — toggle click-through
  globalShortcut.register('F11', () => {
    setClickThrough(!clickThrough);
  });

  // Escape — quit
  globalShortcut.register('Escape', () => {
    app.quit();
  });
});

// IPC: renderer can request click-through toggle
ipcMain.on('toggle-click-through', () => {
  setClickThrough(!clickThrough);
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (retryTimer) clearTimeout(retryTimer);
});

app.on('window-all-closed', () => {
  app.quit();
});
