"""
Multi-launcher: corre TODOS los tenants activos en paralelo, en threads.

Uso:
    python multi_launcher.py

Cada tenant corre en su propio thread con su bot, su BD y su sesión aislada.
Esto te permite atender 50 negocios desde un solo proceso/servidor.
"""

import os
import sys
import time
import logging
import threading

from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_multi.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("multi")

load_dotenv()

# Señal global para apagado ordenado de todos los supervisores.
_STOP = threading.Event()


def _alert_admin(text: str, fallback_token: str | None = None):
    """Avisa al dueño del servicio (ADMIN_ID) cuando un bot se cae o se recupera.
    Usa el bot admin si existe; si no, el token del propio tenant.
    """
    admin_id = os.getenv("ADMIN_ID")
    token = os.getenv("TELEGRAM_TOKEN_ADMIN") or os.getenv("TELEGRAM_TOKEN") or fallback_token
    if not admin_id or not token:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": admin_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"No se pudo alertar al admin: {e}")


def _build_agent(tenant_meta: dict, port_hint: int):
    """Construye el agente (Telegram o WhatsApp) o devuelve None si falta config."""
    from core.tenant_registry import load_tenant_profile, resolve_token

    tid     = tenant_meta["_id"]
    channel = (tenant_meta.get("channel") or "telegram").lower()
    profile = load_tenant_profile(tid)

    if channel == "whatsapp":
        from core.whatsapp_handler import WhatsAppAgent
        wa_token   = os.getenv(f"WHATSAPP_TOKEN_{tid.upper()}") or os.getenv("WHATSAPP_TOKEN")
        phone_id   = os.getenv(f"WHATSAPP_PHONE_ID_{tid.upper()}") or os.getenv("WHATSAPP_PHONE_ID")
        verify_tok = os.getenv(f"WHATSAPP_VERIFY_TOKEN_{tid.upper()}") or os.getenv("WHATSAPP_VERIFY_TOKEN", "mi_verify_token")
        # Puerto: el del config si lo define; si no, uno único por tenant (evita
        # que dos bots de WhatsApp choquen al intentar bindear el mismo 8000).
        port = int(profile.config.get("whatsapp_port") or port_hint)
        if not wa_token or not phone_id:
            logger.error(f"[{tid}] Sin WHATSAPP_TOKEN o WHATSAPP_PHONE_ID. Saltando.")
            return None, channel
        agent = WhatsAppAgent(profile=profile, wa_token=wa_token, phone_number_id=phone_id,
                              verify_token=verify_tok, admin_id=os.getenv("ADMIN_ID"), port=port)
        return agent, channel

    from core.telegram_handler import TelegramAgent
    token = resolve_token(tenant_meta) or os.getenv(f"TELEGRAM_TOKEN_{tid.upper()}")
    if not token:
        logger.error(f"[{tid}] Sin token (revisa tenant.json::telegram_token_env). Saltando.")
        return None, channel
    agent = TelegramAgent(profile=profile, telegram_token=token, admin_id=os.getenv("ADMIN_ID"))
    agent._self_token = token  # para alertas con su propio bot
    return agent, channel


def run_tenant(tenant_meta: dict, port_hint: int = 8000):
    """Supervisa un tenant: si su bot se cae, lo reinicia con backoff y alerta al admin.
    Antes, un crash dejaba el bot muerto en silencio hasta reinicio manual.
    """
    tid = tenant_meta["_id"]
    backoff = 5
    MAX_BACKOFF = 300
    fails = 0

    while not _STOP.is_set():
        self_token = None
        try:
            agent, channel = _build_agent(tenant_meta, port_hint)
            if agent is None:
                return  # config faltante: no tiene sentido reintentar
            self_token = getattr(agent, "_self_token", None)

            if fails > 0:
                _alert_admin(f"✅ Bot '{tid}' se recuperó y está en línea de nuevo.", self_token)
            fails = 0
            backoff = 5

            logger.info(f"[{tid}] arrancando '{agent.profile.DISPLAY_NAME}' [{channel.upper()}]")
            agent.run()  # bloquea; si retorna o lanza, lo tomamos como caída
            logger.warning(f"[{tid}] el bot terminó su loop inesperadamente — reiniciando")
        except Exception as e:
            logger.error(f"[{tid}] crash: {e}")
            fails += 1
            # Avisar solo en la primera caída y cuando ya llegó al backoff máximo,
            # para no spamear al admin con un bot permanentemente roto.
            if fails == 1 or backoff >= MAX_BACKOFF:
                _alert_admin(f"⚠️ Bot '{tid}' se cayó: {e}\nReintentando en {backoff}s.", self_token)

        if _STOP.wait(backoff):
            break
        backoff = min(backoff * 2, MAX_BACKOFF)


def main():
    from core.tenant_registry import active_tenants

    tenants = list(active_tenants())
    if not tenants:
        print("No hay tenants activos. Crea uno con: python admin/new_tenant.py")
        return

    logger.info("═" * 60)
    logger.info(f"  🚀 MULTI-LAUNCHER — {len(tenants)} tenant(s) activos")
    logger.info("═" * 60)
    for t in tenants:
        logger.info(f"  • {t['_id']:<25} ({t.get('profile_type')}) — {t.get('display_name','?')}")
    logger.info("═" * 60)

    from core.tenant_registry import resolve_token

    threads = []
    wa_port = 8000
    used_tokens = set()
    for t in tenants:
        tid = t["_id"]
        channel = (t.get("channel") or "telegram").lower()
        if channel == "telegram":
            token = resolve_token(t) or os.getenv(f"TELEGRAM_TOKEN_{tid.upper()}")
            if not token:
                logger.warning(f"[{tid}] Sin token configurado en .env. Saltando.")
                continue
            if token in used_tokens:
                logger.warning(f"[{tid}] El token ya está en uso por otro tenant activo. Saltando para evitar conflictos.")
                continue
            used_tokens.add(token)

        # Puerto único por cada tenant de WhatsApp (8000, 8001, 8002, ...).
        port_hint = wa_port
        if channel == "whatsapp":
            wa_port += 1
        th = threading.Thread(
            target=run_tenant, args=(t, port_hint), name=f"bot-{t['_id']}", daemon=True
        )
        th.start()
        threads.append(th)
        time.sleep(1)  # escalonar arranque para no spamear Telegram

    try:
        while any(th.is_alive() for th in threads):
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Detenido manualmente. Cerrando supervisores...")
        _STOP.set()
        _alert_admin("🛑 Multi-launcher detenido manualmente. Todos los bots están offline.")


if __name__ == "__main__":
    main()
