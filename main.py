#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🏗️ ORQUESTRATOR ISA v6.0-NEON-STABLE
Stack: FastAPI + SQLAlchemy 2.0 (psycopg) + Neon DB
"""
import os, json, uuid, httpx, logging, time as time_module
from datetime import datetime, date, time as datetime_time
from enum import Enum
from typing import Optional, List, Dict, Any
from collections import defaultdict
from decimal import Decimal
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Enum as SAEnum, String, Text, Integer, Boolean, DECIMAL, Date, Time, DateTime, JSON, select, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

# 🔧 CONFIGURACIÓN
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("orquestrator_bot")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PANEL_SECRET = os.getenv("PANEL_SESSION_SECRET", "fallback_secret_2026").strip()
WEBHOOK_VERIFY = os.getenv("VERIFY_TOKEN", "isa_verify_2026").strip()
WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
WA_PHONE_ID = os.getenv("PHONE_NUMBER_ID", "").strip()

if not DATABASE_URL: logger.warning("⚠️ DATABASE_URL vacía. Modo DEMO.")
if not WA_TOKEN: logger.warning("⚠️ WHATSAPP_TOKEN vacío. Envío WA deshabilitado.")
if not WA_PHONE_ID: logger.warning("⚠️ PHONE_NUMBER_ID vacío. Webhook deshabilitado.")

# 🗄️ ENGINE DB (NEON COMPATIBLE + POOL SEGURO)
engine = None
async_session_maker = None

if DATABASE_URL:
    # Asegurar esquema psycopg3
    if "postgresql://" in DATABASE_URL and "psycopg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    
    # Pool settings para evitar SSL errors
    engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True, pool_recycle=300, echo=False
    )
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    logger.info("✅ Engine DB inicializado (Neon-compatible)")

class Base(DeclarativeBase): pass

# 📦 MODELOS (ENUMS CORRECTOS - SALTO DE LÍNEA)
class EstadoPedido(str, Enum):
    pendiente = "pendiente"
    confirmado = "confirmado"
    entregado = "entregado"
    cancelado = "cancelado"

class Restaurante(Base):
    __tablename__ = "restaurantes"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

class Cliente(Base):
    __tablename__ = "clientes"
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    wa_id: Mapped[str] = mapped_column(String, unique=True)
    telefono: Mapped[str] = mapped_column(String)

class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    contexto_bot: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class Pedido(Base):
    __tablename__ = "pedidos"
    id_pedido: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado: Mapped[EstadoPedido] = mapped_column(SAEnum(EstadoPedido, name="estado_pedido", create_type=False), default=EstadoPedido.pendiente)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

# 🛠️ HELPERS
async def send_wa(phone: str, text: str):
    if not WA_PHONE_ID or not WA_TOKEN: return logger.info(f"[SIM-WA] {phone}: {text}")
    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","to":phone,"type":"text","text":{"body": text[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload, headers=headers)
            if r.status_code != 200: logger.error(f"WA Error {r.status_code}: {r.text[:200]}")
    except Exception as e: logger.error(f"WA Ex: {e}")

# 🤖 BOT LOGIC
async def process_msg(payload: dict):
    if not async_session_maker: return
    try:
        entry = payload["entry"][0]
        val = entry["changes"][0]["value"]
        msg = val.get("messages", [{}])[0]
        if not msg or msg.get("type") != "text": return

        phone, txt = msg["from"], msg["text"]["body"].strip().lower()

        async with async_session_maker() as db:
            res_r = await db.execute(select(Restaurante).limit(1))
            rest = res_r.scalar_one_or_none()
            if not rest: return
            rid = rest.id_restaurante

            res_c = await db.execute(select(Cliente).where(Cliente.wa_id == phone))
            cli = res_c.scalar_one_or_none()
            if not cli:
                cli = Cliente(id_restaurante=rid, wa_id=phone, telefono=phone)
                db.add(cli); await db.flush()

            res_v = await db.execute(select(Conversacion).where(Conversacion.id_cliente == cli.id_cliente).limit(1))
            conv = res_v.scalar_one_or_none()
            if not conv:
                conv = Conversacion(id_cliente=cli.id_cliente, id_restaurante=rid, contexto_bot={"fase":"lang", "carrito":[]})
                db.add(conv)
            
            ctx = conv.contexto_bot
            if txt == "m":
                await send_wa(phone, "📋 MENÚ:\n1. Tajín (70)\n2. Cuscús (80)\nResponde nº.")
                ctx["fase"] = "menu"
            elif txt.isdigit() and ctx.get("fase") == "menu":
                platos = {"1":{"n":"Tajín","p":70}, "2":{"n":"Cuscús","p":80}}
                if txt in platos:
                    ctx["carrito"].append(platos[txt])
                    t = sum(i['p'] for i in ctx["carrito"])
                    await send_wa(phone, f"✅ {platos[txt]['n']} añadido. Total: {t} MAD.")
            elif txt == "c":
                if ctx.get("carrito"):
                    total = sum(i['p'] for i in ctx["carrito"])
                    ped = Pedido(id_restaurante=rid, id_cliente=cli.id_cliente, items=ctx["carrito"], total=Decimal(str(total)))
                    db.add(ped)
                    await db.commit()
                    await send_wa(phone, f"✅ Pedido guardado! ID: {str(ped.id_pedido)[-6:]}")
                    ctx["carrito"] = []
                else: await send_wa(phone, "⚠️ Vacío.")
            else:
                await send_wa(phone, "🤔 Usa: `m` menú, `c` confirmar, `r` reservar, `q` salir")
            
            conv.contexto_bot = ctx
            await db.commit()
    except Exception as e: logger.error(f"Error bot: {e}")

# 🌐 APP
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET)

@app.get("/health")
def health(): return {"status":"ok", "db":"online" if engine else "offline"}

@app.get("/api/whatsapp/webhook")
def wb_verify(req: Request):
    if req.query_params.get("hub.verify_token") == WEBHOOK_VERIFY:
        return int(req.query_params.get("hub.challenge", 0))
    return JSONResponse(status_code=403)

@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    bg.add_task(process_msg, await req.json())
    return {"status": "ok"}

@app.get("/panel/login")
def p_login():
    return HTMLResponse(content="<html><body><form action='/panel/login' method='post'><input name='api_key'><button>Entrar</button></form></body></html>")

@app.post("/panel/login")
def p_login_post(req: Request, api_key: str = Form(...)):
    req.session["auth"]="ok"
    return RedirectResponse("/panel/recepcion", status_code=303)

@app.get("/panel/recepcion")
def p_recep(req: Request):
    if req.session.get("auth") != "ok": return RedirectResponse("/panel/login")
    return HTMLResponse(content="<html><body><h1>📊 Panel Recepción</h1><p>Operativo.</p></body></html>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
