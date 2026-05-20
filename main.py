"""
Orquestrator ISA v2.3.0-MVP-PANEL-FIXED
Arquitectura: FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + WhatsApp Cloud API
┌─────────────┐      HTTPS/JSON       ┌──────────────────────────────┐      asyncpg      ┌──────────────────┐
│   CLIENTE   │ ─────────────────────▶ │   RENDER (FastAPI + Panel)   │ ────────────────▶ │   SUPABASE       │
│  (WhatsApp) │ ◀──────────────────── │   main.py v2.3               │ ◀──────────────── │   PostgreSQL     │
└─────────────┘      WhatsApp API      └──────────┬───────────────────┘                 └──────────────────┘
                                                  │
                                          ┌───────┴───────────┐
                                          │  🔄 WhatsApp Cloud│
                                          │  API (Meta)       │
                                          └───────────────────┘
"""
import os
import uuid
import json
import time
import httpx
import logging
from datetime import datetime, date, time, timedelta
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
from sqlalchemy import Enum as SAEnum, String, Text, Integer, Boolean, DECIMAL, Date, Time, DateTime, JSON, select, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

from pydantic import BaseModel, Field, ConfigDict
# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN Y LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:5432/postgres")
PANEL_SECRET = os.getenv("PANEL_SESSION_SECRET", "super-secret-mvp-2026")
WEBHOOK_VERIFY = os.getenv("WEBHOOK_VERIFY_TOKEN", "isa_verify_2026")
WA_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v18.0")
WA_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE ENGINE
# ─────────────────────────────────────────────────────────────────────────────
engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

# ─────────────────────────────────────────────────────────────────────────────
# ENUMS NATIVOS POSTGRESQL
# ─────────────────────────────────────────────────────────────────────────────
class EstadoPedido(str, Enum):
    pendiente = "pendiente"
    confirmado = "confirmado"
    en_preparacion = "en_preparacion"
    listo = "listo"
    entregado = "entregado"
    cancelado = "cancelado"

class EstadoReserva(str, Enum):
    pendiente = "pendiente"
    confirmada = "confirmada"
    sentada = "sentada"
    completada = "completada"
    cancelada = "cancelada"
    no_show = "no_show"

# ─────────────────────────────────────────────────────────────────────────────
# MODELOS SQLALCHEMY 2.0
# ─────────────────────────────────────────────────────────────────────────────
class Language(Base):
    __tablename__ = "languages"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    name_native: Mapped[Optional[str]]
    is_rtl: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class Currency(Base):
    __tablename__ = "currencies"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    symbol: Mapped[str]
    decimals: Mapped[int] = mapped_column(Integer, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class Restaurante(Base):
    __tablename__ = "restaurantes"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str]
    telefono: Mapped[Optional[str]]
    direccion: Mapped[Optional[str]]
    ciudad: Mapped[Optional[str]]
    pais: Mapped[str] = mapped_column(String, default="Marruecos")
    currency_code: Mapped[str] = mapped_column(String)
    default_language: Mapped[str] = mapped_column(String, default="es")
    plan: Mapped[str] = mapped_column(String, default="basico")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    meta_verificado: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

class ApiKey(Base):
    __tablename__ = "api_keys"
    id_api_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key: Mapped[str] = mapped_column(String, unique=True)
    descripcion: Mapped[Optional[str]]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

class RestauranteApiKey(Base):
    __tablename__ = "restaurante_api_keys"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id_api_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class RestauranteConfig(Base):
    __tablename__ = "restaurante_config"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    welcome_message: Mapped[str] = mapped_column(Text, default="¡Bienvenido!")
    menu_auto_send: Mapped[bool] = mapped_column(Boolean, default=True)
    horario_apertura: Mapped[Optional[time]]
    horario_cierre: Mapped[Optional[time]]
    dias_abierto: Mapped[list] = mapped_column(JSON, default=list)
    tax_rate: Mapped[Decimal] = mapped_column(DECIMAL(5,2), default=0.00)
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pickup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reservation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_reservation_days_ahead: Mapped[int] = mapped_column(Integer, default=30)
    max_guests_per_reservation: Mapped[int] = mapped_column(Integer, default=10)
    ai_agent_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

class RestauranteIdioma(Base):
    __tablename__ = "restaurante_idiomas"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    codigo_idioma: Mapped[str] = mapped_column(String, primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

class Cliente(Base):
    __tablename__ = "clientes"
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    wa_id: Mapped[str] = mapped_column(String)
    nombre: Mapped[Optional[str]]
    telefono: Mapped[str]
    language_pref: Mapped[str] = mapped_column(String, default="es")
    total_pedidos: Mapped[int] = mapped_column(Integer, default=0)
    total_gastado: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=0.00)
    last_visit_at: Mapped[Optional[datetime]]
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class Menu(Base):
    __tablename__ = "menus"
    id_menu: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    nombre: Mapped[str]
    descripcion: Mapped[Optional[str]]
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    orden: Mapped[int] = mapped_column(Integer, default=0)

class Plato(Base):
    __tablename__ = "platos"
    id_plato: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_menu: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    nombre: Mapped[str]
    descripcion: Mapped[Optional[str]]
    precio: Mapped[Decimal] = mapped_column(DECIMAL(10,2))
    categoria: Mapped[Optional[str]]
    disponible: Mapped[bool] = mapped_column(Boolean, default=True)
    destacado: Mapped[bool] = mapped_column(Boolean, default=False)
    prep_time_min: Mapped[Optional[int]]
    orden: Mapped[int] = mapped_column(Integer, default=0)

class PlatoTraduccion(Base):
    __tablename__ = "plato_traducciones"
    id_plato: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    codigo_idioma: Mapped[str] = mapped_column(String, primary_key=True)
    nombre: Mapped[str]
    descripcion: Mapped[Optional[str]]

class PlatoOpcion(Base):
    __tablename__ = "plato_opciones"
    id_opcion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_plato: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    nombre: Mapped[str]
    requerido: Mapped[bool] = mapped_column(Boolean, default=False)
    multiple: Mapped[bool] = mapped_column(Boolean, default=False)
    opciones: Mapped[list] = mapped_column(JSONB, default=list)

class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado: Mapped[str] = mapped_column(String, default="activa")
    contexto_bot: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class Mensaje(Base):
    __tablename__ = "mensajes"
    id_mensaje: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_conversacion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    direccion: Mapped[str] = mapped_column(String)
    tipo: Mapped[str] = mapped_column(String, default="texto")
    contenido: Mapped[Optional[str]]
    ai_intent: Mapped[Optional[str]]
    ai_confidence: Mapped[Optional[Decimal]]
    ai_model: Mapped[Optional[str]]

class Pedido(Base):
    __tablename__ = "pedidos"
    id_pedido: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_conversacion: Mapped[Optional[uuid.UUID]]
    estado: Mapped[EstadoPedido] = mapped_column(SAEnum(EstadoPedido, name="estado_pedido", create_type=False), default=EstadoPedido.pendiente)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    subtotal: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=0.00)
    impuestos: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=0.00)
    descuento: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=0.00)
    total: Mapped[Decimal] = mapped_column(DECIMAL(10,2))
    metodo_pago: Mapped[str] = mapped_column(String, default="efectivo")
    delivery_type: Mapped[str] = mapped_column(String, default="pickup")

class PedidoHistorial(Base):
    __tablename__ = "pedido_historial"
    id_historial: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_pedido: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado_anterior: Mapped[Optional[str]]
    estado_nuevo: Mapped[str]
    cambiado_por: Mapped[Optional[str]]
    notas: Mapped[Optional[str]]

class Reservacion(Base):
    __tablename__ = "reservaciones"
    id_reserva: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_conversacion: Mapped[Optional[uuid.UUID]]
    codigo_reserva: Mapped[str] = mapped_column(String, unique=True)
    estado: Mapped[EstadoReserva] = mapped_column(SAEnum(EstadoReserva, name="estado_reserva", create_type=False), default=EstadoReserva.pendiente)
    fecha_reserva: Mapped[date]
    hora_reserva: Mapped[time]
    num_personas: Mapped[int]
    mesa_asignada: Mapped[Optional[str]]
    zona: Mapped[Optional[str]]
    ai_confirmada: Mapped[bool] = mapped_column(Boolean, default=False)

class ReservaHistorial(Base):
    __tablename__ = "reserva_historial"
    id_historial: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_reserva: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado_anterior: Mapped[Optional[str]]
    estado_nuevo: Mapped[str]
    cambiado_por: Mapped[Optional[str]]
    notas: Mapped[Optional[str]]

class Suscripcion(Base):
    __tablename__ = "suscripciones"
    id_suscripcion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    plan: Mapped[str]
    precio_mensual: Mapped[Decimal] = mapped_column(DECIMAL(10,2))
    currency_code: Mapped[str]
    estado: Mapped[str]

class Factura(Base):
    __tablename__ = "facturas"
    id_factura: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_suscripcion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    periodo_inicio: Mapped[date]
    periodo_fin: Mapped[date]
    monto: Mapped[Decimal] = mapped_column(DECIMAL(10,2))
    estado_pago: Mapped[str]

# ─────────────────────────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────
class RestauranteCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    nombre: str
    telefono: Optional[str] = None
    ciudad: str
    currency_code: str = "MAD"
    default_language: str = "es"

class PedidoCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    items: List[Dict]
    delivery_type: str = "pickup"
    metodo_pago: str = "efectivo"
    descuento: float = 0.0

class ReservaCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    fecha_reserva: date
    hora_reserva: time
    num_personas: int = Field(gt=0)
    mesa_asignada: Optional[str] = None
    zona: Optional[str] = None
    solicitudes_especiales: Optional[str] = None

# ─────────────────────────────────────────────────────────────────────────────
# DEPENDENCIES & HELPERS
# ─────────────────────────────────────────────────────────────────────────────
async def get_db() -> AsyncSession:
    async with async_session_maker() as session:
        yield session

async def get_tenant(api_key: str = Header(..., alias="X-Restaurant-API-Key"), db: AsyncSession = Depends(get_db)) -> Restaurante:
    result = await db.execute(
        select(Restaurante)
        .join(RestauranteApiKey, RestauranteApiKey.id_restaurante == Restaurante.id_restaurante)
        .join(ApiKey, ApiKey.id_api_key == RestauranteApiKey.id_api_key)
        .where(ApiKey.api_key == api_key, Restaurante.activo == True)
    )
    rest = result.scalar_one_or_none()
    if not rest:
        raise HTTPException(status_code=403, detail="API Key inválida o restaurante inactivo")
    return rest

# Rate limiting en memoria
RATE_LIMIT_DB: Dict[str, List[float]] = defaultdict(list)
RATE_LIMIT_MAX = 100
RATE_LIMIT_WINDOW = 60

def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    RATE_LIMIT_DB[client_ip] = [t for t in RATE_LIMIT_DB[client_ip] if t > now - RATE_LIMIT_WINDOW]
    if len(RATE_LIMIT_DB[client_ip]) >= RATE_LIMIT_MAX:
        return False
    RATE_LIMIT_DB[client_ip].append(now)
    return True

async def send_wa_message(wa_id: str, text: str):
    url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": wa_id,
        "type": "text",
        "text": {"body": text}
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.error(f"WA Send failed: {resp.text}")

# ─────────────────────────────────────────────────────────────────────────────
# APP INIT & MIDDLEWARE
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Orquestrator ISA v2.3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET, https_only=False)

@app.on_event("startup")
async def startup():
    #await seed_database_once()
    pass 
# ─────────────────────────────────────────────────────────────────────────────
# SEED DATA (solo la primera vez)
# ─────────────────────────────────────────────────────────────────────────────
async def seed_database_once():
    async with async_session_maker() as db:
        res = await db.execute(select(Restaurante).limit(1))
        if res.scalar_one_or_none():
            logger.info("Base de datos ya poblada. Seed omitido.")
            return

        logger.info("🌱 Ejecutando seed básico...")
        r1 = Restaurante(id_restaurante=uuid.uuid4(), nombre="Restinga", telefono="+212668087490", ciudad="Tetuan", currency_code="MAD")
        r2 = Restaurante(id_restaurante=uuid.uuid4(), nombre="Cafe Al Hizam", telefono="+212600000000", ciudad="Marrakech", currency_code="MAD")
        db.add_all([r1, r2])
        await db.commit()

        db.add_all([
            RestauranteConfig(id_restaurante=r1.id_restaurante, welcome_message="Marhaba bi Restinga!", tax_rate=0.10, reservation_enabled=True),
            RestauranteConfig(id_restaurante=r2.id_restaurante, welcome_message="Bienvenido a Al Hizam", tax_rate=0.10, reservation_enabled=True)
        ])
        await db.commit()
        logger.info("Seed básico completado. Las API keys deben insertarse manualmente en Supabase.")

# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK Y BOT
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/whatsapp/webhook")
def webhook_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == WEBHOOK_VERIFY:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Invalid webhook token")

@app.post("/api/whatsapp/webhook")
async def webhook_receive(request: Request, background_tasks: BackgroundTasks):
    client_ip = request.client.host
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    payload = await request.json()
    background_tasks.add_task(process_inbound_message, payload)
    return {"status": "success"}

async def process_inbound_message(payload: dict):
    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]["value"]
        msg_data = changes.get("messages", [{}])[0]
        if not msg_data:
            return

        wa_id = msg_data["from"]
        text = msg_data["text"]["body"].lower().strip()

        async with async_session_maker() as db:
            # Obtener el primer restaurante activo como tenant por defecto
            result = await db.execute(select(Restaurante).where(Restaurante.activo == True).limit(1))
            rest = result.scalar_one_or_none()
            if not rest:
                logger.error("No hay restaurantes activos para procesar mensajes")
                return
            rest_id = rest.id_restaurante

            # Cliente
            res = await db.execute(select(Cliente).where(Cliente.wa_id == wa_id))
            cliente = res.scalar_one_or_none()
            if not cliente:
                cliente = Cliente(id_restaurante=rest_id, wa_id=wa_id, telefono=wa_id, nombre="Cliente Nuevo")
                db.add(cliente)
                await db.commit()

            conv = await db.execute(select(Conversacion).where(Conversacion.id_cliente == cliente.id_cliente))
            conv = conv.scalar_one_or_none()
            if not conv:
                conv = Conversacion(id_cliente=cliente.id_cliente, id_restaurante=rest_id, contexto_bot={"order_draft": []})
                db.add(conv)
                await db.commit()

            ctx = conv.contexto_bot or {"order_draft": []}
            draft = ctx.get("order_draft", [])

            reply = ""
            if text in ["m", "menu", "0"]:
                reply = "📋 *Menú Restinga*\n1. Tajín Pollo - 70\n2. Cuscús - 80\nResponde con el número para añadir."
            elif text in ["v", "pedido", "carrito"]:
                if draft:
                    reply = "🛒 *Tu Carrito*\n" + "\n".join([f"- {i.get('nombre')}: {i.get('precio')} MAD" for i in draft])
                else:
                    reply = "El carrito está vacío. Escribe 'menu'."
            elif text in ["c", "confirm"]:
                if draft:
                    total = sum(i["precio"] for i in draft)
                    nuevo_pedido = Pedido(
                        id_restaurante=rest_id, id_cliente=cliente.id_cliente, id_conversacion=conv.id_conversacion,
                        items=draft, subtotal=total, total=total
                    )
                    db.add(nuevo_pedido)
                    ctx["order_draft"] = []
                    conv.contexto_bot = ctx
                    reply = "✅ Pedido confirmado. Tiempo estimado: 30 min."
                else:
                    reply = "No hay items en el carrito."
            elif text.startswith("x "):
                try:
                    idx = int(text.split()[1]) - 1
                    if 0 <= idx < len(draft):
                        draft.pop(idx)
                        ctx["order_draft"] = draft
                        conv.contexto_bot = ctx
                        reply = "Item eliminado."
                    else:
                        reply = "Índice inválido."
                except:
                    reply = "Uso: x 1"
            elif text.isdigit() and int(text) > 0:
                platos = [{"nombre": "Tajín Pollo", "precio": 70}, {"nombre": "Cuscús", "precio": 80}]
                idx = int(text) - 1
                if idx < len(platos):
                    draft.append(platos[idx])
                    ctx["order_draft"] = draft
                    conv.contexto_bot = ctx
                    reply = f"✅ {platos[idx]['nombre']} añadido."
                else:
                    reply = "Plato no encontrado."
            elif text in ["q", "salir"]:
                ctx["order_draft"] = []
                conv.contexto_bot = ctx
                reply = "🔄 Sesión reiniciada."
            else:
                reply = "No entendí. Usa 'menu', 'v', 'c', 'x 1', o un número."

            msg = Mensaje(id_conversacion=conv.id_conversacion, direccion="inbound", contenido=text)
            db.add(msg)
            await db.commit()

            await send_wa_message(wa_id, reply)
    except Exception as e:
        logger.error(f"Bot process failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS API v1
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.3.0-MVP", "ts": datetime.utcnow().isoformat()}

@app.get("/api/v1/restaurantes")
async def get_restaurantes(rest: Restaurante = Depends(get_tenant)):
    # Devuelve el restaurante autenticado (o una lista con él)
    return [rest]

@app.get("/api/v1/pedidos")
async def get_pedidos(db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    res = await db.execute(select(Pedido).where(Pedido.id_restaurante == rest.id_restaurante).order_by(Pedido.created_at.desc()))
    return res.scalars().all()

@app.get("/api/v1/pedidos/activos")
async def get_pedidos_activos(db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    active_states = [EstadoPedido.pendiente, EstadoPedido.confirmado, EstadoPedido.en_preparacion]
    res = await db.execute(select(Pedido).where(Pedido.id_restaurante == rest.id_restaurante, Pedido.estado.in_(active_states)))
    return res.scalars().all()

@app.post("/api/v1/pedidos")
async def create_pedido(data: PedidoCreate, db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    subtotal = sum(i.get("precio", 0) for i in data.items)
    total = subtotal + (subtotal * 0.10) - data.descuento
    ped = Pedido(id_restaurante=rest.id_restaurante, id_cliente=uuid.uuid4(), items=data.items, subtotal=subtotal, total=total, delivery_type=data.delivery_type)
    db.add(ped)
    await db.commit()
    return ped

@app.patch("/api/v1/pedidos/{pedido_id}")
async def update_pedido_estado(pedido_id: uuid.UUID, nuevo_estado: EstadoPedido, db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    ped = await db.get(Pedido, pedido_id)
    if not ped or ped.id_restaurante != rest.id_restaurante:
        raise HTTPException(404, "Pedido no encontrado")
    hist = PedidoHistorial(id_pedido=ped.id_pedido, estado_anterior=ped.estado.value, estado_nuevo=nuevo_estado.value, cambiado_por="admin")
    db.add(hist)
    ped.estado = nuevo_estado
    await db.commit()
    return {"status": "updated"}

@app.get("/api/v1/reservaciones/hoy")
async def get_reservas_hoy(db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    hoy = date.today()
    res = await db.execute(select(Reservacion).where(Reservacion.id_restaurante == rest.id_restaurante, Reservacion.fecha_reserva == hoy))
    return res.scalars().all()

@app.post("/api/v1/reservaciones")
async def create_reserva(data: ReservaCreate, db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    config_res = await db.execute(select(RestauranteConfig).where(RestauranteConfig.id_restaurante == rest.id_restaurante))
    config = config_res.scalar_one_or_none()
    if not config or not config.reservation_enabled:
        raise HTTPException(400, "Reservas deshabilitadas")
    count = (await db.execute(select(func.count(Reservacion.id_reserva)))).scalar()
    codigo = f"RES-{date.today().strftime('%Y%m%d')}-{count + 1:02d}"
    reserva = Reservacion(id_restaurante=rest.id_restaurante, id_cliente=uuid.uuid4(), codigo_reserva=codigo, estado=EstadoReserva.pendiente, **data.dict())
    db.add(reserva)
    await db.commit()
    return reserva

@app.patch("/api/v1/reservaciones/{reserva_id}/confirmar")
async def confirmar_reserva(reserva_id: uuid.UUID, db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    res = await db.get(Reservacion, reserva_id)
    if not res or res.id_restaurante != rest.id_restaurante:
        raise HTTPException(404, "No encontrada")
    if res.estado != EstadoReserva.pendiente:
        raise HTTPException(400, "Solo pendientes")
    res.estado = EstadoReserva.confirmada
    await db.commit()
    return res

@app.patch("/api/v1/reservaciones/{reserva_id}/asignar-mesa")
async def asignar_mesa(reserva_id: uuid.UUID, mesa: str, zona: str, db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    res = await db.get(Reservacion, reserva_id)
    if not res or res.id_restaurante != rest.id_restaurante:
        raise HTTPException(404, "No encontrada")
    if res.estado not in [EstadoReserva.pendiente, EstadoReserva.confirmada]:
        raise HTTPException(400, "Estado inválido")
    res.mesa_asignada = mesa
    res.zona = zona
    await db.commit()
    return res

# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/v1/dashboard/hoy")
async def dashboard_hoy(db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    hoy = date.today()
    ingresos = await db.execute(select(func.sum(Pedido.total)).where(Pedido.id_restaurante == rest.id_restaurante, func.date(Pedido.created_at) == hoy))
    pedidos = await db.execute(select(func.count(Pedido.id_pedido)).where(Pedido.id_restaurante == rest.id_restaurante, func.date(Pedido.created_at) == hoy))
    reservas = await db.execute(select(func.count(Reservacion.id_reserva)).where(Reservacion.id_restaurante == rest.id_restaurante, Reservacion.fecha_reserva == hoy))
    return {
        "ingresos_hoy": float(ingresos.scalar() or 0),
        "pedidos_hoy": pedidos.scalar() or 0,
        "reservas_hoy": reservas.scalar() or 0
    }

# ─────────────────────────────────────────────────────────────────────────────
# PANEL HTML
# ─────────────────────────────────────────────────────────────────────────────
HTML_LOGIN = """
<form action="/panel/login" method="post" class="bg-white p-6 rounded shadow max-w-sm mx-auto mt-20">
  <h1 class="text-xl font-bold mb-4">🔐 Panel de Recepción</h1>
  <input name="api_key" type="password" placeholder="API Key" class="w-full p-2 border mb-2" required>
  <button type="submit" class="w-full bg-blue-600 text-white p-2 rounded">Ingresar</button>
  <p class="text-red-500 mt-2 text-sm">{error}</p>
</form>
"""

HTML_RECEPCION = """
<!DOCTYPE html><html><head><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-6">
<h1 class="text-2xl font-bold mb-4">📥 Recepción - {nombre}</h1>
<div class="grid grid-cols-2 gap-4">
  <div>
    <h2 class="text-lg font-bold mb-2">📅 Reservas Hoy <span id="r-count" class="bg-blue-100 text-blue-800 px-2 py-1 rounded">0</span></h2>
    <table class="w-full bg-white shadow rounded text-sm" id="reservas-tabla">
      <thead><tr class="bg-gray-100"><th class="p-2">Código</th><th>Personas</th><th>Hora</th><th>Estado</th></tr></thead>
      <tbody id="r-body"></tbody>
    </table>
  </div>
  <div>
    <h2 class="text-lg font-bold mb-2">🛒 Pedidos Activos <span id="p-count" class="bg-green-100 text-green-800 px-2 py-1 rounded">0</span></h2>
    <table class="w-full bg-white shadow rounded text-sm" id="pedidos-tabla">
      <thead><tr class="bg-gray-100"><th class="p-2">ID</th><th>Total</th><th>Estado</th></tr></thead>
      <tbody id="p-body"></tbody>
    </table>
  </div>
</div>
<script>
const refresh = async () => {
  const r = await fetch('/api/v1/reservaciones/hoy').then(x=>x.json());
  const p = await fetch('/api/v1/pedidos/activos').then(x=>x.json());
  document.getElementById('r-count').textContent = r.length;
  document.getElementById('p-count').textContent = p.length;
  document.getElementById('r-body').innerHTML = r.map(x => `<tr class="border-t"><td class="p-2">${x.codigo_reserva}</td><td>${x.num_personas}</td><td>${x.hora_reserva}</td><td>${x.estado}</td></tr>`).join('');
  document.getElementById('p-body').innerHTML = p.map(x => `<tr class="border-t"><td class="p-2">${x.id_pedido.slice(0,8)}</td><td>${x.total} MAD</td><td>${x.estado}</td></tr>`).join('');
};
setInterval(refresh, 30000); refresh();
</script></body></html>
"""

@app.get("/panel/login")
def panel_login(request: Request, error: str = ""):
    return HTMLResponse(content=HTML_LOGIN.format(error=error), status_code=200)

@app.post("/panel/login")
async def panel_login_submit(request: Request, api_key: str = Form(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Restaurante)
        .join(RestauranteApiKey, RestauranteApiKey.id_restaurante == Restaurante.id_restaurante)
        .join(ApiKey, ApiKey.id_api_key == RestauranteApiKey.id_api_key)
        .where(ApiKey.api_key == api_key, Restaurante.activo == True)
    )
    rest = result.scalar_one_or_none()
    if not rest:
        return panel_login(request, error="Clave inválida")
    request.session["rest_id"] = str(rest.id_restaurante)
    request.session["api_key"] = api_key
    return RedirectResponse("/panel/recepcion", status_code=303)

@app.get("/panel/recepcion")
def panel_recepcion(request: Request):
    if "api_key" not in request.session:
        return panel_login(request, error="No autenticado")
    # Podrías obtener el nombre del restaurante desde la sesión o DB
    return HTMLResponse(content=HTML_RECEPCION.format(nombre="Restinga"))

@app.get("/panel/metricas")
async def panel_metricas(request: Request, db: AsyncSession = Depends(get_db)):
    if "api_key" not in request.session:
        return panel_login(request, error="No autenticado")
    # Obtener el restaurante desde la sesión
    rest_id = uuid.UUID(request.session.get("rest_id"))
    rest = await db.get(Restaurante, rest_id)
    if not rest:
        return panel_login(request, error="Restaurante no encontrado")
    data = await dashboard_hoy(db, rest=rest)
    html = f"""
    <!DOCTYPE html><html><head><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-6">
    <h1 class="text-2xl font-bold mb-6">📊 Métricas Hoy - {rest.nombre}</h1>
    <div class="grid grid-cols-3 gap-4">
      <div class="bg-white p-4 rounded shadow"><div class="text-green-500 text-3xl">💰</div><h2 class="text-lg">{data['ingresos_hoy']:.2f} MAD</h2><p class="text-gray-500">Ingresos</p></div>
      <div class="bg-white p-4 rounded shadow"><div class="text-blue-500 text-3xl">🛒</div><h2 class="text-lg">{data['pedidos_hoy']}</h2><p class="text-gray-500">Pedidos</p></div>
      <div class="bg-white p-4 rounded shadow"><div class="text-purple-500 text-3xl">📅</div><h2 class="text-lg">{data['reservas_hoy']}</h2><p class="text-gray-500">Reservas</p></div>
    </div>
    </body></html>"""
    return HTMLResponse(content=html)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
