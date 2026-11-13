from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workshop.sqlite"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS teams (
                  id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, bucket TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS records (
                  team TEXT, key TEXT, value TEXT, PRIMARY KEY(team,key));
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, team TEXT, stage TEXT, case_id TEXT,
                  fingerprint TEXT, status TEXT, value TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS requests (
                  id TEXT PRIMARY KEY, team TEXT, run_id TEXT, bucket TEXT,
                  status TEXT, reserved INTEGER, observed INTEGER, unknown INTEGER,
                  value TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS charges (
                  id TEXT PRIMARY KEY, bucket TEXT, description TEXT, micros INTEGER);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def create_team(self, team, bucket="workshop"):
        if bucket not in {"workshop", "rehearsal", "recovery"}:
            raise ValueError("Unknown budget bucket")
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM teams WHERE id=?", (team,)).fetchone():
                raise ValueError("Team already exists. Tokens are never silently rotated.")
            db.execute(
                "INSERT INTO teams VALUES(?,?,?,?)",
                (team, hashlib.sha256(token.encode()).hexdigest(), bucket, time.time()),
            )
        return token

    def authenticate(self, token):
        with self.connect() as db:
            row = db.execute(
                "SELECT id,bucket FROM teams WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        return dict(row) if row else None

    def team(self, team):
        with self.connect() as db:
            row = db.execute("SELECT id,bucket FROM teams WHERE id=?", (team,)).fetchone()
        if row is None:
            raise ValueError("Team not provisioned")
        return dict(row)

    def get(self, team, key, default=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM records WHERE team=? AND key=?", (team, key)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, team, key, value, *, once=False):
        with self.transaction() as db:
            if once:
                db.execute("INSERT INTO records VALUES(?,?,?)", (team, key, json.dumps(value)))
            else:
                db.execute(
                    "INSERT OR REPLACE INTO records VALUES(?,?,?)", (team, key, json.dumps(value))
                )

    def claim_run(self, identity):
        with self.transaction() as db:
            row = db.execute(
                "SELECT value,status FROM runs WHERE id=?", (identity["id"],)
            ).fetchone()
            if row:
                return False, json.loads(row[0])
            value = {**identity, "status": "running", "events": [], "outcome": None}
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity["id"],
                    identity["team"],
                    identity["stage"],
                    identity["case_id"],
                    identity["fingerprint"],
                    "running",
                    json.dumps(value),
                    time.time(),
                ),
            )
            return True, value

    def save_run(self, value):
        with self.transaction() as db:
            db.execute(
                "UPDATE runs SET status=?,value=? WHERE id=?",
                (value["status"], json.dumps(value), value["id"]),
            )

    def runs(self, team=None, stage=None):
        query, params = "SELECT value FROM runs WHERE 1=1", []
        for field, v in (("team", team), ("stage", stage)):
            if v is not None:
                query += f" AND {field}=?"
                params.append(v)
        query += " ORDER BY created,id"
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(query, params)]

    def recover_interrupted(self):
        """Call only at single-worker server startup, never while another worker is live."""
        with self.transaction() as db:
            for row in db.execute("SELECT id,value FROM runs WHERE status='running'").fetchall():
                value = json.loads(row["value"])
                value.update(
                    status="interrupted",
                    ended=time.time(),
                    error="Service stopped before completion. Preserved; not rerun.",
                )
                db.execute(
                    "UPDATE runs SET status='interrupted',value=? WHERE id=?",
                    (json.dumps(value), row["id"]),
                )
            db.execute(
                "UPDATE requests SET status='bounded_unknown',unknown=reserved "
                "WHERE status='pending'"
            )
