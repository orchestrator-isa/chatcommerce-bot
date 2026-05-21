# -*- coding: utf-8 -*-
"""
🏗️ ORQUESTRATOR ISA v12.1-ROOT-FIX
Stack: FastAPI + SQLAlchemy 2.0 (psycopg) + Neon DB + WhatsApp Cloud API
Python 3.10 | Render | Production Ready | Single-File
"""

import os
import uuid
import httpx
import logging
from datetime import datetime
from enum import Enum
from decimal import Decimal

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Enum as SAEnum, String, Boolean, DECIMAL, DateTime, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

# 🔧 CONFIGURACIÓN SEGURA
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("orquestrator_bot")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PANEL_SECRET = os.getenv("PANEL_SESSION_SECRET", "fallback_secret_2026").strip()
WEBHOOK_VERIFY = os.getenv("VERIFY_TOKEN", "isa_verify_2026").strip()
WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
WA_PHONE_ID = os.getenv("PHONE_NUMBER_ID", "").strip()

if not DATABASE_URL:
    logger.warning("⚠️ DATABASE_URL vacía. Modo DEMO.")
if not WA_TOKEN:
    logger.warning("⚠️ WHATSAPP_TOKEN vacío. Envío WA deshabilitado.")
if not WA_PHONE_ID:
    logger.warning("⚠️ PHONE_NUMBER_ID vacío. Webhook deshabilitado.")

# 🗄️ ENGINE DB (NEON + POOL SEGURO)
engine = None
async_session_maker = None

if DATABASE_URL:
    if "postgresql://" in DATABASE_URL and "psycopg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True, pool_recycle=300, echo=False
    )
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    logger.info("✅ Engine DB inicializado (Neon-compatible)")


class Base(DeclarativeBase):
    pass


# 📦 MODELOS (ENUMS CORRECTOS: 1 MIEMBRO POR LÍNEA)
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
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nombre: Mapped[str] = mapped_column(String)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Cliente(Base):
    __tablename__ = "clientes"
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    wa_id: Mapped[str] = mapped_column(String, unique=True)
    telefono: Mapped[str] = mapped_column(String)


class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    contexto_bot: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class Pedido(Base):
    __tablename__ = "pedidos"
    id_pedido: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado: Mapped[EstadoPedido] = mapped_column(
        SAEnum(EstadoPedido, name="estado_pedido", create_type=False),
        default=EstadoPedido.pendiente,
    )
    items: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


# 🛠️ HELPERS
async def get_db() -> AsyncSession:
    if not async_session_maker:
        raise HTTPException(500, "DB offline")
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except:
            await session.rollback()
            raise


async def send_wa(phone: str, text: str):
    if not WA_PHONE_ID or not WA_TOKEN:
        return logger.info(f"[SIM-WA] {phone}: {text}")
    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text[:1600]},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json=payload, headers=headers)
    except Exception as e:
        logger.error(f"WA Ex: {e}")


# 🤖 BOT LOGIC (PERSISTENCIA REAL)
async def process_msg(payload: dict):
    if not async_session_maker:
        return
    try:
        msg = payload["entry"][0]["changes"][0]["value"].get("messages", [{}])[0]
        if not msg or msg.get("type") != "text":
            return

        phone, txt = msg["from"], msg["text"]["body"].strip().lower()
        async with async_session_maker() as db:
            res_r = await db.execute(select(Restaurante).limit(1))
            rest = res_r.scalar_one_or_none()
            if not rest:
                return
            rid = rest.id_restaurante

            res_c = await db.execute(select(Cliente).where(Cliente.wa_id == phone))
            cli = res_c.scalar_one_or_none()
            if not cli:
                cli = Cliente(id_restaurante=rid, wa_id=phone, telefono=phone)
                db.add(cli)
                await db.flush()

            res_v = await db.execute(
                select(Conversacion)
                .where(Conversacion.id_cliente == cli.id_cliente)
                .limit(1)
            )
            conv = res_v.scalar_one_or_none()
            if not conv:
                conv = Conversacion(
                    id_cliente=cli.id_cliente,
                    id_restaurante=rid,
                    contexto_bot={"fase": "lang", "carrito": []},
                )
                db.add(conv)
                await db.flush()

            ctx = conv.contexto_bot or {"fase": "lang", "carrito": []}
            reply = "🤔 Usa: `m` menú, `v` pedido, `c` confirmar, `q` salir"

            if txt in ("q", "salir"):
                ctx["fase"] = "lang"
                ctx["carrito"] = []
                reply = "🔄 Sesión reiniciada."
            elif ctx.get("fase") == "lang" or txt == "1":
                ctx["fase"] = "menu"
                reply = "📋 *MENÚ*\n1. Tajín (70)\n2. Cuscús (80)\nResponde nº."
            elif txt in ("m", "menu"):
                ctx["fase"] = "menu"
                reply = "📋 *MENÚ*\n1. Tajín (70)\n2. Cuscús (80)\nResponde nº."
            elif txt.isdigit() and ctx.get("fase") == "menu":
                platos = {"1": {"n": "Tajín", "p": 70}, "2": {"n": "Cuscús", "p": 80}}
                if txt in platos:
                    ctx.setdefault("carrito", []).append(platos[txt])
                    t = sum(i["p"] for i in ctx["carrito"])
                    reply = f"✅ {platos[txt]['n']} añadido. Total: {t} MAD."
                else:
                    reply = "❌ Nº inválido."
            elif txt in ("v", "pedido"):
                items = ctx.get("carrito", [])
                if items:
                    t = sum(i["p"] for i in items)
                    reply = (
                        "🛒 *PEDIDO*\n"
                        + "\n".join([f"• {i['n']}" for i in items])
                        + f"\n💰 Total: {t} MAD"
                    )
                else:
                    reply = "🛒 Carrito vacío."
            elif txt in ("c", "confirm"):
                if ctx.get("carrito"):
                    total = sum(i["p"] for i in ctx["carrito"])
                    ped = Pedido(
                        id_restaurante=rid,
                        id_cliente=cli.id_cliente,
                        items=ctx["carrito"],
                        total=Decimal(str(total)),
                    )
                    db.add(ped)
                    await db.flush()
                    reply = f"✅ Guardado! ID: {str(ped.id_pedido)[-6:]}"
                    ctx["carrito"] = []
                    ctx["fase"] = "menu"
                else:
                    reply = "⚠️ Vacío."

            # ✅ PERSISTENCIA OBLIGATORIA ANTES DE RESPONDER
            conv.contexto_bot = ctx
            conv.last_message_at = datetime.utcnow()
            await db.commit()
            await send_wa(phone, reply)
    except Exception as e:
        logger.error(f"Webhook err: {e}")


# 🌐 APP & ROUTES (EXPANDIDAS CORRECTAMENTE)
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET)


@app.get("/health")
def health():
    return {"status": "ok", "db": "online" if engine else "offline"}


@app.get("/api/whatsapp/webhook")
def wb_verify(req: Request):
    if req.query_params.get("hub.verify_token") == WEBHOOK_VERIFY:
        return int(req.query_params.get("hub.challenge", 0))
    return JSONResponse(content={"status": "forbidden"}, status_code=403)


@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    bg.add_task(process_msg, await req.json())
    return JSONResponse(content={"status": "ok"}, status_code=200)


@app.get("/panel/login")
def p_login():
    return HTMLResponse(
        content="<html><body><form action='/panel/login' method='post'><input name='api_key'><button>Entrar</button></form></body></html>"
    )


@app.post("/panel/login")
def p_login_post(req: Request, api_key: str = Form(...)):
    req.session["auth"] = "ok"
    return RedirectResponse("/panel/recepcion", status_code=303)


@app.get("/panel/recepcion")
def p_recep(req: Request):
    if req.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(
        content="<html><body><h1>📊 Panel</h1><p>Operativo.</p></body></html>"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
