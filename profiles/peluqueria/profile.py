"""
Perfil Peluquería — agendamiento de citas.
"""

import json
from datetime import datetime
from profiles._base_profile import BaseProfile


class PeluqueriaProfile(BaseProfile):
    NAME = "peluqueria"
    DISPLAY_NAME = "Peluquería"
    LOCATION = "San Cristóbal, Táchira"

    EVENT_TAGS = {
        "CITA_AGENDADA": "cita",
        "CITA_CANCELADA": "cita_cancelada",
    }

    def _servicios_text(self) -> str:
        lines = []
        for s in self.catalogo.get("servicios", []):
            lines.append(f"- {s['nombre']} — ${s['precio_usd']} ({s['duracion_min']} min)")
        return "\n".join(lines)

    def _horario_text(self) -> str:
        h = self.config.get("horario", {})
        return ", ".join([f"{d.capitalize()}: {hr}" for d, hr in h.items()])

    def build_system_prompt(self, client_context: str = "") -> str:
        estilista = self.config.get("estilista_principal", "Sofía")
        negocio = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        anticipacion = self.config.get("politica_anticipacion_horas", 2)

        return f"""Eres {estilista}, recepcionista virtual de '{negocio}' en {self.LOCATION}.
Eres dulce, organizada y siempre amable. Hablas natural, no robótica.

═══════════════════════════════════════════════
  TU MISIÓN
═══════════════════════════════════════════════
1. Recibir consultas de clientes que quieren agendar.
2. Sugerir el servicio correcto si tienen dudas.
3. Confirmar día y hora viable según el horario.
4. Pedir nombre y teléfono.
5. Cerrar la cita emitiendo el bloque [CITA_AGENDADA].

═══════════════════════════════════════════════
  REGLAS
═══════════════════════════════════════════════
- Solo agendas en horario de atención.
- Pides al menos {anticipacion}h de anticipación.
- Si la hora pedida ya pasó, sugiere la siguiente disponible.
- Para servicios largos (mechas, novia) avisa la duración.
- Nunca inventas servicios fuera del catálogo.
- Si el cliente solo está preguntando precios, da el precio y pregunta si quiere agendar.

═══════════════════════════════════════════════
  CONTEXTO DEL CLIENTE
═══════════════════════════════════════════════
{client_context}

═══════════════════════════════════════════════
  CATÁLOGO DE SERVICIOS
═══════════════════════════════════════════════
{self._servicios_text()}

═══════════════════════════════════════════════
  HORARIO
═══════════════════════════════════════════════
{self._horario_text()}

Hora actual: {datetime.now().strftime("%A %d/%m %H:%M")}

═══════════════════════════════════════════════
  CIERRE — emite ESTE bloque cuando agendes
═══════════════════════════════════════════════
[CITA_AGENDADA]
Cliente: [nombre completo]
Telefono: [teléfono]
Servicio: [nombre exacto del servicio]
Precio: [USD]
Duracion: [minutos]
Fecha: [día y hora confirmada, ej. "viernes 9 de junio 2:00 PM"]
Notas: [observaciones del cliente, ej. "es para una boda"]
[/CITA_AGENDADA]

Si el cliente quiere cancelar:
[CITA_CANCELADA]
Cliente: [nombre]
Motivo: [si lo da]
[/CITA_CANCELADA]
"""

    def welcome_message(self) -> str:
        nombre = self.config.get("estilista_principal", "Sofía")
        negocio = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        return (
            f"¡Hola! Soy {nombre} de {negocio} 💇‍♀️ "
            "¿Para qué servicio te gustaría agendar y qué día tienes en mente?"
        )

    def admin_commands(self) -> dict:
        def cmd_agenda(message, bot, profile, memory):
            citas = memory.events_today("cita")
            if not citas:
                bot.reply_to(message, "Sin citas para hoy.")
                return
            lines = [f"AGENDA HOY ({len(citas)} citas):\n"]
            for e in citas:
                try:
                    p = json.loads(e["payload"])
                    lines.append(
                        f"• {p.get('fecha','?')} — {p.get('servicio','?')} → {p.get('cliente','?')} ({p.get('telefono','?')})"
                    )
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_resumen(message, bot, profile, memory):
            citas = memory.events_today("cita")
            cancel = memory.events_today("cita_cancelada")
            total = 0.0
            for e in citas:
                try:
                    p = json.loads(e["payload"])
                    val = ''.join(c for c in str(p.get("precio", "0")) if c.isdigit() or c == '.')
                    total += float(val or 0)
                except Exception:
                    pass
            bot.reply_to(message, f"Citas hoy: {len(citas)}\nCanceladas: {len(cancel)}\nIngreso esperado: ${total:.2f}")

        return {"agenda": cmd_agenda, "resumen": cmd_resumen}


PROFILE_CLASS = PeluqueriaProfile
