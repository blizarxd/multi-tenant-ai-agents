"""
Launcher universal de agentes IA.

Modos de uso:
    # Modo plantilla (datos en profiles/<nombre>/)
    python launcher.py --perfil inmobiliaria

    # Modo tenant (cliente real, datos en tenants/<id>/)
    python launcher.py --tenant clinica_demo

    # Listar tenants registrados
    python launcher.py --list
"""

import os
import sys
import argparse
import logging
import importlib

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
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("launcher")

load_dotenv()


def load_profile_only(profile_name: str):
    profile_dir = os.path.join(os.path.dirname(__file__), "profiles", profile_name)
    if not os.path.isdir(profile_dir):
        raise SystemExit(f"❌ No existe el perfil: {profile_dir}")
    module = importlib.import_module(f"profiles.{profile_name}.profile")
    cls = getattr(module, "PROFILE_CLASS", None)
    if cls is None:
        raise SystemExit(f"❌ {profile_name}/profile.py no expone PROFILE_CLASS")
    return cls(profile_dir=profile_dir)


def main():
    parser = argparse.ArgumentParser(description="Lanzador de agentes IA.")
    parser.add_argument("--perfil", help="Nombre de la plantilla en profiles/")
    parser.add_argument("--tenant", help="ID del tenant en tenants/")
    parser.add_argument("--list", action="store_true", help="Lista tenants registrados")
    args = parser.parse_args()

    from core.tenant_registry import list_tenants, load_tenant_profile, resolve_token

    if args.list:
        tenants = list_tenants()
        if not tenants:
            print("No hay tenants registrados todavía.")
            print("Crea uno con: python admin/new_tenant.py")
            return
        print(f"\n{'ID':<25} {'TIPO':<18} {'STATUS':<10} {'DUEÑO'}")
        print("-" * 75)
        for t in tenants:
            owner = t.get('owner') or {}
            owner_str = owner.get('name', '-') if isinstance(owner, dict) else str(owner)
            print(f"{t['_id']:<25} {t.get('profile_type','?'):<18} {t.get('status','active'):<10} {owner_str}")
        return

    if not args.perfil and not args.tenant:
        parser.error("Especifica --perfil o --tenant (o usa --list)")

    if args.tenant:
        profile = load_tenant_profile(args.tenant)
        # Token: primero del tenant.json, luego TELEGRAM_TOKEN_<TENANT_UPPER>, luego genérico
        token = resolve_token(profile.tenant_meta) or os.getenv(f"TELEGRAM_TOKEN_{args.tenant.upper()}") or os.getenv("TELEGRAM_TOKEN")
    else:
        profile = load_profile_only(args.perfil)
        token = os.getenv(f"TELEGRAM_TOKEN_{args.perfil.upper()}") or os.getenv("TELEGRAM_TOKEN")

    # Detectar canal: telegram (default) o whatsapp
    channel = (profile.tenant_meta.get("channel") or "telegram").lower()

    logger.info("═" * 60)
    logger.info(f"  🤖 AGENTE: {profile.DISPLAY_NAME}")
    logger.info(f"  📁 Plantilla: {profile.NAME}")
    if args.tenant:
        logger.info(f"  🏷️  Tenant: {profile.tenant_id}")
    logger.info(f"  📍 {profile.LOCATION}")
    logger.info(f"  📡 Canal: {channel.upper()}")
    logger.info("═" * 60)

    if channel == "whatsapp":
        tid = (args.tenant or args.perfil).upper()
        wa_token    = os.getenv(f"WHATSAPP_TOKEN_{tid}") or os.getenv("WHATSAPP_TOKEN")
        phone_id    = os.getenv(f"WHATSAPP_PHONE_ID_{tid}") or os.getenv("WHATSAPP_PHONE_ID")
        verify_tok  = os.getenv(f"WHATSAPP_VERIFY_TOKEN_{tid}") or os.getenv("WHATSAPP_VERIFY_TOKEN", "mi_verify_token")
        port        = int(profile.config.get("whatsapp_port", 8000))
        if not wa_token or not phone_id:
            raise SystemExit("❌ Falta WHATSAPP_TOKEN o WHATSAPP_PHONE_ID en el .env")
        from core.whatsapp_handler import WhatsAppAgent
        agent = WhatsAppAgent(
            profile=profile,
            wa_token=wa_token,
            phone_number_id=phone_id,
            verify_token=verify_tok,
            admin_id=os.getenv("ADMIN_ID"),
            port=port,
        )
    else:
        if not token:
            raise SystemExit("❌ Falta el TELEGRAM_TOKEN en el .env")
        from core.telegram_handler import TelegramAgent
        agent = TelegramAgent(profile=profile, telegram_token=token, admin_id=os.getenv("ADMIN_ID"))

    agent.run()


if __name__ == "__main__":
    main()
