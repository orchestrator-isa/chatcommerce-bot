# main_minimal_api.py — solo para debug
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

app = FastAPI()

class ClientCreate(BaseModel):
    name: Optional[str] = Field(None, alias="name")
    nombre: Optional[str] = Field(None, alias="nombre")
    
    class Config:
        populate_by_name = True

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/clients")
async def list_clients():
    return {"clients": [], "count": 0}

@app.post("/api/clients")
async def create_client(c: ClientCreate):
    return {"client": {"name": c.nombre or c.name}}

# ← Estos endpoints DEBEN estar al mismo nivel que @app.get("/health")
# Si están indentados dentro de una clase, NO se registrarán
