# Agentic Market Arbitrage — API Gateway

A **FastAPI** service that exposes agent-friendly HTTP APIs: a **paid scrape-to-arbitrage** tool (`/v1/clean-data`), an **x402-style payment gate** (simulated via the `payment-signature` header), a **public discovery** document for directories such as **agentic.market**, and a small **HTML admin dashboard** for demos and observability.

This repository is intended as a **gateway / product surface** for AI agents: scrape web pages, normalize text, and return **structured mock arbitrage signals** today (real LLM scoring is left as a documented TODO in code).

---

## Table of contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the server](#running-the-server)
7. [API reference](#api-reference)
8. [Payment model (x402-style)](#payment-model-x402-style)
9. [Scraping behavior](#scraping-behavior)
10. [Admin dashboard](#admin-dashboard)
11. [OpenAPI and interactive docs](#openapi-and-interactive-docs)
12. [Troubleshooting](#troubleshooting)
13. [Project layout](#project-layout)

---

## Features

- **Health check** — `GET /health` for load balancers and smoke tests.
- **Service discovery** — `GET /v1/discovery` returns JSON metadata (name, description, pricing, category, capabilities) for **agent indexers** (e.g. agentic.market).
- **Scrape-to-arbitrage (mock)** — `POST /v1/clean-data` accepts a JSON body, fetches the URL with **httpx**, strips HTML with **BeautifulSoup**, and returns a **fixed mock arbitrage** JSON payload (no live LLM call in the current handler).
- **Simulated payment gate** — Paid route requires a non-empty **`payment-signature`** header; missing header yields **HTTP 402** plus a **`PAYMENT-REQUIRED`** response header (Base64 JSON payment hints).
- **CORS** — Permissive defaults for agent clients; **`PAYMENT-REQUIRED`** is exposed so browsers can read it on 402 responses.
- **Admin dashboard** — `GET /dashboard` serves a dark-mode HTML page with **simulated revenue**, **successful scrape count**, **mock LLM mode**, and a **rolling log** of the last five scrape attempts.
- **Settings** — **Pydantic Settings** loads `.env` and environment variables (`app/core/config.py`).

---

## Architecture

- **Entry point:** `app/main.py` — creates the FastAPI `app`, CORS, Jinja2 templates for `/dashboard`, in-process **dashboard metrics**, and mounts the API router.
- **HTTP routes:** `app/api/routes.py` — all JSON routes under a single `APIRouter`; payment dependency on `/v1/clean-data` only.
- **Configuration:** `app/core/config.py` — `Settings` with optional `API_KEY` / `OPENAI_BASE_URL` for future OpenAI-compatible integrations (the current `/v1/clean-data` handler does not call an LLM).

There is **no global payment middleware** on every path: only routes that attach `Depends(require_payment_signature)` are gated.

---

## Requirements

- **Python 3.12+** (the project has been used with **Python 3.14**; use a supported version available on your machine).
- **pip** and a **virtual environment** are strongly recommended.

---

## Installation

**Always run install commands from the repository root** (the directory that contains `requirements.txt`).

```bash
cd /path/to/Agentic-Market-Arbitrage-main

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

### Why installs fail from `~`

If you see `ERROR: Could not open requirements file`, your shell is usually in your **home directory**, not the project folder. `cd` into the repo first, then run `pip install -r requirements.txt`.

### Jinja2 and the dashboard

The dashboard uses **Starlette `Jinja2Templates`**, which requires **Jinja2** to be installed in the **same environment** as **uvicorn**. It is listed in `requirements.txt` (`jinja2==3.1.6`). If you see:

`ImportError: jinja2 must be installed to use Jinja2Templates`

…re-run `pip install -r requirements.txt` inside your activated `.venv`, then restart the server.

---

## Configuration

Copy `.env.example` to `.env` and adjust values. Pydantic Settings reads `.env` from the **process working directory** (typically the repo root when you start uvicorn from there).

| Variable | Purpose |
|----------|---------|
| `API_KEY` | Reserved for future **OpenAI-compatible** API calls (not required for the current mock `/v1/clean-data` flow). |
| `OPENAI_BASE_URL` | Optional; maps to `openai_base_url` in settings for Azure or custom hosts when you wire an LLM. |
| `APP_NAME`, `ENVIRONMENT`, `DEBUG` | Optional app metadata and debug flag (`app/core/config.py`). |

Secrets and local overrides should stay in `.env`; that file is **gitignored** (see `.gitignore`).

---

## Running the server

From the **repository root**:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **`--reload`** — development auto-reload (spawns a parent and worker process on the listen port; that is normal).
- **`0.0.0.0`** — listen on all interfaces (reachable as `127.0.0.1` locally).

**Important:** Uvicorn must be able to import the `app` package. That means the **current working directory** should be the repo root so `app.main:app` resolves correctly.

### Port already in use

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
kill <PID>
```

Or:

```bash
kill $(lsof -t -iTCP:8000 -sTCP:LISTEN)
```

---

## API reference

| Method | Path | Auth / headers | Description |
|--------|------|----------------|-------------|
| `GET` | `/health` | None | Liveness: `{"status":"ok","message":"Gateway is running"}`. |
| `GET` | `/v1/discovery` | None | Public service catalog JSON for agent directories. |
| `POST` | `/v1/clean-data` | **`payment-signature`**: any non-empty string (simulated pay) | JSON body `{"url":"<https...>"}`; scrapes page; returns mock arbitrage JSON or **400** with error detail. |
| `GET` | `/dashboard` | None | HTML admin dashboard (not listed in OpenAPI schema). |

### `GET /v1/discovery`

Returns:

- `name`: **Blaze Scrape-to-Arbitrage**
- `description`, `pricing` (`amount`, `asset`, `network`), `category`, `capabilities`

No payment header required so indexers can fetch it anonymously.

### `POST /v1/clean-data`

**Request body (JSON)** — Pydantic model **`ScrapeRequest`**:

```json
{ "url": "https://example.com" }
```

- Field **`url`** must be a non-empty string. Extra JSON keys are **ignored** (`extra="ignore"`).
- Do **not** name the FastAPI body parameter `request`; that name is reserved for Starlette’s `Request` and breaks body parsing with Pydantic v2.

**Headers**

- **`payment-signature`**: required, non-empty (simulates an authorized payment). When valid, the server increments **simulated revenue** by **$0.01** for dashboard metrics.

**Success response (200)** — example shape:

```json
{
  "status": "success",
  "source_url": "https://example.com",
  "arbitrage_opportunity": true,
  "confidence_score": 0.95,
  "action_recommendation": "BUY",
  "summary": "The scraped data indicates a price discrepancy between Exchange A and Exchange B."
}
```

The arbitrage fields are **mocked**; they do not reflect real market data. The scraper still runs so failures (blocked sites, timeouts, empty text) return **HTTP 400** with a helpful `detail` string.

**Example `curl`**

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/clean-data \
  -H "Content-Type: application/json" \
  -H "payment-signature: test" \
  -d '{"url":"https://example.com"}'
```

---

## Payment model (x402-style)

The paid tool uses a **FastAPI dependency** `require_payment_signature` (`app/api/routes.py`).

1. **Missing or blank `payment-signature`**
   - **HTTP 402** `Payment Required`
   - Response header **`PAYMENT-REQUIRED`**: Base64-encoded JSON, e.g.  
     `price`, `asset` (`USDC`), `network` (`base`), `address` (placeholder zero address)

2. **Header present and non-empty**
   - Dependency calls **`record_payment_verified()`** in `app/main.py` (**+$0.01** simulated revenue).
   - Request proceeds to the scraper handler.

This is a **demonstration** of how agents could receive machine-readable payment hints; it does not perform on-chain verification.

---

## Scraping behavior

- **Client:** `httpx.AsyncClient` with timeouts, redirects, and a **browser-like `User-Agent`** to reduce trivial blocking.
- **Parse:** **BeautifulSoup** (`html.parser`) removes `<script>` and `<style>`, then extracts visible text.
- **Limits:** Response body capped at **8 MiB**; excessive blank lines collapsed.
- **Errors:** Most scrape and URL problems are returned as **HTTP 400** with a string `detail` (including upstream **403** from sites that block bots, e.g. some Wikipedia pages).

A code comment marks a **TODO** to pass `extracted_text` to an **LLM (AWS/Azure)** once credits and product requirements are finalized.

---

## Admin dashboard

- **URL:** `http://127.0.0.1:8000/dashboard`
- **Template:** `app/templates/dashboard.html` (dark theme).
- **Metrics (in-memory, per process; reset on restart):**
  - **Total revenue** — increases by **$0.01** each time `payment-signature` is accepted on the gated route.
  - **Requests processed** — count of **successful** scrapes (completed pipeline, mock response returned).
  - **Current arbitrage logic** — shows a **LIVE** badge and **Mode: Mock LLM (Credits Saved)**.
  - **Recent logs** — up to **five** most recent rows: URL + status (`success` or error detail snippet).

The **`favicon.ico` 404** in server logs is normal; browsers request it automatically.

---

## OpenAPI and interactive docs

When the server is running:

- **Swagger UI:** `http://127.0.0.1:8000/docs`
- **OpenAPI JSON:** `http://127.0.0.1:8000/openapi.json`

The `/dashboard` route is registered with `include_in_schema=False`, so it may not appear in Swagger.

---

## Troubleshooting

| Issue | What to check |
|--------|----------------|
| `ModuleNotFoundError` (e.g. `httpx`, `jinja2`) | Run `pip install -r requirements.txt` inside the **same venv** you use for `uvicorn`. |
| `Couldn't connect to server` / connection refused | Start uvicorn; confirm `lsof` shows something listening on **8000**. |
| `404` on `/v1/clean-data` | Ensure you are on a branch/commit that registers the route; start uvicorn from the **repo root**. |
| `402` on `/v1/clean-data` | Add a non-empty **`payment-signature`** header. |
| Wikipedia or other sites return **400** with **403** wording | Origin is blocking automated fetches; try `https://example.com` or a page you control. |
| `Address already in use` | Another uvicorn (or process) is bound to 8000; stop it or pick another `--port`. |

---

## Project layout

```
Agentic-Market-Arbitrage-main/
├── README.md                 # This file
├── requirements.txt          # Pinned Python dependencies
├── .env.example              # Example environment keys
├── .gitignore                # e.g. .env, venv/, __pycache__/
└── app/
    ├── main.py               # FastAPI app, CORS, dashboard route, metrics helpers
    ├── api/
    │   └── routes.py         # Health, discovery, clean-data, payment dependency
    ├── core/
    │   └── config.py         # Pydantic Settings
    └── templates/
        └── dashboard.html    # Admin dashboard UI
```

---

## Contributing and branches

Feature work may live on branches such as `cursor/<short-description>`. Push your branch and open a pull request against `main` on your Git host when ready.

---

## Disclaimer

Mock arbitrage output is **not financial advice** and **not** derived from live exchange feeds in the current implementation. Use appropriate compliance, rate limits, and robots policies when scraping third-party sites.
