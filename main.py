"""
Orquestrator ISA v4.1.0-STABLE
Stack: FastAPI + SQLAlchemy 2.0 Async + psycopg3 + WhatsApp Cloud API
Python 3.10 | Render | Neon DB | Single File MVP
"""
import os, uuid, json, time as time_module, httpx, logging
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
from pydantic import BaseModel, ConfigDict

# ==========================================================
# 🔧 CONFIGURACIÓN SEGURA (Sin espacios en claves, fallbacks robustos)
# ==========================================================
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("orquestrator_bot")

# Variables de entorno limpias
DATABASE_URL = os.getenv("DATABASE_URL")
PANEL_SECRET = os.getenv("PANEL_SESSION_SECRET", "super-secret-mvp-2026")
WEBHOOK_VERIFY = os.getenv("VERIFY_TOKEN") or os.getenv("WA_VERIFY_TOKEN", "isa_verify_2026")
WA_TOKEN = os.getenv("WHATSAPP_TOKEN") or os.getenv("WA_ACCESS_TOKEN")
WA_PHONE_ID = os.getenv("PHONE_NUMBER_ID") or os.getenv("WA_PHONE_NUMBER_ID")

if not DATABASE_URL:
    logger.warning("⚠️ DATABASE_URL no configurada. Modo DEMO.")
if not WA_TOKEN or not WA_PHONE_ID:
    logger.warning("⚠️ Credenciales WhatsApp incompletas. Envío deshabilitado.")

# Engine con pool settings para Neon/Postgres
engine = None
async_session_maker = None
if DATABASE_URL:
    # Asegurar sintaxis async para psycopg
    if DATABASE_URL.startswith("postgresql://") and "psycopg" not in DATABASE_URL and "asyncpg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True, pool_recycle=300, echo=False,
        connect_args={"sslmode": "require"} if "neon" in DATABASE_URL or "supabase" in DATABASE_URL else {}
    )
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    logger.info("✅ Engine DB inicializado correctamente")

class Base(DeclarativeBase): pass

# ==========================================================
# 🗄️ MODELOS SQLALCHEMY 2.0 (Corregidos: sin typos, tipos exactos)
# ==========================================================
class EstadoPedido(str, Enum):
    pendiente="pendiente", confirmado="confirmado", en_preparacion="en_preparacion", listo="listo", entregado="entregado", cancelado="cancelado"

class EstadoReserva(str, Enum):
    pendiente="pendiente", confirmada="confirmada", sentada="sentada", completada="completada", cancelada="cancelada", no_show="no_show"

class Restaurante(Base):
    __tablename__ = "restaurantes"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String)
    telefono: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class ApiKey(Base):
    __tablename__ = "api_keys"
    id_api_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key: Mapped[str] = mapped_column(String, unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

class RestauranteApiKey(Base):
    __tablename__ = "restaurante_api_keys"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id_api_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)

class RestauranteConfig(Base):
    __tablename__ = "restaurante_config"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    reservation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_reservation_days_ahead: Mapped[int] = mapped_column(Integer, default=30)
    max_guests_per_reservation: Mapped[int] = mapped_column(Integer, default=10)

class Cliente(Base):
    __tablename__ = "clientes"
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    wa_id: Mapped[str] = mapped_column(String)
    telefono: Mapped[str] = mapped_column(String)
    language_pref: Mapped[str] = mapped_column(String, default="es")

class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    contexto_bot: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class Mensaje(Base):
    __tablename__ = "mensajes"
    id_mensaje: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_conversacion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    direccion: Mapped[str] = mapped_column(String)
    contenido: Mapped[Optional[str]] = mapped_column(String, nullable=True)

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
# 🛠️ DEPENDENCIES & HELPERS
# ==========================================================
async def get_db() -> AsyncSession:
    if not async_session_maker: raise HTTPException(500, "DB offline")
    async with async_session_maker() as session:
        try: yield session; await session.commit()
        except: await session.rollback(); raise

async def get_tenant(api_key: str = Header(..., alias="X-Restaurant-API-Key"), db: AsyncSession = Depends(get_db)) -> Restaurante:
    if not api_key: raise HTTPException(401, "Missing API Key")
    res = await db.execute(
        select(Restaurante)
        .join(RestauranteApiKey, RestauranteApiKey.id_restaurante == Restaurante.id_restaurante)
        .join(ApiKey, ApiKey.id_api_key == RestauranteApiKey.id_api_key)
        .where(ApiKey.api_key == api_key, Restaurante.activo == True, ApiKey.activo == True)
    )
    rest = res.scalar_one_or_none()
    if not rest: raise HTTPException(403, "API Key inválida o restaurante inactivo")
    return rest

async def send_wa(wa_id: str, text: str):
    if not WA_TOKEN or not WA_PHONE_ID: return logger.info(f"[SIM-WA] {wa_id}: {text}")
    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","to":wa_id,"type":"text","text":{"body": text[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload, headers=headers)
            if r.status_code != 200: logger.error(f"WA Error: {r.text}")
    except Exception as e: logger.error(f"WA Exception: {e}")

rate_limits: Dict[str, List[float]] = defaultdict(list)
def check_rate_limit(client_ip: str) -> bool:
    now = time_module.time()
    rate_limits[client_ip] = [t for t in rate_limits[client_ip] if t > now - 60]
    if len(rate_limits[client_ip]) >= 100: return False
    rate_limits[client_ip].append(now)
    return True

# ==========================================================
# 🤖 WEBHOOK & BOT LOGIC
# ==========================================================
async def process_wa_message(payload: dict):
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        msg_data = change["value"].get("messages", [{}])[0]
        if not msg_data or msg_data.get("type") != "text": return

        wa_id = msg_data["from"]
        text = msg_data["text"]["body"].strip().lower()

        if not async_session_maker: return
        async with async_session_maker() as db:
            rest_res = await db.execute(select(Restaurante).where(Restaurante.activo == True).limit(1))
            rest = rest_res.scalar_one_or_none()
            if not rest: return
            rest_id = rest.id_restaurante

            cl_res = await db.execute(select(Cliente).where(Cliente.wa_id == wa_id))
            cliente = cl_res.scalar_one_or_none()
            if not cliente:
                cliente = Cliente(id_restaurante=rest_id, wa_id=wa_id, telefono=wa_id, nombre="Nuevo Cliente")
                db.add(cliente); await db.flush()

            conv_res = await db.execute(select(Conversacion).where(Conversacion.id_cliente == cliente.id_cliente).order_by(Conversacion.last_message_at.desc()).limit(1))
            conv = conv_res.scalar_one_or_none()
            if not conv:
                conv = Conversacion(id_cliente=cliente.id_cliente, id_restaurante=rest_id, contexto_bot={"fase": "inicio", "carrito": []})
                db.add(conv); await db.flush()

            ctx = conv.contexto_bot or {"fase": "inicio", "carrito": []}
            db.add(Mensaje(id_conversacion=conv.id_conversacion, direccion="inbound", contenido=text))
            reply = "🤔 No entendí. Usa: `menu`, `v`, `c`, `1-10`, `reservar`, `q`"

            if text in ("q", "salir", "reset"):
                ctx = {"fase": "inicio", "carrito": []}; reply = "🔄 Sesión reiniciada. Envía `menu`."
            elif text in ("m", "menu", "0"):
                ctx["fase"] = "menu"; reply = "📋 *MENÚ*\n1. Tajín Pollo (70)\n2. Cuscús (80)\n3. Pastilla (90)\n4. Té Menta (15)\nResponde con el número."
            elif text in ("v", "pedido"):
                cart = ctx.get("carrito", [])
                if not cart: reply = "🛒 Carrito vacío."
                else: reply = "🛒 *PEDIDO*\n" + "\n".join([f"• {i['nombre']} x{i.get('cant',1)}" for i in cart])
            elif text in ("c", "confirm"):
                cart = ctx.get("carrito", [])
                if not cart: reply = "⚠️ Carrito vacío."
                else:
                    total = sum(i.get("precio",0) * i.get("cant",1) for i in cart)
                    pedido = Pedido(id_restaurante=rest_id, id_cliente=cliente.id_cliente, items=cart, total=Decimal(str(total)))
                    db.add(pedido); await db.flush()
                    ctx["carrito"] = []; ctx["fase"] = "inicio"
                    reply = f"✅ Pedido guardado. ID: {str(pedido.id_pedido)[-6:].upper()}"
            elif text.isdigit():
                idx = int(text)-1
                platos = [{"nombre":"Tajín Pollo","precio":70},{"nombre":"Cuscús","precio":80},{"nombre":"Pastilla","precio":90},{"nombre":"Té Menta","precio":15}]
                if 0 <= idx < len(platos):
                    ctx.setdefault("carrito", []).append(platos[idx])
                    reply = f"✅ {platos[idx]['nombre']} añadido. Escribe `v` para ver."
            elif text == "reservar":
                ctx["fase"] = "res_p"; reply = "👥 ¿Cuántas personas?"
            elif ctx.get("fase") == "res_p" and text.isdigit():
                ctx["temp"] = {"p": int(text)}; ctx["fase"] = "res_t"; reply = "🕒 Fecha/Hora (DD-MM-AAAA HH:MM):"
            elif ctx.get("fase") == "res_t":
                try:
                    dt = datetime.strptime(text, "%d-%m-%Y %H:%M")
                    code = f"RES-{date.today().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
                    res = Reservacion(id_restaurante=rest_id, id_cliente=cliente.id_cliente, codigo_reserva=code, estado=EstadoReserva.pendiente, fecha_reserva=dt.date(), hora_reserva=dt.time(), num_personas=ctx["temp"]["p"])
                    db.add(res); await db.flush()
                    reply = f"📅 Reserva confirmada. Código: {code}"
                    ctx["fase"] = "inicio"
                except: reply = "📅 Formato: DD-MM-AAAA HH:MM"

            conv.contexto_bot = ctx
            await db.commit()
            await send_wa(wa_id, reply)
    except Exception as e: logger.error(f"Webhook err: {e}")

# ==========================================================
# 🌐 APP & ROUTES
# ==========================================================
app = FastAPI(title="Orquestrator ISA", version="4.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET, https_only=False)

@app.get("/health")
def health(): return {"status":"ok","db":"online" if engine else "offline","wa":"configured" if WA_PHONE_ID else "missing"}

@app.get("/api/whatsapp/webhook")
def wb_verify(req: Request):
    if req.query_params.get("hub.mode")=="subscribe" and req.query_params.get("hub.verify_token")==WEBHOOK_VERIFY:
        return JSONResponse(content=int(req.query_params.get("hub.challenge","0")), status_code=200)
    return JSONResponse(status_code=403)

@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    if not check_rate_limit(req.client.host): return JSONResponse(status_code=429)
    try:
        bg.add_task(process_wa_message, await req.json())
        return JSONResponse(status_code=200)
    except: return JSONResponse(status_code=500)

@app.get("/api/v1/pedidos/activos")
async def get_pedidos_activos(db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    res = await db.execute(select(Pedido).where(Pedido.id_restaurante == rest.id_restaurante, Pedido.estado.in_([EstadoPedido.pendiente, EstadoPedido.confirmado, EstadoPedido.en_preparacion])).order_by(Pedido.created_at.desc()))
    return [{"id":str(p.id_pedido)[:8], "total":float(p.total), "estado":p.estado.value} for p in res.scalars().all()]

@app.get("/api/v1/reservaciones/hoy")
async def get_reservas_hoy(db: AsyncSession = Depends(get_db), rest: Restaurante = Depends(get_tenant)):
    res = await db.execute(select(Reservacion).where(Reservacion.id_restaurante == rest.id_restaurante, Reservacion.fecha_reserva == date.today()).order_by(Reservacion.hora_reserva))
    return [{"codigo":r.codigo_reserva, "personas":r.num_personas, "hora":r.hora_reserva.isoformat(), "estado":r.estado.value} for r in res.scalars().all()]

HTML_LOGIN = """<!DOCTYPE html><html><head><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-100 flex h-screen items-center justify-center">
<div class="bg-white p-8 rounded shadow-md w-96"><h1 class="text-2xl font-bold mb-4">🔐 Panel Recepción</h1>
<form action="/panel/login" method="post"><input type="password" name="api_key" placeholder="API Key" class="w-full p-2 border rounded mb-4"><button type="submit" class="w-full bg-blue-600 text-white p-2 rounded">Entrar</button></form>
<p class="text-red-500 mt-2 text-sm">{error}</p></div></body></html>"""

HTML_RECEPCION = """<!DOCTYPE html><html><head><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-6">
<h1 class="text-2xl font-bold mb-6">📊 Panel Recepción</h1>
<div class="grid grid-cols-2 gap-6">
<div class="bg-white p-4 rounded shadow"><h2 class="text-xl font-bold mb-2">📅 Reservas Hoy <span id="r-count" class="bg-blue-100 text-blue-800 px-2 py-1 rounded">0</span></h2><div id="reservas" class="text-gray-500">Cargando...</div></div>
<div class="bg-white p-4 rounded shadow"><h2 class="text-xl font-bold mb-2">🛒 Pedidos Activos <span id="p-count" class="bg-green-100 text-green-800 px-2 py-1 rounded">0</span></h2><div id="pedidos" class="text-gray-500">Cargando...</div></div>
</div>
<script>
const refresh = async () => {
  const r = await fetch('/api/v1/reservaciones/hoy').then(x=>x.json());
  const p = await fetch('/api/v1/pedidos/activos').then(x=>x.json());
  document.getElementById('r-count').textContent = r.length;
  document.getElementById('p-count').textContent = p.length;
  document.getElementById('reservas').innerHTML = r.map(x=>`<div class="border-b p-2">${x.codigo} | ${x.personas}pax | ${x.hora} | ${x.estado}</div>`).join('') || 'Sin reservas';
  document.getElementById('pedidos').innerHTML = p.map(x=>`<div class="border-b p-2">${x.id} | ${x.total} MAD | ${x.estado}</div>`).join('') || 'Sin pedidos';
};
setInterval(refresh, 30000); refresh();
</script></body></html>"""

@app.get("/panel/login")
def panel_login(error: str = ""): return HTMLResponse(content=HTML_LOGIN.format(error=error))

@app.post("/panel/login")
async def panel_login_post(request: Request, api_key: str = Form(...), db: AsyncSession = Depends(get_db)):
    try:
        rest = await get_tenant(api_key, db)
        request.session["rest_id"] = str(rest.id_restaurante)
        request.session["api_key"] = api_key
        return RedirectResponse(url="/panel/recepcion", status_code=303)
    except HTTPException: return panel_login(error="Clave inválida")

@app.get("/panel/recepcion")
def panel_recepcion(request: Request):
    if "api_key" not in request.session: return RedirectResponse("/panel/login")
    return HTMLResponse(content=HTML_RECEPCION)

# ==========================================================
# 🚀 STARTUP
# ==========================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), workers=1)
