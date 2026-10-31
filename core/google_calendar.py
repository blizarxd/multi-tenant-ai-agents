"""
Integración con Google Calendar vía gws CLI (ya autenticado).

No necesita setup adicional — usa las credenciales de gws que ya están
configuradas en ~/.config/gws/

Cuenta activa: your-account@example.com
Calendar ID por defecto: "primary" (= la cuenta anterior)

Uso desde el código:
    from core.google_calendar import create_appointment, list_appointments
"""

import re
import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Busca gws en PATH; en Windows puede estar en npm como gws.cmd
_GWS = shutil.which("gws") or shutil.which("gws.cmd") or "gws"


def _run_gws(*args) -> dict:
    """Ejecuta un comando gws y retorna el JSON parseado."""
    cmd = [_GWS] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", shell=False)
    if result.returncode != 0:
        raise RuntimeError(f"gws error: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _parse_date(date_str: str) -> datetime | None:
    """
    Parsea fechas en los formatos que genera la IA:
      - "martes 13/05 9:00 AM"
      - "martes 13/05/2026 10:30 AM"
      - "16 de mayo a las 3:00 PM"
      - "2026-05-13 09:00"
    """
    now = datetime.now()
    s   = date_str.strip()

    # ISO: 2026-05-13 09:00
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if m:
        return datetime(*map(int, m.groups()))

    # dd/mm/yyyy? HH:MM AM/PM
    m = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\D+(\d{1,2}):(\d{2})\s*(AM|PM)?", s, re.I)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year_explicit = bool(m.group(3))
        year  = int(m.group(3)) if year_explicit else now.year
        hour, minute = int(m.group(4)), int(m.group(5))
        ampm  = (m.group(6) or "").upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        try:
            dt = datetime(year, month, day, hour, minute)
            # Si no dieron año y la fecha quedó en el pasado (ej: estamos en
            # diciembre y agendan "13/05"), avanza al próximo año.
            if not year_explicit and dt < now - timedelta(days=1):
                dt = dt.replace(year=year + 1)
            return dt
        except ValueError:
            pass

    # "16 de mayo a las 3:00 PM"
    meses = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
             "julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12}
    m = re.search(r"(\d{1,2})\s+de\s+(\w+).*?(\d{1,2}):(\d{2})\s*(AM|PM)?", s, re.I)
    if m:
        day = int(m.group(1))
        mes = meses.get(m.group(2).lower())
        hour, minute = int(m.group(3)), int(m.group(4))
        ampm = (m.group(5) or "").upper()
        if mes:
            if ampm == "PM" and hour < 12:
                hour += 12
            if ampm == "AM" and hour == 12:
                hour = 0
            try:
                dt = datetime(now.year, mes, day, hour, minute)
                if dt < now - timedelta(days=1):
                    dt = dt.replace(year=now.year + 1)
                return dt
            except ValueError:
                pass

    logger.warning(f"No se pudo parsear la fecha: '{date_str}'")
    return None


def create_appointment(calendar_id: str, summary: str, date_str: str,
                        duration_min: int = 20, description: str = "") -> dict | None:
    """
    Crea un evento en Google Calendar usando gws CLI.
    calendar_id: "primary" o la dirección de email del calendario.
    """
    start_dt = _parse_date(date_str)
    if not start_dt:
        logger.warning(f"Fecha no parseada — evento no creado: {date_str!r}")
        return None

    end_dt = start_dt + timedelta(minutes=duration_min)

    event_body = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:00"),
            "timeZone": "America/Caracas"
        },
        "end": {
            "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:00"),
            "timeZone": "America/Caracas"
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 30},
                {"method": "email", "minutes": 60}
            ]
        }
    }

    # Separar calendarId (URL param) del cuerpo del evento
    url_params = {"calendarId": calendar_id}

    try:
        result = _run_gws(
            "calendar", "events", "insert",
            "--params", json.dumps(url_params),
            "--json",   json.dumps(event_body)
        )
        link = result.get("htmlLink", "")
        logger.info(f"Evento creado en Calendar: {summary} | {link}")
        return result
    except Exception as e:
        logger.error(f"Error creando evento en Calendar: {e}")
        return None


def list_appointments(calendar_id: str, date: datetime | None = None) -> list[dict]:
    """Lista eventos del día especificado (o hoy)."""
    if date is None:
        date = datetime.now()

    day_start = date.replace(hour=0,  minute=0,  second=0,  microsecond=0)
    day_end   = date.replace(hour=23, minute=59, second=59, microsecond=0)

    params = {
        "calendarId":   calendar_id,
        "timeMin":      day_start.strftime("%Y-%m-%dT%H:%M:00-04:00"),
        "timeMax":      day_end.strftime("%Y-%m-%dT%H:%M:00-04:00"),
        "singleEvents": True,
        "orderBy":      "startTime",
    }

    try:
        result = _run_gws("calendar", "events", "list", "--params", json.dumps(params))
        return result.get("items", [])
    except Exception as e:
        logger.error(f"Error listando eventos: {e}")
        return []


def delete_appointment(calendar_id: str, event_id: str) -> bool:
    """Elimina un evento de Google Calendar."""
    try:
        _run_gws(
            "calendar", "events", "delete",
            "--params", json.dumps({"calendarId": calendar_id, "eventId": event_id})
        )
        return True
    except Exception as e:
        logger.error(f"Error eliminando evento: {e}")
        return False
