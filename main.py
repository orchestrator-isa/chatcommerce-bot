#!/usr/bin/env python3
"""
Orquestrator ISA — ChatCommerce Bot
FastAPI backend para WhatsApp Business API + Supabase
Schema: restaurantes, clientes, conversaciones, mensajes, pedidos, platos, menus
Deploy: Render.com
"""

# ───────────────────────────────────────────
# CARGAR .env PRIMERO (antes de cualquier import)
# ───────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()  # Carga variables de .env antes de todo

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from supabase import create_client, Client
import httpx

# ───────────────────────────────────────────
# CONFIGURACION / LOGGING
# ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("isa-bot")

# Variables de entorno (Render o .env)
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
META_API_VERSION = os.getenv("META_API_VERSION", "v18.0")

logger.info(f"[INIT] WHATSAPP_TOKEN presente: {bool(WHATSAPP_TOKEN)}")
logger.info(f"[INIT] VERIFY_TOKEN cargado: '{VERIFY_TOKEN}'")
logger.info(f"[INIT] VERIFY_TOKEN tipo: {type(VERIFY_TOKEN)}")
logger.info(f"[INIT] VERIFY_TOKEN len: {len(VERIFY_TOKEN)}")
logger.info(f"[INIT] VERIFY_TOKEN bytes: {VERIFY_TOKEN.encode()}")
logger.info(f"[INIT] PHONE_NUMBER_ID: {PHONE_NUMBER_ID}")
logger.info(f"[INIT] SUPABASE_URL presente: {bool(SUPABASE_URL)}")
logger.info(f"[INIT] SUPABASE_KEY presente: {bool(SUPABASE_KEY)}")
logger.info(f"[INIT] SUPABASE_KEY primeros 20 chars: {SUPABASE_KEY[:20] if SUPABASE_KEY else 'None'}")

# ───────────────────────────────────────────
# SUPABASE CLIENT (singleton)
# ───────────────────────────────────────────
supabase: Optional[Client] = None

def get_supabase() -> Client:
    global supabase
    if supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no configurados")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[SUPABASE] Cliente inicializado")
    return supabase

# ───────────────────────────────────────────
# MODELOS Pydantic
# ───────────────────────────────────────────
class RestauranteCreate(BaseModel):
    nombre: str
    telefono: str
    zone: str = "centro"
    business_type: str = "restaurant"
    plan: str = "trial"

class PlatoCreate(BaseModel):
    nombre: str
    descripcion: str
    precio: int
    id_menu: Optional[str] = None

# ───────────────────────────────────────────
# SERVICIOS
# ───────────────────────────────────────────

class WhatsAppService:
    """Envia mensajes via WhatsApp Cloud API"""

    BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"

    @classmethod
    async def send_text_message(cls, to_number: str, message: str) -> Dict:
        if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
            logger.error("[WA] Faltan WHATSAPP_TOKEN o PHONE_NUMBER_ID")
            return {"error": "Configuracion incompleta"}

        url = f"{cls.BASE_URL}/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                data = response.json()
                if response.status_code == 200:
                    logger.info(f"[WA] Mensaje enviado a {to_number}")
                else:
                    logger.error(f"[WA] Error {response.status_code}: {data}")
                return data
            except Exception as e:
                logger.error(f"[WA] Excepcion enviando mensaje: {e}")
                return {"error": str(e)}


class BotLogic:
    """Logica de negocio del bot adaptada al schema real"""

    WELCOME_MESSAGE = """👋 Bienvenido a Orquestrator ISA!

Soy tu asistente de pedidos por WhatsApp. Aqui puedes:

🍽️ Ver menu — Escribe "menu"
📋 Mi pedido — Escribe "pedido"
📍 Restaurantes — Escribe "lista"
❓ Ayuda — Escribe "ayuda"

Como te puedo ayudar hoy?"""

    HELP_MESSAGE = """*Comandos disponibles:*

• menu — Ver menu del restaurante
• pedido — Ver tu pedido actual
• lista — Ver restaurantes disponibles
• ayuda — Este mensaje
• hola — Saludo inicial

Para hacer un pedido, escribe el numero del plato."""

    @classmethod
    async def get_or_create_cliente(cls, phone: str, nombre: str = "Cliente WhatsApp") -> Dict:
        """Obtiene o crea un cliente por numero de telefono"""
        try:
            sb = get_supabase()
            result = sb.table("clientes").select("*").eq("telefono", phone).execute()
            if result.data:
                return result.data[0]

            nuevo = sb.table("clientes").insert({
                "telefono": phone,
                "nombre": nombre,
                "language": "darija",
                "is_active": True,
                "fecha_registro": datetime.utcnow().isoformat(),
            }).execute()
            return nuevo.data[0]
        except Exception as e:
            logger.error(f"[DB] Error con cliente: {e}")
            return {"id_cliente": None, "telefono": phone}

    @classmethod
    async def get_or_create_conversacion(cls, id_cliente: str, phone: str) -> Dict:
        """Obtiene conversacion activa o crea nueva"""
        try:
            sb = get_supabase()
            result = sb.table("conversaciones").select("*").eq("id_cliente", id_cliente).eq("estado", "activa").order("fecha_inicio", desc=True).limit(1).execute()
            if result.data:
                return result.data[0]

            nueva = sb.table("conversaciones").insert({
                "id_cliente": id_cliente,
                "whatsapp_number": phone,
                "estado": "activa",
                "fecha_inicio": datetime.utcnow().isoformat(),
                "total_messages": 0,
            }).execute()
            return nueva.data[0]
        except Exception as e:
            logger.error(f"[DB] Error con conversacion: {e}")
            return {"id_conversacion": None}

    @classmethod
    async def guardar_mensaje(cls, id_conversacion: str, id_cliente: str, contenido: str, direction: str = "incoming", tipo: str = "text") -> bool:
        """Guarda mensaje en tabla mensajes"""
        try:
            sb = get_supabase()
            sb.table("mensajes").insert({
                "id_conversacion": id_conversacion,
                "id_cliente": id_cliente,
                "contenido": contenido,
                "direction": direction,
                "tipo": tipo,
                "fecha_hora": datetime.utcnow().isoformat(),
                "estado": "enviado" if direction == "outgoing" else "recibido",
            }).execute()
            return True
        except Exception as e:
            logger.warning(f"[DB] No se pudo guardar mensaje: {e}")
            return False

    @classmethod
    async def process_message(cls, from_number: str, message_text: str) -> str:
        text = message_text.strip().lower()

        # Obtener o crear cliente
        cliente = await cls.get_or_create_cliente(from_number)
        id_cliente = cliente.get("id_cliente")

        # Obtener o crear conversacion
        conversacion = await cls.get_or_create_conversacion(id_cliente, from_number)
        id_conversacion = conversacion.get("id_conversacion")

        # Guardar mensaje entrante
        await cls.guardar_mensaje(id_conversacion, id_cliente, message_text, "incoming", "text")

        # Procesar comando
        if text in ["hola", "hi", "hello", "salam", "marhaba"]:
            return cls.WELCOME_MESSAGE

        if text in ["ayuda", "help", "?", "musaaada"]:
            return cls.HELP_MESSAGE

        if text in ["menu", "menú", "liste"]:
            return await cls.get_menu_for_user()

        if text in ["lista", "list", "restaurantes", "matalim"]:
            return await cls.get_restaurants_list()

        if text in ["pedido", "order", "commande", "talab"]:
            return await cls.get_current_order(id_cliente)

        if text.isdigit():
            return await cls.add_to_order(id_cliente, int(text))

        return """Ma fhamteksh 😅

Kteb *ayuda* bach tchouf chno yemken lik.
Wlla *menu* bach tchouf lmaakoul."""

    @classmethod
    async def get_restaurants_list(cls) -> str:
        try:
            sb = get_supabase()
            result = sb.table("restaurantes").select("nombre, business_type, zone, google_rating, google_reviews").eq("is_active", True).order("google_reviews", desc=True).limit(10).execute()

            if not result.data:
                return "🏪 Ma kaynsh matalim moujoudin\n\nMa kaynsh restaurante actif."

            lines = ["🏪 *Restaurantes disponibles en Tetouan:*\n"]
            for r in result.data:
                emoji = {"restaurant": "🍽️", "cafe": "☕", "fast_food": "🍔", "seafood": "🦐"}.get(r["business_type"], "🍽️")
                lines.append(f"{emoji} *{r['nombre']}*")
                lines.append(f"   📍 {r['zone']} | ⭐ {r['google_rating']} ({r['google_reviews']} reviews)\n")

            lines.append("Kteb *menu* bach tchouf lmenu.")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error obteniendo restaurantes: {e}")
            return "❌ Error. Jareb merra okhra."

    @classmethod
    async def get_menu_for_user(cls) -> str:
        try:
            sb = get_supabase()
            rest_result = sb.table("restaurantes").select("id_restaurante, nombre").eq("is_active", True).order("google_reviews", desc=True).limit(1).execute()

            if not rest_result.data:
                return "📋 Ma kaynsh menu\n\nMa kaynsh menu moujoud."

            restaurante = rest_result.data[0]
            menu_result = sb.table("menus").select("id_menu").eq("id_restaurante", restaurante["id_restaurante"]).eq("is_active", True).execute()

            if not menu_result.data:
                return f"📋 *Menu de {restaurante['nombre']}:*\n\nMenu ma kaynsh."

            menu_id = menu_result.data[0]["id_menu"]
            platos_result = sb.table("platos").select("*").eq("id_menu", menu_id).eq("is_available", True).execute()

            if not platos_result.data:
                return f"📋 *Menu de {restaurante['nombre']}:*\n\nMa kaynsh platos."

            lines = [f"📋 *Menu de {restaurante['nombre']}:*\n"]
            for i, plato in enumerate(platos_result.data, 1):
                star = "⭐ " if plato.get("is_star") else ""
                lines.append(f"{i}. {star}{plato['nombre']} — {plato['precio']} MAD")
                lines.append(f"   _{plato['descripcion']}_\n")

            lines.append("✍️ Kteb numero dial lplate bach tcommandi.")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error obteniendo menu: {e}")
            return "❌ Error f menu. Jareb merra okhra."

    @classmethod
    async def get_current_order(cls, id_cliente: str) -> str:
        try:
            sb = get_supabase()
            result = sb.table("pedidos").select("*").eq("id_cliente", id_cliente).eq("estado", "pendiente").order("fecha_creacion", desc=True).limit(1).execute()

            if not result.data:
                return """🛒 *Talab dialk:*

Ma kaynsh talab actif.

Kteb *menu* bach tbeddi."""

            pedido = result.data[0]
            items = json.loads(pedido.get("items_json", "[]"))
            total = pedido.get("total_mad", 0)

            lines = ["🛒 *Talab dialk:*\n"]
            for item in items:
                lines.append(f"• {item.get('nombre', 'Plate')} x{item.get('qty', 1)} — {item.get('precio', 0)} MAD")
            lines.append(f"\n*Total: {total} MAD*")
            lines.append(f"_Hal: {pedido['estado']}_")

            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error obteniendo pedido: {e}")
            return "🛒 Ma kaynsh talab."

    @classmethod
    async def add_to_order(cls, id_cliente: str, item_number: int) -> str:
        try:
            sb = get_supabase()
            rest_result = sb.table("restaurantes").select("id_restaurante").eq("is_active", True).order("google_reviews", desc=True).limit(1).execute()
            if not rest_result.data:
                return "❌ Ma kaynsh restaurante."

            menu_result = sb.table("menus").select("id_menu").eq("id_restaurante", rest_result.data[0]["id_restaurante"]).eq("is_active", True).execute()
            if not menu_result.data:
                return "❌ Ma kaynsh menu."

            platos = sb.table("platos").select("*").eq("id_menu", menu_result.data[0]["id_menu"]).eq("is_available", True).execute()
            if not platos.data or item_number < 1 or item_number > len(platos.data):
                return "❌ Raqam ma mchichech. Jareb raqam akhor."

            plato = platos.data[item_number - 1]

            pedido_existente = sb.table("pedidos").select("*").eq("id_cliente", id_cliente).eq("estado", "pendiente").execute()

            if pedido_existente.data:
                pedido = pedido_existente.data[0]
                items = json.loads(pedido.get("items_json", "[]"))
                items.append({
                    "id_plato": plato["id_plato"],
                    "nombre": plato["nombre"],
                    "precio": plato["precio"],
                    "qty": 1
                })
                total = sum(item["precio"] * item["qty"] for item in items)

                sb.table("pedidos").update({
                    "items_json": json.dumps(items),
                    "total_mad": total,
                }).eq("id_pedido", pedido["id_pedido"]).execute()
            else:
                sb.table("pedidos").insert({
                    "id_cliente": id_cliente,
                    "items_json": json.dumps([{
                        "id_plato": plato["id_plato"],
                        "nombre": plato["nombre"],
                        "precio": plato["precio"],
                        "qty": 1
                    }]),
                    "total_mad": plato["precio"],
                    "estado": "pendiente",
                    "fecha_creacion": datetime.utcnow().isoformat(),
                }).execute()

            return f"✅ *{plato['nombre']}* zed f talab dialk.\n\nKteb *pedido* bach tchouf talab kamil."
        except Exception as e:
            logger.error(f"[DB] Error agregando a pedido: {e}")
            return "❌ Error. Jareb merra okhra."


# ───────────────────────────────────────────
# LIFESPAN (startup / shutdown)
# ───────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[STARTUP] Orquestrator ISA iniciando...")
    try:
        sb = get_supabase()
        result = sb.table("restaurantes").select("count", count="exact").limit(1).execute()
        logger.info(f"[STARTUP] Supabase conectado. Tabla restaurantes accesible.")
    except Exception as e:
        logger.error(f"[STARTUP] Error conectando Supabase: {e}")

    yield

    logger.info("[SHUTDOWN] Orquestrator ISA deteniendo...")

# ───────────────────────────────────────────
# FASTAPI APP
# ───────────────────────────────────────────
app = FastAPI(
    title="Orquestrator ISA — ChatCommerce Bot",
    description="WhatsApp Business API + Supabase backend para pedidos por chat",
    version="2.3.0",
    lifespan=lifespan,
)

# ───────────────────────────────────────────
# HEALTH CHECK
# ───────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Orquestrator ISA",
        "version": "2.3.0",
        "timestamp": datetime.utcnow().isoformat(),
        "webhook_url": "https://chatcommerce-bot.onrender.com/api/whatsapp/webhook",
    }

@app.get("/health")
async def health():
    health_status = {
        "status": "healthy",
        "supabase_connected": False,
        "whatsapp_token_present": bool(WHATSAPP_TOKEN),
        "phone_number_id_present": bool(PHONE_NUMBER_ID),
        "verify_token": VERIFY_TOKEN,
        "verify_token_len": len(VERIFY_TOKEN),
        "verify_token_bytes": str(VERIFY_TOKEN.encode()),
    }
    try:
        sb = get_supabase()
        sb.table("restaurantes").select("count", count="exact").limit(1).execute()
        health_status["supabase_connected"] = True
    except:
        pass
    return health_status

# ───────────────────────────────────────────
# WEBHOOK WHATSAPP (GET — verificacion)
# ───────────────────────────────────────────
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    """
    Meta envia GET con query params:
    ?hub.mode=subscribe&hub.verify_token=XXX&hub.challenge=YYY
    FastAPI no convierte hub.mode -> hub_mode automaticamente.
    Usamos request.query_params para capturar manualmente.
    """
    params = dict(request.query_params)

    hub_mode = params.get("hub.mode")
    hub_verify_token = params.get("hub.verify_token")
    hub_challenge = params.get("hub.challenge")

    # LOGGING ULTRA-DETALLADO
    logger.info(f"[WEBHOOK GET] === INICIO VERIFICACION ===")
    logger.info(f"[WEBHOOK GET] Query params recibidos: {params}")
    logger.info(f"[WEBHOOK GET] hub.mode='{hub_mode}' (tipo: {type(hub_mode)})")
    logger.info(f"[WEBHOOK GET] hub.verify_token='{hub_verify_token}' (tipo: {type(hub_verify_token)})")
    logger.info(f"[WEBHOOK GET] hub.challenge='{hub_challenge}' (tipo: {type(hub_challenge)})")
    logger.info(f"[WEBHOOK GET] VERIFY_TOKEN='{VERIFY_TOKEN}' (tipo: {type(VERIFY_TOKEN)})")
    logger.info(f"[WEBHOOK GET] Comparacion: hub.mode == 'subscribe' -> {hub_mode == 'subscribe'}")
    logger.info(f"[WEBHOOK GET] Comparacion: hub.verify_token == VERIFY_TOKEN -> {hub_verify_token == VERIFY_TOKEN}")
    logger.info(f"[WEBHOOK GET] Comparacion completa -> {hub_mode == 'subscribe' and hub_verify_token == VERIFY_TOKEN}")

    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("[WEBHOOK GET] Verificacion exitosa")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(f"[WEBHOOK GET] Verificacion fallida")
    raise HTTPException(status_code=403, detail="Verification failed")

# ───────────────────────────────────────────
# WEBHOOK WHATSAPP (POST — mensajes)
# ───────────────────────────────────────────
@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        logger.info(f"[WEBHOOK POST] Payload recibido: {json.dumps(body, ensure_ascii=False)[:500]}")
    except Exception as e:
        logger.error(f"[WEBHOOK POST] Error parseando JSON: {e}")
        return JSONResponse(content={"status": "error"}, status_code=400)

    background_tasks.add_task(process_webhook_payload, body)
    return JSONResponse(content={"status": "ok"}, status_code=200)

async def process_webhook_payload(body: Dict[str, Any]):
    try:
        if body.get("object") != "whatsapp_business_account":
            logger.warning(f"[WEBHOOK] Objeto no reconocido")
            return

        entries = body.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])

                for msg in messages:
                    from_number = msg.get("from")
                    msg_type = msg.get("type")
                    msg_id = msg.get("id")

                    logger.info(f"[MSG] De: {from_number} | Tipo: {msg_type} | ID: {msg_id}")

                    if msg_type == "text":
                        text_body = msg.get("text", {}).get("body", "")
                        response_text = await BotLogic.process_message(from_number, text_body)
                        await WhatsAppService.send_text_message(from_number, response_text)

                        # Guardar respuesta saliente
                        cliente = await BotLogic.get_or_create_cliente(from_number)
                        conversacion = await BotLogic.get_or_create_conversacion(cliente.get("id_cliente"), from_number)
                        await BotLogic.guardar_mensaje(
                            conversacion.get("id_conversacion"),
                            cliente.get("id_cliente"),
                            response_text,
                            "outgoing",
                            "text"
                        )
                    else:
                        await WhatsAppService.send_text_message(
                            from_number,
                            "📎 Recibit message multimedia. Daba ghir nprocessi text.\n\nKteb *ayuda* bach tchouf chno yemken lik."
                        )
    except Exception as e:
        logger.error(f"[WEBHOOK] Error procesando payload: {e}", exc_info=True)

# ───────────────────────────────────────────
# API ADMIN — Restaurantes
# ───────────────────────────────────────────
@app.get("/api/restaurantes")
async def list_restaurantes():
    try:
        sb = get_supabase()
        result = sb.table("restaurantes").select("*").eq("is_active", True).order("google_reviews", desc=True).execute()
        return {"restaurantes": result.data, "count": len(result.data)}
    except Exception as e:
        logger.error(f"[API] Error listando restaurantes: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/restaurantes")
async def create_restaurante(rest: RestauranteCreate):
    try:
        sb = get_supabase()
        data = {
            "nombre": rest.nombre,
            "telefono": rest.telefono,
            "zone": rest.zone,
            "business_type": rest.business_type,
            "plan": rest.plan,
            "is_active": True,
            "whatsapp_status": "contactar",
            "trial_ends_at": (datetime.utcnow() + timedelta(days=20)).isoformat(),
            "fecha_registro": datetime.utcnow().isoformat(),
        }
        result = sb.table("restaurantes").insert(data).execute()
        return {"restaurante": result.data[0]}
    except Exception as e:
        logger.error(f"[API] Error creando restaurante: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ───────────────────────────────────────────
# API ADMIN — Platos
# ───────────────────────────────────────────
@app.get("/api/platos/{menu_id}")
async def get_platos(menu_id: str):
    try:
        sb = get_supabase()
        result = sb.table("platos").select("*").eq("id_menu", menu_id).eq("is_available", True).execute()
        return {"platos": result.data, "count": len(result.data)}
    except Exception as e:
        logger.error(f"[API] Error obteniendo platos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/platos")
async def create_plato(plato: PlatoCreate):
    try:
        sb = get_supabase()
        data = {
            "nombre": plato.nombre,
            "descripcion": plato.descripcion,
            "precio": plato.precio,
            "id_menu": plato.id_menu,
            "is_available": True,
            "created_at": datetime.utcnow().isoformat(),
        }
        result = sb.table("platos").insert(data).execute()
        return {"plato": result.data[0]}
    except Exception as e:
        logger.error(f"[API] Error creando plato: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ───────────────────────────────────────────
# API — Estadisticas
# ───────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    try:
        sb = get_supabase()
        restaurantes = sb.table("restaurantes").select("count", count="exact").execute()
        clientes = sb.table("clientes").select("count", count="exact").execute()
        mensajes = sb.table("mensajes").select("count", count="exact").execute()
        pedidos = sb.table("pedidos").select("count", count="exact").execute()

        return {
            "restaurantes": restaurantes.count,
            "clientes": clientes.count,
            "mensajes": mensajes.count,
            "pedidos": pedidos.count,
            "whatsapp_configured": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        }
    except Exception as e:
        logger.error(f"[API] Error obteniendo stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ───────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
