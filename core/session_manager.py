"""
Gestor de sesiones por usuario de Telegram.
Cada sesión = un AIEngine vivo + estado conversacional.
Agnóstico al perfil del negocio.
"""

import logging
from datetime import datetime
from collections import defaultdict
from .engine import AIEngine

logger = logging.getLogger(__name__)


class UserSession:
    def __init__(self):
        self.ai: AIEngine | None = None
        self.message_count = 0
        self.created_at = datetime.now()
        self.last_active = datetime.now()
        self.context_flags: dict = {}  # ej: {"menu_sent_at": datetime, "lead_score": 7}

    def is_ready(self) -> bool:
        return self.ai is not None and self.ai.engine is not None


class SessionManager:
    """Mantiene una sesión por telegram_id. Reset diario opcional."""

    def __init__(self, profile):
        self.profile = profile
        self.sessions: dict[int, UserSession] = defaultdict(UserSession)
        self._last_reset_date = None

    def get_or_create(self, telegram_id: int, client_context: str = "") -> UserSession:
        session = self.sessions[telegram_id]
        if session.is_ready():
            session.last_active = datetime.now()
            return session

        prompt = self.profile.build_system_prompt(client_context=client_context)
        session.ai = AIEngine(
            system_prompt=prompt,
            temperature=getattr(self.profile, "TEMPERATURE", 0.7),
        )
        if session.ai.engine:
            logger.info(f"Sesión creada para {telegram_id} en tenant {getattr(self.profile, 'tenant_id', self.profile.NAME)}")
        return session

    def reset(self, telegram_id: int):
        if telegram_id in self.sessions:
            del self.sessions[telegram_id]

    def reset_all(self):
        self.sessions.clear()

    def daily_reset_if_needed(self):
        from datetime import date
        today = date.today()
        if self._last_reset_date != today:
            self.reset_all()
            self._last_reset_date = today
            logger.info("Reset diario de sesiones")
            return True
        return False
