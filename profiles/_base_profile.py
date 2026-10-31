"""
Clase base para todos los perfiles de negocio.

ARQUITECTURA MULTI-TENANT:
- profile_dir = la PLANTILLA (tipo de negocio: inmobiliaria, inmobiliaria, etc.)
- tenant_dir  = el CLIENTE REAL que paga el servicio (su data específica)

Si hay tenant_dir, su config/catalogo SOBRESCRIBE el del profile.
Esto permite que 50 ferreterías distintas usen la misma plantilla "ferreteria"
pero cada una con su catálogo, nombre, dueño y BD aparte.

Cada perfil concreto debe implementar:
  - NAME, EVENT_TAGS
  - build_system_prompt(client_context)
  - welcome_message()
"""

import os
import json
import re
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Merge profundo de dicts. overrides gana."""
    out = dict(base)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_env_refs(value):
    """Resuelve referencias a variables de entorno dentro del config.
    Un valor como "env:GMAIL_APP_PASSWORD" o "${GMAIL_APP_PASSWORD}" se reemplaza
    por el valor real de la env var. Así los secretos NO viven en config.json.
    Si la env var no existe, deja el valor original (y se loguea aparte).
    """
    if isinstance(value, dict):
        return {k: _resolve_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(v) for v in value]
    if isinstance(value, str):
        ref = None
        if value.startswith("env:"):
            ref = value[4:].strip()
        else:
            m = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", value.strip())
            if m:
                ref = m.group(1)
        if ref:
            resolved = os.getenv(ref)
            if resolved is None:
                logger.warning(f"Config referencia env var '{ref}' pero no está definida en .env")
                return ""
            return resolved
    return value


class BaseProfile(ABC):
    NAME: str = "base"                    # nombre del TIPO de negocio
    DISPLAY_NAME: str = "Negocio"
    LOCATION: str = "San Cristóbal, Táchira"
    EVENT_TAGS: dict[str, str] = {}
    # Temperatura del LLM. 0.7 = natural (ventas/conversación).
    # Bájala (0.3–0.4) en perfiles transaccionales que necesitan precisión
    # al capturar fechas/montos (médico, dental, ferretería).
    TEMPERATURE: float = 0.7

    def __init__(self, profile_dir: str, tenant_dir: str | None = None,
                 tenant_id: str | None = None, memory=None):
        self.profile_dir = profile_dir
        self.tenant_dir = tenant_dir
        self.tenant_id = tenant_id or self.NAME  # ID único para BD
        self.memory = memory
        self.config: dict = {}
        self.catalogo = {}
        self.tenant_meta: dict = {}
        self.media_dir = self._resolve_media_dir()
        self.load_data()

    def _resolve_media_dir(self) -> str:
        # Prioriza media del tenant; si no existe, cae al profile
        if self.tenant_dir:
            t_media = os.path.join(self.tenant_dir, "media")
            if os.path.isdir(t_media) and os.listdir(t_media):
                return t_media
        return os.path.join(self.profile_dir, "media")

    # ── Carga: profile defaults + tenant overrides ────────────────
    def load_data(self):
        # 1. Cargar plantilla del profile
        config = self._load_json(os.path.join(self.profile_dir, "config.json"), {})
        catalogo = self._load_json(os.path.join(self.profile_dir, "catalogo.json"), {})

        # 2. Aplicar overrides del tenant
        if self.tenant_dir:
            tenant_meta = self._load_json(os.path.join(self.tenant_dir, "tenant.json"), {})
            tenant_config = self._load_json(os.path.join(self.tenant_dir, "config.json"), {})
            tenant_catalogo = self._load_json(os.path.join(self.tenant_dir, "catalogo.json"), None)

            self.tenant_meta = tenant_meta
            config = _deep_merge(config, tenant_config)
            if tenant_catalogo is not None:
                catalogo = tenant_catalogo  # el catálogo se reemplaza completo

            # Override de DISPLAY_NAME desde el tenant
            if tenant_meta.get("display_name"):
                self.DISPLAY_NAME = tenant_meta["display_name"]
            if tenant_meta.get("location"):
                self.LOCATION = tenant_meta["location"]

        # Resolver secretos referenciados como "env:VAR" o "${VAR}" → .env
        self.config = _resolve_env_refs(config)
        self.catalogo = catalogo

    @staticmethod
    def _load_json(path: str, default):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return default

    def media_files(self, exts=(".png", ".jpg", ".jpeg"),
                    user_text: str = "", telegram_id: int = 0) -> list[str]:
        # user_text + telegram_id se pasan para que perfiles que rotan media
        # por cliente o varían por intención puedan implementarlo.
        # Default: ignora ambos y devuelve todas las fotos del media_dir.
        if not os.path.isdir(self.media_dir):
            return []
        return sorted([
            os.path.join(self.media_dir, f)
            for f in os.listdir(self.media_dir)
            if f.lower().endswith(exts)
        ])

    # ── Métodos abstractos ────────────────────────────────────────
    @abstractmethod
    def build_system_prompt(self, client_context: str = "") -> str: ...

    @abstractmethod
    def welcome_message(self) -> str: ...

    # ── Parser genérico de tags ───────────────────────────────────
    def parse_response(self, text: str) -> tuple[str, list[tuple[str, dict]]]:
        if not text:
            return "", []
        events = []
        clean = text
        for tag, event_type in self.EVENT_TAGS.items():
            pattern = re.compile(rf"\[{tag}\](.*?)\[/{tag}\]", re.DOTALL)
            for match in pattern.finditer(text):
                payload = self._parse_block(match.group(1).strip())
                events.append((event_type, payload))
            clean = pattern.sub("", clean)
            clean = clean.replace(f"[{tag}]", "").replace(f"[/{tag}]", "")
        # Gemini insiste en usar markdown aunque el prompt lo prohíba:
        # "* item" → "- item"; **bold**/*italic* → texto plano.
        clean = re.sub(r"^[ \t]*\*+[ \t]+", "- ", clean, flags=re.MULTILINE)
        clean = clean.replace("*", "")
        return clean.strip(), events

    @staticmethod
    def _parse_block(block: str) -> dict:
        data = {}
        for line in block.split("\n"):
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip().lower().replace(" ", "_")] = v.strip()
        return data

    # ── Hooks ─────────────────────────────────────────────────────
    def on_event(self, event_type: str, payload: dict, telegram_id: int):
        if self.memory:
            self.memory.save_event(telegram_id, event_type, payload)
        logger.info(f"[{self.tenant_id}] Evento {event_type} de {telegram_id}: {payload}")

    def should_send_media(self, user_text: str) -> bool:
        return False

    def photo_prompt(self) -> str:
        """Prompt que se envía a Gemini Vision cuando el cliente manda una foto.
        Los perfiles pueden sobrescribirlo para análisis especializados."""
        return "El cliente envió una imagen. Si es comprobante de pago verifica y confirma; si no, responde según el contexto de la conversación."

    def admin_commands(self) -> dict:
        return {}
