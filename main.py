import os
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx

# ─── CARGAR .env PRIMERO ───
load_dotenv()

# ─── LOGGING ───
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG ───
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "1097255916805484")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

logger.info(f"🔧 WHATSAPP_TOKEN presente: {bool(WHATSAPP_TOKEN)}")
logger.info(f"🔧 PHONE_NUMBER_ID: {PHONE_NUMBER_ID}")
logger.info(f"🔧 SUPABASE_URL presente: {bool(SUPABASE_URL)}")

# ─── SUPABASE CLIENT ───
from supabase import create_client

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase conectado")
    except Exception as e:
        logger.error(f"❌ Error conectando Supabase: {e}")
else:
    logger.warning("⚠️ SUPABASE_URL o SUPABASE_KEY no configurados")

# ─── LIFESPAN ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Orquestrador ISA iniciado. Versión: 1.0.0")
    yield
    logger.info("🛑 Orquestrador ISA detenido.")

app = FastAPI(title="Orquestrator ISA", version="1.0.0", lifespan=lifespan)

# ─── HEALTH ───
@app.get("/health")
async def health_check():
    health = {
        "status": "ok",
        "service": "orquestrator-isa",
        "version": "1.0.0",
        "supabase_connected": supabase is not None,
        "whatsapp_token_present": bool(WHATSAPP_TOKEN),
        "phone_number_id": PHONE_NUMBER_ID,
    }
    return JSONResponse(health)

# ─── WEBHOOK GET (Verificación Meta) ───
@app.get("/api/whatsapp/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    logger.info(f"🔍 Webhook GET: mode={mode}, token={token}")
    
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        logger.info("✅ Webhook verificado")
        return Response(content=challenge, media_type="text/plain", status_code=200)
    
    logger.warning("❌ Verificación fallida")
    return Response(status_code=403)

# ─── ENVIAR MENSAJE WHATSAPP ───
async def send_whatsapp_message(to_number: str, message_text: str):
    """Envía mensaje de texto vía WhatsApp Cloud API"""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("❌ Falta WHATSAPP_TOKEN o PHONE_NUMBER_ID")
        return False
    
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
            data = response.json()
            
            if response.status_code == 200:
                logger.info(f"✅ Mensaje enviado a {to_number}")
                return True
            else:
                logger.error(f"❌ Error enviando mensaje: {data}")
                return False
    except Exception as e:
        logger.error(f"❌ Excepción enviando mensaje: {e}")
        return False

# ─── GUARDAR MENSAJE EN SUPABASE ───
def save_message(phone: str, name: str, message_type: str, content: str, direction: str = "incoming"):
    """Guarda mensaje en tabla 'mensajes'"""
    if not supabase:
        logger.warning("⚠️ Supabase no disponible, mensaje no guardado")
        return None
    
    try:
        data = {
            "telefono": phone,
            "nombre": name,
            "tipo": message_type,
            "contenido": content,
            "direccion": direction,
        }
        result = supabase.table("mensajes").insert(data).execute()
        logger.info(f"💾 Mensaje guardado: {phone}")
        return result
    except Exception as e:
        logger.error(f"❌ Error guardando mensaje: {e}")
        return None

# ─── PROCESAR MENSAJE ───
async def process_message(from_number: str, name: str, message_body: str):
    """Procesa el mensaje y responde"""
    
    text = message_body.strip().lower()
    logger.info(f"📩 Procesando: '{text}' de {from_number}")
    
    # Guardar mensaje entrante
    save_message(from_number, name, "text", message_body, "incoming")
    
    # ─── LÓGICA DE RESPUESTA ───
    if text in ["hola", "hi", "hello", "salam", "مرحبا", "سلام"]:
        response = (
            "👋 *¡Hola! Bienvenido a Orquestrator ISA*\n\n"
            "Soy tu asistente de pedidos por WhatsApp. ¿Qué deseas hacer?\n\n"
            "1️⃣ *Ver menú* — Escribe: *menu*\n"
            "2️⃣ *Hacer pedido* — Escribe: *pedir*\n"
            "3️⃣ *Ver mis pedidos* — Escribe: *mis pedidos*\n"
            "4️⃣ *Ayuda* — Escribe: *ayuda*\n\n"
            "¿Cómo puedo ayudarte hoy? 😊"
        )
    elif text in ["menu", "menú", "1"]:
        response = (
            "🍽️ *MENÚ DEL DÍA*\n\n"
            "🥘 *Platos principales:*\n"
            "• Tagine de pollo — 65 DH\n"
            "• Couscous royal — 80 DH\n"
            "• Pastilla — 90 DH\n\n"
            "🥤 *Bebidas:*\n"
            "• Té marroquí — 10 DH\n"
            "• Jugo natural — 15 DH\n\n"
            "Para pedir, escribe: *pedir [nombre del plato]*\n"
            "Ejemplo: *pedir tagine de pollo*"
        )
    elif text in ["pedir", "pedido", "2"]:
        response = (
            "🛒 *HACER PEDIDO*\n\n"
            "Escribe tu pedido así:\n"
            "*pedir [plato] [cantidad]*\n\n"
            "Ejemplos:\n"
            "• pedir tagine de pollo 2\n"
            "• pedir couscous 1\n\n"
            "Te confirmaré el total y te pediré la dirección de entrega."
        )
    elif text.startswith("pedir "):
        order_text = message_body[6:].strip()
        response = (
            f"✅ *Pedido recibido:* {order_text}\n\n"
            f"📍 Por favor, envía tu dirección de entrega.\n"
            f"O escribe *cancelar* para anular."
        )
    elif text in ["ayuda", "help", "4"]:
        response = (
            "❓ *AYUDA*\n\n"
            "• *menu* — Ver menú\n"
            "• *pedir [plato]* — Hacer pedido\n"
            "• *mis pedidos* — Ver historial\n"
            "• *cancelar* — Cancelar pedido actual\n\n"
            "¿Necesitas hablar con un humano? Escribe *agente*"
        )
    else:
        response = (
            "🤔 No entendí bien. ¿Puedes repetir?\n\n"
            "Escribe *menu* para ver opciones o *ayuda* para asistencia."
        )
    
    # Enviar respuesta
    await send_whatsapp_message(from_number, response)
    
    # Guardar respuesta saliente
    save_message(from_number, "Bot ISA", "text", response, "outgoing")

# ─── WEBHOOK POST (Recibir mensajes) ───
@app.post("/api/whatsapp/webhook")
async def receive_message(request: Request):
    try:
        data = await request.json()
        logger.info(f"📥 Payload recibido: {json.dumps(data, indent=2)[:500]}")
        
        if "entry" in data:
            for entry in data["entry"]:
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    
                    if "messages" in value:
                        for message in value["messages"]:
                            from_number = message.get("from", "")
                            message_type = message.get("type", "")
                            
                            contacts = value.get("contacts", [])
                            name = contacts[0].get("profile", {}).get("name", "Cliente") if contacts else "Cliente"
                            
                            if message_type == "text":
                                body = message.get("text", {}).get("body", "")
                                await process_message(from_number, name, body)
                            else:
                                logger.info(f"📎 Mensaje tipo '{message_type}' recibido de {from_number}")
                                await send_whatsapp_message(
                                    from_number,
                                    "📎 Recibí tu archivo. Por ahora solo proceso mensajes de texto.\nEscribe *menu* para ver opciones."
                                )
        
        return Response(status_code=200)
        
    except Exception as e:
        logger.error(f"❌ Error en webhook POST: {e}")
        return Response(status_code=200)

# ─── MAIN ───
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
