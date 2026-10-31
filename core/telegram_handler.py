"""
Handlers genéricos de Telegram. No saben de qué negocio se trata.
Toda la lógica específica vive en el perfil.
"""

import os
import sys
import time
import hashlib
import logging
import threading
from io import BytesIO

import telebot
from PIL import Image

from .session_manager import SessionManager
from .memory import Memory

logger = logging.getLogger(__name__)

_REMINDER_INTERVAL = 60  # segundos entre chequeos de recordatorios


class TelegramAgent:
    def __init__(self, profile, telegram_token: str, admin_id: str | None = None):
        self.profile = profile
        self.bot = telebot.TeleBot(telegram_token)
        self.admin_id = admin_id
        self.memory = Memory(getattr(profile, "tenant_id", profile.NAME))
        self.profile.memory = self.memory
        # Dar acceso al bot al perfil (para mensajes proactivos como recordatorios)
        self.profile.bot = self.bot
        self.profile.admin_id = admin_id
        self.profile.agent = self
        # IDs autorizados para comandos admin: el dueño del SERVICIO (agencia)
        # + el dueño del NEGOCIO declarado en tenant.json (owner.telegram_id).
        self.owner_ids = self._compute_owner_ids(profile, admin_id)
        self.sessions = SessionManager(profile)
        self._msg_count = 0
        # Anti-duplicados: {chat_id: {"text_hash": str, "ts": float}}
        self._last_user_msg: dict[int, dict] = {}
        # Anti-repetición de respuesta: {chat_id: str}
        self._last_bot_reply: dict[int, str] = {}
        self._register_handlers()
        self._start_reminder_thread()
        self._start_heartbeat_thread()
        # Watchdog solo para el perfil admin
        if hasattr(profile, "start_watchdog"):
            profile.start_watchdog()

    # ─────────────────────────────────────────────────────────────
    def _start_reminder_thread(self):
        """Hilo daemon que envía recordatorios de citas 30 min antes."""
        def _loop():
            while True:
                try:
                    due = self.memory.get_due_reminders()
                    for r in due:
                        try:
                            self.bot.send_message(r["telegram_id"], r["message"])
                            self.memory.mark_reminder_sent(r["id"])
                            logger.info(f"Recordatorio enviado a {r['telegram_id']}")
                        except Exception as e:
                            logger.warning(f"Error enviando recordatorio #{r['id']}: {e}")
                except Exception as e:
                    logger.error(f"Error en hilo de recordatorios: {e}")
                time.sleep(_REMINDER_INTERVAL)

        t = threading.Thread(target=_loop, daemon=True, name="reminder-loop")
        t.start()

    def _start_heartbeat_thread(self):
        """Escribe heartbeat cada 5 minutos para el monitor."""
        from core.monitor import write_heartbeat

        def _loop():
            while True:
                write_heartbeat(
                    getattr(self.profile, "tenant_id", self.profile.NAME),
                    self.profile.DISPLAY_NAME,
                    self._msg_count,
                )
                time.sleep(300)  # cada 5 minutos

        threading.Thread(target=_loop, daemon=True, name="heartbeat").start()

    def _build_client_context(self, telegram_id) -> str:
        cli = self.memory.get_client(telegram_id)
        lead = self.memory.get_lead_score(telegram_id)

        # Contexto extra inyectado por el perfil (ej: CEO mode en Nova)
        extra = ""
        if hasattr(self.profile, "extra_client_context"):
            try:
                extra = self.profile.extra_client_context(telegram_id) or ""
            except Exception:
                extra = ""

        if not cli:
            return "Es un cliente NUEVO. Primer contacto." + extra

        parts = [f"Cliente recurrente: {cli.get('nombre') or 'sin nombre'}."]
        if cli.get('telefono'):
            parts.append(f"Teléfono: {cli['telefono']}.")
        if cli.get("total_eventos"):
            parts.append(f"Ha tenido {cli['total_eventos']} interacciones cerradas antes.")
        if lead:
            parts.append(
                f"Calificación previa: {lead['clasificacion']} (score {lead['score']}/10). "
                f"Motivo: {lead.get('motivo','')}"
            )

        # Hidratar últimas interacciones (memoria persistente entre sesiones)
        recientes = self.memory.recent_interactions(telegram_id, limit=8)
        if recientes:
            parts.append("\n\nÚLTIMAS INTERACCIONES PREVIAS (úsalas para dar continuidad, no las repitas literal):")
            for r in recientes[-8:]:
                role = "Cliente" if r["role"] == "user" else "Tú"
                content = (r["content"] or "")[:200]
                parts.append(f"  {role}: {content}")

        # Eventos de negocio históricos (pedidos previos, citas, etc.)
        with self.memory._conn() as c:
            ev_rows = c.execute(
                "SELECT event_type, payload, timestamp FROM events WHERE telegram_id = ? ORDER BY id DESC LIMIT 3",
                (str(telegram_id),)
            ).fetchall()
        if ev_rows:
            parts.append("\nHISTORIAL DE TRANSACCIONES PREVIAS:")
            for ev in ev_rows:
                parts.append(f"  - {ev['event_type']} ({ev['timestamp'][:10]}): {ev['payload'][:150]}")

        # Contexto médico enriquecido (solo si el perfil lo soporta)
        if hasattr(self.profile, "medical_history_context"):
            med = self.profile.medical_history_context(telegram_id, self.memory)
            if med:
                parts.append(med)

        base = " ".join(parts) if len(parts) <= 4 else "\n".join(parts)
        return base + extra

    def _safe_typing(self, chat_id, action='typing'):
        try:
            self.bot.send_chat_action(chat_id, action)
        except Exception:
            pass

    def _send_long(self, chat_id, text):
        if not text:
            return
        for attempt in range(3):
            try:
                if len(text) > 4000:
                    for i in range(0, len(text), 4000):
                        self.bot.send_message(chat_id, text[i:i+4000])
                else:
                    self.bot.send_message(chat_id, text)
                return
            except Exception as e:
                logger.warning(f"Error enviando a Telegram intento {attempt+1}: {e}")
                time.sleep(1)

    def _send_media_files(self, chat_id, paths):
        for p in paths:
            try:
                ext = p.lower().rsplit('.', 1)[-1] if '.' in p else ''
                with open(p, 'rb') as fh:
                    # timeout=180s: el default (25s) tumba uploads de video > ~5MB.
                    if ext in ('mp4', 'mov', 'webm', 'mkv'):
                        self._safe_typing(chat_id, 'upload_video')
                        self.bot.send_video(chat_id, fh, timeout=180,
                                            supports_streaming=True)
                    else:
                        self._safe_typing(chat_id, 'upload_photo')
                        self.bot.send_photo(chat_id, fh, timeout=60)
            except Exception as e:
                logger.warning(f"Error enviando media {p}: {e}")

    # ─────────────────────────────────────────────────────────────
    def _is_duplicate_msg(self, chat_id: int, text: str, window: float = 5.0) -> bool:
        """Retorna True si el mismo usuario mandó el mismo texto dentro de `window` segundos."""
        h = hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()
        now = time.time()
        prev = self._last_user_msg.get(chat_id)
        # Siempre actualizamos el registro
        self._last_user_msg[chat_id] = {"text_hash": h, "ts": now}
        if prev and prev["text_hash"] == h and (now - prev["ts"]) < window:
            return True
        return False

    def _is_duplicate_reply(self, chat_id: int, reply: str) -> bool:
        """Retorna True si la respuesta del bot es idéntica a la última enviada a este chat."""
        prev = self._last_bot_reply.get(chat_id)
        self._last_bot_reply[chat_id] = reply
        return prev is not None and prev == reply

    # ─────────────────────────────────────────────────────────────
    def _process_text(self, message, text: str):
        self.sessions.daily_reset_if_needed()
        telegram_id = message.chat.id
        first_name = message.chat.first_name or "Cliente"

        # ── Anti-spam: ignorar mensajes duplicados rápidos ──
        if self._is_duplicate_msg(telegram_id, text):
            logger.info(f"⏩ Mensaje duplicado de {telegram_id}, ignorado.")
            return

        client_ctx = self._build_client_context(telegram_id)
        session = self.sessions.get_or_create(telegram_id, client_context=client_ctx)

        if not session.is_ready():
            self.bot.reply_to(message, "El asistente está arrancando, dame un segundo. ⏳")
            return

        self._safe_typing(telegram_id)
        self.memory.upsert_client(telegram_id, nombre=first_name)
        self.memory.log_interaction(telegram_id, "user", text)
        self._msg_count += 1

        try:
            response = session.ai.send(f"El cliente dice: {text}")
        except Exception as e:
            logger.error(f"Error IA: {e}")
            err = str(e).lower()
            if any(k in err for k in ["quota", "429", "exhausted", "deadline"]):
                self.sessions.reset(telegram_id)
            # NUNCA dejar al usuario sin respuesta
            self._send_long(
                telegram_id,
                "perdón amor, se me fue la señal un momento 😅 repíteme eso que me decías"
            )
            return

        session.message_count += 1
        clean, events = self.profile.parse_response(response)

        for event_type, payload in events:
            try:
                self.profile.on_event(event_type, payload, telegram_id)
            except Exception as e:
                logger.error(f"Error en on_event: {e}")

        if self.profile.should_send_media(text):
            self._send_media_files(
                telegram_id,
                self.profile.media_files(user_text=text, telegram_id=telegram_id),
            )

        if clean:
            # ── Anti-repetición: no enviar la misma respuesta consecutiva ──
            if self._is_duplicate_reply(telegram_id, clean):
                logger.info(f"⏩ Respuesta duplicada para {telegram_id}, suprimida.")
                return
            self.memory.log_interaction(telegram_id, "assistant", clean)
            self._send_long(telegram_id, clean)

    # ── Autorización de comandos admin ────────────────────────────
    @staticmethod
    def _compute_owner_ids(profile, admin_id) -> set[str]:
        ids: set[str] = set()
        if admin_id:
            ids.add(str(admin_id))
        meta = getattr(profile, "tenant_meta", {}) or {}
        owner = meta.get("owner") or {}
        if isinstance(owner, dict) and owner.get("telegram_id"):
            ids.add(str(owner["telegram_id"]))
        return ids

    def _is_owner(self, message) -> bool:
        """True solo si quien escribe es el dueño del servicio o del negocio."""
        try:
            uid = str(message.from_user.id)
        except Exception:
            uid = str(message.chat.id)
        return bool(self.owner_ids) and uid in self.owner_ids

    def _guarded(self, handler):
        """Envuelve un comando admin para que SOLO lo ejecuten los owners.
        Sin esto, cualquier cliente podría correr /historia_excel, /broadcast, etc.
        """
        def wrapped(message):
            if not self._is_owner(message):
                logger.warning(
                    f"[{self.profile.tenant_id}] Comando admin '{message.text}' "
                    f"BLOQUEADO para {getattr(message.from_user, 'id', '?')}"
                )
                # Respuesta neutra: no revela que existen comandos privados.
                self.bot.reply_to(message, "No reconozco ese comando. Escríbeme tu consulta y te ayudo. 🙂")
                return
            if not self.owner_ids:
                logger.error(
                    f"[{self.profile.tenant_id}] ADMIN_ID no configurado — "
                    f"comandos admin deshabilitados por seguridad."
                )
            handler(message)
        return wrapped

    # ─────────────────────────────────────────────────────────────
    def _register_handlers(self):
        bot = self.bot
        profile = self.profile
        admin_cmds = profile.admin_commands()

        @bot.message_handler(commands=['start', 'help'])
        def _cmd_start(message):
            logger.info(f"Comando /start de {message.chat.id}")
            self.sessions.daily_reset_if_needed()
            telegram_id = message.chat.id
            client_ctx = self._build_client_context(telegram_id)
            session = self.sessions.get_or_create(telegram_id, client_context=client_ctx)
            if session.is_ready():
                self._safe_typing(telegram_id)
                try:
                    resp = session.ai.send("El cliente acaba de iniciar la conversación. Salúdalo según tus instrucciones.")
                    clean, events = profile.parse_response(resp)
                    for et, pl in events:
                        profile.on_event(et, pl, telegram_id)
                    self._send_long(telegram_id, clean or profile.welcome_message())
                except Exception as e:
                    logger.error(f"Error /start: {e}")
                    self._send_long(telegram_id, profile.welcome_message())
            else:
                self._send_long(telegram_id, profile.welcome_message())

        @bot.message_handler(commands=['reset'])
        def _cmd_reset(message):
            self.sessions.reset(message.chat.id)
            bot.reply_to(message, "Sesión reiniciada. Escríbeme de nuevo.")

        # Comandos custom del perfil — SIEMPRE protegidos por owner-guard.
        # Antes cualquier cliente del bot podía ejecutar /historia_excel,
        # /broadcast, /reporte_pdf, etc. y exponer datos de todos los clientes.
        for cmd, handler in admin_cmds.items():
            raw = lambda m, h=handler: h(m, bot, profile, self.memory)
            bot.message_handler(commands=[cmd])(self._guarded(raw))

        @bot.message_handler(content_types=['location'])
        def _on_location(message):
            self._process_text(
                message,
                f"[SISTEMA: ubicación GPS recibida — lat {message.location.latitude}, lon {message.location.longitude}]"
            )

        @bot.message_handler(content_types=['voice'])
        def _on_voice(message):
            telegram_id = message.chat.id
            first_name  = message.chat.first_name or "Cliente"
            client_ctx  = self._build_client_context(telegram_id)
            session     = self.sessions.get_or_create(telegram_id, client_context=client_ctx)
            if not session.is_ready():
                bot.reply_to(message, "Escríbeme tu mensaje porfa, no puedo escuchar ahorita.")
                return
            self._safe_typing(telegram_id, 'record_voice')
            self.memory.upsert_client(telegram_id, nombre=first_name)
            self.memory.log_interaction(telegram_id, "user", "[Nota de voz]")
            try:
                if session.ai.engine != 'gemini':
                    resp = session.ai.send(
                        "El cliente envió una nota de voz pero el motor actual no procesa audio. "
                        "Dile amablemente que escriba su mensaje."
                    )
                else:
                    file_info  = bot.get_file(message.voice.file_id)
                    audio_bytes = bot.download_file(file_info.file_path)
                    audio_data  = {"mime_type": "audio/ogg", "data": audio_bytes}
                    resp = session.ai.send_with_media(
                        "El cliente envió una nota de voz. Transcribe lo que dice y responde "
                        "como si fuera texto normal, siguiendo el flujo de la conversación.",
                        audio_data
                    )
                clean, events = profile.parse_response(resp)
                for et, pl in events:
                    profile.on_event(et, pl, telegram_id)
                if clean:
                    self.memory.log_interaction(telegram_id, "assistant", clean)
                    self._send_long(telegram_id, clean)
            except Exception as e:
                logger.error(f"Error voz: {e}")
                bot.reply_to(message, "No te entendí el audio, ¿me lo escribes?")

        @bot.message_handler(content_types=['video_note'])
        def _on_video_note(message):
            """Videos circulares (notas de video de Telegram)."""
            telegram_id = message.chat.id
            first_name  = message.chat.first_name or "Cliente"
            client_ctx  = self._build_client_context(telegram_id)
            session     = self.sessions.get_or_create(telegram_id, client_context=client_ctx)
            if not session.is_ready():
                bot.reply_to(message, "No puedo procesar el video ahorita, escríbeme.")
                return
            self._safe_typing(telegram_id)
            self.memory.upsert_client(telegram_id, nombre=first_name)
            self.memory.log_interaction(telegram_id, "user", "[Nota de video]")
            try:
                if session.ai.engine != 'gemini':
                    resp = session.ai.send(
                        "El cliente envió un video circular pero el motor actual no lo procesa. "
                        "Dile amablemente que escriba o envíe una foto."
                    )
                else:
                    file_info   = bot.get_file(message.video_note.file_id)
                    video_bytes = bot.download_file(file_info.file_path)
                    video_data  = {"mime_type": "video/mp4", "data": video_bytes}
                    resp = session.ai.send_with_media(
                        "El cliente envió un video corto (nota de video). Analiza el contenido visual "
                        "y responde según el flujo de la conversación.",
                        video_data
                    )
                clean, events = profile.parse_response(resp)
                for et, pl in events:
                    profile.on_event(et, pl, telegram_id)
                if clean:
                    self.memory.log_interaction(telegram_id, "assistant", clean)
                    self._send_long(telegram_id, clean)
            except Exception as e:
                logger.error(f"Error video_note: {e}")
                bot.reply_to(message, "No pude ver el video, ¿me escribes o mandas una foto?")

        @bot.message_handler(content_types=['photo'])
        def _on_photo(message):
            telegram_id = message.chat.id
            client_ctx = self._build_client_context(telegram_id)
            session = self.sessions.get_or_create(telegram_id, client_context=client_ctx)
            if not session.is_ready():
                bot.reply_to(message, "No puedo procesar fotos ahorita.")
                return
            bot.reply_to(message, "Recibido, revisando…")
            try:
                if session.ai.engine != 'gemini':
                    resp = session.ai.send(
                        "El cliente envió una foto pero el motor actual no la ve. Pide los datos por escrito."
                    )
                else:
                    file_info = bot.get_file(message.photo[-1].file_id)
                    img_bytes = bot.download_file(file_info.file_path)
                    image = Image.open(BytesIO(img_bytes))
                    resp = session.ai.send_with_media(
                        profile.photo_prompt(),
                        image
                    )
                clean, events = profile.parse_response(resp)
                for et, pl in events:
                    profile.on_event(et, pl, telegram_id)
                if clean:
                    self.memory.log_interaction(telegram_id, "assistant", clean)
                    self._send_long(telegram_id, clean)
            except Exception as e:
                logger.error(f"Error foto: {e}")
                bot.reply_to(message, "No pude procesar la imagen, mándamela de nuevo.")

        @bot.message_handler(content_types=['text'])
        def _on_text(message):
            logger.info(f"Mensaje recibido de {message.chat.id}: {message.text[:50]}")
            self._process_text(message, message.text)

    # ─────────────────────────────────────────────────────────────
    def run(self):
        logger.info(f"Bot {self.profile.DISPLAY_NAME} en línea")
        try:
            self.bot.remove_webhook()
            logger.info("Webhook/sesión anterior limpiada")
        except Exception:
            pass
        time.sleep(2)

        self.bot.infinity_polling(
            timeout=20,
            long_polling_timeout=20,
            logger_level=logging.INFO,
            restart_on_change=False,
        )
