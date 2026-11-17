# Website Health Monitor

A SaaS-style web application that monitors the availability, response time, and history of your websites from one dashboard. Checks run automatically on the backend every 5 minutes, so monitoring continues even when your browser is closed.

> **Status:** working MVP for local use. Monitoring, email alerts and the Chrome extension are built. The deployment guide, hosted demo and health reports are still pending (see [Roadmap](#roadmap)).

---

## Features

**Available now**

- Landing page with a live demo check (nothing is saved)
- Registration and login (bcrypt password hashing, JWT authentication)
- Dashboard: overview metrics, recent activity
- Add, edit, pause, and delete up to 25 websites per account
- Manual "Check now" and automatic checks every 5 minutes
- Per-check data: HTTP status, response time, final URL, redirects, page title, content type, server header, error message
- Monitoring history with filters (website, status, period) and a per-check detail view
- Analytics: response time, observed availability, and check activity charts (24 hours / 7 days / 30 days)
- Website details page with stats, latest check, and recent issues
- Light and dark themes, responsive layout
- SSRF protections for user-submitted URLs
- Interactive API documentation (Swagger)
- **Email alerts:** verified alert address, configurable failure threshold (1 to 5 consecutive Down checks), Down and Recovered emails, retry with backoff, incident list
- **Chrome extension (Manifest V3):** toolbar popup with site status and latency, one-click manual checks, one-time pairing code (or 1-click connect), hashed and revocable tokens, remote disconnect

**Planned**

- Health reports (shareable summaries)
- Push notifications and other alert channels
- Privacy Policy and Terms pages
- Deployment guide and Chrome Web Store listing

---

## How health is classified

"Reachable" and "healthy" are different things. A server that answers with an error is reachable but not healthy.

| Result | Status |
|---|---|
| 2xx response, received within 2000 ms | **Operational** |
| 2xx response slower than 2000 ms | **Degraded** |
| 3xx that did not resolve, or 4xx response | **Degraded** |
| 5xx response | **Down** |
| Timeout, DNS failure, SSL error, connection error, blocked URL | **Down** |
| Not checked yet | **Unknown** |

**Response time** is the time until the response headers arrive, including any redirects, in milliseconds.

**Observed availability** is the share of recorded checks that were Operational in the selected period. It is not a formal SLA figure.

---

## Architecture

```text
Browser (landing, dashboard)           Chrome extension (popup)
            |                                     |
            +---------------+---------------------+
                            |
                     FastAPI backend
              +-------------+--------------+
              |                            |
       SQLite database          APScheduler (every 5 min)
       (SQLAlchemy ORM)                    |
                                    Monitoring engine
                              (SSRF-safe HTTP checks)
```

The backend serves the frontend files itself, so there is only one process to run.

---

## Technology stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2, SQLite, Requests, BeautifulSoup, APScheduler 3, PyJWT, bcrypt, Pydantic
- **Frontend:** HTML, CSS, vanilla JavaScript, Chart.js (loaded from the jsDelivr CDN)

---

## Project structure

```text
api-health-monitor/
├── backend/
│   ├── main.py         App, routers, lifespan, demo endpoint, static files
│   ├── config.py       Environment configuration
│   ├── database.py     SQLAlchemy engine/session (+ legacy sqlite3 helpers)
│   ├── models.py       Users, websites, checks, extension pairing/tokens
│   ├── schemas.py      Pydantic request/response models
│   ├── security.py     Password hashing, JWT, pairing-code helpers
│   ├── auth.py         Register, login, current user, extension pairing
│   ├── alerts.py       Incidents, alert emails, retry queue
│   ├── alert_routes.py Alert settings, verification, test email, incidents API
│   ├── notifications.py SMTP email sending
│   ├── websites.py     Website CRUD, history, stats, analytics
│   ├── monitor.py      Monitoring engine and SSRF protection
│   ├── checks.py       Runs and stores a check (overlap guard)
│   └── scheduler.py    5-minute background scheduler
├── frontend/
│   ├── theme.css       Shared design system
│   ├── style.css       Landing page styles
│   ├── index.html      Landing page
│   ├── script.js       Landing page logic (auth dialog, live demo)
│   ├── dashboard.html  Dashboard page
│   ├── dashboard.css   Dashboard styles
│   ├── dashboard.js    Dashboard logic and charts
│   ├── alerts.html     Alerts and incidents page
│   └── alerts.js       Alerts page logic
├── extension/          Chrome extension (Manifest V3), see extension/README.md
├── tests/              pytest tests (alerts)
├── .env.example
├── requirements.txt
└── README.md
```

---

## Setup (Windows PowerShell)

```powershell
# 1. Create and activate a virtual environment (Python 3.12)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env file with a freshly generated secret
$secret = python -c "import secrets; print(secrets.token_urlsafe(48))"
@"
JWT_SECRET=$secret
ACCESS_TOKEN_MINUTES=60
CHECK_INTERVAL_MINUTES=5
REQUEST_TIMEOUT_SECONDS=10
"@ | Set-Content -Encoding ascii .env
```

The database file `monitor.db` is created automatically on first start. Never commit `.env` or the database files (they are in `.gitignore`).

> **Tip:** if the project lives in a OneDrive folder, keep the folder set to "Always keep on this device" or move it elsewhere. SQLite uses extra `-wal` and `-shm` files that cloud sync can interfere with.

---

## Run

```powershell
python -m uvicorn backend.main:app --reload
```

| URL | Purpose |
|---|---|
| http://127.0.0.1:8000 | Landing page |
| http://127.0.0.1:8000/dashboard | Dashboard (requires login) |
| http://127.0.0.1:8000/docs | Swagger API documentation |
| http://127.0.0.1:8000/health | Health endpoint |

After changing frontend files, hard-refresh the browser with **Ctrl+F5**.

On startup the terminal should show `Scheduler started: every 5 minute(s)`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET` | *(required)* | Secret used to sign tokens. The app refuses to start without it. |
| `ACCESS_TOKEN_MINUTES` | `60` | Login token lifetime |
| `CHECK_INTERVAL_MINUTES` | `5` | How often the scheduler checks all websites |
| `REQUEST_TIMEOUT_SECONDS` | `10` | Timeout for each HTTP request |
| `SCHEDULER_ENABLED` | `true` | Set to `false` to disable the scheduler |
| `DATABASE_URL` | local SQLite file | Override the database location |
| `EXTENSION_TOKEN_DAYS`, `PAIRING_CODE_MINUTES` | `30`, `10` | Chrome extension token lifetime and pairing-code lifetime |
| `APP_BASE_URL` | `http://127.0.0.1:8000` | Base URL used for links inside emails. Set to your public URL when deployed |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_SSL` | *(empty)*, `587`, `false` | Outgoing email server. Alerts are disabled until `SMTP_HOST` and `EMAIL_FROM` are set |
| `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM` | *(empty)* | SMTP login and sender address. For Gmail use an App password |

---

## API overview

Full interactive documentation is available at `/docs`. Endpoints marked 🔒 need the header `Authorization: Bearer <token>`.

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create an account, returns a token |
| POST | `/auth/login` | Log in, returns a token |
| GET | `/me` 🔒 | Current user |
| POST | `/websites` 🔒 | Add a website |
| GET | `/websites` 🔒 | List websites with their latest check |
| GET | `/websites/overview` 🔒 | Dashboard summary metrics |
| GET | `/websites/activity` 🔒 | Checks across all websites (filters: `website_id`, `status`, `hours`, `limit`, `offset`) |
| GET | `/websites/analytics` 🔒 | Chart data for all websites (`hours`) |
| GET / PUT / DELETE | `/websites/{id}` 🔒 | Read, update name or pause, delete |
| POST | `/websites/{id}/check` 🔒 | Run a check now |
| GET | `/websites/{id}/history` 🔒 | Check history for one website |
| GET | `/websites/{id}/stats` 🔒 | Stats and time series for one website |
| POST | `/extension/pair/start` 🔒 | Generate a one-time pairing code |
| POST | `/extension/pair/exchange` | Exchange a pairing code for an extension token |
| GET | `/extension/status` 🔒 | Active extension sessions |
| POST | `/extension/disconnect` 🔒 | Revoke extension tokens |
| GET | `/extension/download` | Download the extension as a zip |
| GET / PUT | `/alerts/settings` 🔒 | Alert email, on/off, failure threshold |
| POST | `/alerts/verification` 🔒 | Send the email verification link |
| POST | `/alerts/test` 🔒 | Send a test alert email (rate limited) |
| GET | `/alerts/incidents` 🔒 | List incidents |
| POST | `/demo/check` | Public live check for the landing page (rate limited, not saved) |
| GET | `/health` | Service health |

Every website query is filtered by the logged-in user, so users can only access their own websites and history.

---

## Security

**Implemented**

- Passwords stored only as bcrypt hashes
- JWT secret loaded from environment variables, never from frontend code
- Per-user data isolation on every website and history query
- SSRF protections on every URL (when adding, and again on every redirect hop):
  - only `http` and `https`
  - credentials embedded in the URL are rejected
  - only ports 80, 443, 8080, 8443
  - the hostname is resolved and every IP must be public (loopback, private, link-local, multicast, and reserved ranges are blocked)
  - redirects are followed manually, at most 5, and validated at each step
  - 10-second request timeout, 30-second total cap, and only the first 256 KB of HTML is read
- All server-provided text is HTML-escaped before display in the dashboard (XSS prevention)
- Basic security headers; rate limiting on the public demo endpoint

**Known limitations. This is not production-hardened.**

- **DNS rebinding:** the hostname is validated before the request, but the HTTP library resolves it again when connecting. A malicious DNS server could return a different address the second time. Fully closing this needs connecting to the validated IP directly, or network-level egress rules.
- **Token storage:** the login token is kept in `localStorage`, so an XSS bug could expose it. HttpOnly cookies are a stronger option.
- **Logout** only removes the token in the browser. Tokens are not revoked server-side and expire after `ACCESS_TOKEN_MINUTES`.
- **No rate limiting on login/register**, no email verification, and no password reset.
- The demo rate limiter is in memory and keyed by client IP. Behind a reverse proxy it needs proxy-aware IP handling.
- No automated security review has been done.

---

## Known limitations

- **Single backend instance only.** The scheduler runs inside the web process. Do not run multiple workers (`--workers N`) or multiple copies, or every website will be checked several times. For scaled deployments, move checks into a separate worker or a managed queue.
- SQLite is fine for an MVP and a single instance. For multiple instances or heavier load, move to PostgreSQL.
- A website's URL cannot be edited. Delete it and add it again.
- Monitoring only checks a single URL, so it does not detect content problems, expired-certificate warnings, or multi-step failures.
- Checks older than 90 days are deleted automatically.
- Automated tests currently cover alerts only. Monitoring, auth and extension pairing are not covered yet.
- The extension is installed manually (developer mode). It is not on the Chrome Web Store, and `extension/config.js` and `manifest.json` must be pointed at your production domain after deployment.

---

## Deployment (guidance, not yet performed)

Before exposing this to the internet:

1. Use a strong, unique `JWT_SECRET` set through the host's environment settings, never committed.
2. Serve over **HTTPS** behind a reverse proxy.
3. Run **one** Uvicorn worker (without `--reload`).
4. Store the SQLite file on a persistent volume, or move to PostgreSQL.
5. Restrict outbound network access from the server where possible (defence in depth for SSRF).
6. Review the security limitations above.

---

## Roadmap

1. ~~Chrome extension~~ done
2. ~~Email alerts when a website goes down~~ done
3. Deployment guide and hosted demo
4. Health reports, Privacy and Terms pages
5. Wider automated tests, and a separate worker for scaled deployments