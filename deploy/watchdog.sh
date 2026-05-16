#!/bin/bash
# =============================================================================
# XAU/USD Trading System — Watchdog Process
# =============================================================================
# Monitors the trading system heartbeat file.
# If no heartbeat for > max_stale_seconds, triggers alert and restart.
#
# Install: cp deploy/watchdog.sh /usr/local/bin/xau-watchdog
#          chmod +x /usr/local/bin/xau-watchdog
# Cron:    */1 * * * * /usr/local/bin/xau-watchdog
# =============================================================================

HEARTBEAT_FILE="/tmp/xau_heartbeat"
MAX_STALE_SECONDS=90
LOG_FILE="/var/log/xau_watchdog.log"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
SYSTEMD_SERVICE="xau-trading"

log() {
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" | tee -a "$LOG_FILE"
}

send_telegram() {
    if [[ -n "$TELEGRAM_BOT_TOKEN" && -n "$TELEGRAM_CHAT_ID" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=$1" \
            -d "parse_mode=HTML" \
            > /dev/null 2>&1 || true
    fi
}

# Check if heartbeat file exists
if [[ ! -f "$HEARTBEAT_FILE" ]]; then
    log "ERROR: Heartbeat file $HEARTBEAT_FILE does not exist"
    send_telegram "🔴 <b>WATCHDOG:</b> Heartbeat file missing! System may be down."
    
    # Touch it so we don't spam
    touch "$HEARTBEAT_FILE"
    exit 1
fi

# Get file age in seconds
NOW=$(date +%s)
FILE_MTIME=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo "$NOW")
STALE_SECONDS=$((NOW - FILE_MTIME))

if [[ $STALE_SECONDS -gt $MAX_STALE_SECONDS ]]; then
    log "CRITICAL: Heartbeat stale for ${STALE_SECONDS}s (max: ${MAX_STALE_SECONDS}s)"
    
    send_telegram "🔴 <b>WATCHDOG:</b> Trading system heartbeat STALE (${STALE_SECONDS}s). Attempting restart..."
    
    # Try restart via systemd
    if systemctl is-active --quiet "$SYSTEMD_SERVICE" 2>/dev/null; then
        log "Restarting $SYSTEMD_SERVICE via systemd"
        systemctl restart "$SYSTEMD_SERVICE" 2>&1 | tee -a "$LOG_FILE"
        
        if systemctl is-active --quiet "$SYSTEMD_SERVICE"; then
            log "Restart successful"
            send_telegram "🟢 <b>WATCHDOG:</b> System restarted successfully."
        else
            log "Restart FAILED"
            send_telegram "🔴 <b>WATCHDOG:</b> System restart FAILED! Manual intervention required."
        fi
    else
        log "WARNING: systemd service $SYSTEMD_SERVICE not found or not active"
        send_telegram "🟡 <b>WATCHDOG:</b> Cannot restart — systemd service not found."
    fi
else
    # All good — heartbeat is fresh
    log "OK: Heartbeat fresh (${STALE_SECONDS}s ago)"
fi
