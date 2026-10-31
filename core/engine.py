"""
Motor de IA — Gemini multi-key + fallback extendido a Gemma + Ollama.

Mejoras respecto a la versión anterior:
  - Múltiples API keys (GEMINI_API_KEY_1, GEMINI_API_KEY_2, ...) con rotación automática
  - Cadena de fallback por sesión: rota modelos antes de escalar a Ollama
  - Separación entre modelos con visión y solo-texto
  - get_vision_chat() para fotos/voz: usa el chat actual si soporta visión,
    o crea uno temporal con un modelo vision-capable
"""

import os
import time
import logging
import requests
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ── Modelos con soporte de visión (imagen + voz + texto) ──────
# Orden = prioridad. Los "pro" siguen instrucciones mucho mejor (menos
# repeticiones, mejor comprensión). Si se agota su cuota, cae a los flash.
# NOTA: en esta key/plan, los modelos "pro" y los flash 3.x responden demasiado
# lento (10-120s) o dan 404 → inservibles para chat en vivo. gemini-2.5-flash es
# el rápido y confiable. La calidad la aporta sobre todo el prompt del perfil.
# (Si algún día hay plan pago, subir aquí a un pro rápido.)
GEMINI_VISION_MODELS = [
    'models/gemini-2.5-flash',
    'models/gemini-flash-latest',
    'models/gemini-2.5-flash-lite',
]

# ── Modelos solo texto (fallback cuando la cuota de visión se agota) ──
GEMINI_TEXT_ONLY_MODELS = [
    'models/gemini-flash-lite-latest',
]

# ── Lista unificada para crear sesiones (visión primero) ──────
GEMINI_MODELS = GEMINI_VISION_MODELS + GEMINI_TEXT_ONLY_MODELS


def _model_supports_vision(model_name: str) -> bool:
    """True si el modelo puede procesar imágenes/audio."""
    no_vision = ['gemma', 'text-bison', 'palm', 'aqa', 'embedding']
    return not any(x in model_name.lower() for x in no_vision)


def _collect_api_keys(single_key: str | None = None) -> list[str]:
    """
    Recolecta todas las API keys disponibles:
      - GEMINI_API_KEY_1, GEMINI_API_KEY_2, ... (hasta 10)
      - GEMINI_API_KEY (legacy, key única)
    Deduplica y filtra placeholders.
    """
    keys = []
    placeholders = {"TU_SEGUNDA_API_KEY_AQUI", "TU_API_KEY_AQUI", ""}
    for i in range(1, 11):
        k = os.getenv(f"GEMINI_API_KEY_{i}")
        if k and k not in placeholders and k not in keys:
            keys.append(k)
    legacy = single_key or os.getenv("GEMINI_API_KEY")
    if legacy and legacy not in placeholders and legacy not in keys:
        keys.append(legacy)
    return keys


class AIEngine:
    """
    Una instancia por sesión de usuario. Mantiene el chat con la IA.

    Lógica de fallback:
      1. Intenta modelo primario con la key activa.
      2. En error 429/cuota → rota al siguiente modelo de la lista.
      3. Si todos los modelos de la key actual fallan → rota a la siguiente API key.
      4. Si todas las keys fallan → escala a Ollama local.
    """

    def __init__(self, system_prompt: str, gemini_api_key: str | None = None,
                 temperature: float = 0.7):
        self.system_prompt      = system_prompt
        self.temperature        = temperature
        self._api_keys          = _collect_api_keys(gemini_api_key)
        self._key_index         = 0
        self._clients: list     = []
        self.engine: str | None = None
        self.chat               = None
        self.current_model: str | None = None
        self._exhausted_models: set    = set()
        self.ollama_model: str | None  = None
        self.ollama_messages: list     = []
        self._init_engine()

    # ── Init ─────────────────────────────────────────────────────
    def _init_engine(self):
        self._clients = []
        for key in self._api_keys:
            try:
                self._clients.append(genai.Client(api_key=key))
            except Exception as e:
                logger.warning(f"No se pudo crear cliente Gemini: {e}")

        if self._clients:
            if self._try_create_chat():
                return

        # Sin Gemini → Ollama
        ollama = self._detect_ollama()
        if ollama:
            self.engine        = 'ollama'
            self.ollama_model  = ollama
            self.ollama_messages = [{"role": "system", "content": self.system_prompt}]
            logger.info(f"AIEngine listo con Ollama: {ollama}")
            return

        logger.error("No hay motor de IA disponible (ni Gemini ni Ollama)")

    def _active_client(self):
        if not self._clients:
            return None
        return self._clients[self._key_index % len(self._clients)]

    def _try_create_chat(self) -> bool:
        """Intenta crear un chat con el primer modelo disponible. True si lo logra."""
        client = self._active_client()
        if not client:
            return False
        for m_name in GEMINI_MODELS:
            if m_name in self._exhausted_models:
                continue
            try:
                self.chat = client.chats.create(
                    model=m_name,
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_prompt,
                        temperature=self.temperature,
                        top_p=0.95,
                        response_mime_type="text/plain"
                    )
                )
                self.engine        = 'gemini'
                self.current_model = m_name
                logger.info(f"AIEngine listo con Gemini: {m_name} (key #{self._key_index})")
                return True
            except Exception as e:
                logger.warning(f"Gemini {m_name} falló al iniciar: {e}")
                self._exhausted_models.add(m_name)
        return False

    # ── Rotación de modelo (por cuota) ────────────────────────────
    def _try_fallback_model(self) -> bool:
        """
        Rota al siguiente modelo disponible.
        Si agota todos los modelos de la key actual → rota a la siguiente key.
        Retorna True si logró crear una nueva sesión.
        """
        if self.current_model:
            self._exhausted_models.add(self.current_model)
            logger.warning(f"Modelo {self.current_model} agotado. Buscando alternativa...")

        client = self._active_client()
        if client:
            for m_name in GEMINI_MODELS:
                if m_name in self._exhausted_models:
                    continue
                try:
                    self.chat = client.chats.create(
                        model=m_name,
                        config=types.GenerateContentConfig(
                            system_instruction=self.system_prompt,
                            temperature=self.temperature,
                            top_p=0.95,
                            response_mime_type="text/plain"
                        )
                    )
                    old = self.current_model
                    self.current_model = m_name
                    logger.info(f"Fallback: {old} → {m_name}")
                    return True
                except Exception as e:
                    logger.warning(f"{m_name} también falló: {e}")
                    self._exhausted_models.add(m_name)

        # Todos los modelos de esta key agotados → rotar key
        if len(self._clients) > 1:
            self._key_index = (self._key_index + 1) % len(self._clients)
            self._exhausted_models.clear()
            logger.warning(f"Rotando a API key #{self._key_index}")
            return self._try_fallback_model()

        logger.error("Todos los modelos Gemini agotados en todas las keys.")
        return False

    # ── Vision chat ───────────────────────────────────────────────
    def _get_vision_chat(self):
        """
        Retorna (chat, es_temporal).
        Si el modelo actual soporta visión → devuelve el chat actual.
        Si no → crea un chat temporal con un modelo vision-capable.
        """
        if self.current_model and _model_supports_vision(self.current_model):
            return self.chat, False

        client = self._active_client()
        if not client:
            return None, False

        for m_name in GEMINI_VISION_MODELS:
            if m_name in self._exhausted_models:
                continue
            try:
                tmp = client.chats.create(
                    model=m_name,
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_prompt,
                        temperature=self.temperature,
                        top_p=0.95,
                        response_mime_type="text/plain"
                    )
                )
                logger.info(f"Chat de visión temporal: {m_name}")
                return tmp, True
            except Exception as e:
                logger.warning(f"Vision {m_name} falló: {e}")
        return None, False

    # ── Ollama detect ─────────────────────────────────────────────
    @staticmethod
    def _detect_ollama() -> str | None:
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=3)
            if r.status_code == 200:
                models = r.json().get("models", [])
                for m in models:
                    if 'llava' not in m["name"].lower():
                        return m["name"]
                if models:
                    return models[0]["name"]
        except Exception:
            pass
        return None

    # ── API pública ───────────────────────────────────────────────
    def is_ready(self) -> bool:
        return self.engine is not None

    def send(self, text: str) -> str:
        if self.engine == 'gemini':
            return self._send_gemini(text)
        if self.engine == 'ollama':
            return self._send_ollama(text)
        raise RuntimeError("Motor de IA no inicializado")

    def send_with_media(self, text: str, media) -> str:
        """Envía texto + imagen/audio. Solo Gemini soporta multimodal."""
        if self.engine != 'gemini':
            return self.send(
                f"{text}\n[SISTEMA: el cliente envió multimedia pero el motor actual no la procesa]"
            )
        vision_chat, is_temp = self._get_vision_chat()
        if not vision_chat:
            return self.send(
                f"{text}\n[SISTEMA: no hay modelo de visión disponible ahora mismo]"
            )
        try:
            resp = vision_chat.send_message([text, media])
            return resp.text
        except Exception as e:
            logger.warning(f"Error procesando multimedia: {e}")
            return self.send(
                f"{text}\n[SISTEMA: no pude procesar el archivo multimedia, responde sin verlo]"
            )

    # ── Gemini send (con retry + rotación) ───────────────────────
    def _send_gemini(self, text: str) -> str:
        MAX_RETRIES = len(GEMINI_MODELS) + 2  # enough for full model rotation
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.chat.send_message(text)
                try:
                    val = resp.text
                    if val is None or not val.strip():
                        raise ValueError("Respuesta vacía o None de Gemini (posible bloqueo de seguridad)")
                    return val
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Error al obtener resp.text en [{self.current_model}]: {e}")
                    if attempt == MAX_RETRIES - 1:
                        return "Disculpa la demora, ¿me confirmas lo último que me dijiste?"
                    time.sleep(2)
                    continue
            except Exception as e:
                err = str(e).lower()
                logger.warning(f"Gemini error [{self.current_model}] intento {attempt+1}: {e}")

                # Siempre intentamos rotar de modelo ante cualquier error persistente de Gemini
                # (ya sea quota, key bloqueada, error de conexión o error interno del modelo)
                if self._try_fallback_model():
                    logger.info(f"Reintentando con {self.current_model}")
                    time.sleep(1)
                    continue

                # Sin modelos Gemini → Ollama
                ollama = self._detect_ollama()
                if ollama:
                    self.engine       = 'ollama'
                    self.ollama_model = ollama
                    self.ollama_messages = [
                        {"role": "system", "content": self.system_prompt}
                    ]
                    logger.info(f"Escalando a Ollama: {ollama}")
                    return self._send_ollama(text)

                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(2 * (attempt + 1))
        return "Estamos con mucha demanda ahorita, dame un momento y vuelve a escribirme."

    # ── Ollama send ───────────────────────────────────────────────
    def _send_ollama(self, text: str) -> str:
        self.ollama_messages.append({"role": "user", "content": text})
        try:
            r = requests.post("http://localhost:11434/api/chat", json={
                "model": self.ollama_model,
                "messages": self.ollama_messages,
                "stream": False
            }, timeout=180)
            if r.status_code == 200:
                out = r.json()["message"]["content"]
                self.ollama_messages.append({"role": "assistant", "content": out})
                return out
            raise Exception(f"Ollama HTTP {r.status_code}")
        except Exception:
            if self.ollama_messages and self.ollama_messages[-1]["role"] == "user":
                self.ollama_messages.pop()
            raise
