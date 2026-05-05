#!/usr/bin/env python3
"""Orquestrator ISA — ChatCommerce Bot v3.0 COMPLETO"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
# API PLATOS - GET (CORREGIDO)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    """Devuelve todos los platos de un restaurante"""
    try:
        sb = get_supabase()
        logger.info(f"🔍 Buscando platos para client_id: {client_id}")
        
        # Buscar directamente en menu_items
        response = sb.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        
        logger.info(f"✅ Encontrados {len(response.data)} platos en Supabase")
        
        # Formatear respuesta
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

# ──────────────────────────────────────────────────────────────────────────────
# API PLATOS - POST
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/api/platos")
async def create_plato(item: dict):
    """Crea un nuevo plato"""
    try:
        sb = get_supabase()
        
        # Validar client_id
        client_id = item.get("client_id")
        if not client_id:
            raise HTTPException(400, detail="client_id es requerido")
        
        # Datos a insertar (mapeo español -> inglés)
        data = {
            "client_id": client_id,
            "dish_name": item.get("nombre"),
            "price": item.get("precio", 0),
            "description": item.get("descripcion", ""),
            "is_available": True
        }
        
        logger.info(f"📝 Creando plato: {data}")
        response = sb.table("menu_items").insert(data).execute()
        
        if response.data:
            nuevo = response.data[0]
            logger.info(f"✅ Plato creado con ID: {nuevo.get('id')}")
            return {"plato": {
                "id_plato": nuevo.get("id"),
                "nombre": nuevo.get("dish_name"),
                "precio": nuevo.get("price")
            }}
        return {"plato": None}
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# ALIAS EN INGLÉS - ESTE ES EL QUE FALTABA
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

# ──────────────────────────────────────────────────────────────────────────────
# ESTADÍSTICAS
# ──────────────────────────────────────────────────────────────────────────────
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
