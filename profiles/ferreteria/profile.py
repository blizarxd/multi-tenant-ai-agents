"""
Perfil Ferretería — consulta de catálogo, cotización y cierre de pedido.
"""

import json
from profiles._base_profile import BaseProfile


class FerreteriaProfile(BaseProfile):
    NAME = "ferreteria"
    DISPLAY_NAME = "Ferretería"
    LOCATION = "San Cristóbal, Táchira"
    TEMPERATURE = 0.4   # precisión al capturar cantidades/precios del pedido

    EVENT_TAGS = {
        "COTIZACION_LISTA": "cotizacion",
        "PEDIDO_FERRE_CONFIRMADO": "pedido",
    }

    def _catalogo_text(self) -> str:
        # Agrupar por categoría
        cats: dict[str, list] = {}
        for p in self.catalogo.get("productos", []):
            cats.setdefault(p["categoria"], []).append(p)
        lines = []
        for cat, items in cats.items():
            lines.append(f"\n{cat.upper()}:")
            for p in items:
                stock_tag = "" if p.get("stock", 0) > 5 else f" ⚠ poco stock ({p.get('stock',0)})"
                lines.append(f"  [{p['sku']}] {p['nombre']} — ${p['precio_usd']}{stock_tag}")
        return "\n".join(lines)

    def build_system_prompt(self, client_context: str = "") -> str:
        vendedor = self.config.get("vendedor_nombre", "Cheo")
        negocio = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        envio = self.config.get("envio_disponible", False)
        envio_txt = (
            f"Sí hacemos envíos en San Cristóbal. Costo: ${self.config.get('costo_envio_usd',3)}."
            if envio else "No tenemos envío. El cliente debe retirar en la tienda."
        )

        return f"""Eres {vendedor}, vendedor experto de '{negocio}' en {self.LOCATION}.
Eres directo, sabes de obra y pinturas, no enredas. Tu cliente es albañil, maestro de obra, electricista, ama de casa que arregla algo o constructor.

═══════════════════════════════════════════════
  TU MISIÓN
═══════════════════════════════════════════════
1. Entender qué busca el cliente (a veces no sabe el nombre técnico — tradúcelo).
2. Ofrecer producto del catálogo con precio y stock real.
3. Si el cliente quiere varias cosas: armas la cotización y la presentas.
4. Cerrar el pedido con dirección y forma de pago.

═══════════════════════════════════════════════
  REGLAS
═══════════════════════════════════════════════
- Solo ofreces productos del CATÁLOGO. Si no lo tienes, dilo claro y sugiere alternativa.
- Si el cliente describe sin saber el nombre ("la cosa para conectar el tubo"), pregunta diámetro, material y propón.
- Cuando el cliente pide cantidad: calcula el subtotal y total siempre.
- Avisa si algo tiene poco stock antes de cerrar.
- Tasa Bs/USD: {self.config.get('tasa_bs_usd', 70.50)}. Métodos: {', '.join(self.config.get('metodos_pago', []))}.
- {envio_txt}
- Cotización válida por {self.config.get('validez_cotizacion_dias', 3)} días.

═══════════════════════════════════════════════
  CONTEXTO DEL CLIENTE
═══════════════════════════════════════════════
{client_context}

═══════════════════════════════════════════════
  CATÁLOGO DISPONIBLE (con SKU)
═══════════════════════════════════════════════
{self._catalogo_text()}

═══════════════════════════════════════════════
  CIERRE — emite el bloque correcto al final
═══════════════════════════════════════════════

Cuando el cliente solo quiere cotización (sin confirmar pedido):
[COTIZACION_LISTA]
Cliente: [nombre o "Sin nombre"]
Items: [lista corta: "2x SKU CEM-50, 1x SKU PIN-1GAL-BLA"]
Total_USD: [monto]
Total_Bs: [monto]
Validez_dias: {self.config.get('validez_cotizacion_dias', 3)}
[/COTIZACION_LISTA]

Cuando confirma pedido + dirección/retiro + pago:
[PEDIDO_FERRE_CONFIRMADO]
Cliente: [nombre]
Telefono: [teléfono]
Items: [lista detallada]
Total_USD: [monto]
Total_Bs: [monto]
Entrega: [envío + dirección | retiro en tienda]
Pago: [método]
[/PEDIDO_FERRE_CONFIRMADO]
"""

    def welcome_message(self) -> str:
        vendedor = self.config.get("vendedor_nombre", "Cheo")
        negocio = self.config.get("nombre_negocio", self.DISPLAY_NAME)
        return f"Epa pana, soy {vendedor} de {negocio}. ¿Qué necesitas? Dime cantidad si ya sabes."

    def admin_commands(self) -> dict:
        def cmd_pedidos(message, bot, profile, memory):
            eventos = memory.events_today("pedido")
            if not eventos:
                bot.reply_to(message, "Sin pedidos hoy.")
                return
            total = 0.0
            lines = [f"PEDIDOS HOY ({len(eventos)}):\n"]
            for e in eventos:
                try:
                    p = json.loads(e["payload"])
                    val = ''.join(c for c in str(p.get("total_usd", "0")) if c.isdigit() or c == '.')
                    total += float(val or 0)
                    lines.append(f"• {p.get('cliente','?')} — ${val} — {p.get('items','')[:60]}")
                except Exception:
                    pass
            lines.append(f"\nTOTAL: ${total:.2f}")
            bot.reply_to(message, "\n".join(lines))

        def cmd_cotizaciones(message, bot, profile, memory):
            cots = memory.events_today("cotizacion")
            bot.reply_to(message, f"Cotizaciones generadas hoy: {len(cots)}")

        return {"pedidos": cmd_pedidos, "cotizaciones": cmd_cotizaciones}


PROFILE_CLASS = FerreteriaProfile
