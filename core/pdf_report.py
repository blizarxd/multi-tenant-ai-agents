"""
Generador de reportes PDF diarios.

Usa fpdf2 (pip install fpdf2).
Genera un PDF con la agenda del día, urgencias y resumen económico.
"""

import json
import os
import sqlite3
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _s(text: str) -> str:
    """Reemplaza caracteres fuera de latin-1 para que Helvetica los acepte."""
    return (str(text)
            .replace("—", "-").replace("–", "-")   # em/en dash
            .replace("‘", "'").replace("’", "'")   # comillas curvas
            .replace("“", '"').replace("”", '"')
            .replace("•", "*").replace("…", "...")
            .encode("latin-1", errors="replace").decode("latin-1"))
DATA_DIR = os.path.join(ROOT, "data")


def _load_events(tenant_id: str, event_type: str | None = None,
                 for_date: date | None = None) -> list[dict]:
    db = os.path.join(DATA_DIR, f"{tenant_id}.db")
    if not os.path.isfile(db):
        return []
    today = (for_date or date.today()).isoformat()
    conn  = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM events WHERE event_type=? AND DATE(timestamp)=? ORDER BY id",
                (event_type, today)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE DATE(timestamp)=? ORDER BY id",
                (today,)
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
    finally:
        conn.close()


def _count_clients(tenant_id: str) -> int:
    db = os.path.join(DATA_DIR, f"{tenant_id}.db")
    if not os.path.isfile(db):
        return 0
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    finally:
        conn.close()


def generate_daily_report(tenant_id: str, doctor: str, clinica: str,
                           for_date: date | None = None) -> bytes:
    """
    Genera el PDF del reporte diario y lo retorna como bytes.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        raise ImportError("Instala fpdf2: pip install fpdf2")

    report_date = for_date or date.today()
    citas   = _load_events(tenant_id, "cita",     report_date)
    urgencias = _load_events(tenant_id, "urgencia", report_date)
    cancel  = _load_events(tenant_id, "cita_cancelada", report_date)
    total_pacientes = _count_clients(tenant_id)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── Encabezado ────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_fill_color(30, 90, 160)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 12, _s(clinica), new_x="LMARGIN", new_y="NEXT", fill=True, align="C")

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 6, _s(f"{doctor}  |  Medicina General  |  Your City"),
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 6, _s(f"Reporte diario - {report_date.strftime('%d/%m/%Y')}"),
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    # ── Resumen rápido ────────────────────────────────────────────
    pdf.set_fill_color(240, 245, 255)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "RESUMEN DEL DIA", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_font("Helvetica", "", 11)

    cols = [
        ("Citas agendadas", len(citas)),
        ("Urgencias",       len(urgencias)),
        ("Canceladas",      len(cancel)),
        ("Pacientes totales (histórico)", total_pacientes),
    ]
    for label, val in cols:
        pdf.cell(110, 7, label)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, str(val), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
    pdf.ln(4)

    # ── Tabla de citas ────────────────────────────────────────────
    if citas:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_fill_color(240, 245, 255)
        pdf.cell(0, 8, "AGENDA - CITAS DEL DIA", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(200, 220, 255)

        # Cabecera de tabla
        widths = [45, 35, 30, 22, 55]
        headers = ["Paciente", "Teléfono", "Hora", "Triage", "Motivo"]
        for w, h in zip(widths, headers):
            pdf.cell(w, 7, h, border=1, fill=True, align="C")
        pdf.ln()

        pdf.set_font("Helvetica", "", 9)
        for i, ev in enumerate(citas):
            p = ev["payload"]
            triage = p.get("triage", "RUTINA")
            if triage == "URGENTE":
                pdf.set_fill_color(255, 200, 200)
            elif triage == "PRIORIDAD":
                pdf.set_fill_color(255, 240, 180)
            else:
                pdf.set_fill_color(220, 255, 220)
            fill = True

            row = [
                _s((p.get("paciente", "-") or "-")[:30]),
                _s((p.get("telefono", "-") or "-")[:18]),
                _s((p.get("fecha", "-") or "-")[:18]),
                _s(triage[:10]),
                _s((p.get("motivo", "-") or "-")[:40]),
            ]
            for w, val in zip(widths, row):
                pdf.cell(w, 6, val, border=1, fill=fill, align="C" if w < 40 else "L")
            pdf.ln()
        pdf.ln(4)

    # ── Urgencias ─────────────────────────────────────────────────
    if urgencias:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_fill_color(255, 200, 200)
        pdf.cell(0, 8, "!! URGENCIAS", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_font("Helvetica", "", 10)
        for ev in urgencias:
            p = ev["payload"]
            pdf.multi_cell(
                0, 6,
                _s(f"* {p.get('paciente','?')} | Tel: {p.get('telefono','?')} | {p.get('sintoma','')}"),
            )
        pdf.ln(4)

    # ── Canceladas ────────────────────────────────────────────────
    if cancel:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(0, 8, "CANCELACIONES", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_font("Helvetica", "", 10)
        for ev in cancel:
            p = ev["payload"]
            pdf.cell(0, 6,
                     _s(f"* {p.get('paciente','?')} - Motivo: {p.get('motivo','-')}"),
                     new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    # ── Pie de página ─────────────────────────────────────────────
    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 5,
             _s(f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} - Sistema de Agentes IA"),
             align="C")

    return bytes(pdf.output())
