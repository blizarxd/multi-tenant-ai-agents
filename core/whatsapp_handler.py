"""
Handler de WhatsApp Business Cloud API (Meta).

Arquitectura idéntica al TelegramAgent pero con webhook en vez de polling.
El perfil del negocio no cambia — misma lógica, distinto canal.

Requisitos en .env del tenant:
    WHATSAPP_TOKEN_<TENANT>         — Bearer token de la app de Meta
    WHATSAPP_PHONE_ID_<TENANT>      — Phone Number ID del número de WhatsApp
    WHATSAPP_VERIFY_TOKEN_<TENANT>  — Cualquier string secreto para verificar webhook

El dueño recibe notificaciones en su WhatsApp cuando llega un pedido/evento:
    config["dueno_whatsapp"]  — número en formato internacional sin + (ej: 584141234567)
"""

import os
import time
import logging
import threading
import requests
from io import BytesIO
from flask import Flask, request, jsonify
from PIL import Image

from .session_manager import SessionManager
from .memory import Memory

logger = logging.getLogger(__name__)

META_API = "https://graph.facebook.com/v19.0"
_REMINDER_INTERVAL = 60


class WhatsAppAgent:
    def __init__(self, profile, wa_token: str, phone_number_id: str,
                 verify_token: str, admin_id: str | None = None,
                 port: int = 8000):
        self.profile = profile
        self.wa_token = wa_token
        self.phone_number_id = phone_number_id
        self.verify_token = verify_token
        self.admin_id = admin_id
        self.port = port
        self.memory = Memory(getattr(profile, "tenant_id", profile.NAME))
        self.profile.memory = self.memory
        self.profile.bot = self           # para mensajes proactivos del perfil
        self.profile.admin_id = admin_id
        self.sessions = SessionManager(profile)
        self._msg_count = 0
        self._app = Flask(f"wa_{profile.NAME}")
        self._register_routes()
        self._start_reminder_thread()
        self._start_heartbeat_thread()
        if hasattr(profile, "start_watchdog"):
            profile.start_watchdog()

    # ── Envío de mensajes ──────────────────────────────────────────

    def send_message(self, to: str, text: str):
        """Envía texto al número `to` (formato: 584141234567)."""
        if not text:
            return
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            for attempt in range(3):
                try:
                    r = requests.post(
                        f"{META_API}/{self.phone_number_id}/messages",
                        headers={"Authorization": f"Bearer {self.wa_token}"},
                        json={
                            "messaging_product": "whatsapp",
                            "to": to,
                            "type": "text",
                            "text": {"body": chunk},
                        },
                        timeout=15,
                    )
                    r.raise_for_status()
                    break
                except Exception as e:
                    logger.warning(f"[WA] Error enviando a {to} intento {attempt+1}: {e}")
                    time.sleep(1)

    def notify_owner(self, text: str):
        """Envía notificación al dueño del negocio vía WhatsApp."""
        dueno = self.profile.config.get("dueno_whatsapp")
        if dueno:
            self.send_message(str(dueno), text)

    def _download_media(self, media_id: str) -> bytes | None:
        """Descarga un archivo multimedia de WhatsApp por su media_id."""
        try:
            # Primero obtener la URL
            r = requests.get(
                f"{META_API}/{media_id}",
                headers={"Authorization": f"Bearer {self.wa_token}"},
                timeout=15,
            )
            r.raise_for_status()
            url = r.json().get("url")
            if not url:
                return None
            # Luego descargar el archivo
            r2 = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.wa_token}"},
                timeout=30,
            )
            r2.raise_for_status()
            return r2.content
        except Exception as e:
            logger.warning(f"[WA] Error descargando media {media_id}: {e}")
            return None

    # ── Threads de fondo ───────────────────────────────────────────

    def _start_reminder_thread(self):
        def _loop():
            while True:
                try:
                    due = self.memory.get_due_reminders()
                    for r in due:
                        try:
                            self.send_message(r["telegram_id"], r["message"])
                            self.memory.mark_reminder_sent(r["id"])
                        except Exception as e:
                            logger.warning(f"[WA] Error recordatorio #{r['id']}: {e}")
                except Exception as e:
                    logger.error(f"[WA] Error hilo recordatorios: {e}")
                time.sleep(_REMINDER_INTERVAL)
        threading.Thread(target=_loop, daemon=True, name="wa-reminder").start()

    def _start_heartbeat_thread(self):
        from core.monitor import write_heartbeat
        def _loop():
            while True:
                write_heartbeat(
                    getattr(self.profile, "tenant_id", self.profile.NAME),
                    self.profile.DISPLAY_NAME,
                    self._msg_count,
                )
                time.sleep(300)
        threading.Thread(target=_loop, daemon=True, name="wa-heartbeat").start()

    # ── Procesamiento de mensajes ──────────────────────────────────

    def _build_client_context(self, wa_id: str) -> str:
        cli = self.memory.get_client(wa_id)
        if not cli:
            return "Es un cliente NUEVO. Primer contacto."
        parts = [f"Cliente recurrente: {cli.get('nombre') or 'sin nombre'}."]
        if cli.get("telefono"):
            parts.append(f"Teléfono: {cli['telefono']}.")
        if cli.get("total_eventos"):
            parts.append(f"Ha tenido {cli['total_eventos']} interacciones antes.")
        recientes = self.memory.recent_interactions(wa_id, limit=8)
        if recientes:
            parts.append("\n\nÚLTIMAS INTERACCIONES:")
            for r in recientes[-8:]:
                role = "Cliente" if r["role"] == "user" else "Tú"
                parts.append(f"  {role}: {(r['content'] or '')[:200]}")
        return "\n".join(parts)

    def _process_text(self, wa_id: str, name: str, text: str):
        self.sessions.daily_reset_if_needed()
        client_ctx = self._build_client_context(wa_id)
        session = self.sessions.get_or_create(wa_id, client_context=client_ctx)
        if not session.is_ready():
            self.send_message(wa_id, "El asistente está iniciando, escríbeme en un momento.")
            return

        self.memory.upsert_client(wa_id, nombre=name)
        self.memory.log_interaction(wa_id, "user", text)
        self._msg_count += 1

        try:
            response = session.ai.send(f"El cliente dice: {text}")
        except Exception as e:
            logger.error(f"[WA] Error IA: {e}")
            err = str(e).lower()
            if any(k in err for k in ["quota", "429", "exhausted", "deadline"]):
                self.sessions.reset(wa_id)
            return

        session.message_count += 1
        clean, events = self.profile.parse_response(response)

        for event_type, payload in events:
            try:
                self.profile.on_event(event_type, payload, wa_id)
            except Exception as e:
                logger.error(f"[WA] Error on_event: {e}")

        if clean:
            self.memory.log_interaction(wa_id, "assistant", clean)
            self.send_message(wa_id, clean)

    def _process_image(self, wa_id: str, name: str, media_id: str, caption: str = ""):
        self.sessions.daily_reset_if_needed()
        client_ctx = self._build_client_context(wa_id)
        session = self.sessions.get_or_create(wa_id, client_context=client_ctx)
        if not session.is_ready():
            return
        self.memory.upsert_client(wa_id, nombre=name)
        self.memory.log_interaction(wa_id, "user", "[Foto enviada]")
        try:
            if session.ai.engine != "gemini":
                resp = session.ai.send("El cliente envió una foto. Pide los datos por escrito.")
            else:
                img_bytes = self._download_media(media_id)
                if not img_bytes:
                    self.send_message(wa_id, "No pude ver la imagen, ¿me la mandas de nuevo?")
                    return
                image = Image.open(BytesIO(img_bytes))
                prompt = self.profile.photo_prompt()
                if caption:
                    prompt += f"\nEl cliente escribió junto a la foto: {caption}"
                resp = session.ai.send_with_media(prompt, image)
            clean, events = self.profile.parse_response(resp)
            for et, pl in events:
                self.profile.on_event(et, pl, wa_id)
            if clean:
                self.memory.log_interaction(wa_id, "assistant", clean)
                self.send_message(wa_id, clean)
        except Exception as e:
            logger.error(f"[WA] Error foto: {e}")
            self.send_message(wa_id, "No pude procesar la imagen, intenta de nuevo.")

    def _process_audio(self, wa_id: str, name: str, media_id: str):
        self.sessions.daily_reset_if_needed()
        client_ctx = self._build_client_context(wa_id)
        session = self.sessions.get_or_create(wa_id, client_context=client_ctx)
        if not session.is_ready():
            return
        self.memory.upsert_client(wa_id, nombre=name)
        self.memory.log_interaction(wa_id, "user", "[Nota de voz]")
        try:
            if session.ai.engine != "gemini":
                resp = session.ai.send("El cliente envió un audio. Dile que escriba el mensaje.")
            else:
                audio_bytes = self._download_media(media_id)
                if not audio_bytes:
                    self.send_message(wa_id, "No pude escuchar el audio, ¿me lo escribes?")
                    return
                audio_data = {"mime_type": "audio/ogg", "data": audio_bytes}
                resp = session.ai.send_with_media(
                    "El cliente envió una nota de voz. Transcribe y responde según el flujo.",
                    audio_data,
                )
            clean, events = self.profile.parse_response(resp)
            for et, pl in events:
                self.profile.on_event(et, pl, wa_id)
            if clean:
                self.memory.log_interaction(wa_id, "assistant", clean)
                self.send_message(wa_id, clean)
        except Exception as e:
            logger.error(f"[WA] Error audio: {e}")
            self.send_message(wa_id, "No entendí el audio, ¿me lo escribes?")

    # ── Rutas Flask (webhook) ──────────────────────────────────────

    def _register_routes(self):
        app = self._app

        @app.get("/webhook")
        def verify():
            """Meta llama aquí para verificar el webhook."""
            mode      = request.args.get("hub.mode")
            token     = request.args.get("hub.verify_token")
            challenge = request.args.get("hub.challenge")
            if mode == "subscribe" and token == self.verify_token:
                logger.info("[WA] Webhook verificado")
                return challenge, 200
            return "Forbidden", 403

        @app.post("/webhook")
        def receive():
            """Recibe todos los eventos de WhatsApp."""
            data = request.get_json(silent=True) or {}
            try:
                for entry in data.get("entry", []):
                    for change in entry.get("changes", []):
                        value = change.get("value", {})
                        messages = value.get("messages", [])
                        contacts = value.get("contacts", [])
                        name_map = {c["wa_id"]: c["profile"]["name"] for c in contacts if "wa_id" in c}
                        for msg in messages:
                            wa_id = msg.get("from")
                            name  = name_map.get(wa_id, "Cliente")
                            mtype = msg.get("type")
                            if mtype == "text":
                                text = msg["text"]["body"]
                                threading.Thread(
                                    target=self._process_text,
                                    args=(wa_id, name, text),
                                    daemon=True,
                                ).start()
                            elif mtype == "image":
                                media_id = msg["image"]["id"]
                                caption  = msg["image"].get("caption", "")
                                threading.Thread(
                                    target=self._process_image,
                                    args=(wa_id, name, media_id, caption),
                                    daemon=True,
                                ).start()
                            elif mtype in ("audio", "voice"):
                                media_id = msg[mtype]["id"]
                                threading.Thread(
                                    target=self._process_audio,
                                    args=(wa_id, name, media_id),
                                    daemon=True,
                                ).start()
                            else:
                                logger.info(f"[WA] Tipo de mensaje ignorado: {mtype}")
            except Exception as e:
                logger.error(f"[WA] Error procesando webhook: {e}")
            return jsonify({"status": "ok"}), 200

    # ── Run ────────────────────────────────────────────────────────

    def run(self):
        logger.info(f"[WA] {self.profile.DISPLAY_NAME} escuchando en :{self.port}/webhook")
        self._app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False)
