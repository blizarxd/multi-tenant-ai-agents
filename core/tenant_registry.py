"""
Registro centralizado de tenants (clientes del servicio).

Cada tenant vive en tenants/<tenant_id>/ con:
  - tenant.json   { profile_type, display_name, telegram_token_env, owner, status, ... }
  - config.json   (overrides del config base del profile)
  - catalogo.json (catálogo del cliente — reemplaza al de la plantilla)
  - media/        (fotos del cliente)

Status posibles: 'active', 'paused', 'trial'.
"""

import os
import json
import logging
import importlib
from typing import Iterator

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TENANTS_DIR = os.path.join(ROOT, "tenants")
PROFILES_DIR = os.path.join(ROOT, "profiles")


def list_tenants() -> list[dict]:
    """Lista todos los tenants encontrando sus tenant.json."""
    out = []
    if not os.path.isdir(TENANTS_DIR):
        return out
    for name in sorted(os.listdir(TENANTS_DIR)):
        if name.startswith("_") or name.startswith("."):
            continue
        tdir = os.path.join(TENANTS_DIR, name)
        meta_path = os.path.join(tdir, "tenant.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["_id"] = name
            meta["_dir"] = tdir
            out.append(meta)
        except Exception as e:
            logger.error(f"Tenant '{name}' tiene tenant.json inválido: {e}")
    return out


def active_tenants() -> Iterator[dict]:
    """Solo tenants con status active o trial."""
    for t in list_tenants():
        if t.get("status", "active") in ("active", "trial"):
            yield t


def load_tenant_profile(tenant_id: str):
    """Devuelve una instancia de profile lista para usar (con merge de config)."""
    tenant_dir = os.path.join(TENANTS_DIR, tenant_id)
    meta_path = os.path.join(tenant_dir, "tenant.json")
    if not os.path.isfile(meta_path):
        raise SystemExit(f"❌ No existe tenant: {tenant_id}")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    profile_type = meta.get("profile_type")
    if not profile_type:
        raise SystemExit(f"❌ tenant '{tenant_id}' no define profile_type")

    profile_dir = os.path.join(PROFILES_DIR, profile_type)
    if not os.path.isdir(profile_dir):
        raise SystemExit(f"❌ Plantilla '{profile_type}' no existe en profiles/")

    module = importlib.import_module(f"profiles.{profile_type}.profile")
    cls = getattr(module, "PROFILE_CLASS", None)
    if cls is None:
        raise SystemExit(f"❌ profiles/{profile_type}/profile.py no expone PROFILE_CLASS")

    return cls(profile_dir=profile_dir, tenant_dir=tenant_dir, tenant_id=tenant_id)


def resolve_token(tenant_meta: dict) -> str | None:
    """Lee la env var indicada en tenant.json (token_env)."""
    env_key = tenant_meta.get("telegram_token_env")
    if env_key:
        v = os.getenv(env_key)
        if v:
            return v
    return None
