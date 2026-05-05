#!/usr/bin/env python3
import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("isa-bot")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

# ========== WEBHOOK ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info(f"Webhook verificado: {challenge}")
        return Response(content=challenge, media_type="text/plain")
    
    logger.warning(f"Verificación fallida: mode={mode}, token={token}")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        logger.info(f"Mensaje recibido: {body}")
        background_tasks.add_task(process_message, body)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": "error"}

async def process_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    if msg.get("type") == "text":
                        from_number = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        logger.info(f"Mensaje de {from_number}: {text}")
                        
                        # Responder
                        await send_message(from_number, f"Recibí: {text}")
    except Exception as e:
        logger.error(f"Error procesando: {e}")

async def send_message(to: str, message: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp no configurado")
        return
    
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        if response.status_code != 200:
            logger.error(f"Error: {response.text}")

# ========== API RESTAURANTES ==========
@app.get("/api/restaurantes")
async def get_restaurantes():
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    result = supabase.table("clients").select("*").eq("is_active", True).execute()
    return {"restaurantes": result.data, "count": len(result.data)}

@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
    platos = [{"id_plato": r["id"], "nombre": r["dish_name"], "precio": r["price"]} for r in result.data]
    return {"platos": platos, "count": len(platos)}

@app.post("/api/platos")
async def create_plato(item: dict):
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    data = {
        "client_id": item["client_id"],
        "dish_name": item["nombre"],
        "price": item["precio"],
        "description": item.get("descripcion", ""),
        "is_available": True
    }
    result = supabase.table("menu_items").insert(data).execute()
    if result.data:
        return {"plato": {"id_plato": result.data[0]["id"], "nombre": result.data[0]["dish_name"], "precio": result.data[0]["price"]}}
    return {"plato": None}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return await get_platos(client_id)

@app.post("/api/menu")
async def create_menu_item(item: dict):
    plato_data = {
        "client_id": item.get("client_id"),
        "nombre": item.get("dish_name"),
        "precio": item.get("price"),
        "descripcion": item.get("description", "")
    }
    return await create_plato(plato_data)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
