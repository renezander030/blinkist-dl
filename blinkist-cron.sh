#\!/bin/bash
# Blinkist free daily - cron job
# Downloads free daily book and syncs to Google Drive

set -euo pipefail
trap '$HOME/bin/telegram-notify.sh --silent "Blinkist cron failed. Check /var/log/blinkist-daily.log"' ERR

BLINKIST_DIR="$HOME/blinkist-app"
OUTPUT_DIR="$BLINKIST_DIR/output"
VENV="$BLINKIST_DIR/venv/bin/python3"
GDRIVE_PATH=""

echo "[$(date)] Starting Blinkist daily download..."

cd "$BLINKIST_DIR"

MAX_RETRIES=5
RETRY_DELAY=300  # 5 minutes between retries
for attempt in $(seq 1 $MAX_RETRIES); do
    if "$VENV" main.py --freedaily --no-cover --no-audio "$OUTPUT_DIR"; then
        break
    fi
    if [ "$attempt" -eq "$MAX_RETRIES" ]; then
        echo "[$(date)] Failed after $MAX_RETRIES attempts"
        exit 1
    fi
    echo "[$(date)] Attempt $attempt failed (Cloudflare?), retrying in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
done

# Sync to Google Drive
if command -v rclone &>/dev/null && rclone listremotes | grep -q "gdrive:"; then
    echo "[$(date)] Syncing to Google Drive..."
    rclone copy "$OUTPUT_DIR" "gdrive:$GDRIVE_PATH" --verbose
    echo "[$(date)] Sync complete."
else
    echo "[$(date)] WARNING: rclone/gdrive not configured, skipping sync."
fi

# Clean up downloads older than 7 days
CLEANED=0
for dir in "$OUTPUT_DIR"/*/; do
    [ -d "$dir" ] || continue
    if [ "$(find "$dir" -maxdepth 0 -mtime +7 -print 2>/dev/null)" ]; then
        echo "[$(date)] Removing old download: $(basename "$dir")"
        rm -rf "$dir"
        CLEANED=$((CLEANED + 1))
    fi
done
[ "$CLEANED" -gt 0 ] && echo "[$(date)] Cleaned $CLEANED old downloads (>7 days)."

echo "[$(date)] Done."
echo "[$(date)] Blinkist completed successfully" >> /var/log/blinkist-daily.log
