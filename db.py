"""The database. Every lead, every message and every event (the audit log) is saved here.

SQLite = the whole database is one file, no server needed. For a real client this would be
their CRM (HubSpot, Zoho, Salesforce) or a PostgreSQL database; the rest of the code would not change.
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

DUBAI = timezone(timedelta(hours=4))          # Dubai is UTC+4 all year (no summer time)
TIME_COLUMNS = ("created_at", "last_inbound_at", "viewing_at", "booked_at")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client TEXT, phone TEXT, source TEXT, created_at TEXT,
    fields TEXT DEFAULT '{}', score INTEGER DEFAULT 0, tier TEXT DEFAULT 'NEW', stage TEXT DEFAULT 'New',
    agent TEXT, language TEXT DEFAULT 'en', first_reply_seconds REAL,
    last_inbound_at TEXT, followups_sent INTEGER DEFAULT 0,
    offered_slots TEXT DEFAULT '[]', viewing_at TEXT, booked_at TEXT, reminder_sent INTEGER DEFAULT 0,
    UNIQUE (client, phone)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, at TEXT, direction TEXT, text TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, at TEXT, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def to_text(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return json.dumps([to_text(v) for v in value], ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


class Database:
    def __init__(self, path="leads.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()            # many website visitors can use it at the same time
        with self.lock:
            self.conn.executescript(SCHEMA)

    def run(self, sql, args=()):
        with self.lock:
            cursor = self.conn.execute(sql, args)
            self.conn.commit()
            return cursor

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---- demo clock: lets the demo "skip ahead" in time to show follow-ups and reminders ----
    def now(self):
        found = self.rows("SELECT value FROM settings WHERE key = 'clock_offset_hours'")
        offset = float(found[0]["value"]) if found else 0.0
        return datetime.now(DUBAI).replace(microsecond=0) + timedelta(hours=offset)

    def skip_time(self, hours):
        found = self.rows("SELECT value FROM settings WHERE key = 'clock_offset_hours'")
        offset = (float(found[0]["value"]) if found else 0.0) + hours
        self.run("INSERT OR REPLACE INTO settings (key, value) VALUES ('clock_offset_hours', ?)", (str(offset),))

    # ---- leads ----
    def find_or_create_lead(self, client, phone, source, now):
        """One person = one lead. If the same phone number writes again, we continue the same lead
        instead of making a duplicate. Returns (lead_id, is_new)."""
        found = self.rows("SELECT id FROM leads WHERE client = ? AND phone = ?", (client, phone))
        if found:
            return found[0]["id"], False
        cursor = self.run("INSERT INTO leads (client, phone, source, created_at) VALUES (?, ?, ?, ?)",
                          (client, phone, source, now.isoformat()))
        return cursor.lastrowid, True

    def get_lead(self, lead_id):
        found = self.rows("SELECT * FROM leads WHERE id = ?", (lead_id,))
        return self.parse(found[0]) if found else None

    def parse(self, lead):
        lead["fields"] = json.loads(lead["fields"] or "{}")
        lead["offered_slots"] = [datetime.fromisoformat(s) for s in json.loads(lead["offered_slots"] or "[]")]
        for column in TIME_COLUMNS:
            lead[column] = datetime.fromisoformat(lead[column]) if lead[column] else None
        return lead

    def update_lead(self, lead_id, **changes):
        columns = ", ".join("%s = ?" % name for name in changes)
        self.run("UPDATE leads SET %s WHERE id = ?" % columns, [to_text(v) for v in changes.values()] + [lead_id])

    def leads(self, client):
        return [self.parse(r) for r in self.rows("SELECT * FROM leads WHERE client = ? ORDER BY id DESC", (client,))]

    def open_leads_by_agent(self, client):
        rows = self.rows("SELECT agent, COUNT(*) AS n FROM leads WHERE client = ? AND agent IS NOT NULL "
                         "AND stage NOT IN ('Opted out', 'Not a lead') GROUP BY agent", (client,))
        return {r["agent"]: r["n"] for r in rows}

    def taken_slots(self, client, agent):
        rows = self.rows("SELECT viewing_at FROM leads WHERE client = ? AND agent = ? AND viewing_at IS NOT NULL",
                         (client, agent))
        return {datetime.fromisoformat(r["viewing_at"]) for r in rows}

    # ---- messages and the audit log ----
    def add_message(self, lead_id, at, direction, text, note=""):
        self.run("INSERT INTO messages (lead_id, at, direction, text, note) VALUES (?, ?, ?, ?, ?)",
                 (lead_id, at.isoformat(), direction, text, note))

    def messages(self, lead_id):
        return self.rows("SELECT * FROM messages WHERE lead_id = ? ORDER BY at, id", (lead_id,))

    def log(self, lead_id, at, kind, detail=""):
        """The audit log: every action the system takes is written down, with the time."""
        self.run("INSERT INTO events (lead_id, at, kind, detail) VALUES (?, ?, ?, ?)",
                 (lead_id, at.isoformat(), kind, detail))

    def events(self, client, lead_id=None):
        sql = ("SELECT e.*, l.phone FROM events e JOIN leads l ON l.id = e.lead_id WHERE l.client = ?"
               + (" AND e.lead_id = ?" if lead_id else "") + " ORDER BY e.at DESC, e.id DESC")
        return self.rows(sql, (client, lead_id) if lead_id else (client,))

    def reset(self):
        for table in ("leads", "messages", "events", "settings"):
            self.run("DELETE FROM %s" % table)
