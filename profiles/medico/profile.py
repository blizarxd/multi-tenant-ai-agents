"""
Perfil Médico — Medicina General (o cualquier especialidad).

Funciones especiales vs. otros perfiles:
  1. FAQ integrado — responde preguntas frecuentes sin molestar al doctor.
  2. Triage — clasifica URGENTE / PRIORIDAD / RUTINA desde el primer mensaje.
  3. Google Calendar — crea el evento al confirmar la cita.
  4. PDF + Email — reporte diario vía /reporte_pdf y /enviar_reporte.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from profiles._base_profile import BaseProfile

logger = logging.getLogger(__name__)


class MedicoProfile(BaseProfile):
    NAME = "medico"
    DISPLAY_NAME = "Consultorio Médico"
    LOCATION = "San Cristóbal, Táchira"
    TEMPERATURE = 0.4   # precisión al capturar fecha/hora/triage de la cita

    EVENT_TAGS = {
        "CITA_MEDICA_AGENDADA":  "cita",
        "CITA_CANCELADA":        "cita_cancelada",
        "TRIAGE_URGENTE":        "urgencia",
        "FAQ_RESPONDIDA":        "faq",
        "FOTO_MEDICA_RECIBIDA":  "foto_medica",
    }

    # Síntomas que sugieren pedirle foto al paciente
    _SINTOMAS_VISIBLES = [
        "herida", "cortada", "sarpullido", "ronchas", "roncha", "lunar",
        "quemadura", "inflamaci", "hinchad", "ojo rojo", "ojo", "piel",
        "mancha", "grano", "absceso", "infeccion", "infección", "picadura",
        "mordida", "cicatriz", "llaga", "ampolla", "erupcion", "erupción",
        "dermatitis", "acne", "acné", "urticaria", "hematoma", "moretón",
        "golpe", "tumor", "bulto", "quiste",
    ]

    # ── Helpers de texto ──────────────────────────────────────────
    def _servicios_text(self) -> str:
        lines = []
        for s in self.catalogo.get("servicios", []):
            lines.append(
                f"- {s['nombre']} — ${s['precio_usd']} ({s['duracion_min']} min): {s.get('descripcion','')}"
            )
        return "\n".join(lines)

    def _faq_text(self) -> str:
        faqs = self.config.get("faq", [])
        return "\n\n".join(
            f"P: {f['pregunta']}\nR: {f['respuesta']}" for f in faqs
        )

    def _horario_text(self) -> str:
        h = self.config.get("horario", {})
        return "\n".join(f"  {d.capitalize()}: {hr}" for d, hr in h.items())

    # ── Prompt principal ──────────────────────────────────────────
    def build_system_prompt(self, client_context: str = "") -> str:
        doctor    = self.config.get("doctor_nombre", "el doctor")
        asistente = self.config.get("asistente_nombre", "Mariana")
        clinica   = self.config.get("nombre_clinica", self.DISPLAY_NAME)
        especial  = self.config.get("especialidad", "Medicina General")
        precio    = self.config.get("consulta_precio_usd", 15)
        metodos   = ", ".join(self.config.get("metodos_pago", []))

        return f"""Eres {asistente}, asistente virtual del '{clinica}' ({especial}) en {self.LOCATION}.
Atiendes por Telegram. Eres cálida, empática, profesional y muy organizada.
Representas al {doctor} y cuidas su agenda agendando solo pacientes reales.

═══════════════════════════════════════════════
  TU MISIÓN
═══════════════════════════════════════════════
1. RESPONDER PREGUNTAS FRECUENTES — tienes las respuestas listas, úsalas directamente.
2. HACER TRIAGE — clasificar urgencia desde el primer mensaje del paciente.
3. AGENDAR LA CITA — con fecha, hora, nombre y teléfono confirmados.
4. CERRAR emitiendo el bloque correcto.

═══════════════════════════════════════════════
  TRIAGE (evalúalo desde el primer mensaje)
═══════════════════════════════════════════════
URGENTE → dolor fuerte, fiebre alta (+39°C), vómito intenso, trauma, mareo severo,
          dificultad respiratoria, sangrado, herida abierta.
  → Acción: ofrece cita HOY o MAÑANA máximo. Di "el doctor puede verte hoy mismo".

PRIORIDAD → fiebre moderada, infección, síntoma de varios días, muela floja, herida que no cierra.
  → Acción: cita esta semana, los primeros días disponibles.

RUTINA → chequeo general, certificado médico, control de tensión, interpretación de exámenes.
  → Acción: próxima semana, horario normal.

═══════════════════════════════════════════════
  FLUJO DE ATENCIÓN
═══════════════════════════════════════════════
PASO 1 — Saludo cálido: "¿Qué te trae hoy? ¿Tienes algún síntoma o es para un chequeo?"
PASO 2 — Evalúa el triage. Si hay urgencia, actúa de inmediato con empatía.
PASO 2b — FOTO PREVIA: si el síntoma es VISIBLE (herida, sarpullido, ojo, piel, inflamación,
           lunar, quemadura, golpe, mancha, grano, absceso, etc.), pregunta UNA SOLA VEZ:
           "¿Quieres enviarme una foto del área afectada? Así el doctor puede tener una idea
           antes de verte y prepararse mejor para la consulta. Es completamente opcional."
           Si manda la foto → analízala y agradece. Luego sigue con el flujo normal.
           Si no quiere → respeta y continúa sin insistir.
PASO 3 — Si es una pregunta frecuente (precio, seguro, dirección), respóndela directamente.
PASO 4 — Propón día y hora según el triage y el horario del consultorio.
PASO 5 — Confirma: nombre completo, teléfono de contacto, motivo de consulta.
PASO 6 — Cierra con el bloque [CITA_MEDICA_AGENDADA].

═══════════════════════════════════════════════
  REGLAS CRÍTICAS
═══════════════════════════════════════════════
- NUNCA DIAGNOSTIQUES. Ante síntomas: "El doctor te evalúa en consulta y te indica exactamente qué pasa."
- No prometas tratamientos ni medicamentos por chat.
- Si preguntan si eres IA: "Soy {asistente}, la asistente del consultorio."
- No reveles la dirección exacta hasta que la cita esté confirmada.
- Si el paciente menciona EMERGENCIA real (accidente grave, paro cardíaco): "Ve a urgencias del hospital más cercano ahora mismo."
- Si ya conoces al paciente (contexto histórico), salúdalo por su nombre.

═══════════════════════════════════════════════
  CONTEXTO DEL PACIENTE
═══════════════════════════════════════════════
{client_context}

═══════════════════════════════════════════════
  SERVICIOS Y PRECIOS
═══════════════════════════════════════════════
{self._servicios_text()}

Métodos de pago: {metodos}

═══════════════════════════════════════════════
  HORARIO DE ATENCIÓN
═══════════════════════════════════════════════
{self._horario_text()}

Fecha y hora actual: {datetime.now().strftime("%A %d/%m/%Y %H:%M")}

═══════════════════════════════════════════════
  PREGUNTAS FRECUENTES — responde directo con estas respuestas
═══════════════════════════════════════════════
{self._faq_text()}

═══════════════════════════════════════════════
  CIERRE — emite el bloque correcto al final del mensaje
═══════════════════════════════════════════════

Al AGENDAR cita (siempre que se confirme):
[CITA_MEDICA_AGENDADA]
Paciente: [nombre completo]
Telefono: [teléfono]
Motivo: [motivo de consulta en una línea]
Triage: [URGENTE | PRIORIDAD | RUTINA]
Fecha: [día y hora exacta, ej. "martes 13/05 9:00 AM"]
Servicio: [nombre del servicio del catálogo]
Precio_USD: [{precio}]
Notas: [observaciones adicionales]
[/CITA_MEDICA_AGENDADA]

Si el triage es URGENTE, emite TAMBIÉN:
[TRIAGE_URGENTE]
Paciente: [nombre]
Telefono: [teléfono]
Sintoma: [síntoma principal]
[/TRIAGE_URGENTE]

Si el paciente cancela:
[CITA_CANCELADA]
Paciente: [nombre]
Motivo: [razón si la menciona]
[/CITA_CANCELADA]

Si solo pregunta sin querer agendar:
[FAQ_RESPONDIDA]
Pregunta: [lo que preguntó]
Interes_en_agendar: [SI | NO]
[/FAQ_RESPONDIDA]

Cuando el paciente envía una foto médica (herida, piel, ojo, etc.):
[FOTO_MEDICA_RECIBIDA]
Zona_afectada: [parte del cuerpo visible en la foto]
Descripcion_visual: [descripción objetiva — color, tamaño, apariencia, sin diagnóstico]
Nivel_urgencia_visual: [NORMAL | ATENCION | URGENTE]
[/FOTO_MEDICA_RECIBIDA]

Estos bloques son INTERNOS — el sistema los lee, el paciente NO los ve.
Después del bloque continúa la conversación de forma natural.
"""

    def photo_prompt(self) -> str:
        """Prompt especializado para analizar fotos médicas con Gemini Vision."""
        doctor   = self.config.get("doctor_nombre", "el doctor")
        clinica  = self.config.get("nombre_clinica", self.DISPLAY_NAME)
        return (
            f"El paciente de {clinica} te envió una foto de su condición médica "
            f"para que {doctor} la vea como referencia previa antes de la consulta.\n\n"
            "Analiza la imagen con cuidado:\n"
            "1. Describe OBJETIVAMENTE lo que ves: zona del cuerpo, coloración, tamaño estimado, "
            "   apariencia (inflamación, enrojecimiento, lesión, etc.).\n"
            "2. NO diagnostiques ni menciones enfermedades específicas. "
            "   Di 'el doctor lo evaluará en consulta'.\n"
            "3. Si visualmente parece muy grave (hemorragia activa, quemadura severa, "
            "   trauma grave), indícalo con urgencia.\n"
            "4. Agradece el envío al paciente de manera cálida y confirma que la foto "
            "   quedó registrada para que el doctor la revise.\n\n"
            "Al final emite el bloque interno:\n"
            "[FOTO_MEDICA_RECIBIDA]\n"
            "Zona_afectada: [parte del cuerpo]\n"
            "Descripcion_visual: [descripción objetiva breve]\n"
            "Nivel_urgencia_visual: [NORMAL | ATENCION | URGENTE]\n"
            "[/FOTO_MEDICA_RECIBIDA]"
        )

    def welcome_message(self) -> str:
        asistente = self.config.get("asistente_nombre", "Mariana")
        clinica   = self.config.get("nombre_clinica", self.DISPLAY_NAME)
        doctor    = self.config.get("doctor_nombre", "el doctor")
        return (
            f"¡Hola! Soy {asistente}, asistente de {clinica} — {doctor}.\n"
            f"¿En qué te puedo ayudar hoy? ¿Tienes algún síntoma o quieres agendar una consulta?"
        )

    # ── on_event: Calendar + BD ───────────────────────────────────
    # ── Historial médico para el contexto ────────────────────────
    def medical_history_context(self, telegram_id, memory) -> str:
        history = memory.get_medical_history(telegram_id)
        # Incluir también fotos médicas previas
        with memory._conn() as c:
            foto_rows = c.execute(
                "SELECT payload, timestamp FROM events "
                "WHERE telegram_id = ? AND event_type = 'foto_medica' ORDER BY id DESC LIMIT 3",
                (str(telegram_id),)
            ).fetchall()

        if not history and not foto_rows:
            return ""

        lines = ["\nHISTORIAL MÉDICO DEL PACIENTE (úsalo para dar continuidad clínica):"]

        for h in history:
            p = h["payload"]
            fecha    = p.get("fecha") or h["timestamp"][:10]
            motivo   = p.get("motivo") or p.get("sintoma") or "—"
            triage   = p.get("triage", "")
            servicio = p.get("servicio", "")
            notas    = p.get("notas", "")
            linea = f"  [{fecha}] {h['event_type'].upper()} — {motivo}"
            if triage:   linea += f" | Triage: {triage}"
            if servicio: linea += f" | Servicio: {servicio}"
            if notas and notas not in ("—", ""):
                linea += f" | Notas: {notas}"
            lines.append(linea)

        if foto_rows:
            import json as _json
            lines.append("\nFOTOS MÉDICAS PREVIAS (el paciente las envió antes de la cita):")
            for fr in foto_rows:
                try:
                    fp = _json.loads(fr["payload"])
                except Exception:
                    fp = {}
                zona  = fp.get("zona_afectada", "—")
                desc  = fp.get("descripcion_visual", "—")
                nivel = fp.get("nivel_urgencia_visual", "NORMAL")
                lines.append(
                    f"  [{fr['timestamp'][:10]}] FOTO: zona={zona} | {desc} | urgencia_visual={nivel}"
                )

        return "\n".join(lines)

    def _get_sheet_id(self) -> str | None:
        """Obtiene (o crea) el spreadsheet ID para este tenant."""
        try:
            from core.google_sheets import get_or_create_spreadsheet
            clinica = self.config.get("nombre_clinica", self.DISPLAY_NAME)
            title   = f"Pacientes — {clinica}"
            return get_or_create_spreadsheet(self.tenant_id, title)
        except Exception as e:
            logger.warning(f"Google Sheets no disponible: {e}")
            return None

    def _sync_sheets(self, telegram_id: int, event_type: str, payload: dict):
        """Sincroniza el evento con Google Sheets en hilo aparte."""
        import threading
        def _do():
            try:
                from core.google_sheets import (
                    append_cita, append_foto, sync_patient_tab, get_spreadsheet_url
                )
                sid = self._get_sheet_id()
                if not sid:
                    return

                if event_type == "cita":
                    append_cita(sid, telegram_id, payload)
                elif event_type == "foto_medica":
                    append_foto(sid, telegram_id, payload)

                # Siempre actualizar la hoja de vida del paciente
                if self.memory:
                    client = self.memory.get_client(telegram_id)
                    nombre   = (client or {}).get("nombre") or payload.get("paciente") or f"ID_{telegram_id}"
                    telefono = (client or {}).get("telefono") or payload.get("telefono") or ""
                    events   = self.memory.get_events_for_patient(telegram_id)
                    sync_patient_tab(sid, nombre, telegram_id, telefono, events)
            except Exception as e:
                logger.warning(f"Error sincronizando Sheets: {e}")

        threading.Thread(target=_do, daemon=True, name="sheets-sync").start()

    def on_event(self, event_type: str, payload: dict, telegram_id: int):
        if self.memory:
            self.memory.save_event(telegram_id, event_type, payload)

        if event_type == "cita":
            if self.memory:
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("paciente"),
                    telefono=payload.get("telefono"),
                )
            self._push_to_calendar(payload)
            self._schedule_reminder(telegram_id, payload)
            self._sync_sheets(telegram_id, event_type, payload)

        elif event_type == "foto_medica":
            logger.info(
                f"Foto médica recibida de {telegram_id}: "
                f"zona={payload.get('zona_afectada')} "
                f"urgencia={payload.get('nivel_urgencia_visual')}"
            )
            self._sync_sheets(telegram_id, event_type, payload)

    def _schedule_reminder(self, telegram_id: int, payload: dict):
        """Programa un recordatorio 30 min antes de la cita."""
        if not self.memory:
            return
        try:
            from core.google_calendar import _parse_date
            fecha_str = payload.get("fecha", "")
            if not fecha_str:
                return
            cita_dt = _parse_date(fecha_str)
            if not cita_dt:
                return
            remind_at = cita_dt - timedelta(minutes=30)
            if remind_at <= datetime.now():
                logger.info(f"Cita muy próxima — recordatorio omitido para {telegram_id}")
                return

            paciente = payload.get("paciente", "paciente")
            clinica  = self.config.get("nombre_clinica", self.DISPLAY_NAME)
            doctor   = self.config.get("doctor_nombre", "el doctor")
            servicio = payload.get("servicio", "consulta")
            mensaje  = (
                f"Hola {paciente}, te recuerdo que tienes una cita en {clinica} "
                f"con {doctor} en 30 minutos ({cita_dt.strftime('%I:%M %p')}) "
                f"para {servicio}.\n\n"
                f"Si necesitas cancelar o reagendar, escríbeme aquí."
            )
            self.memory.add_reminder(telegram_id, remind_at, mensaje)
            logger.info(f"Recordatorio programado para {paciente} a las {remind_at.strftime('%H:%M')}")
        except Exception as e:
            logger.warning(f"No se pudo programar recordatorio: {e}")

    def _push_to_calendar(self, payload: dict):
        calendar_id = self.config.get("google_calendar_id", "")
        if not calendar_id:
            return
        try:
            from core.google_calendar import create_appointment
            create_appointment(
                calendar_id=calendar_id,
                summary=f"Consulta: {payload.get('paciente', 'Paciente')}",
                date_str=payload.get("fecha", ""),
                duration_min=int(self.config.get("duracion_consulta_min", 20)),
                description=(
                    f"Motivo: {payload.get('motivo', '')}\n"
                    f"Triage: {payload.get('triage', 'RUTINA')}\n"
                    f"Tel: {payload.get('telefono', '')}\n"
                    f"Notas: {payload.get('notas', '')}"
                ),
            )
            logger.info(f"Cita creada en Google Calendar: {payload.get('paciente')}")
        except Exception as e:
            logger.warning(f"Google Calendar no disponible: {e}")

    # ── Comandos admin ────────────────────────────────────────────
    def admin_commands(self) -> dict:

        def cmd_agenda(message, bot, profile, memory):
            citas = memory.events_today("cita")
            if not citas:
                bot.reply_to(message, "Sin citas para hoy.")
                return

            # Agrupar fotos por telegram_id para saber quién envió
            fotos_por_tid = {}
            with memory._conn() as c:
                rows = c.execute(
                    "SELECT telegram_id, payload FROM events WHERE event_type='foto_medica' ORDER BY id DESC"
                ).fetchall()
            for r in rows:
                tid = r["telegram_id"]
                if tid not in fotos_por_tid:
                    try:
                        fotos_por_tid[tid] = json.loads(r["payload"])
                    except Exception:
                        fotos_por_tid[tid] = {}

            lines = [f"AGENDA HOY ({len(citas)} citas):\n"]
            for e in citas:
                try:
                    p = json.loads(e["payload"])
                    triage = p.get("triage", "RUTINA")
                    icon = "🔴" if triage == "URGENTE" else ("🟡" if triage == "PRIORIDAD" else "🟢")
                    tid = str(e.get("telegram_id", ""))
                    foto_info = ""
                    if tid in fotos_por_tid:
                        fp = fotos_por_tid[tid]
                        zona  = fp.get("zona_afectada", "")
                        nivel = fp.get("nivel_urgencia_visual", "")
                        foto_info = f"\n   📸 Foto previa: {zona}"
                        if nivel and nivel != "NORMAL":
                            foto_info += f" [{nivel}]"
                    lines.append(
                        f"{icon} {p.get('fecha','?')} — {p.get('paciente','?')}\n"
                        f"   {p.get('motivo','')} | Tel: {p.get('telefono','')}"
                        f"{foto_info}"
                    )
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_urgencias(message, bot, profile, memory):
            urgs = memory.events_today("urgencia")
            if not urgs:
                bot.reply_to(message, "Sin urgencias hoy.")
                return
            lines = ["⚠ URGENCIAS HOY:\n"]
            for e in urgs:
                try:
                    p = json.loads(e["payload"])
                    lines.append(
                        f"🔴 {p.get('paciente','?')} — Tel: {p.get('telefono','?')}\n"
                        f"   Síntoma: {p.get('sintoma','')}"
                    )
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_resumen(message, bot, profile, memory):
            citas  = memory.events_today("cita")
            urg    = memory.events_today("urgencia")
            cancel = memory.events_today("cita_cancelada")
            precio = float(profile.config.get("consulta_precio_usd", 15))
            bot.reply_to(
                message,
                f"RESUMEN DEL DÍA — {datetime.now().strftime('%d/%m/%Y')}\n"
                f"Citas agendadas:    {len(citas)}\n"
                f"Urgencias:          {len(urg)}\n"
                f"Canceladas:         {len(cancel)}\n"
                f"Ingreso estimado:   ${len(citas) * precio:.0f} USD"
            )

        def cmd_reporte_pdf(message, bot, profile, memory):
            try:
                from core.pdf_report import generate_daily_report
                pdf_bytes = generate_daily_report(
                    tenant_id=profile.tenant_id,
                    doctor=profile.config.get("doctor_nombre", "Doctor"),
                    clinica=profile.config.get("nombre_clinica", profile.DISPLAY_NAME),
                )
                bot.send_document(
                    message.chat.id,
                    ("reporte_diario.pdf", pdf_bytes),
                    caption=f"Reporte del día — {datetime.now().strftime('%d/%m/%Y')}"
                )
            except Exception as e:
                bot.reply_to(message, f"Error generando PDF: {e}")

        def cmd_crear_hoja(message, bot, profile, memory):
            bot.reply_to(message, "Creando/verificando hoja de vida en Google Sheets...")
            try:
                from core.google_sheets import (
                    get_or_create_spreadsheet, sync_patient_tab, get_spreadsheet_url,
                    append_cita, append_foto
                )
                clinica = profile.config.get("nombre_clinica", profile.DISPLAY_NAME)
                title   = f"Pacientes — {clinica}"
                sid     = get_or_create_spreadsheet(profile.tenant_id, title)

                # Sincronizar todos los pacientes existentes
                patients = memory.get_all_patients()
                synced   = 0
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
                bot.reply_to(
                    message,
                    f"Hoja de vida creada/actualizada en Google Sheets.\n"
                    f"Pacientes sincronizados: {synced}\n\n"
                    f"Abre aqui:\n{url}"
                )
            except Exception as e:
                bot.reply_to(message, f"Error: {e}")

        def cmd_historia_excel(message, bot, profile, memory):
            try:
                from core.excel_report import generate_patient_excel
                xl_bytes = generate_patient_excel(
                    tenant_id=profile.tenant_id,
                    clinica=profile.config.get("nombre_clinica", profile.DISPLAY_NAME),
                    doctor=profile.config.get("doctor_nombre", "Doctor"),
                    memory=memory,
                )
                filename = f"historial_pacientes_{datetime.now().strftime('%Y%m%d')}.xlsx"
                bot.send_document(
                    message.chat.id,
                    (filename, xl_bytes),
                    caption=f"Historial de pacientes — {datetime.now().strftime('%d/%m/%Y')}"
                )
            except Exception as e:
                bot.reply_to(message, f"Error generando Excel: {e}")

        def cmd_subir_reporte(message, bot, profile, memory):
            bot.reply_to(message, "Generando reporte y subiendo a Google Drive...")
            try:
                from core.pdf_report import generate_daily_report
                from core.google_drive import upload_file, save_report_locally
                pdf_bytes = generate_daily_report(
                    tenant_id=profile.tenant_id,
                    doctor=profile.config.get("doctor_nombre", "Doctor"),
                    clinica=profile.config.get("nombre_clinica", profile.DISPLAY_NAME),
                )
                filename = f"reporte_{datetime.now().strftime('%Y-%m-%d')}.pdf"
                url = upload_file(pdf_bytes, filename, folder_name="Reportes Médicos")
                if url:
                    bot.reply_to(message,
                        f"Reporte subido a Google Drive:\n{url}")
                else:
                    # Fallback: guardar localmente y avisar
                    local_path = save_report_locally(pdf_bytes, filename)
                    bot.reply_to(message,
                        f"No se pudo subir a Drive. Guardado localmente:\n{local_path}")
            except Exception as e:
                bot.reply_to(message, f"Error: {e}")

        def cmd_enviar_reporte(message, bot, profile, memory):
            email = profile.config.get("email_doctor", "")
            if not email:
                bot.reply_to(message, "Falta configurar email_doctor en config.json del tenant.")
                return
            try:
                from core.pdf_report import generate_daily_report
                from core.email_sender import send_pdf_report
                pdf_bytes = generate_daily_report(
                    tenant_id=profile.tenant_id,
                    doctor=profile.config.get("doctor_nombre", "Doctor"),
                    clinica=profile.config.get("nombre_clinica", profile.DISPLAY_NAME),
                )
                sender   = profile.config.get("gmail_sender", "")
                password = profile.config.get("gmail_app_password") or os.getenv("GMAIL_APP_PASSWORD", "")
                send_pdf_report(
                    to_email=email,
                    subject=f"Reporte diario — {datetime.now().strftime('%d/%m/%Y')} — {profile.config.get('nombre_clinica','')}",
                    pdf_bytes=pdf_bytes,
                    from_email=sender,
                    app_password=password,
                )
                bot.reply_to(message, f"Reporte enviado a {email}")
            except Exception as e:
                bot.reply_to(message, f"Error enviando reporte: {e}")

        return {
            "agenda":          cmd_agenda,
            "urgencias":       cmd_urgencias,
            "resumen":         cmd_resumen,
            "reporte_pdf":     cmd_reporte_pdf,
            "enviar_reporte":  cmd_enviar_reporte,
            "historia_excel":  cmd_historia_excel,
            "subir_reporte":   cmd_subir_reporte,
            "crear_hoja":      cmd_crear_hoja,
        }


PROFILE_CLASS = MedicoProfile
