#!/usr/bin/env bash
# Blinkist Weekly Insights — cross-references last week's free dailies with TickTick goals
# Sends findings to Telegram if any book connects to active goals/tasks
# Cron: 30 5 * * 0 (Sunday 5:30 UTC = 6:30/7:30 Madrid)

set -uo pipefail

CLAUDE_BIN="/home/debian/.local/bin/claude"
CLAUDE_WORK_DIR="/home/debian/claude"
NOTIFY="/home/debian/bin/telegram-notify.sh"
LOG="/var/log/blinkist-weekly-insights.log"
OUTPUT_DIR="/home/debian/blinkist-app/output"
TODAY=$(date +%F)

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG" >&2; }

trap '$NOTIFY --silent "Blinkist Weekly Insights FAILED ($TODAY). Check $LOG"' ERR

log "=== Blinkist Weekly Insights — $TODAY ==="

# Collect this week's book summaries (downloaded in last 7 days)
BOOK_SUMMARIES=""
BOOK_COUNT=0
for dir in "$OUTPUT_DIR"/*/; do
    [ -d "$dir" ] || continue
    # Only include books from the last 7 days
    if [ -z "$(find "$dir" -maxdepth 0 -mtime +7 -print 2>/dev/null)" ]; then
        BOOK_MD="$dir/book.md"
        BOOK_YAML="$dir/book.yaml"
        if [ -f "$BOOK_MD" ]; then
            SLUG=$(basename "$dir")
            # Extract title and categories from yaml if available
            TITLE=""
            CATEGORIES=""
            if [ -f "$BOOK_YAML" ]; then
                TITLE=$(grep "^title:" "$BOOK_YAML" | head -1 | sed 's/^title: //')
                CATEGORIES=$(grep "^  title:" "$BOOK_YAML" | sed 's/^  title: //' | tr '\n' ', ')
            fi
            # Get synopsis (first ~500 chars of the md)
            SYNOPSIS=$(head -c 1500 "$BOOK_MD")
            BOOK_SUMMARIES+="
---
BOOK: $TITLE ($SLUG)
CATEGORIES: $CATEGORIES
CONTENT:
$SYNOPSIS
...
"
            BOOK_COUNT=$((BOOK_COUNT + 1))
        fi
    fi
done

if [ "$BOOK_COUNT" -eq 0 ]; then
    log "No books from last 7 days found. Skipping."
    exit 0
fi

log "Found $BOOK_COUNT books from last 7 days."

PROMPT='Blinkist Weekly Insights for René. Today is '"$TODAY"'.

You have access to TickTick via MCP tools (mcp__ticktick__*). Your job:

1. Read the book summaries below (this week'\''s Blinkist free dailies)
2. For EACH book, use mcp__ticktick__search_tasks_semantic to find matching tasks/goals. Extract the key themes and topics from each book and run 2-3 semantic searches per book using those themes as queries (e.g. for a book on leadership, search "leadership career development", "executive management skills", etc.). Use include_content=true and limit=10 to get enough context.
3. For each book, analyze: Does this book'\''s knowledge directly help with any of the matched tasks or goals? Be specific about WHICH task/goal and HOW the knowledge applies.
4. Only report MEANINGFUL connections — not vague "this could be useful" but concrete "this book teaches X which directly applies to your task Y because Z"

## Output Format
Produce a concise Telegram message (max 2000 chars). Structure:

📚 **Blinkist Weekly Insights** (CW <week_number>)

For each book with a meaningful connection:
📖 *<Book Title>* → <Goal/Task it connects to>
<1-2 sentences: what specific knowledge to extract and how to apply it>

If a book has no meaningful connection to any goal, skip it entirely.

End with a short "💡 Recommendation:" line suggesting the single most actionable book to revisit this week and why.

If NO books have meaningful connections, say so briefly — do not force connections.

## This Week'\''s Books

'"$BOOK_SUMMARIES"'
'

log "Calling Claude for analysis..."

RESULT=$(cd "$CLAUDE_WORK_DIR" && env -i \
    HOME=/home/debian \
    PATH="/home/debian/.local/bin:/home/debian/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/usr/bin:/bin" \
    SHELL=/bin/bash \
    USER=debian \
    timeout 300 "$CLAUDE_BIN" \
    -p --model opus \
    --permission-mode bypassPermissions \
    --max-budget-usd 1 \
    "$PROMPT" 2>>"$LOG")

log "Claude returned ${#RESULT} chars"

if [ -n "$RESULT" ]; then
    $NOTIFY "$RESULT"
    log "Sent insights to Telegram."
else
    log "Empty result from Claude, skipping notification."
fi

log "=== Done ==="
