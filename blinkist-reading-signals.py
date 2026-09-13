#!/usr/bin/env python3
"""
Blinkist Reading Signals — weekly.

Finds where knowledge in the new Blinkist free-daily summaries creates concrete
opportunities for RENE's recently-changed TickTick tasks, and how each advances a
laid-out project goal or the overall mission (from the 🧭 Steering Goal Map +
trunk targets). Uses the existing task vector index (ats hybrid/find) for retrieval.

Delivery mirrors the steering layer: ONE dated NOTE in 🧭 Steering holds the full
picture; a Resend email carries the overview and a deep-link button to that note.
No Telegram.

    blinkist-reading-signals.py            # dry-run: prints, writes nothing
    blinkist-reading-signals.py --commit   # create Steering note + send email
"""
import os, sys, re, json, glob, html as _html, time, subprocess, datetime, shutil

# cron runs with PATH=/usr/bin:/bin, so a bare "ats" raises FileNotFoundError and every
# lookup silently returns []. Resolve it once, explicitly.
ATS_BIN = os.environ.get("ATS_BIN") or shutil.which("ats") or "/usr/local/bin/ats"
import urllib.request, urllib.error, argparse

ARCHIVE_DIR  = "/home/debian/blinkist-app/archive"
STEERING_MAP = "/home/debian/claude/steering/trunk-steering-map.json"
RESEND_ENV   = "/home/debian/.config/resend.env"
ATS_TOKENS   = "/home/debian/.config/ats/tokens.json"
GOAL_MAP_ID  = "6a366e6b8f0881db88a139a9"      # 🎯 Goal Map note, in 🧭 Steering
CLAUDE_BIN   = "/home/debian/.local/bin/claude"
LOG          = "/var/log/blinkist-reading-signals.log"
BOOK_DAYS    = 7          # analyse books archived in the last week
TASK_DAYS    = 28         # against tasks changed in the last 4 weeks
CAND_PER_Q   = 8          # ats candidates per query
MAX_MATCH    = 6          # matched tasks kept per book (best first)

def log(msg):
    line = "[%s] %s" % (datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), msg)
    sys.stderr.write(line + "\n")
    try:
        open(LOG, "a").write(line + "\n")
    except Exception:
        pass

# ---------------------------------------------------------------- helpers (steer.py-compatible)
def load_env(path):
    out = {}
    try:
        for ln in open(path):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out

def open_token():
    return json.load(open(ATS_TOKENS)).get("accessToken")

def http_json(url, data=None, headers=None, method=None, retries=1):
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method or ("POST" if data is not None else "GET"))
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries - 1:   # retry transient 5xx only
                raise
            last = e
        except urllib.error.URLError as e:
            if attempt == retries - 1:
                raise
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last

def open_get(path):
    # GETs are safe to retry; the TickTick Open API 5xx's transiently.
    return http_json("https://api.ticktick.com/open/v1" + path,
                     headers={"Authorization": "Bearer " + open_token()}, retries=3)

def create_note(project_id, title, content):
    """One NOTE-kind item in 🧭 Steering (Open API kind=NOTE). Returns id or None."""
    H = {"Authorization": "Bearer " + open_token()}
    try:
        created = http_json("https://api.ticktick.com/open/v1/task",
                            data={"projectId": project_id, "title": title, "content": content, "kind": "NOTE"},
                            headers=H)
        tid = created.get("id")
        if tid:  # ensure NOTE kind (proven convert path from steer.py)
            http_json("https://api.ticktick.com/open/v1/task/%s" % tid,
                      data={"id": tid, "projectId": project_id, "kind": "NOTE"}, headers=H, method="POST")
        return tid
    except urllib.error.HTTPError as e:
        log("note create failed %s %s" % (e.code, e.read().decode()[:200]))
        return None

def note_link(project_id, note_id):
    return "https://ticktick.com/webapp/#p/%s/tasks/%s" % (project_id, note_id) if note_id else None

def send_email(cfg, subject, html_body):
    key = load_env(RESEND_ENV).get("RESEND_API_KEY")
    if not key:
        return "no resend key"
    payload = {"from": cfg["email_from"], "to": [cfg["email_to"]], "subject": subject, "html": html_body}
    try:
        http_json("https://api.resend.com/emails", data=payload, headers={"Authorization": "Bearer " + key})
        return "sent"
    except urllib.error.HTTPError as e:
        return "email failed %s %s" % (e.code, e.read().decode()[:200])

# ---------------------------------------------------------------- gather: books
def _clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def recent_books(days):
    cutoff = time.time() - days * 86400
    try:
        import yaml
    except Exception:
        yaml = None
    books = []
    for d in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*"))):
        md = os.path.join(d, "book.md")
        if not os.path.isfile(md) or os.path.getmtime(d) < cutoff:
            continue
        data = {}
        yml = os.path.join(d, "book.yaml")
        if yaml and os.path.isfile(yml):
            try:
                data = yaml.safe_load(open(yml, encoding="utf-8")) or {}
            except Exception:
                data = {}
        title = _clean(data.get("title")) or os.path.basename(d)
        # a compact "what this book teaches" blob: synopsis + chapter action-titles
        synopsis = _clean(data.get("aboutTheBook"))
        chapter_titles = []
        for ch in (data.get("chapters") or []):
            t = _clean(ch.get("action_title"))
            if t:
                chapter_titles.append(t)
        books.append({
            "slug": os.path.basename(d),
            "title": title,
            "author": _clean(data.get("author")),
            "subtitle": _clean(data.get("subtitle")),
            "synopsis": synopsis,
            "chapter_titles": chapter_titles,
        })
    return books

# ---------------------------------------------------------------- gather: recently-changed tasks
def recent_tasks(days):
    cut = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    by_id = {}
    projects = open_get("/project")
    for p in projects:
        pid, pname = p.get("id"), p.get("name", "")
        try:
            data = open_get("/project/%s/data" % pid)
        except Exception as e:
            log("project data fail %s: %s" % (pname[:20], e))
            continue
        for t in data.get("tasks", []):
            mt = t.get("modifiedTime")
            if not mt or mt[:19] < cut:
                continue
            if t.get("status", 0) != 0:            # skip completed
                continue
            if (t.get("kind") or "TEXT") == "NOTE":  # match real tasks only, not notes
                continue
            by_id[t["id"]] = {
                "id": t["id"], "title": _clean(t.get("title")), "project": pname,
                "projectId": pid, "modified": mt[:10],
                "content": _clean(t.get("content"))[:400],
                "tags": t.get("tags", []),
            }
    return by_id

# ---------------------------------------------------------------- vector match (ats hybrid)
ATS_STATS = {"ok": 0, "fail": 0}

def ats_candidates(query, k=CAND_PER_Q):
    try:
        out = subprocess.run([ATS_BIN, "hybrid", query, "--json"],
                             capture_output=True, text=True, timeout=60)
        j = json.loads(out.stdout or "{}")
        ATS_STATS["ok"] += 1
        return [t["id"] for t in j.get("tasks", [])[:k] if t.get("id")]
    except Exception as e:
        ATS_STATS["fail"] += 1
        log("ats hybrid fail %r: %s" % (query[:40], e))
        return []

def match_books_to_tasks(books, recent):
    for b in books:
        queries = [q for q in [
            (b["title"] + " " + b["subtitle"]).strip(),
            b["synopsis"][:160],
        ] if q]
        seen, matched = set(), []
        for q in queries:
            for tid in ats_candidates(q):
                if tid in recent and tid not in seen:
                    seen.add(tid)
                    matched.append(recent[tid])
        b["matches"] = matched[:MAX_MATCH]
    return books

# ---------------------------------------------------------------- goal / mission context
def goal_context(steering_pid):
    parts = []
    try:
        gm = open_get("/project/%s/task/%s" % (steering_pid, GOAL_MAP_ID))
        parts.append("## Goal Map\n" + _clean(gm.get("content"))[:1200])
    except Exception as e:
        log("goal map fetch fail: %s" % e)
    try:
        tm = json.load(open(STEERING_MAP))
        lines = []
        for t in tm.get("trunks", []):
            tgt = t.get("target") or ""
            if tgt:
                lines.append("- %s: %s" % (t.get("name", "?"), tgt))
        if lines:
            parts.append("## Project goals (trunk targets)\n" + "\n".join(lines))
    except Exception as e:
        log("trunk map fail: %s" % e)
    return "\n\n".join(parts)

# ---------------------------------------------------------------- Claude analysis
PROMPT_TMPL = """You are RENE's strategy analyst. Today is {today}.

RENE reads a Blinkist book summary most days. Your job: find where a book's ideas
create a CONCRETE, ACTIONABLE opportunity for one of his recently-changed TickTick
tasks, and explain how acting on it advances a laid-out project goal or his overall
mission. Be strict — report only genuinely meaningful connections, not "this could
be loosely relevant". If a book connects to nothing, drop it. Zero is a valid answer.

# RENE's goals & mission
{goals}

# This week's Blinkist summaries and their semantically-matched recent tasks
{books}

# Output — STRICT JSON, nothing else, no markdown fence:
{{
  "opportunity_count": <int>,
  "email_overview_md": "<plain-text/markdown overview for an email: 1 sentence framing, then one '- ' bullet per opportunity in the form: <Book> -> <Task> — <the single most valuable action, one line>. If none, one honest sentence.>",
  "note_markdown": "<full markdown for a TickTick note. For each opportunity: a '## <Book Title>' heading, then: **Task:** <title> (<project>); **Opportunity:** 2-3 sentences on the specific knowledge to extract and how to apply it to that task; **Advances:** which project goal/mission and why it matters. End with a '## Scanned' line listing books/tasks considered. If no opportunities, still write a short note saying so and list what was scanned.>"
}}
"""

def build_prompt(goals, books):
    blocks = []
    for b in books:
        m = "\n".join("    - [%s] %s (%s)%s" % (
            t["modified"], t["title"], t["project"],
            (" — " + t["content"][:120]) if t["content"] else "")
            for t in b["matches"]) or "    (no recently-changed task matched)"
        blocks.append(
            "### %s%s\n%s%sMatched recent tasks:\n%s" % (
                b["title"],
                (" — " + b["author"]) if b["author"] else "",
                ("Subtitle: %s\n" % b["subtitle"]) if b["subtitle"] else "",
                ("Teaches: %s\n" % b["synopsis"][:400]) if b["synopsis"] else "",
                m))
    return PROMPT_TMPL.format(today=datetime.date.today().isoformat(),
                              goals=goals or "(none provided)",
                              books="\n\n".join(blocks))

def run_claude(prompt, attempts=3):
    cenv = {"HOME": "/home/debian", "USER": "debian", "SHELL": "/bin/bash",
            "PATH": "/home/debian/.local/bin:/home/debian/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/usr/bin:/bin"}
    try:
        out = subprocess.run(
            [CLAUDE_BIN, "-p", "--model", "opus", "--permission-mode", "bypassPermissions",
             "--max-budget-usd", "1", prompt],
            capture_output=True, text=True, env=cenv, cwd="/home/debian/claude", timeout=300)
    except subprocess.TimeoutExpired:
        log("claude timeout"); return None
    raw = out.stdout.strip()
    if not raw:
        log("claude empty (stderr: %s)" % out.stderr[:200])
        if attempts > 1:
            time.sleep(60)
            log("empty output - retrying analysis (%d left)" % (attempts - 1))
            return run_claude(prompt, attempts - 1)
        return None
    m = re.search(r"\{.*\}", raw, re.S)      # tolerate stray prose around the JSON
    if not m:
        log("no JSON in claude output: %s" % raw[:200])
        if attempts > 1 and re.search(r"(429|500|502|503|529|overloaded|rate.?limit)", raw, re.I):
            time.sleep(60)
            log("transient API error - retrying analysis (%d left)" % (attempts - 1))
            return run_claude(prompt, attempts - 1)
        return None
    try:
        return json.loads(m.group(0))
    except Exception as e:
        log("claude JSON parse fail: %s :: %s" % (e, m.group(0)[:200])); return None

# ---------------------------------------------------------------- email
def email_html(overview_md, link, title, count):
    # overview_md is light markdown; convert the '- ' bullets + newlines to minimal HTML
    esc = _html.escape(overview_md)
    lines = []
    for ln in esc.split("\n"):
        s = ln.strip()
        if s.startswith("- "):
            lines.append("<li>%s</li>" % s[2:])
        elif s:
            lines.append("<p style='margin:0 0 10px'>%s</p>" % s)
    body = re.sub(r"(<li>.*?</li>)+", lambda m: "<ul style='margin:0 0 12px;padding-left:20px'>%s</ul>" % m.group(0),
                  "".join(lines), flags=re.S)
    btn = ("<a href='%s' style='display:inline-block;background:#00A67E;color:#fff;"
           "text-decoration:none;padding:11px 20px;border-radius:8px;font-weight:600'>"
           "Open the full picture in TickTick →</a>" % link) if link else ""
    return """<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
max-width:620px;margin:0 auto;color:#1a1a1a;line-height:1.5">
  <div style="font-size:13px;color:#888;letter-spacing:.04em;text-transform:uppercase">🧭 Reading Signals</div>
  <h1 style="font-size:20px;margin:4px 0 4px">%s</h1>
  <div style="font-size:14px;color:#666;margin-bottom:16px">%d opportunity(s) this week</div>
  <div style="font-size:15px">%s</div>
  <div style="margin:20px 0 8px">%s</div>
  <div style="font-size:12px;color:#aaa;margin-top:18px">The note holds the full picture: task, opportunity, and the goal/mission it advances.</div>
</div>""" % (_html.escape(title), count, body, btn)

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="create Steering note + send email (default: dry-run)")
    args = ap.parse_args()

    cfg = json.load(open(STEERING_MAP))["config"]
    steering_pid = cfg["steering_project_id"]

    books = recent_books(BOOK_DAYS)
    log("books in last %dd: %d (%s)" % (BOOK_DAYS, len(books), ", ".join(b["title"][:24] for b in books)))
    if not books:
        log("no new books; nothing to do"); return

    recent = recent_tasks(TASK_DAYS)
    log("recently-changed tasks (%dd): %d" % (TASK_DAYS, len(recent)))
    match_books_to_tasks(books, recent)
    total_matches = sum(len(b["matches"]) for b in books)
    log("book→task candidate matches: %d" % total_matches)
    log("ats retrieval: %d ok, %d failed" % (ATS_STATS["ok"], ATS_STATS["fail"]))
    if ATS_STATS["ok"] == 0:
        log("ABORT: every ats hybrid call failed (%d) - retrieval is down, so a zero here "
            "would be an artefact, not a reading result. Not writing a note." % ATS_STATS["fail"])
        sys.exit(2)

    goals = goal_context(steering_pid)
    prompt = build_prompt(goals, books)
    result = run_claude(prompt)
    if not result:
        log("FAILED: no analysis produced"); sys.exit(1)

    count = int(result.get("opportunity_count", 0))
    title = "%s Reading Signals" % datetime.date.today().isoformat()
    note_md = result.get("note_markdown") or "_No analysis body._"
    overview = result.get("email_overview_md") or "No strong connections this week."

    if not args.commit:
        print("=== TITLE ===\n" + title)
        print("\n=== EMAIL OVERVIEW ===\n" + overview)
        print("\n=== NOTE (full picture) ===\n" + note_md[:2000])
        print("\n[dry-run] opportunity_count=%d — nothing written. Re-run with --commit." % count)
        return

    nid = create_note(steering_pid, title, note_md)
    link = note_link(steering_pid, nid)
    subject = "🧭 Reading Signals — %d opportunity(s) — %s" % (count, datetime.date.today().isoformat())
    em = send_email(cfg, subject, email_html(overview, link, title, count))
    log("[committed] note=%s email=%s opportunities=%d" % (nid or "FAILED", em, count))

if __name__ == "__main__":
    main()
