# HoM Pipeline Report

Generates the fortnightly sales pipeline report from Pipedrive and emails it out:
a newsletter-style HTML email with the pipeline highlights, and the full
four-sheet Excel workbook attached.

Built to match the supplied `HoM_Pipeline_Report` workbook and
`pipeline-report-email.html` design exactly.

---

## What it produces

**The workbook** (`HoM_Pipeline_Report_<date>.xlsx`) — four sheets, same layout,
palette, number formats, conditional formatting and charts as the template:

| Sheet | Contents |
|---|---|
| **Summary** | Four KPI tiles, pipeline by stage / account owner / account manager / industry, and the two bar charts |
| **Open Deals** | One row per open deal, 18 columns, autofilter, stage and status colour coding, weighted-value data bars |
| **Brands** | One row per brand with open deals: status, industry, pipeline value, furthest stage, owner, contacts |
| **Settings** | Report date, FX rates used, and the stage probabilities everything is calculated from |

Values stay as **live formulas**, not hard-coded numbers, so the recipient can
re-sort, filter and audit the figures in Excel.

**The email** — the newsletter design: headline stats, top 10 clients and top 10
new business by weighted value, pipeline by stage, and weighted value by account
owner.

---

## How the numbers are derived

| Field | Source |
|---|---|
| Brand | Pipedrive organisation name |
| Account Owner | Deal owner |
| Account Manager | Deal custom field named "Account Manager" (resolved by name, not by key) |
| Client status | `Existing client` if the organisation's `won_deals_count` is above zero, else `New business` |
| Stage / Probability | Pipeline stage and its default probability |
| Value (£) | Deal value × the FX rate on the Settings sheet |
| Weighted (£) | Value (£) × stage probability |
| Industry / Sub-industry | Organisation custom fields — "Wide niche" / "Narrow niche" in this account |
| Days in stage | Report date − last stage change |

Custom fields are looked up **by their display name** at run time, so the app
keeps working if the CRM is rebuilt and the field keys change. Matching is
deliberately strict — an exact name match, then a whole-word match on a long
label — because silently matching the wrong field is worse than leaving a
column blank. `GET /preview/fields` lists every field the account has next to
the ones the report matched; if something is named unusually, paste its key
into `PIPEDRIVE_FIELD_*` to pin it.

Client status reads each organisation's own `won_deals_count` rather than
sweeping won deals, because `/api/v2/deals` hides archived deals and Pipedrive
archives old won ones — long-standing clients were otherwise counted as new
business.

FX rates are fetched live each run and written into the Settings sheet; if the
lookup fails it falls back to `FX_FALLBACK_USD_GBP` / `FX_FALLBACK_EUR_GBP` so a
scheduled report never fails to send because a rates API is down.

---

## Setup

### 1. Pipedrive token

Pipedrive → *Settings → Personal preferences → API* → copy the token into
`PIPEDRIVE_API_TOKEN`.

### 2. Gmail

Either route sends **as you**, so the report lands in your Sent folder and
replies come back to you.

#### App Password — recommended, about two minutes

1. Turn on 2-Step Verification on the sending account, if it isn't already
2. Go to <https://myaccount.google.com/apppasswords>
3. Name it anything (e.g. "Pipeline report"), and copy the 16 characters
4. Set `MAIL_FROM` to that Gmail address and `GMAIL_APP_PASSWORD` to the code

That's it — nothing to register with Google Cloud. The app sends over
`smtp.gmail.com`, and Gmail copies SMTP-sent mail into Sent automatically.
Spaces in the displayed password are stripped for you.

Revoke it any time from the same page; it grants sending only, not inbox access.

#### OAuth — only if App Passwords are disabled

Some Workspace admins turn App Passwords off. In that case set
`MAIL_TRANSPORT=oauth` and:

1. [Google Cloud Console](https://console.cloud.google.com/) → create or pick a project
2. *APIs & Services → Library* → enable **Gmail API**
3. *OAuth consent screen* → External → add yourself as a **Test user**
4. *Credentials → Create credentials → OAuth client ID → Desktop app*
5. On your own machine:

```bash
pip install google-auth-oauthlib
GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... python scripts/gmail_oauth.py
```

It opens a browser, you approve the "send email" permission, and prints the
three `GMAIL_*` values. Only the `gmail.send` scope is requested — it can send
mail as you and nothing else, and cannot read your inbox.

### 3. Deploy to Railway

```bash
railway init
railway up
```

`railway.json` builds from the `Dockerfile` and health-checks `/health`. Set the
variables from `.env.example` in the Railway dashboard.

The port is handled by the Dockerfile's `CMD`, which expands `$PORT` through a
shell. Do **not** set a custom start command of `uvicorn ... --port $PORT` in
`railway.json` or the Railway dashboard — Railway runs the start command without
a shell, so `$PORT` arrives as a literal string and the container crashloops
with `Invalid value for '--port': '$PORT' is not a valid integer`.

---

## Testing before it goes to anyone

`TEST_MODE=true` (the default) means:

* every send is redirected to `TEST_RECIPIENT` — the real To/Cc are never used
* the subject is prefixed `[TEST]`
* a banner at the top of the email states who it *would* have gone to

Endpoints (all but `/health` require the `X-Admin-Token` header when
`ADMIN_TOKEN` is set):

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status, whether credentials are configured, next scheduled runs |
| `GET /preview/email` | The newsletter in your browser, exactly as it will be sent |
| `GET /preview/excel` | Downloads the workbook |
| `GET /preview/data` | The underlying numbers as JSON, for spot-checking against Pipedrive |
| `GET /preview/fields` | Every Pipedrive field name, and which ones the report matched |
| `POST /run` | Builds and sends now (honours `TEST_MODE`) |
| `POST /run?dry_run=true` | Builds everything and reports what it *would* send, without sending |

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://<app>.up.railway.app/preview/email
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" "https://<app>.up.railway.app/run"
```

Locally, without deploying:

```bash
pip install -r requirements.txt
python scripts/preview_local.py            # live Pipedrive data
python scripts/preview_local.py --fixture  # bundled sample data, no credentials needed
```

Writes `out/email.html` and `out/report.xlsx`.

**When you are happy**, set `TEST_MODE=false` and fill in `MAIL_TO` / `MAIL_CC`.

---

## Scheduling

Off by default (`SCHEDULE_ENABLED=false`) until a cadence is confirmed. To turn
it on:

```
SCHEDULE_ENABLED=true
SCHEDULE_CRON=0 9 * * WED      # 5-field cron, in REPORT_TIMEZONE
SCHEDULE_FORTNIGHTLY=true
SCHEDULE_ANCHOR_DATE=2026-09-16  # a date in a week it SHOULD send
```

The cron fires weekly and the fortnightly gate skips the off weeks, so the
cadence stays locked to the anchor week and does not drift. `GET /health` lists
the next three real send dates. Daylight-saving shifts are handled by
`REPORT_TIMEZONE`.

Railway's own cron can be used instead — point a scheduled job at
`POST /run` and set `SCHEDULE_ENABLED=false`.

---

## Tests

```bash
python -m pytest tests/ -q
```

62 tests. They run against the 129 real deals in
`assets/template_reference.xlsx`, so no API credentials are needed, and they
assert the output reproduces the reference report exactly — headline totals,
per-stage and per-owner breakdowns, workbook structure and formulas, and the
email's figures and bar widths.

---

## Layout

```
app/
  config.py       env-driven settings
  pipedrive.py    REST client, resolves custom fields by name
  fx.py           live GBP rates with fallback
  model.py        Pipedrive payloads -> report rows and aggregates
  excel.py        the workbook
  email_html.py   the newsletter
  formatting.py   £4.35m / £992k, bar widths
  mailer.py       Gmail API send
  report.py       collect -> build -> send
  scheduler.py    fortnightly cadence
  web.py          health, previews, manual trigger
templates/
  email.html.j2   the newsletter design
scripts/
  gmail_oauth.py    one-off: mint the refresh token
  preview_local.py  render locally without sending
```

## Notes

* Brand names come through exactly as they are in Pipedrive. A few in the sample
  email were tidied by hand (`Mars Royal Canin` → `Royal Canin`,
  `stellaris Robotics` → `Stellaris Robotics`); tidy them in the CRM and the
  report follows.
* Deals with no linked organisation are skipped — there is no brand to report
  them under. They will show up as a gap between the Pipedrive deal count and
  the report's.
