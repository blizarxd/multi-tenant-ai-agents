import os
import io
import re
import time
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from profiles._base_profile import BaseProfile

logger = logging.getLogger(__name__)


def get_meses_deuda(conn, socio_id: int) -> list[str]:
    # Hacemos los cálculos en base a la hora de Venezuela (UTC-4)
    now = datetime.now(timezone(timedelta(hours=-4)))
    meses_deuda = []
    
    row = conn.execute("SELECT fecha_ingreso FROM socios WHERE id = ?", (socio_id,)).fetchone()
    if not row:
        return []
    
    fecha_ingreso_str = row["fecha_ingreso"]
    try:
        # fecha_ingreso puede venir como YYYY-MM-DD o con hora adjunta
        ingreso_date = datetime.strptime(fecha_ingreso_str.split()[0], "%Y-%m-%d")
    except Exception:
        ingreso_date = now
        
    ingreso_limit = datetime(ingreso_date.year, ingreso_date.month, 1)
    
    # Revisamos los últimos 12 meses hacia atrás
    for i in range(12):
        m = now.month - 1 - i
        year = now.year + m // 12
        month = m % 12 + 1
        
        mes_dt = datetime(year, month, 1)
        if mes_dt < ingreso_limit:
            continue
            
        mes_str = f"{year}-{month:02d}"
        pago = conn.execute("SELECT id FROM pagos WHERE socio_id = ? AND mes_correspondiente = ?", (socio_id, mes_str)).fetchone()
        if not pago:
            meses_deuda.append(mes_str)
            
    return meses_deuda


def normalizar_cedula(cedula: str) -> str | None:
    if not cedula:
        return None
    # Eliminar V, -, . y espacios en blanco
    cleaned = re.sub(r'[V\-\.]', '', cedula, flags=re.IGNORECASE).strip()
    if not re.match(r'^\d{6,9}$', cleaned):
        return None
    # Rellenar con ceros a la izquierda hasta 8 caracteres
    s = cleaned.zfill(8)
    return f"V-{s[:-6]}.{s[-6:-3]}.{s[-3:]}"


class ClubSocialProfile(BaseProfile):
    NAME = "club_social"
    DISPLAY_NAME = "Club Centro Latino"
    LOCATION = "San Cristóbal, Estado Táchira"

    EVENT_TAGS = {
        "VINCULAR_SOCIO": "vincular_socio",
        "REGISTRAR_PAGO": "registrar_pago",
        "RESERVAR_ALQUILER": "reservar_alquiler",
        "RESERVAR_KARAOKE": "reservar_karaoke",
        "CONFIRMAR_ASISTENCIA": "confirmar_asistencia",
        "CREAR_TICKET": "crear_ticket",
        "ACTUALIZAR_DATOS": "actualizar_datos",
        "GUARDAR_MEMORIA": "guardar_memoria",
        "BORRAR_MEMORIA": "borrar_memoria",
        "ENVIAR_IMAGEN_MENU": "enviar_imagen_menu",
        "ADMIN_RESPONDER_TICKET": "admin_responder_ticket",
        "ADMIN_ENVIAR_ALERTA_PAGO": "admin_enviar_alerta_pago",
        "ADMIN_GENERAR_REPORTE": "admin_generar_reporte"
    }

    def _hora_vzla(self):
        return datetime.now(timezone(timedelta(hours=-4)))

    def format_mes(self, mes_str: str) -> str:
        if not mes_str or "-" not in mes_str:
            return ""
        parts = mes_str.split("-")
        year = parts[0]
        month = parts[1]
        meses_names = [
            'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
            'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
        ]
        try:
            idx = int(month) - 1
            if 0 <= idx < 12:
                return f"{meses_names[idx]} {year}"
        except Exception:
            pass
        return mes_str

    def get_user_context(self, telegram_id: int) -> dict:
        """Obtiene el contexto de base de datos para el telegram_id dado."""
        with self.memory._conn() as conn:
            socio_row = conn.execute(
                "SELECT * FROM socios WHERE telegram_id = ? AND estado = ?",
                (str(telegram_id), 'activo')
            ).fetchone()
            
            is_admin = str(telegram_id) == str(self.admin_id) or \
                       conn.execute("SELECT id FROM admins WHERE telegram_id = ?", (str(telegram_id),)).fetchone() is not None
            
            if socio_row:
                socio = dict(socio_row)
                meses_deuda = get_meses_deuda(conn, socio["id"])
                solvente = len(meses_deuda) == 0
                socio["meses_deuda"] = meses_deuda
                socio["solvente"] = solvente
                return {"socio": socio, "is_admin": is_admin}
                
            return {"socio": None, "is_admin": is_admin}

    def extra_client_context(self, telegram_id: int) -> str:
        """Genera el contexto específico del club para inyectar en el prompt de la IA."""
        now = self._hora_vzla()
        
        wd_es = {
            "Monday": "lunes", "Tuesday": "martes", "Wednesday": "miércoles",
            "Thursday": "jueves", "Friday": "viernes", "Saturday": "sábado", "Sunday": "domingo"
        }
        mn_es = {
            "January": "enero", "February": "febrero", "March": "marzo", "April": "abril",
            "May": "mayo", "June": "junio", "July": "julio", "August": "agosto",
            "September": "septiembre", "October": "octubre", "November": "noviembre", "December": "diciembre"
        }
        day_name = wd_es.get(now.strftime("%A"), now.strftime("%A"))
        month_name = mn_es.get(now.strftime("%B"), now.strftime("%B"))
        date_str = f"{day_name}, {now.day} de {month_name} de {now.year}"
        time_str = now.strftime("%I:%M %p")

        context_lines = [
            f"\nHora actual en Táchira: {time_str} | Fecha: {date_str}"
        ]

        with self.memory._conn() as conn:
            # Asegurar que existan las tablas de partner_facts
            conn.execute("""
                CREATE TABLE IF NOT EXISTS partner_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id TEXT,
                    fact TEXT,
                    created_at DATETIME DEFAULT (datetime('now'))
                )
            """)
            
            # Obtener datos de memoria
            facts_rows = conn.execute(
                "SELECT fact FROM partner_facts WHERE telegram_id = ? ORDER BY id ASC",
                (str(telegram_id),)
            ).fetchall()
            
            if facts_rows:
                context_lines.append("\nDATOS GUARDADOS DE SU PREFERENCIA (Memoria a largo plazo):")
                for f in facts_rows:
                    context_lines.append(f"- {f['fact']}")
                context_lines.append("Utilice esta información con total tacto y amabilidad para ofrecerle una atención personalizada y recordarle sus gustos de manera servicial y natural.")

            # Obtener estatus del socio
            user_ctx = self.get_user_context(telegram_id)
            socio = user_ctx.get("socio")
            is_admin = user_ctx.get("is_admin")

            if socio:
                solvente_str = "SOLVENTE (al día con la administración)" if socio["solvente"] else "INSOLVENTE (con deuda pendiente)"
                context_lines.append(f"""
CONTEXTO DEL SOCIO CONECTADO:
- Nombre completo: {socio['nombre']} {socio['apellido']}
- Cédula: {socio['cedula']}
- Categoría de Socio: {socio.get('tipo_socio', 'titular')}
- Estado Financiero: {solvente_str}
- Teléfono: {socio.get('telefono') or 'No registrado'}
- Email: {socio.get('email') or 'No registrado'}
""")
                if not socio["solvente"]:
                    meses_deuda_str = ", ".join([self.format_mes(m) for m in socio["meses_deuda"]])
                    mensualidad = self.config.get("mensualidad", 16.50)
                    context_lines.append(f"- Mensualidades pendientes: {meses_deuda_str} (cuota mensual: ${mensualidad} USD)")

                context_lines.append("""
Instrucciones para Socios:
Trate al socio con el máximo respeto y deferencia que se merece como copropietario del club. Diríjase a él/ella por su nombre de pila de forma respetuosa (ej: "Estimado Señor/Señora [Nombre]"). Bríndele asistencia prioritaria para reportar pagos, reservar instalaciones con su descuento exclusivo del 20%, o verificar torneos.
""")
            else:
                context_lines.append("""
CONTEXTO DEL VISITANTE (Público General / Cliente Potencial):
- No se ha identificado como socio ni ha vinculado su cuenta.

Instrucciones para Visitantes (Enfoque de Ventas):
Actúa como promotora activa. Tu objetivo es enamorarlo de las instalaciones para que alquile o se asocie:
1. Alquiler de espacios: Caney Grande (80 personas, $150 regular / $120 socio, parrillera incluida), Caneys Pequeños (30 personas, $80/$64 — 3 unidades), Salón de Fiestas Principal (150 personas, $250/$200, acústica profesional), Salones Secundarios (60 personas, $120/$96 — 2 unidades), Parque Infantil (50 personas, $50/$40).
2. Karaoke: Noches de Karaoke los viernes (libre para socios). Equipo disponible como add-on para eventos privados ($40 / $32 socios) — sonido profesional, pantalla LED, 2 micrófonos.
3. Deportes: béisbol, softball, tenis, futbolito, básquetbol, voleibol, bolas criollas, billar, dominó y piscina olímpica con salvavidas.
4. Gastronomía: 2 restaurantes, 5 puestos de comida rápida y la Fuente de Soda "Splash" frente a la piscina.
5. Conversión a Socio ($16.50/mes): descuento 20% en alquileres, piscina libre, torneos, Noches de Karaoke, entrada preferencial a eventos y soporte VIP.
6. Descripción: destaca seguridad privada, áreas verdes, estacionamiento garantizado en San Cristóbal.
7. Demo: Si el usuario pregunta cómo probar el sistema como socio, indícale que puede escribir "mi cédula es V-19.776.759" para simular la vinculación inmediata.
""")

            if is_admin:
                now_y_m = f"{now.year}-{now.month:02d}"
                total_socios = conn.execute("SELECT COUNT(*) as c FROM socios WHERE estado = 'activo'").fetchone()["c"]
                pagaron = conn.execute("SELECT COUNT(DISTINCT socio_id) as c FROM pagos WHERE mes_correspondiente = ?", (now_y_m,)).fetchone()["c"]
                recaudacion = conn.execute("SELECT SUM(monto) as t FROM pagos WHERE mes_correspondiente = ?", (now_y_m,)).fetchone()["t"] or 0
                eventos_prox = conn.execute("SELECT COUNT(*) as c FROM eventos WHERE estado = 'activo' AND fecha_evento >= date('now')").fetchone()["c"]
                reservas_pend = conn.execute("SELECT COUNT(*) as c FROM reservas WHERE estado = 'pendiente'").fetchone()["c"]
                tickets_abiertos = conn.execute("SELECT COUNT(*) as c FROM tickets_soporte WHERE estado IN ('abierto', 'en_proceso')").fetchone()["c"]

                pending_tickets = conn.execute("""
                    SELECT t.codigo, s.nombre, s.apellido, t.categoria, t.asunto, t.mensaje 
                    FROM tickets_soporte t 
                    LEFT JOIN socios s ON t.socio_id = s.id 
                    WHERE t.estado = 'abierto' 
                    ORDER BY t.id DESC LIMIT 5
                """).fetchall()

                pending_reservations = conn.execute("""
                    SELECT r.codigo, s.nombre, s.apellido, i.nombre as instalacion, r.fecha_reserva, r.hora_inicio, r.hora_fin
                    FROM reservas r 
                    JOIN socios s ON r.socio_id = s.id 
                    JOIN instalaciones i ON r.instalacion_id = i.id 
                    WHERE r.estado = 'pendiente' 
                    ORDER BY r.id DESC LIMIT 5
                """).fetchall()

                context_lines.append(f"""
CONTEXTO DE ADMINISTRADOR (DASHBOARD REAL-TIME):
- Total socios activos: {total_socios}
- Solventes este mes ({now_y_m}): {pagaron}
- Insolventes este mes: {total_socios - pagaron}
- Recaudación del mes: ${recaudacion:.2f} USD
- Eventos activos programados: {eventos_prox}
- Reservas pendientes de aprobación: {reservas_pend}
- Tickets de soporte abiertos: {tickets_abiertos}
""")
                if pending_tickets:
                    context_lines.append("TICKETS DE SOPORTE ABIERTOS (Últimos 5):")
                    for tk in pending_tickets:
                        s_name = f"{tk['nombre']} {tk['apellido']}" if tk['nombre'] else "No socio"
                        context_lines.append(f"- [{tk['codigo']}] Solicitante: {s_name} | Cat: {tk['categoria']} | Asunto: {tk['asunto']} | Mensaje: {tk['mensaje']}")
                
                if pending_reservations:
                    context_lines.append("\nRESERVAS PENDIENTES DE APROBACIÓN (Últimos 5):")
                    for rs in pending_reservations:
                        context_lines.append(f"- [{rs['codigo']}] Socio: {rs['nombre']} {rs['apellido']} | Lugar: {rs['instalacion']} | Fecha: {rs['fecha_reserva']} ({rs['hora_inicio']} - {rs['hora_fin']})")

                context_lines.append("""
Instrucciones de Administrador:
El usuario es Administrador. Tienes activas las herramientas de gestión del club. Si te solicita reportes financieros en Excel/CSV, lista de deudores, enviar un cobro masivo por Telegram o responder tickets de soporte, indícale de inmediato que procesarás la solicitud y ejecuta la función administrativa correspondiente.
""")

        return "\n".join(context_lines)

    def build_system_prompt(self, client_context: str = "") -> str:
        return f"""Eres TINO, la asistente y secretaria virtual de atención al cliente del prestigioso Club Centro Latino en San Cristóbal, Estado Táchira.
Tu tono de voz es sumamente amable, educado, hospitalario y profesional, pero a la vez muy natural y cercano (evita sonar rígida o robótica). Eres la cara del club, por lo que te distingues por tu excelente servicio al cliente, educación y cortesía.

═══════════════════════════════════════════════
NORMAS DE CONVERSACIÓN
═══════════════════════════════════════════════
- RESPUESTAS CORTAS: máximo 3-4 líneas por mensaje. Si hay mucha info, entregala en partes — pregunta qué quiere saber primero. NUNCA envíes bloques largos de texto de una sola vez.
- INICIO DE CONVERSACIÓN OBLIGATORIO: si el usuario no está identificado como socio, tu PRIMER mensaje es SOLO preguntar si es socio o visitante. Nada más. Espera su respuesta antes de dar cualquier otra información.
- No repitas saludos si ya saludaste en la sesión. Entra directo a responder.
- Evita el lenguaje callejero: "mi pana", "brutal", "epa", "pana" (queja directa del presidente, prohibidas).
- Expresiones positivas moderadas permitidas: "excelente", "chévere", "buenísimo", "con todo gusto".
- Si dan mucha info de golpe, responde lo más urgente primero y pide confirmación para lo demás.

═══════════════════════════════════════════════
REGLAS DE ORO DE ATENCIÓN
═══════════════════════════════════════════════
1. Responde siempre con amabilidad. Presenta información del club de forma atractiva y estructurada (viñetas o negritas).
2. Si solicitan reserva o alquiler, pide fecha (YYYY-MM-DD), hora inicio/fin e instalación. Los socios solventes tienen 20% de descuento automático.
3. Si un socio reporta pago móvil o Zelle con referencia, emite inmediatamente el bloque [REGISTRAR_PAGO].
4. Para consultas gastronómicas (restaurantes, comida rápida, fuente de soda), usa [ENVIAR_IMAGEN_MENU] con la foto del menú correspondiente.
5. Si preguntan si eres IA, responde: "Soy TINO, su asistente virtual del Club Centro Latino. ¿En qué le puedo asistir?"
6. Si recibes preferencias o pasatiempos del usuario, guárdalos con [GUARDAR_MEMORIA].
7. Si un usuario no identificado da su cédula (ej: "mi cédula es V-12.345.678"), emite el bloque [VINCULAR_SOCIO].
8. Si alguien pregunta por karaoke: infórmale que los viernes hay Noches de Karaoke abiertas para socios y acompañantes, y que el equipo se puede alquilar como add-on para eventos privados ($40 regular / $32 para socios). Si quiere reservar el equipo de karaoke, emite [RESERVAR_KARAOKE].

═══════════════════════════════════════════════
INSTALACIONES Y ALQUILER (precios en USD)
═══════════════════════════════════════════════
- Caney Grande — capacidad 80 personas — $150 regular / $120 socio (incluye parrillera)
- Caneys Pequeños — capacidad 30 personas c/u — $80 regular / $64 socio (3 unidades disponibles)
- Salón de Fiestas Principal — capacidad 150 personas — $250 regular / $200 socio (acústica profesional, A/C)
- Salones Secundarios — capacidad 60 personas c/u — $120 regular / $96 socio (A/C, 2 unidades)
- Parque Infantil — hasta 50 personas — $50 regular / $40 socio (columpios, toboganes, piñatas)
- Equipo de Karaoke (add-on) — $40 regular / $32 socio — sistema sonido profesional, pantalla LED, 2 micrófonos inalámbricos
  Los socios solventes obtienen 20% de descuento en todos los alquileres.
  Contacto administración: 0276-351-0000

═══════════════════════════════════════════════
DEPORTES Y RECREACIÓN
═══════════════════════════════════════════════
El club cuenta con instalaciones deportivas de primer nivel, acceso libre para socios solventes:
- ⚾ Campo de Béisbol y Softbol
- 🎾 Canchas de Tenis (arcilla y superficie rápida)
- ⚽ Canchas de Futbolito con grama artificial
- 🏀 Cancha de Básquetbol
- 🏐 Cancha de Voleibol
- 🎯 Canchas de Bolas Criollas
- 🎱 Sala de Pool y Billar
- 🎲 Sala de Dominó
- 🏊 Piscina Olímpica con zona infantil, trampolín y salvavidas
  Horario piscina: Lunes-Viernes 8:00 AM - 6:00 PM | Sábado-Domingo 7:00 AM - 7:00 PM
Los socios solventes tienen acceso libre a piscina y canchas. También pueden inscribir equipos en ligas y torneos internos.

═══════════════════════════════════════════════
GASTRONOMÍA
═══════════════════════════════════════════════
Horario general: 10:00 AM - 9:00 PM
- 2 Restaurantes Formales — platos a la carta, especialidades nacionales e internacionales (11 AM - 9 PM)
- 5 Puestos de Comida Rápida — hamburguesas, pizzas, empanadas (10 AM - 8 PM)
- Fuente de Soda "Splash" — frente a la piscina olímpica, batidos, jugos, cócteles y snacks (9 AM - 6 PM)
Para ver el menú, usa [ENVIAR_IMAGEN_MENU] con: menu_hamburguesas, menu_pizzas, menu_extras o menu_promos.

═══════════════════════════════════════════════
MEMBRESÍA — BENEFICIOS EXCLUSIVOS
═══════════════════════════════════════════════
Cuota mensual: $16.50 USD (pagar en los primeros días del mes)
Beneficios de ser socio solvente:
- 20% de descuento fijo en alquiler de cualquier instalación
- Acceso libre a piscina olímpica y áreas deportivas
- Inscripción de equipos en ligas y torneos internos
- Voz y voto en encuestas y decisiones administrativas
- Entrada preferencial a eventos y conciertos del club
- Noches de Karaoke incluidas (viernes) — solo socios y acompañantes
- Soporte prioritario VIP por este chat
Solicitar membresía: administración del club, presencialmente o al 0276-351-0000

═══════════════════════════════════════════════
KARAOKE — NOCHES DE VIERNES
═══════════════════════════════════════════════
Los viernes el club organiza Noches de Karaoke en el Salón Principal. Entrada libre para socios solventes y sus acompañantes.
El equipo de karaoke también puede alquilarse para eventos privados como add-on a cualquier espacio ($40 / $32 socios).
Equipo incluye: sistema de sonido profesional, pantalla LED grande, 2 micrófonos inalámbricos y catálogo de canciones actualizado.

═══════════════════════════════════════════════
GATILLOS DE EVENTOS (bloques invisibles al cliente — solo para el sistema)
═══════════════════════════════════════════════

[VINCULAR_SOCIO]
cedula: [cedula]
[/VINCULAR_SOCIO]

[REGISTRAR_PAGO]
metodo: [zelle o pago_movil]
referencia: [referencia]
[/REGISTRAR_PAGO]

[RESERVAR_ALQUILER]
instalacion_nombre: [nombre de instalación]
fecha: [YYYY-MM-DD]
hora_inicio: [HH:MM]
hora_fin: [HH:MM]
[/RESERVAR_ALQUILER]

[RESERVAR_KARAOKE]
fecha: [YYYY-MM-DD]
hora_inicio: [HH:MM]
hora_fin: [HH:MM]
espacio: [nombre del espacio donde se usará — opcional]
[/RESERVAR_KARAOKE]

[CONFIRMAR_ASISTENCIA]
evento_codigo: [código de evento]
[/CONFIRMAR_ASISTENCIA]

[CREAR_TICKET]
categoria: [pagos, instalaciones, eventos, locales, sugerencia o general]
asunto: [Asunto corto]
mensaje: [Descripción detallada]
[/CREAR_TICKET]

[ACTUALIZAR_DATOS]
telefono: [telefono]
email: [email]
[/ACTUALIZAR_DATOS]

[GUARDAR_MEMORIA]
fact: [hecho de preferencia o gusto]
[/GUARDAR_MEMORIA]

[BORRAR_MEMORIA]
[/BORRAR_MEMORIA]

[ENVIAR_IMAGEN_MENU]
media_name: [menu_pizzas, menu_hamburguesas, menu_extras o menu_promos]
[/ENVIAR_IMAGEN_MENU]

[ADMIN_RESPONDER_TICKET]
ticket_codigo: [codigo]
respuesta: [respuesta del admin]
[/ADMIN_RESPONDER_TICKET]

[ADMIN_ENVIAR_ALERTA_PAGO]
[/ADMIN_ENVIAR_ALERTA_PAGO]

[ADMIN_GENERAR_REPORTE]
tipo_reporte: [socios, pagos o reservas]
[/ADMIN_GENERAR_REPORTE]

═══════════════════════════════════════════════
CONTEXTO DEL CLIENTE Y DEL CLUB
═══════════════════════════════════════════════
{client_context}
"""

    def welcome_message(self) -> str:
        return (
            "¡Bienvenido al Club Centro Latino! Soy TINO, su asistente virtual.\n\n"
            "Para comenzar: ¿es usted *socio del club* o viene como *visitante*?"
        )

    def photo_prompt(self) -> str:
        return """El cliente ha enviado una imagen. 
Analiza la imagen cuidadosamente. Si es un comprobante de pago (como capture de Pago Móvil, transferencia bancaria, capture de Zelle, efectivo o recibo):
1. Extrae los datos clave del pago:
   - Método de pago (Zelle, Pago Móvil, Transferencia, etc.)
   - Referencia o número de transacción
   - Monto en USD o Bolívares
   - Fecha
2. Responde confirmando que estás procesando la conciliación bancaria y emite el bloque EXACTO de registro al final de tu respuesta:

[REGISTRAR_PAGO]
metodo: [zelle o pago_movil]
referencia: [número de referencia extraído]
[/REGISTRAR_PAGO]

Si la imagen NO es un comprobante de pago (por ejemplo, es la foto de un plato, una instalación, etc.), responde de manera conversacional acorde a la imagen y al contexto del club."""

    def on_event(self, event_type: str, payload: dict, telegram_id: int):
        if self.memory:
            self.memory.save_event(telegram_id, event_type, payload)
        
        logger.info(f"[{self.tenant_id}] Evento {event_type} de {telegram_id}: {payload}")
        
        # Obtener contexto de usuario para operaciones
        user_ctx = self.get_user_context(telegram_id)
        socio = user_ctx.get("socio")
        is_admin = user_ctx.get("is_admin")
        
        with self.memory._conn() as conn:
            if event_type == "vincular_socio":
                cedula = payload.get("cedula")
                cedula_norm = normalizar_cedula(cedula)
                if not cedula_norm:
                    self.bot.send_message(telegram_id, "❌ La cédula proporcionada no tiene un formato válido (Ejemplo: V-12.345.678).")
                    return
                
                socio_row = conn.execute("SELECT * FROM socios WHERE cedula = ?", (cedula_norm,)).fetchone()
                if not socio_row:
                    self.bot.send_message(telegram_id, f"❌ Lo siento, no encontré ningún socio activo registrado con la cédula {cedula_norm}. Por favor, verifique el número.")
                    return
                
                db_socio = dict(socio_row)
                if db_socio["telegram_id"] and db_socio["telegram_id"] != str(telegram_id):
                    self.bot.send_message(telegram_id, "❌ Esta cédula ya está vinculada a otra cuenta de Telegram. Si considera que es un error, por favor contacte a la administración.")
                    return
                
                conn.execute("UPDATE socios SET telegram_id = ? WHERE id = ?", (str(telegram_id), db_socio["id"]))
                self.bot.send_message(telegram_id, f"✨ *¡VINCULACIÓN EXITOSA!* ✅\n━━━━━━━━━━━━━━━━━━━━\n\nEstimado/a *{db_socio['nombre']} {db_socio['apellido']}*, su cuenta ha sido vinculada correctamente. Desde ahora puedo asistirle en la consulta de su solvencia, reserva de áreas y demás servicios del club.", parse_mode="Markdown")
                
                # Forzar recreación de sesión para actualizar system prompt con el socio
                if hasattr(self, "agent") and self.agent:
                    self.agent.sessions.reset(telegram_id)

            elif event_type == "registrar_pago":
                if not socio:
                    self.bot.send_message(telegram_id, "❌ Debe vincular su cuenta de socio primero para reportar pagos.")
                    return
                
                metodo = payload.get("metodo", "pago_movil").strip().lower()
                referencia = payload.get("referencia", "").strip()
                
                if not referencia:
                    self.bot.send_message(telegram_id, "❌ No se pudo extraer la referencia del pago. Por favor, indíquela por escrito.")
                    return
                
                mensualidad = self.config.get("mensualidad", 16.50)
                meses_deuda = get_meses_deuda(conn, socio["id"])
                
                now = self._hora_vzla()
                mes_actual = f"{now.year}-{now.month:02d}"
                meses_a_pagar = meses_deuda if meses_deuda else [mes_actual]
                total_monto = len(meses_a_pagar) * mensualidad
                
                for mes in meses_a_pagar:
                    conn.execute("""
                        INSERT INTO pagos (socio_id, monto, mes_correspondiente, metodo_pago, referencia, registrado_por)
                        VALUES (?, ?, ?, ?, ?, 'auto_api')
                    """, (socio["id"], mensualidad, mes, metodo, referencia))
                
                met_text = "Zelle 💲" if metodo == "zelle" else "Pago Móvil 📱"
                meses_nombres = ", ".join([self.format_mes(m) for m in meses_a_pagar])
                
                # Mensaje de éxito al usuario
                exito_msg = f"""✨ *¡PAGO VERIFICADO AL INSTANTE!* ✅
━━━━━━━━━━━━━━━━━━━━━━

🏛️ *Conciliación bancaria en tiempo real exitosa.*

👤 *Socio:* {socio['nombre']} {socio['apellido']}
🪪 *Cédula:* {socio['cedula']}
💳 *Método:* {met_text}
📋 *Ref/Trans:* `{referencia}`
💵 *Monto total conciliado:* ${total_monto:.2f} USD
📅 *Meses procesados:* {meses_nombres}

🍀 Estado del socio: *SOLVENTE* ✅

¡Su acceso al club, reservas y beneficios están habilitados inmediatamente! 🎉"""
                self.bot.send_message(telegram_id, exito_msg, parse_mode="Markdown")
                
                # Notificar al administrador
                try:
                    admin_msg = f"🤖 *CONCILIACIÓN AUTOMÁTICA EXITOSA*\n\n👤 Socio: {socio['nombre']} {socio['apellido']} ({socio['cedula']})\n💳 Método: {met_text}\n📋 Ref: {referencia}\n💵 Monto: ${total_monto:.2f} USD\n📅 Meses pagados: {meses_nombres}\n\n_Estado actualizado automáticamente a Solvente._"
                    self.bot.send_message(self.admin_id, admin_msg, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"No se pudo notificar al admin del pago: {e}")
                
                # Forzar recreación de sesión para actualizar system prompt a solvente
                if hasattr(self, "agent") and self.agent:
                    self.agent.sessions.reset(telegram_id)

            elif event_type == "reservar_alquiler":
                if not socio:
                    self.bot.send_message(telegram_id, "❌ Debe vincular su cuenta de socio primero para realizar reservas.")
                    return
                if not socio["solvente"]:
                    self.bot.send_message(telegram_id, "❌ Lo siento, debe estar *solvente* con la administración para poder reservar instalaciones.")
                    return
                
                instalacion_nombre = payload.get("instalacion_nombre", "")
                fecha = payload.get("fecha", "").strip()
                hora_inicio = payload.get("hora_inicio", "08:00").strip()
                hora_fin = payload.get("hora_fin", "22:00").strip()
                
                inst_row = conn.execute(
                    "SELECT * FROM instalaciones WHERE nombre LIKE ? AND categoria = 'alquiler'",
                    (f"%{instalacion_nombre}%",)
                ).fetchone()
                
                if not inst_row:
                    self.bot.send_message(telegram_id, f"❌ Lo siento, no encontré la instalación '{instalacion_nombre}'. Por favor, verifique el catálogo.")
                    return
                
                # Chequear conflictos
                conflicto = conn.execute(
                    "SELECT * FROM reservas WHERE instalacion_id = ? AND fecha_reserva = ? AND estado IN ('pendiente', 'aprobada')",
                    (inst_row["id"], fecha)
                ).fetchone()
                
                if conflicto:
                    self.bot.send_message(telegram_id, f"❌ Disculpe, el espacio *{inst_row['nombre']}* ya se encuentra reservado para el día {fecha}. Por favor, intente con otra fecha o instalación.")
                    return
                
                # Descuento socio (20%)
                regular_price = inst_row["precio_alquiler"] or 0
                monto = regular_price * 0.8
                codigo = f"RES-{str(int(time.time()))[-6:]}"
                
                conn.execute("""
                    INSERT INTO reservas (codigo, socio_id, instalacion_id, tipo_uso, fecha_reserva, hora_inicio, hora_fin, sillas_solicitadas, mesas_solicitadas, monto_total, estado, notas)
                    VALUES (?, ?, ?, 'personal', ?, ?, ?, ?, ?, ?, 'aprobada', 'Reservado vía TINO AI')
                """, (codigo, socio["id"], inst_row["id"], fecha, hora_inicio, hora_fin, inst_row["sillas"], inst_row["mesas"], monto))
                
                res_msg = f"""✨ *¡RESERVA CONFIRMADA!* ✅
━━━━━━━━━━━━━━━━━━━━

📋 *Código:* `{codigo}`
🏛️ *Instalación:* {inst_row['nombre']}
📅 *Fecha:* {fecha}
⏰ *Horario:* {hora_inicio} a {hora_fin}
💵 *Monto a pagar (Con 20% Desc. Socio):* ${monto:.2f} USD
🪑 *Incluye:* {inst_row['sillas']} sillas y {inst_row['mesas']} mesas

Su reserva ha sido registrada y aprobada automáticamente. ¡Que disfrute de su actividad! 🎉"""
                self.bot.send_message(telegram_id, res_msg, parse_mode="Markdown")
                
                # Notificar a administración
                try:
                    admin_msg = f"🏟️ *NUEVA RESERVA AUTOMÁTICA*\n\n👤 Socio: {socio['nombre']} {socio['apellido']}\n🏛️ Lugar: {inst_row['nombre']}\n📅 Fecha: {fecha} ({hora_inicio} - {hora_fin})\n💵 Total: ${monto:.2f} USD\n📋 Código: {codigo}"
                    self.bot.send_message(self.admin_id, admin_msg, parse_mode="Markdown")
                except Exception:
                    pass

            elif event_type == "reservar_karaoke":
                if not socio:
                    self.bot.send_message(telegram_id, "❌ Debe vincular su cuenta de socio primero para reservar el equipo de karaoke.")
                    return
                if not socio["solvente"]:
                    self.bot.send_message(telegram_id, "❌ Lo siento, debe estar *solvente* con la administración para reservar servicios del club.", parse_mode="Markdown")
                    return

                fecha = payload.get("fecha", "").strip()
                hora_inicio = payload.get("hora_inicio", "").strip()
                hora_fin = payload.get("hora_fin", "").strip()
                espacio = payload.get("espacio", "Sin especificar").strip()

                precio = 40.00 * 0.8  # precio socio con 20% desc
                codigo = f"KAR-{str(int(time.time()))[-6:]}"

                conn.execute("""
                    INSERT INTO reservas (codigo, socio_id, instalacion_id, tipo_uso, fecha_reserva, hora_inicio, hora_fin, sillas_solicitadas, mesas_solicitadas, monto_total, estado, notas)
                    VALUES (?, ?, NULL, 'karaoke', ?, ?, ?, 0, 0, ?, 'aprobada', ?)
                """, (codigo, socio["id"], fecha, hora_inicio, hora_fin, precio, f"Karaoke en: {espacio}"))

                kar_msg = f"""🎤 *¡RESERVA DE KARAOKE CONFIRMADA!* ✅
━━━━━━━━━━━━━━━━━━━━

📋 *Código:* `{codigo}`
🎵 *Servicio:* Equipo de Karaoke (add-on)
📅 *Fecha:* {fecha}
⏰ *Horario:* {hora_inicio} a {hora_fin}
🏛️ *Espacio:* {espacio}
💵 *Monto (Con 20% Desc. Socio):* ${precio:.2f} USD
🎙️ *Incluye:* Sistema de sonido profesional, pantalla LED, 2 micrófonos inalámbricos y catálogo actualizado

¡Todo listo para una noche inolvidable! 🎶"""
                self.bot.send_message(telegram_id, kar_msg, parse_mode="Markdown")

                try:
                    self.bot.send_message(
                        self.admin_id,
                        f"🎤 *RESERVA DE KARAOKE*\n\n👤 Socio: {socio['nombre']} {socio['apellido']}\n📅 Fecha: {fecha} ({hora_inicio} - {hora_fin})\n🏛️ Espacio: {espacio}\n💵 Total: ${precio:.2f} USD\n📋 Código: {codigo}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            elif event_type == "confirmar_asistencia":
                if not socio:
                    self.bot.send_message(telegram_id, "❌ Solo los socios vinculados pueden confirmar asistencia a eventos deportivos o sociales.")
                    return
                
                evento_codigo = payload.get("evento_codigo", "").strip()
                evt = conn.execute("SELECT * FROM eventos WHERE codigo = ? OR titulo LIKE ?", (evento_codigo, f"%{evento_codigo}%")).fetchone()
                
                if not evt:
                    self.bot.send_message(telegram_id, f"❌ No logré ubicar el evento '{evento_codigo}' en la agenda del club.")
                    return
                
                try:
                    conn.execute("INSERT INTO asistencia_eventos (evento_id, socio_id) VALUES (?, ?)", (evt["id"], socio["id"]))
                    self.bot.send_message(telegram_id, f"✅ *Asistencia Confirmada* para el evento *{evt['titulo']}* el {evt['fecha_evento'][:10]}. ¡Le esperamos!")
                except sqlite3.IntegrityError:
                    self.bot.send_message(telegram_id, f"ℹ️ Ya se encuentra registrado en la lista de asistencia para *{evt['titulo']}*.")

            elif event_type == "crear_ticket":
                categoria = payload.get("categoria", "general").strip()
                asunto = payload.get("asunto", "Soporte general").strip()
                mensaje = payload.get("mensaje", "").strip()
                
                if not mensaje:
                    self.bot.send_message(telegram_id, "❌ Por favor proporcione el detalle del requerimiento.")
                    return
                
                codigo = f"TKT-{str(int(time.time()))[-6:]}"
                
                conn.execute("""
                    INSERT INTO tickets_soporte (codigo, socio_id, telegram_id, nombre_solicitante, es_socio, categoria, asunto, mensaje, estado, prioridad)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'abierto', 'alta')
                """, (
                    codigo, 
                    socio["id"] if socio else None, 
                    str(telegram_id), 
                    f"{socio['nombre']} {socio['apellido']}" if socio else "Visitante",
                    1 if socio else 0,
                    categoria,
                    asunto,
                    mensaje
                ))
                
                self.bot.send_message(
                    telegram_id, 
                    f"📨 *Ticket de Requerimiento Creado* ✅\n\n📋 *Código:* `{codigo}`\n📝 *Asunto:* {asunto}\n\nNuestra junta directiva y equipo de administración revisarán su solicitud. Le notificaremos a la brevedad posible.",
                    parse_mode="Markdown"
                )
                
                # Notificar admin
                try:
                    admin_msg = f"🆘 *NUEVO TICKET VIP*\n\n📋 Código: {codigo}\n👤 Solicitante: {socio['nombre'] if socio else 'Visitante'} ({telegram_id})\n📝 Asunto: {asunto}\n💬 Mensaje: {mensaje}"
                    self.bot.send_message(self.admin_id, admin_msg, parse_mode="Markdown")
                except Exception:
                    pass

            elif event_type == "actualizar_datos":
                if not socio:
                    self.bot.send_message(telegram_id, "❌ Debe vincular su cuenta de socio primero para actualizar sus datos.")
                    return
                
                telefono = payload.get("telefono")
                email = payload.get("email")
                
                if telefono:
                    conn.execute("UPDATE socios SET telefono = ? WHERE id = ?", (telefono, socio["id"]))
                if email:
                    conn.execute("UPDATE socios SET email = ? WHERE id = ?", (email, socio["id"]))
                    
                self.bot.send_message(telegram_id, "✅ Sus datos de contacto han sido actualizados exitosamente en nuestra base de datos.")
                
                if hasattr(self, "agent") and self.agent:
                    self.agent.sessions.reset(telegram_id)

            elif event_type == "guardar_memoria":
                fact = payload.get("fact", "").strip()
                if fact:
                    conn.execute("INSERT INTO partner_facts (telegram_id, fact) VALUES (?, ?)", (str(telegram_id), fact))
                    logger.info(f"Fact guardado para {telegram_id}: {fact}")

            elif event_type == "borrar_memoria":
                conn.execute("DELETE FROM partner_facts WHERE telegram_id = ?", (str(telegram_id),))
                self.bot.send_message(telegram_id, "🧹 He restablecido mi memoria sobre sus preferencias de manera exitosa.")

            elif event_type == "enviar_imagen_menu":
                media_name = payload.get("media_name", "").strip()
                media_map = {
                    "menu_hamburguesas": "menu_hamburguesas.png",
                    "menu_pizzas": "menu_pizzas.png",
                    "menu_extras": "menu_extras.png",
                    "menu_promos": "menu_promos.png"
                }
                
                file_name = media_map.get(media_name)
                if file_name:
                    path = os.path.join(self.media_dir, file_name)
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            self.bot.send_photo(telegram_id, f, caption="¡Con muchísimo gusto! Aquí tiene el menú solicitado. 🍽️😊")
                    else:
                        self.bot.send_message(telegram_id, "⚠️ Lo siento, el archivo de imagen correspondiente no se encuentra disponible temporalmente.")

            elif event_type == "admin_responder_ticket":
                if not is_admin:
                    return
                
                ticket_codigo = payload.get("ticket_codigo", "").strip().upper()
                respuesta = payload.get("respuesta", "").strip()
                
                tkt = conn.execute("""
                    SELECT t.codigo, s.telegram_id, t.telegram_id as t_tg_id 
                    FROM tickets_soporte t 
                    LEFT JOIN socios s ON t.socio_id = s.id 
                    WHERE t.codigo = ?
                """, (ticket_codigo,)).fetchone()
                
                if not tkt:
                    self.bot.send_message(telegram_id, f"❌ El ticket {ticket_codigo} no existe.")
                    return
                
                conn.execute("""
                    UPDATE tickets_soporte 
                    SET respuesta = ?, estado = 'resuelto', respondido_por = 'Admin', updated_at = datetime('now') 
                    WHERE codigo = ?
                """, (respuesta, ticket_codigo))
                
                self.bot.send_message(telegram_id, f"✅ Requerimiento {ticket_codigo} resuelto en el sistema.")
                
                user_tg_id = tkt["telegram_id"] or tkt["t_tg_id"]
                if user_tg_id:
                    try:
                        self.bot.send_message(
                            user_tg_id,
                            f"✅ *Respuesta de la Administración a su solicitud {ticket_codigo}*\n\n💬 *Respuesta:* {respuesta}\n\n_Estatus: Resuelto._",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"No se pudo notificar al socio: {e}")

            elif event_type == "admin_enviar_alerta_pago":
                if not is_admin:
                    return
                
                mensualidad = self.config.get("mensualidad", 16.50)
                now = self._hora_vzla()
                now_y_m = f"{now.year}-{now.month:02d}"
                
                insolventes = conn.execute("""
                    SELECT s.id, s.nombre, s.telegram_id FROM socios s 
                    WHERE s.estado = 'activo' AND s.telegram_id IS NOT NULL
                    AND s.id NOT IN (SELECT socio_id FROM pagos WHERE mes_correspondiente = ?)
                """, (now_y_m,)).fetchall()
                
                enviados = 0
                for s in insolventes:
                    try:
                        unpaid = get_meses_deuda(conn, s["id"])
                        if not unpaid:
                            continue
                        debt_str = ", ".join([self.format_mes(m) for m in unpaid])
                        total_debt = len(unpaid) * mensualidad
                        
                        self.bot.send_message(
                            s["telegram_id"],
                            f"🔔 *Aviso Administrativo*\n\nEstimado/a socio *{s['nombre']}*,\n\nLe recordamos de la manera más atenta que presenta cuotas de mantenimiento pendientes por un total de *${total_debt:.2f} USD* correspondientes a los meses: {debt_str}.\n\nLe agradecemos registrar su pago móvil o Zelle por este chat para mantener sus beneficios al día. ¡Que tenga un feliz día! 🙏",
                            parse_mode="Markdown"
                        )
                        enviados += 1
                    except Exception as e:
                        logger.warning(f"Error enviando cobro masivo a {s['telegram_id']}: {e}")
                        
                self.bot.send_message(telegram_id, f"✅ Proceso de cobranza masiva finalizado. Se enviaron {enviados} recordatorios.")

            elif event_type == "admin_generar_reporte":
                if not is_admin:
                    return
                
                tipo_reporte = payload.get("tipo_reporte", "socios").strip().lower()
                
                import csv
                output = io.StringIO()
                writer = csv.writer(output)
                
                if tipo_reporte == "socios":
                    list_socios = conn.execute("SELECT * FROM socios").fetchall()
                    writer.writerow(["Cedula", "Nombre", "Apellido", "Telefono", "Email", "Tipo Socio", "Estado", "Fecha Ingreso"])
                    for s in list_socios:
                        writer.writerow([s["cedula"], s["nombre"], s["apellido"], s["telefono"] or "", s["email"] or "", s["tipo_socio"], s["estado"], s["fecha_ingreso"]])
                    csv_bytes = output.getvalue().encode('utf-8')
                    self.bot.send_document(
                        telegram_id,
                        ("reporte_socios.csv", csv_bytes),
                        caption="📋 Listado de socios generado por solicitud de IA."
                    )
                elif tipo_reporte == "pagos":
                    list_pagos = conn.execute("""
                        SELECT s.cedula, s.nombre, s.apellido, p.monto, p.mes_correspondiente, p.metodo_pago, p.referencia, p.fecha_pago 
                        FROM pagos p 
                        JOIN socios s ON p.socio_id = s.id
                        ORDER BY p.fecha_pago DESC
                    """).fetchall()
                    writer.writerow(["Cedula", "Nombre Completo", "Monto USD", "Mes Pagado", "Metodo", "Referencia", "Fecha Registro"])
                    for p in list_pagos:
                        writer.writerow([p["cedula"], f"{p['nombre']} {p['apellido']}", p["monto"], p["mes_correspondiente"], p["metodo_pago"] or "", p["referencia"] or "", p["fecha_pago"]])
                    csv_bytes = output.getvalue().encode('utf-8')
                    self.bot.send_document(
                        telegram_id,
                        ("reporte_pagos.csv", csv_bytes),
                        caption="💰 Consolidado histórico de pagos generado por solicitud de IA."
                    )
                else:  # reservas
                    list_reservas = conn.execute("""
                        SELECT r.codigo, s.cedula, s.nombre, s.apellido, i.nombre as instalacion, r.fecha_reserva, r.hora_inicio, r.hora_fin, r.monto_total, r.estado 
                        FROM reservas r 
                        JOIN socios s ON r.socio_id = s.id 
                        JOIN instalaciones i ON r.instalacion_id = i.id
                        ORDER BY r.fecha_reserva DESC
                    """).fetchall()
                    writer.writerow(["Codigo", "Cedula", "Socio", "Instalacion", "Fecha Reserva", "Inicio", "Fin", "Monto USD", "Estado"])
                    for r in list_reservas:
                        writer.writerow([r["codigo"], r["cedula"], f"{r['nombre']} {r['apellido']}", r["instalacion"], r["fecha_reserva"], r["hora_inicio"], r["hora_fin"], r["monto_total"], r["estado"]])
                    csv_bytes = output.getvalue().encode('utf-8')
                    self.bot.send_document(
                        telegram_id,
                        ("reporte_reservas.csv", csv_bytes),
                        caption="🏟️ Listado de reservas generado por solicitud de IA."
                    )

    def admin_commands(self) -> dict:
        def _check_admin(message, profile, memory) -> bool:
            user_id = str(message.chat.id)
            if user_id == str(profile.admin_id):
                return True
            with memory._conn() as conn:
                row = conn.execute("SELECT id FROM admins WHERE telegram_id = ?", (user_id,)).fetchone()
                return row is not None

        def cmd_admin(message, bot, profile, memory):
            if not _check_admin(message, profile, memory):
                bot.reply_to(message, "❌ Este comando es exclusivo para administradores del club.")
                return
            
            now = profile._hora_vzla()
            now_y_m = f"{now.year}-{now.month:02d}"
            
            with memory._conn() as conn:
                total_socios = conn.execute("SELECT COUNT(*) as c FROM socios WHERE estado = 'activo'").fetchone()["c"]
                pagaron = conn.execute("SELECT COUNT(DISTINCT socio_id) as c FROM pagos WHERE mes_correspondiente = ?", (now_y_m,)).fetchone()["c"]
                recaudacion = conn.execute("SELECT SUM(monto) as t FROM pagos WHERE mes_correspondiente = ?", (now_y_m,)).fetchone()["t"] or 0
                eventos_prox = conn.execute("SELECT COUNT(*) as c FROM eventos WHERE estado = 'activo' AND fecha_evento >= date('now')").fetchone()["c"]
                reservas_pend = conn.execute("SELECT COUNT(*) as c FROM reservas WHERE estado = 'pendiente'").fetchone()["c"]
                tickets_abiertos = conn.execute("SELECT COUNT(*) as c FROM tickets_soporte WHERE estado IN ('abierto', 'en_proceso')").fetchone()["c"]
                
                meta = total_socios * profile.config.get("mensualidad", 16.50)
                pct_meta = (recaudacion / meta * 100) if meta > 0 else 0
                
            text = f"""👑 *PANEL DE CONTROL GENERAL*
━━━━━━━━━━━━━━━━━━━━

💰 *Recaudación de {profile.format_mes(now_y_m).upper()}*
├── Recaudado: ${recaudacion:.2f} USD
├── Meta: ${meta:.2f} USD
├── Cumplimiento: {pct_meta:.1f}%
└── Solventes: {pagaron}/{total_socios} socios ({total_socios - pagaron} pendientes)

🏟️ *Gestión de Instalaciones*
├── Reservas pendientes: {reservas_pend}
└── Eventos programados: {eventos_prox}

🆘 *Soporte Técnico*
└── Tickets abiertos: {tickets_abiertos}

*Comandos Admin Disponibles:*
/deudores - Lista socios deudores
/alerta_cobro - Envía recordatorios masivos
/reporte_socios - Descarga listado de socios en CSV
/reporte_pagos - Descarga historial de pagos en CSV
/reporte_reservas - Descarga reservas en CSV
/responder_ticket [código] [mensaje] - Responde un ticket
"""
            bot.send_message(message.chat.id, text, parse_mode="Markdown")

        def cmd_deudores(message, bot, profile, memory):
            if not _check_admin(message, profile, memory):
                bot.reply_to(message, "❌ Acceso denegado.")
                return
            
            now = profile._hora_vzla()
            now_y_m = f"{now.year}-{now.month:02d}"
            
            with memory._conn() as conn:
                deudores = conn.execute("""
                    SELECT s.cedula, s.nombre, s.apellido, s.telefono FROM socios s
                    WHERE s.estado = 'activo' AND s.id NOT IN (SELECT socio_id FROM pagos WHERE mes_correspondiente = ?)
                    LIMIT 40
                """, (now_y_m,)).fetchall()
                
            if not deudores:
                bot.reply_to(message, "¡Excelente noticia! Todos los socios se encuentran solventes este mes. 🎉")
                return
                
            lines = [f"📋 *SOCIOS DEUDORES ({profile.format_mes(now_y_m).upper()})*\n━━━━━━━━━━━━━━━━━━━━\n"]
            for d in deudores:
                tel = d["telefono"] or "Sin teléfono"
                lines.append(f"• {d['cedula']} | {d['nombre']} {d['apellido']} (📞 {tel})")
            
            if len(deudores) >= 40:
                lines.append("\n_Nota: Mostrando solo los primeros 40 deudores._")
                
            bot.send_message(message.chat.id, "\n".join(lines), parse_mode="Markdown")

        def cmd_alerta_cobro(message, bot, profile, memory):
            if not _check_admin(message, profile, memory):
                bot.reply_to(message, "❌ Acceso denegado.")
                return
            
            bot.reply_to(message, "⏳ Enviando alertas de cobro masivo a los socios deudores con Telegram vinculado...")
            
            now = profile._hora_vzla()
            now_y_m = f"{now.year}-{now.month:02d}"
            mensualidad = profile.config.get("mensualidad", 16.50)
            
            enviados = 0
            with memory._conn() as conn:
                insolventes = conn.execute("""
                    SELECT s.id, s.nombre, s.telegram_id FROM socios s 
                    WHERE s.estado = 'activo' AND s.telegram_id IS NOT NULL
                    AND s.id NOT IN (SELECT socio_id FROM pagos WHERE mes_correspondiente = ?)
                """, (now_y_m,)).fetchall()
                
                for s in insolventes:
                    try:
                        unpaid = get_meses_deuda(conn, s["id"])
                        if not unpaid:
                            continue
                        debt_str = ", ".join([profile.format_mes(m) for m in unpaid])
                        total_debt = len(unpaid) * mensualidad
                        
                        bot.send_message(
                            s["telegram_id"],
                            f"🔔 *Recordatorio de Administración*\n\nEstimado/a *{s['nombre']}*,\n\nLe recordamos cortésmente que presenta cuotas de mantenimiento pendientes en su membresía por un monto total de *${total_debt:.2f} USD* correspondientes a los meses: {debt_str}.\n\nAgradecemos reportar su pago a la brevedad mediante Pago Móvil o Zelle por este medio para mantener su acceso al día. ¡Muchas gracias! 🙏",
                            parse_mode="Markdown"
                        )
                        enviados += 1
                    except Exception as e:
                        logger.warning(f"Error enviando alerta a telegram_id {s['telegram_id']}: {e}")
            
            bot.send_message(message.chat.id, f"✅ Alerta finalizada. Se enviaron {enviados} recordatorios exitosamente.")

        def cmd_reporte_socios(message, bot, profile, memory):
            if not _check_admin(message, profile, memory):
                bot.reply_to(message, "❌ Acceso denegado.")
                return
            
            with memory._conn() as conn:
                list_socios = conn.execute("SELECT * FROM socios").fetchall()
                
            import csv
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Cedula", "Nombre", "Apellido", "Telefono", "Email", "Tipo Socio", "Estado", "Fecha Ingreso"])
            for s in list_socios:
                writer.writerow([s["cedula"], s["nombre"], s["apellido"], s["telefono"] or "", s["email"] or "", s["tipo_socio"], s["estado"], s["fecha_ingreso"]])
                
            csv_bytes = output.getvalue().encode('utf-8')
            bot.send_document(
                message.chat.id,
                ("reporte_socios.csv", csv_bytes),
                caption="📋 Listado completo de socios registrado en el sistema."
            )

        def cmd_reporte_pagos(message, bot, profile, memory):
            if not _check_admin(message, profile, memory):
                bot.reply_to(message, "❌ Acceso denegado.")
                return
            
            with memory._conn() as conn:
                list_pagos = conn.execute("""
                    SELECT s.cedula, s.nombre, s.apellido, p.monto, p.mes_correspondiente, p.metodo_pago, p.referencia, p.fecha_pago 
                    FROM pagos p 
                    JOIN socios s ON p.socio_id = s.id
                    ORDER BY p.fecha_pago DESC
                """).fetchall()
                
            import csv
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Cedula", "Nombre Completo", "Monto USD", "Mes Pagado", "Metodo", "Referencia", "Fecha Registro"])
            for p in list_pagos:
                writer.writerow([p["cedula"], f"{p['nombre']} {p['apellido']}", p["monto"], p["mes_correspondiente"], p["metodo_pago"] or "", p["referencia"] or "", p["fecha_pago"]])
                
            csv_bytes = output.getvalue().encode('utf-8')
            bot.send_document(
                message.chat.id,
                ("reporte_pagos.csv", csv_bytes),
                caption="💰 Historial consolidado de pagos reportados y conciliados."
            )

        def cmd_reporte_reservas(message, bot, profile, memory):
            if not _check_admin(message, profile, memory):
                bot.reply_to(message, "❌ Acceso denegado.")
                return
            
            with memory._conn() as conn:
                list_reservas = conn.execute("""
                    SELECT r.codigo, s.cedula, s.nombre, s.apellido, i.nombre as instalacion, r.fecha_reserva, r.hora_inicio, r.hora_fin, r.monto_total, r.estado 
                    FROM reservas r 
                    JOIN socios s ON r.socio_id = s.id 
                    JOIN instalaciones i ON r.instalacion_id = i.id
                    ORDER BY r.fecha_reserva DESC
                """).fetchall()
                
            import csv
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Codigo", "Cedula", "Socio", "Instalacion", "Fecha Reserva", "Inicio", "Fin", "Monto USD", "Estado"])
            for r in list_reservas:
                writer.writerow([r["codigo"], r["cedula"], f"{r['nombre']} {r['apellido']}", r["instalacion"], r["fecha_reserva"], r["hora_inicio"], r["hora_fin"], r["monto_total"], r["estado"]])
                
            csv_bytes = output.getvalue().encode('utf-8')
            bot.send_document(
                message.chat.id,
                ("reporte_reservas.csv", csv_bytes),
                caption="🏟️ Historial completo de reservas de instalaciones."
            )

        def cmd_responder_ticket(message, bot, profile, memory):
            if not _check_admin(message, profile, memory):
                bot.reply_to(message, "❌ Acceso denegado.")
                return
                
            parts = message.text.split(" ", 2)
            if len(parts) < 3:
                bot.reply_to(message, "❌ Formato incorrecto. Uso: /responder_ticket [código_ticket] [respuesta]")
                return
                
            tkt_code = parts[1].strip().upper()
            reply_text = parts[2].strip()
            
            with memory._conn() as conn:
                tkt = conn.execute("""
                    SELECT t.codigo, s.telegram_id, t.telegram_id as t_tg_id 
                    FROM tickets_soporte t 
                    LEFT JOIN socios s ON t.socio_id = s.id 
                    WHERE t.codigo = ?
                """, (tkt_code,)).fetchone()
                
                if not tkt:
                    bot.reply_to(message, f"❌ El ticket {tkt_code} no existe.")
                    return
                
                conn.execute("""
                    UPDATE tickets_soporte 
                    SET respuesta = ?, estado = 'resuelto', respondido_por = 'Admin', updated_at = datetime('now') 
                    WHERE codigo = ?
                """, (reply_text, tkt_code))
                
            # Notificar al socio
            user_tg_id = tkt["telegram_id"] or tkt["t_tg_id"]
            if user_tg_id:
                try:
                    bot.send_message(
                        user_tg_id,
                        f"✅ *Respuesta a su requerimiento {tkt_code}*\n\n💬 *Respuesta de Administración:* {reply_text}\n\n_Estatus del caso: Resuelto._",
                        parse_mode="Markdown"
                    )
                    bot.reply_to(message, f"✅ Ticket {tkt_code} resuelto y notificado al socio.")
                except Exception as e:
                    bot.reply_to(message, f"✅ Ticket {tkt_code} resuelto en BD, pero no se pudo enviar mensaje por Telegram: {e}")
            else:
                bot.reply_to(message, f"✅ Ticket {tkt_code} resuelto en BD. (No hay Telegram ID asociado para notificar).")

        return {
            "admin": cmd_admin,
            "deudores": cmd_deudores,
            "alerta_cobro": cmd_alerta_cobro,
            "reporte_socios": cmd_reporte_socios,
            "reporte_pagos": cmd_reporte_pagos,
            "reporte_reservas": cmd_reporte_reservas,
            "responder_ticket": cmd_responder_ticket,
        }


PROFILE_CLASS = ClubSocialProfile
