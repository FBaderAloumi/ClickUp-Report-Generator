# -*- coding: utf-8 -*-
"""
ClickUp Report Generator
========================
Run:  pip install flask requests
Then: python clickup_report.py
Opens automatically at http://localhost:5000
"""

from flask import Flask, render_template_string, request, jsonify
from datetime import datetime, timezone
import requests

app = Flask(__name__)
BASE = "https://api.clickup.com/api/v2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hdr(api_key):
    return {"Authorization": api_key, "Content-Type": "application/json"}


def ms_to_duration(ms):
    if not ms:
        return None
    ms = int(ms)
    if ms <= 0:
        return None
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def fmt_date(ts_ms):
    if not ts_ms:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        return dt.strftime("%b %d, %Y")
    except Exception:
        return None


def to_ts(date_str, end_of_day=False):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp() * 1000)
    return ts + 86399999 if end_of_day else ts


def parse_cf_value(cf):
    """Return a human-readable string for any ClickUp custom field value."""
    cf_type = cf.get("type", "")
    val = cf.get("value")
    if val is None or val == "":
        return None

    opts = cf.get("type_config", {}).get("options", [])

    if cf_type in ("text", "short_text", "url", "email", "phone"):
        return str(val).strip() or None

    if cf_type == "number":
        try:
            n = float(val)
            return str(int(n)) if n == int(n) else str(n)
        except Exception:
            return str(val)

    if cf_type == "currency":
        try:
            symbol = cf.get("type_config", {}).get("currency_type", "$")
            return f"{symbol}{float(val):,.2f}"
        except Exception:
            return str(val)

    if cf_type == "checkbox":
        return "Yes" if val else "No"

    if cf_type == "date":
        return fmt_date(val)

    if cf_type == "drop_down":
        if opts:
            match = next(
                (o["name"] for o in opts if o.get("orderindex") == val or o.get("id") == val),
                None,
            )
            return match or str(val)
        return str(val)

    if cf_type == "label":
        if isinstance(val, list) and opts:
            names = [o["name"] for o in opts if o.get("id") in val]
            return ", ".join(names) or None
        return str(val) if val else None

    if cf_type == "people":
        if isinstance(val, list):
            names = []
            for u in val:
                if isinstance(u, dict):
                    names.append(u.get("username") or u.get("email") or str(u.get("id", "?")))
                else:
                    names.append(str(u))
            return ", ".join(names) or None
        return str(val)

    if cf_type == "rating":
        return str(val)

    if cf_type == "location":
        if isinstance(val, dict):
            return val.get("formattedAddress") or val.get("name") or str(val)
        return str(val)

    # Fallback
    if isinstance(val, (list, dict)):
        return str(val)
    s = str(val).strip()
    return s or None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/auth", methods=["POST"])
def auth():
    key = (request.json or {}).get("api_key", "").strip()
    if not key:
        return jsonify(error="API key is required"), 400
    try:
        r = requests.get(f"{BASE}/team", headers=hdr(key), timeout=10)
    except Exception as e:
        return jsonify(error=str(e)), 500
    if r.status_code != 200:
        return jsonify(error="Invalid API key or ClickUp unreachable"), 401
    teams = r.json().get("teams", [])
    return jsonify([{"id": t["id"], "name": t["name"]} for t in teams])


@app.route("/api/lists", methods=["POST"])
def get_lists():
    d = request.json or {}
    key, team_id = d.get("api_key"), d.get("team_id")
    h = hdr(key)
    result = []
    try:
        spaces = requests.get(
            f"{BASE}/team/{team_id}/space?archived=false", headers=h, timeout=10
        ).json().get("spaces", [])

        for sp in spaces:
            sid, sname = sp["id"], sp["name"]

            for lst in requests.get(
                f"{BASE}/space/{sid}/list?archived=false", headers=h, timeout=10
            ).json().get("lists", []):
                result.append({"id": lst["id"], "name": f"{sname}  /  {lst['name']}"})

            for folder in requests.get(
                f"{BASE}/space/{sid}/folder?archived=false", headers=h, timeout=10
            ).json().get("folders", []):
                fname = folder["name"]
                for lst in requests.get(
                    f"{BASE}/folder/{folder['id']}/list?archived=false", headers=h, timeout=10
                ).json().get("lists", []):
                    result.append({"id": lst["id"], "name": f"{sname}  /  {fname}  /  {lst['name']}"})
    except Exception as e:
        return jsonify(error=str(e)), 500

    return jsonify(result)


@app.route("/api/statuses", methods=["POST"])
def get_statuses():
    d = request.json or {}
    key = d.get("api_key")
    list_ids = d.get("list_ids", [])
    h = hdr(key)
    seen, result = set(), []
    for lid in list_ids:
        try:
            r = requests.get(f"{BASE}/list/{lid}", headers=h, timeout=10)
            if r.status_code == 200:
                for s in r.json().get("statuses", []):
                    name = s.get("status", "")
                    if name not in seen:
                        seen.add(name)
                        result.append(s)
        except Exception:
            continue
    return jsonify(result)


@app.route("/api/report", methods=["POST"])
def report():
    d = request.json or {}
    key        = d.get("api_key")
    team_id    = d.get("team_id")
    list_ids   = d.get("list_ids", [])
    statuses   = [s.lower() for s in d.get("statuses", [])]
    date_from  = d.get("date_from")
    date_to    = d.get("date_to")
    date_field = d.get("date_field", "created")
    lists_meta = {item["id"]: item["name"] for item in (d.get("lists_meta") or [])}
    h = hdr(key)

    # 1. Fetch tasks from all selected lists (paginated)
    all_tasks = []
    for list_id in list_ids:
        params = {"include_closed": "true", "subtasks": "true", "page": 0}
        if date_field == "due":
            if date_from:
                params["due_date_gt"] = to_ts(date_from)
            if date_to:
                params["due_date_lt"] = to_ts(date_to, end_of_day=True)
        else:
            if date_from:
                params["date_created_gt"] = to_ts(date_from)
            if date_to:
                params["date_created_lt"] = to_ts(date_to, end_of_day=True)
        try:
            while True:
                r = requests.get(f"{BASE}/list/{list_id}/task", headers=h, params=params, timeout=20)
                if r.status_code != 200:
                    break
                batch = r.json().get("tasks", [])
                for task in batch:
                    task["_list_id"] = list_id
                    task["_list_name"] = lists_meta.get(list_id, list_id)
                all_tasks.extend(batch)
                if len(batch) < 100:
                    break
                params["page"] += 1
        except Exception:
            continue

    # 2. Status filter
    if statuses:
        all_tasks = [
            t for t in all_tasks
            if t.get("status", {}).get("status", "").lower() in statuses
        ]

    # 3. Fetch time entries for ALL users across all lists
    time_map = {}  # task_id -> {username: total_ms}
    try:
        member_ids = []
        team_r = requests.get(f"{BASE}/team/{team_id}", headers=h, timeout=10)
        if team_r.status_code == 200:
            members = team_r.json().get("team", {}).get("members", [])
            member_ids = [str(m["user"]["id"]) for m in members if m.get("user", {}).get("id")]

        for list_id in list_ids:
            te_params = {
                "list_id":    list_id,
                "start_date": 1420070400000,
                "end_date":   int(datetime.now(timezone.utc).timestamp() * 1000) + 86400000,
            }
            if member_ids:
                te_params["assignee"] = ",".join(member_ids)
            te_r = requests.get(
                f"{BASE}/team/{team_id}/time_entries", headers=h, params=te_params, timeout=30
            )
            if te_r.status_code == 200:
                for entry in te_r.json().get("data", []):
                    task_id_te = (entry.get("task") or {}).get("id")
                    if not task_id_te:
                        continue
                    user = (
                        entry.get("user", {}).get("username")
                        or entry.get("user", {}).get("email")
                        or "Unknown"
                    )
                    dur = abs(int(entry.get("duration") or 0))
                    if dur > 0:
                        time_map.setdefault(task_id_te, {})
                        time_map[task_id_te][user] = time_map[task_id_te].get(user, 0) + dur
    except Exception:
        pass

    # 4. Build rows
    rows = []
    for task in all_tasks:
        tid = task["id"]
        assignees = [
            a.get("username") or a.get("email") or "?"
            for a in task.get("assignees", [])
        ]
        pri_obj = task.get("priority")
        priority = pri_obj["priority"].title() if pri_obj else None
        user_times = time_map.get(tid, {})
        time_entries = [
            {"user": u, "duration": ms_to_duration(ms), "ms": ms}
            for u, ms in user_times.items()
            if ms > 0
        ]
        tags = [t.get("name", "") for t in task.get("tags", []) if t.get("name")]
        task_type = task.get("custom_type") or None
        custom_fields = {}
        for cf in task.get("custom_fields", []):
            cf_name = cf.get("name", "").strip()
            if not cf_name:
                continue
            if cf_name.lower() in ("type", "task type", "issue type", "task_type"):
                if task_type is None:
                    task_type = parse_cf_value(cf)
                continue
            val_str = parse_cf_value(cf)
            if val_str is not None:
                custom_fields[cf_name] = val_str

        status_obj = task.get("status", {})
        status_type = status_obj.get("type", "")
        is_closed = status_type in ("closed", "done")

        rows.append({
            "id":            tid,
            "name":          task["name"],
            "list_name":     task.get("_list_name", ""),
            "url":           task.get("url", f"https://app.clickup.com/t/{tid}"),
            "assignees":     assignees,
            "due_date":      fmt_date(task.get("due_date")),
            "date_created":  fmt_date(task.get("date_created")),
            "priority":      priority,
            "time_entries":  time_entries,
            "status":        status_obj.get("status", "").title() or "--",
            "status_color":  status_obj.get("color", "#888"),
            "is_closed":     is_closed,
            "time_estimate": ms_to_duration(task.get("time_estimate")),
            "task_type":     task_type,
            "tags":          tags,
            "custom_fields": custom_fields,
        })

    return jsonify(rows)


# ---------------------------------------------------------------------------
# Cron Script Template + Generator
# ---------------------------------------------------------------------------

_CRON_TEMPLATE = r"""#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ClickUp Report - Auto-generated scheduled script
# Generated: PLACEHOLDER_GEN_DATE
#
# Requirements:
#   pip install requests cryptography
#
# ── Linux / macOS cron (daily 8am) ───────────────────────────────────────────
#   crontab -e
#   0 8 * * * /usr/bin/python3 /path/to/clickup_cron.py >> /var/log/clickup_cron.log 2>&1
#
# ── Windows Task Scheduler ───────────────────────────────────────────────────
#   Option A (command line, run as Administrator):
#     schtasks /create /tn "ClickUpReport" /tr "python C:\path\to\clickup_cron.py" /sc daily /st 08:00
#
#   Option B (GUI):
#     1. Open Task Scheduler → Create Basic Task
#     2. Trigger: Daily at your preferred time
#     3. Action: Start a program
#        Program: python   (or full path, e.g. C:\Python312\python.exe)
#        Arguments: C:\path\to\clickup_cron.py
#     4. Finish
#
# SECURITY: This file contains encrypted credentials. Keep it private.

from cryptography.fernet import Fernet
import requests, smtplib, csv, io
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# ── Encrypted credentials (Fernet symmetric encryption) ───────────────────────
_KEY          = b'PLACEHOLDER_KEY'
_ENC_API_KEY  = b'PLACEHOLDER_API'
_ENC_SMTP_PWD = b'PLACEHOLDER_SMTP'

# ── Report configuration ──────────────────────────────────────────────────────
TEAM_ID       = 'PLACEHOLDER_TEAM'
LIST_IDS      = PLACEHOLDER_LISTS
LIST_NAMES    = PLACEHOLDER_LIST_NAMES    # {list_id: list_name}
STATUSES      = PLACEHOLDER_STATUSES
DATE_FIELD    = 'PLACEHOLDER_DATEFIELD'   # 'created' or 'due'
DURATION_DAYS = PLACEHOLDER_DURATION      # look-back window in days

# ── SMTP configuration ────────────────────────────────────────────────────────
SMTP_HOST  = 'PLACEHOLDER_HOST'
SMTP_PORT  = PLACEHOLDER_PORT
SMTP_SSL   = PLACEHOLDER_SSL
SMTP_USER  = 'PLACEHOLDER_USER'
EMAIL_FROM = 'PLACEHOLDER_FROM'
EMAIL_TO   = 'PLACEHOLDER_TO'

BASE = 'https://api.clickup.com/api/v2'


def _dec(token):
    return Fernet(_KEY).decrypt(token).decode()


def _hdr():
    return {'Authorization': _dec(_ENC_API_KEY), 'Content-Type': 'application/json'}


def _ms(ms):
    if not ms:
        return '--'
    ms = abs(int(ms))
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    return f'{h}h {m}m' if h else f'{m}m'


def _dt(ts):
    if not ts:
        return ''
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    except Exception:
        return ''


def fetch_report():
    now     = datetime.now(timezone.utc)
    ts_to   = int(now.timestamp() * 1000)
    ts_from = int((now - timedelta(days=DURATION_DAYS)).timestamp() * 1000)
    h = _hdr()

    all_tasks = []
    for list_id in LIST_IDS:
        params = {'include_closed': 'true', 'subtasks': 'true', 'page': 0}
        if DATE_FIELD == 'due':
            params['due_date_gt'] = ts_from
            params['due_date_lt'] = ts_to
        else:
            params['date_created_gt'] = ts_from
            params['date_created_lt'] = ts_to
        while True:
            r = requests.get(f'{BASE}/list/{list_id}/task', headers=h, params=params, timeout=20)
            if r.status_code != 200:
                break
            batch = r.json().get('tasks', [])
            for t in batch:
                t['_list_id'] = list_id
            all_tasks.extend(batch)
            if len(batch) < 100:
                break
            params['page'] += 1

    if STATUSES:
        sl = [s.lower() for s in STATUSES]
        all_tasks = [t for t in all_tasks
                     if t.get('status', {}).get('status', '').lower() in sl]

    time_map = {}
    try:
        tr = requests.get(f'{BASE}/team/{TEAM_ID}', headers=h, timeout=10)
        mids = []
        if tr.ok:
            mids = [str(m['user']['id']) for m in
                    tr.json().get('team', {}).get('members', [])
                    if m.get('user', {}).get('id')]
        for list_id in LIST_IDS:
            tp = {'list_id': list_id, 'start_date': ts_from,
                  'end_date': ts_to + 86400000}
            if mids:
                tp['assignee'] = ','.join(mids)
            ter = requests.get(f'{BASE}/team/{TEAM_ID}/time_entries',
                               headers=h, params=tp, timeout=30)
            if ter.ok:
                for e in ter.json().get('data', []):
                    tid = (e.get('task') or {}).get('id')
                    if not tid:
                        continue
                    user = (e.get('user', {}).get('username') or
                            e.get('user', {}).get('email') or '?')
                    dur = abs(int(e.get('duration') or 0))
                    if dur:
                        time_map.setdefault(tid, {})
                        time_map[tid][user] = time_map[tid].get(user, 0) + dur
    except Exception:
        pass

    rows = []
    for task in all_tasks:
        tid = task['id']
        assignees = [a.get('username') or a.get('email', '?')
                     for a in task.get('assignees', [])]
        ut = time_map.get(tid, {})
        time_str = (' | '.join(f'{u}: {_ms(ms)}' for u, ms in ut.items())
                    if ut else '--')
        rows.append({
            'ID':            tid,
            'Name':          task['name'],
            'List':          LIST_NAMES.get(task.get('_list_id', ''), task.get('_list_id', '')),
            'Assignees':     ', '.join(assignees),
            'Due Date':      _dt(task.get('due_date')),
            'Priority':      ((task.get('priority') or {}).get('priority') or '').title(),
            'Time Tracked':  time_str,
            'Status':        task.get('status', {}).get('status', '').title(),
            'Time Estimate': _ms(task.get('time_estimate')),
            'Tags':          ', '.join(t.get('name', '') for t in task.get('tags', [])),
            'URL':           task.get('url', f'https://app.clickup.com/t/{tid}'),
        })
    return rows


def make_csv(rows):
    if not rows:
        return ''
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def send_email(csv_data, row_count, rows):
    smtp_pwd = _dec(_ENC_SMTP_PWD)
    now_str  = datetime.now().strftime('%Y-%m-%d')
    fname    = f'clickup_report_{now_str}.csv'

    # Build summary counts by status
    status_counts = {}
    for r in rows:
        s = r.get('Status', '--')
        status_counts[s] = status_counts.get(s, 0) + 1
    status_lines = '\n'.join(f'    {s}: {c}' for s, c in sorted(status_counts.items()))

    # Build summary counts by assignee
    assignee_counts = {}
    for r in rows:
        for a in (r.get('Assignees') or '').split(', '):
            a = a.strip()
            if a:
                assignee_counts[a] = assignee_counts.get(a, 0) + 1
    assignee_lines = '\n'.join(f'    {a}: {c}' for a, c in
                               sorted(assignee_counts.items(), key=lambda x: -x[1])[:10])

    body = (
        f'ClickUp Automated Report\n'
        f'{"=" * 40}\n'
        f'Date      : {now_str}\n'
        f'Period    : last {DURATION_DAYS} days ({DATE_FIELD} date)\n'
        f'Lists     : {len(LIST_IDS)}\n'
        f'Total Tasks: {row_count}\n\n'
        f'By Status:\n{status_lines}\n\n'
        f'Top Assignees:\n{assignee_lines}\n\n'
        f'The full report is attached as: {fname}\n'
    )

    msg = MIMEMultipart()
    msg['From']    = EMAIL_FROM
    msg['To']      = EMAIL_TO
    msg['Subject'] = f'ClickUp Report {now_str} - {row_count} tasks'
    msg.attach(MIMEText(body, 'plain'))

    # Attach CSV
    part = MIMEBase('application', 'octet-stream')
    part.set_payload(csv_data.encode('utf-8-sig'))
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', f'attachment; filename={fname}')
    msg.attach(part)

    if SMTP_SSL:
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        s.ehlo()
        s.starttls()
        s.ehlo()
    s.login(SMTP_USER, smtp_pwd)
    s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
    s.quit()
    print(f'[OK] Report emailed to {EMAIL_TO} — {fname} ({row_count} tasks)')


if __name__ == '__main__':
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] Fetching ClickUp report...')
    try:
        rows = fetch_report()
        print(f'  Found {len(rows)} tasks')
        if rows:
            send_email(make_csv(rows), len(rows), rows)
        else:
            print('  No tasks found, skipping email.')
    except Exception as e:
        print(f'[ERROR] {e}')
        raise
"""


@app.route("/api/generate_cron", methods=["POST"])
def generate_cron():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return jsonify(error=(
            "cryptography package not installed on the server. "
            "Run: pip install cryptography"
        )), 500

    d = request.json or {}
    api_key      = d.get("api_key", "")
    smtp_password = d.get("smtp_password", "")

    key      = Fernet.generate_key()
    f        = Fernet(key)
    enc_api  = f.encrypt(api_key.encode())
    enc_smtp = f.encrypt(smtp_password.encode())

    script = (
        _CRON_TEMPLATE
        # IMPORTANT: PLACEHOLDER_DATEFIELD must come before PLACEHOLDER_DATE
        # to avoid partial substitution (DATE is a prefix of DATEFIELD)
        .replace("PLACEHOLDER_DATEFIELD", d.get("date_field", "created"))
        .replace("PLACEHOLDER_GEN_DATE",  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        .replace("PLACEHOLDER_KEY",       key.decode())
        .replace("PLACEHOLDER_API",       enc_api.decode())
        .replace("PLACEHOLDER_SMTP",      enc_smtp.decode())
        .replace("PLACEHOLDER_TEAM",      d.get("team_id", ""))
        .replace("PLACEHOLDER_LISTS",     repr(d.get("list_ids", [])))
        .replace("PLACEHOLDER_LIST_NAMES", repr({
            item["id"]: item["name"]
            for item in (d.get("lists_meta") or [])
        }))
        .replace("PLACEHOLDER_STATUSES",  repr(d.get("statuses", [])))
        .replace("PLACEHOLDER_DURATION",  str(d.get("duration_days", 7)))
        .replace("PLACEHOLDER_HOST",      d.get("smtp_host", ""))
        .replace("PLACEHOLDER_PORT",      str(d.get("smtp_port", 587)))
        .replace("PLACEHOLDER_SSL",       "True" if d.get("smtp_ssl") else "False")
        .replace("PLACEHOLDER_USER",      d.get("smtp_user", ""))
        .replace("PLACEHOLDER_FROM",      d.get("email_from", ""))
        .replace("PLACEHOLDER_TO",        d.get("email_to", ""))
    )

    return jsonify({"script": script})


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ClickUp Report Generator</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    --purple: #7c5cbf;
    --purple-dark: #5e3fa3;
    --purple-light: #f0ecff;
    --bg: #f4f5f7;
    --card: #ffffff;
    --border: #e2e4ea;
    --text: #172b4d;
    --muted: #6b778c;
    --radius: 8px;
    --shadow: 0 2px 8px rgba(0,0,0,.08);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); font-size: 14px; }

  .app { display: flex; flex-direction: column; min-height: 100vh; }
  header { background: var(--purple); color: #fff; padding: 14px 24px;
           display: flex; align-items: center; gap: 10px; box-shadow: 0 2px 6px rgba(0,0,0,.2); }
  header h1 { font-size: 18px; font-weight: 600; }
  .body { display: flex; flex: 1; }

  /* Sidebar */
  .sidebar { width: 300px; min-width: 300px; background: var(--card);
             border-right: 1px solid var(--border); padding: 20px;
             display: flex; flex-direction: column; gap: 18px; overflow-y: auto; }
  .sidebar h2 { font-size: 11px; font-weight: 700; text-transform: uppercase;
                letter-spacing: .8px; color: var(--muted); margin-bottom: 4px; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  label { font-size: 12px; font-weight: 600; color: var(--text); }
  input[type="text"], input[type="password"], input[type="date"], select {
    width: 100%; padding: 8px 10px; border: 1.5px solid var(--border);
    border-radius: var(--radius); font-size: 13px; color: var(--text);
    background: #fafafa; outline: none; transition: border-color .15s;
  }
  input:focus, select:focus { border-color: var(--purple); background: #fff; }
  .date-row { display: flex; gap: 8px; }
  .date-row .field { flex: 1; }

  .status-list { display: flex; flex-direction: column; gap: 6px;
                 max-height: 200px; overflow-y: auto; padding: 2px; }
  .status-item { display: flex; align-items: center; gap: 8px; padding: 5px 8px;
                 border: 1.5px solid var(--border); border-radius: 6px;
                 cursor: pointer; transition: background .12s; user-select: none; }
  .status-item:hover { background: var(--purple-light); }
  .status-item.checked { border-color: var(--purple); background: var(--purple-light); }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .status-label { font-size: 12px; font-weight: 500; flex: 1; }
  .status-check { width: 16px; height: 16px; border: 2px solid var(--border);
                  border-radius: 4px; display: flex; align-items: center;
                  justify-content: center; flex-shrink: 0; }
  .status-item.checked .status-check { background: var(--purple); border-color: var(--purple); }
  .status-item.checked .status-check::after {
    content: ''; width: 5px; height: 8px; border: 2px solid #fff;
    border-top: none; border-left: none;
    transform: rotate(45deg) translate(-1px,-1px); display: block;
  }

  /* Buttons */
  .btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px;
         padding: 9px 16px; border: none; border-radius: var(--radius); font-size: 13px;
         font-weight: 600; cursor: pointer; transition: opacity .15s, transform .1s; }
  .btn:active { transform: scale(.98); }
  .btn:disabled { opacity: .5; cursor: not-allowed; transform: none; }
  .btn-primary { background: var(--purple); color: #fff; width: 100%; }
  .btn-primary:hover:not(:disabled) { background: var(--purple-dark); }
  .btn-sm { padding: 6px 12px; font-size: 12px; }
  .btn-outline { background: #fff; color: var(--purple); border: 1.5px solid var(--purple); }
  .btn-outline:hover:not(:disabled) { background: var(--purple-light); }
  .btn-connect { background: var(--purple-light); color: var(--purple); border: 1.5px solid var(--purple); }
  .btn-connect:hover:not(:disabled) { background: var(--purple); color: #fff; }

  /* Main */
  .main { flex: 1; padding: 24px; overflow: auto; display: flex; flex-direction: column; gap: 16px; }
  .empty-state { flex: 1; display: flex; flex-direction: column; align-items: center;
                 justify-content: center; gap: 12px; color: var(--muted); }
  .empty-state svg { opacity: .3; }
  .empty-state p { font-size: 15px; font-weight: 500; }
  .empty-state span { font-size: 13px; }

  .results-header { display: flex; align-items: center; justify-content: space-between;
                    flex-wrap: wrap; gap: 10px; }
  .results-header h3 { font-size: 15px; font-weight: 700; }
  .results-header span { font-size: 12px; color: var(--muted); }

  /* Table */
  .table-wrap { overflow-x: auto; border-radius: var(--radius); box-shadow: var(--shadow);
                background: var(--card); border: 1px solid var(--border); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 900px; }
  thead { background: #f8f9fb; }
  th { padding: 11px 14px; text-align: left; font-size: 11px; font-weight: 700;
       text-transform: uppercase; letter-spacing: .5px; color: var(--muted);
       border-bottom: 1px solid var(--border); white-space: nowrap; }
  td { padding: 11px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #fafbff; }
  .task-name { font-weight: 600; }
  .task-name a { color: inherit; text-decoration: none; }
  .task-name a:hover { color: var(--purple); text-decoration: underline; }

  /* Badges & pills */
  .badge { display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px;
           border-radius: 12px; font-size: 11px; font-weight: 600; white-space: nowrap; }
  .badge-dot { width: 7px; height: 7px; border-radius: 50%; }
  .priority-urgent { background: #fff0f0; color: #c00; }
  .priority-high   { background: #fff5e8; color: #c45c00; }
  .priority-normal { background: #e8f4ff; color: #0055c4; }
  .priority-low    { background: #f0f0f0; color: #555; }

  .pills { display: flex; flex-wrap: wrap; gap: 4px; }
  .pill { padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .pill-purple { background: var(--purple-light); color: var(--purple); }
  .pill-gray   { background: #f0f0f0; color: #444; }
  .pill-tag    { background: #e6f7f0; color: #0a6640; }

  .time-user  { display: flex; flex-direction: column; gap: 2px; }
  .time-row   { display: flex; gap: 6px; align-items: center; white-space: nowrap; }
  .time-val   { font-weight: 600; color: var(--purple); font-size: 12px; }
  .time-uname { font-size: 11px; color: var(--muted); }

  .null { color: #ccc; }
  .toggle-row { display: flex; align-items: center; gap: 8px; }
  .divider { border: none; border-top: 1px solid var(--border); margin: 2px 0; }

  /* Toggle switch */
  .toggle-row { display: flex; align-items: center; gap: 8px; }
  .toggle-label { font-size: 12px; font-weight: 500; color: var(--muted);
                  transition: color .2s, font-weight .2s; }
  .toggle-switch { width: 40px; height: 22px; background: var(--purple); border-radius: 11px;
                   cursor: pointer; position: relative; flex-shrink: 0;
                   transition: background .2s; }
  .toggle-knob { position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
                 background: #fff; border-radius: 50%;
                 transition: transform .2s; box-shadow: 0 1px 3px rgba(0,0,0,.2); }
  .toggle-switch.on .toggle-knob { transform: translateX(18px); }

  /* Summary chips */
  .chips-row { display: flex; gap: 14px; flex-wrap: wrap; }
  .chip { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
          padding: 12px 20px; display: flex; flex-direction: column; gap: 4px;
          box-shadow: var(--shadow); min-width: 140px; }
  .chip-value { font-size: 24px; font-weight: 700; color: var(--purple); line-height: 1; }
  .chip-label { font-size: 11px; font-weight: 600; text-transform: uppercase;
                letter-spacing: .6px; color: var(--muted); }
  .chip.chip-closed .chip-value { color: #27ae60; }
  .chip.chip-time .chip-value { color: #e67e22; }

  /* Charts */
  .charts-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
                box-shadow: var(--shadow); padding: 16px; }
  .chart-card h4 { font-size: 12px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: .6px; color: var(--muted); margin-bottom: 12px; }

  /* List checkbox UI (reuses status-list styles) */
  .list-apply-btn { margin-top: 6px; width: 100%; }

  /* ── Cron Modal ─────────────────────────────────────────────────────────── */
  .modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.45);
    display: flex; align-items: center; justify-content: center;
    z-index: 1000; padding: 20px;
  }
  .modal-box {
    background: var(--card); border-radius: 12px; width: 100%;
    max-width: 560px; max-height: 90vh; display: flex;
    flex-direction: column; box-shadow: 0 8px 40px rgba(0,0,0,.25);
    overflow: hidden;
  }
  .modal-hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 22px; border-bottom: 1px solid var(--border);
    background: var(--purple);
  }
  .modal-hdr h2 { font-size: 16px; font-weight: 700; color: #fff; }
  .modal-close {
    background: none; border: none; color: #fff; font-size: 22px;
    cursor: pointer; line-height: 1; padding: 0 4px;
  }
  .modal-body { padding: 22px; overflow-y: auto; flex: 1; display: flex; flex-direction: column; gap: 16px; }

  /* Step indicator */
  .step-bar { display: flex; align-items: center; gap: 0; margin-bottom: 6px; }
  .step-node {
    width: 30px; height: 30px; border-radius: 50%; background: var(--border);
    color: var(--muted); font-size: 12px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; transition: background .2s, color .2s;
  }
  .step-node.active { background: var(--purple); color: #fff; }
  .step-node.done   { background: #27ae60; color: #fff; }
  .step-line { flex: 1; height: 2px; background: var(--border); }
  .step-title {
    font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 2px;
  }
  .step-sub { font-size: 11px; color: var(--muted); margin-bottom: 10px; }

  /* Modal fields */
  .m-field { display: flex; flex-direction: column; gap: 5px; }
  .m-field label { font-size: 12px; font-weight: 600; color: var(--text); }
  .m-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .m-row3 { display: grid; grid-template-columns: 2fr 1fr auto; gap: 10px; align-items: end; }

  /* SSL toggle inside modal */
  .ssl-toggle { display: flex; align-items: center; gap: 8px; padding: 8px 0; }
  .ssl-toggle span { font-size: 12px; font-weight: 600; }

  /* Modal nav buttons */
  .modal-nav { display: flex; gap: 10px; margin-top: 4px; }
  .modal-nav .btn { flex: 1; }

  /* Duration row */
  .dur-row { display: flex; align-items: center; gap: 10px; }
  .dur-row input { width: 80px; }
  .dur-row span { font-size: 13px; color: var(--muted); }

  .hidden { display: none !important; }

  .spinner { width: 20px; height: 20px; border: 3px solid rgba(124,92,191,.2);
             border-top-color: var(--purple); border-radius: 50%;
             animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading-row { display: flex; align-items: center; gap: 10px; padding: 24px;
                 color: var(--muted); font-size: 13px; }
  .alert { padding: 10px 14px; border-radius: var(--radius); font-size: 13px; font-weight: 500; }
  .alert-error   { background: #fff0f0; color: #c00; border: 1px solid #ffc0c0; }
  .alert-success { background: #f0fff4; color: #006630; border: 1px solid #9de8b5; }
  .select-all-btn { font-size: 11px; color: var(--purple); cursor: pointer; font-weight: 600;
                    text-decoration: underline; background: none; border: none; padding: 0; margin-bottom: 2px; }
</style>
</head>
<body>
<div class="app">

<header>
  <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
    <circle cx="14" cy="14" r="13" fill="white" fill-opacity=".15"/>
    <path d="M7 16l4 4 3-6 3 6 4-4" stroke="white" stroke-width="2.5"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
  <h1>ClickUp Report Generator</h1>
  <div style="margin-left:auto">
    <button class="btn btn-sm" onclick="openCronModal()"
      style="background:rgba(255,255,255,.18);color:#fff;border:1.5px solid rgba(255,255,255,.4)">
      &#9201; Create Cron
    </button>
  </div>
</header>

<div class="body">
<aside class="sidebar">

  <div>
    <h2>Connection</h2>
    <div class="field" style="margin-top:8px">
      <label for="apiKey">API Key</label>
      <input type="password" id="apiKey" placeholder="pk_xxxxxxxxxxxxxxx" autocomplete="off">
    </div>
    <button class="btn btn-connect" style="margin-top:8px;width:100%" onclick="connect()">
      <span id="connectLabel">Connect</span>
    </button>
    <div id="authMsg" class="alert hidden" style="margin-top:8px"></div>
  </div>

  <hr class="divider">

  <div id="workspaceBlock" class="hidden">
    <h2>Workspace</h2>
    <div class="field" style="margin-top:8px">
      <label for="workspaceSel">Workspace</label>
      <select id="workspaceSel" onchange="loadLists()">
        <option value="">-- select workspace --</option>
      </select>
    </div>
  </div>

  <div id="listBlock" class="hidden">
    <div class="field">
      <label>Lists</label>
      <button class="select-all-btn" onclick="toggleAllLists()">Select / Deselect All</button>
      <div id="listCheckboxes" class="status-list" style="max-height:200px"></div>
      <button class="btn btn-sm btn-outline list-apply-btn" onclick="applyListSelection()">Apply &amp; Load Statuses</button>
    </div>
  </div>

  <div id="statusBlock" class="hidden">
    <h2>Task Statuses</h2>
    <button class="select-all-btn" onclick="toggleAllStatuses()">Select / Deselect All</button>
    <div id="statusList" class="status-list"></div>
  </div>

  <div id="dateBlock" class="hidden">
    <h2>Date Range</h2>
    <div class="toggle-row" style="margin-top:8px">
      <span class="toggle-label" id="toggleLabelCreated" style="font-weight:700;color:var(--purple)">Created</span>
      <div class="toggle-switch" id="dateToggle" onclick="switchDateField()">
        <div class="toggle-knob" id="toggleKnob"></div>
      </div>
      <span class="toggle-label" id="toggleLabelDue">Due Date</span>
    </div>
    <div class="date-row" style="margin-top:8px">
      <div class="field"><label>From</label><input type="date" id="dateFrom"></div>
      <div class="field"><label>To</label><input type="date" id="dateTo"></div>
    </div>
  </div>

  <div id="generateBlock" class="hidden">
    <button class="btn btn-primary" id="genBtn" onclick="generateReport()">
      Generate Report
    </button>
  </div>

</aside>

<main class="main">
  <div class="empty-state" id="emptyState">
    <svg width="64" height="64" viewBox="0 0 64 64" fill="none">
      <rect x="8" y="12" width="48" height="40" rx="6" stroke="#7c5cbf" stroke-width="2"/>
      <line x1="18" y1="24" x2="46" y2="24" stroke="#7c5cbf" stroke-width="2" stroke-linecap="round"/>
      <line x1="18" y1="32" x2="46" y2="32" stroke="#7c5cbf" stroke-width="2" stroke-linecap="round"/>
      <line x1="18" y1="40" x2="36" y2="40" stroke="#7c5cbf" stroke-width="2" stroke-linecap="round"/>
    </svg>
    <p>No report generated yet</p>
    <span>Connect your API key and fill in the filters to get started</span>
  </div>

  <div id="loadingState" class="loading-row hidden">
    <div class="spinner"></div>
    <span id="loadingMsg">Fetching report...</span>
  </div>

  <div id="errorState" class="alert alert-error hidden"></div>

  <div id="resultsArea" class="hidden">
    <div class="results-header">
      <div>
        <h3 id="resultCount"></h3>
        <span id="resultMeta"></span>
      </div>
      <div>
        <button class="btn btn-sm btn-outline" id="csvBtn" onclick="exportCSV()">
          &#11015; Export CSV
        </button>
      </div>
    </div>

    <!-- Summary chips -->
    <div class="chips-row" id="chipsRow">
      <div class="chip">
        <span class="chip-value" id="chipTotal">0</span>
        <span class="chip-label">Total Tasks</span>
      </div>
      <div class="chip chip-closed">
        <span class="chip-value" id="chipClosed">0</span>
        <span class="chip-label">Closed Tasks</span>
      </div>
      <div class="chip chip-time">
        <span class="chip-value" id="chipTime">0h 0m</span>
        <span class="chip-label">Total Time Tracked</span>
      </div>
    </div>

    <!-- Charts -->
    <div class="charts-grid" id="chartsGrid">
      <div class="chart-card">
        <h4 id="chartDayTitle">Tasks per Day</h4>
        <canvas id="chartDay" height="200"></canvas>
      </div>
      <div class="chart-card">
        <h4>Tasks per Assignee</h4>
        <canvas id="chartAssignee" height="200"></canvas>
      </div>
      <div class="chart-card">
        <h4>Time Tracked per Assignee</h4>
        <canvas id="chartTime" height="200"></canvas>
      </div>
    </div>

    <div class="table-wrap">
      <table id="reportTable">
        <thead><tr id="tableHead"></tr></thead>
        <tbody id="reportBody"></tbody>
      </table>
    </div>
  </div>
</main>
</div>
</div>

<!-- ═══════════════════ CRON MODAL ═══════════════════ -->
<div id="cronModal" class="modal-overlay hidden">
  <div class="modal-box">
    <div class="modal-hdr">
      <h2>&#9201; Create Cron Script</h2>
      <button class="modal-close" onclick="closeCronModal()">&#10005;</button>
    </div>
    <div class="modal-body">

      <!-- Step indicator -->
      <div class="step-bar">
        <div class="step-node active" id="sn1">1</div>
        <div class="step-line"></div>
        <div class="step-node" id="sn2">2</div>
        <div class="step-line"></div>
        <div class="step-node" id="sn3">3</div>
        <div class="step-line"></div>
        <div class="step-node" id="sn4">4</div>
      </div>

      <!-- Step 1: API Key -->
      <div id="cronS1">
        <div class="step-title">Step 1 — API Key</div>
        <div class="step-sub">Enter your ClickUp API key to get started.</div>
        <div class="m-field">
          <label>API Key</label>
          <input type="password" id="cronApiKey" placeholder="pk_xxxxxxxxxxxxxxx" autocomplete="off">
        </div>
        <div id="cronAuthMsg" class="alert hidden" style="margin-top:8px"></div>
        <div class="modal-nav" style="margin-top:14px">
          <button class="btn btn-primary" onclick="cronConnect()">
            <span id="cronConnLabel">Connect &amp; Continue</span>
          </button>
        </div>
      </div>

      <!-- Step 2: Workspace + Lists -->
      <div id="cronS2" class="hidden">
        <div class="step-title">Step 2 — Workspace &amp; Lists</div>
        <div class="step-sub">Select which workspace and lists to include in the report.</div>
        <div class="m-field">
          <label>Workspace</label>
          <select id="cronWorkspaceSel" onchange="cronLoadLists()">
            <option value="">-- select workspace --</option>
          </select>
        </div>
        <div class="m-field" id="cronListField" style="margin-top:10px;display:none">
          <label>Lists</label>
          <button class="select-all-btn" onclick="cronToggleAllLists()">Select / Deselect All</button>
          <div id="cronListBoxes" class="status-list" style="max-height:180px"></div>
        </div>
        <div class="modal-nav" style="margin-top:14px">
          <button class="btn btn-outline" onclick="cronGoStep(1)">&#8592; Back</button>
          <button class="btn btn-primary" id="cronS2Next" onclick="cronGoStep(3)" disabled>Next &#8594;</button>
        </div>
      </div>

      <!-- Step 3: Statuses + Duration -->
      <div id="cronS3" class="hidden">
        <div class="step-title">Step 3 — Statuses &amp; Duration</div>
        <div class="step-sub">Choose which statuses to include and how many days to look back.</div>
        <div class="m-field">
          <label>Task Statuses</label>
          <button class="select-all-btn" onclick="cronToggleAllStatuses()">Select / Deselect All</button>
          <div id="cronStatusList" class="status-list" style="max-height:160px"></div>
        </div>
        <div class="m-field" style="margin-top:10px">
          <label>Look-back Period</label>
          <div class="dur-row">
            <input type="number" id="cronDuration" value="7" min="1" max="365"
                   style="width:80px;padding:8px 10px;border:1.5px solid var(--border);border-radius:var(--radius);font-size:13px">
            <span>days (e.g. 7 = last 7 days)</span>
          </div>
        </div>
        <div class="m-field" style="margin-top:10px">
          <label>Date Filter Field</label>
          <div class="toggle-row" style="margin-top:4px">
            <span class="toggle-label" id="cronLblCreated" style="font-weight:700;color:var(--purple)">Created</span>
            <div class="toggle-switch" id="cronDateToggle" onclick="cronSwitchDateField()">
              <div class="toggle-knob" id="cronToggleKnob"></div>
            </div>
            <span class="toggle-label" id="cronLblDue">Due Date</span>
          </div>
        </div>
        <div class="modal-nav" style="margin-top:14px">
          <button class="btn btn-outline" onclick="cronGoStep(2)">&#8592; Back</button>
          <button class="btn btn-primary" onclick="cronGoStep(4)">Next &#8594;</button>
        </div>
      </div>

      <!-- Step 4: SMTP + Generate -->
      <div id="cronS4" class="hidden">
        <div class="step-title">Step 4 — Email (SMTP)</div>
        <div class="step-sub">Configure the mail server to send the automated report.</div>
        <div class="m-row3">
          <div class="m-field">
            <label>SMTP Hostname</label>
            <input type="text" id="cronSmtpHost" placeholder="smtp.gmail.com">
          </div>
          <div class="m-field">
            <label>Port</label>
            <input type="number" id="cronSmtpPort" placeholder="587" value="587">
          </div>
          <div class="ssl-toggle">
            <div class="toggle-switch" id="cronSslToggle" onclick="cronToggleSsl()" style="background:#ccc">
              <div class="toggle-knob" id="cronSslKnob"></div>
            </div>
            <span>SSL/TLS</span>
          </div>
        </div>
        <div class="m-row2" style="margin-top:10px">
          <div class="m-field">
            <label>SMTP Username</label>
            <input type="text" id="cronSmtpUser" placeholder="user@example.com" autocomplete="off">
          </div>
          <div class="m-field">
            <label>SMTP Password</label>
            <input type="password" id="cronSmtpPass" placeholder="••••••••" autocomplete="new-password">
          </div>
        </div>
        <div class="m-row2" style="margin-top:10px">
          <div class="m-field">
            <label>From Email</label>
            <input type="email" id="cronEmailFrom" placeholder="from@example.com">
          </div>
          <div class="m-field">
            <label>To Email</label>
            <input type="email" id="cronEmailTo" placeholder="to@example.com">
          </div>
        </div>
        <div id="cronGenMsg" class="alert hidden" style="margin-top:8px"></div>
        <div class="modal-nav" style="margin-top:14px">
          <button class="btn btn-outline" onclick="cronGoStep(3)">&#8592; Back</button>
          <button class="btn btn-primary" id="cronGenBtn" onclick="generateCronScript()">
            &#11015; Generate &amp; Download Script
          </button>
        </div>
        <div style="margin-top:10px;font-size:11px;color:var(--muted)">
          &#128274; API key &amp; SMTP password are encrypted with Fernet symmetric encryption before being embedded in the script.
        </div>
      </div>

    </div><!-- /modal-body -->
  </div><!-- /modal-box -->
</div><!-- /cronModal -->

<script>
// ---- state ----
var state = {
  api_key: '', team_id: '', rows: [], cfKeys: [], dateField: 'created',
  allLists: [],          // [{id, name}, ...]
  selectedListIds: [],   // checked list IDs
  charts: {}             // Chart.js instances keyed by canvas id
};

// ---- date field toggle ----
function switchDateField() {
  var toDue = state.dateField === 'created';
  state.dateField = toDue ? 'due' : 'created';
  var sw = document.getElementById('dateToggle');
  var lc = document.getElementById('toggleLabelCreated');
  var ld = document.getElementById('toggleLabelDue');
  if (toDue) {
    sw.classList.add('on');
    lc.style.fontWeight = '500'; lc.style.color = 'var(--muted)';
    ld.style.fontWeight = '700'; ld.style.color = 'var(--purple)';
  } else {
    sw.classList.remove('on');
    ld.style.fontWeight = '500'; ld.style.color = 'var(--muted)';
    lc.style.fontWeight = '700'; lc.style.color = 'var(--purple)';
  }
}

// ---- connect ----
async function connect() {
  var key = document.getElementById('apiKey').value.trim();
  if (!key) return showAuthMsg('Enter your ClickUp API key', 'error');
  setConnectLoading(true);
  try {
    var res = await post('/api/auth', { api_key: key });
    if (res.error) return showAuthMsg(res.error, 'error');
    state.api_key = key;
    showAuthMsg('Connected!', 'success');
    var sel = document.getElementById('workspaceSel');
    sel.innerHTML = '<option value="">-- select workspace --</option>';
    res.forEach(function(t) { sel.appendChild(new Option(t.name, t.id)); });
    hide('listBlock'); hide('statusBlock'); hide('dateBlock'); hide('generateBlock');
    show('workspaceBlock');
  } catch(e) {
    showAuthMsg('Connection failed: ' + e.message, 'error');
  } finally {
    setConnectLoading(false);
  }
}

// ---- load lists ----
async function loadLists() {
  var tid = document.getElementById('workspaceSel').value;
  if (!tid) return;
  state.team_id = tid;
  hide('listBlock'); hide('statusBlock'); hide('dateBlock'); hide('generateBlock');
  var container = document.getElementById('listCheckboxes');
  container.innerHTML = '<div class="loading-row"><div class="spinner"></div> Loading lists...</div>';
  show('listBlock');
  try {
    var res = await post('/api/lists', { api_key: state.api_key, team_id: tid });
    if (res.error) { container.innerHTML = '<span>Error: ' + res.error + '</span>'; return; }
    state.allLists = res;
    container.innerHTML = '';
    res.forEach(function(l) {
      var item = document.createElement('div');
      item.className = 'status-item checked';
      item.dataset.listId = l.id;
      item.dataset.listName = l.name;
      item.innerHTML =
        '<div class="status-dot" style="background:var(--purple)"></div>' +
        '<span class="status-label" style="font-size:11px">' + esc(l.name) + '</span>' +
        '<div class="status-check"></div>';
      item.onclick = function() { item.classList.toggle('checked'); };
      container.appendChild(item);
    });
  } catch(e) {
    container.innerHTML = '<span>Error loading lists</span>';
  }
}

function toggleAllLists() {
  var items = document.querySelectorAll('#listCheckboxes .status-item');
  var anyUnchecked = Array.from(items).some(function(i) { return !i.classList.contains('checked'); });
  items.forEach(function(i) { anyUnchecked ? i.classList.add('checked') : i.classList.remove('checked'); });
}

async function applyListSelection() {
  var checked = Array.from(document.querySelectorAll('#listCheckboxes .status-item.checked'));
  state.selectedListIds = checked.map(function(i) { return i.dataset.listId; });
  if (!state.selectedListIds.length) { alert('Please select at least one list.'); return; }
  hide('statusBlock'); hide('generateBlock');
  show('dateBlock');
  var container = document.getElementById('statusList');
  container.innerHTML = '<div class="loading-row"><div class="spinner"></div> Loading statuses...</div>';
  show('statusBlock');
  try {
    var res = await post('/api/statuses', { api_key: state.api_key, list_ids: state.selectedListIds });
    if (res.error) { container.innerHTML = '<span>Error</span>'; return; }
    container.innerHTML = '';
    res.forEach(function(s) {
      var item = document.createElement('div');
      item.className = 'status-item checked';
      item.dataset.status = s.status;
      item.innerHTML =
        '<div class="status-dot" style="background:' + (s.color||'#888') + '"></div>' +
        '<span class="status-label">' + capitalize(s.status) + '</span>' +
        '<div class="status-check"></div>';
      item.onclick = function() { item.classList.toggle('checked'); };
      container.appendChild(item);
    });
    show('generateBlock');
  } catch(e) {
    container.innerHTML = '<span>Error loading statuses</span>';
  }
}

function toggleAllStatuses() {
  var items = document.querySelectorAll('#statusList .status-item');
  var anyUnchecked = Array.from(items).some(function(i) { return !i.classList.contains('checked'); });
  items.forEach(function(i) { anyUnchecked ? i.classList.add('checked') : i.classList.remove('checked'); });
}

// ---- generate report ----
async function generateReport() {
  var statuses = Array.from(document.querySelectorAll('#statusList .status-item.checked')).map(function(i) { return i.dataset.status; });
  var dateFrom = document.getElementById('dateFrom').value;
  var dateTo   = document.getElementById('dateTo').value;
  var listsMeta = state.selectedListIds.map(function(id) {
    var found = state.allLists.find(function(l) { return l.id === id; });
    return { id: id, name: found ? found.name : id };
  });

  hide('emptyState'); hide('errorState'); hide('resultsArea');
  show('loadingState');
  document.getElementById('loadingMsg').textContent = 'Fetching tasks, time data and custom fields...';
  document.getElementById('genBtn').disabled = true;

  try {
    var res = await post('/api/report', {
      api_key:    state.api_key,
      team_id:    state.team_id,
      list_ids:   state.selectedListIds,
      lists_meta: listsMeta,
      statuses:   statuses,
      date_from:  dateFrom || null,
      date_to:    dateTo   || null,
      date_field: state.dateField,
    });

    hide('loadingState');
    document.getElementById('genBtn').disabled = false;

    if (res.error) {
      document.getElementById('errorState').textContent = res.error;
      show('errorState'); return;
    }

    state.rows = res;
    var cfSet = {};
    res.forEach(function(row) {
      Object.keys(row.custom_fields || {}).forEach(function(k) { cfSet[k] = true; });
    });
    state.cfKeys = Object.keys(cfSet).sort();

    renderTable(res, { dateFrom: dateFrom, dateTo: dateTo, statuses: statuses, dateField: state.dateField });
    renderChips(res);
    renderCharts(res);
    show('resultsArea');
  } catch(e) {
    hide('loadingState');
    document.getElementById('genBtn').disabled = false;
    document.getElementById('errorState').textContent = e.message;
    show('errorState');
  }
}

// ---- render chips ----
function renderChips(rows) {
  var total = rows.length;
  var closed = rows.filter(function(r) { return r.is_closed; }).length;
  var totalMs = 0;
  rows.forEach(function(r) {
    (r.time_entries || []).forEach(function(e) { totalMs += (e.ms || 0); });
  });
  var h = Math.floor(totalMs / 3600000);
  var m = Math.floor((totalMs % 3600000) / 60000);
  document.getElementById('chipTotal').textContent = total;
  document.getElementById('chipClosed').textContent = closed;
  document.getElementById('chipTime').textContent = h + 'h ' + m + 'm';
}

// ---- render charts ----
var _chartInstances = {};
function renderCharts(rows) {
  // Destroy existing charts
  Object.keys(_chartInstances).forEach(function(k) {
    try { _chartInstances[k].destroy(); } catch(e) {}
  });
  _chartInstances = {};

  // Chart 1: Tasks per day (by the same field used for filtering)
  var dayField = state.dateField === 'due' ? 'due_date' : 'date_created';
  var dayTitleEl = document.getElementById('chartDayTitle');
  if (dayTitleEl) dayTitleEl.textContent = 'Tasks per ' + (state.dateField === 'due' ? 'Due Date' : 'Created Date');
  var dayMap = {};
  rows.forEach(function(r) {
    var d = r[dayField] || 'Unknown';
    dayMap[d] = (dayMap[d] || 0) + 1;
  });
  var dayLabels = Object.keys(dayMap).filter(function(d) { return d !== 'Unknown'; }).sort();
  if (dayMap['Unknown']) dayLabels.push('Unknown');
  var dayVals   = dayLabels.map(function(d) { return dayMap[d]; });
  _chartInstances['chartDay'] = new Chart(document.getElementById('chartDay'), {
    type: 'bar',
    data: {
      labels: dayLabels,
      datasets: [{ label: 'Tasks', data: dayVals,
        backgroundColor: 'rgba(124,92,191,0.7)', borderRadius: 4 }]
    },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } }
  });

  // Chart 2: Tasks per assignee
  var assigneeMap = {};
  rows.forEach(function(r) {
    if (r.assignees && r.assignees.length) {
      r.assignees.forEach(function(a) { assigneeMap[a] = (assigneeMap[a] || 0) + 1; });
    } else {
      assigneeMap['Unassigned'] = (assigneeMap['Unassigned'] || 0) + 1;
    }
  });
  var aLabels = Object.keys(assigneeMap);
  var aVals   = aLabels.map(function(a) { return assigneeMap[a]; });
  _chartInstances['chartAssignee'] = new Chart(document.getElementById('chartAssignee'), {
    type: 'bar',
    data: {
      labels: aLabels,
      datasets: [{ label: 'Tasks', data: aVals,
        backgroundColor: 'rgba(39,174,96,0.7)', borderRadius: 4 }]
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
  });

  // Chart 3: Total time tracked per assignee (hours)
  var timeByUser = {};
  rows.forEach(function(r) {
    (r.time_entries || []).forEach(function(e) {
      timeByUser[e.user] = (timeByUser[e.user] || 0) + (e.ms || 0);
    });
  });
  var tLabels = Object.keys(timeByUser);
  var tVals   = tLabels.map(function(u) { return +(timeByUser[u] / 3600000).toFixed(2); });
  _chartInstances['chartTime'] = new Chart(document.getElementById('chartTime'), {
    type: 'bar',
    data: {
      labels: tLabels,
      datasets: [{ label: 'Hours', data: tVals,
        backgroundColor: 'rgba(230,126,34,0.7)', borderRadius: 4 }]
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true } }
    }
  });
}

// ---- render table ----
function renderTable(rows, meta) {
  var count = rows.length;
  document.getElementById('resultCount').textContent = count + ' task' + (count !== 1 ? 's' : '') + ' found';

  var parts = [];
  if (meta.dateFrom || meta.dateTo) {
    var label = meta.dateField === 'due' ? 'Due' : 'Created';
    parts.push(label + ': ' + (meta.dateFrom || '...') + ' to ' + (meta.dateTo || '...'));
  }
  if (meta.statuses && meta.statuses.length) {
    parts.push('Statuses: ' + meta.statuses.map(capitalize).join(', '));
  }
  document.getElementById('resultMeta').textContent = parts.join('  |  ');

  var fixedHeaders = ['#','List','Task Name','Assignee(s)','Due Date','Priority',
                      'Time Tracked','Status','Time Estimate','Task Type','Tags'];
  var head = document.getElementById('tableHead');
  head.innerHTML = '';
  fixedHeaders.concat(state.cfKeys).forEach(function(h) {
    var th = document.createElement('th');
    th.textContent = h;
    head.appendChild(th);
  });

  var body = document.getElementById('reportBody');
  body.innerHTML = '';

  rows.forEach(function(row, i) {
    var tr = document.createElement('tr');
    var cells = [];

    cells.push('<td style="color:var(--muted);font-size:12px">' + (i+1) + '</td>');
    cells.push('<td><span class="pill pill-gray" style="font-size:10px">' + esc(row.list_name || '--') + '</span></td>');
    cells.push('<td class="task-name"><a href="' + esc(row.url) + '" target="_blank">' + esc(row.name) + '</a></td>');

    if (row.assignees && row.assignees.length) {
      cells.push('<td><div class="pills">' +
        row.assignees.map(function(a) { return '<span class="pill pill-purple">' + esc(a) + '</span>'; }).join('') +
        '</div></td>');
    } else {
      cells.push('<td><span class="null">--</span></td>');
    }

    cells.push('<td>' + (row.due_date ? esc(row.due_date) : '<span class="null">--</span>') + '</td>');

    if (row.priority) {
      var pc = row.priority.toLowerCase();
      cells.push('<td><span class="badge priority-' + pc + '">' + esc(row.priority) + '</span></td>');
    } else {
      cells.push('<td><span class="null">--</span></td>');
    }

    if (row.time_entries && row.time_entries.length) {
      var html = '<div class="time-user">';
      row.time_entries.forEach(function(e) {
        html += '<div class="time-row"><span class="time-val">' + esc(e.duration || '--') + '</span>';
        if (row.time_entries.length > 1) {
          html += '<span class="time-uname">' + esc(e.user) + '</span>';
        }
        html += '</div>';
      });
      html += '</div>';
      cells.push('<td>' + html + '</td>');
    } else {
      cells.push('<td><span class="null">--</span></td>');
    }

    cells.push('<td><span class="badge" style="background:' + row.status_color + '22;color:' + row.status_color + '">' +
      '<span class="badge-dot" style="background:' + row.status_color + '"></span>' +
      esc(row.status) + '</span></td>');

    cells.push('<td>' + (row.time_estimate
      ? '<span style="font-weight:600;color:#555">' + esc(row.time_estimate) + '</span>'
      : '<span class="null">--</span>') + '</td>');

    cells.push('<td>' + (row.task_type
      ? '<span class="badge pill-gray">' + esc(row.task_type) + '</span>'
      : '<span class="null">--</span>') + '</td>');

    if (row.tags && row.tags.length) {
      cells.push('<td><div class="pills">' +
        row.tags.map(function(t) { return '<span class="pill pill-tag">' + esc(t) + '</span>'; }).join('') +
        '</div></td>');
    } else {
      cells.push('<td><span class="null">--</span></td>');
    }

    state.cfKeys.forEach(function(k) {
      var v = (row.custom_fields || {})[k];
      cells.push('<td>' + (v ? esc(String(v)) : '<span class="null">--</span>') + '</td>');
    });

    tr.innerHTML = cells.join('');
    body.appendChild(tr);
  });
}

// ---- export CSV ----
function exportCSV() {
  var rows = state.rows;
  if (!rows || !rows.length) return;

  var fixedHeaders = ['#','List','Task Name','URL','Assignee(s)','Due Date','Priority',
                      'Time Tracked','Status','Time Estimate','Task Type','Tags'];
  var allHeaders = fixedHeaders.concat(state.cfKeys);
  var lines = [allHeaders.map(csvEsc).join(',')];

  rows.forEach(function(row, i) {
    var timeStr = (row.time_entries && row.time_entries.length)
      ? row.time_entries.map(function(e) { return e.user + ': ' + e.duration; }).join(' | ')
      : '';

    var fixedVals = [
      i + 1,
      row.list_name || '',
      row.name,
      row.url,
      (row.assignees || []).join(', '),
      row.due_date || '',
      row.priority || '',
      timeStr,
      row.status,
      row.time_estimate || '',
      row.task_type || '',
      (row.tags || []).join(', '),
    ];

    var cfVals = state.cfKeys.map(function(k) {
      return (row.custom_fields || {})[k] || '';
    });

    lines.push(fixedVals.concat(cfVals).map(csvEsc).join(','));
  });

  var blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'clickup_report_' + new Date().toISOString().slice(0,10) + '.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// ---- utils ----
async function post(url, body) {
  var res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}
function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }
function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function csvEsc(v) {
  var s = String(v == null ? '' : v);
  return /[,"\n\r]/.test(s) ? '"' + s.replace(/"/g,'""') + '"' : s;
}
function showAuthMsg(msg, type) {
  var el = document.getElementById('authMsg');
  el.textContent = msg;
  el.className = 'alert alert-' + type;
  el.classList.remove('hidden');
  if (type === 'success') setTimeout(function() { el.classList.add('hidden'); }, 3000);
}
function setConnectLoading(on) {
  document.getElementById('connectLabel').textContent = on ? 'Connecting...' : 'Connect';
}
document.getElementById('apiKey').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') connect();
});

// ════════════════════════════════════════════════════════════
// CRON MODAL
// ════════════════════════════════════════════════════════════
var cronState = {
  api_key: '', team_id: '', allLists: [], list_ids: [],
  allStatuses: [], dateField: 'created', sslEnabled: false
};

function openCronModal() {
  document.getElementById('cronModal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  cronGoStep(1);
  // pre-fill API key if already connected
  if (state.api_key) {
    document.getElementById('cronApiKey').value = state.api_key;
  }
}
function closeCronModal() {
  document.getElementById('cronModal').classList.add('hidden');
  document.body.style.overflow = '';
}
// close on overlay click
document.getElementById('cronModal').addEventListener('click', function(e) {
  if (e.target === this) closeCronModal();
});

function cronGoStep(n) {
  [1,2,3,4].forEach(function(i) {
    var el = document.getElementById('cronS' + i);
    if (el) el.classList.toggle('hidden', i !== n);
    var dot = document.getElementById('sn' + i);
    if (dot) {
      dot.classList.remove('active','done');
      if (i < n)       dot.classList.add('done');
      else if (i === n) dot.classList.add('active');
    }
  });
}

// ── Step 1: Connect ──────────────────────────────────────────
async function cronConnect() {
  var key = document.getElementById('cronApiKey').value.trim();
  if (!key) return cronMsg('cronAuthMsg', 'Enter your API key', 'error');
  document.getElementById('cronConnLabel').textContent = 'Connecting...';
  try {
    var res = await post('/api/auth', { api_key: key });
    document.getElementById('cronConnLabel').textContent = 'Connect & Continue';
    if (res.error) return cronMsg('cronAuthMsg', res.error, 'error');
    cronState.api_key = key;
    var sel = document.getElementById('cronWorkspaceSel');
    sel.innerHTML = '<option value="">-- select workspace --</option>';
    res.forEach(function(t) { sel.appendChild(new Option(t.name, t.id)); });
    cronMsg('cronAuthMsg', 'Connected!', 'success');
    setTimeout(function() { cronGoStep(2); }, 600);
  } catch(e) {
    document.getElementById('cronConnLabel').textContent = 'Connect & Continue';
    cronMsg('cronAuthMsg', 'Failed: ' + e.message, 'error');
  }
}

// ── Step 2: Lists ────────────────────────────────────────────
async function cronLoadLists() {
  var tid = document.getElementById('cronWorkspaceSel').value;
  if (!tid) return;
  cronState.team_id = tid;
  document.getElementById('cronS2Next').disabled = true;
  var field = document.getElementById('cronListField');
  var boxes = document.getElementById('cronListBoxes');
  field.style.display = 'flex';
  boxes.innerHTML = '<div class="loading-row"><div class="spinner"></div> Loading...</div>';
  try {
    var res = await post('/api/lists', { api_key: cronState.api_key, team_id: tid });
    if (res.error) { boxes.innerHTML = '<span>' + res.error + '</span>'; return; }
    cronState.allLists = res;
    boxes.innerHTML = '';
    res.forEach(function(l) {
      var item = document.createElement('div');
      item.className = 'status-item checked';
      item.dataset.listId = l.id;
      item.innerHTML =
        '<div class="status-dot" style="background:var(--purple)"></div>' +
        '<span class="status-label" style="font-size:11px">' + esc(l.name) + '</span>' +
        '<div class="status-check"></div>';
      item.onclick = function() {
        item.classList.toggle('checked');
        cronUpdateS2Next();
      };
      boxes.appendChild(item);
    });
    cronUpdateS2Next();
  } catch(e) {
    boxes.innerHTML = '<span>Error: ' + e.message + '</span>';
  }
}

function cronUpdateS2Next() {
  var any = document.querySelectorAll('#cronListBoxes .status-item.checked').length > 0;
  document.getElementById('cronS2Next').disabled = !any;
}

function cronToggleAllLists() {
  var items = document.querySelectorAll('#cronListBoxes .status-item');
  var anyUnchecked = Array.from(items).some(function(i) { return !i.classList.contains('checked'); });
  items.forEach(function(i) { anyUnchecked ? i.classList.add('checked') : i.classList.remove('checked'); });
  cronUpdateS2Next();
}

// Override cronGoStep(3) to also load statuses
var _origCronGoStep = cronGoStep;
cronGoStep = function(n) {
  if (n === 3) {
    var checked = Array.from(document.querySelectorAll('#cronListBoxes .status-item.checked'));
    cronState.list_ids = checked.map(function(i) { return i.dataset.listId; });
    if (cronState.list_ids.length === 0) { alert('Select at least one list.'); return; }
    cronLoadStatuses();
  }
  _origCronGoStep(n);
};

async function cronLoadStatuses() {
  var container = document.getElementById('cronStatusList');
  container.innerHTML = '<div class="loading-row"><div class="spinner"></div> Loading...</div>';
  try {
    var res = await post('/api/statuses', { api_key: cronState.api_key, list_ids: cronState.list_ids });
    cronState.allStatuses = res.error ? [] : res;
    container.innerHTML = '';
    (res.error ? [] : res).forEach(function(s) {
      var item = document.createElement('div');
      item.className = 'status-item checked';
      item.dataset.status = s.status;
      item.innerHTML =
        '<div class="status-dot" style="background:' + (s.color||'#888') + '"></div>' +
        '<span class="status-label">' + capitalize(s.status) + '</span>' +
        '<div class="status-check"></div>';
      item.onclick = function() { item.classList.toggle('checked'); };
      container.appendChild(item);
    });
  } catch(e) {
    container.innerHTML = '<span>Error: ' + e.message + '</span>';
  }
}

function cronToggleAllStatuses() {
  var items = document.querySelectorAll('#cronStatusList .status-item');
  var anyUnchecked = Array.from(items).some(function(i) { return !i.classList.contains('checked'); });
  items.forEach(function(i) { anyUnchecked ? i.classList.add('checked') : i.classList.remove('checked'); });
}

// ── Date field toggle (Step 3) ───────────────────────────────
function cronSwitchDateField() {
  cronState.dateField = cronState.dateField === 'created' ? 'due' : 'created';
  var sw = document.getElementById('cronDateToggle');
  var lc = document.getElementById('cronLblCreated');
  var ld = document.getElementById('cronLblDue');
  var toDue = cronState.dateField === 'due';
  if (toDue) {
    sw.classList.add('on');
    lc.style.fontWeight = '500'; lc.style.color = 'var(--muted)';
    ld.style.fontWeight = '700'; ld.style.color = 'var(--purple)';
  } else {
    sw.classList.remove('on');
    ld.style.fontWeight = '500'; ld.style.color = 'var(--muted)';
    lc.style.fontWeight = '700'; lc.style.color = 'var(--purple)';
  }
}

// ── SSL toggle (Step 4) ──────────────────────────────────────
function cronToggleSsl() {
  cronState.sslEnabled = !cronState.sslEnabled;
  var sw = document.getElementById('cronSslToggle');
  if (cronState.sslEnabled) {
    sw.classList.add('on');
    sw.style.background = 'var(--purple)';
    document.getElementById('cronSmtpPort').value = '465';
  } else {
    sw.classList.remove('on');
    sw.style.background = '#ccc';
    document.getElementById('cronSmtpPort').value = '587';
  }
}

// ── Generate script ──────────────────────────────────────────
async function generateCronScript() {
  var smtpHost  = document.getElementById('cronSmtpHost').value.trim();
  var smtpPort  = parseInt(document.getElementById('cronSmtpPort').value) || 587;
  var smtpUser  = document.getElementById('cronSmtpUser').value.trim();
  var smtpPass  = document.getElementById('cronSmtpPass').value;
  var emailFrom = document.getElementById('cronEmailFrom').value.trim();
  var emailTo   = document.getElementById('cronEmailTo').value.trim();
  var duration  = parseInt(document.getElementById('cronDuration').value) || 7;

  if (!smtpHost) return cronMsg('cronGenMsg', 'SMTP hostname is required', 'error');
  if (!smtpUser) return cronMsg('cronGenMsg', 'SMTP username is required', 'error');
  if (!smtpPass) return cronMsg('cronGenMsg', 'SMTP password is required', 'error');
  if (!emailFrom) return cronMsg('cronGenMsg', 'From email is required', 'error');
  if (!emailTo)   return cronMsg('cronGenMsg', 'To email is required', 'error');

  var statuses = Array.from(document.querySelectorAll('#cronStatusList .status-item.checked'))
    .map(function(i) { return i.dataset.status; });

  document.getElementById('cronGenBtn').disabled = true;
  document.getElementById('cronGenBtn').textContent = 'Generating...';
  cronMsg('cronGenMsg', '', 'success');

  try {
    var listsMeta = cronState.list_ids.map(function(id) {
      var found = cronState.allLists.find(function(l) { return l.id === id; });
      return { id: id, name: found ? found.name : id };
    });
    var res = await post('/api/generate_cron', {
      api_key:       cronState.api_key,
      team_id:       cronState.team_id,
      list_ids:      cronState.list_ids,
      lists_meta:    listsMeta,
      statuses:      statuses,
      date_field:    cronState.dateField,
      duration_days: duration,
      smtp_host:     smtpHost,
      smtp_port:     smtpPort,
      smtp_ssl:      cronState.sslEnabled,
      smtp_user:     smtpUser,
      smtp_password: smtpPass,
      email_from:    emailFrom,
      email_to:      emailTo,
    });

    document.getElementById('cronGenBtn').disabled = false;
    document.getElementById('cronGenBtn').innerHTML = '&#11015; Generate &amp; Download Script';

    if (res.error) return cronMsg('cronGenMsg', res.error, 'error');

    // Download the script
    var blob = new Blob([res.script], { type: 'text/x-python;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'clickup_cron.py';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    cronMsg('cronGenMsg', 'Script downloaded! Install deps: pip install requests cryptography', 'success');
  } catch(e) {
    document.getElementById('cronGenBtn').disabled = false;
    document.getElementById('cronGenBtn').innerHTML = '&#11015; Generate &amp; Download Script';
    cronMsg('cronGenMsg', 'Error: ' + e.message, 'error');
  }
}

function cronMsg(id, msg, type) {
  var el = document.getElementById(id);
  if (!msg) { el.classList.add('hidden'); return; }
  el.textContent = msg;
  el.className = 'alert alert-' + type;
  el.classList.remove('hidden');
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import threading, webbrowser
    url = "http://localhost:5000"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print("\n  ClickUp Report Generator")
    print("  -------------------------")
    print("  Running at " + url)
    print("  Press Ctrl+C to stop")
    app.run(debug=False, port=5000, host="0.0.0.0")
