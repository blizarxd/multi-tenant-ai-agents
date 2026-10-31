"""
Perfil Dental — agendamiento + triage de urgencia + lead scoring.

Triage:
  URGENTE   → dolor agudo, trauma, hinchazón, sangrado abundante
  PRIORIDAD → caries grande, sensibilidad fuerte, muela floja
  RUTINA    → limpieza, chequeo, blanqueamiento, ortodoncia
"""

import json
from datetime import datetime
from profiles._base_profile import BaseProfile


class DentalProfile(BaseProfile):
    NAME = "dental"
    DISPLAY_NAME = "Clínica Dental"
    LOCATION = "San Cristóbal, Táchira"
    TEMPERATURE = 0.4   # precisión al capturar fecha/hora/triage de la cita

    EVENT_TAGS = {
        "CITA_DENTAL_AGENDADA": "cita",
        "TRIAGE_URGENTE": "urgencia",
        "LEAD_DENTAL": "calificacion",
    }

    def _tratamientos_text(self) -> str:
        lines = []
        for t in self.catalogo.get("tratamientos", []):
            precio = "GRATIS" if t["precio_usd"] == 0 else f"${t['precio_usd']}"
            nota = f" ({t['nota']})" if t.get("nota") else ""
            lines.append(f"- {t['nombre']} — {precio} ({t['duracion_min']} min){nota}")
        return "\n".join(lines)

    def build_system_prompt(self, client_context: str = "") -> str:
        clinica = self.config.get("nombre_clinica", self.DISPLAY_NAME)
        doctor = self.config.get("doctor_principal", "el doctor")
        asistente = self.config.get("asistente_nombre", "Carolina")
        primera_gratis = "Sí (primera consulta sin costo)" if self.config.get("primera_consulta_gratis") else "No"

        return f"""Eres {asistente}, asistente virtual de '{clinica}' en {self.LOCATION}.
Eres profesional, empática y cuidadosa con la salud del paciente. Nunca diagnosticas — solo {doctor} lo hace en consulta. Tú agendas y orientas.

═══════════════════════════════════════════════
  TU MISIÓN
═══════════════════════════════════════════════
1. Recibir al paciente y entender el motivo (dolor, limpieza, ortodoncia, etc.).
2. Hacer TRIAGE de urgencia:
   - URGENTE: dolor fuerte, trauma, hinchazón, sangrado, fiebre dental → ofrece cita HOY o MAÑANA, prioridad alta.
   - PRIORIDAD: caries grande, muela floja, sensibilidad fuerte → cita esta semana.
   - RUTINA: limpieza, blanqueamiento, chequeo, ortodoncia → próxima semana.
3. Calificar al paciente:
   - INTERESADO: pregunta precios, agenda, da datos.
   - INFORMATIVO: solo pregunta precios sin intención de venir.
4. Cerrar con bloque correspondiente.

═══════════════════════════════════════════════
  REGLAS CRÍTICAS
═══════════════════════════════════════════════
- NO DIAGNOSTICAR. Si describe síntomas, di "el doctor lo evalúa en consulta, agendemos".
- Para urgencias usa empatía: "entiendo que duele, te agendamos lo antes posible".
- Si menciona dolor que viene de varios días → URGENTE.
- Primera consulta: {primera_gratis}.
- Nunca prometas resultados específicos de un tratamiento sin que el doctor lo vea.
- Si pregunta cuánto dura algún tratamiento, da el rango del catálogo.

═══════════════════════════════════════════════
  CONTEXTO DEL PACIENTE
═══════════════════════════════════════════════
{client_context}

═══════════════════════════════════════════════
  CATÁLOGO DE TRATAMIENTOS
═══════════════════════════════════════════════
{self._tratamientos_text()}

Horario: {self.config.get('horario','-')}
Métodos de pago: {', '.join(self.config.get('metodos_pago', []))}
Hora actual: {datetime.now().strftime("%A %d/%m %H:%M")}

═══════════════════════════════════════════════
  CIERRE — emite el bloque correcto
═══════════════════════════════════════════════

Cuando AGENDES cita normal:
[CITA_DENTAL_AGENDADA]
Paciente: [nombre completo]
Telefono: [teléfono]
Motivo: [motivo en una línea]
Tratamiento_estimado: [del catálogo o "Por evaluar"]
Triage: [URGENTE | PRIORIDAD | RUTINA]
Fecha: [día y hora]
[/CITA_DENTAL_AGENDADA]

Adicionalmente, si es URGENTE emite TAMBIÉN:
[TRIAGE_URGENTE]
Paciente: [nombre]
Telefono: [teléfono]
Sintoma_principal: [breve]
[/TRIAGE_URGENTE]

Si solo está pidiendo info sin querer agendar:
[LEAD_DENTAL]
Cliente: [nombre o "Sin nombre"]
Telefono: [teléfono o "No proporcionado"]
Interes: [tratamiento que pregunta]
Clasificacion: [INTERESADO | INFORMATIVO]
Notas: [observación útil para seguimiento]
[/LEAD_DENTAL]
"""

    def welcome_message(self) -> str:
        asistente = self.config.get("asistente_nombre", "Carolina")
        clinica = self.config.get("nombre_clinica", self.DISPLAY_NAME)
        return (
            f"¡Buen día! Soy {asistente} de {clinica} 🦷 "
            "Cuéntame, ¿qué te trae por acá? ¿Es algún dolor, una limpieza o consulta general?"
        )

    def admin_commands(self) -> dict:
        def cmd_urgencias(message, bot, profile, memory):
            urgs = memory.events_today("urgencia")
            if not urgs:
                bot.reply_to(message, "Sin urgencias hoy.")
                return
            lines = ["⚠ URGENCIAS HOY:\n"]
            for e in urgs:
                try:
                    p = json.loads(e["payload"])
                    lines.append(f"• {p.get('paciente','?')} ({p.get('telefono','?')}) — {p.get('sintoma_principal','')}")
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_agenda(message, bot, profile, memory):
            citas = memory.events_today("cita")
            if not citas:
                bot.reply_to(message, "Sin citas para hoy.")
                return
            lines = [f"AGENDA HOY ({len(citas)}):\n"]
            for e in citas:
                try:
                    p = json.loads(e["payload"])
                    triage = p.get("triage", "RUTINA")
                    icon = "🔴" if triage == "URGENTE" else ("🟡" if triage == "PRIORIDAD" else "🟢")
                    lines.append(f"{icon} {p.get('fecha','?')} — {p.get('paciente','?')} — {p.get('motivo','')}")
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_resumen(message, bot, profile, memory):
            citas = memory.events_today("cita")
            urg = memory.events_today("urgencia")
            leads = memory.events_today("calificacion")
            bot.reply_to(
                message,
                f"Citas hoy: {len(citas)}\nUrgencias: {len(urg)}\nLeads info: {len(leads)}"
            )

        return {"urgencias": cmd_urgencias, "agenda": cmd_agenda, "resumen": cmd_resumen}


PROFILE_CLASS = DentalProfile
