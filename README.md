# ClickUp Report Generator

A self-contained Flask web app that connects to your ClickUp workspace and generates interactive task reports — with charts, CSV export, and an automated email cron script generator.

---

## Quick Start

**1. Install dependencies**
```bash
pip install flask requests cryptography
```

**2. Run**
```bash
python clickup_report.py
```

The browser opens automatically at `http://localhost:5000`.

---

## Features

### Report Generator
- Connect with your ClickUp API key
- Select **multiple lists** across any workspace
- Filter by **task status** (multi-select)
- Filter by **date range** — toggle between Created date or Due date
- Displays: Task Name, List, Assignee(s), Due Date, Priority, Time Tracked (per user), Status, Time Estimate, Task Type, Tags, and all **custom fields** as dynamic columns
- **Summary chips** — Total Tasks, Closed Tasks, Total Time Tracked
- **3 charts** — Tasks per day, Tasks per assignee, Time tracked per assignee
- **Export CSV** — full report with all columns, UTF-8 encoded

### Cron Script Generator
Click **Create Cron** in the header to open a 4-step wizard that produces a ready-to-run Python script (`clickup_cron.py`):

| Step | What you configure |
|------|--------------------|
| 1 | ClickUp API key (validated live) |
| 2 | Workspace + Lists (multi-select) |
| 3 | Statuses, look-back duration (e.g. last 7 days), date field |
| 4 | SMTP server, port, SSL/TLS, credentials, From/To email |

The generated script:
- Fetches tasks for the configured period
- Sends an email with a **text summary** (tasks by status, top assignees) and the **full CSV as an attachment**
- **Encrypts** the API key and SMTP password using [Fernet symmetric encryption](https://cryptography.io/en/latest/fernet/) — credentials are never stored in plaintext

---

## Scheduling the Cron Script

After downloading `clickup_cron.py`, install its dependencies:
```bash
pip install requests cryptography
```

### Linux / macOS — crontab
```bash
crontab -e
# Add (daily at 8am):
0 8 * * * python3 /path/to/clickup_cron.py >> /var/log/clickup_cron.log 2>&1
```

### Windows — Task Scheduler

**Option A — Command line (run as Administrator)**
```cmd
schtasks /create /tn "ClickUpReport" /tr "python C:\path\to\clickup_cron.py" /sc daily /st 08:00
```

**Option B — GUI**
1. Open **Task Scheduler** → Create Basic Task
2. Trigger: Daily at your preferred time
3. Action: Start a program
   - Program: `python` (or full path e.g. `C:\Python312\python.exe`)
   - Arguments: `C:\path\to\clickup_cron.py`
4. Finish

---

## Getting Your ClickUp API Key

1. Log in to ClickUp
2. Go to **Settings** → **Apps**
3. Click **Generate** under Personal API Token
4. Copy the token (starts with `pk_`)

---

## Security Notes

- The API key and SMTP password in the generated cron script are encrypted with **AES-128-CBC via Fernet** — they appear as opaque byte strings, not plaintext
- The encryption key is embedded in the script — keep `clickup_cron.py` private
- The main `clickup_report.py` never stores your API key to disk

---

## Sharing

Send your coworker just the single file:

```
clickup_report.py
```

They install the dependencies and run it — no configuration files, no database, no setup.

---

## Requirements

| Package | Purpose |
|---------|---------|
| `flask` | Web server |
| `requests` | ClickUp API calls |
| `cryptography` | Encrypting credentials in the cron script |

Python 3.8+ required.
