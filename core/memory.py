"""
Memoria persistente: SQLite local.
Cada negocio tiene su propia BD: data/<perfil>.db

Tablas:
  - clients         : datos del cliente (telegram_id, nombre, primera/última visita)
  - interactions    : cada mensaje (para análisis y contexto histórico)
  - events          : eventos de negocio (pedido, cita, lead calificado, etc.)
  - lead_scores     : calificación acumulada por cliente (potencial / bajo)
"""

import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager


class Memory:
    def __init__(self, profile_name: str, base_dir: str = "data"):
        os.makedirs(base_dir, exist_ok=True)
        self.db_path = os.path.join(base_dir, f"{profile_name}.db")
        self._init_schema()

    @contextmanager
    def _conn(self):
        # timeout=10: espera si otra escritura tiene el lock en vez de fallar al instante.
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL permite lecturas concurrentes mientras se escribe; busy_timeout evita
        # los "database is locked" cuando el hilo de recordatorios, el sync de Sheets
        # y el handler de mensajes tocan la misma BD a la vez.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                telegram_id   TEXT PRIMARY KEY,
                nombre        TEXT,
                telefono      TEXT,
                first_seen    TEXT,
                last_seen     TEXT,
                total_eventos INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS interactions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   TEXT,
                role          TEXT,
                content       TEXT,
                timestamp     TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   TEXT,
                event_type    TEXT,
                payload       TEXT,
                timestamp     TEXT
            );

            CREATE TABLE IF NOT EXISTS lead_scores (
                telegram_id   TEXT PRIMARY KEY,
                score         INTEGER,
                clasificacion TEXT,
                motivo        TEXT,
                updated_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS scheduled_reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                remind_at   TEXT,
                message     TEXT,
                sent        INTEGER DEFAULT 0,
                created_at  TEXT
            );
            """)

    # ── Clientes ──────────────────────────────────────────────────
    def upsert_client(self, telegram_id, nombre=None, telefono=None):
        now = datetime.now().isoformat()
        with self._conn() as c:
            row = c.execute(
                "SELECT total_eventos FROM clients WHERE telegram_id = ?",
                (str(telegram_id),)
            ).fetchone()
            if row:
                c.execute(
                    "UPDATE clients SET nombre = COALESCE(?, nombre), telefono = COALESCE(?, telefono), last_seen = ? WHERE telegram_id = ?",
                    (nombre, telefono, now, str(telegram_id))
                )
            else:
                c.execute(
                    "INSERT INTO clients (telegram_id, nombre, telefono, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                    (str(telegram_id), nombre, telefono, now, now)
                )

    def get_client(self, telegram_id) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM clients WHERE telegram_id = ?",
                (str(telegram_id),)
            ).fetchone()
            return dict(row) if row else None

    # ── Interacciones (historial conversacional) ──────────────────
    def log_interaction(self, telegram_id, role, content):
        with self._conn() as c:
            c.execute(
                "INSERT INTO interactions (telegram_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (str(telegram_id), role, content, datetime.now().isoformat())
            )

    def recent_interactions(self, telegram_id, limit=20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content, timestamp FROM interactions WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
                (str(telegram_id), limit)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # ── Eventos de negocio ────────────────────────────────────────
    def save_event(self, telegram_id, event_type, payload: dict):
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (telegram_id, event_type, payload, timestamp) VALUES (?, ?, ?, ?)",
                (str(telegram_id), event_type, json.dumps(payload, ensure_ascii=False), datetime.now().isoformat())
            )
            c.execute(
                "UPDATE clients SET total_eventos = total_eventos + 1 WHERE telegram_id = ?",
                (str(telegram_id),)
            )

    def events_today(self, event_type: str | None = None) -> list[dict]:
        today = datetime.now().date().isoformat()
        with self._conn() as c:
            if event_type:
                rows = c.execute(
                    "SELECT * FROM events WHERE event_type = ? AND DATE(timestamp) = ? ORDER BY id DESC",
                    (event_type, today)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM events WHERE DATE(timestamp) = ? ORDER BY id DESC",
                    (today,)
                ).fetchall()
            return [dict(r) for r in rows]

    # ── Lead scoring ──────────────────────────────────────────────
    def set_lead_score(self, telegram_id, score: int, clasificacion: str, motivo: str = ""):
        with self._conn() as c:
            c.execute("""
                INSERT INTO lead_scores (telegram_id, score, clasificacion, motivo, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    score = excluded.score,
                    clasificacion = excluded.clasificacion,
                    motivo = excluded.motivo,
                    updated_at = excluded.updated_at
            """, (str(telegram_id), score, clasificacion, motivo, datetime.now().isoformat()))

    def get_lead_score(self, telegram_id) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM lead_scores WHERE telegram_id = ?",
                (str(telegram_id),)
            ).fetchone()
            return dict(row) if row else None

    # ── Recordatorios programados ─────────────────────────────────
    def add_reminder(self, telegram_id, remind_at: "datetime", message: str):
        with self._conn() as c:
            c.execute(
                "INSERT INTO scheduled_reminders (telegram_id, remind_at, message, created_at) VALUES (?, ?, ?, ?)",
                (str(telegram_id), remind_at.isoformat(), message, datetime.now().isoformat())
            )

    def get_due_reminders(self) -> list[dict]:
        now = datetime.now().isoformat()
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM scheduled_reminders WHERE sent = 0 AND remind_at <= ? ORDER BY remind_at",
                (now,)
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_sent(self, reminder_id: int):
        with self._conn() as c:
            c.execute("UPDATE scheduled_reminders SET sent = 1 WHERE id = ?", (reminder_id,))

    # ── Historial médico ──────────────────────────────────────────
    def get_medical_history(self, telegram_id) -> list[dict]:
        """Todas las citas/urgencias del paciente, más recientes primero."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_type, payload, timestamp FROM events "
                "WHERE telegram_id = ? AND event_type IN ('cita','urgencia','cita_cancelada') "
                "ORDER BY id DESC LIMIT 15",
                (str(telegram_id),)
            ).fetchall()
        result = []
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except Exception:
                payload = {}
            result.append({"event_type": r["event_type"], "payload": payload, "timestamp": r["timestamp"]})
        return result

    def get_all_patients(self) -> list[dict]:
        """Todos los clientes con sus eventos para el Excel."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM clients ORDER BY last_seen DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_clients(self) -> list[dict]:
        """Alias genérico de get_all_patients — funciona para cualquier tipo de negocio."""
        return self.get_all_patients()

    def get_all_events_since(self, since_date: str) -> list[dict]:
        """
        Todos los eventos de todos los clientes desde 'since_date' (formato 'YYYY-MM-DD').
        Retorna lista de dicts con event_type, payload (dict), created_at.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE timestamp >= ? ORDER BY id ASC",
                (since_date,)
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                d["payload"] = {}
            d["created_at"] = d.get("timestamp", "")
            result.append(d)
        return result

    def get_events_for_patient(self, telegram_id, event_type: str | None = None) -> list[dict]:
        with self._conn() as c:
            if event_type:
                rows = c.execute(
                    "SELECT * FROM events WHERE telegram_id = ? AND event_type = ? ORDER BY id DESC",
                    (str(telegram_id), event_type)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM events WHERE telegram_id = ? ORDER BY id DESC",
                    (str(telegram_id),)
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    d["payload"] = {}
                result.append(d)
            return result

    def leads_by_classification(self, clasificacion: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("""
                SELECT ls.*, c.nombre, c.telefono
                FROM lead_scores ls
                LEFT JOIN clients c ON c.telegram_id = ls.telegram_id
                WHERE ls.clasificacion = ?
                ORDER BY ls.score DESC, ls.updated_at DESC
            """, (clasificacion,)).fetchall()
            return [dict(r) for r in rows]
