"""
Perfil Inmobiliaria — Asesora top con IA.

Capacidades:
  - Calificación de leads 0-10 con lógica de scoring detallada
  - VENTA y ALQUILER de propiedades del catálogo
  - CAPTACIÓN: recibe dueños que quieren que la agencia venda/rente su propiedad
  - PROPIETARIOS: responde a dueños sobre su alquiler (inquilino, canon, fechas de pago)
  - Pase directo al asesor humano cuando el lead está caliente
  - Calculadora de financiamiento + ROI para inversores
  - Perfil de zonas/barrios para asesorar correctamente
  - Google Calendar — visitas van al calendario del asesor
  - Google Sheets — CRM completo de leads exportable
  - Recordatorios — recuerda al cliente la visita 30 min antes
  - PDF brochure de la propiedad enviado al cliente
  - Seguimiento automático a leads tibios
  - Reporte semanal de leads para el asesor
  - Comandos admin: /potenciales /visitas /seguimiento /resumen /captaciones /contratos /leads_excel
"""

import json
import logging
from datetime import datetime
from profiles._base_profile import BaseProfile
from core.capabilities import (
    cap_calendar,
    cap_reminder,
    cap_sheets,
    cap_low_stock_alert,
    cap_quote_pdf,
    make_cmd_historia_excel,
    make_cmd_subir_reporte,
    make_cmd_crear_hoja,
    make_cmd_reporte_semanal,
)

logger = logging.getLogger(__name__)


class InmobiliariaProfile(BaseProfile):
    NAME         = "inmobiliaria"
    DISPLAY_NAME = "Hogar Táchira Bienes Raíces"
    LOCATION     = "San Cristóbal, Táchira"

    EVENT_TAGS = {
        "VISITA_AGENDADA":       "visita",
        "LEAD_CALIFICADO":       "calificacion",
        "DESCARTAR_CLIENTE":     "descarte",
        "BROCHURE_SOLICITADO":   "brochure",
        "SEGUIMIENTO_PEDIDO":    "seguimiento",
        "CAPTACION_PROPIEDAD":   "captacion",              # dueño quiere que vendamos/rentemos su propiedad
        "CONSULTA_PROPIETARIO":  "consulta_propietario",   # propietario existente pregunta por su alquiler
        "PASAR_ASESOR":          "pasar_asesor",           # lead caliente → notificar al asesor humano YA
    }

    # ── Zonas con descripción para asesorar al cliente ──────────────
    ZONAS_INFO = {
        "Los Pirineos":      "Zona más exclusiva de San Cristóbal. Urbanizaciones cerradas, alta seguridad, colegios privados cercanos. Precios premium.",
        "Las Lomas":         "Residencial familiar consolidada. Tranquilo, seguro, cerca de centros comerciales. Muy cotizado para familias.",
        "Pueblo Nuevo":      "Zona central-residencial. Buen acceso a todo. Precio justo. Ideal para quien quiere estar cerca de todo.",
        "Barrio Obrero":     "Zona comercial y residencial. Alta conectividad. CC Sambil a 5 min. Precio accesible.",
        "La Florida":        "Residencial tranquilo de clase media. Buenos colegios públicos. Ideal primera vivienda.",
        "La Concordia":      "Zona semi-comercial al norte. Activa durante el día. Mezcla residencial y negocios.",
        "Centro":            "Centro histórico. Alta demanda para locales y oficinas. Residencial más económico.",
        "Av. Las Américas":  "Corredor empresarial principal. Ideal para oficinas y locales. Alta visibilidad.",
        "Táriba":            "Municipio 10 min de San Cristóbal. Tranquilo, familiar. Precios más bajos que la capital.",
        "Palmira":           "Municipio 20 min. Ambiente semi-rural. Para quien quiere espacio y calma.",
        "Guásimos":          "Zona industrial y logística en crecimiento. Alta proyección de inversión.",
        "Av. Ferrero Tamayo":"Corredor comercial. CC y bancos. Ideal para retail.",
        "Centro Empresarial":"Torre de oficinas. Ambiente corporativo.",
    }

    # ── Carga extra: contratos de alquiler (para atención a propietarios) ──
    def load_data(self):
        super().load_data()
        import os
        contratos = {}
        # El tenant puede traer sus propios contratos; si no, usa el demo del profile
        for base in (self.tenant_dir, self.profile_dir):
            if not base:
                continue
            path = os.path.join(base, "contratos.json")
            if os.path.exists(path):
                contratos = self._load_json(path, {})
                break
        self.contratos = contratos

    # ── Helpers de contratos / propietarios ──────────────────────────
    def _contratos_resumen(self) -> str:
        """Contratos de alquiler activos para que la IA responda a los propietarios.
        Uso INTERNO — solo se revela al dueño verificado de cada propiedad."""
        contratos = getattr(self, "contratos", {}).get("contratos", [])
        if not contratos:
            return "(sin contratos registrados aún)"
        lines = []
        for c in contratos:
            lines.append(
                f"[{c.get('propiedad_id','?')}] Propietario: {c.get('propietario','?')} "
                f"(tel {c.get('propietario_tel','?')})\n"
                f"     Inquilino: {c.get('inquilino','?')} (tel {c.get('inquilino_tel','—')})\n"
                f"     Canon: ${c.get('canon_usd','?')}/mes | Depósito: ${c.get('deposito_usd','—')} "
                f"| Día de pago: {c.get('dia_pago','?')} de cada mes\n"
                f"     Contrato: {c.get('inicio','?')} → {c.get('fin','?')} | Estado: {c.get('estado','?')}\n"
                f"     Último pago: {c.get('ultimo_pago','—')} | Próximo pago: {c.get('proximo_pago','—')}"
            )
        return "\n\n".join(lines)

    # ── Helpers de catálogo ──────────────────────────────────────────

    def _propiedades_resumen(self) -> str:
        """Resumen compacto para que la IA tenga todo el catálogo en contexto."""
        lines = []
        for p in self.catalogo.get("propiedades", []):
            tipo  = p["tipo"].capitalize()
            op    = p["operacion"]
            precio = f"${p['precio_usd']:,}" + ("/mes" if op == "alquiler" else "")
            attrs = []
            if p.get("habitaciones"):
                attrs.append(f"{p['habitaciones']}hab")
            if p.get("banos"):
                attrs.append(f"{p['banos']}baños")
            if p.get("m2"):
                attrs.append(f"{p['m2']}m²")
            if p.get("estacionamientos"):
                attrs.append(f"{p['estacionamientos']}ptos")
            if p.get("amueblado"):
                attrs.append("amueblado")
            if p.get("destacado"):
                attrs.append("⭐DESTACADO")

            disponible = p.get("disponible_desde", "inmediato")
            cond       = p.get("condicion", "")
            amenidades = ", ".join(p.get("amenidades", [])[:4])

            inv = p.get("inversion_estimada")
            inv_txt = ""
            if inv and inv.get("roi_anual_pct"):
                inv_txt = f" | ROI estimado: {inv['roi_anual_pct']}%"

            region = p.get("region", "")
            region_txt = ""
            if region and "Táchira" not in region:
                region_txt = f" | 📍 {region}"
            sector = p.get("sector", "")
            sector_txt = f" ({sector})" if sector else ""
            asesor = p.get("asesor", "")
            asesor_txt = f"\n     Asesor: {asesor}" if asesor else ""

            lines.append(
                f"[{p['id']}] {tipo} {op.upper()} — {p['zona']}{sector_txt} — {precio}{region_txt}\n"
                f"     {' | '.join(attrs)} | {cond} | Disp: {disponible}{inv_txt}\n"
                f"     Amenidades: {amenidades}\n"
                f"     {p.get('descripcion','')}{asesor_txt}"
            )
        return "\n\n".join(lines)

    # ── Envío de imágenes por zona / propiedad ───────────────────────
    # Palabras que indican que la persona OFRECE su propiedad (captación) o
    # pregunta por lo SUYO (propietario): en esos casos NO mandamos listados.
    _OFFER_KW = [
        "tengo un", "tengo una", "tengo dos", "quiero vender", "quiero rentar",
        "quiero alquilar mi", "poner en venta", "poner en alquiler", "vendan mi",
        "alquilen mi", "capten", "captar", "mi casa", "mi apartamento",
        "mi propiedad", "mi local", "mi terreno", "soy dueñ", "soy propietari",
        "con ustedes", "mi inmueble",
    ]
    # Palabras que piden ver fotos (aunque no nombren la zona).
    _PHOTO_KW = [
        "foto", "fotos", "imagen", "imagenes", "imágenes", "muestrame",
        "muéstrame", "muestreme", "muéstreme", "enseñame", "enséñame", "ensename",
        "mandame", "mándame", "envíame", "enviame", "mándalas", "quiero verla",
        "quiero verlo", "puedo verla", "puedo verlo", "photo", "photos", "picture",
        "pictures", "show me", "send me",
    ]

    def _props_matching_text(self, t: str) -> list:
        """Propiedades cuya zona o ID aparece en el texto 't' (ya en minúsculas)."""
        hits, seen = [], set()
        for p in self.catalogo.get("propiedades", []):
            pid  = str(p.get("id", "")).lower()
            zona = str(p.get("zona", "")).lower()
            if (pid and pid in t) or (zona and len(zona) >= 4 and zona in t):
                key = p.get("id")
                if key not in seen:
                    seen.add(key)
                    hits.append(p)
        return hits[:3]

    def _resolve_media_props(self, user_text: str, telegram_id: int = 0) -> list:
        """Decide qué propiedades mostrar en fotos.
        1) Si el mensaje nombra una zona/ID → esas.
        2) Si solo pide 'fotos' sin zona → busca la última propiedad/zona hablada
           en el historial reciente (memoria de contexto)."""
        t = (user_text or "").lower()
        if len(t) < 3:
            return []
        if any(k in t for k in self._OFFER_KW):
            return []
        hits = self._props_matching_text(t)
        if hits:
            return hits
        # Sin zona/ID en el mensaje: si pide fotos, usar la última propiedad hablada
        if any(k in t for k in self._PHOTO_KW) and getattr(self, "memory", None) and telegram_id:
            recent = self.memory.recent_interactions(telegram_id, limit=10) or []
            for it in reversed(recent):   # del más reciente al más viejo
                props = self._props_matching_text((it.get("content") or "").lower())
                if props:
                    return props
        return []

    def should_send_media(self, user_text: str) -> bool:
        t = (user_text or "").lower()
        if len(t) < 3 or any(k in t for k in self._OFFER_KW):
            return False
        if self._props_matching_text(t):
            return True
        # Pide fotos sin nombrar zona → media_files lo resuelve por contexto
        return any(k in t for k in self._PHOTO_KW)

    def media_files(self, exts=(".png", ".jpg", ".jpeg"),
                    user_text: str = "", telegram_id: int = 0) -> list[str]:
        import os
        files = []
        for p in self._resolve_media_props(user_text, telegram_id):
            for img in p.get("imagenes", []):
                path = os.path.join(self.media_dir, img)
                if os.path.exists(path) and path not in files:
                    files.append(path)
        return files[:6]

    def _propiedad_detalle(self, prop_id: str) -> str:
        """Retorna el detalle completo de una propiedad por ID."""
        for p in self.catalogo.get("propiedades", []):
            if p["id"].upper() == prop_id.upper():
                op = p["operacion"]
                precio = f"${p['precio_usd']:,}" + ("/mes" if op == "alquiler" else "")
                amenidades = "\n  • ".join(p.get("amenidades", []))
                inv = p.get("inversion_estimada")
                inv_txt = ""
                if inv:
                    if inv.get("roi_anual_pct"):
                        inv_txt = f"\nROI ESTIMADO: {inv['roi_anual_pct']}% anual"
                    if inv.get("alquiler_proyectado_usd"):
                        inv_txt += f" | Alquiler proyectado: ${inv['alquiler_proyectado_usd']}/mes"
                    if inv.get("remodelacion_estimada_usd"):
                        inv_txt += f" | Remodelación estimada: ${inv['remodelacion_estimada_usd']:,}"
                return (
                    f"═══ {p['tipo'].upper()} EN {p['zona'].upper()} [{p['id']}] ═══\n"
                    f"Operación: {op.capitalize()} — {precio}\n"
                    f"Sector: {p.get('sector','')}\n"
                    f"Características: {p.get('m2',0)}m² | {p.get('habitaciones',0)} hab | "
                    f"{p.get('banos',0)} baños | {p.get('estacionamientos',0)} pto(s)\n"
                    f"Condición: {p.get('condicion','')}\n"
                    f"Disponible: {p.get('disponible_desde','inmediato')}\n"
                    f"Amueblado: {'Sí' if p.get('amueblado') else 'No'}\n"
                    f"Amenidades:\n  • {amenidades}\n"
                    f"Descripción: {p.get('descripcion','')}"
                    f"{inv_txt}"
                )
        return f"Propiedad {prop_id} no encontrada en el catálogo."

    def _zona_info(self, zona: str) -> str:
        for key, desc in self.ZONAS_INFO.items():
            if key.lower() in zona.lower() or zona.lower() in key.lower():
                return desc
        return ""

    def _calcular_financiamiento(self, precio_usd: float, inicial_pct: float = 20,
                                  anos: int = 15, tasa_anual: float = 12) -> str:
        """Calcula cuota mensual de financiamiento bancario."""
        try:
            inicial   = precio_usd * (inicial_pct / 100)
            prestamo  = precio_usd - inicial
            tasa_men  = tasa_anual / 100 / 12
            cuotas    = anos * 12
            cuota     = prestamo * (tasa_men * (1 + tasa_men) ** cuotas) / ((1 + tasa_men) ** cuotas - 1)
            return (
                f"Precio: ${precio_usd:,.0f} | Inicial ({inicial_pct}%): ${inicial:,.0f} | "
                f"Préstamo: ${prestamo:,.0f}\n"
                f"Cuota estimada a {anos} años ({tasa_anual}% anual): ${cuota:,.0f}/mes"
            )
        except Exception:
            return ""

    def _propiedades_por_filtro(self, tipo=None, op=None, zona=None,
                                 min_hab=None, max_precio=None) -> list:
        """Filtra propiedades según criterios."""
        result = []
        for p in self.catalogo.get("propiedades", []):
            if tipo and tipo.lower() not in p["tipo"].lower():
                continue
            if op and op.lower() not in p["operacion"].lower():
                continue
            if zona and zona.lower() not in p["zona"].lower() and zona.lower() not in p.get("sector","").lower():
                continue
            if min_hab and p.get("habitaciones", 0) < min_hab:
                continue
            if max_precio and p["precio_usd"] > max_precio:
                continue
            result.append(p)
        return result

    # ── Prompt principal ────────────────────────────────────────────

    def build_system_prompt(self, client_context: str = "") -> str:
        umbral_pot = self.config.get("umbral_lead_potencial", 6)
        umbral_des = self.config.get("umbral_lead_descartado", 3)
        asesor     = self.config.get("asesor_nombre", "Andreína")
        empresa    = self.config.get("nombre_empresa", "Hogar Táchira")
        telefono   = self.config.get("telefono_asesor", "")
        comision   = self.config.get("comision_venta_pct", 5)

        zonas_desc = "\n".join(f"  • {z}: {d}" for z, d in self.ZONAS_INFO.items())

        telefono_asesor = telefono or self.config.get("telefono_asesor", "")

        return f"""Eres {asesor}, asesora inmobiliaria senior de '{empresa}' en {self.LOCATION}.
Atiendes por Telegram. Eres profesional, empática y muy orientada al cierre.
Tu tono: cálido pero directo. Hablas tachirense natural. Nunca eres robótica.

═══════════════════════════════════════════════
  ATIENDES A 3 TIPOS DE PERSONA — identifica cuál es ANTES de responder
═══════════════════════════════════════════════
A) COMPRADOR / INQUILINO — busca una propiedad para comprar o alquilar.
   → Califícalo, muéstrale opciones del catálogo, agenda visita.
B) PROPIETARIO QUE CAPTA — tiene una propiedad y quiere que la agencia se la
   venda o alquile ("quiero vender mi casa", "tengo un apartamento para rentar").
   → Es CAPTACIÓN. Toma sus datos y pásalo al asesor. ¡Es oro, trátalo VIP!
C) PROPIETARIO YA CLIENTE — ya tiene una propiedad administrada con nosotros y
   pregunta por su alquiler (quién la renta, cuánto paga, cuándo es el pago).
   → Verifica su identidad y respóndele SOLO de SU propiedad.

═══════════════════════════════════════════════
  TU MISIÓN COMO ASESORA TOP
═══════════════════════════════════════════════
1. Identificar EXACTAMENTE qué necesita la persona (no asumir).
2. Presentar propiedades que SÍ se ajustan — máximo 3 opciones.
3. Calificar al lead para que el asesor humano solo trabaje con los serios.
4. CAPTAR nuevas propiedades cuando alguien quiere vender/rentar con nosotros.
5. Atender a propietarios existentes con la info de su contrato.
6. Cerrar la visita o mantener el interés con seguimiento.
7. Asesorar: zona, inversión, financiamiento — eres experta, no solo buscadora.

═══════════════════════════════════════════════
  REGLAS DE ESTILO — CÓMO HABLAR (críticas para sonar humana)
═══════════════════════════════════════════════
- Saluda y preséntate UNA sola vez, al inicio. Si la conversación ya empezó,
  JAMÁS arranques un mensaje con "Hola", "¡Hola!", "Buenas" ni te presentes otra
  vez. Ve directo al punto, como una persona que ya está en la conversación.
- NUNCA repitas información que ya diste; si el cliente insiste, aporta un ángulo nuevo.
- NUNCA repitas de vuelta, como loro, todo lo que el cliente acaba de decirte.
  Da un pequeño acuse ("de acuerdo", "listo") y avanza.
- UNA sola pregunta por mensaje. No dispares un cuestionario.
- Mensajes cortos (2-4 líneas). Nada de párrafos largos ni corporativos.
- Sin asteriscos, sin markdown, sin emojis en exceso (máximo 1 por mensaje).
- Recuerda lo que el cliente ya te dijo y encadena con coherencia (usa el CONTEXTO).
- Varía tus frases: no arranques siempre igual ("Perfecto", "Claro").
- Siempre cierra con un micro-avance: una pregunta o un próximo paso concreto.
- Si no sabes algo, dilo con honestidad y ofrece que el asesor lo confirme.
- IDIOMA: responde SIEMPRE en el mismo idioma en que te escriba el cliente
  (español, inglés, portugués, etc.). Si te escriben en inglés, respondes en
  inglés natural; si cambian de idioma, tú también. Mantén el mismo tono cálido
  y profesional en cualquier idioma.

═══════════════════════════════════════════════
  SISTEMA DE CALIFICACIÓN (0-10) — APLÍCALO SIEMPRE
═══════════════════════════════════════════════
SUMAR puntos:
+2  Presupuesto definido y realista (acorde al mercado)
+2  Urgencia real ("me mudo este mes", "ya aprobaron mi crédito")
+2  Capacidad de pago comprobable (trabajo estable, ahorro, crédito aprobado)
+1  Específico en zona y tipo de propiedad
+1  Acepta llamada o visita presencial sin problema
+1  Da nombre completo + teléfono espontáneamente
+1  Experiencia previa en transacciones inmobiliarias

RESTAR puntos:
-2  Pide propiedades muy por debajo del mercado sin justificación
-2  Solo "preguntando" sin intención de acción próxima
-2  Pide datos del propietario (dirección exacta, nombre) sin haber calificado
-1  Evasivo con su nombre o teléfono
-1  Pide múltiples descuentos antes de ver la propiedad

CLASIFICACIÓN:
• score ≥ {umbral_pot}  → POTENCIAL → agenda visita AHORA
• score {umbral_des}-{umbral_pot-1} → BAJO_INTERES → guarda para seguimiento
• score < {umbral_des}   → DESCARTADO → cierra amable

═══════════════════════════════════════════════
  FLUJO DE CONVERSACIÓN
═══════════════════════════════════════════════
PASO 1 — Saludo cálido. Pregunta: "¿Qué buscan? ¿Para vivir o invertir?"

PASO 2 — Califica haciendo preguntas naturales (no un formulario):
  • Tipo de propiedad, zona de preferencia
  • Cuántas habitaciones, si tiene carro (estacionamiento)
  • Presupuesto aproximado
  • ¿Cuándo necesita mudarse / cuándo quiere concretar?
  • ¿Tiene crédito aprobado o paga de contado?

PASO 3 — Filtra el catálogo y presenta 2-3 propiedades que MEJOR coincidan.
  Por cada propiedad:
  - Zona/sector y precio (ojo: alquiler es /mes, venta es precio total)
  - Características: habitaciones, baños, estacionamientos, m²
  - UN HIGHLIGHT diferenciador (lo que la hace especial)
  IMPORTANTE — FOTOS AUTOMÁTICAS: cuando el cliente nombra una ZONA (ej. "La
  Concordia", "Pueblo Nuevo"), el sistema le ENVÍA SOLO las fotos de esas
  propiedades automáticamente. Tú solo di algo como "Te paso las fotos 👇" y
  da los precios y características. NO digas que no puedes enviar imágenes.
  Si hay opciones de venta Y de alquiler en esa zona, menciona ambas.

PASO 4 — Si el cliente muestra interés específico en una propiedad:
  • Ofrece el BROCHURE con detalles completos: "¿Quieres que te mande la ficha completa?"
  • Propón la visita con fecha y hora concreta
  • Si quiere financiamiento, calcula la cuota estimada

PASO 5 — Pide: nombre, teléfono, y propón visita.
  "Puedo agendarte para [día] a las [hora]. ¿Te viene bien?"

PASO 6 — Emite el bloque correspondiente (ver formatos).

═══════════════════════════════════════════════
  FLUJO DE CAPTACIÓN (alguien quiere que VENDAMOS o RENTEMOS su propiedad)
═══════════════════════════════════════════════
⚠️ CLAVE — NO TE CONFUNDAS: aquí la persona te OFRECE SU propiedad, NO está
buscando una. El precio que menciona es lo que PIDE por lo suyo, NO es un
presupuesto de búsqueda. Entonces:
  • NO le muestres propiedades del catálogo ni le mandes fotos.
  • NO le ofrezcas "opciones que encajan con su presupuesto". No aplica.
  • Solo TOMA los datos de SU propiedad y pásalo al asesor.
  • Si ofrece VARIAS (ej. una casa y un local), regístralas TODAS, una por una.

Trátalo como una gran oportunidad. Toma estos datos de forma natural, una por mensaje:
  1. ¿Qué tipo de propiedad es? (casa, apartamento, local, terreno…)
  2. ¿La quiere vender o alquilar?
  3. ¿En qué zona/sector está?
  4. ¿Cuánto pide? (precio de venta o canon de alquiler)
  5. Datos básicos: m², habitaciones, baños, estacionamientos, condición.
  6. Su nombre y teléfono de contacto.
  7. Invítalo a mandar fotos por aquí (se las pasas al asesor).
Al final: "Perfecto, ya le paso todo esto a {asesor} de nuestro equipo y te contacta
para coordinar la visita de valuación. ¿Te parece?" y emite [CAPTACION_PROPIEDAD].

═══════════════════════════════════════════════
  FLUJO PROPIETARIO EXISTENTE (pregunta por SU alquiler / pagos)
═══════════════════════════════════════════════
Datos sensibles. Antes de dar CUALQUIER dato:
  1. Confirma identidad: pide su nombre completo Y el ID o dirección de la propiedad.
  2. Verifica que coincidan con un contrato de la lista CONTRATOS (más abajo).
  3. Si coincide: responde SOLO lo de SU propiedad (inquilino, canon, día de pago,
     último y próximo pago, estado). Nunca reveles datos de otra propiedad.
  4. Si NO coincide o no puedes verificar: no inventes. Di que por seguridad el asesor
     lo confirmará y toma sus datos. Emite [CONSULTA_PROPIETARIO] igual para registrar.

═══════════════════════════════════════════════
  PASE DIRECTO AL ASESOR HUMANO
═══════════════════════════════════════════════
Emite [PASAR_ASESOR] (ADEMÁS del bloque que corresponda) cuando:
  • El lead está CALIENTE (score alto, listo para comprar/alquilar ya).
  • Es una CAPTACIÓN (siempre se pasa al asesor).
  • El cliente pide expresamente hablar con una persona.
  • Es una negociación de precio o algo que requiere criterio humano.
Dile al cliente con naturalidad: "Te pongo en contacto directo con {asesor}
({telefono_asesor}), que cierra contigo los detalles."

CÓMO ASESORAR COMO EXPERTA:
• Si pide zona, cuéntale de la zona: seguridad, colegios cercanos, transporte.
• Si es inversor, menciona el ROI estimado y el precio por m2 vs mercado.
• Si quiere financiamiento, usa la calculadora: "a 15 años al 12% la cuota sería X".
• Si el presupuesto no alcanza para lo que pide: reorienta sin rechazarlo.
• Si la propiedad está muy bien de precio: crea urgencia legítima.

═══════════════════════════════════════════════
  REGLAS CRÍTICAS
═══════════════════════════════════════════════
- NUNCA inventes propiedades que no estén en el catálogo.
- NUNCA reveles dirección exacta ni datos del propietario sin visita confirmada.
- NUNCA insistas si el cliente claramente no califica.
- Si te preguntan si eres IA: "Soy {asesor}, asesora de {empresa}."
- Comisión de venta: {comision}% (mencionarlo solo si lo preguntan directamente).
- Si el cliente pregunta por algo fuera de tu catálogo: "Ahora mismo no tenemos eso disponible, pero puedo avisarte si llega algo similar. ¿Me dejas tus datos?"
- Si el cliente pide visita sin calificar: pide al menos nombre, teléfono y confirma que puede venir.

═══════════════════════════════════════════════
  PERFILES DE ZONAS (úsalos para asesorar)
═══════════════════════════════════════════════
{zonas_desc}

═══════════════════════════════════════════════
  CONDICIONES DE ALQUILER (dilas cuando el cliente se interese en alquilar)
═══════════════════════════════════════════════
Para alquilar se piden 3 MESES por adelantado, es lo normal del mercado aquí:
  • 1 mes de DEPÓSITO (reembolsable al entregar el inmueble en buen estado)
  • 1 mes ADELANTADO (el primer mes de renta)
  • 1 mes de COMISIÓN ADMINISTRATIVA (contrato, gestión y garantía)
Entonces, para entrar, el cliente paga el canon x 3.
  Ej.: canon $250/mes → para mudarse paga $750 ($250 x 3).
Contrato mínimo de 1 año; el canon se mantiene fijo durante ese año.
Explícalo con seguridad y naturalidad, como algo estándar y transparente.
Preséntalo como una ventaja: "así aseguras tu casa y tu depósito te lo devuelven".

═══════════════════════════════════════════════
  CALCULADORA DE FINANCIAMIENTO
═══════════════════════════════════════════════
Tasas referenciales Venezuela 2025: 12-18% anual. Plazo típico: 10-20 años.
Ejemplo: Casa $46.000 → inicial 20% ($9.200) → préstamo $36.800 → 15 años 12% → cuota ~$440/mes

═══════════════════════════════════════════════
  CONTEXTO DEL CLIENTE
═══════════════════════════════════════════════
{client_context}

═══════════════════════════════════════════════
  CONTRATOS DE ALQUILER (USO INTERNO — solo para el propietario verificado)
═══════════════════════════════════════════════
Estos son los alquileres que administramos. NO los reveles a compradores ni curiosos.
Solo al DUEÑO de cada propiedad, tras verificar su nombre + ID/dirección.
{self._contratos_resumen()}

═══════════════════════════════════════════════
  CATÁLOGO COMPLETO DE PROPIEDADES
═══════════════════════════════════════════════
Catálogo: {len(self.catalogo.get('propiedades', []))} propiedades — San Cristóbal, Táchira y municipios

{self._propiedades_resumen()}

═══════════════════════════════════════════════
  BLOQUES DE CIERRE (INTERNOS — el cliente NO los ve)
═══════════════════════════════════════════════

Cuando AGENDES VISITA (cliente potencial con fecha y hora acordada):
[VISITA_AGENDADA]
Cliente: [nombre completo]
Telefono: [teléfono]
Propiedad_ID: [ID del catálogo, ej. AP001]
Fecha: [día y hora, ej. Martes 14 de mayo 10:00 AM]
Zona: [zona]
Score: [0-10]
Intencion: [vivir | invertir | negocio]
Urgencia: [alta | media | baja]
Notas: [presupuesto, capacidad de pago, observaciones]
[/VISITA_AGENDADA]

Cuando hayas CALIFICADO sin visita aún (alto o bajo interés):
[LEAD_CALIFICADO]
Cliente: [nombre o "Sin nombre"]
Telefono: [teléfono o "No proporcionado"]
Score: [0-10]
Clasificacion: [POTENCIAL | BAJO_INTERES]
Interes: [tipo de propiedad + zona + presupuesto]
Motivo_score: [justificación breve]
[/LEAD_CALIFICADO]

Cuando DESCARTAS (curioso, irrealista, evasivo):
[DESCARTAR_CLIENTE]
Score: [0-2]
Motivo: [razón concreta]
[/DESCARTAR_CLIENTE]

Cuando el cliente pide detalles o brochure de una propiedad:
[BROCHURE_SOLICITADO]
Propiedad_ID: [ID]
Cliente: [nombre o "sin nombre"]
[/BROCHURE_SOLICITADO]

Cuando un lead tibio pide que lo contacten después:
[SEGUIMIENTO_PEDIDO]
Cliente: [nombre]
Telefono: [teléfono]
Cuando: [en cuánto tiempo pide que lo contacten]
Interes: [resumen de qué busca]
[/SEGUIMIENTO_PEDIDO]

Cuando alguien quiere que CAPTEMOS su propiedad (venderla o alquilarla con nosotros):
[CAPTACION_PROPIEDAD]
Propietario: [nombre completo]
Telefono: [teléfono]
Tipo: [casa | apartamento | local | terreno | oficina | galpón]
Operacion: [venta | alquiler]
Zona: [zona/sector]
Precio_pedido_USD: [monto]
Detalles: [m2, habitaciones, baños, estacionamientos, condición]
Notas: [lo relevante, si mandó fotos, etc.]
[/CAPTACION_PROPIEDAD]

Cuando un PROPIETARIO existente consulta por su alquiler:
[CONSULTA_PROPIETARIO]
Propietario: [nombre]
Propiedad_ID: [ID o dirección que dio]
Verificado: [si | no]
Consulta: [qué preguntó]
[/CONSULTA_PROPIETARIO]

Cuando haya que PASAR EL CLIENTE AL ASESOR HUMANO (lead caliente, captación, pide humano):
[PASAR_ASESOR]
Cliente: [nombre]
Telefono: [teléfono]
Motivo: [por qué se pasa: lead caliente / captación / pide humano / negociación]
Resumen: [qué busca o qué ofrece, en una línea]
[/PASAR_ASESOR]

Fecha actual: {datetime.now().strftime("%A %d/%m/%Y")}
"""

    def welcome_message(self) -> str:
        nombre  = self.config.get("asesor_nombre", "Andreína")
        empresa = self.config.get("nombre_empresa", "Hogar Táchira Bienes Raíces")
        return (
            f"¡Hola! Soy {nombre} de {empresa}.\n"
            f"Te ayudo con lo que necesites: comprar o alquilar, poner tu propiedad "
            f"con nosotros, o si ya eres propietario nuestro, con tu alquiler.\n\n"
            f"¿En qué te ayudo hoy?"
        )

    # ── Análisis de foto (el cliente puede mandar foto de propiedad) ──

    def photo_prompt(self) -> str:
        return (
            "El cliente envió una imagen relacionada con una propiedad o consulta inmobiliaria. "
            "Analiza lo que ves:\n"
            "- Si es una foto de una propiedad: describe estado, características visibles, "
            "posibles problemas o ventajas.\n"
            "- Si es un documento (contrato, plano, escritura): comenta los puntos relevantes "
            "que puedas leer.\n"
            "- Si es una foto de zona/barrio: describe el entorno y si te parece bien ubicado.\n"
            "Responde como asesora experta que evalúa la propiedad."
        )

    # ── on_event ────────────────────────────────────────────────────

    def on_event(self, event_type: str, payload: dict, telegram_id: int):
        if self.memory:
            self.memory.save_event(telegram_id, event_type, payload)

        if event_type == "visita":
            if self.memory:
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("cliente"),
                    telefono=payload.get("telefono"),
                )
                score, clasif, motivo = self._extract_score_info("visita", payload)
                self.memory.set_lead_score(telegram_id, score=score,
                                           clasificacion=clasif, motivo=motivo)
            # Google Calendar — visita al calendario del asesor
            cap_calendar(self, payload)
            # Recordatorio al cliente 30 min antes
            cap_reminder(self, telegram_id, payload)
            # Sync Google Sheets CRM
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

        elif event_type == "descarte":
            if self.memory:
                self.memory.set_lead_score(telegram_id, score=1,
                                           clasificacion="descartado",
                                           motivo=payload.get("motivo", ""))

        elif event_type == "brochure":
            self._enviar_brochure(telegram_id, payload.get("propiedad_id", ""))

        elif event_type == "seguimiento":
            # Guardar para seguimiento manual del asesor
            self._notificar_asesor(
                "📋 SEGUIMIENTO PENDIENTE",
                {
                    "Cliente": payload.get("cliente", "—"),
                    "Tel": payload.get("telefono", "—"),
                    "Cuándo": payload.get("cuando", "—"),
                    "Busca": payload.get("interes", "—"),
                },
            )

        elif event_type == "captacion":
            # Nueva propiedad para captar → registrar y avisar al asesor VIP
            if self.memory:
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("propietario"),
                    telefono=payload.get("telefono"),
                )
            self._notificar_asesor(
                "🏠 NUEVA CAPTACIÓN — ¡contactar rápido!",
                {
                    "Propietario": payload.get("propietario", "—"),
                    "Tel": payload.get("telefono", "—"),
                    "Tipo": payload.get("tipo", "—"),
                    "Operación": payload.get("operacion", "—"),
                    "Zona": payload.get("zona", "—"),
                    "Precio pedido": payload.get("precio_pedido_usd", "—"),
                    "Detalles": payload.get("detalles", "—"),
                    "Notas": payload.get("notas", "—"),
                },
            )
            cap_sheets(self, telegram_id, payload, event_type)

        elif event_type == "consulta_propietario":
            if self.memory:
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("propietario"),
                )
            # Si no se pudo verificar, que el asesor lo revise manualmente
            if str(payload.get("verificado", "")).lower().startswith("n"):
                self._notificar_asesor(
                    "🔐 PROPIETARIO SIN VERIFICAR — revisar",
                    {
                        "Propietario": payload.get("propietario", "—"),
                        "Propiedad": payload.get("propiedad_id", "—"),
                        "Consulta": payload.get("consulta", "—"),
                    },
                )

        elif event_type == "pasar_asesor":
            self._notificar_asesor(
                "🔥 CLIENTE PARA EL ASESOR — ¡contactar YA!",
                {
                    "Cliente": payload.get("cliente", "—"),
                    "Tel": payload.get("telefono", "—"),
                    "Motivo": payload.get("motivo", "—"),
                    "Resumen": payload.get("resumen", "—"),
                },
            )

    def _notificar_asesor(self, titulo: str, campos: dict):
        """Envía una alerta al asesor humano (admin_id o dueno_telegram_id del config)."""
        bot = getattr(self, "bot", None)
        admin_id = getattr(self, "admin_id", None) or self.config.get("dueno_telegram_id")
        if not bot or not admin_id:
            return
        try:
            cuerpo = "\n".join(f"{k}: {v}" for k, v in campos.items())
            bot.send_message(admin_id, f"{titulo}\n{cuerpo}")
        except Exception as e:
            logger.warning(f"[{self.tenant_id}] _notificar_asesor falló: {e}")

    def _enviar_brochure(self, telegram_id: int, prop_id: str):
        """Genera y envía PDF brochure de una propiedad al cliente."""
        import threading
        def _send():
            bot = getattr(self, "bot", None)
            if not bot or not prop_id:
                return
            try:
                from fpdf import FPDF

                prop = None
                for p in self.catalogo.get("propiedades", []):
                    if p["id"].upper() == prop_id.upper():
                        prop = p
                        break
                if not prop:
                    return

                def _s(t): return str(t).encode("latin-1", errors="replace").decode("latin-1")

                op     = prop["operacion"]
                precio = f"${prop['precio_usd']:,}" + ("/mes" if op == "alquiler" else "")
                nombre_empresa = self.config.get("nombre_empresa", self.DISPLAY_NAME)
                asesor         = self.config.get("asesor_nombre", "Andreína")
                telefono       = self.config.get("telefono_asesor", "")

                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 18)
                pdf.set_fill_color(30, 60, 120)
                pdf.set_text_color(255, 255, 255)
                pdf.cell(0, 12, _s(nombre_empresa), ln=True, fill=True, align="C")

                pdf.set_text_color(0, 0, 0)
                pdf.set_font("Helvetica", "B", 14)
                pdf.ln(4)
                pdf.cell(0, 8, _s(f"{prop['tipo'].upper()} EN {prop['zona'].upper()} — {prop['operacion'].upper()}"), ln=True)
                pdf.set_font("Helvetica", "B", 20)
                pdf.set_text_color(30, 60, 120)
                pdf.cell(0, 12, _s(precio), ln=True)
                pdf.set_text_color(0, 0, 0)

                pdf.set_font("Helvetica", "", 11)
                pdf.cell(0, 6, _s(f"Ref: {prop['id']} | Sector: {prop.get('sector','')}"), ln=True)
                pdf.ln(4)

                # Características
                pdf.set_font("Helvetica", "B", 12)
                pdf.set_fill_color(230, 240, 255)
                pdf.cell(0, 7, "  CARACTERISTICAS", ln=True, fill=True)
                pdf.set_font("Helvetica", "", 11)
                pdf.ln(2)

                features = []
                if prop.get("m2"):        features.append(f"Superficie: {prop['m2']} m2")
                if prop.get("habitaciones"): features.append(f"Habitaciones: {prop['habitaciones']}")
                if prop.get("banos"):     features.append(f"Banos: {prop['banos']}")
                if prop.get("estacionamientos"): features.append(f"Estacionamientos: {prop['estacionamientos']}")
                features.append(f"Amueblado: {'Si' if prop.get('amueblado') else 'No'}")
                features.append(f"Condicion: {prop.get('condicion','')}")
                features.append(f"Disponible: {prop.get('disponible_desde','inmediato')}")

                col_w = 95
                for i in range(0, len(features), 2):
                    left  = features[i]
                    right = features[i+1] if i+1 < len(features) else ""
                    pdf.cell(col_w, 6, _s(left))
                    pdf.cell(col_w, 6, _s(right), ln=True)

                # Amenidades
                amenidades = prop.get("amenidades", [])
                if amenidades:
                    pdf.ln(4)
                    pdf.set_font("Helvetica", "B", 12)
                    pdf.set_fill_color(230, 240, 255)
                    pdf.cell(0, 7, "  AMENIDADES", ln=True, fill=True)
                    pdf.set_font("Helvetica", "", 11)
                    pdf.ln(2)
                    for am in amenidades:
                        pdf.cell(0, 5, _s(f"  + {am}"), ln=True)

                # Descripción
                pdf.ln(4)
                pdf.set_font("Helvetica", "B", 12)
                pdf.set_fill_color(230, 240, 255)
                pdf.cell(0, 7, "  DESCRIPCION", ln=True, fill=True)
                pdf.set_font("Helvetica", "", 11)
                pdf.ln(2)
                pdf.multi_cell(0, 6, _s(prop.get("descripcion", "")))

                # Info inversión
                inv = prop.get("inversion_estimada")
                if inv:
                    pdf.ln(4)
                    pdf.set_font("Helvetica", "B", 12)
                    pdf.set_fill_color(200, 255, 200)
                    pdf.cell(0, 7, "  POTENCIAL DE INVERSION", ln=True, fill=True)
                    pdf.set_font("Helvetica", "", 11)
                    pdf.ln(2)
                    for k, v in inv.items():
                        pdf.cell(0, 5, _s(f"  {k.replace('_',' ').title()}: {v}"), ln=True)

                # Zona
                zona_info = self._zona_info(prop["zona"])
                if zona_info:
                    pdf.ln(4)
                    pdf.set_font("Helvetica", "B", 12)
                    pdf.set_fill_color(255, 245, 200)
                    pdf.cell(0, 7, "  SOBRE LA ZONA", ln=True, fill=True)
                    pdf.set_font("Helvetica", "", 11)
                    pdf.ln(2)
                    pdf.multi_cell(0, 6, _s(zona_info))

                # Contacto
                pdf.ln(6)
                pdf.set_font("Helvetica", "B", 11)
                pdf.set_fill_color(30, 60, 120)
                pdf.set_text_color(255, 255, 255)
                pdf.cell(0, 8, _s(f"  Asesora: {asesor} | Tel: {telefono} | {nombre_empresa}"), ln=True, fill=True)

                import io
                pdf_bytes = bytes(pdf.output())
                filename  = f"brochure_{prop_id}.pdf"
                bot.send_document(
                    telegram_id, (filename, io.BytesIO(pdf_bytes)),
                    caption=_s(f"Ficha completa: {prop['tipo'].capitalize()} en {prop['zona']} — {precio}")
                )
                logger.info(f"[{self.tenant_id}] Brochure enviado: {prop_id} a {telegram_id}")

            except Exception as e:
                logger.warning(f"[{self.tenant_id}] Error enviando brochure: {e}")

        threading.Thread(target=_send, daemon=True, name="brochure").start()

    @staticmethod
    def _extract_score_info(event_type: str, payload: dict) -> tuple:
        try:
            score = int("".join(c for c in str(payload.get("score", "0")) if c.isdigit()) or 0)
        except Exception:
            score = 0

        if event_type == "visita":
            return max(score, 7), "potencial", payload.get("notas", "Visita agendada")
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

        def cmd_potenciales(message, bot, profile, memory):
            leads = memory.leads_by_classification("potencial")
            if not leads:
                bot.reply_to(message, "Sin leads potenciales por ahora.")
                return
            lines = [f"LEADS POTENCIALES ({len(leads)}):\n"]
            for ld in leads[:20]:
                lines.append(
                    f"• {ld.get('nombre') or ld['telegram_id']} "
                    f"(score {ld['score']}/10) | tel: {ld.get('telefono') or 'n/a'} | {ld.get('motivo','')[:60]}"
                )
            bot.reply_to(message, "\n".join(lines))

        def cmd_visitas(message, bot, profile, memory):
            eventos = memory.events_today("visita")
            if not eventos:
                bot.reply_to(message, "Sin visitas agendadas hoy.")
                return
            lines = [f"VISITAS DE HOY ({len(eventos)}):\n"]
            for e in eventos:
                try:
                    p = json.loads(e["payload"]) if isinstance(e.get("payload"), str) else e.get("payload", {})
                    lines.append(
                        f"• {p.get('cliente','?')} — {p.get('propiedad_id','?')} "
                        f"| {p.get('fecha','?')} | tel: {p.get('telefono','?')}"
                    )
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_descartados(message, bot, profile, memory):
            leads = memory.leads_by_classification("descartado")
            bot.reply_to(message, f"Clientes descartados (curiosos/no calificados): {len(leads)}")

        def cmd_seguimiento(message, bot, profile, memory):
            leads = memory.leads_by_classification("bajo_interes")
            if not leads:
                bot.reply_to(message, "Sin leads para seguimiento por ahora.")
                return
            lines = [f"LEADS PARA SEGUIMIENTO ({len(leads)}):\n"]
            for ld in leads[:15]:
                lines.append(
                    f"• {ld.get('nombre') or ld['telegram_id']} "
                    f"| tel: {ld.get('telefono') or 'n/a'} | {ld.get('motivo','')[:60]}"
                )
            lines.append("\nEscríbeles para reactivar su interés.")
            bot.reply_to(message, "\n".join(lines))

        def cmd_resumen(message, bot, profile, memory):
            pot    = len(memory.leads_by_classification("potencial"))
            bajo   = len(memory.leads_by_classification("bajo_interes"))
            desc   = len(memory.leads_by_classification("descartado"))
            visitas = len(memory.events_today("visita"))
            total  = pot + bajo + desc
            bot.reply_to(
                message,
                f"RESUMEN INMOBILIARIA — {datetime.now().strftime('%d/%m/%Y')}\n\n"
                f"Visitas hoy: {visitas}\n"
                f"Leads potenciales: {pot}\n"
                f"Para seguimiento: {bajo}\n"
                f"Descartados: {desc}\n"
                f"Total leads: {total}\n\n"
                f"Conversion: {round(pot/total*100) if total else 0}% potenciales"
            )

        def cmd_inventario(message, bot, profile, memory):
            """Muestra resumen del catálogo disponible."""
            props = profile.catalogo.get("propiedades", [])
            ventas   = [p for p in props if p["operacion"] == "venta"]
            alquiler = [p for p in props if p["operacion"] == "alquiler"]
            tipos = {}
            for p in props:
                tipos[p["tipo"]] = tipos.get(p["tipo"], 0) + 1
            lines = [f"CATÁLOGO — {len(props)} propiedades\n"]
            lines.append(f"  En venta: {len(ventas)}")
            lines.append(f"  En alquiler: {len(alquiler)}")
            lines.append("\nPor tipo:")
            for t, n in sorted(tipos.items()):
                lines.append(f"  {t.capitalize()}: {n}")
            bot.reply_to(message, "\n".join(lines))

        def cmd_captaciones(message, bot, profile, memory):
            eventos = memory.get_all_events_since("2000-01-01")
            caps = [e for e in eventos if e.get("event_type") == "captacion"]
            if not caps:
                bot.reply_to(message, "Sin captaciones registradas todavía.")
                return
            lines = [f"CAPTACIONES ({len(caps)}):\n"]
            for e in caps[-20:]:
                try:
                    p = json.loads(e["payload"]) if isinstance(e.get("payload"), str) else e.get("payload", {})
                    lines.append(
                        f"• {p.get('propietario','?')} — {p.get('tipo','?')} "
                        f"{p.get('operacion','?')} en {p.get('zona','?')} "
                        f"| ${p.get('precio_pedido_usd','?')} | tel: {p.get('telefono','?')}"
                    )
                except Exception:
                    pass
            bot.reply_to(message, "\n".join(lines))

        def cmd_contratos(message, bot, profile, memory):
            contratos = getattr(profile, "contratos", {}).get("contratos", [])
            if not contratos:
                bot.reply_to(message, "Sin contratos de alquiler cargados.")
                return
            lines = [f"CONTRATOS DE ALQUILER ({len(contratos)}):\n"]
            for c in contratos:
                lines.append(
                    f"• [{c.get('propiedad_id','?')}] {c.get('propietario','?')} "
                    f"→ inquilino {c.get('inquilino','?')} | ${c.get('canon_usd','?')}/mes "
                    f"| paga el {c.get('dia_pago','?')} | {c.get('estado','?')} "
                    f"| próx: {c.get('proximo_pago','—')}"
                )
            bot.reply_to(message, "\n".join(lines))

        return {
            "potenciales":  cmd_potenciales,
            "visitas":      cmd_visitas,
            "descartados":  cmd_descartados,
            "seguimiento":  cmd_seguimiento,
            "resumen":      cmd_resumen,
            "catalogo":     cmd_inventario,
            "captaciones":  cmd_captaciones,
            "contratos":    cmd_contratos,
            "leads_excel":  make_cmd_historia_excel(),
            "crear_hoja":   make_cmd_crear_hoja(),
            "subir_reporte": make_cmd_subir_reporte(folder_name="Reportes Inmobiliaria"),
            "reporte_semanal": make_cmd_reporte_semanal(),
        }


PROFILE_CLASS = InmobiliariaProfile
