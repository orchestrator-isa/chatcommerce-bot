#!/usr/bin/env python3
"""Orquestrator ISA — TEST EXTREMO"""
import os, logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("isa-test")

# ── FastAPI App ─────────────────────────────────────────────────────
app = FastAPI(title="ISA Test", version="TEST")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "ok", "message": "ISA Bot Test"}

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": "2026-05-04"}

# ── ENDPOINTS API - SIMPLIFICADOS AL MÁXIMO ─────────────────────────
@app.get("/api/test")
async def test():
    return {"message": "API endpoint works!"}

@app.get("/api/clients")
async def list_clients():
    # Respuesta hardcodeada para probar
    return {"clients": [], "count": 0, "message": "API endpoint is reachable"}

@app.post("/api/clients")
async def create_client(name: str = None, telefono: str = None):
    return {
        "message": "Cliente creado (mock)",
        "data": {"name": name or "Test", "telefono": telefono or "+212600000000"}
    }

@app.get("/api/clients")
async def clients_alias():
    """Redirige a /api/restaurantes"""
    from fastapi.responses import JSONResponse
    # Reutilizar la función existente
    return await list_restaurantes()

@app.post("/api/clients")
async def create_client_alias(client_data: dict):
    """Redirige a /api/restaurantes"""
    from fastapi.responses import JSONResponse
    # Adaptar el formato si es necesario
    return await create_restaurante(client_data)

@app.get("/api/menu/{client_id}")
async def menu_alias(client_id: str):
    """Redirige a /api/platos/{menu_id}"""
    return await get_platos(client_id)

@app.post("/api/menu")
async def create_plato_alias(plato_data: dict):
    """Redirige a /api/platos"""
    return await create_plato(plato_data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
