#!/bin/bash
# Blinkist free daily - cron job
# Downloads free daily book and archives it locally on this machine

set -euo pipefail

NOTIFY="/home/debian/bin/telegram-notify.sh"
LOG="/var/log/blinkist-daily.log"
# Alert on ANY unexpected failure. $LINENO = the failing line; include the tail
# of the log so the Telegram message says *why*, not just *that* it broke.
trap 'rc=$?; "$NOTIFY" "⚠️ Blinkist daily FAILED (line $LINENO, rc=$rc). Last log: $(tail -n 3 "$LOG" 2>/dev/null | tr "\n" "|")"' ERR

BLINKIST_DIR="$HOME/blinkist-app"
OUTPUT_DIR="$BLINKIST_DIR/output"
VENV="$BLINKIST_DIR/venv/bin/python3"
ARCHIVE_DIR="$BLINKIST_DIR/archive"   # permanent local store (was Google Drive)

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
        # The retry loop swallows main.py's exit code (it's an `if` condition),
        # so the ERR trap never fires here — notify explicitly before bailing.
        # This is the gap that hid the 3-month scraper breakage.
        "$NOTIFY" "⚠️ Blinkist daily: download FAILED after $MAX_RETRIES attempts. Free-daily scraper may be broken again — check $LOG."
        exit 1
    fi
    echo "[$(date)] Attempt $attempt failed (Cloudflare?), retrying in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
done

# Archive to permanent local store on this machine.
# Copy completed book dirs (those with book.md); skip .tmp partials; never clobber.
mkdir -p "$ARCHIVE_DIR"
ARCHIVED=0
for dir in "$OUTPUT_DIR"/*/; do
    [ -d "$dir" ] || continue
    [ -f "${dir}book.md" ] || continue          # only finished downloads
    dest="$ARCHIVE_DIR/$(basename "$dir")"
    if [ ! -e "$dest" ]; then
        cp -a "$dir" "$dest"
        ARCHIVED=$((ARCHIVED + 1))
    fi
done
echo "[$(date)] Archived $ARCHIVED new book(s) to $ARCHIVE_DIR."

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
