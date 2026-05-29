"""
Bella Vista Restaurant Reservation Agent — Backend
FastAPI app with Claude tool-use integration and a JSON-based reservation store.
"""

import json
import logging
import os
import random
import string
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DATA_FILE = Path("reservations.json")
STATIC_DIR = Path("static")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bella_vista")

# ---------------------------------------------------------------------------
# Business constants
# ---------------------------------------------------------------------------

OPERATING_DAYS = {1, 2, 3, 4, 5, 6}  # Tue=1 … Sun=6 (Mon=0 excluded)
OPEN_HOUR = 17
CLOSE_HOUR = 22
MAX_PARTY = 8
MIN_PARTY = 1
MAX_ADVANCE_DAYS = 60

VALID_TIMES = [
    f"{h:02d}:{m:02d}"
    for h in range(OPEN_HOUR, CLOSE_HOUR)
    for m in (0, 30)
]  # 17:00 … 21:30

TABLE_INVENTORY = (
    [{"id": f"T{i}", "seats": 2} for i in range(1, 5)]   # 4 × 2-seat
    + [{"id": f"T{i}", "seats": 4} for i in range(5, 11)] # 6 × 4-seat
    + [{"id": f"T{i}", "seats": 6} for i in range(11, 13)]# 2 × 6-seat
)

# ---------------------------------------------------------------------------
# Reservation Store
# ---------------------------------------------------------------------------


class ReservationStore:
    """Manages reservations persisted to a JSON file."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.tables = TABLE_INVENTORY
        self._data: dict[str, Any] = {"reservations": []}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        if self.filepath.exists():
            with open(self.filepath, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info("Loaded %d reservations from %s", len(self._data["reservations"]), self.filepath)
        else:
            self._seed()

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def _seed(self):
        """Initialize with the BV-DEMO reservation."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self._data = {
            "reservations": [
                {
                    "confirmation_code": "BV-DEMO",
                    "date": tomorrow,
                    "time": "19:00",
                    "party_size": 4,
                    "table_id": "T5",
                    "guest_name": "Alex Rivera",
                    "phone": "555-0123",
                    "notes": "",
                    "status": "confirmed",
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            ]
        }
        self._save()
        logger.info("Seeded reservations.json with BV-DEMO")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_reservations(self) -> list[dict]:
        return [r for r in self._data["reservations"] if r["status"] == "confirmed"]

    def _occupied_table_ids(self, date_str: str, time_str: str) -> set[str]:
        occupied = set()
        for r in self._active_reservations():
            if r["date"] == date_str and r["time"] == time_str:
                table_id_str = r["table_id"]
                if "," in table_id_str:
                    occupied.update([tid.strip() for tid in table_id_str.split(",")])
                else:
                    occupied.add(table_id_str)
        return occupied

    def _find_table(self, party_size: int, date_str: str, time_str: str) -> str | None:
        """Return table ID(s) (comma-separated if combined) that fit the party and are free."""
        occupied = self._occupied_table_ids(date_str, time_str)
        free_tables = [t for t in self.tables if t["id"] not in occupied]

        # 1. Try to find a single table first
        single_candidates = sorted(
            [t for t in free_tables if t["seats"] >= party_size],
            key=lambda t: t["seats"]
        )
        if single_candidates:
            return single_candidates[0]["id"]

        # 2. Try to find a combination of 2 tables
        from itertools import combinations
        two_table_candidates = []
        for t1, t2 in combinations(free_tables, 2):
            total_seats = t1["seats"] + t2["seats"]
            if total_seats >= party_size:
                two_table_candidates.append(((t1, t2), total_seats))

        if two_table_candidates:
            # Sort by total seats (ascending to minimize waste)
            two_table_candidates.sort(key=lambda x: x[1])
            pair = two_table_candidates[0][0]
            return f"{pair[0]['id']},{pair[1]['id']}"

        # 3. Try to find a combination of 3 tables
        three_table_candidates = []
        for t1, t2, t3 in combinations(free_tables, 3):
            total_seats = t1["seats"] + t2["seats"] + t3["seats"]
            if total_seats >= party_size:
                three_table_candidates.append(((t1, t2, t3), total_seats))

        if three_table_candidates:
            three_table_candidates.sort(key=lambda x: x[1])
            triple = three_table_candidates[0][0]
            return f"{triple[0]['id']},{triple[1]['id']},{triple[2]['id']}"

        return None

    def _generate_code(self) -> str:
        existing = {r["confirmation_code"] for r in self._data["reservations"]}
        for _ in range(100):
            code = "BV-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
            if code not in existing:
                return code
        raise RuntimeError("Could not generate a unique confirmation code")

    def _get_by_code(self, code: str) -> dict | None:
        code = code.upper().strip()
        return next((r for r in self._data["reservations"] if r["confirmation_code"] == code), None)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def check_availability(self, date_str: str, time_str: str, party_size: int) -> dict:
        # Validate party size
        if party_size < MIN_PARTY:
            return {"available": False, "reason": f"Party size must be at least {MIN_PARTY}."}
        if party_size > MAX_PARTY:
            return {
                "available": False,
                "reason": f"Party of {party_size} exceeds our maximum of {MAX_PARTY}. Please call the restaurant for large group bookings.",
            }

        # Validate date
        try:
            req_date = date.fromisoformat(date_str)
        except ValueError:
            return {"available": False, "reason": f"Invalid date format: {date_str}. Use YYYY-MM-DD."}

        today = date.today()
        if req_date < today:
            return {"available": False, "reason": "Reservations cannot be made for dates in the past."}
        if req_date > today + timedelta(days=MAX_ADVANCE_DAYS):
            return {"available": False, "reason": f"Reservations can only be made up to {MAX_ADVANCE_DAYS} days in advance."}

        # Validate day of week (0=Mon closed)
        if req_date.weekday() == 0:
            return {"available": False, "reason": "We are closed on Mondays."}

        # Validate time
        if time_str not in VALID_TIMES:
            return {
                "available": False,
                "reason": f"'{time_str}' is not a valid reservation time. Available times are {', '.join(VALID_TIMES)}.",
            }

        # Check if past time today
        if req_date == today:
            now_time = datetime.now().strftime("%H:%M")
            if time_str <= now_time:
                return {"available": False, "reason": "That time has already passed today."}

        # Check primary slot
        table_id = self._find_table(party_size, date_str, time_str)
        if table_id:
            return {"available": True, "date": date_str, "time": time_str, "party_size": party_size}

        # Suggest alternatives ±90 minutes
        alternatives = self._find_alternatives(date_str, time_str, party_size)
        return {
            "available": False,
            "reason": f"No tables available for a party of {party_size} at {time_str} on {date_str}.",
            "alternatives": alternatives,
        }

    def _find_alternatives(self, date_str: str, time_str: str, party_size: int) -> list[dict]:
        try:
            base = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return []

        alternatives = []
        for delta_minutes in [-90, -60, -30, 30, 60, 90]:
            candidate_dt = base + timedelta(minutes=delta_minutes)
            cand_time = candidate_dt.strftime("%H:%M")
            cand_date = candidate_dt.strftime("%Y-%m-%d")
            if cand_time not in VALID_TIMES or cand_date != date_str:
                continue
            if self._find_table(party_size, cand_date, cand_time):
                alternatives.append({"date": cand_date, "time": cand_time})
            if len(alternatives) >= 2:
                break
        return alternatives

    def create_reservation(
        self,
        date_str: str,
        time_str: str,
        party_size: int,
        guest_name: str,
        phone: str,
        notes: str = "",
    ) -> dict:
        # Re-validate availability
        avail = self.check_availability(date_str, time_str, party_size)
        if not avail["available"]:
            return {"success": False, "reason": avail.get("reason", "Slot not available.")}

        # BR-8: one reservation per guest per date
        guest_lower = guest_name.strip().lower()
        for r in self._active_reservations():
            if r["date"] == date_str and r["guest_name"].lower() == guest_lower:
                return {
                    "success": False,
                    "reason": f"{guest_name} already has a reservation on {date_str} (code {r['confirmation_code']}).",
                }

        # Assign table
        table_id = self._find_table(party_size, date_str, time_str)
        code = self._generate_code()
        reservation = {
            "confirmation_code": code,
            "date": date_str,
            "time": time_str,
            "party_size": party_size,
            "table_id": table_id,
            "guest_name": guest_name.strip(),
            "phone": phone.strip(),
            "notes": notes.strip(),
            "status": "confirmed",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._data["reservations"].append(reservation)
        self._save()
        logger.info("Created reservation %s for %s on %s at %s", code, guest_name, date_str, time_str)
        return {"success": True, "reservation": reservation}

    def get_reservation(self, confirmation_code: str) -> dict:
        r = self._get_by_code(confirmation_code)
        if not r:
            return {"found": False, "reason": f"No reservation found for code {confirmation_code}."}
        return {"found": True, "reservation": r}

    def modify_reservation(self, confirmation_code: str, changes: dict) -> dict:
        r = self._get_by_code(confirmation_code)
        if not r:
            return {"success": False, "reason": f"No reservation found for code {confirmation_code}."}
        if r["status"] == "cancelled":
            return {"success": False, "reason": "This reservation has already been cancelled."}

        new_date = changes.get("date", r["date"])
        new_time = changes.get("time", r["time"])
        new_party = int(changes.get("party_size", r["party_size"]))
        new_notes = changes.get("notes", r["notes"])
        new_phone = changes.get("phone", r["phone"])

        slot_changed = (new_date != r["date"]) or (new_time != r["time"]) or (new_party != r["party_size"])

        if slot_changed:
            # Temporarily release the old table to avoid blocking ourselves
            old_table = r["table_id"]
            r["table_id"] = "__MODIFYING__"
            avail = self.check_availability(new_date, new_time, new_party)
            if not avail["available"]:
                r["table_id"] = old_table  # restore
                return {
                    "success": False,
                    "reason": avail.get("reason", "New slot not available."),
                    "alternatives": avail.get("alternatives", []),
                }
            new_table = self._find_table(new_party, new_date, new_time)
            r["table_id"] = new_table
            r["date"] = new_date
            r["time"] = new_time
            r["party_size"] = new_party

        r["notes"] = new_notes
        r["phone"] = new_phone
        self._save()
        logger.info("Modified reservation %s", confirmation_code)
        return {"success": True, "reservation": r}

    def cancel_reservation(self, confirmation_code: str) -> dict:
        r = self._get_by_code(confirmation_code)
        if not r:
            return {"success": False, "reason": f"No reservation found for code {confirmation_code}."}
        if r["status"] == "cancelled":
            return {"success": False, "reason": "This reservation is already cancelled."}
        r["status"] = "cancelled"
        self._save()
        logger.info("Cancelled reservation %s", confirmation_code)
        return {"success": True, "confirmation_code": confirmation_code}


# ---------------------------------------------------------------------------
# Claude tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "check_availability",
        "description": (
            "Check whether a specific date/time slot can seat a party of the given size. "
            "Returns availability and, if unavailable, up to 2 alternative times within ±90 minutes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Reservation date in YYYY-MM-DD format."},
                "time": {"type": "string", "description": "Reservation time in HH:MM 24-hour format (e.g. 19:00)."},
                "party_size": {"type": "integer", "description": "Number of guests."},
            },
            "required": ["date", "time", "party_size"],
        },
    },
    {
        "name": "create_reservation",
        "description": "Create and persist a reservation after availability has been confirmed. Returns the reservation with its confirmation code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Reservation date in YYYY-MM-DD format."},
                "time": {"type": "string", "description": "Reservation time in HH:MM 24-hour format."},
                "party_size": {"type": "integer", "description": "Number of guests."},
                "guest_name": {"type": "string", "description": "Full name of the guest."},
                "phone": {"type": "string", "description": "Guest contact phone number."},
                "notes": {"type": "string", "description": "Optional special requests or notes."},
            },
            "required": ["date", "time", "party_size", "guest_name", "phone"],
        },
    },
    {
        "name": "get_reservation",
        "description": "Look up an existing reservation by its confirmation code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmation_code": {"type": "string", "description": "The reservation confirmation code (e.g. BV-DEMO)."},
            },
            "required": ["confirmation_code"],
        },
    },
    {
        "name": "modify_reservation",
        "description": "Change one or more fields of an existing reservation. Re-validates availability if date, time, or party size changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmation_code": {"type": "string", "description": "The reservation confirmation code."},
                "changes": {
                    "type": "object",
                    "description": "Fields to update. Any subset of: date (YYYY-MM-DD), time (HH:MM), party_size (int), notes (str), phone (str).",
                    "properties": {
                        "date": {"type": "string"},
                        "time": {"type": "string"},
                        "party_size": {"type": "integer"},
                        "notes": {"type": "string"},
                        "phone": {"type": "string"},
                    },
                },
            },
            "required": ["confirmation_code", "changes"],
        },
    },
    {
        "name": "cancel_reservation",
        "description": "Mark a reservation as cancelled and release its table slot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmation_code": {"type": "string", "description": "The reservation confirmation code."},
            },
            "required": ["confirmation_code"],
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def build_system_prompt() -> str:
    today = date.today().strftime("%A, %B %d, %Y")
    return f"""You are the reservation assistant for Bella Vista, a cozy Italian restaurant. Today is {today}.

## Your role
Help guests book, modify, and cancel reservations through friendly, natural conversation.

## What you CAN do
- Check availability for a date, time, and party size
- Create new reservations and provide confirmation codes
- Modify existing reservations (date, time, party size, contact, notes)
- Cancel existing reservations
- Note special requests (dietary needs, occasions, seating preferences)

## What you CANNOT do
- Answer questions about menu items, prices, or specific dishes — politely say "I'm not able to help with that, but you're welcome to call us or check our website."
- Take food orders
- Make guarantees about specific seating (window, corner, etc.) — say "I'll note that preference, though I can't guarantee a specific seat."
- Handle payment or deposits
- Discuss topics unrelated to reservations — redirect politely and suggest calling the restaurant.

## Restaurant details
- **Hours:** Tuesday–Sunday, 5:00 PM–10:00 PM. **Closed Mondays.**
- **Last seating:** 9:30 PM
- **Reservations:** 30-minute increments (5:00, 5:30, 6:00, … 9:30 PM)
- **Party sizes:** 1–8 guests. For 9 or more, guests must call the restaurant.
- **Advance booking:** Up to 60 days ahead

## Conversation style
- Be warm, concise, and professional — like a helpful maître d'.
- Collect information naturally — don't ask for everything at once.
- Always check availability before confirming a booking.
- When a slot is unavailable, offer the alternatives the tool provides.
- Use hedging language for special requests: "I'll note that…" never "you'll get…"
- When showing a confirmation code, format it clearly so the guest notices it.
- Before cancelling, always confirm with the guest what reservation you're about to cancel.

## Important
- Always use the tools — never invent availability or confirmation codes.
- If the guest provides a date in natural language ("next Friday", "tomorrow"), resolve it to an exact date before calling tools.
- If something goes wrong with a tool, apologize and suggest the guest call the restaurant."""


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Bella Vista Reservation Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

store = ReservationStore(DATA_FILE)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# In-memory session store: session_id → list of messages
sessions: dict[str, list[dict]] = {}


# ------------------------------------------------------------------
# Tool dispatch
# ------------------------------------------------------------------

def dispatch_tool(tool_name: str, tool_input: dict) -> str:
    logger.info("TOOL CALL → %s(%s)", tool_name, json.dumps(tool_input))
    if tool_name == "check_availability":
        result = store.check_availability(tool_input["date"], tool_input["time"], tool_input["party_size"])
    elif tool_name == "create_reservation":
        result = store.create_reservation(
            tool_input["date"],
            tool_input["time"],
            tool_input["party_size"],
            tool_input["guest_name"],
            tool_input["phone"],
            tool_input.get("notes", ""),
        )
    elif tool_name == "get_reservation":
        result = store.get_reservation(tool_input["confirmation_code"])
    elif tool_name == "modify_reservation":
        result = store.modify_reservation(tool_input["confirmation_code"], tool_input.get("changes", {}))
    elif tool_name == "cancel_reservation":
        result = store.cancel_reservation(tool_input["confirmation_code"])
    else:
        result = {"error": f"Unknown tool: {tool_name}"}

    logger.info("TOOL RESULT ← %s", json.dumps(result))
    return json.dumps(result)


# ------------------------------------------------------------------
# Claude agentic loop
# ------------------------------------------------------------------

def run_agent(messages: list[dict]) -> str:
    """Run the Claude tool-use loop and return the final text reply."""
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=build_system_prompt(),
            tools=TOOLS,
            messages=messages,
        )

        # Append assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            # Execute all tool calls and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_str = dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            messages.append({"role": "user", "content": tool_results})
            # Continue the loop — Claude will produce the next response

        else:
            # stop_reason == "end_turn" — extract text
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "I'm sorry, I didn't understand that. Could you try again?"


# ------------------------------------------------------------------
# Request/response models
# ------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@app.get("/session")
def new_session():
    session_id = str(uuid.uuid4())
    sessions[session_id] = []
    logger.info("New session: %s", session_id)
    return {"session_id": session_id}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if req.session_id not in sessions:
        sessions[req.session_id] = []

    messages = sessions[req.session_id]
    messages.append({"role": "user", "content": req.message})

    try:
        reply = run_agent(messages)
    except anthropic.APIError as e:
        logger.error("Anthropic API error: %s", e)
        raise HTTPException(status_code=502, detail="LLM service error — please try again.")
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error.")

    return ChatResponse(reply=reply, session_id=req.session_id)


# ------------------------------------------------------------------
# Static files — serve frontend
# ------------------------------------------------------------------

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    if not ANTHROPIC_API_KEY:
        print("WARNING: ANTHROPIC_API_KEY is not set. Set it in a .env file or environment variable.")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
