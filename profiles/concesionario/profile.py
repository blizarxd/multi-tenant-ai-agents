"""
Perfil Concesionario — Vendedor de carros de segunda (intermediario / consignación).

Capacidades activas:
  - Calificación de leads 0-10 (presupuesto, urgencia, capacidad de pago)
  - Catálogo dinámico de vehículos consignados con highlights
  - Filtro inteligente por marca, presupuesto, transmisión, tipo
  - Agendar test drive (Google Calendar + recordatorio 30 min antes)
  - Recibir trade-in: pide datos + fotos del carro a entregar
  - Análisis de fotos: trade-in del cliente o referencias que manda
  - Google Sheets — CRM de leads y trade-ins
  - Reporte semanal automático
  - Comandos admin: /leads /test_drives /trade_ins /resumen /catalogo
"""

import json
import logging
from datetime import datetime
from profiles._base_profile import BaseProfile
from core.capabilities import (
    cap_calendar,
    cap_reminder,
    cap_sheets,
    make_cmd_historia_excel,
    make_cmd_subir_reporte,
    make_cmd_crear_hoja,
    make_cmd_reporte_semanal,
)

logger = logging.getLogger(__name__)


class ConcesionarioProfile(BaseProfile):
    NAME         = "concesionario"
    DISPLAY_NAME = "Concesionario Demo"
    LOCATION     = "San Cristóbal, Táchira"

    EVENT_TAGS = {
        "TEST_DRIVE_AGENDADO":  "cita",
        "LEAD_CALIFICADO":      "calificacion",
        "TRADE_IN_RECIBIDO":    "trade_in",
        "OFERTA_RECIBIDA":      "oferta",
        "DESCARTAR_CLIENTE":    "descarte",
    }

    # ── Helpers de catálogo ──────────────────────────────────────────

    def _vehiculos_resumen(self) -> str:
        """Resumen compacto de todos los vehículos para que la IA tenga el catálogo en contexto."""
        lines = []
        for v in self.catalogo.get("vehiculos", []):
            destacado = " ⭐" if v.get("destacado") else ""
            highlights = " | ".join(v.get("highlights", [])[:3])
            lines.append(
                f"[{v['id']}] {v['marca']} {v['modelo']} {v['anio']}{destacado} — ${v['precio_usd']:,}\n"
                f"     {v['transmision']} | {v['combustible']} | {v['kilometraje']:,} km | {v['color']} | {v['condicion']}\n"
                f"     {highlights}\n"
                f"     {v.get('descripcion','')}"
            )
        return "\n\n".join(lines)

    def _vehiculo_detalle(self, car_id: str) -> str:
        """Ficha completa de un vehículo por ID."""
        for v in self.catalogo.get("vehiculos", []):
            if v["id"].upper() == car_id.upper():
                highlights = "\n  • ".join(v.get("highlights", []))
                return (
                    f"═══ {v['marca'].upper()} {v['modelo'].upper()} {v['anio']} [{v['id']}] ═══\n"
                    f"Precio: ${v['precio_usd']:,} USD\n"
                    f"Kilometraje: {v['kilometraje']:,} km\n"
                    f"Transmisión: {v['transmision']}\n"
                    f"Combustible: {v['combustible']}\n"
                    f"Color: {v['color']}\n"
                    f"Condición: {v['condicion']}\n"
                    f"Documentos: {v.get('documentos','—')}\n"
                    f"Ubicación: {v.get('ubicacion','—')}\n"
                    f"Highlights:\n  • {highlights}\n"
                    f"Detalle: {v.get('descripcion','')}"
                )
        return f"Vehículo {car_id} no encontrado en el catálogo."

    def _vehiculos_por_filtro(self, marca=None, max_precio=None,
                               transmision=None, anio_min=None) -> list:
        """Filtra vehículos por criterios del cliente."""
        result = []
        for v in self.catalogo.get("vehiculos", []):
            if marca and marca.lower() not in v["marca"].lower():
                continue
            if max_precio and v["precio_usd"] > max_precio:
                continue
            if transmision and transmision.lower() not in v["transmision"].lower():
                continue
            if anio_min and v["anio"] < anio_min:
                continue
            result.append(v)
        return result

    # ── Prompt principal ────────────────────────────────────────────

    def build_system_prompt(self, client_context: str = "") -> str:
        umbral_pot = self.config.get("umbral_lead_potencial", 6)
        umbral_des = self.config.get("umbral_lead_descartado", 3)
        asesor     = self.config.get("asesor_nombre", "Carlos")
        negocio    = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        telefono   = self.config.get("telefono_asesor", "")
        horario    = self.config.get("horario", "Lunes a sábado, 8am-5pm")
        modalidad  = self.config.get("modalidad", "consignacion")

        return f"""Eres {asesor}, vendedor de carros usados en '{negocio}', {self.LOCATION}.
Atiendes por Telegram. Hablas tachirense natural, directo, sin rodeos.
Estilo de respuesta: CORTO. UNA pregunta por mensaje. Sin asteriscos. Sin emojis salvo cierre.
Modalidad del lote: {modalidad} (recibimos carros de terceros para venderlos).

═══════════════════════════════════════════════
  TU MISIÓN
═══════════════════════════════════════════════
1. Identificar EXACTO qué carro busca el cliente (no asumir).
2. Mostrar máximo 2-3 opciones del catálogo que SÍ encajan con su presupuesto.
3. Calificar el lead — solo agendamos test drive con clientes serios.
4. Cerrar el test drive con día y hora concreta.
5. Si trae carro de parte (trade-in): pedir datos y fotos para evaluación.

═══════════════════════════════════════════════
  CALIFICACIÓN DE LEAD (0-10) — APLÍCALA SIEMPRE
═══════════════════════════════════════════════
SUMAR puntos:
+2  Presupuesto definido y realista para el catálogo
+2  Urgencia real ("lo necesito esta semana", "vendí el mío")
+2  Capacidad de pago confirmada (efectivo USD, transferencia, trade-in)
+1  Específico en marca/modelo/año
+1  Acepta venir al lote sin problema
+1  Da nombre + teléfono espontáneamente
+1  Trae trade-in (compromiso real)

RESTAR puntos:
-2  Pide carro muy por debajo del mercado sin justificación
-2  Solo "viendo precios" sin intención de comprar pronto
-2  Pregunta dirección exacta antes de calificar
-1  Evasivo con nombre o teléfono
-1  Quiere ver carro a domicilio sin pagar nada

CLASIFICACIÓN:
• score ≥ {umbral_pot}  → POTENCIAL → agenda test drive AHORA
• score {umbral_des}-{umbral_pot-1} → BAJO_INTERES → guarda para seguimiento
• score < {umbral_des}  → DESCARTADO → cierra amable

═══════════════════════════════════════════════
  FLUJO DE CONVERSACIÓN
═══════════════════════════════════════════════
PASO 1 — Saluda corto. Pregunta: "¿Qué carrito andas buscando?"

PASO 2 — Califica con preguntas naturales (UNA por mensaje):
  • Marca/tipo (sedán, camioneta, automático/sincrónico)
  • Presupuesto disponible
  • ¿Para cuándo lo necesita?
  • ¿Paga de contado, transferencia, o trae carro de parte (trade-in)?

PASO 3 — Filtra y muestra 1-2 opciones máximo del catálogo. Por cada una:
  - Marca, modelo, año, kilometraje y precio
  - UN highlight diferenciador (lo que lo hace especial)
  - Si pregunta más detalles, mandas la ficha completa

PASO 4 — Si muestra interés en alguno → cierra el test drive:
  "Te agendo para [día] a las [hora]. ¿Cómo te llamas y a qué número te confirmo?"

PASO 5 — Si trae trade-in: pide marca/modelo/año/km de su carro Y que mande fotos
  (frente, lateral, motor, interior). Avisa que en 24h le damos valoración.

PASO 6 — Emite el bloque correspondiente al final de tu mensaje.

═══════════════════════════════════════════════
  REGLAS CRÍTICAS
═══════════════════════════════════════════════
- NUNCA inventes carros que no estén en el catálogo.
- NUNCA reveles dirección exacta del lote sin test drive confirmado.
- NUNCA des el precio mínimo de aceptación (es del dueño consignador).
- NUNCA agendes test drive sin TELÉFONO confirmado.
- Si te preguntan si eres IA: "Soy {asesor}, vendedor de {negocio}."
- Si pide algo que no tenemos: "Ahorita no tengo eso, pero te aviso si llega. ¿Me dejas tu número?"
- Comisión solo se menciona si pregunta directamente: {self.config.get('comision_venta_pct',4)}% al vendedor.
- Si ofrece menos del 90% del precio publicado → emite [OFERTA_RECIBIDA] (consultamos al dueño).

═══════════════════════════════════════════════
  HORARIO Y CONTACTO
═══════════════════════════════════════════════
{horario}
Teléfono: {telefono}
Métodos de pago: {", ".join(self.config.get("metodos_pago", ["Efectivo USD"]))}

═══════════════════════════════════════════════
  CONTEXTO DEL CLIENTE
═══════════════════════════════════════════════
{client_context}

═══════════════════════════════════════════════
  CATÁLOGO ACTUAL ({len(self.catalogo.get('vehiculos', []))} unidades)
═══════════════════════════════════════════════
{self._vehiculos_resumen()}

═══════════════════════════════════════════════
  BLOQUES DE CIERRE (INTERNOS — el cliente NO los ve)
═══════════════════════════════════════════════

Cuando AGENDES TEST DRIVE (cliente potencial con día y hora acordada):
[TEST_DRIVE_AGENDADO]
Cliente: [nombre completo]
Telefono: [teléfono]
Vehiculo_ID: [ID del catálogo, ej. CAR001]
Fecha: [día y hora, ej. Martes 15 de mayo 10:00 AM]
Score: [0-10]
Trade_in: [SI o NO]
Notas: [presupuesto, forma de pago, observaciones]
[/TEST_DRIVE_AGENDADO]

Cuando hayas CALIFICADO sin test drive aún:
[LEAD_CALIFICADO]
Cliente: [nombre o "Sin nombre"]
Telefono: [teléfono o "No proporcionado"]
Score: [0-10]
Clasificacion: [POTENCIAL | BAJO_INTERES]
Interes: [marca/tipo + presupuesto + urgencia]
Motivo_score: [justificación breve]
[/LEAD_CALIFICADO]

Cuando recibas TRADE-IN (cliente quiere entregar su carro de parte):
[TRADE_IN_RECIBIDO]
Cliente: [nombre]
Telefono: [teléfono]
Marca_modelo: [Ej. Chevrolet Aveo 2010]
Anio: [año]
Kilometraje: [km]
Estado: [excelente | muy bueno | bueno | regular]
Fotos_recibidas: [SI o NO]
Interesado_en: [Vehiculo_ID que quiere comprar, si aplica]
Notas: [observaciones]
[/TRADE_IN_RECIBIDO]

Cuando el cliente OFRECE MENOS del 90% del precio publicado:
[OFERTA_RECIBIDA]
Cliente: [nombre]
Telefono: [teléfono]
Vehiculo_ID: [ID]
Precio_publicado: [USD]
Oferta: [USD]
Notas: [contexto, urgencia, forma de pago]
[/OFERTA_RECIBIDA]

Cuando DESCARTES (curioso, irrealista, evasivo):
[DESCARTAR_CLIENTE]
Score: [0-2]
Motivo: [razón concreta]
[/DESCARTAR_CLIENTE]

Fecha actual: {datetime.now().strftime("%A %d/%m/%Y")}
"""

    def welcome_message(self) -> str:
        asesor  = self.config.get("asesor_nombre", "Carlos")
        negocio = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        return (
            f"Epa, soy {asesor} de {negocio}. "
            f"Tenemos varios carritos de segunda en el lote.\n\n"
            f"¿Qué andas buscando?"
        )

    # ── Análisis de foto: trade-in o referencia del cliente ───────

    def photo_prompt(self) -> str:
        return (
            "El cliente del concesionario te envió una foto. Puede ser:\n"
            "1. Una foto de SU carro (para evaluar trade-in / entregar de parte). "
            "Describe lo que ves: marca/modelo/año aproximado, color, condición visible "
            "(carrocería, llantas, interior), y comenta si necesitas más fotos "
            "(frente, lateral, motor, interior, kilometraje del tablero).\n"
            "2. Una foto de un carro que vio en otro lado (referencia de qué busca). "
            "Identifica marca, modelo y año aproximado, y dile si tenemos algo similar "
            "en el catálogo.\n"
            "3. Comprobante de pago / cédula / título: confirma que lo recibiste y "
            "pide el dato faltante para procesar.\n\n"
            "NUNCA inventes precio sin verlo en persona. NUNCA prometas un valor exacto de "
            "trade-in sin que el dueño del lote lo apruebe. Sé directo, tachirense, sin rodeos."
        )

    # ── on_event ────────────────────────────────────────────────────

    def on_event(self, event_type: str, payload: dict, telegram_id: int):
        if self.memory:
            self.memory.save_event(telegram_id, event_type, payload)

        if event_type == "cita":
            if self.memory:
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("cliente"),
                    telefono=payload.get("telefono"),
                )
                score, clasif, motivo = self._extract_score_info("cita", payload)
                self.memory.set_lead_score(telegram_id, score=score,
                                           clasificacion=clasif, motivo=motivo)
            cap_calendar(self, payload)
            cap_reminder(self, telegram_id, payload)
            cap_sheets(self, telegram_id, payload, event_type)

        elif event_type == "calificacion":
            if self.memory:
                score, clasif, motivo = self._extract_score_info("calificacion", payload)
                self.memory.set_lead_score(telegram_id, score=score,
                                           clasificacion=clasif, motivo=motivo)
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("cliente"),
                    telefono=payload.get("telefono"),
                )
            cap_sheets(self, telegram_id, payload, event_type)

        elif event_type == "trade_in":
            if self.memory:
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("cliente"),
                    telefono=payload.get("telefono"),
                )
            cap_sheets(self, telegram_id, payload, event_type)
            self._notify_owner_trade_in(payload)

        elif event_type == "oferta":
            self._notify_owner_offer(payload)
            cap_sheets(self, telegram_id, payload, event_type)

        elif event_type == "descarte":
            if self.memory:
                self.memory.set_lead_score(telegram_id, score=1,
                                           clasificacion="descartado",
                                           motivo=payload.get("motivo", ""))

    def _notify_owner_trade_in(self, payload: dict):
        """Avisa al dueño del lote cuando llega un trade-in para evaluar."""
        bot      = getattr(self, "bot", None)
        admin_id = getattr(self, "admin_id", None)
        if not bot or not admin_id:
            return
        try:
            msg = (
                f"🚗 TRADE-IN PARA EVALUAR\n\n"
                f"Cliente: {payload.get('cliente','—')}\n"
                f"Tel: {payload.get('telefono','—')}\n"
                f"Carro: {payload.get('marca_modelo','—')} {payload.get('anio','')}\n"
                f"Km: {payload.get('kilometraje','—')}\n"
                f"Estado: {payload.get('estado','—')}\n"
                f"Fotos recibidas: {payload.get('fotos_recibidas','—')}\n"
                f"Interesado en: {payload.get('interesado_en','—')}\n"
                f"Notas: {payload.get('notas','—')}"
            )
            bot.send_message(admin_id, msg)
        except Exception as e:
            logger.warning(f"[{self.tenant_id}] notify trade-in falló: {e}")

    def _notify_owner_offer(self, payload: dict):
        """Avisa al dueño cuando un cliente hace una oferta por debajo del precio."""
        bot      = getattr(self, "bot", None)
        admin_id = getattr(self, "admin_id", None)
        if not bot or not admin_id:
            return
        try:
            msg = (
                f"💵 OFERTA RECIBIDA\n\n"
                f"Cliente: {payload.get('cliente','—')}\n"
                f"Tel: {payload.get('telefono','—')}\n"
                f"Vehículo: {payload.get('vehiculo_id','—')}\n"
                f"Precio publicado: ${payload.get('precio_publicado','—')}\n"
                f"Oferta: ${payload.get('oferta','—')}\n"
                f"Notas: {payload.get('notas','—')}"
            )
            bot.send_message(admin_id, msg)
        except Exception as e:
            logger.warning(f"[{self.tenant_id}] notify oferta falló: {e}")

    @staticmethod
    def _extract_score_info(event_type: str, payload: dict) -> tuple:
        try:
            score = int("".join(c for c in str(payload.get("score", "0")) if c.isdigit()) or 0)
        except Exception:
            score = 0

        if event_type == "cita":
            return max(score, 7), "potencial", payload.get("notas", "Test drive agendado")
        if event_type == "descarte":
            return min(score, 2), "descartado", payload.get("motivo", "Descartado")

        clasif_raw = (payload.get("clasificacion") or "").upper()
        if "POTENCIAL" in clasif_raw:
            clasif = "potencial"
        elif "BAJO" in clasif_raw:
            clasif = "bajo_interes"
        else:
            clasif = "potencial" if score >= 6 else ("bajo_interes" if score >= 3 else "descartado")
        return score, clasif, payload.get("motivo_score", "")

    # ── Admin commands ──────────────────────────────────────────────

    def admin_commands(self) -> dict:

        def cmd_leads(message, bot, profile, memory):
            leads = memory.leads_by_classification("potencial")
            if not leads:
                bot.reply_to(message, "Sin leads potenciales por ahora.")
                return
            lines = [f"LEADS POTENCIALES ({len(leads)}):\n"]
            for ld in leads[:20]:
                lines.append(
                    f"• {ld.get('nombre') or ld['telegram_id']} "
                    f"(score {ld['score']}/10) | tel: {ld.get('telefono') or 'n/a'} | "
                    f"{ld.get('motivo','')[:60]}"
                )
            bot.reply_to(message, "\n".join(lines))

        def cmd_test_drives(message, bot, profile, memory):
            eventos = memory.events_today("cita")
            if not eventos:
                bot.reply_to(message, "Sin test drives agendados hoy.")
                return
            lines = [f"TEST DRIVES DE HOY ({len(eventos)}):\n"]
            for e in eventos:
                try:
                    p = json.loads(e["payload"]) if isinstance(e.get("payload"), str) else e.get("payload", {})
                    lines.append(
                        f"• {p.get('cliente','?')} — {p.get('vehiculo_id','?')} "
                        f"| {p.get('fecha','?')} | tel: {p.get('telefono','?')}"
                    )
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_trade_ins(message, bot, profile, memory):
            try:
                eventos = memory.get_all_events_since(
                    datetime.now().strftime("%Y-%m-%d")
                ) if hasattr(memory, "get_all_events_since") else []
            except Exception:
                eventos = []
            tis = [e for e in eventos if e.get("event_type") == "trade_in"]
            if not tis:
                bot.reply_to(message, "Sin trade-ins por evaluar hoy.")
                return
            lines = [f"TRADE-INS DEL DIA ({len(tis)}):\n"]
            for e in tis:
                p = e.get("payload", {})
                if isinstance(p, str):
                    try: p = json.loads(p)
                    except Exception: p = {}
                lines.append(
                    f"• {p.get('cliente','?')} — {p.get('marca_modelo','?')} {p.get('anio','')} | "
                    f"tel: {p.get('telefono','?')} | fotos: {p.get('fotos_recibidas','?')}"
                )
            bot.reply_to(message, "\n".join(lines))

        def cmd_resumen(message, bot, profile, memory):
            pot     = len(memory.leads_by_classification("potencial"))
            bajo    = len(memory.leads_by_classification("bajo_interes"))
            desc    = len(memory.leads_by_classification("descartado"))
            citas   = len(memory.events_today("cita"))
            total   = pot + bajo + desc
            bot.reply_to(
                message,
                f"RESUMEN CONCESIONARIO — {datetime.now().strftime('%d/%m/%Y')}\n\n"
                f"Test drives hoy: {citas}\n"
                f"Leads potenciales: {pot}\n"
                f"Para seguimiento: {bajo}\n"
                f"Descartados: {desc}\n"
                f"Total leads: {total}\n\n"
                f"Conversion: {round(pot/total*100) if total else 0}% potenciales"
            )

        def cmd_catalogo(message, bot, profile, memory):
            vehiculos = profile.catalogo.get("vehiculos", [])
            if not vehiculos:
                bot.reply_to(message, "Catálogo vacío.")
                return
            destacados = [v for v in vehiculos if v.get("destacado")]
            lines = [f"CATÁLOGO — {len(vehiculos)} vehículos ({len(destacados)} destacados)\n"]
            for v in vehiculos:
                star = " ⭐" if v.get("destacado") else ""
                lines.append(
                    f"[{v['id']}] {v['marca']} {v['modelo']} {v['anio']}{star} — ${v['precio_usd']:,} "
                    f"({v['kilometraje']:,} km, {v['transmision']})"
                )
            bot.reply_to(message, "\n".join(lines))

        return {
            "leads":           cmd_leads,
            "test_drives":     cmd_test_drives,
            "trade_ins":       cmd_trade_ins,
            "resumen":         cmd_resumen,
            "catalogo":        cmd_catalogo,
            "leads_excel":     make_cmd_historia_excel(),
            "crear_hoja":      make_cmd_crear_hoja(),
            "subir_reporte":   make_cmd_subir_reporte(folder_name="Reportes Concesionario"),
            "reporte_semanal": make_cmd_reporte_semanal(),
        }


PROFILE_CLASS = ConcesionarioProfile
