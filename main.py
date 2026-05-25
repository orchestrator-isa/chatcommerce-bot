# -*- coding: utf-8 -*-
# ruff: noqa: E501
"""
ORQUESTRATOR ISA v18.3 - DEFINITIVO (4 idiomas, comandos separados)
- Selector con 4 idiomas: Español, English, Français, Darija
- Al elegir idioma: muestra bienvenida + comandos disponibles
- Menú se muestra solo con 'm' (navegación n/a, PDF)
- Carrito, cantidades, pedidos, reservas con reglas
- Panel, endpoints staff, PDF desde BD
- Sin errores ruff E701
"""

import os
import uuid
import httpx
import logging
import textwrap
from datetime import datetime, timezone, timedelta, date, time
from enum import Enum
from decimal import Decimal
from typing import Optional

from fastapi import (
    FastAPI, HTTPException, BackgroundTasks, Request, Form, Depends, Header
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import (
    Enum as SAEnum, String, Boolean, DECIMAL, DateTime,
    Integer, Time as SQLTime, Date, select, and_, func, LargeBinary, update
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

# ============================================================
# CONFIGURACIÓN
# ============================================================
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

# ============================================================
# ENGINE DB
# ============================================================
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
    logger.info("✅ Engine DB inicializado (Neon)")

# ============================================================
# MODELOS
# ============================================================
class Base(DeclarativeBase):
    pass

class EstadoPedido(str, Enum):
    pendiente = "pendiente"
    confirmado = "confirmado"
    entregado = "entregado"
    cancelado = "cancelado"

class EstadoReserva(str, Enum):
    pendiente = "pendiente"
    confirmada = "confirmada"
    sentada = "sentada"
    completada = "completada"
    cancelada = "cancelada"
    no_show = "no_show"

class Restaurante(Base):
    __tablename__ = "restaurantes"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

class ApiKey(Base):
    __tablename__ = "api_keys"
    id_api_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_value: Mapped[str] = mapped_column(String, unique=True)
    descripcion: Mapped[str] = mapped_column(String, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

class RestauranteApiKey(Base):
    __tablename__ = "restaurante_api_keys"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id_api_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)

class RestauranteConfig(Base):
    __tablename__ = "restaurante_config"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    reservation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_reservation_days_ahead: Mapped[int] = mapped_column(Integer, default=7)
    max_guests_per_reservation: Mapped[int] = mapped_column(Integer, default=10)
    horario_apertura: Mapped[time] = mapped_column(SQLTime, nullable=True)
    horario_cierre: Mapped[time] = mapped_column(SQLTime, nullable=True)
    dias_abierto: Mapped[list[int]] = mapped_column(JSONB, default=list)

class Cliente(Base):
    __tablename__ = "clientes"
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    wa_id: Mapped[str] = mapped_column(String, unique=True)
    telefono: Mapped[str] = mapped_column(String)
    language_pref: Mapped[str] = mapped_column(String, default="es")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    contexto_bot: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Menu(Base):
    __tablename__ = "menus"
    id_menu: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

class Plato(Base):
    __tablename__ = "platos"
    id_plato: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_menu: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    precio: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))
    disponible: Mapped[bool] = mapped_column(Boolean, default=True)
    orden: Mapped[int] = mapped_column(Integer, default=0)

class PlatoTraduccion(Base):
    __tablename__ = "plato_traducciones"
    id_plato: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    codigo_idioma: Mapped[str] = mapped_column(String, primary_key=True)
    nombre: Mapped[str] = mapped_column(String)

class Pedido(Base):
    __tablename__ = "pedidos"
    id_pedido: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado: Mapped[EstadoPedido] = mapped_column(SAEnum(EstadoPedido, name="estado_pedido", create_type=False), default=EstadoPedido.pendiente)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Reservacion(Base):
    __tablename__ = "reservaciones"
    id_reserva: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    codigo_reserva: Mapped[str] = mapped_column(String, unique=True)
    estado: Mapped[EstadoReserva] = mapped_column(SAEnum(EstadoReserva, name="estado_reserva", create_type=False), default=EstadoReserva.pendiente)
    fecha_reserva: Mapped[date] = mapped_column(Date)
    hora_reserva: Mapped[time] = mapped_column(SQLTime)
    num_personas: Mapped[int] = mapped_column(Integer)
    mesa_asignada: Mapped[str] = mapped_column(String, nullable=True)
    zona: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class ReservaHistorial(Base):
    __tablename__ = "reserva_historial"
    id_historial: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_reserva: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado_anterior: Mapped[str] = mapped_column(String(20), nullable=True)
    estado_nuevo: Mapped[str] = mapped_column(String(20))
    cambiado_por: Mapped[str] = mapped_column(String(100), nullable=True)
    notas: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class MenuPDF(Base):
    __tablename__ = "menu_pdfs"
    id_pdf: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    nombre_archivo: Mapped[str] = mapped_column(String)
    pdf_data: Mapped[bytes] = mapped_column(LargeBinary)
    mime_type: Mapped[str] = mapped_column(String, default="application/pdf")
    tamano_bytes: Mapped[int] = mapped_column(Integer)
    subido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

# ============================================================
# APP
# ============================================================
app = FastAPI(title="Orquestrator ISA v18.3")
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET)

# ============================================================
# HELPERS
# ============================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

async def send_wa(phone: str, text: str):
    if not WA_PHONE_ID or not WA_TOKEN:
        logger.info(f"[SIM-WA] {phone}: {text}")
        return
    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json=payload, headers=headers)
    except Exception as e:
        logger.error(f"WA error: {e}")

_rate_limits = {}
def check_rate_limit(ip: str, max_req: int = 100, window_sec: int = 60) -> bool:
    now = now_utc()
    if ip not in _rate_limits:
        _rate_limits[ip] = []
    _rate_limits[ip] = [t for t in _rate_limits[ip] if (now - t).total_seconds() < window_sec]
    if len(_rate_limits[ip]) >= max_req:
        return False
    _rate_limits[ip].append(now)
    return True

async def get_restaurante_from_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> uuid.UUID:
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        stmt = select(ApiKey).where(
            ApiKey.key_value == x_api_key,
            ApiKey.activo.is_(True),
            (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now_utc())
        )
        api_key_obj = (await db.execute(stmt)).scalar_one_or_none()
        if not api_key_obj:
            raise HTTPException(401, "API Key inválida o expirada")
        stmt2 = select(RestauranteApiKey.id_restaurante).where(RestauranteApiKey.id_api_key == api_key_obj.id_api_key)
        restaurante_id = (await db.execute(stmt2)).scalar_one_or_none()
        if not restaurante_id:
            raise HTTPException(403, "API Key no asociada a ningún restaurante")
        return restaurante_id

async def get_restaurante_id_optional(request: Request, x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> uuid.UUID:
    if x_api_key:
        return await get_restaurante_from_api_key(x_api_key)
    api_key = request.session.get("api_key")
    if api_key:
        return await get_restaurante_from_api_key(api_key)
    raise HTTPException(401, "No se proporcionó autenticación")

def clean_serializable(obj):
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.strftime("%H:%M:%S")
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: clean_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_serializable(item) for item in obj]
    return obj

# ============================================================
# DETECCIÓN DE IDIOMA (4 idiomas)
# ============================================================
IDIOMA_KEYWORDS = {
    "es": ["hola", "buenas", "gracias", "quiero", "menu", "pedido", "español"],
    "en": ["hello", "hi", "thanks", "want", "menu", "order", "english"],
    "fr": ["bonjour", "merci", "je veux", "menu", "français"],
    "dar": ["salam", "marhba", "bghit", "menu", "darija"],
}

def detectar_idioma_por_keyword(txt: str) -> str | None:
    txt_lower = txt.lower()
    for idioma, palabras in IDIOMA_KEYWORDS.items():
        for palabra in palabras:
            if palabra in txt_lower:
                return idioma
    return None

# ============================================================
# TRADUCCIONES (4 idiomas, con comandos en welcome)
# ============================================================
I18N = {
    "es": {
        "welcome": (
            "🌍 *Bienvenido a {restaurante}*\n\n"
            "✅ *Idioma guardado: Español*\n\n"
            "📋 *Comandos disponibles:*\n"
            "`m` → Ver menú\n"
            "`v` → Ver carrito\n"
            "`c` → Confirmar pedido\n"
            "`x N` → Eliminar ítem N\n"
            "`reservar` → Reservar mesa\n"
            "`q` → Salir (reiniciar)\n\n"
            "Escribe `m` para ver el menú."
        ),
        "menu_header": "📋 *MENÚ* (Página {page}/{total_pages})\n",
        "menu_item": "{num}. {nombre} — {precio} MAD",
        "menu_footer": (
            "\n`n` → ➡️ Siguiente\n`a` → ⬅️ Anterior\n"
            "🛒 Escribe un número para añadir\n"
            "📄 `menu pdf` para descargar el menú"
        ),
        "added": "✅ {plato} añadido. Total: {total} MAD.",
        "cart": "🛒 *PEDIDO*\n{items}\n💰 Total: {total} MAD",
        "cart_empty": "🛒 Carrito vacío.",
        "confirm": "✅ Pedido guardado! ID: {id_pedido}",
        "confirm_empty": "⚠️ Carrito vacío.",
        "removed": "🗑️ {plato} eliminado. Total: {total} MAD.",
        "invalid": "❌ Opción inválida.",
        "help": "🤔 *Comandos:*\n`m` → Menú\n`v` → Carrito\n`c` → Confirmar\n`x N` → Eliminar ítem\n`reservar` → Reservar\n`q` → Salir",
        "res_personas": "👥 ¿Cuántas personas? (responde un número)",
        "res_fecha": "📅 ¿Qué fecha? (YYYY-MM-DD)\nEj: 2026-05-25",
        "res_hora": "🕐 ¿Qué hora? (HH:MM)\nEj: 19:30",
        "res_confirm": "📋 *Reserva*\n👥 {personas} personas\n📅 {fecha} 🕐 {hora}\n\nResponde `si` para confirmar",
        "res_saved": "✅ Reserva guardada!\nCódigo: {codigo}",
        "res_cancel": "❌ Reserva cancelada.",
        "res_error_disabled": "❌ Las reservas no están habilitadas.",
        "res_error_date_range": "❌ Solo se pueden reservar hasta {max} días.",
        "res_error_hours": "❌ El restaurante está cerrado en ese horario.",
        "res_error_capacity": "❌ Máximo {max} personas por reserva.",
    },
    "en": {
        "welcome": (
            "🌍 *Welcome to {restaurante}*\n\n"
            "✅ *Language saved: English*\n\n"
            "📋 *Available commands:*\n"
            "`m` → Show menu\n"
            "`v` → View cart\n"
            "`c` → Confirm order\n"
            "`x N` → Remove item N\n"
            "`reservar` → Book table\n"
            "`q` → Exit (restart)\n\n"
            "Type `m` to see the menu."
        ),
        "menu_header": "📋 *MENU* (Page {page}/{total_pages})\n",
        "menu_item": "{num}. {nombre} — {precio} MAD",
        "menu_footer": (
            "\n`n` → ➡️ Next\n`a` → ⬅️ Prev\n"
            "🛒 Reply a number to add\n"
            "📄 `menu pdf` to download the menu"
        ),
        "added": "✅ {plato} added. Total: {total} MAD.",
        "cart": "🛒 *ORDER*\n{items}\n💰 Total: {total} MAD",
        "cart_empty": "🛒 Cart empty.",
        "confirm": "✅ Order saved! ID: {id_pedido}",
        "confirm_empty": "⚠️ Cart empty.",
        "removed": "🗑️ {plato} removed. Total: {total} MAD.",
        "invalid": "❌ Invalid option.",
        "help": "🤔 *Commands:*\n`m` → Menu\n`v` → Cart\n`c` → Confirm\n`x N` → Remove item\n`reservar` → Book\n`q` → Exit",
        "res_personas": "👥 How many people? (reply a number)",
        "res_fecha": "📅 Date? (YYYY-MM-DD)\nEx: 2026-05-25",
        "res_hora": "🕐 Time? (HH:MM)\nEx: 19:30",
        "res_confirm": "📋 *Reservation*\n👥 {personas} people\n📅 {fecha} 🕐 {hora}\n\nReply `yes` to confirm",
        "res_saved": "✅ Reservation saved!\nCode: {codigo}",
        "res_cancel": "❌ Reservation cancelled.",
        "res_error_disabled": "❌ Reservations not enabled.",
        "res_error_date_range": "❌ Max {max} days ahead.",
        "res_error_hours": "❌ Restaurant closed at that time.",
        "res_error_capacity": "❌ Max {max} guests.",
    },
}

def t(key: str, lang: str = "es", **kwargs) -> str:
    return I18N.get(lang, I18N["es"]).get(key, I18N["es"][key]).format(**kwargs)

ITEMS_PER_PAGE = 33

async def get_menu_page(db: AsyncSession, restaurante_id: uuid.UUID, lang: str, page: int):
    menu_query = select(Menu.id_menu).where(Menu.id_restaurante == restaurante_id, Menu.activo)
    menu_ids = (await db.execute(menu_query)).scalars().all()
    if not menu_ids:
        return [], 0

    platos_query = select(Plato).where(Plato.id_menu.in_(menu_ids), Plato.disponible).order_by(Plato.orden)
    platos = (await db.execute(platos_query)).scalars().all()
    total = len(platos)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_platos = platos[start:end]

    plato_ids = [p.id_plato for p in page_platos]
    trans = {}
    if plato_ids:
        trans_stmt = select(PlatoTraduccion).where(and_(PlatoTraduccion.id_plato.in_(plato_ids), PlatoTraduccion.codigo_idioma == lang))
        trans_result = await db.execute(trans_stmt)
        trans = {tr.id_plato: tr for tr in trans_result.scalars().all()}

    menu_items = []
    for idx, p in enumerate(page_platos, start=1):
        global_num = (page - 1) * ITEMS_PER_PAGE + idx
        nombre = trans[p.id_plato].nombre if p.id_plato in trans else f"Plato {p.id_plato}"
        menu_items.append({"num": global_num, "id_plato": str(p.id_plato), "nombre": nombre, "precio": float(p.precio)})
    return menu_items, total_pages

# ============================================================
# PDF ENDPOINT
# ============================================================
@app.get("/menu/pdf")
async def get_menu_pdf(restaurante_id: uuid.UUID):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        stmt = select(MenuPDF).where(MenuPDF.id_restaurante == restaurante_id, MenuPDF.activo)
        pdf_record = (await db.execute(stmt)).scalar_one_or_none()
        if not pdf_record:
            raise HTTPException(404, "PDF no encontrado")
        return Response(content=pdf_record.pdf_data, media_type=pdf_record.mime_type,
                        headers={"Content-Disposition": f"attachment; filename={pdf_record.nombre_archivo}"})

# ============================================================
# BOT: PROCESAMIENTO DE MENSAJES
# ============================================================
async def process_msg(payload: dict):
    if not async_session_maker:
        return
    try:
        entry = payload["entry"][0]
        val = entry["changes"][0]["value"]
        msg = val.get("messages", [{}])[0]
        if not msg or msg.get("type") != "text":
            return

        phone = msg["from"]
        txt_raw = msg["text"]["body"].strip()
        txt = txt_raw.lower()

        async with async_session_maker() as db:
            # 1. Cliente
            stmt_cli = select(Cliente).where(Cliente.wa_id == phone)
            cli = (await db.execute(stmt_cli)).scalar_one_or_none()
            if not cli:
                rest_stmt = select(Restaurante).where(Restaurante.nombre == "Restinga", Restaurante.activo).limit(1)
                rest = (await db.execute(rest_stmt)).scalar_one_or_none()
                if not rest:
                    logger.error("No se encontró el restaurante Restinga")
                    return
                rid = rest.id_restaurante
                rname = rest.nombre
                cli = Cliente(id_restaurante=rid, wa_id=phone, telefono=phone, language_pref="es")
                db.add(cli)
                await db.flush()
                await db.refresh(cli)
            else:
                rid = cli.id_restaurante
                rname = (await db.execute(select(Restaurante.nombre).where(Restaurante.id_restaurante == rid))).scalar_one_or_none() or "Restaurante"

            lang = cli.language_pref

            # 2. Conversación
            stmt_conv = select(Conversacion).where(Conversacion.id_cliente == cli.id_cliente).execution_options(populate_existing=True).limit(1)
            conv = (await db.execute(stmt_conv)).scalar_one_or_none()
            if not conv:
                conv = Conversacion(id_cliente=cli.id_cliente, id_restaurante=rid, contexto_bot={"fase": "lang", "carrito": [], "menu_page": 1, "current_menu_page_dishes": []})
                db.add(conv)
                await db.flush()
                await db.refresh(conv)

            ctx = dict(conv.contexto_bot) if conv.contexto_bot else {}
            ctx.setdefault("fase", "lang")
            ctx.setdefault("carrito", [])
            ctx.setdefault("menu_page", 1)
            ctx.setdefault("current_menu_page_dishes", [])

            logger.info(f"FASE: {ctx['fase']} - Msg: '{txt}' - RestID: {rid}")

            # --- 0. RESET GLOBAL (q) ---
            if txt in ("q", "salir", "quit"):
                try:
                    nuevo_ctx = {"fase": "lang", "carrito": [], "menu_page": 1, "current_menu_page_dishes": []}
                    stmt = update(Conversacion).where(Conversacion.id_cliente == cli.id_cliente).values(
                        contexto_bot=clean_serializable(nuevo_ctx),
                        last_message_at=now_utc()
                    )
                    await db.execute(stmt)
                    await db.commit()
                    logger.info(f"✅ q ejecutado: fase reseteada a 'lang' para {cli.wa_id}")
                except Exception as e:
                    logger.error(f"❌ Error en q: {e}", exc_info=True)
                    await db.rollback()
                    return
                # Enviar bienvenida con comandos
                await send_wa(phone, t("welcome", cli.language_pref, restaurante=rname))
                return

            # --- 1. PDF ---
            if txt == "menu pdf":
                await send_wa(phone, f"📄 *Menú Digital de Restinga*\n➡️ https://chatcommerce-bot.onrender.com/menu/pdf?restaurante_id={rid}\n(Haz clic o copia el enlace)")
                return

            # --- 2. DETECCIÓN DE IDIOMA PRECOZ (Global) ---
            detected_lang = detectar_idioma_por_keyword(txt)
            if detected_lang:
                ctx["fase"] = "lang"
                conv.contexto_bot = clean_serializable(ctx)
                conv.last_message_at = now_utc()
                await db.commit()
                await send_wa(phone, t("welcome", detected_lang, restaurante=rname))
                return

            # --- 3. FLUJO POR FASE ---
            reply = ""
            fase = ctx["fase"]
            lang_map = {
                "s": "es", "es": "es", "español": "es", "1": "es",
                "e": "en", "en": "en", "english": "en", "2": "en",
                "f": "fr", "fr": "fr", "français": "fr", "3": "fr",
                "d": "dar", "dar": "dar", "darija": "dar", "4": "dar",
            }

            if fase == "lang":
                new_lang = lang_map.get(txt)
                if new_lang:
                    lang = new_lang
                    ctx["lang"] = lang
                    ctx["fase"] = "menu"
                    cli.language_pref = lang
                    ctx["menu_page"] = 1
                    ctx["carrito"] = []
                    ctx["current_menu_page_dishes"] = []
                    await db.flush()
                    # No mostrar menú automáticamente, solo enviar bienvenida con comandos
                    reply = t("welcome", lang, restaurante=rname)
                else:
                    reply = t("welcome", lang, restaurante=rname)

            elif fase == "menu":
                # Mostrar menú solo si el usuario escribe 'm'
                if txt in ("m", "menu", "menú"):
                    page = ctx.get("menu_page", 1)
                    menu_items, total_pages = await get_menu_page(db, rid, lang, page)
                    ctx["current_menu_page_dishes"] = menu_items
                    reply = t("menu_header", lang, page=page, total_pages=total_pages)
                    reply += "\n".join(t("menu_item", lang, **it) for it in menu_items)
                    reply += t("menu_footer", lang)

                elif txt in ("n", "siguiente", "next", ">", "->"):
                    page = ctx.get("menu_page", 1)
                    _, total_pages = await get_menu_page(db, rid, lang, 1)
                    if page < total_pages:
                        page += 1
                        ctx["menu_page"] = page
                        menu_items, _ = await get_menu_page(db, rid, lang, page)
                        ctx["current_menu_page_dishes"] = menu_items
                        reply = t("menu_header", lang, page=page, total_pages=total_pages)
                        reply += "\n".join(t("menu_item", lang, **it) for it in menu_items)
                        reply += t("menu_footer", lang)
                    else:
                        reply = "📄 Ya estás en la última página."

                elif txt in ("a", "anterior", "prev", "<", "-<"):
                    page = ctx.get("menu_page", 1)
                    if page > 1:
                        page -= 1
                        ctx["menu_page"] = page
                        menu_items, total_pages = await get_menu_page(db, rid, lang, page)
                        ctx["current_menu_page_dishes"] = menu_items
                        reply = t("menu_header", lang, page=page, total_pages=total_pages)
                        reply += "\n".join(t("menu_item", lang, **it) for it in menu_items)
                        reply += t("menu_footer", lang)
                    else:
                        reply = "📄 Ya estás en la primera página."

                # Añadir al carrito (número simple)
                elif txt.isdigit():
                    num = int(txt)
                    menu_items = ctx.get("current_menu_page_dishes", [])
                    selected = next((item for item in menu_items if item["num"] == num), None)
                    if selected:
                        carrito = list(ctx.get("carrito", []))
                        carrito.append({"id": str(selected["id_plato"]), "nombre": selected["nombre"], "precio": selected["precio"]})
                        ctx["carrito"] = carrito
                        total = sum(item["precio"] for item in carrito)
                        reply = t("added", lang, plato=selected["nombre"], total=total)
                    else:
                        reply = t("invalid", lang)

                # Cantidades: "5 33"
                elif " " in txt and txt.split()[0].isdigit() and len(txt.split()) == 2:
                    parts = txt.split()
                    cantidad = int(parts[0])
                    num_plato = int(parts[1])
                    menu_items = ctx.get("current_menu_page_dishes", [])
                    selected = next((item for item in menu_items if item["num"] == num_plato), None)
                    if selected:
                        carrito = list(ctx.get("carrito", []))
                        for _ in range(cantidad):
                            carrito.append({"id": str(selected["id_plato"]), "nombre": selected["nombre"], "precio": selected["precio"]})
                        ctx["carrito"] = carrito
                        total = sum(item["precio"] for item in carrito)
                        reply = t("added", lang, plato=selected["nombre"], total=total)
                    else:
                        reply = t("invalid", lang)

                elif txt in ("v", "pedido", "view", "order"):
                    items = ctx.get("carrito", [])
                    if items:
                        total = sum(i["precio"] for i in items)
                        items_txt = "\n".join(f"• {i['nombre']} ({i['precio']} MAD)" for i in items)
                        reply = t("cart", lang, items=items_txt, total=total)
                    else:
                        reply = t("cart_empty", lang)

                elif txt in ("c", "confirm", "confirmar"):
                    items = ctx.get("carrito", [])
                    if items:
                        total = sum(i["precio"] for i in items)
                        ped = Pedido(id_restaurante=rid, id_cliente=cli.id_cliente,
                                     items=[{"nombre": i["nombre"], "precio": i["precio"]} for i in items],
                                     total=Decimal(str(total)))
                        db.add(ped)
                        await db.flush()
                        reply = t("confirm", lang, id_pedido=str(ped.id_pedido)[-6:])
                        ctx["carrito"] = []
                    else:
                        reply = t("confirm_empty", lang)

                elif txt.startswith("x "):
                    parts = txt.split()
                    if len(parts) == 2 and parts[1].isdigit():
                        idx = int(parts[1]) - 1
                        carrito = list(ctx.get("carrito", []))
                        if 0 <= idx < len(carrito):
                            removed = carrito.pop(idx)
                            ctx["carrito"] = carrito
                            total = sum(i["precio"] for i in carrito)
                            reply = t("removed", lang, plato=removed["nombre"], total=total)
                        else:
                            reply = t("invalid", lang)
                    else:
                        reply = t("invalid", lang)

                elif txt in ("r", "reservar", "reserve", "book"):
                    config = (await db.execute(select(RestauranteConfig).where(RestauranteConfig.id_restaurante == rid))).scalar_one_or_none()
                    if not config or not config.reservation_enabled:
                        reply = t("res_error_disabled", lang)
                    else:
                        ctx["reserva_config"] = {
                            "max_days": config.max_reservation_days_ahead,
                            "max_guests": config.max_guests_per_reservation,
                            "open_time": config.horario_apertura.strftime("%H:%M") if config.horario_apertura else "09:00",
                            "close_time": config.horario_cierre.strftime("%H:%M") if config.horario_cierre else "23:00",
                            "dias_abierto": config.dias_abierto
                        }
                        ctx["fase"] = "res_p"
                        reply = t("res_personas", lang)

                else:
                    reply = t("help", lang)

            # FLUJO RESERVAS (no modificado, funciona)
            elif fase == "res_p":
                if txt.isdigit():
                    ctx["res_personas"] = int(txt)
                    ctx["fase"] = "res_f"
                    reply = t("res_fecha", lang)
                else:
                    reply = t("res_personas", lang)

            elif fase == "res_f":
                try:
                    fecha_obj = datetime.strptime(txt_raw, "%Y-%m-%d").date()
                    cfg = ctx.get("reserva_config", {})
                    if fecha_obj > datetime.now(timezone.utc).date() + timedelta(days=cfg.get("max_days", 7)):
                        reply = t("res_error_date_range", lang).replace("{max}", str(cfg.get("max_days", 7)))
                    else:
                        ctx["res_fecha"] = txt_raw
                        ctx["fase"] = "res_h"
                        reply = t("res_hora", lang)
                except ValueError:
                    reply = t("res_fecha", lang)

            elif fase == "res_h":
                try:
                    hora_obj = datetime.strptime(txt_raw, "%H:%M").time()
                    cfg = ctx.get("reserva_config", {})
                    open_t = datetime.strptime(cfg.get("open_time", "09:00"), "%H:%M").time()
                    close_t = datetime.strptime(cfg.get("close_time", "23:00"), "%H:%M").time()
                    hoy_weekday = datetime.now(timezone.utc).weekday()
                    if hoy_weekday not in cfg.get("dias_abierto", list(range(7))):
                        reply = "❌ Hoy el restaurante está cerrado."
                    elif not (open_t <= hora_obj <= close_t):
                        reply = t("res_error_hours", lang)
                    else:
                        ctx["res_hora"] = txt_raw
                        ctx["fase"] = "res_c"
                        reply = t("res_confirm", lang, personas=ctx.get("res_personas", 1), fecha=ctx.get("res_fecha", ""), hora=txt_raw)
                except ValueError:
                    reply = t("res_hora", lang)

            elif fase == "res_c":
                cfg = ctx.get("reserva_config", {})
                max_guests = cfg.get("max_guests", 10)
                if ctx.get("res_personas", 1) > max_guests:
                    reply = t("res_error_capacity", lang, max=cfg.get("max_guests", 10))
                    ctx["fase"] = "menu"
                    for k in ("res_personas", "res_fecha", "res_hora", "reserva_config"):
                        ctx.pop(k, None)
                elif txt in ("si", "yes", "oui", "نعم"):
                    codigo = f"RES-{now_utc().strftime('%Y%m%d')}-{now_utc().second:02d}"
                    res = Reservacion(
                        id_restaurante=rid, id_cliente=cli.id_cliente, codigo_reserva=codigo,
                        num_personas=ctx["res_personas"],
                        fecha_reserva=datetime.strptime(ctx["res_fecha"], "%Y-%m-%d").date(),
                        hora_reserva=datetime.strptime(ctx["res_hora"], "%H:%M").time(),
                        estado=EstadoReserva.confirmada
                    )
                    db.add(res)
                    await db.flush()
                    reply = t("res_saved", lang, codigo=codigo)
                    ctx["fase"] = "menu"
                    for k in ("res_personas", "res_fecha", "res_hora", "reserva_config"):
                        ctx.pop(k, None)
                else:
                    reply = t("res_cancel", lang)
                    ctx["fase"] = "menu"
                    for k in ("res_personas", "res_fecha", "res_hora", "reserva_config"):
                        ctx.pop(k, None)
            else:
                ctx["fase"] = "lang"
                reply = t("reset", lang)

            # Guardar contexto y enviar respuesta
            if reply:
                conv.contexto_bot = clean_serializable(ctx)
                conv.last_message_at = now_utc()
                try:
                    await db.commit()
                except Exception as e:
                    logger.error(f"❌ Error commit final: {e}", exc_info=True)
                    await db.rollback()
                await send_wa(phone, reply)

    except Exception as e:
        logger.error(f"Webhook error (outer): {e}", exc_info=True)

# ============================================================
# ENDPOINTS STAFF (idénticos a versión anterior, funcionan)
# ============================================================
@app.patch("/api/v1/reservaciones/{id}/confirmar")
async def confirmar_reserva(id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        reserva = (await db.execute(select(Reservacion).where(Reservacion.id_reserva == id, Reservacion.id_restaurante == restaurante_id))).scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if reserva.estado != EstadoReserva.pendiente:
            raise HTTPException(400, "Solo se puede confirmar una reserva pendiente")
        reserva.estado = EstadoReserva.confirmada
        await db.commit()
        return {"status": "ok", "nuevo_estado": reserva.estado.value}

@app.patch("/api/v1/reservaciones/{id}/cancelar")
async def cancelar_reserva(id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        reserva = (await db.execute(select(Reservacion).where(Reservacion.id_reserva == id, Reservacion.id_restaurante == restaurante_id))).scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if reserva.estado in (EstadoReserva.cancelada, EstadoReserva.completada):
            raise HTTPException(400, "La reserva ya está cancelada o completada")
        reserva.estado = EstadoReserva.cancelada
        await db.commit()
        return {"status": "ok", "nuevo_estado": reserva.estado.value}

@app.patch("/api/v1/reservaciones/{id}/asignar-mesa")
async def asignar_mesa_reserva(id: uuid.UUID, request: Request, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional), mesa: str = Form(...), zona: str = Form(None)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        reserva = (await db.execute(select(Reservacion).where(Reservacion.id_reserva == id, Reservacion.id_restaurante == restaurante_id))).scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if reserva.estado not in (EstadoReserva.pendiente, EstadoReserva.confirmada):
            raise HTTPException(400, "No se puede asignar mesa en este estado")
        reserva.mesa_asignada = mesa
        reserva.zona = zona
        await db.commit()
        return {"status": "ok", "mesa": mesa, "zona": zona}

@app.patch("/api/v1/reservaciones/{id}/marcar-sentada")
async def marcar_sentada(id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        reserva = (await db.execute(select(Reservacion).where(Reservacion.id_reserva == id, Reservacion.id_restaurante == restaurante_id))).scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if not reserva.mesa_asignada:
            raise HTTPException(400, "Primero asigna una mesa")
        if reserva.estado != EstadoReserva.confirmada:
            raise HTTPException(400, "Solo se puede marcar sentada una reserva confirmada")
        reserva.estado = EstadoReserva.sentada
        await db.commit()
        return {"status": "ok", "nuevo_estado": reserva.estado.value}

@app.get("/api/v1/pedidos/activos")
async def pedidos_activos(restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        pedidos = (await db.execute(select(Pedido).where(Pedido.id_restaurante == restaurante_id, Pedido.estado.in_([EstadoPedido.pendiente, EstadoPedido.confirmado])).order_by(Pedido.created_at.desc()))).scalars().all()
        return [{"id": str(p.id_pedido), "cliente": str(p.id_cliente), "total": float(p.total), "estado": p.estado.value, "created_at": p.created_at.isoformat()} for p in pedidos]

@app.patch("/api/v1/pedidos/{id}/estado")
async def cambiar_estado_pedido(id: uuid.UUID, nuevo_estado: str, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        pedido = (await db.execute(select(Pedido).where(Pedido.id_pedido == id, Pedido.id_restaurante == restaurante_id))).scalar_one_or_none()
        if not pedido:
            raise HTTPException(404, "Pedido no encontrado")
        try:
            nuevo = EstadoPedido(nuevo_estado)
        except ValueError:
            raise HTTPException(400, "Estado inválido")
        pedido.estado = nuevo
        await db.commit()
        return {"status": "ok", "nuevo_estado": nuevo.value}

@app.get("/api/v1/dashboard/hoy")
async def dashboard_hoy(restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        today = datetime.now(timezone.utc).date()
        start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(today, datetime.max.time(), tzinfo=timezone.utc)
        total_ingresos = (await db.scalar(select(func.sum(Pedido.total)).where(Pedido.id_restaurante == restaurante_id, Pedido.created_at.between(start, end)))) or Decimal(0)
        total_pedidos = (await db.scalar(select(func.count(Pedido.id_pedido)).where(Pedido.id_restaurante == restaurante_id, Pedido.created_at.between(start, end)))) or 0
        total_reservas = (await db.scalar(select(func.count(Reservacion.id_reserva)).where(Reservacion.id_restaurante == restaurante_id, Reservacion.fecha_reserva == today))) or 0
        nuevos_clientes = (await db.scalar(select(func.count(Cliente.id_cliente)).where(Cliente.id_restaurante == restaurante_id, Cliente.created_at >= datetime.now(timezone.utc) - timedelta(days=30)))) or 0
        return {"ingresos_hoy": float(total_ingresos), "pedidos_hoy": total_pedidos, "reservas_hoy": total_reservas, "clientes_nuevos_30d": nuevos_clientes, "fecha": today.isoformat()}

@app.get("/api/v1/reservaciones/hoy")
async def reservas_hoy_api(restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        today = datetime.now(timezone.utc).date()
        reservas = (await db.execute(select(Reservacion).where(Reservacion.id_restaurante == restaurante_id, Reservacion.fecha_reserva == today).order_by(Reservacion.hora_reserva))).scalars().all()
        return [{"id": str(r.id_reserva), "codigo_reserva": r.codigo_reserva, "nombre_cliente": None, "num_personas": r.num_personas, "hora_reserva": r.hora_reserva.strftime("%H:%M"), "mesa_asignada": r.mesa_asignada, "zona": r.zona, "estado": r.estado.value} for r in reservas]

# ============================================================
# PANEL HTML
# ============================================================
LOGIN_HTML = textwrap.dedent("""\
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script><title>ISA Panel - Login</title></head>
<body class="bg-gray-100 flex items-center justify-center min-h-screen">
<div class="bg-white p-8 rounded shadow-md w-96"><h1 class="text-2xl font-bold mb-6 text-center">🔐 Panel ISA</h1>
<form action="/panel/login" method="post"><input type="password" name="api_key" placeholder="API Key" class="w-full p-2 border rounded mb-4" required>
<button type="submit" class="w-full bg-blue-600 text-white p-2 rounded hover:bg-blue-700">Ingresar</button></form></div></body></html>""")

RECEPCION_HTML = textwrap.dedent("""\
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script><title>Recepción - ISA</title><script>
async function fetchData(){try{const r=await fetch('/api/v1/reservaciones/hoy').then(r=>r.json());const p=await fetch('/api/v1/pedidos/activos').then(r=>r.json());renderReservas(r);renderPedidos(p);}catch(e){console.error(e);}}
function renderReservas(data){const tbody=document.getElementById('reservas-body');if(!data.length){tbody.innerHTML='<tr><td colspan="7" class="text-center">No hay reservas hoy<html><body>';return;}
tbody.innerHTML=data.map(r=>`<tr><td class="border p-2">${r.codigo_reserva}</td><td class="border p-2">${r.nombre_cliente||''}</td><td class="border p-2">${r.num_personas}</td><td class="border p-2">${r.hora_reserva}</td><td class="border p-2">${r.mesa_asignada||'-'}</td><td class="border p-2">${r.zona||'-'}</td><td class="border p-2">${r.estado}</td></tr>`).join('');}
function renderPedidos(data){const tbody=document.getElementById('pedidos-body');if(!data.length){tbody.innerHTML='</td><td colspan="5" class="text-center">No hay pedidos activos<html><body>';return;}
tbody.innerHTML=data.map(p=>`<tr><td class="border p-2">${p.id.slice(0,8)}</td><td class="border p-2">${p.total} MAD</td><td class="border p-2">${p.estado}</td><td class="border p-2">${new Date(p.created_at).toLocaleTimeString()}</td><td class="border p-2"><button class="bg-blue-500 text-white px-2 py-1 rounded" onclick="cambiarEstado('${p.id}')">Cambiar</button></td></tr>`).join('');}
async function cambiarEstado(id){alert('Función en construcción');}
setInterval(fetchData,30000);fetchData();</script></head>
<body class="bg-gray-100"><div class="container mx-auto p-4"><h1 class="text-3xl font-bold mb-6">📋 Recepción</h1>
<div class="bg-white p-4 rounded shadow mb-8"><h2 class="text-xl font-semibold mb-2">📅 Reservas de hoy</h2><table class="w-full border"><thead><tr><th>Código</th><th>Cliente</th><th>Personas</th><th>Hora</th><th>Mesa</th><th>Zona</th><th>Estado</th></td></thead><tbody id="reservas-body"></tbody></table></div>
<div class="bg-white p-4 rounded shadow"><h2 class="text-xl font-semibold mb-2">🛒 Pedidos activos</h2><table class="w-full border"><thead><tr><th>ID</th><th>Total</th><th>Estado</th><th>Hora</th><th>Acción</th></tr></thead><tbody id="pedidos-body"></tbody></table></div></div></body></html>""")

METRICAS_HTML = textwrap.dedent("""\
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script><title>Métricas - ISA</title><script>
async function loadMetrics(){const res=await fetch('/api/v1/dashboard/hoy');const data=await res.json();document.getElementById('ingresos').innerText=data.ingresos_hoy+' MAD';document.getElementById('pedidos').innerText=data.pedidos_hoy;document.getElementById('reservas').innerText=data.reservas_hoy;document.getElementById('clientes').innerText=data.clientes_nuevos_30d;}
loadMetrics();setInterval(loadMetrics,60000);</script></head>
<body class="bg-gray-100"><div class="container mx-auto p-4"><h1 class="text-3xl font-bold mb-6">📊 Panel de Métricas</h1>
<div class="grid grid-cols-1 md:grid-cols-4 gap-4"><div class="bg-white p-4 rounded shadow"><h3 class="text-lg font-bold">💰 Ingresos hoy</h3><p id="ingresos" class="text-2xl">-</p></div>
<div class="bg-white p-4 rounded shadow"><h3 class="text-lg font-bold">🛒 Pedidos hoy</h3><p id="pedidos" class="text-2xl">-</p></div>
<div class="bg-white p-4 rounded shadow"><h3 class="text-lg font-bold">📅 Reservas hoy</h3><p id="reservas" class="text-2xl">-</p></div>
<div class="bg-white p-4 rounded shadow"><h3 class="text-lg font-bold">👥 Clientes nuevos (30d)</h3><p id="clientes" class="text-2xl">-</p></div></div></div></body></html>""")

@app.get("/panel/login")
def p_login():
    return HTMLResponse(content=LOGIN_HTML)

@app.post("/panel/login")
async def p_login_post(request: Request, api_key: str = Form(...)):
    if not async_session_maker:
        return HTMLResponse(content=LOGIN_HTML + "<p class='text-red-500'>Error de conexión</p>")
    async with async_session_maker() as db:
        ak = (await db.execute(select(ApiKey).where(ApiKey.key_value == api_key, ApiKey.activo.is_(True), (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now_utc())))).scalar_one_or_none()
        if ak:
            request.session["auth"] = "ok"
            request.session["api_key"] = api_key
            return RedirectResponse("/panel/recepcion", status_code=303)
        return HTMLResponse(content=LOGIN_HTML + "<p class='text-red-500'>API Key inválida</p>")

@app.get("/panel/recepcion")
def p_recep(request: Request):
    if request.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(content=RECEPCION_HTML)

@app.get("/panel/metricas")
def p_metricas(request: Request):
    if request.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(content=METRICAS_HTML)

@app.get("/panel/logout")
def p_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/panel/login")

# ============================================================
# WEBHOOKS Y HEALTH
# ============================================================
@app.get("/health")
def health():
    return {"status": "ok", "version": "18.3", "db": "online" if engine else "offline"}

@app.get("/api/whatsapp/webhook")
def wb_verify(req: Request):
    if req.query_params.get("hub.verify_token") == WEBHOOK_VERIFY:
        return int(req.query_params.get("hub.challenge", 0))
    return JSONResponse(content={"status": "forbidden"}, status_code=403)

@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    client_ip = req.headers.get("x-forwarded-for", req.client.host) or "unknown"
    if not check_rate_limit(client_ip.split(",")[0].strip()):
        return JSONResponse(content={"status": "rate_limited"}, status_code=429)
    bg.add_task(process_msg, await req.json())
    return JSONResponse(content={"status": "ok"}, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
