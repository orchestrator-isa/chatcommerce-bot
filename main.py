#!/usr/bin/env python3
"""Orquestrator ISA — ChatCommerce Bot v3.0"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import httpx

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("isa-bot")

# ──────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT VARIABLES
# ──────────────────────────────────────────────────────────────────────────────
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
WEBHOOK_PREFIX = os.getenv("WEBHOOK_PREFIX", "/api/whatsapp/webhook")

# ──────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT
# ──────────────────────────────────────────────────────────────────────────────
_supabase: Optional[Client] = None

def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no configurados")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[SUPABASE] Conectado")
    return _supabase

# ──────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] Bot iniciando...")
    yield
    logger.info("[STOP] Bot detenido.")

app = FastAPI(title="Orquestrator ISA", version="3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINTS BÁSICOS
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "version": "3.0"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get(WEBHOOK_PREFIX)
async def webhook_verify(req: Request):
    p = req.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403, "Verification failed")

@app.post(WEBHOOK_PREFIX)
async def webhook_post(req: Request, bg: BackgroundTasks):
    try:
        bg.add_task(lambda: None, await req.json())
        return JSONResponse({"status": "ok"}, 200)
    except:
        return JSONResponse({"status": "error"}, 400)

# ──────────────────────────────────────────────────────────────────────────────
# API RESTAURANTES
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/restaurantes")
async def list_restaurantes():
    try:
        sb = get_supabase()
        res = sb.table("clients").select("*").eq("is_active", True).execute()
        restaurantes = [{"id_restaurante": r.get("id"), "nombre": r.get("name"), "telefono": r.get("owner_phone")} for r in res.data]
        return {"restaurantes": restaurantes, "count": len(restaurantes)}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.get("/api/clients")
async def get_clients():
    return await list_restaurantes()

# ──────────────────────────────────────────────────────────────────────────────
# API PLATOS (CORREGIDO - ESTE ES EL IMPORTANTE)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    """Devuelve todos los platos de un restaurante"""
    try:
        sb = get_supabase()
        logger.info(f"🔍 Buscando platos para client_id: {client_id}")
        
        response = sb.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        
        logger.info(f"✅ Encontrados {len(response.data)} platos")
        
        platos = []
        for item in response.data:
            platos.append({
                "id_plato": item.get("id"),
                "nombre": item.get("dish_name"),
                "precio": item.get("price"),
                "descripcion": item.get("description", ""),
                "is_available": item.get("is_available")
            })
        
        return {"platos": platos, "count": len(platos)}
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

@app.post("/api/platos")
async def create_plato(item: dict):
    """Crea un nuevo plato"""
    try:
        sb = get_supabase()
        data = {
            "client_id": item.get("client_id"),
            "dish_name": item.get("nombre"),
            "price": item.get("precio", 0),
            "description": item.get("descripcion", ""),
            "is_available": True
        }
        logger.info(f"📝 Creando plato: {data}")
        response = sb.table("menu_items").insert(data).execute()
        if response.data:
            return {"plato": {"id_plato": response.data[0].get("id"), "nombre": response.data[0].get("dish_name"), "precio": response.data[0].get("price")}}
        return {"plato": None}
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# ALIAS EN INGLÉS (EL QUE TE FALTA)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    """Alias en inglés para /api/platos/{client_id}"""
    return await get_platos(client_id)

@app.post("/api/menu")
async def create_menu_item(item: dict):
    """Alias en inglés para crear plato"""
    plato_data = {
        "client_id": item.get("client_id"),
        "nombre": item.get("dish_name") or item.get("nombre"),
        "precio": item.get("price") or item.get("precio"),
        "descripcion": item.get("description", "")
    }
    return await create_plato(plato_data)

@app.get("/api/stats")
async def get_stats():
    try:
        sb = get_supabase()
        restaurantes = sb.table("clients").select("count", count="exact").execute()
        platos = sb.table("menu_items").select("count", count="exact").execute()
        return {"restaurantes": restaurantes.count, "platos_totales": platos.count}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
