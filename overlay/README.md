# Super Hornet Overlay

Transparent overlay that displays the Super Hornet HUD on top of Star Citizen.

## Prerequisites

- Node.js 18+
- Super Hornet web server running (`http://localhost:3839`)
- Star Citizen running in Borderless or Windowed mode

## Setup

```bash
cd overlay
npm install
npm start
```

## Controls

| Key | Action |
|-----|--------|
| F12 | Toggle overlay visibility |
| F11 | Toggle click-through (interact with overlay vs pass-through to game) |
| Escape | Quit overlay |

## Notes

- Star Citizen must be in **Borderless** or **Windowed** mode (not Exclusive Fullscreen)
- The overlay is click-through by default — press F11 to interact with it
- The web server must be running before starting the overlay
