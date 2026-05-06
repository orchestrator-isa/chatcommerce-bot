#!/usr/bin/env python3
import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from typing import Dict, List
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("isa-bot")

# ========== CONFIGURACIÓN ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ========== CARITOS DE COMPRA (PERSISTENTES POR USUARIO) ==========
carts: Dict[str, List[dict]] = {}

# ========== WHATSAPP WEBHOOK ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403, "Verification failed")

@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        background_tasks.add_task(process_message, body)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": "error"}

# ========== FUNCIONES DEL MENÚ ==========
async def get_restaurant_menu(client_id: str) -> tuple:
    """Devuelve (menu_text, platos_lista)"""
    try:
        if not supabase:
            return "❌ Error de conexión", []
        result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        if not result.data:
            return "📋 *MENÚ*\nNo hay platos disponibles.", []
        menu_lines = ["📋 *MENÚ*", ""]
        for i, item in enumerate(result.data, 1):
            menu_lines.append(f"{i}. 🍽️ *{item['dish_name']}* — {item['price']} MAD")
            if item.get('description'):
                menu_lines.append(f"   📝 {item['description']}")
            menu_lines.append("")
        return "\n".join(menu_lines), result.data
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error al cargar el menú", []

# ========== PROCESAMIENTO DE MENSAJES ==========
async def process_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # ID del restaurante (El Reducto por defecto)
                client_id = "ba4351a0-763f-402d-acf9-30594ce40d87"
                
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        user_id = msg.get("from")
                        text = msg.get("text", {}).get("body", "").lower().strip()
                        
                        logger.info(f"📨 {user_id}: {text}")
                        
                        # Comandos en español
                        if text == "hola":
                            response = "👋 ¡Hola! Bienvenido a El Reducto.\n\nEscribe *MENU* para ver nuestros platos."
                        
                        elif text == "menu":
                            menu_text, platos = await get_restaurant_menu(client_id)
                            # Guardar platos en memoria para el usuario
                            carts[f"{user_id}_menu"] = platos
                            response = menu_text
                        
                        elif text == "pedido":
                            if user_id in carts and isinstance(carts[user_id], list):
                                items = carts[user_id]
                                if items:
                                    total = sum(item["price"] for item in items)
                                    resumen = "\n".join([f"{i+1}. {item['dish_name']} — {item['price']} MAD" for i, item in enumerate(items)])
                                    response = f"🛒 *TU PEDIDO*\n\n{resumen}\n\n💰 *TOTAL: {total} MAD*\n\nEscribe *CONFIRMAR* para finalizar."
                                else:
                                    response = "🛒 *Carrito vacío*\n\nEscribe *MENU* para ver los platos."
                            else:
                                response = "🛒 *Carrito vacío*\n\nEscribe *MENU* para ver los platos."
                        
                        elif text == "confirmar":
                            if user_id in carts and isinstance(carts[user_id], list) and carts[user_id]:
                                items = carts[user_id]
                                total = sum(item["price"] for item in items)
                                carts[user_id] = []  # Limpiar carrito
                                response = f"✅ *¡PEDIDO CONFIRMADO!*\n\n💰 Total: {total} MAD\n\n📋 Tu pedido ha sido enviado a la cocina.\n\n⏱️ Tiempo estimado: 20-30 minutos.\n\n¡Gracias por tu compra!"
                            else:
                                response = "❌ No tienes un pedido pendiente.\n\nEscribe *MENU* para ver los platos."
                        
                        elif text.isdigit():
                            # Es un número - selección de plato
                            num = int(text)
                            menu_key = f"{user_id}_menu"
                            if menu_key in carts and carts[menu_key]:
                                platos = carts[menu_key]
                                if 1 <= num <= len(platos):
                                    selected = platos[num - 1]
                                    if user_id not in carts or not isinstance(carts[user_id], list):
                                        carts[user_id] = []
                                    carts[user_id].append({
                                        "dish_name": selected["dish_name"],
                                        "price": selected["price"]
                                    })
                                    total = sum(item["price"] for item in carts[user_id])
                                    response = f"✅ *{selected['dish_name']}* añadido al carrito.\n💰 Total parcial: {total} MAD\n\nEscribe *PEDIDO* para ver tu carrito o *MENU* para seguir agregando."
                                else:
                                    response = f"❌ Número inválido. Escribe un número del 1 al {len(platos)}."
                            else:
                                response = "❌ Primero escribe *MENU* para ver los platos disponibles."
                        
                        elif text == "ayuda" or text == "help":
                            response = "📋 *COMANDOS DISPONIBLES*\n\n• *HOLA* - Saludo\n• *MENU* - Ver platos\n• *NÚMERO* - Añadir plato\n• *PEDIDO* - Ver carrito\n• *CONFIRMAR* - Finalizar pedido"
                        
                        else:
                            response = "❓ No entendí. Escribe *MENU* para ver los platos o *AYUDA* para ver comandos."
                        
                        await send_message(user_id, response)
                        
    except Exception as e:
        logger.error(f"Error procesando: {e}")

async def send_message(to: str, message: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp no configurado")
        return
    
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message[:1600]}}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        if response.status_code != 200:
            logger.error(f"Error WhatsApp: {response.text}")

# ========== API ENDPOINTS ==========
@app.get("/")
async def root():
    return {"status": "ok", "service": "Orquestrator ISA", "version": "3.2"}

@app.get("/health")
async def health():
    supabase_status = False
    try:
        supabase.table("restaurantes").select("count", count="exact").limit(1).execute()
        supabase_status = True
    except:
        pass
    return {"status": "healthy", "supabase": supabase_status, "whatsapp": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID), "carts_active": len([k for k in carts if not k.endswith("_menu")])}

@app.get("/api/restaurantes")
async def get_restaurantes():
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    result = supabase.table("restaurantes").select("*").eq("is_active", True).execute()
    return {"restaurantes": result.data, "count": len(result.data)}

@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
    platos = [{"id_plato": r["id"], "nombre": r["dish_name"], "precio": r["price"]} for r in result.data]
    return {"platos": platos, "count": len(platos)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
