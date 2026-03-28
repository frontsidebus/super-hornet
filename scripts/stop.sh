#!/usr/bin/env bash
# =============================================================================
# Super Hornet Shutdown Script
# Gracefully stops all Super Hornet components.
# =============================================================================

CYAN='\033[0;36m'
GREEN='\033[0;32m'
NC='\033[0m'

log() { echo -e "${CYAN}[HORNET]${NC} $1"; }
ok()  { echo -e "${GREEN}[  OK  ]${NC} $1"; }

# Stop web server
log "Stopping web server..."
lsof -ti :3838 2>/dev/null | xargs -r kill 2>/dev/null && ok "Web server stopped" || ok "Web server not running"

# Stop Docker services
log "Stopping Docker services..."
docker compose stop whisper chromadb 2>/dev/null
ok "Docker services stopped"

echo ""
log "All Super Hornet components shut down."
