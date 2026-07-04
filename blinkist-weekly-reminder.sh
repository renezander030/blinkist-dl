#!/bin/bash
# Blinkist Weekly Reminder — tells René what free dailies were archived this week
# and where to find them. Lightweight and unconditional (unlike the insights cron).
# Storage is local now (see blinkist-cron.sh); this reads the archive dir.
# Cron: 0 17 * * 0 (Sunday 17:00 UTC ≈ 19:00 Madrid summer / 18:00 winter)

set -euo pipefail

ARCHIVE_DIR="/home/debian/blinkist-app/archive"
VENV="/home/debian/blinkist-app/venv/bin/python3"
NOTIFY="/home/debian/bin/telegram-notify.sh"
LOG="/var/log/blinkist-weekly-reminder.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG" >&2; }
trap 'rc=$?; "$NOTIFY" --silent "⚠️ Blinkist weekly reminder FAILED (line $LINENO, rc=$rc). Check $LOG"' ERR

log "=== Blinkist Weekly Reminder ==="

# Build the digest body: one entry per book archived in the last 7 days, with
# title, author, a short synopsis, and the on-server path. All dynamic text is
# stripped of Markdown-special chars (telegram-notify.sh forces parse_mode=Markdown)
# and the whole list is capped so the message can't exceed Telegram's 4096 limit.
# First stdout line = count; the rest = the formatted list.
OUT=$("$VENV" - "$ARCHIVE_DIR" <<'PY'
import os, sys, time, re, glob, html
try:
    import yaml
except Exception:
    yaml = None

archive = sys.argv[1]
cutoff = time.time() - 7 * 86400

def clean(s):
    s = re.sub(r'<[^>]+>', '', s or '')      # strip HTML
    s = html.unescape(s)
    s = re.sub(r'[`*_\[\]]', '', s)          # neutralize Markdown specials
    return re.sub(r'\s+', ' ', s).strip()

books = []
for d in sorted(glob.glob(os.path.join(archive, '*'))):
    if not os.path.isfile(os.path.join(d, 'book.md')):
        continue
    if os.path.getmtime(d) < cutoff:
        continue
    slug = os.path.basename(d)
    data = {}
    yml = os.path.join(d, 'book.yaml')
    if yaml and os.path.isfile(yml):
        try:
            data = yaml.safe_load(open(yml, encoding='utf-8')) or {}
        except Exception:
            data = {}
    title = clean(data.get('title')) or slug
    author = clean(data.get('author'))
    syn = clean(data.get('aboutTheBook'))
    if len(syn) > 160:
        syn = syn[:160].rsplit(' ', 1)[0] + '…'
    books.append({'slug': slug, 'title': title, 'author': author, 'syn': syn})

def render(with_syn):
    out = []
    for b in books:
        line = f"• {b['title']}" + (f" — {b['author']}" if b['author'] else "")
        if with_syn and b['syn']:
            line += f"\n  “{b['syn']}”"
        line += f"\n  ~/blinkist-app/archive/{b['slug']}/"
        out.append(line)
    return "\n\n".join(out)

body = render(with_syn=True)
if len(body) > 3600:          # too long for one message → drop synopses
    body = render(with_syn=False)

print(len(books))
print(body)
PY
)

COUNT=$(printf '%s\n' "$OUT" | head -n1)
LIST=$(printf '%s\n' "$OUT" | tail -n +2)

if [ "${COUNT:-0}" -gt 0 ]; then
    MSG="📚 Blinkist — new this week ($COUNT)

$LIST

Read them on the server: ls ~/blinkist-app/archive"
else
    MSG="📚 Blinkist — no new free dailies archived in the last 7 days."
fi

"$NOTIFY" "$MSG"
log "Sent reminder ($COUNT new book(s))."
log "=== Done ==="
