"""
Integración con Google Sheets vía gws CLI.
Crea y mantiene un libro de hojas de vida de pacientes.

Estructura del spreadsheet:
  - Tab "Historial"      → una fila por evento (cita, foto, urgencia)
  - Tab "[Nombre]"       → hoja de vida individual de cada paciente
"""

import json
import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

_GWS = shutil.which("gws") or shutil.which("gws.cmd") or "gws"

# Colores para el encabezado de hojas (RGB en fracción 0-1)
_COLOR_AZUL   = {"red": 0.118, "green": 0.353, "blue": 0.627, "alpha": 1}
_COLOR_VERDE  = {"red": 0.204, "green": 0.659, "blue": 0.325, "alpha": 1}
_COLOR_BLANCO = {"red": 1.0,   "green": 1.0,   "blue": 1.0,   "alpha": 1}

_CACHE_DIR = "data"


def _run(cmd: list) -> dict:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", shell=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    text = result.stdout.strip()
    return json.loads(text) if text else {}


def _gws(*args) -> dict:
    return _run([_GWS, "sheets"] + list(args))


# ── Caché de spreadsheet ID ───────────────────────────────────────
def _cache_path(tenant_id: str) -> str:
    return os.path.join(_CACHE_DIR, f"{tenant_id}_sheets.json")


def _load_cache(tenant_id: str) -> dict:
    p = _cache_path(tenant_id)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def _save_cache(tenant_id: str, data: dict):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_cache_path(tenant_id), "w") as f:
        json.dump(data, f, indent=2)


# ── Crear / obtener spreadsheet ───────────────────────────────────
def get_or_create_spreadsheet(tenant_id: str, title: str) -> str:
    """Retorna el ID del spreadsheet (lo crea si no existe)."""
    cache = _load_cache(tenant_id)
    if "spreadsheet_id" in cache:
        return cache["spreadsheet_id"]

    # Buscar spreadsheet existente con ese título
    try:
        from core.google_drive import _run_gws as drive_run
        params = {"q": f"name='{title}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                  "fields": "files(id,name)"}
        result = drive_run("drive", "files", "list", "--params", json.dumps(params))
        files = result.get("files", [])
        if files:
            sid = files[0]["id"]
            _save_cache(tenant_id, {"spreadsheet_id": sid, "title": title})
            logger.info(f"Spreadsheet existente encontrado: {title} ({sid})")
            return sid
    except Exception as e:
        logger.warning(f"No se pudo buscar spreadsheet en Drive: {e}")

    # Crear nuevo
    body = {
        "properties": {"title": title, "locale": "es"},
        "sheets": [
            {"properties": {"title": "Historial", "index": 0}},
            {"properties": {"title": "Pacientes",  "index": 1}},
        ]
    }
    result = _gws("spreadsheets", "create", "--json", json.dumps(body))
    sid = result["spreadsheetId"]
    _save_cache(tenant_id, {"spreadsheet_id": sid, "title": title})
    logger.info(f"Spreadsheet creado: {title} ({sid})")

    # Cabecera del Historial
    _write_header(sid, "Historial", [
        "Fecha", "Nombre", "Teléfono", "Telegram_ID",
        "Tipo_Evento", "Motivo / Síntoma", "Triage",
        "Servicio", "Precio_USD", "Notas",
        "Foto_Zona", "Foto_Urgencia_Visual", "Foto_Descripcion"
    ], color=_COLOR_AZUL)

    # Cabecera de Pacientes
    _write_header(sid, "Pacientes", [
        "Nombre", "Teléfono", "Telegram_ID",
        "Primera Visita", "Última Visita",
        "Total Eventos", "Última Cita", "Último Motivo"
    ], color=_COLOR_VERDE)

    return sid


# ── Escribir cabecera de una hoja ─────────────────────────────────
def _write_header(sid: str, sheet_name: str, headers: list, color: dict = None):
    params = {
        "spreadsheetId": sid,
        "range":         f"'{sheet_name}'!A1",
        "valueInputOption": "USER_ENTERED"
    }
    body = {"values": [headers]}
    _gws("spreadsheets", "values", "update",
         "--params", json.dumps(params),
         "--json",   json.dumps(body))

    if color:
        _format_header_row(sid, sheet_name, color, len(headers))


def _format_header_row(sid: str, sheet_name: str, bg_color: dict, num_cols: int):
    """Pone fondo de color + texto blanco en la primera fila."""
    # Necesitamos el sheetId numérico
    try:
        info = _gws("spreadsheets", "get", "--params", json.dumps({"spreadsheetId": sid}))
        sheet_id = None
        for s in info.get("sheets", []):
            if s["properties"]["title"] == sheet_name:
                sheet_id = s["properties"]["sheetId"]
                break
        if sheet_id is None:
            return
    except Exception:
        return

    request = {
        "repeatCell": {
            "range": {
                "sheetId":       sheet_id,
                "startRowIndex": 0,
                "endRowIndex":   1,
                "startColumnIndex": 0,
                "endColumnIndex": num_cols
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": bg_color,
                    "textFormat": {"bold": True, "foregroundColor": _COLOR_BLANCO},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }
    }
    _gws("spreadsheets", "batchUpdate",
         "--params", json.dumps({"spreadsheetId": sid}),
         "--json",   json.dumps({"requests": [request]}))


# ── Asegurar que existe una pestaña ──────────────────────────────
def _ensure_tab(sid: str, tab_name: str) -> int:
    """Crea la pestaña si no existe. Retorna su sheetId numérico."""
    try:
        info = _gws("spreadsheets", "get", "--params", json.dumps({"spreadsheetId": sid}))
        for s in info.get("sheets", []):
            if s["properties"]["title"] == tab_name:
                return s["properties"]["sheetId"]
        # Crear pestaña
        result = _gws("spreadsheets", "batchUpdate",
                      "--params", json.dumps({"spreadsheetId": sid}),
                      "--json", json.dumps({"requests": [
                          {"addSheet": {"properties": {"title": tab_name}}}
                      ]}))
        new_sheet = result.get("replies", [{}])[0].get("addSheet", {})
        return new_sheet.get("properties", {}).get("sheetId", 0)
    except Exception as e:
        logger.warning(f"No se pudo asegurar pestaña '{tab_name}': {e}")
        return 0


# ── Append de una fila ────────────────────────────────────────────
def _append_row(sid: str, sheet_name: str, row: list):
    params = {
        "spreadsheetId": sid,
        "range":         f"'{sheet_name}'!A1",
        "valueInputOption": "USER_ENTERED",
        "insertDataOption": "INSERT_ROWS"
    }
    body = {"values": [row]}
    _gws("spreadsheets", "values", "append",
         "--params", json.dumps(params),
         "--json",   json.dumps(body))


# ── API pública ───────────────────────────────────────────────────
def append_cita(sid: str, telegram_id: str, payload: dict):
    """Agrega una fila de cita al historial."""
    row = [
        payload.get("fecha", ""),
        payload.get("paciente", ""),
        payload.get("telefono", ""),
        str(telegram_id),
        "CITA",
        payload.get("motivo", ""),
        payload.get("triage", ""),
        payload.get("servicio", ""),
        payload.get("precio_usd", ""),
        payload.get("notas", ""),
        "", "", ""  # foto vacío
    ]
    _append_row(sid, "Historial", row)
    logger.info(f"Cita sincronizada con Sheets: {payload.get('paciente')}")


def append_foto(sid: str, telegram_id: str, payload: dict):
    """Agrega la info de foto médica al historial."""
    from datetime import datetime
    row = [
        datetime.now().strftime("%d/%m/%Y %H:%M"),
        "", "",  # nombre/telefono se llenan con la cita
        str(telegram_id),
        "FOTO_PREVIA",
        "",  # motivo
        "", "", "", "",  # triage, servicio, precio, notas
        payload.get("zona_afectada", ""),
        payload.get("nivel_urgencia_visual", ""),
        payload.get("descripcion_visual", ""),
    ]
    _append_row(sid, "Historial", row)


def sync_patient_tab(sid: str, patient_name: str, telegram_id: str,
                     telefono: str, events: list):
    """
    Crea o actualiza la hoja de vida individual del paciente.
    events = lista de dicts con event_type + payload + timestamp
    """
    tab = patient_name[:99] if patient_name else f"ID_{telegram_id}"
    _ensure_tab(sid, tab)

    from datetime import datetime
    first_seen = ""
    last_seen  = ""
    rows = []
    for ev in reversed(events):  # cronológico
        p    = ev["payload"]
        ts   = ev.get("timestamp", "")[:16].replace("T", " ")
        tipo = ev.get("event_type", "")

        if not first_seen:
            first_seen = ts
        last_seen = ts

        if tipo == "cita":
            rows.append([
                ts,
                "CITA",
                p.get("motivo", ""),
                p.get("triage", ""),
                p.get("servicio", ""),
                f"${p.get('precio_usd', '')}",
                p.get("fecha", ""),
                p.get("notas", ""),
                "", "", ""
            ])
        elif tipo == "foto_medica":
            rows.append([
                ts,
                "FOTO PREVIA",
                "", "", "", "", "",
                "",
                p.get("zona_afectada", ""),
                p.get("nivel_urgencia_visual", ""),
                p.get("descripcion_visual", ""),
            ])
        elif tipo == "urgencia":
            rows.append([
                ts,
                "URGENCIA",
                p.get("sintoma", ""),
                "URGENTE",
                "", "", "", "", "", "", ""
            ])
        elif tipo == "cita_cancelada":
            rows.append([
                ts,
                "CANCELADA",
                p.get("motivo", ""),
                "", "", "", "", "", "", "", ""
            ])

    # Escribir hoja de vida completa (sobreescribir)
    header_patient = [
        ["HOJA DE VIDA — PACIENTE"],
        ["Nombre:",    patient_name or "—"],
        ["Teléfono:",  telefono or "—"],
        ["Telegram:",  str(telegram_id)],
        ["Primera visita:", first_seen],
        ["Última visita:",  last_seen],
        [],
        ["Fecha/Hora", "Tipo", "Motivo / Síntoma", "Triage",
         "Servicio", "Precio", "Fecha Cita", "Notas",
         "Foto Zona", "Foto Urgencia", "Foto Descripción"],
    ]
    all_rows = header_patient + rows

    params = {
        "spreadsheetId": sid,
        "range": f"'{tab}'!A1",
        "valueInputOption": "USER_ENTERED"
    }
    body = {"values": all_rows}
    try:
        _gws("spreadsheets", "values", "update",
             "--params", json.dumps(params),
             "--json",   json.dumps(body))
        logger.info(f"Hoja de vida actualizada: {tab}")
    except Exception as e:
        logger.warning(f"No se pudo actualizar hoja de vida '{tab}': {e}")


def get_spreadsheet_url(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"
