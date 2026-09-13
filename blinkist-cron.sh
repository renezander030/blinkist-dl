#!/bin/bash
# Blinkist free daily - cron job
# Downloads free daily book and archives it locally on this machine

set -euo pipefail

NOTIFY="${NOTIFY:-/home/debian/bin/telegram-notify.sh}"   # override for dry runs: NOTIFY=echo
LOG="/var/log/blinkist-daily.log"
# Alert on ANY unexpected failure. $LINENO = the failing line; include the tail
# of the log so the Telegram message says *why*, not just *that* it broke.
# The trailing tr strips Markdown specials (the notifier sends parse_mode=Markdown).
trap 'rc=$?; "$NOTIFY" "⚠️ Blinkist daily FAILED (line $LINENO, rc=$rc). Last log: $(tail -n 3 "$LOG" 2>/dev/null | tr "\n" "|" | tr -d "\`*_[]")"' ERR

BLINKIST_DIR="$HOME/blinkist-app"
OUTPUT_DIR="$BLINKIST_DIR/output"
VENV="${VENV:-$BLINKIST_DIR/venv/bin/python3}"            # override for dry runs
ARCHIVE_DIR="$BLINKIST_DIR/archive"   # permanent local store (was Google Drive)

# Retry window: every RETRY_DELAY seconds for up to MAX_WINDOW_MIN minutes
# (default every 15 min for 6 h), then alert once, quoting the last error line.
RETRY_DELAY="${RETRY_DELAY:-900}"
MAX_WINDOW_MIN="${MAX_WINDOW_MIN:-360}"

ATTEMPT_LOG="$(mktemp -t blinkist-attempt.XXXXXX)"
trap 'rm -f "$ATTEMPT_LOG"' EXIT

echo "[$(date)] Starting Blinkist daily download..."

cd "$BLINKIST_DIR"

# Rich wraps at 80 columns off a TTY; widen so one log line stays one line.
export COLUMNS=250

DOWNLOAD_OK=0
LAST_ERROR=""
START_TS=$(date +%s)
attempt=0
while :; do
    attempt=$((attempt + 1))
    # tee keeps this attempt's output so the alert can quote the actual error.
    if "$VENV" main.py --freedaily --no-cover --no-audio "$OUTPUT_DIR" 2>&1 | tee "$ATTEMPT_LOG"; then
        DOWNLOAD_OK=1
        break
    fi
    LAST_ERROR="$(grep -E 'Error|Exception|unavailable' "$ATTEMPT_LOG" | tail -n 1 | sed 's/\x1b\[[0-9;]*m//g; s/^ *//; s/  */ /g' | cut -c1-200 || true)"
    elapsed_min=$(( ($(date +%s) - START_TS) / 60 ))
    if [ "$elapsed_min" -ge "$MAX_WINDOW_MIN" ]; then
        break
    fi
    echo "[$(date)] Attempt $attempt failed (${LAST_ERROR:-no error line}), retrying in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
done
if [ "$DOWNLOAD_OK" -eq 1 ] && [ "$attempt" -gt 1 ]; then
    echo "[$(date)] Download succeeded on attempt $attempt."
fi

# Archive to permanent local store on this machine. Runs even after a failed
# window so a partial result (one locale downloaded) is kept.
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

if [ "$DOWNLOAD_OK" -ne 1 ]; then
    echo "[$(date)] Failed after $attempt attempts over ${MAX_WINDOW_MIN} min. Last error: ${LAST_ERROR:-unknown}"
    # The retry loop swallows main.py's exit code (it's an `if` condition),
    # so the ERR trap never fires here — notify explicitly before bailing.
    "$NOTIFY" "⚠️ Blinkist daily: download FAILED after $attempt attempts over ${MAX_WINDOW_MIN} min. Last error: $(printf '%s' "${LAST_ERROR:-none captured}" | tr -d '`*_[]'). Check $LOG."
    exit 1
fi

echo "[$(date)] Done."
echo "[$(date)] Blinkist completed successfully"
