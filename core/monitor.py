"""
Monitor de agentes — heartbeats, pagos y alertas de errores.
Usado por el bot admin para ver el estado de todos los bots activos.
"""

import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ROOT     = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(ROOT, "data")
ADMIN_DIR = os.path.join(ROOT, "admin")


# ── Heartbeats ──────────────────────────────────────────────────────

def write_heartbeat(tenant_id: str, display_name: str, msg_count: int = 0):
    """Cada bot escribe su heartbeat cada vez que procesa un mensaje."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, f"heartbeat_{tenant_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "tenant_id":    tenant_id,
                "display_name": display_name,
                "last_seen":    datetime.now().isoformat(),
                "msg_count":    msg_count,
            }, f, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"heartbeat write error: {e}")


def read_all_heartbeats(max_age_minutes: int = 8) -> list[dict]:
    """
    Lee todos los heartbeats y marca cada bot como alive/offline.
    Un bot se considera offline si no escribió en los últimos max_age_minutes.
    """
    result = []
    if not os.path.exists(DATA_DIR):
        return result
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.startswith("heartbeat_") or not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(DATA_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            last = datetime.fromisoformat(data.get("last_seen", "2000-01-01"))
            age  = (datetime.now() - last).total_seconds() / 60
            data["age_min"] = round(age, 1)
            data["alive"]   = age <= max_age_minutes
            result.append(data)
        except Exception:
            pass
    return result


# ── Pagos ───────────────────────────────────────────────────────────

def read_payments() -> list[dict]:
    """Lee admin/clients_payments.json"""
    path = os.path.join(ADMIN_DIR, "clients_payments.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("clients", [])
    except Exception:
        return []


def get_due_payments(days_ahead: int = 5) -> list[dict]:
    """
    Retorna clientes con pago vencido o próximo a vencer en N días.
    Cada dict tiene 'days_left' y 'status' añadidos.
    """
    today  = datetime.now().date()
    result = []
    for c in read_payments():
        due_str = c.get("next_payment_date", "")
        if not due_str:
            continue
        try:
            due      = datetime.strptime(due_str, "%Y-%m-%d").date()
            days     = (due - today).days
            if days <= days_ahead:
                c = dict(c)
                c["days_left"] = days
                c["status"]    = "🔴 VENCIDO" if days < 0 else ("🟠 HOY" if days == 0 else f"🟡 en {days} días")
                result.append(c)
        except Exception:
            pass
    return sorted(result, key=lambda x: x.get("days_left", 99))


def get_all_clients_summary() -> list[dict]:
    """Resumen de todos los clientes con estado de pago."""
    today  = datetime.now().date()
    result = []
    for c in read_payments():
        due_str = c.get("next_payment_date", "")
        days = None
        if due_str:
            try:
                due  = datetime.strptime(due_str, "%Y-%m-%d").date()
                days = (due - today).days
            except Exception:
                pass
        c = dict(c)
        c["days_to_payment"] = days
        c["payment_status"]  = (
            "VENCIDO" if days is not None and days < 0 else
            "HOY"     if days == 0 else
            "OK"      if days is not None and days > 5 else
            "PROXIMO" if days is not None else "SIN FECHA"
        )
        result.append(c)
    return result


# ── Logs ────────────────────────────────────────────────────────────

def read_recent_errors(n: int = 25) -> list[str]:
    """Últimas líneas de ERROR/WARNING del log activo."""
    for log_name in ["bot_multi.log", "bot.log"]:
        path = os.path.join(ROOT, log_name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                errors = [
                    l.strip() for l in lines
                    if " - ERROR - " in l or " - WARNING - " in l
                ]
                return errors[-n:] if errors else ["Sin errores recientes en el log."]
            except Exception as e:
                return [f"Error leyendo log: {e}"]
    return ["No se encontró archivo de log."]


def read_recent_log(n: int = 30) -> list[str]:
    """Últimas N líneas del log (cualquier nivel)."""
    for log_name in ["bot_multi.log", "bot.log"]:
        path = os.path.join(ROOT, log_name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                return [l.strip() for l in lines[-n:]] if lines else ["Log vacío."]
            except Exception as e:
                return [f"Error leyendo log: {e}"]
    return ["No se encontró archivo de log."]


# ── Resumen global ───────────────────────────────────────────────────

def global_status_report() -> str:
    """Genera el reporte de estado completo para el bot admin."""
    hb      = read_all_heartbeats()
    vivos   = [b for b in hb if b["alive"]]
    muertos = [b for b in hb if not b["alive"]]
    pagos   = get_due_payments(days_ahead=5)
    errors  = read_recent_errors(n=5)

    lines = [f"📊 ESTADO SISTEMA — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"]

    # Bots
    lines.append(f"🤖 BOTS ACTIVOS: {len(vivos)}/{len(hb)}")
    for b in vivos:
        lines.append(f"  ✅ {b['display_name']} ({b['tenant_id']}) — {b['age_min']}min")
    for b in muertos:
        lines.append(f"  ❌ {b['display_name']} ({b['tenant_id']}) — OFFLINE {b['age_min']}min")

    # Pagos
    if pagos:
        lines.append(f"\n💸 PAGOS URGENTES ({len(pagos)}):")
        for p in pagos:
            lines.append(
                f"  {p['status']} — {p['business_name']} (${p['monthly_fee_usd']}/mes) "
                f"| {p['client_name']} | {p.get('contact','')}"
            )

    # Errores recientes
    if errors and errors[0] != "Sin errores recientes en el log.":
        lines.append(f"\n⚠️ ÚLTIMOS ERRORES:")
        for e in errors[-3:]:
            lines.append(f"  {e[:120]}")

    return "\n".join(lines)
