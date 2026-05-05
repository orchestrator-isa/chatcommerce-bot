#!/usr/bin/env python3
import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("isa-bot")

# Configuración
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

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
    
    # Validar campos requeridos
    if "client_id" not in item:
        raise HTTPException(400, "client_id es requerido")
    if "nombre" not in item:
        raise HTTPException(400, "nombre es requerido")
    if "precio" not in item:
        raise HTTPException(400, "precio es requerido")
    
    data = {
        "client_id": item["client_id"],
        "dish_name": item["nombre"],
        "price": item["precio"],
        "description": item.get("descripcion", ""),  # descripcion es opcional
        "is_available": True
    }
    result = supabase.table("menu_items").insert(data).execute()
    if result.data:
        nuevo = result.data[0]
        return {"plato": {"id_plato": nuevo["id"], "nombre": nuevo["dish_name"], "precio": nuevo["price"]}}
    return {"plato": None}

# ========== ENDPOINT QUE TE FALTA ==========
@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    """Alias en inglés para /api/platos"""
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port)
# force deploy Tue May  5 02:44:44 PM +01 2026
