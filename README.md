# Bella Vista — Restaurant Reservation Agent

A conversational AI chat agent that lets guests book, modify, and cancel reservations for Bella Vista Italian Restaurant. Built as part of an interview exercise (BRD v1.0).

---

## How to Run

### 1. Prerequisites

- Python 3.10+
- An Anthropic API key

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your API key

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_api_key_here
```

### 4. Start the server

```bash
python app.py
```

The app will be available at **http://localhost:8000**

> The server hot-reloads on file changes (uvicorn `--reload`).

---

## Project Structure

```
Resturant-Reservation-Agent/
├── app.py                  # FastAPI backend + Claude agent + tools
├── requirements.txt        # Python dependencies
├── reservations.json       # Persistent data store (auto-created on first run)
├── .env                    # API key (not committed)
├── README.md
└── static/                 # Frontend (served by FastAPI)
    ├── index.html
    ├── style.css
    └── app.js
```

---

## Stack Choice & Rationale

| Layer          | Choice                                         | Why                                                                                                                          |
| -------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Backend**    | Python + FastAPI                               | Minimal boilerplate, async-ready, serves static files natively, one-command startup.                                         |
| **LLM**        | Claude (`claude-sonnet-4-5`) via Anthropic SDK | Native tool_use support; structured tool-calling with no extra parsing.                                                      |
| **Frontend**   | Vanilla HTML/CSS/JS                            | The BRD explicitly says a heavy SPA is not required. Vanilla is faster to write, easier to inspect, and has zero build step. |
| **Data store** | `reservations.json`                            | BRD allows file-based storage. Easy to read/verify during demo. Survives server restarts.                                    |

---

## What's Implemented

### Core User Stories (all required)

- **US-1** — Book a table (happy path): collects party size, date, time, name, phone; checks availability; returns `BV-XXXX` confirmation code.
- **US-2** — Suggest alternatives: if the requested slot is full, offers up to 2 alternatives within ±90 minutes.
- **US-3** — Modify a reservation: change date, time, party size, notes, or phone by confirmation code.
- **US-4** — Cancel a reservation: look up by code, confirm with guest, mark cancelled, release slot.
- **US-5** — Special requests: free-text notes captured; agent hedges ("I'll note that…" not "you'll get…").
- **US-6** — Refuse out-of-scope: agent politely declines menu/order/unrelated questions and suggests calling.

### Business Rules Enforced

- Closed Mondays; open Tue–Sun 17:00–21:30 (30-min slots)
- Party size 1–8; larger parties directed to call
- No past-date bookings; max 60 days advance
- No double-booking (finite table inventory per slot)
- One reservation per guest per date
- Human-readable confirmation codes (`BV-XXXX`)

### Agent Tools

| Tool                 | Purpose                             |
| -------------------- | ----------------------------------- |
| `check_availability` | Validate slot + return alternatives |
| `create_reservation` | Create and persist reservation      |
| `get_reservation`    | Lookup by confirmation code         |
| `modify_reservation` | Update fields, re-validate slot     |
| `cancel_reservation` | Mark cancelled, release slot        |

---

## Assumptions (from BRD §12 Open Questions)

| Question                            | Decision                                        |
| ----------------------------------- | ----------------------------------------------- |
| Casual date formats ("next Friday") | Resolved by Claude's reasoning before tool call |
| Guest changes mind mid-booking      | Updated in place; no restart needed             |
| Fake/unusual guest names            | Accepted as-is (a real host wouldn't refuse)    |

---

## Known Gaps / Out of Scope

- No user authentication (per BRD)
- No SMS/email confirmations (logged to console only)
- No payment handling
- Session state is in-memory,restarting the server clears active sessions (but reservations persist in JSON)
- No rate limiting or input sanitisation beyond business rules
- Stretch goals (waitlist, recurring reservations, admin commands) not implemented

---

## Demo Seed Data

A reservation `BV-DEMO` is pre-loaded on first run:

- **Guest:** Alex Rivera
- **Date:** Tomorrow at 19:00
- **Party:** 4

Try: _"Can you cancel BV-DEMO?"_ or _"I'd like to change BV-DEMO to 7:30pm."_
