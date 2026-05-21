# -*- coding: utf-8 -*-
"""
🏗️ ORQUESTRATOR ISA v7.0-NEON-STABLE
Stack: FastAPI + SQLAlchemy 2.0 Async (psycopg) + Neon DB + WhatsApp Cloud API
Python 3.10 | Render | Production Ready
"""
import os
import json
import uuid
import httpx
import logging
import time as time_module
from datetime import datetime, date, time as datetime_time
from enum import Enum
from typing import Optional, List, Dict, Any
from collections import defaultdict
from decimal import Decimal

from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Enum as SAEnum, String, Text, Integer, Boolean, DECIMAL, Date, Time, DateTime, JSON, select, insert
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

# ==========================================================
# 🔧 CONFIGURACIÓN SEGURA
# ==========================================================
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("orquestrator_bot")

# 1. STRIP en variables críticas para evitar "Bearer " vacío
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PANEL_SECRET = os.getenv("PANEL_SESSION_SECRET", "fallback_secret_2026").strip()
WEBHOOK_VERIFY = os.getenv("VERIFY_TOKEN", "isa_verify_2026").strip()
WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
WA_PHONE_ID = os.getenv("PHONE_NUMBER_ID", "").strip()

if not DATABASE_URL: logger.warning("⚠️ DATABASE_URL vacía. Modo DEMO.")
if not WA_TOKEN: logger.warning("⚠️ WHATSAPP_TOKEN vacío. Envío WA deshabilitado.")
if not WA_PHONE_ID: logger.warning("⚠️ PHONE_NUMBER_ID vacío. Webhook deshabilitado.")

# ==========================================================
# 🗄️ ENGINE DB (NEON COMPATIBLE + POOL SEGURO)
# ==========================================================
engine = None
async_session_maker = None

if DATABASE_URL:
    # Asegurar esquema psycopg
    if "postgresql://" in DATABASE_URL and "psycopg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    
    # 2. POOL SETTINGS (Soluciona "SSL connection closed unexpectedly")
    engine = create_async_engine(
        DATABASE_URL,
        pool_pre_ping=True,       # ✅ Revive conexiones caídas antes de usarlas
        pool_recycle=300,         # ✅ Recicla cada 5 min
        echo=False
    )
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    logger.info("✅ Engine DB inicializado (Neon-compatible)")

class Base(DeclarativeBase): pass

# ==========================================================
# 📦 MODELOS SQLALCHEMY 2.0 (ENUMS CORRECTOS)
# ==========================================================
# 3. ENUMS CON SALTOS DE LÍNEA (Sintaxis limpia)
class EstadoPedido(str, Enum):
    pendiente = "pendiente"
    confirmado = "confirmado"
    entregado = "entregado"
    cancelado = "cancelado"

class EstadoReserva(str, Enum):
    pendiente = "pendiente"
    confirmada = "confirmada"
    cancelada = "cancelada"

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
    language_pref: Mapped[str] = mapped_column(String, default="es")

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
    delivery_type: Mapped[str] = mapped_column(String, default="pickup")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class Reservacion(Base):
    __tablename__ = "reservaciones"
    id_reserva: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    codigo_reserva: Mapped[str] = mapped_column(String, unique=True)
    estado: Mapped[EstadoReserva] = mapped_column(SAEnum(EstadoReserva, name="estado_reserva", create_type=False), default=EstadoReserva.pendiente)
    fecha_reserva: Mapped[date] = mapped_column(Date)
    hora_reserva: Mapped[datetime_time] = mapped_column(Time)
    num_personas: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

# ==========================================================
# 🛠️ HELPERS
# ==========================================================
async def get_db() -> AsyncSession:
    if not async_session_maker: raise HTTPException(500, "DB offline")
    async with async_session_maker() as session:
        try: yield session; await session.commit()
        except: await session.rollback(); raise

async def send_wa(phone: str, text: str):
    if not WA_PHONE_ID or not WA_TOKEN: return logger.info(f"[SIM-WA] {phone}: {text}")
    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    # 4. HEADER BLINDADO
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","to":phone,"type":"text","text":{"body": text[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload, headers=headers)
            if r.status_code != 200: logger.error(f"WA Error: {r.text[:200]}")
    except Exception as e: logger.error(f"WA Ex: {e}")

# ==========================================================
# 🤖 BOT LOGIC (HÍBRIDA: Estado en Memoria, Pedidos en DB)
# ==========================================================
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
pedido_estado: Dict[str, dict] = {}

async def process_msg(payload: dict):
    if not async_session_maker: return
    try:
        entry = payload["entry"][0]
        val = entry["changes"][0]["value"]
        msg = val.get("messages", [{}])[0]
        if not msg or msg.get("type") != "text": return

        phone = msg["from"]
        txt = msg["text"]["body"].strip().lower()
        lang = user_lang.get(phone, "es")
        fase = pedido_estado.get(phone, {}).get("fase", "inicio")

        async with async_session_maker() as db:
            # 1. Fallback Restaurante
            res_r = await db.execute(select(Restaurante).limit(1))
            rest = res_r.scalar_one_or_none()
            if not rest: return
            rid = rest.id_restaurante

            # 2. Upsert Cliente
            res_c = await db.execute(select(Cliente).where(Cliente.wa_id == phone))
            cli = res_c.scalar_one_or_none()
            if not cli:
                cli = Cliente(id_restaurante=rid, wa_id=phone, telefono=phone, language_pref=lang)
                db.add(cli); await db.flush()

            # 3. Conversación
            res_v = await db.execute(select(Conversacion).where(Conversacion.id_cliente == cli.id_cliente).order_by(Conversacion.last_message_at.desc()).limit(1))
            conv = res_v.scalar_one_or_none()
            if not conv:
                conv = Conversacion(id_cliente=cli.id_cliente, id_restaurante=rid, contexto_bot={"fase":"lang"})
                db.add(conv); await db.flush()
            conv.last_message_at = datetime.utcnow()

            ctx = conv.contexto_bot or {"fase":"lang", "carrito": []}
            reply = "🤔 Usa: `m` menú, `v` pedido, `c` confirmar, `r` reservar, `q` salir"

            # --- MÁQUINA DE ESTADOS ---
            if ctx.get("fase") == "lang" or txt in ("q", "salir"):
                if txt == "1": reply="🇪🇸 Español activado."; ctx["lang"]="es"
                elif txt == "2": reply="🇬🇧 English active."; ctx["lang"]="en"
                else: reply="🌍 Elige: 1. Español 2. English"
                ctx["fase"] = "lang" if txt in ("q","salir") else "menu"
            
            elif txt in ("m", "menu"):
                ctx["fase"] = "menu"; reply = "📋 *MENÚ*\n1. Tajín (70)\n2. Cuscús (80)\n3. Pastilla (90)\nResponde nº."
            
            elif txt.isdigit() and ctx.get("fase") == "menu":
                platos = {"1":{"n":"Tajín","p":70}, "2":{"n":"Cuscús","p":80}, "3":{"n":"Pastilla","p":90}}
                if txt in platos:
                    ctx.setdefault("carrito", []).append(platos[txt])
                    total = sum(i['p'] for i in ctx["carrito"])
                    reply = f"✅ {platos[txt]['n']} añadido. Total: {total} MAD."
                else: reply = "❌ Nº inválido."
            
            elif txt in ("v", "pedido"):
                items = ctx.get("carrito", [])
                if items:
                    t = sum(i['p'] for i in items)
                    reply = "🛒 *PEDIDO*\n" + "\n".join([f"• {i['n']}" for i in items]) + f"\n💰 Total: {t} MAD\nEnvía `c`."
                else: reply = "🛒 Carrito vacío."
            
            elif txt in ("c", "confirm"):
                if ctx.get("carrito"):
                    ctx["fase"] = "pago"; reply = "💳 *PAGO*\n1. Efectivo\n2. Tarjeta"
                else: reply = "⚠️ Vacío."

            elif ctx.get("fase") == "pago":
                total = sum(i['p'] for i in ctx.get("carrito", []))
                if txt == "1":
                    # GUARDAR PEDIDO EN DB NEON
                    ped = Pedido(id_restaurante=rid, id_cliente=cli.id_cliente, items=ctx["carrito"], total=Decimal(str(total)))
                    db.add(ped); await db.flush()
                    reply = f"✅ Guardado! ID: {str(ped.id_pedido)[-6:]}"
                    ctx["carrito"] = []; ctx["fase"] = "menu"
                elif txt == "2":
                    reply = "💳 Tarjeta no disponible aún."
            
            elif txt == "r":
                ctx["fase"] = "res_p"; reply = "👥 ¿Personas?"
            elif ctx.get("fase") == "res_p" and txt.isdigit():
                ctx["temp"] = {"p": int(txt)}; ctx["fase"] = "res_t"; reply = "🕒 DD-MM-AAAA HH:MM:"
            elif ctx.get("fase") == "res_t":
                try:
                    dt = datetime.strptime(txt, "%d-%m-%Y %H:%M")
                    code = f"RES-{date.today().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
                    res = Reservacion(id_restaurante=rid, id_cliente=cli.id_cliente, codigo_reserva=code, estado=EstadoReserva.pendiente, fecha_reserva=dt.date(), hora_reserva=dt.time(), num_personas=ctx["temp"]["p"])
                    db.add(res); await db.flush()
                    reply = f"📅 Confirmada. Código: {code}"; ctx["fase"] = "menu"
                except: reply = "📅 Formato: DD-MM-AAAA HH:MM"

            conv.contexto_bot = ctx
            await db.commit()
            await send_wa(phone, reply)
    except Exception as e:
        logger.error(f"Webhook err: {e}")

# ==========================================================
# 🌐 APP & ROUTES
# ==========================================================
app = FastAPI(title="Orquestrator ISA v7.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET, https_only=False)

@app.get("/health")
def health(): return {"status":"ok","db":"online" if engine else "offline"}

@app.get("/api/whatsapp/webhook")
def wb_verify(req: Request):
    if req.query_params.get("hub.mode")=="subscribe" and req.query_params.get("hub.verify_token")==WEBHOOK_VERIFY:
        return JSONResponse(content=int(req.query_params.get("hub.challenge","0")), status_code=200)
    return JSONResponse(status_code=403)

@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    try:
        bg.add_task(process_msg, await req.json())
        return JSONResponse(status_code=200)
    except: return JSONResponse(status_code=500)

# --- PANEL HTML ---
HTML_LOGIN = """<html><body class="bg-gray-100 flex h-screen items-center justify-center"><div class="bg-white p-8 rounded shadow w-96"><h1 class="text-xl font-bold mb-4">🔐 Panel</h1><form action="/panel/login" method="post"><input name="api_key" class="w-full p-2 border mb-4" placeholder="API Key" required><button class="w-full bg-blue-600 text-white p-2 rounded">Entrar</button></form></div></body></html>"""
HTML_RECEPCION = """<html><head><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-6"><h1 class="text-2xl font-bold mb-6">📊 Recepción</h1><div id="pedidos" class="text-gray-500">Cargando...</div><script>fetch('/api/v1/pedidos/activos').then(r=>r.json()).then(d=>document.getElementById('pedidos').innerHTML=d.length+' pedidos activos');</script></body></html>"""

@app.get("/panel/login") def p_login(): return HTMLResponse(content=HTML_LOGIN)
@app.post("/panel/login") def p_login_post(req: Request, api_key: str = Form(...)): req.session["auth"]="ok"; return RedirectResponse("/panel/recepcion", status_code=303)
@app.get("/panel/recepcion") def p_recep(req: Request):
    if req.session.get("auth") != "ok": return RedirectResponse("/panel/login")
    return HTMLResponse(content=HTML_RECEPCION)

@app.get("/api/v1/pedidos/activos")
async def get_pedidos(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Pedido).order_by(Pedido.created_at.desc()))
    return [{"id":str(p.id_pedido)[-6:], "total":float(p.total)} for p in res.scalars().all()]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
