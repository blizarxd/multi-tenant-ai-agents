"""
╔══════════════════════════════════════════════════════════════════════╗
║  CAPACIDADES OPCIONALES — core/capabilities.py                       ║
║                                                                      ║
║  Punto de entrada único para todas las capacidades externas.         ║
║  Los perfiles importan desde aquí en lugar de los módulos directos.  ║
║                                                                      ║
║  REGLA DE ORO: las capacidades son silenciosas si no están           ║
║  configuradas. Nunca lanzan excepción al usuario final.              ║
╚══════════════════════════════════════════════════════════════════════╝

═══════════════════════════════════════════════════════════════════════
  MENÚ DE CAPACIDADES DISPONIBLES
═══════════════════════════════════════════════════════════════════════

  CAP_CALENDAR   → Crea / lista eventos en Google Calendar
                   Requiere: config["google_calendar_id"] = "primary"
                   Usa: gws CLI (ya autenticado con your-account@example.com)
                   Cuándo usarlo: negocios con citas (médico, dental, peluquería, gym)

  CAP_SHEETS     → Hoja de vida de cada cliente en Google Sheets
                   Requiere: nada (crea el spreadsheet automáticamente)
                   Usa: gws CLI
                   Cuándo usarlo: cualquier negocio que quiera historial de clientes
                   accesible desde el celular del dueño

  CAP_DRIVE      → Sube archivos (PDF, Excel) a Google Drive
                   Requiere: nada (sube a la carpeta "Reportes" por defecto)
                   Usa: gws CLI
                   Cuándo usarlo: cuando el dueño necesita los reportes en la nube

  CAP_PDF        → Genera reportes PDF diarios (agenda, pedidos, ventas)
                   Requiere: pip install fpdf2 (ya en requirements.txt)
                   Cuándo usarlo: negocios con operaciones diarias que el dueño
                   quiere resumidas (médico, restaurante, ferretería)

  CAP_EXCEL      → Genera historial Excel de clientes/pacientes
                   Requiere: pip install openpyxl (ya en requirements.txt)
                   Cuándo usarlo: negocios que necesitan exportar data a Excel

  CAP_EMAIL      → Envía correos Gmail con adjuntos (PDF/Excel)
                   Requiere: config["gmail_sender"] + config["gmail_app_password"]
                   Cuándo usarlo: cuando el dueño quiere el reporte en su correo

  CAP_REMINDERS  → Recordatorios automáticos por Telegram 30 min antes
                   Requiere: que el evento tenga campo "fecha" con hora
                   Cuándo usarlo: negocios con citas que confirman hora exacta

  CAP_PHOTOS     → Análisis de imágenes via Gemini Vision
                   Requiere: motor Gemini activo (no Ollama)
                   Cuándo usarlo: médico (foto de síntoma), ferretería (foto de pieza),
                   inmobiliaria (foto de propiedad), verificación de pagos

  CAP_INVENTORY  → Control de inventario básico en SQLite
                   Requiere: catalogo.json con campo "stock"
                   Cuándo usarlo: ferretería, farmacia, tienda con stock limitado

  CAP_PAYMENTS   → Verificación de comprobantes de pago via imagen + IA
                   Requiere: Gemini Vision activo
                   Cuándo usarlo: cualquier negocio que recibe pagos y quiere
                   confirmar el comprobante antes de procesar el pedido

  CAP_BROADCAST  → Mensaje masivo a todos los clientes del bot
                   Requiere: profile.bot + profile.memory
                   Cuándo usarlo: promociones, avisos de stock, cambios de horario
                   Comando: make_cmd_broadcast()

  CAP_QUOTE_PDF  → Cotización PDF generada y enviada directo al cliente en Telegram
                   Requiere: fpdf2 + profile.bot
                   Cuándo usarlo: repuestos, ferretería, servicios donde el cliente
                   pide precio antes de decidir
                   Payload: {cliente, items:[{nombre,cantidad,precio_usd,subtotal}], total_usd}

  CAP_LOW_STOCK_ALERT → Alerta automática al dueño cuando un producto baja del mínimo
                   Requiere: profile.bot + profile.admin_id
                   Cuándo usarlo: cualquier negocio con inventario — repuestos, farmacia,
                   ferretería, tienda
                   Llamar en on_event() después de reducir stock

  CAP_WEEKLY_REPORT → PDF consolidado semanal o mensual de ventas + sin_stock
                   Requiere: fpdf2 + profile.memory
                   Cuándo usarlo: negocios que quieren resumen periódico al dueño
                   Comandos: make_cmd_reporte_semanal(), make_cmd_reporte_mensual()

═══════════════════════════════════════════════════════════════════════
  CÓMO USAR EN TU profile.py
═══════════════════════════════════════════════════════════════════════

  # 1. Importa solo lo que tu agente necesita
  from core.capabilities import (
      cap_calendar,         # si tienes citas con hora
      cap_reminder,         # si quieres recordatorio 30 min antes
      cap_sheets,           # si quieres hoja de vida en Google Sheets
      cap_pdf,              # si quieres reporte PDF diario
      cap_excel,            # si quieres exportar a Excel
      cap_drive,            # si quieres subir reportes a Drive
      cap_email,            # si quieres enviar reporte por correo
      cap_photo_prompt,     # si quieres análisis de fotos especializado
      cap_inventory_check,  # si manejas stock
      cap_inventory_reduce, # reduce stock al confirmar venta
      cap_payment_prompt,   # si verificas comprobantes de pago
      cap_broadcast,        # mensaje masivo a todos los clientes
      cap_quote_pdf,        # cotización PDF enviada al cliente
      cap_low_stock_alert,  # alerta al dueño cuando stock baja del mínimo
      cap_weekly_report,    # PDF consolidado semanal/mensual
      cap_verify_payment,   # verifica pago por referencia en Gmail o Binance API
  )

  # 2. En on_event(), llama a las capacidades que tu negocio usa:
  def on_event(self, event_type, payload, telegram_id):
      self.memory.save_event(telegram_id, event_type, payload)
      if event_type == "cita":
          cap_calendar(self, payload)       # → crea evento en Google Calendar
          cap_reminder(self, telegram_id, payload)  # → recordatorio 30 min antes
          cap_sheets(self, telegram_id, payload, event_type)  # → actualiza Sheets

  # 3. En admin_commands(), agrega los comandos que correspondan:
  #    Los comandos de reporte, historial, subida a Drive, etc. se arman
  #    combinando las funciones de capabilities según el perfil.

═══════════════════════════════════════════════════════════════════════
"""

import re
import threading
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Serializa el read-modify-write del catálogo: sin esto, dos pedidos
# simultáneos leen el mismo stock y uno de los descuentos se pierde.
_INVENTORY_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════════════
#  CAP_CALENDAR — Google Calendar
# ══════════════════════════════════════════════════════════════════════

def cap_calendar(profile, payload: dict) -> bool:
    """
    Crea un evento en Google Calendar con los datos de la cita.
    Silencioso si 'google_calendar_id' no está en config.
    Retorna True si se creó el evento.
    """
    calendar_id = profile.config.get("google_calendar_id", "")
    if not calendar_id:
        return False
    try:
        from core.google_calendar import create_appointment
        create_appointment(
            calendar_id=calendar_id,
            summary=f"Consulta: {payload.get('paciente', payload.get('cliente', 'Cliente'))}",
            date_str=payload.get("fecha", ""),
            duration_min=int(profile.config.get("duracion_consulta_min",
                             profile.config.get("duracion_cita_min", 30))),
            description=(
                f"Motivo: {payload.get('motivo', payload.get('servicio', ''))}\n"
                f"Tel: {payload.get('telefono', '')}\n"
                f"Notas: {payload.get('notas', '')}"
            ),
        )
        logger.info(f"[{profile.tenant_id}] Evento creado en Calendar")
        return True
    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_calendar falló: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  CAP_REMINDER — Recordatorio automático por Telegram
# ══════════════════════════════════════════════════════════════════════

def cap_reminder(profile, telegram_id: int, payload: dict,
                 minutes_before: int = 30) -> bool:
    """
    Programa un recordatorio N minutos antes de la cita.
    Requiere: campo 'fecha' en payload con hora parseable.
    El hilo de recordatorios en TelegramAgent lo envía automáticamente.
    """
    if not profile.memory:
        return False
    try:
        from core.google_calendar import _parse_date
        fecha_str = payload.get("fecha", "")
        if not fecha_str:
            return False
        cita_dt = _parse_date(fecha_str)
        if not cita_dt:
            return False
        remind_at = cita_dt - timedelta(minutes=minutes_before)
        if remind_at <= datetime.now():
            return False

        nombre   = payload.get("paciente", payload.get("cliente", "paciente"))
        clinica  = profile.config.get("nombre_clinica",
                   profile.config.get("nombre_negocio", profile.DISPLAY_NAME))
        servicio = payload.get("servicio", "tu cita")
        mensaje  = (
            f"Hola {nombre}, te recuerdo que tienes {servicio} en {clinica} "
            f"en {minutes_before} minutos ({cita_dt.strftime('%I:%M %p')}).\n"
            f"Si necesitas cancelar o reagendar, escríbeme aquí."
        )
        profile.memory.add_reminder(telegram_id, remind_at, mensaje)
        logger.info(f"[{profile.tenant_id}] Recordatorio programado para {nombre} a las {remind_at.strftime('%H:%M')}")
        return True
    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_reminder falló: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  CAP_SHEETS — Google Sheets (hoja de vida de clientes)
# ══════════════════════════════════════════════════════════════════════

def cap_sheets(profile, telegram_id: int, payload: dict, event_type: str) -> bool:
    """
    Sincroniza el evento con Google Sheets en un hilo aparte.
    Crea el spreadsheet automáticamente en el primer uso.
    Actualiza la pestaña individual del cliente.
    """
    import threading
    def _sync():
        try:
            from core.google_sheets import (
                get_or_create_spreadsheet, append_cita, append_foto,
                sync_patient_tab
            )
            clinica = profile.config.get("nombre_clinica",
                      profile.config.get("nombre_negocio", profile.DISPLAY_NAME))
            sid = get_or_create_spreadsheet(profile.tenant_id, f"Clientes — {clinica}")
            if event_type in ("cita", "pedido", "reserva"):
                append_cita(sid, telegram_id, payload)
            elif event_type == "foto_medica":
                append_foto(sid, telegram_id, payload)
            if profile.memory:
                client   = profile.memory.get_client(telegram_id) or {}
                nombre   = client.get("nombre") or payload.get("paciente") or payload.get("cliente") or f"ID_{telegram_id}"
                telefono = client.get("telefono") or payload.get("telefono") or ""
                events   = profile.memory.get_events_for_patient(telegram_id)
                sync_patient_tab(sid, nombre, telegram_id, telefono, events)
        except Exception as e:
            logger.warning(f"[{profile.tenant_id}] cap_sheets falló: {e}")

    threading.Thread(target=_sync, daemon=True, name="sheets-sync").start()
    return True


# ══════════════════════════════════════════════════════════════════════
#  CAP_PDF — Reporte PDF diario
# ══════════════════════════════════════════════════════════════════════

def cap_pdf(profile) -> bytes | None:
    """
    Genera el PDF del reporte diario y lo retorna como bytes.
    Requiere: fpdf2 instalado.
    """
    try:
        from core.pdf_report import generate_daily_report
        return generate_daily_report(
            tenant_id=profile.tenant_id,
            doctor=profile.config.get("doctor_nombre",
                   profile.config.get("dueno_nombre", "Responsable")),
            clinica=profile.config.get("nombre_clinica",
                    profile.config.get("nombre_negocio", profile.DISPLAY_NAME)),
        )
    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_pdf falló: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  CAP_EXCEL — Historial Excel de clientes
# ══════════════════════════════════════════════════════════════════════

def cap_excel(profile) -> bytes | None:
    """
    Genera un Excel con el historial completo de clientes.
    Requiere: openpyxl instalado + profile.memory activo.
    """
    if not profile.memory:
        return None
    try:
        from core.excel_report import generate_patient_excel
        return generate_patient_excel(
            tenant_id=profile.tenant_id,
            clinica=profile.config.get("nombre_clinica",
                    profile.config.get("nombre_negocio", profile.DISPLAY_NAME)),
            doctor=profile.config.get("doctor_nombre",
                   profile.config.get("dueno_nombre", "Responsable")),
            memory=profile.memory,
        )
    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_excel falló: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  CAP_DRIVE — Subir archivo a Google Drive
# ══════════════════════════════════════════════════════════════════════

def cap_drive(file_bytes: bytes, filename: str,
              folder_name: str = "Reportes") -> str:
    """
    Sube bytes a Google Drive.
    Retorna URL del archivo o "" si falla.
    """
    try:
        from core.google_drive import upload_file
        return upload_file(file_bytes, filename, folder_name)
    except Exception as e:
        logger.warning(f"cap_drive falló: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════
#  CAP_EMAIL — Enviar reporte por correo
# ══════════════════════════════════════════════════════════════════════

def cap_email(profile, pdf_bytes: bytes, subject: str = "") -> bool:
    """
    Envía el PDF por correo Gmail.
    Requiere en config: gmail_sender, gmail_app_password, email_doctor (o email_dueno).
    """
    to_email   = profile.config.get("email_doctor",
                 profile.config.get("email_dueno",
                 profile.config.get("email", "")))
    sender     = profile.config.get("gmail_sender", "")
    password   = profile.config.get("gmail_app_password", "")
    if not to_email or not sender or not password:
        logger.info(f"[{profile.tenant_id}] cap_email omitido — falta configuración de Gmail")
        return False
    try:
        from core.email_sender import send_pdf_report
        clinica = profile.config.get("nombre_clinica",
                  profile.config.get("nombre_negocio", profile.DISPLAY_NAME))
        send_pdf_report(
            to_email=to_email,
            subject=subject or f"Reporte diario — {datetime.now().strftime('%d/%m/%Y')} — {clinica}",
            pdf_bytes=pdf_bytes,
            from_email=sender,
            app_password=password,
        )
        logger.info(f"[{profile.tenant_id}] Reporte enviado a {to_email}")
        return True
    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_email falló: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  CAP_PHOTO_PROMPT — Prompt especializado para análisis de fotos
# ══════════════════════════════════════════════════════════════════════

def cap_photo_prompt(context: str = "", negocio: str = "") -> str:
    """
    Retorna un prompt para que Gemini Vision analice una foto.
    context: descripción del tipo de negocio / qué buscar en la foto.
    Úsalo sobrescribiendo photo_prompt() en tu profile.
    """
    if context:
        return context
    return (
        f"El cliente de {negocio} te envió una imagen. Analízala y responde según el contexto "
        "de la conversación. Si es un comprobante de pago: verifica el monto y el destinatario. "
        "Si es una foto de producto/propiedad/pieza: descríbela objetivamente. "
        "Si es otro tipo de imagen: responde con sentido común según tu rol."
    )


# ══════════════════════════════════════════════════════════════════════
#  CAP_INVENTORY — Control básico de stock
# ══════════════════════════════════════════════════════════════════════

def cap_inventory_check(profile, item_name: str, quantity: int = 1) -> dict:
    """
    Verifica disponibilidad de un item en el catálogo.
    Retorna {"available": bool, "stock": int, "item": dict | None}
    Requiere campo "stock" en los items del catalogo.json.
    """
    catalogo = profile.catalogo
    items = catalogo.get("productos", catalogo.get("items", catalogo.get("servicios", [])))
    for item in items:
        nombre = item.get("nombre", "").lower()
        if item_name.lower() in nombre or nombre in item_name.lower():
            stock = item.get("stock", None)
            if stock is None:
                return {"available": True, "stock": -1, "item": item}
            return {
                "available": stock >= quantity,
                "stock": stock,
                "item": item
            }
    return {"available": False, "stock": 0, "item": None}


def cap_inventory_reduce(profile, item_name: str, quantity: int = 1) -> bool:
    """
    Reduce el stock de un item en el catálogo (modifica catalogo.json del tenant)
    de forma atómica y actualiza también el stock en memoria (profile.catalogo),
    para que el siguiente cap_inventory_check vea el valor correcto.
    Retorna True si se redujo correctamente.
    """
    import json, os
    catalog_path = os.path.join(profile.tenant_dir or profile.profile_dir, "catalogo.json")
    if not os.path.exists(catalog_path):
        catalog_path = os.path.join(profile.profile_dir, "catalogo.json")
    if not os.path.exists(catalog_path):
        return False

    with _INVENTORY_LOCK:
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                catalogo = json.load(f)
            key = next((k for k in ["productos", "items", "servicios"] if k in catalogo), None)
            if not key:
                return False
            for item in catalogo[key]:
                nombre = item.get("nombre", "").lower()
                if item_name.lower() in nombre or nombre in item_name.lower():
                    if "stock" in item and item["stock"] >= quantity:
                        item["stock"] -= quantity
                        # Escritura atómica: archivo temporal + replace, para no
                        # dejar el catálogo a medio escribir si el proceso muere.
                        tmp = catalog_path + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump(catalogo, f, ensure_ascii=False, indent=2)
                        os.replace(tmp, catalog_path)
                        # Reflejar el cambio en el catálogo cargado en memoria.
                        _sync_in_memory_stock(profile, key, item.get("nombre", ""), item["stock"])
                        return True
            return False
        except Exception as e:
            logger.warning(f"cap_inventory_reduce falló: {e}")
            return False


def _sync_in_memory_stock(profile, key: str, nombre: str, nuevo_stock: int):
    """Actualiza el stock de un item en profile.catalogo (en RAM)."""
    try:
        items = profile.catalogo.get(key, [])
        for it in items:
            if it.get("nombre", "") == nombre:
                it["stock"] = nuevo_stock
                return
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
#  CAP_PAYMENT — Verificación de comprobante de pago via imagen
# ══════════════════════════════════════════════════════════════════════

def cap_payment_prompt(profile) -> str:
    """
    Prompt para verificar comprobantes de pago por imagen.
    Úsalo sobrescribiendo photo_prompt() en tu profile cuando el negocio
    necesite confirmar pagos antes de procesar el pedido.
    """
    metodos = ", ".join(profile.config.get("metodos_pago", ["Pago Móvil", "Zelle", "USD"]))
    negocio = profile.config.get("nombre_negocio",
              profile.config.get("nombre_clinica", profile.DISPLAY_NAME))
    return (
        f"El cliente de {negocio} envió un comprobante de pago. Analiza la imagen:\n"
        f"1. ¿Es un comprobante de {metodos}?\n"
        f"2. ¿Se ve claramente el monto pagado?\n"
        f"3. ¿El destinatario parece correcto (nombre o número del negocio)?\n"
        f"4. ¿La fecha es de hoy o reciente?\n\n"
        f"Si todo parece válido: confirma el pago y continúa con el pedido.\n"
        f"Si hay dudas: pide que reenvíe la imagen más clara.\n"
        f"NUNCA confirmes un pago que no puedas verificar visualmente.\n\n"
        f"Emite el bloque:\n"
        f"[PAGO_VERIFICADO]\n"
        f"Monto: [monto visible]\n"
        f"Metodo: [tipo de pago]\n"
        f"Valido: [SI | DUDOSO | NO]\n"
        f"[/PAGO_VERIFICADO]"
    )


# ══════════════════════════════════════════════════════════════════════
#  COMANDOS ADMIN REUTILIZABLES
#  Funciones listas para usar en admin_commands() de cualquier perfil
# ══════════════════════════════════════════════════════════════════════

def make_cmd_reporte_pdf(event_labels: dict | None = None):
    """
    Genera el comando /reporte_pdf para cualquier perfil.
    event_labels: {"cita": "Citas", "pedido": "Pedidos", ...}
    """
    def cmd(message, bot, profile, memory):
        pdf = cap_pdf(profile)
        if pdf:
            filename = f"reporte_{datetime.now().strftime('%Y%m%d')}.pdf"
            bot.send_document(
                message.chat.id, (filename, pdf),
                caption=f"Reporte del dia — {datetime.now().strftime('%d/%m/%Y')}"
            )
        else:
            bot.reply_to(message, "No hay datos para generar el reporte hoy.")
    return cmd


def make_cmd_historia_excel():
    """Genera el comando /historia_excel para cualquier perfil."""
    def cmd(message, bot, profile, memory):
        xl = cap_excel(profile)
        if xl:
            filename = f"historial_{datetime.now().strftime('%Y%m%d')}.xlsx"
            bot.send_document(
                message.chat.id, (filename, xl),
                caption=f"Historial de clientes — {datetime.now().strftime('%d/%m/%Y')}"
            )
        else:
            bot.reply_to(message, "No hay datos suficientes para el Excel.")
    return cmd


def make_cmd_subir_reporte(folder_name: str = "Reportes"):
    """Genera el comando /subir_reporte para cualquier perfil."""
    def cmd(message, bot, profile, memory):
        bot.reply_to(message, "Generando y subiendo a Google Drive...")
        pdf = cap_pdf(profile)
        if not pdf:
            bot.reply_to(message, "Sin datos para hoy.")
            return
        filename = f"reporte_{datetime.now().strftime('%Y-%m-%d')}.pdf"
        url = cap_drive(pdf, filename, folder_name)
        if url:
            bot.reply_to(message, f"Reporte subido a Drive:\n{url}")
        else:
            from core.google_drive import save_report_locally
            path = save_report_locally(pdf, filename)
            bot.reply_to(message, f"No se pudo subir a Drive. Guardado localmente:\n{path}")
    return cmd


def make_cmd_crear_hoja():
    """Genera el comando /crear_hoja para cualquier perfil."""
    def cmd(message, bot, profile, memory):
        bot.reply_to(message, "Creando/sincronizando hoja en Google Sheets...")
        try:
            from core.google_sheets import (
                get_or_create_spreadsheet, sync_patient_tab, get_spreadsheet_url
            )
            clinica = profile.config.get("nombre_clinica",
                      profile.config.get("nombre_negocio", profile.DISPLAY_NAME))
            sid = get_or_create_spreadsheet(profile.tenant_id, f"Clientes — {clinica}")
            patients = memory.get_all_patients()
            synced = 0
            for pat in patients:
                tid    = pat["telegram_id"]
                events = memory.get_events_for_patient(tid)
                if events:
                    sync_patient_tab(
                        sid,
                        pat.get("nombre") or f"ID_{tid}",
                        tid,
                        pat.get("telefono") or "",
                        events
                    )
                    synced += 1
            url = get_spreadsheet_url(sid)
            bot.reply_to(message,
                f"Hoja sincronizada — {synced} clientes.\n{url}")
        except Exception as e:
            bot.reply_to(message, f"Error: {e}")
    return cmd


def make_cmd_enviar_reporte():
    """Genera el comando /enviar_reporte por email para cualquier perfil."""
    def cmd(message, bot, profile, memory):
        pdf = cap_pdf(profile)
        if not pdf:
            bot.reply_to(message, "Sin datos para el reporte de hoy.")
            return
        ok = cap_email(profile, pdf)
        to = profile.config.get("email_doctor",
             profile.config.get("email_dueno",
             profile.config.get("email", "—")))
        if ok:
            bot.reply_to(message, f"Reporte enviado a {to}")
        else:
            bot.reply_to(message,
                "Falta configurar: gmail_sender, gmail_app_password y email en config.json del tenant.")
    return cmd


# ══════════════════════════════════════════════════════════════════════
#  CAP_BROADCAST — Envío masivo a todos los clientes
# ══════════════════════════════════════════════════════════════════════

def cap_broadcast(profile, message_text: str) -> dict:
    """
    Envía un mensaje a todos los clientes que alguna vez escribieron al bot.
    Requiere: profile.bot y profile.memory activos.
    Retorna {"sent": int, "failed": int}.
    """
    bot    = getattr(profile, "bot", None)
    memory = getattr(profile, "memory", None)
    if not bot or not memory:
        logger.warning(f"[{profile.tenant_id}] cap_broadcast: falta bot o memory")
        return {"sent": 0, "failed": 0}

    sent = failed = 0
    try:
        clients = memory.get_all_clients()
        for client in clients:
            telegram_id = client.get("telegram_id")
            if not telegram_id:
                continue
            try:
                bot.send_message(telegram_id, message_text)
                sent += 1
            except Exception as e:
                logger.warning(f"[{profile.tenant_id}] broadcast fallo para {telegram_id}: {e}")
                failed += 1
    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_broadcast error general: {e}")
    logger.info(f"[{profile.tenant_id}] Broadcast: {sent} enviados, {failed} fallidos")
    return {"sent": sent, "failed": failed}


def make_cmd_broadcast():
    """
    Genera el comando /broadcast para cualquier perfil.
    Uso: /broadcast Tu texto del mensaje aquí
    """
    def cmd(message, bot, profile, memory):
        text = message.text.partition(" ")[2].strip()
        if not text:
            bot.reply_to(message,
                "Uso: /broadcast <mensaje>\nEjemplo: /broadcast ¡Oferta de hoy: 10% en aceites!")
            return
        negocio = profile.config.get("nombre_negocio", profile.DISPLAY_NAME)
        msg = f"📢 *{negocio}*\n\n{text}"
        bot.reply_to(message, f"Enviando a todos los clientes...")
        result = cap_broadcast(profile, msg)
        bot.reply_to(message,
            f"Broadcast completado:\n• Enviados: {result['sent']}\n• Fallidos: {result['failed']}")
    return cmd


# ══════════════════════════════════════════════════════════════════════
#  CAP_QUOTE_PDF — Cotización en PDF enviada directo al cliente
# ══════════════════════════════════════════════════════════════════════

def cap_quote_pdf(profile, telegram_id: int, payload: dict) -> bytes | None:
    """
    Genera un PDF de cotización y lo envía al cliente por Telegram.
    payload debe tener: cliente, items (list), total_usd, notas (opcional).
    Requiere: profile.bot activo + fpdf2.
    """
    bot = getattr(profile, "bot", None)
    try:
        from fpdf import FPDF

        def _s(t: str) -> str:
            return t.encode("latin-1", errors="replace").decode("latin-1")

        negocio   = profile.config.get("nombre_negocio", profile.DISPLAY_NAME)
        ubicacion = profile.config.get("ubicacion", "")
        cliente   = payload.get("cliente", payload.get("nombre", "Cliente"))
        items     = payload.get("items", [])
        total     = payload.get("total_usd", 0)
        notas     = payload.get("notas", "")
        fecha     = datetime.now().strftime("%d/%m/%Y")
        num       = f"COT-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, _s(negocio), ln=True, align="C")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, _s(ubicacion), ln=True, align="C")
        pdf.ln(6)

        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, f"COTIZACION {num}", ln=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 6, f"Fecha: {fecha}", ln=True)
        pdf.cell(0, 6, _s(f"Cliente: {cliente}"), ln=True)
        pdf.ln(4)

        pdf.set_fill_color(30, 60, 120)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(90, 8, "Descripcion", border=1, fill=True)
        pdf.cell(30, 8, "Cant.", border=1, fill=True, align="C")
        pdf.cell(35, 8, "P. Unit (USD)", border=1, fill=True, align="R")
        pdf.cell(35, 8, "Subtotal", border=1, fill=True, align="R", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)

        for item in items:
            nombre   = _s(str(item.get("nombre", "")))
            cantidad = str(item.get("cantidad", 1))
            precio   = float(item.get("precio_usd", 0))
            subtotal = float(item.get("subtotal", precio * int(cantidad)))
            pdf.cell(90, 7, nombre, border=1)
            pdf.cell(30, 7, cantidad, border=1, align="C")
            pdf.cell(35, 7, f"${precio:.2f}", border=1, align="R")
            pdf.cell(35, 7, f"${subtotal:.2f}", border=1, align="R", ln=True)

        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(155, 8, "TOTAL USD:", align="R")
        pdf.cell(35, 8, f"${float(total):.2f}", border=1, align="R", ln=True)

        if notas:
            pdf.ln(4)
            pdf.set_font("Helvetica", "I", 9)
            pdf.multi_cell(0, 5, _s(f"Notas: {notas}"))

        pdf.ln(8)
        pdf.set_font("Helvetica", "", 9)
        metodos = ", ".join(profile.config.get("metodos_pago", ["Efectivo USD"]))
        pdf.cell(0, 5, _s(f"Metodos de pago: {metodos}"), ln=True)
        pdf.cell(0, 5, _s(f"Garantia: {profile.config.get('garantia_dias', 30)} dias por defecto de fabricacion"), ln=True)

        pdf_bytes = bytes(pdf.output())

        if bot and telegram_id:
            import io
            filename = f"cotizacion_{num}.pdf"
            bot.send_document(
                telegram_id,
                (filename, io.BytesIO(pdf_bytes)),
                caption=_s(f"Cotizacion de {negocio} — {fecha}")
            )
            logger.info(f"[{profile.tenant_id}] Cotizacion enviada a {telegram_id}")

        return pdf_bytes

    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_quote_pdf falló: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  CAP_LOW_STOCK_ALERT — Alerta automática de stock bajo al dueño
# ══════════════════════════════════════════════════════════════════════

def cap_low_stock_alert(profile, producto: dict) -> bool:
    """
    Notifica al dueño por Telegram cuando un producto llega al stock mínimo.
    Requiere: profile.bot, profile.admin_id, y el producto debe tener
              "stock" y "stock_minimo" en el catalogo.json.
    """
    bot      = getattr(profile, "bot", None)
    admin_id = getattr(profile, "admin_id", None)
    if not bot or not admin_id:
        return False

    stock     = producto.get("stock", 0)
    minimo    = producto.get("stock_minimo", 0)
    nombre    = producto.get("nombre", "Producto desconocido")
    referencia = producto.get("referencia", "")
    precio    = producto.get("precio_usd", 0)

    if stock > minimo:
        return False

    nivel = "AGOTADO" if stock == 0 else "STOCK BAJO"
    emoji = "🔴" if stock == 0 else "🟡"
    try:
        negocio = profile.config.get("nombre_negocio", profile.DISPLAY_NAME)
        msg = (
            f"{emoji} *{nivel} — {negocio}*\n\n"
            f"Producto: {nombre}\n"
            f"Referencia: {referencia}\n"
            f"Stock actual: {stock} unidades\n"
            f"Stock mínimo: {minimo} unidades\n"
            f"Precio: ${precio:.2f} USD\n\n"
            f"_Reponer antes de quedarse sin inventario._"
        )
        bot.send_message(admin_id, msg, parse_mode="Markdown")
        logger.info(f"[{profile.tenant_id}] Alerta stock bajo enviada: {nombre} ({stock} restantes)")
        return True
    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_low_stock_alert falló: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  CAP_WEEKLY_REPORT — Reporte consolidado semanal/mensual
# ══════════════════════════════════════════════════════════════════════

def cap_weekly_report(profile, period: str = "semana") -> bytes | None:
    """
    Genera un PDF consolidado con el resumen del período (semana o mes).
    period: "semana" (últimos 7 días) o "mes" (últimos 30 días).
    Requiere: profile.memory + fpdf2.
    """
    if not getattr(profile, "memory", None):
        return None
    try:
        from fpdf import FPDF

        days  = 7 if period == "semana" else 30
        label = "Semanal" if period == "semana" else "Mensual"

        from datetime import timedelta
        desde = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        memory   = profile.memory
        events   = memory.get_all_events_since(desde) if hasattr(memory, "get_all_events_since") else []
        clientes = memory.get_all_clients() if hasattr(memory, "get_all_clients") else []
        negocio  = profile.config.get("nombre_negocio", profile.DISPLAY_NAME)
        dueno    = profile.config.get("dueno_nombre", profile.config.get("doctor_nombre", "Responsable"))

        ventas        = [e for e in events if e.get("event_type") in ("venta", "pedido")]
        citas         = [e for e in events if e.get("event_type") in ("cita", "reserva")]
        sin_stock     = [e for e in events if e.get("event_type") == "sin_stock"]
        total_ventas  = sum(float(e.get("payload", {}).get("total_usd", 0)) for e in ventas)

        def _s(t: str) -> str:
            return str(t).encode("latin-1", errors="replace").decode("latin-1")

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, _s(f"Reporte {label} — {negocio}"), ln=True, align="C")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')} | Periodo: ultimos {days} dias", ln=True, align="C")
        pdf.ln(6)

        pdf.set_font("Helvetica", "B", 13)
        pdf.set_fill_color(30, 60, 120)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 8, "  RESUMEN EJECUTIVO", ln=True, fill=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 11)
        pdf.ln(2)

        rows = [
            ("Clientes atendidos (total)", str(len(clientes))),
            (f"Ventas/pedidos ({label.lower()})", str(len(ventas))),
            (f"Total facturado (USD)", f"${total_ventas:.2f}"),
            (f"Citas agendadas ({label.lower()})", str(len(citas))),
            (f"Piezas sin stock solicitadas", str(len(sin_stock))),
        ]
        for label_r, valor in rows:
            pdf.cell(120, 7, _s(label_r))
            pdf.cell(0, 7, valor, ln=True)

        if ventas:
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_fill_color(200, 220, 255)
            pdf.cell(0, 7, f"  VENTAS DEL PERIODO ({len(ventas)})", ln=True, fill=True)
            pdf.set_font("Helvetica", "", 10)
            pdf.ln(1)
            for v in ventas[-20:]:
                p = v.get("payload", {})
                cli  = _s(p.get("cliente", p.get("nombre", "—")))
                det  = _s(p.get("detalle", p.get("pedido", "—")))[:60]
                mto  = p.get("total_usd", "—")
                fecha = v.get("created_at", "")[:10]
                pdf.cell(0, 5, f"{fecha} | {cli} | {det} | USD {mto}", ln=True)

        if sin_stock:
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_fill_color(255, 220, 180)
            pdf.cell(0, 7, f"  PIEZAS SOLICITADAS SIN STOCK ({len(sin_stock)})", ln=True, fill=True)
            pdf.set_font("Helvetica", "", 10)
            pdf.ln(1)
            for s in sin_stock:
                p    = s.get("payload", {})
                parte = _s(p.get("parte", p.get("consulta", "—")))[:80]
                fecha = s.get("created_at", "")[:10]
                pdf.cell(0, 5, f"{fecha} | {parte}", ln=True)

        pdf.ln(8)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 4, _s(f"Reporte generado automaticamente por el asistente IA de {negocio}. Destinatario: {dueno}."), ln=True)

        return bytes(pdf.output())

    except Exception as e:
        logger.warning(f"[{profile.tenant_id}] cap_weekly_report falló: {e}")
        return None


def make_cmd_reporte_semanal():
    """Genera el comando /reporte_semanal para cualquier perfil."""
    def cmd(message, bot, profile, memory):
        bot.reply_to(message, "Generando reporte semanal...")
        pdf = cap_weekly_report(profile, period="semana")
        if pdf:
            filename = f"reporte_semanal_{datetime.now().strftime('%Y%m%d')}.pdf"
            bot.send_document(
                message.chat.id, (filename, pdf),
                caption=f"Reporte semanal — {datetime.now().strftime('%d/%m/%Y')}"
            )
        else:
            bot.reply_to(message, "Sin datos suficientes para el reporte semanal.")
    return cmd


def make_cmd_reporte_mensual():
    """Genera el comando /reporte_mensual para cualquier perfil."""
    def cmd(message, bot, profile, memory):
        bot.reply_to(message, "Generando reporte mensual...")
        pdf = cap_weekly_report(profile, period="mes")
        if pdf:
            filename = f"reporte_mensual_{datetime.now().strftime('%Y%m%d')}.pdf"
            bot.send_document(
                message.chat.id, (filename, pdf),
                caption=f"Reporte mensual — {datetime.now().strftime('%d/%m/%Y')}"
            )
        else:
            bot.reply_to(message, "Sin datos suficientes para el reporte mensual.")
    return cmd


# ══════════════════════════════════════════════════════════════════════
#  CAP_VERIFY_PAYMENT — Verificación de pago por referencia bancaria
#
#  Flujo completo:
#    1. IA emite [PAGO_PARA_VERIFICAR] con referencia + monto
#    2. on_event("verificar_pago", payload, telegram_id) llama esta función
#    3. Verifica en Gmail (IMAP) o Binance API según el método declarado
#    4. Envía resultado proactivo al cliente por Telegram
#
#  Config necesaria en tenant/config.json:
#    "gmail_verificacion_email":    correo donde llegan alertas del banco
#    "gmail_verificacion_password": App Password de Gmail (no la contraseña)
#    "binance_api_key":             (opcional) para pagos Binance P2P
#    "binance_api_secret":          (opcional)
# ══════════════════════════════════════════════════════════════════════

def cap_verify_payment(profile, telegram_id: int, payload: dict) -> None:
    """
    Verifica un pago por referencia bancaria en Gmail o Binance API.
    Corre en hilo daemon — no bloquea la conversación.
    Envía resultado al cliente vía profile.bot.

    payload esperado:
        referencia: str       número de referencia del pago
        monto_declarado: str  monto que dice el cliente haber pagado
        metodo: str           "Pago Móvil", "Binance", "Zelle", etc.
    """
    import threading

    def _verify():
        bot      = getattr(profile, "bot", None)
        if not bot:
            logger.warning(f"[{profile.tenant_id}] cap_verify_payment: no hay bot")
            return

        referencia  = str(payload.get("referencia", "")).strip()
        monto_str   = str(payload.get("monto_declarado", "0")).replace(",", ".")
        metodo      = payload.get("metodo", "").lower()

        try:
            monto = float(re.sub(r"[^0-9.]", "", monto_str))
        except Exception:
            monto = 0.0

        negocio = profile.config.get("nombre_negocio", profile.DISPLAY_NAME)

        # ── Binance P2P ───────────────────────────────────────────
        if "binance" in metodo or "usdt" in metodo or "crypto" in metodo:
            bkey    = profile.config.get("binance_api_key", "")
            bsecret = profile.config.get("binance_api_secret", "")
            if bkey and bsecret:
                from core.payment_verifier import verify_binance_order
                result = verify_binance_order(bkey, bsecret, referencia)
                if result["found"] and result["completed"]:
                    msg = (
                        f"✅ *Pago Binance verificado*\n"
                        f"Orden: `{referencia}`\n"
                        f"Monto: {result['amount']} {result['asset']}\n"
                        f"Estado: COMPLETADO\n\n"
                        f"Tu pedido está confirmado en {negocio}. ¡Gracias!"
                    )
                elif result["found"]:
                    msg = (
                        f"⏳ Orden Binance `{referencia}` encontrada pero el estado es *{result['status']}*.\n"
                        f"Cuando el pago se complete te confirmamos."
                    )
                else:
                    msg = (
                        f"❌ No encontramos la orden Binance `{referencia}`.\n"
                        f"Envíame el comprobante de la transferencia como imagen."
                    )
                try:
                    bot.send_message(telegram_id, msg, parse_mode="Markdown")
                except Exception:
                    bot.send_message(telegram_id, msg)
                return

        # ── Bancos nacionales via Gmail ───────────────────────────
        gmail_email  = profile.config.get("gmail_verificacion_email", "")
        gmail_pass   = profile.config.get("gmail_verificacion_password", "")

        if not gmail_email or not gmail_pass:
            logger.info(f"[{profile.tenant_id}] cap_verify_payment: sin Gmail configurado — pide comprobante")
            try:
                bot.send_message(
                    telegram_id,
                    "Por favor envíame el comprobante del pago como imagen para verificarlo."
                )
            except Exception:
                pass
            return

        from core.payment_verifier import search_payment_by_reference
        result = search_payment_by_reference(
            email_addr=gmail_email,
            app_password=gmail_pass,
            reference=referencia,
            declared_amount=monto,
            hours_back=48,
        )

        if result["found"]:
            monto_ok = result.get("amount_match", False)
            if monto_ok or monto <= 0:
                msg = (
                    f"✅ *Pago verificado automáticamente*\n"
                    f"Referencia: `{referencia}`\n"
                    f"Monto detectado: {result['amount']}\n"
                    f"Banco: {result['bank'][:40]}\n\n"
                    f"Tu pedido está confirmado en {negocio}. ¡Gracias!"
                )
            else:
                msg = (
                    f"⚠️ Encontré un pago con referencia `{referencia}` "
                    f"pero el monto detectado ({result['amount']}) "
                    f"no coincide con lo indicado ({monto_str}).\n\n"
                    f"Envíame el comprobante como imagen para que lo revisemos."
                )
        else:
            msg = (
                f"❌ No encontré ningún pago con referencia `{referencia}` "
                f"en los últimos correos del banco.\n\n"
                f"Puede que el banco tarde en notificar. "
                f"Envíame el comprobante como imagen y lo verifico de inmediato."
            )

        try:
            bot.send_message(telegram_id, msg, parse_mode="Markdown")
        except Exception:
            bot.send_message(telegram_id, msg)

        logger.info(
            f"[{profile.tenant_id}] Verificación pago '{referencia}': "
            f"found={result['found']} amount_match={result.get('amount_match')}"
        )

    threading.Thread(target=_verify, daemon=True, name="pay-verify").start()


