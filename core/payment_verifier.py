"""
Verificador de pagos — email bancario (IMAP) + Binance P2P API.

Flujo:
  1. Cliente dice "pagué, referencia 1234567890"
  2. IA emite [PAGO_PARA_VERIFICAR] con la referencia extraída
  3. on_event("verificar_pago", ...) llama cap_verify_payment()
  4. cap_verify_payment() busca en Gmail el número de referencia
  5. Si lo encuentra → confirma automáticamente al cliente por Telegram
  6. Si no lo encuentra → pide el comprobante como respaldo

Para Gmail:
  config["gmail_verificacion_email"]    = correo donde llegan alertas del banco
  config["gmail_verificacion_password"] = App Password (no la contraseña real)

Para Binance P2P:
  config["binance_api_key"]    = key con permiso de solo lectura
  config["binance_api_secret"] = secret correspondiente
"""

import imaplib
import email
import re
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Remitentes de bancos + servicios de pago ─────────────────────────
BANK_SENDERS = [
    # Venezuela
    "banesco", "bdv.com.ve", "bancodevenezuela", "mercantilbanco",
    "provincial", "bancaribe", "ban100", "bicentenario",
    "sofitasa", "bangente", "bfc", "banco activo",
    # Colombia
    "bancolombia", "davivienda", "bancobogota", "nequi",
    # Otros
    "zelle", "paypal", "binance", "cripto",
    # Genérico
    "notificacion", "notificaciones", "alertas", "transaccion",
    "pagomovil", "pago-movil", "transferencia", "no-reply",
    "noreply",
]

# ── Patrones para extraer monto ──────────────────────────────────────
_AMOUNT_PATTERNS = [
    r"[Mm]onto[\s:]*[\$Bs\.]*\s*([\d]{1,10}[.,]\d{2})",
    r"[Tt]otal[\s:]*[\$Bs\.]*\s*([\d]{1,10}[.,]\d{2})",
    r"USD\s*([\d]{1,10}[.,]\d{2})",
    r"\$([\d]{1,10}[.,]\d{2})",
    r"([\d]{1,10}[.,]\d{2})\s*(?:USD|USDT|dólares|dolares|Bs)",
    r"[Ii]mporte[\s:]*[\$Bs\.]*\s*([\d]{1,10}[.,]\d{2})",
]


def _connect_gmail(email_addr: str, app_password: str) -> "imaplib.IMAP4_SSL | None":
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_addr, app_password)
        mail.select("inbox")
        return mail
    except Exception as e:
        logger.warning(f"[PayVerifier] IMAP connect error: {e}")
        return None


def _get_text_body(msg) -> str:
    """Extrae el cuerpo del email como texto plano."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        except Exception:
            pass
    return body


def _extract_amount(text: str) -> str | None:
    for pat in _AMOUNT_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def _amounts_match(declared: float, found_str: str, tolerance: float = 0.05) -> bool:
    """True si los montos coinciden dentro del margen de tolerancia."""
    if not found_str or declared <= 0:
        return False
    try:
        cleaned = re.sub(r"[,\.](?=\d{3})", "", found_str).replace(",", ".")
        found_num = float(cleaned)
        return abs(found_num - declared) <= (declared * tolerance)
    except Exception:
        return False


def search_payment_by_reference(
    email_addr: str,
    app_password: str,
    reference: str,
    declared_amount: float = 0.0,
    hours_back: int = 48,
) -> dict:
    """
    Busca un pago por número de referencia en los últimos `hours_back` horas.

    Returns dict con keys:
        found: bool
        amount: str          monto detectado en el email
        bank: str            remitente del email
        date: str            fecha del email
        amount_match: bool   True si el monto coincide con el declarado
        snippet: str         fragmento del email (para logs/debug)
        error: str           mensaje de error si found=False
    """
    ref_clean = re.sub(r"[\s\-\.]", "", reference)
    if len(ref_clean) < 4:
        return {"found": False, "error": "Referencia demasiado corta para buscar"}

    mail = _connect_gmail(email_addr, app_password)
    if not mail:
        return {"found": False, "error": "No se pudo conectar al correo de verificación"}

    try:
        since_date = (datetime.now() - timedelta(hours=hours_back)).strftime("%d-%b-%Y")
        status, msg_ids = mail.search(None, f'(SINCE "{since_date}")')

        if status != "OK" or not msg_ids[0]:
            return {"found": False, "error": "Sin emails recientes para revisar"}

        ids = msg_ids[0].split()

        for msg_id in reversed(ids[-150:]):  # max 150 emails, más recientes primero
            try:
                status, data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(data[0][1])
                sender = str(msg.get("From", "")).lower()
                subject = str(msg.get("Subject", "")).lower()
                body = _get_text_body(msg)
                full_text = body + " " + subject

                # Filtrar: ¿parece un email bancario/de pago?
                is_bank_email = any(b in sender or b in subject for b in BANK_SENDERS)
                if not is_bank_email:
                    continue

                # Buscar la referencia (ignora guiones, puntos, espacios)
                full_clean = re.sub(r"[\s\-\.]", "", full_text)
                if ref_clean.lower() not in full_clean.lower():
                    continue

                # Encontrado — extraer monto
                amount_str = _extract_amount(full_text)
                match = _amounts_match(declared_amount, amount_str)

                return {
                    "found": True,
                    "amount": amount_str or "no detectado",
                    "bank": sender[:80],
                    "date": str(msg.get("Date", ""))[:30],
                    "amount_match": match,
                    "snippet": full_text[:400].strip(),
                    "error": "",
                }

            except Exception as e:
                logger.debug(f"[PayVerifier] Error leyendo email {msg_id}: {e}")
                continue

        return {"found": False, "error": "Referencia no encontrada en los últimos correos"}

    except Exception as e:
        logger.warning(f"[PayVerifier] Error búsqueda IMAP: {e}")
        return {"found": False, "error": str(e)}

    finally:
        try:
            mail.logout()
        except Exception:
            pass


def verify_binance_order(api_key: str, api_secret: str, order_id: str) -> dict:
    """
    Verifica una orden Binance P2P por Order Number.
    Requiere: requests (ya en requirements).
    """
    try:
        import hmac
        import hashlib
        import time
        import requests

        ts  = int(time.time() * 1000)
        qs  = f"orderNumber={order_id}&timestamp={ts}"
        sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

        r = requests.get(
            "https://api.binance.com/sapi/v1/c2c/orderMatch/listUserOrderHistory",
            headers={"X-MBX-APIKEY": api_key},
            params={"orderNumber": order_id, "timestamp": ts, "signature": sig},
            timeout=10,
        )
        data = r.json()
        orders = data.get("data", [])
        if not orders:
            return {"found": False, "error": "Orden no encontrada en Binance"}

        o = orders[0]
        completed = str(o.get("orderStatus", "")).upper() == "COMPLETED"
        return {
            "found": True,
            "completed": completed,
            "status": o.get("orderStatus", ""),
            "amount": str(o.get("amount", "")),
            "asset": o.get("asset", "USDT"),
            "date": str(o.get("createTime", "")),
            "error": "",
        }
    except Exception as e:
        logger.warning(f"[PayVerifier] Binance API error: {e}")
        return {"found": False, "error": str(e)}
