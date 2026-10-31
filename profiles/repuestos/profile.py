"""
Perfil Repuestos Automotriz — Chevrolet y Ford

Capacidades activas:
  - Búsqueda de repuestos por modelo + pieza + texto libre
  - Identificación de pieza por FOTO (Gemini Vision)
  - Búsqueda de similares/compatibles si no hay stock exacto
  - Verificación de comprobante de pago por imagen
  - Control de inventario (stock, mínimo, alerta)
  - Reducción automática de stock al confirmar venta
  - Reporte diario de ventas (PDF + Email)
  - Alerta de stock bajo automática al dueño
  - Historial de clientes en Google Sheets
  - Registro de piezas pedidas sin stock (para reposición)
"""

import json
import logging
from datetime import datetime
from profiles._base_profile import BaseProfile
from core.capabilities import (
    cap_sheets,
    cap_inventory_check,
    cap_inventory_reduce,
    cap_payment_prompt,
    cap_low_stock_alert,
    cap_broadcast,
    cap_verify_payment,
    make_cmd_reporte_pdf,
    make_cmd_historia_excel,
    make_cmd_subir_reporte,
    make_cmd_crear_hoja,
    make_cmd_enviar_reporte,
    make_cmd_broadcast,
    make_cmd_reporte_semanal,
    make_cmd_reporte_mensual,
)

logger = logging.getLogger(__name__)


class RepuestosProfile(BaseProfile):
    NAME         = "repuestos"
    DISPLAY_NAME = "Automotor Repuestos"
    LOCATION     = "La Concordia, San Cristóbal, Táchira"
    TEMPERATURE  = 0.4   # precisión al capturar piezas/cantidades/precios

    EVENT_TAGS = {
        "VENTA_CONFIRMADA":    "venta",
        "COTIZACION_ENVIADA":  "cotizacion",
        "PAGO_PARA_VERIFICAR": "verificar_pago",
        "PAGO_VERIFICADO":     "pago",
        "PARTE_NO_ENCONTRADA": "sin_stock",
    }

    # ── Helpers de catálogo ───────────────────────────────────────

    def _catalogo_texto(self) -> str:
        """Convierte el catálogo JSON en texto legible para la IA."""
        productos = self.catalogo.get("productos", [])
        por_marca: dict[str, list] = {}
        for p in productos:
            marca = p.get("marca_auto", "Universal")
            por_marca.setdefault(marca, []).append(p)

        lines = []
        for marca, items in por_marca.items():
            lines.append(f"\n=== {marca} ===")
            for p in items:
                stock_txt = "DISPONIBLE" if p["stock"] > 0 else "SIN STOCK"
                modelos = ", ".join(p.get("modelos_compatibles", []))
                lines.append(
                    f"  [{p['id']}] {p['nombre']} — ${p['precio_usd']:.2f} | {stock_txt}"
                    f"\n    Modelos: {modelos} | Motor: {p.get('motor','todos')}"
                    f"\n    {p.get('descripcion','')}"
                )
        return "\n".join(lines)

    def _stock_bajo(self) -> list[dict]:
        """Retorna productos cuyo stock está en o bajo el mínimo."""
        productos = self.catalogo.get("productos", [])
        return [
            p for p in productos
            if p.get("stock", 0) <= p.get("stock_minimo", 0)
        ]

    def _buscar_en_catalogo(self, texto: str, modelo_auto: str = "") -> list[dict]:
        """Búsqueda fuzzy por texto + modelo en el catálogo."""
        palabras = texto.lower().split()
        modelo_lower = modelo_auto.lower()
        resultados = []
        for p in self.catalogo.get("productos", []):
            nombre_l = p["nombre"].lower()
            desc_l   = p.get("descripcion", "").lower()
            modelos_l = " ".join(p.get("modelos_compatibles", [])).lower()

            coincide_texto  = any(w in nombre_l or w in desc_l for w in palabras)
            coincide_modelo = not modelo_auto or modelo_lower in modelos_l
            if coincide_texto and coincide_modelo:
                resultados.append(p)
        return resultados

    def _faq_texto(self) -> str:
        faqs = self.config.get("faq", [])
        return "\n\n".join(f"P: {f['pregunta']}\nR: {f['respuesta']}" for f in faqs)

    def _horario_texto(self) -> str:
        h = self.config.get("horario", {})
        return "\n".join(f"  {d.capitalize()}: {hr}" for d, hr in h.items())

    # ── Prompt principal ──────────────────────────────────────────

    def build_system_prompt(self, client_context: str = "") -> str:
        negocio  = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        asist    = self.config.get("asistente_nombre", "Carlos")
        dueno    = self.config.get("dueno_nombre", "el dueño")
        metodos  = ", ".join(self.config.get("metodos_pago", ["Pago Móvil", "USD"]))
        garantia = self.config.get("garantia_dias", 30)

        return f"""Eres {asist}, el asistente de ventas de '{negocio}' en {self.LOCATION}.
Atiendes por Telegram. Eres amable, conocedor de repuestos y orientado a cerrar ventas.
Tu meta es ayudar al cliente a encontrar EXACTAMENTE lo que necesita y concretar la venta.

═══════════════════════════════════════════════
  TU MISIÓN
═══════════════════════════════════════════════
1. Identificar qué repuesto necesita el cliente (por descripción, foto o código).
2. Buscar en el inventario si está disponible.
3. Si no hay stock exacto → ofrecer similares o compatibles del catálogo.
4. Cotizar y confirmar el precio.
5. Recibir y verificar el pago.
6. Cerrar la venta con el bloque correcto.

═══════════════════════════════════════════════
  FLUJO DE ATENCIÓN
═══════════════════════════════════════════════
PASO 1 — Saludar y preguntar: "¿Para qué modelo de vehículo necesitas el repuesto?
         ¿Chevrolet o Ford? ¿Qué año y motor?"

PASO 2 — Preguntar qué pieza necesita. Puede ser por nombre, síntoma del carro o foto.
         Si manda foto → identifica la pieza visualmente y busca en inventario.

PASO 3 — Buscar en catálogo. Mostrar precio, stock disponible y referencia.
         NUNCA ofrecer algo que tenga stock = 0.

PASO 4 — Si NO hay en stock → busca similares: misma categoría, compatible con el modelo.
         Di: "No tenemos ese exacto, pero tenemos [X] que es compatible con tu modelo."
         Y registra lo que pidió sin stock en el bloque [PARTE_NO_ENCONTRADA].

PASO 5 — Dar el precio final. Informar métodos de pago: {metodos}.

PASO 6 — Cuando el cliente confirme la compra → pedir el pago.
         Opciones:
         a) El cliente manda el número de referencia y el monto → emite [PAGO_PARA_VERIFICAR]
            (el sistema verifica automáticamente en el banco y te avisa)
         b) El cliente manda foto del comprobante → analizas visualmente

PASO 7 — Si la verificación automática confirma → cerrar con [VENTA_CONFIRMADA].
         Si hay dudas o no encuentra la referencia → pedir foto del comprobante.

═══════════════════════════════════════════════
  REGLAS CRÍTICAS
═══════════════════════════════════════════════
- NUNCA confirmes una venta sin verificar el pago primero.
- NUNCA ofrezcas un producto con stock = 0. Si no hay, dilo y busca alternativa.
- NUNCA menciones la cantidad exacta de unidades en stock. Solo di "tenemos disponible" o "no tenemos en este momento".
- No adivines compatibilidades que no estén en el catálogo. Usa solo los datos del inventario.
- Si preguntan si eres IA: "Soy {asist}, el asistente de {negocio}."
- Garantía de {garantia} días por defecto de fabricación. Aclararlo al cerrar.
- Si piden algo que no es Chevrolet ni Ford: "Por ahora estamos especializados en Chevrolet
  y Ford. ¿Tienes alguno de esos?"
- Registra SIEMPRE en [PARTE_NO_ENCONTRADA] todo lo que pidan y no esté disponible.
  Eso le sirve al dueño para saber qué reponer.

═══════════════════════════════════════════════
  CONTEXTO DEL CLIENTE
═══════════════════════════════════════════════
{client_context}

═══════════════════════════════════════════════
  INVENTARIO COMPLETO (con precios y stock)
═══════════════════════════════════════════════
{self._catalogo_texto()}

═══════════════════════════════════════════════
  HORARIO
═══════════════════════════════════════════════
{self._horario_texto()}

Fecha y hora actual: {datetime.now().strftime("%A %d/%m/%Y %H:%M")}

═══════════════════════════════════════════════
  PREGUNTAS FRECUENTES
═══════════════════════════════════════════════
{self._faq_texto()}

═══════════════════════════════════════════════
  BLOQUES DE CIERRE (INTERNOS — el cliente NO los ve)
═══════════════════════════════════════════════

Al confirmar la venta Y verificar el pago:
[VENTA_CONFIRMADA]
Cliente: [nombre]
Telefono: [teléfono]
Producto_ID: [id del producto vendido]
Producto: [nombre del repuesto]
Cantidad: [cantidad]
Total_USD: [monto total]
Pago: [método de pago]
Modelo_Auto: [marca y modelo del vehículo]
[/VENTA_CONFIRMADA]

Al enviar cotización sin confirmar aún:
[COTIZACION_ENVIADA]
Cliente: [nombre o "sin nombre"]
Producto: [repuesto consultado]
Precio_USD: [precio]
Modelo_Auto: [modelo del vehículo]
[/COTIZACION_ENVIADA]

Cuando el cliente da su número de referencia bancaria (Pago Móvil, transferencia, Zelle):
Responde: "Perfecto, verificando tu pago automáticamente..." y emite:
[PAGO_PARA_VERIFICAR]
Referencia: [número de referencia que dio el cliente]
Monto_Declarado: [monto que mencionó el cliente, en números]
Metodo: [Pago Móvil | Transferencia | Zelle | Binance | Efectivo]
[/PAGO_PARA_VERIFICAR]

Cuando el comprobante (foto) es válido:
[PAGO_VERIFICADO]
Monto: [monto visible]
Metodo: [tipo de pago]
Valido: [SI | DUDOSO | NO]
[/PAGO_VERIFICADO]

Cuando pidan algo que NO está en stock ni como similar:
[PARTE_NO_ENCONTRADA]
Pieza: [nombre de la pieza solicitada]
Modelo_Auto: [modelo del vehículo]
Motivo: [sin_stock | no_manejamos | solo_chevrolet_ford]
[/PARTE_NO_ENCONTRADA]
"""

    def welcome_message(self) -> str:
        asist   = self.config.get("asistente_nombre", "Carlos")
        negocio = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        return (
            f"¡Hola! Soy {asist} de {negocio}.\n"
            f"Tenemos repuestos Chevrolet y Ford. ¿Para qué modelo necesitas algo?"
        )

    # ── Análisis de fotos de repuestos ────────────────────────────

    def photo_prompt(self) -> str:
        negocio = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        modelos_chv = ", ".join(self.config.get("modelos_chevrolet",
                     ["Aveo", "Optra", "Captiva", "Spark"]))
        modelos_ford = ", ".join(self.config.get("modelos_ford",
                      ["Explorer", "F-150", "Focus", "Escape"]))

        catalogo_ids = "\n".join(
            f"  [{p['id']}] {p['nombre']} — modelos: {', '.join(p.get('modelos_compatibles',[]))}"
            for p in self.catalogo.get("productos", [])
            if p.get("stock", 0) > 0
        )

        return (
            f"El cliente de {negocio} envió una foto de un repuesto o pieza automotriz.\n\n"
            f"Modelos que manejamos: Chevrolet ({modelos_chv}) y Ford ({modelos_ford}).\n\n"
            f"Analiza la imagen:\n"
            f"1. Identifica qué tipo de pieza es (filtro, pastillas, amortiguador, sensor, etc.).\n"
            f"2. Intenta leer la referencia/código si está visible en la pieza.\n"
            f"3. Busca en nuestro inventario disponible si coincide con algo:\n"
            f"{catalogo_ids}\n\n"
            f"4. Si encuentras coincidencia: muestra el producto, precio y stock disponible.\n"
            f"5. Si no hay match exacto: busca una pieza similar o compatible de la misma categoría.\n"
            f"6. Sé específico sobre qué es la pieza para que el cliente confirme.\n\n"
            f"Responde de forma conversacional, como un mecánico amable que conoce el catálogo."
        )

    # ── on_event ─────────────────────────────────────────────────

    def on_event(self, event_type: str, payload: dict, telegram_id: int):
        if self.memory:
            self.memory.save_event(telegram_id, event_type, payload)

        if event_type == "venta":
            if self.memory:
                self.memory.upsert_client(
                    telegram_id,
                    nombre=payload.get("cliente"),
                    telefono=payload.get("telefono"),
                )
            # Reducir stock del producto vendido
            producto_id = payload.get("producto_id", "")
            cantidad    = int(payload.get("cantidad", 1))
            nombre_prod = payload.get("producto", "")
            self._reducir_stock(producto_id or nombre_prod, cantidad)
            # Sync Google Sheets
            cap_sheets(self, telegram_id, payload, event_type)
            # Verificar si quedó en stock mínimo → notificar dueño
            for p in self.catalogo.get("productos", []):
                if nombre_prod.lower() in p["nombre"].lower() or p["id"] == producto_id:
                    cap_low_stock_alert(self, p)
                    break

        elif event_type == "verificar_pago":
            # Verificación automática por referencia bancaria en Gmail / Binance API
            cap_verify_payment(self, telegram_id, payload)

        elif event_type == "cotizacion":
            if self.memory:
                self.memory.upsert_client(telegram_id)
            cap_sheets(self, telegram_id, payload, event_type)

        elif event_type == "sin_stock":
            logger.info(
                f"[{self.tenant_id}] Sin stock: {payload.get('pieza')} "
                f"para {payload.get('modelo_auto')}"
            )
            # El registro en BD permite al dueño ver qué piden y no hay
            cap_sheets(self, telegram_id, payload, event_type)

    def _reducir_stock(self, identificador: str, cantidad: int = 1):
        """Reduce stock por ID o por nombre aproximado."""
        import json as _json, os
        catalog_path = None
        for base in [self.tenant_dir, self.profile_dir]:
            if base:
                path = os.path.join(base, "catalogo.json")
                if os.path.exists(path):
                    catalog_path = path
                    break
        if not catalog_path:
            return

        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                cat = _json.load(f)
            for p in cat.get("productos", []):
                if (p["id"] == identificador or
                        identificador.lower() in p["nombre"].lower()):
                    p["stock"] = max(0, p.get("stock", 0) - cantidad)
                    with open(catalog_path, "w", encoding="utf-8") as f:
                        _json.dump(cat, f, ensure_ascii=False, indent=2)
                    logger.info(f"Stock reducido: {p['nombre']} → {p['stock']} unidades")
                    # Actualizar catálogo en memoria
                    self.catalogo = cat
                    return
        except Exception as e:
            logger.warning(f"No se pudo reducir stock: {e}")

    # ── Admin commands ────────────────────────────────────────────

    def admin_commands(self) -> dict:

        def cmd_stock_bajo(message, bot, profile, memory):
            bajos = profile._stock_bajo()
            if not bajos:
                bot.reply_to(message, "Todo el inventario está sobre el mínimo.")
                return
            lines = [f"REPUESTOS A REPONER ({len(bajos)}):\n"]
            for p in bajos:
                emoji = "🔴" if p["stock"] == 0 else "🟡"
                lines.append(
                    f"{emoji} {p['nombre']}\n"
                    f"   Stock: {p['stock']} | Mínimo: {p['stock_minimo']}\n"
                    f"   Modelo: {', '.join(p.get('modelos_compatibles',[]))}\n"
                    f"   ID: {p['id']}"
                )
            bot.reply_to(message, "\n".join(lines))

        def cmd_sin_stock_pedidos(message, bot, profile, memory):
            """Muestra qué piezas pedidas no estaban en stock."""
            eventos = memory.events_today("sin_stock")
            if not eventos:
                bot.reply_to(message, "No hubo piezas solicitadas sin stock hoy.")
                return
            lines = [f"PIEZAS PEDIDAS SIN STOCK HOY ({len(eventos)}):\n"]
            for ev in eventos:
                try:
                    p = json.loads(ev["payload"])
                    lines.append(
                        f"  • {p.get('pieza','?')} "
                        f"[{p.get('modelo_auto','?')}]"
                    )
                except Exception:
                    pass
            lines.append("\nConsidera reponer estos repuestos.")
            bot.reply_to(message, "\n".join(lines))

        def cmd_ventas_hoy(message, bot, profile, memory):
            ventas = memory.events_today("venta")
            cotiz  = memory.events_today("cotizacion")
            total  = 0.0
            lines  = [f"VENTAS HOY — {datetime.now().strftime('%d/%m/%Y')}\n"]
            for ev in ventas:
                try:
                    p = json.loads(ev["payload"])
                    monto = float(p.get("total_usd", 0))
                    total += monto
                    lines.append(
                        f"  ${monto:.2f} — {p.get('producto','?')} "
                        f"| {p.get('cliente','?')} | {p.get('modelo_auto','?')}"
                    )
                except Exception:
                    pass
            lines.append(f"\nTotal ventas: ${total:.2f} USD")
            lines.append(f"Cotizaciones enviadas: {len(cotiz)}")
            bot.reply_to(message, "\n".join(lines))

        def cmd_inventario_full(message, bot, profile, memory):
            """Muestra inventario completo con stock."""
            productos = profile.catalogo.get("productos", [])
            lines = [f"INVENTARIO — {datetime.now().strftime('%d/%m/%Y')}\n"]
            for p in productos:
                emoji = "🟢" if p["stock"] > p["stock_minimo"] else ("🟡" if p["stock"] > 0 else "🔴")
                lines.append(
                    f"{emoji} {p['nombre']}\n"
                    f"   Stock: {p['stock']} | Precio: ${p['precio_usd']} | ID: {p['id']}"
                )
            bot.reply_to(message, "\n".join(lines))

        return {
            "stock_bajo":        cmd_stock_bajo,
            "pedidos_sin_stock": cmd_sin_stock_pedidos,
            "ventas_hoy":        cmd_ventas_hoy,
            "inventario":        cmd_inventario_full,
            "reporte_pdf":       make_cmd_reporte_pdf(),
            "historia_excel":    make_cmd_historia_excel(),
            "subir_reporte":     make_cmd_subir_reporte(
                                  folder_name="Reportes Automotor"),
            "crear_hoja":        make_cmd_crear_hoja(),
            "enviar_reporte":    make_cmd_enviar_reporte(),
            "broadcast":         make_cmd_broadcast(),
            "reporte_semanal":   make_cmd_reporte_semanal(),
            "reporte_mensual":   make_cmd_reporte_mensual(),
        }


PROFILE_CLASS = RepuestosProfile
