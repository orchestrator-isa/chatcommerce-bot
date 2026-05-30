# -*- coding: utf-8 -*-
# ruff: noqa: E501
"""
ORQUESTRATOR ISA v18.2.8 - FLUJO DE PEDIDO COMPLETO + MEJORAS
- v18.2.8: Transferencia bancaria para clientes validados, validación de zona mejorada.
- Flujo: entrega -> dirección (zona) -> pago -> efectivo/tarjeta/transferencia.
- Idiomas: ES/EN/FR/DAR completos (todas las claves).
- q: Reset atómico (evita MissingGreenlet).
"""

import os
import uuid
import httpx
import logging
import textwrap
import re
import csv
import asyncio
import json
import io
from datetime import datetime, timezone, timedelta, date, time
from enum import Enum
from decimal import Decimal
from typing import Optional, List, Dict
from difflib import SequenceMatcher
from fastapi import (
    FastAPI,
    HTTPException,
    BackgroundTasks,
    Request,
    Form,
    Depends,
    Header,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import (
    Enum as SAEnum,
    String,
    Boolean,
    DECIMAL,
    DateTime,
    Integer,
    Time as SQLTime,
    Date,
    select,
    and_,
    func,
    LargeBinary,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from pydantic import BaseModel


# ============================================================
# CONFIGURACIÓN
# ============================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
    pendiente_confirmacion = "pendiente_confirmacion"


class EstadoReserva(str, Enum):
    pendiente = "pendiente"
    confirmada = "confirmada"
    sentada = "sentada"
    completada = "completada"
    cancelada = "cancelada"
    no_show = "no_show"


class Restaurante(Base):
    __tablename__ = "restaurantes"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nombre: Mapped[str] = mapped_column(String)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id_api_key: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key_value: Mapped[str] = mapped_column(String, unique=True)
    descripcion: Mapped[str] = mapped_column(String, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class RestauranteApiKey(Base):
    __tablename__ = "restaurante_api_keys"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    id_api_key: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )


class RestauranteConfig(Base):
    __tablename__ = "restaurante_config"
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    reservation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_reservation_days_ahead: Mapped[int] = mapped_column(Integer, default=7)
    max_guests_per_reservation: Mapped[int] = mapped_column(Integer, default=10)
    horario_apertura: Mapped[time] = mapped_column(SQLTime, nullable=True)
    horario_cierre: Mapped[time] = mapped_column(SQLTime, nullable=True)
    dias_abierto: Mapped[list[int]] = mapped_column(JSONB, default=list)


class Cliente(Base):
    __tablename__ = "clientes"
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    wa_id: Mapped[str] = mapped_column(String, unique=True)
    telefono: Mapped[str] = mapped_column(String)
    language_pref: Mapped[str] = mapped_column(String, default="es")
    validado: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    contexto_bot: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Menu(Base):
    __tablename__ = "menus"
    id_menu: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Plato(Base):
    __tablename__ = "platos"
    id_plato: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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
    delivery_type: Mapped[str] = mapped_column(String, nullable=True)
    direccion: Mapped[str] = mapped_column(String, nullable=True)
    metodo_pago: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Reservacion(Base):
    __tablename__ = "reservaciones"
    id_reserva: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    codigo_reserva: Mapped[str] = mapped_column(String, unique=True)
    estado: Mapped[EstadoReserva] = mapped_column(
        SAEnum(EstadoReserva, name="estado_reserva", create_type=False),
        default=EstadoReserva.pendiente,
    )
    fecha_reserva: Mapped[date] = mapped_column(Date)
    hora_reserva: Mapped[time] = mapped_column(SQLTime)
    num_personas: Mapped[int] = mapped_column(Integer)
    mesa_asignada: Mapped[str] = mapped_column(String, nullable=True)
    zona: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ReservaHistorial(Base):
    __tablename__ = "reserva_historial"
    id_historial: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_reserva: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado_anterior: Mapped[str] = mapped_column(String(20), nullable=True)
    estado_nuevo: Mapped[str] = mapped_column(String(20))
    cambiado_por: Mapped[str] = mapped_column(String(100), nullable=True)
    notas: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MenuPDF(Base):
    __tablename__ = "menu_pdfs"
    id_pdf: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    nombre_archivo: Mapped[str] = mapped_column(String)
    pdf_data: Mapped[bytes] = mapped_column(LargeBinary)
    mime_type: Mapped[str] = mapped_column(String, default="application/pdf")
    tamano_bytes: Mapped[int] = mapped_column(Integer)
    subido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Mensaje(Base):
    __tablename__ = "mensajes"
    id_mensaje: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_conversacion: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    direccion: Mapped[str] = mapped_column(String)
    tipo: Mapped[str] = mapped_column(String, default="texto")
    contenido: Mapped[str] = mapped_column(String, nullable=True)
    ai_intent: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class PlatoCreate(BaseModel):
    menu_id: uuid.UUID
    precio: float
    disponible: bool = True
    orden: int = 0
    traducciones: Dict[str, str]


class PlatoUpdate(BaseModel):
    precio: Optional[float] = None
    disponible: Optional[bool] = None
    orden: Optional[int] = None
    traducciones: Optional[Dict[str, str]] = None


class Campana(Base):
    __tablename__ = "campanas"
    id_campana: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    nombre: Mapped[str] = mapped_column(String)
    mensaje: Mapped[str] = mapped_column(String)
    filtro: Mapped[dict] = mapped_column(JSONB, default=dict)
    total_destinatarios: Mapped[int] = mapped_column(Integer, default=0)
    enviados: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String, default="borrador")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )


class CampanaDestinatario(Base):
    __tablename__ = "campana_destinatarios"
    id_campana: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(String, nullable=True)


class BroadcastRequest(BaseModel):
    nombre: str
    mensaje: str
    filtro: str = "todos"


# ============================================================
# APP
# ============================================================
app = FastAPI(title="Orquestrator ISA v18.2.8")
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET)


# ============================================================
# HELPERS
# ============================================================
async def send_wa(phone: str, text: str):
    if not WA_PHONE_ID or not WA_TOKEN:
        logger.info(f"[SIM-WA] {phone}: {text}")
        return
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
        logger.error(f"WA error: {e}")


_rate_limits = {}


def check_rate_limit(ip: str, max_req: int = 100, window_sec: int = 60) -> bool:
    now = now_utc()
    if ip not in _rate_limits:
        _rate_limits[ip] = []
    _rate_limits[ip] = [
        t for t in _rate_limits[ip] if (now - t).total_seconds() < window_sec
    ]
    if len(_rate_limits[ip]) >= max_req:
        return False
    _rate_limits[ip].append(now)
    return True


async def get_restaurante_from_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> uuid.UUID:
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        stmt = select(ApiKey).where(
            ApiKey.key_value == x_api_key,
            ApiKey.activo.is_(True),
            (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now_utc()),
        )
        api_key_obj = (await db.execute(stmt)).scalar_one_or_none()
        if not api_key_obj:
            raise HTTPException(401, "API Key inválida o expirada")
        stmt2 = select(RestauranteApiKey.id_restaurante).where(
            RestauranteApiKey.id_api_key == api_key_obj.id_api_key
        )
        restaurante_id = (await db.execute(stmt2)).scalar_one_or_none()
        if not restaurante_id:
            raise HTTPException(403, "API Key no asociada a ningún restaurante")
        return restaurante_id


async def get_restaurante_id_optional(
    request: Request, x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> uuid.UUID:
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


async def guardar_mensaje(
    db: AsyncSession, id_conv: uuid.UUID, direccion: str, contenido: str
):
    try:
        db.add(
            Mensaje(id_conversacion=id_conv, direccion=direccion, contenido=contenido)
        )
        await db.flush()
    except Exception as e:
        logger.error(f"Error guardando mensaje: {e}")


# ============================================================
# ZONAS Y VALIDACIÓN MEJORADA
# ============================================================
ZONA_PERMITIDA = [
    "av. mohamed v",
    "av. mohamed",
    "av. muhamed",
    "av. muhammed",
    "av. mohammed",
    "av. muhammad",
    "mohamed v",
    "muhamed v",
    "muhammed v",
    "mohammed v",
    "muhammad v",
    "calle sevilla",
    "plaza primo",
    "restinga",
    "tetouan",
    "tetuán",
    "av mohamed v",
    "av mohamed",
    "avenida mohamed v",
    "avenida mohamed",
]


def validar_zona(addr: str, umbral: float = 0.60) -> bool:
    addr_lower = addr.lower().strip()
    addr_clean = re.sub(r"[^\w\s]", "", addr_lower)
    addr_clean = re.sub(r"\s+", " ", addr_clean).strip()
    short_addr = addr_clean[:80]
    for zona in ZONA_PERMITIDA:
        zona_clean = zona.lower().replace(".", "").replace(",", "")
        zona_clean = re.sub(r"\s+", " ", zona_clean).strip()
        if zona_clean in short_addr:
            return True
        ratio = SequenceMatcher(None, zona_clean, short_addr).ratio()
        if ratio >= umbral:
            return True
    return False


def format_cart(cart: List[Dict]) -> tuple:
    if not cart:
        return "🛒 Carrito vacío.", 0
    grupos = {}
    for i in cart:
        n = i["nombre"]
        if n not in grupos:
            grupos[n] = {"c": 0, "p": i["precio"]}
        grupos[n]["c"] += 1
    lines = [
        f"{d['c']} * {n} ({d['p']} MAD) = {d['c'] * d['p']:.2f} MAD"
        for n, d in grupos.items()
    ]
    total = sum(d["c"] * d["p"] for d in grupos.values())
    return "\n".join(lines), total


async def calc_tiempo(
    db: AsyncSession, rid: uuid.UUID, tipo: str, n_platos: int
) -> str:
    res = await db.execute(
        select(func.count(Pedido.id_pedido)).where(
            Pedido.id_restaurante == rid,
            Pedido.estado.in_([EstadoPedido.pendiente, EstadoPedido.confirmado]),
        )
    )
    activos = res.scalar() or 0
    base = 10 if tipo == "recoger" else 25
    return f"{base + int(round(n_platos * 0.5)) + (activos * 2)} minutos"


# ============================================================
# IDIOMAS Y TRADUCCIONES
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


# Diccionario I18N completo (solo se incluye una versión resumida para no alargar, pero con todas las claves necesarias)
I18N = {
    "es": {
        "welcome": "🌍 Bienvenido a {restaurante}\nElige tu idioma:\n🇪🇸 s → Español\n🇬🇧 e → English\n🇫🇷 f → Français\n🇲🇦 d → Darija\n\n📄 `menu pdf` para descargar el menú",
        "lang_selected": "✅ Idioma guardado: Español\n\n📋 Comandos:\n`m` → Menú\n`v` → Ver pedido\n`c` → Confirmar\n`x N` → Quitar ítem\n`r` → Reservar\n`q` → Salir\n\nEscribe `m` para ver el menú.",
        "menu_header": "📋 MENÚ (Página {page}/{total_pages})\n",
        "menu_item": "{num}. {nombre} — {precio} MAD",
        "menu_footer": "\n`n` → ➡️ Siguiente\n`a` → ⬅️ Anterior\n🛒 Número para añadir\n📄 `menu pdf` para descargar",
        "added": "✅ {plato} añadido. Total: {total} MAD.",
        "cart": "🛒 PEDIDO\n{items}\n💰 Total: {total} MAD",
        "cart_empty": "🛒 Carrito vacío.",
        "confirm_empty": "⚠️ Carrito vacío.",
        "help": "🤔 Comandos:\n`m` → Menú\n`v` → Ver pedido\n`c` → Confirmar\n`x N` → Quitar ítem N\n`r` → Reservar\n`q` → Salir",
        "delivery_type": "🚚 Tipo de entrega\n1. Recoger en local\n2. Domicilio\n\nResponde con el número:",
        "address_request": "📍 Escribe tu dirección completa:\n(Calle, número, referencia)",
        "invalid_zone": "❌ Solo enviamos a zonas cercanas (Av. Mohamed V, Plaza Primo...).\nElige `1` (Recoger) o reintenta con otra dirección:",
        "payment_method": "💳 Método de pago\n1. Efectivo\n2. Tarjeta (solo recoger)\n\nResponde con el número:",
        "payment_method_with_bank": "💳 Método de pago\n1. Efectivo\n2. Tarjeta (solo recoger)\n3. Transferencia bancaria\n\nResponde con el número:",
        "cash_bill_request": "💰 Pago en efectivo\n¿Con qué billete pagas? (ej: 50, 100, 200)",
        "change_calculated": "💶 Cambio: {cambio} MAD.\n\n✅ Pedido #{numero}\n🚚 {delivery_type}\n💳 Efectivo\n💰 Total: {total} MAD\n⏱️ {tiempo}\nGracias 🙏",
        "card_confirm": "✅ Pedido #{numero}\n🚚 {delivery_type}\n💳 Tarjeta\n💰 Total: {total} MAD\n⏱️ {tiempo}\nGracias 🙏",
        "card_not_available_for_delivery": "❌ Tarjeta solo disponible en local. Elige `1` (Efectivo).",
        "bank_transfer_instructions": "🏦 Transferencia bancaria\nRealiza el pago a la siguiente cuenta:\n\nBanco: XXX\nIBAN: ES00 0000 0000 0000 0000 0000\nConcepto: PEDIDO #{numero}\nImporte: {total} MAD\n\nEnvía el comprobante por este chat. Tu pedido se confirmará manualmente.\n\nGracias.",
        "bank_transfer_pending": "✅ Pedido #{numero} registrado. Pendiente de confirmación de pago.\nTe avisaremos cuando esté confirmado.",
        "bank_transfer_confirmed": "✅ Pago confirmado. Tu pedido #{numero} está en preparación.\n⏱️ {tiempo}\nGracias 🙏",
        "pickup": "Recogida",
        "delivery": "Domicilio",
        "res_personas": "👥 ¿Cuántas personas? (responde un número)",
        "res_fecha": "📅 ¿Qué fecha? (YYYY-MM-DD)",
        "res_hora": "🕐 ¿Qué hora? (HH:MM)",
        "res_confirm": "📋 Reserva\n👥 {personas} personas\n📅 {fecha} 🕐 {hora}\nResponde `si` para confirmar",
        "res_saved": "✅ Reserva guardada! Código: {codigo}",
        "res_cancel": "❌ Reserva cancelada.",
        "res_error_disabled": "❌ Reservas no habilitadas.",
        "res_error_date_range": "❌ Solo hasta {max} días.",
        "res_error_hours": "❌ Cerrado en ese horario.",
        "res_error_capacity": "❌ Máximo {max} personas.",
    },
    "en": {
        "welcome": "🌍 Welcome to {restaurante}\nChoose language:\n🇪🇸 s → Spanish\n🇬🇧 e → English\n🇫🇷 f → French\n🇲🇦 d → Darija\n\n📄 `menu pdf` for menu PDF",
        "lang_selected": "✅ Language saved: English\n\n📋 Commands:\n`m` → Menu\n`v` → View\n`c` → Confirm\n`x N` → Remove\n`r` → Book\n`q` → Exit\n\nType `m` to see menu.",
        "menu_header": "📋 MENU (Page {page}/{total_pages})\n",
        "menu_item": "{num}. {nombre} — {precio} MAD",
        "menu_footer": "\n`n` → ➡️ Next\n`a` → ⬅️ Prev\nReply number to add\n📄 `menu pdf` for PDF",
        "added": "✅ {plato} added. Total: {total} MAD.",
        "cart": "🛒 ORDER\n{items}\n💰 Total: {total} MAD",
        "cart_empty": "🛒 Cart empty.",
        "confirm_empty": "⚠️ Cart empty.",
        "help": "🤔 Commands:\n`m` → Menu\n`v` → View\n`c` → Confirm\n`x N` → Remove item\n`r` → Book\n`q` → Exit",
        "delivery_type": "🚚 Delivery type\n1. Pick up\n2. Home delivery\n\nReply with number:",
        "address_request": "📍 Enter full address:",
        "invalid_zone": "❌ Delivery only to nearby areas. Choose `1` (Pick up) or retry:",
        "payment_method": "💳 Payment\n1. Cash\n2. Card (pick up only)\n\nReply with number:",
        "payment_method_with_bank": "💳 Payment\n1. Cash\n2. Card (pick up only)\n3. Bank transfer\n\nReply with number:",
        "cash_bill_request": "💰 Cash payment\nWhich bill? (e.g., 50, 100, 200)",
        "change_calculated": "💶 Change: {cambio} MAD.\n\n✅ Order #{numero}\n🚚 {delivery_type}\n💳 Cash\n💰 Total: {total} MAD\n⏱️ {tiempo}\nThanks 🙏",
        "card_confirm": "✅ Order #{numero}\n🚚 {delivery_type}\n💳 Card\n💰 Total: {total} MAD\n⏱️ {tiempo}\nThanks 🙏",
        "card_not_available_for_delivery": "❌ Card only available for pick up. Choose `1` (Cash).",
        "bank_transfer_instructions": "🏦 Bank transfer\nMake the payment to the following account:\n\nBank: XXX\nIBAN: ES00 0000 0000 0000 0000 0000\nConcept: ORDER #{numero}\nAmount: {total} MAD\n\nSend the proof via this chat. Your order will be manually confirmed.\n\nThanks.",
        "bank_transfer_pending": "✅ Order #{numero} registered. Waiting for payment confirmation.\nWe will notify you when confirmed.",
        "bank_transfer_confirmed": "✅ Payment confirmed. Your order #{numero} is being prepared.\n⏱️ {tiempo}\nThanks 🙏",
        "pickup": "Pick up",
        "delivery": "Home delivery",
        "res_personas": "👥 How many people? (reply number)",
        "res_fecha": "📅 Date? (YYYY-MM-DD)",
        "res_hora": "🕐 Time? (HH:MM)",
        "res_confirm": "📋 Reservation\n👥 {personas} people\n📅 {fecha} 🕐 {hora}\nReply `yes` to confirm",
        "res_saved": "✅ Reservation saved! Code: {codigo}",
        "res_cancel": "❌ Reservation cancelled.",
        "res_error_disabled": "❌ Reservations not enabled.",
        "res_error_date_range": "❌ Max {max} days.",
        "res_error_hours": "❌ Closed at this time.",
        "res_error_capacity": "❌ Max {max} guests.",
    },
    "fr": {
        "welcome": "🌍 Bienvenue à {restaurante}\nChoisissez votre langue:\n🇪🇸 s → Espagnol\n🇬🇧 e → Anglais\n🇫🇷 f → Français\n🇲🇦 d → Darija\n\n📄 `menu pdf` pour télécharger le menu",
        "lang_selected": "✅ Langue sauvegardée: Français\n\n📋 Commandes:\n`m` → Menu\n`v` → Panier\n`c` → Confirmer\n`r` → Réserver\n`q` → Quitter\n\nTapez `m` pour le menu.",
        "menu_header": "📋 MENU (Page {page}/{total_pages})\n",
        "menu_item": "{num}. {nombre} — {precio} MAD",
        "menu_footer": "\n`n` → ➡️ Suivant\n`a` → ⬅️ Précédent\n🛒 Numéro pour ajouter\n📄 `menu pdf` pour PDF",
        "added": "✅ {plato} ajouté. Total: {total} MAD.",
        "cart": "🛒 COMMANDE\n{items}\n💰 Total: {total} MAD",
        "cart_empty": "🛒 Panier vide.",
        "confirm_empty": "⚠️ Panier vide.",
        "help": "🤔 Commandes:\n`m` → Menu\n`v` → Voir\n`c` → Confirmer\n`x N` → Supprimer\n`r` → Réserver\n`q` → Quitter",
        "delivery_type": "🚚 Type de livraison\n1. À emporter\n2. Livraison à domicile\n\nRépondez par le numéro:",
        "address_request": "📍 Adresse complète:",
        "invalid_zone": "❌ Livraison uniquement dans certaines zones. Choisissez `1` (À emporter) ou réessayez:",
        "payment_method": "💳 Mode de paiement\n1. Espèces\n2. Carte (sur place uniquement)\n\nRépondez:",
        "payment_method_with_bank": "💳 Mode de paiement\n1. Espèces\n2. Carte (sur place uniquement)\n3. Virement bancaire\n\nRépondez:",
        "cash_bill_request": "💰 Paiement en espèces\nQuel billet? (ex: 50, 100, 200)",
        "change_calculated": "💶 Monnaie: {cambio} MAD.\n\n✅ Commande #{numero}\n🚚 {delivery_type}\n💳 Espèces\n💰 Total: {total} MAD\n⏱️ {tiempo}\nMerci 🙏",
        "card_confirm": "✅ Commande #{numero}\n🚚 {delivery_type}\n💳 Carte\n💰 Total: {total} MAD\n⏱️ {tiempo}\nMerci 🙏",
        "card_not_available_for_delivery": "❌ Carte uniquement sur place. Choisissez `1` (Espèces).",
        "bank_transfer_instructions": "🏦 Virement bancaire\nEffectuez le paiement sur le compte suivant:\n\nBanque: XXX\nIBAN: ES00 0000 0000 0000 0000 0000\nConcept: COMMANDE #{numero}\nMontant: {total} MAD\n\nEnvoyez la preuve via ce chat. Votre commande sera confirmée manuellement.\n\nMerci.",
        "bank_transfer_pending": "✅ Commande #{numero} enregistrée. En attente de confirmation de paiement.\nNous vous notifierons lorsqu'elle sera confirmée.",
        "bank_transfer_confirmed": "✅ Paiement confirmé. Votre commande #{numero} est en préparation.\n⏱️ {tiempo}\nMerci 🙏",
        "pickup": "À emporter",
        "delivery": "Domicile",
        "res_personas": "👥 Combien de personnes? (répondez un nombre)",
        "res_fecha": "📅 Date? (YYYY-MM-DD)",
        "res_hora": "🕐 Heure? (HH:MM)",
        "res_confirm": "📋 Réservation\n👥 {personas} personnes\n📅 {fecha} 🕐 {hora}\nRépondez `oui` pour confirmer",
        "res_saved": "✅ Réservation enregistrée! Code: {codigo}",
        "res_cancel": "❌ Réservation annulée.",
        "res_error_disabled": "❌ Réservations non activées.",
        "res_error_date_range": "❌ Jusqu'à {max} jours.",
        "res_error_hours": "❌ Fermé à cette heure.",
        "res_error_capacity": "❌ Maximum {max} personnes.",
    },
    "dar": {
        "welcome": "🌍 Mrahba bik f {restaurante}\nKhtar lougha:\n🇪🇸 s → Espagnol\n🇬🇧 e → Anglais\n🇫🇷 f → Français\n🇲🇦 d → Darija\n\n📄 `menu pdf` bach tchouf lmenu",
        "lang_selected": "✅ Lougha tssajlat: Darija\n\n📋 Comandos:\n`m` → Menu\n`v` → Panier\n`c` → Confirmi\n`r` → Reservi\n`q` → Ħerreb\n\nKteb `m` bach tchouf lmenu.",
        "menu_header": "📋 MENU (Page {page}/{total_pages})\n",
        "menu_item": "{num}. {nombre} — {precio} MAD",
        "menu_footer": "\n`n` → ➡️ Mzyan\n`a` → ⬅️ Lwer\n🛒 Raqem bach tzid\n📄 `menu pdf` bach tchouf PDF",
        "added": "✅ {plato} tzad. Total: {total} MAD.",
        "cart": "🛒 TALAB\n{items}\n💰 Total: {total} MAD",
        "cart_empty": "🛒 Panier khawi.",
        "confirm_empty": "⚠️ Panier khawi.",
        "help": "🤔 Comandos:\n`m` → Menu\n`v` → Chouf\n`c` → Confirmi\n`x N` → Hiyed\n`r` → Reservi\n`q` → Ħerreb",
        "delivery_type": "🚚 Nawa3 d l'livraison\n1. Ħed l'local\n2. Domicile\n\nRépondez b raqem:",
        "address_request": "📍 Kteb l'adresse kamla:",
        "invalid_zone": "❌ Kanţelquw fchi zones (Av. Mohamed V, Plaza Primo...). Khtar `1` (Ħed l'local) aw jawb b adresse okhra:",
        "payment_method": "💳 Tarf d l'paiement\n1. Naqdiya\n2. Carte (ghir f l'local)\n\nRépondez:",
        "payment_method_with_bank": "💳 Tarf d l'paiement\n1. Naqdiya\n2. Carte (ghir f l'local)\n3. Transfert bancaire\n\nRépondez:",
        "cash_bill_request": "💰 Paiement b naqdiya\nB ache flous? (mthal: 50, 100, 200)",
        "change_calculated": "💶 Reste: {cambio} MAD.\n\n✅ Talab #{numero}\n🚚 {delivery_type}\n💳 Naqdiya\n💰 Total: {total} MAD\n⏱️ {tiempo}\nMerci 🙏",
        "card_confirm": "✅ Talab #{numero}\n🚚 {delivery_type}\n💳 Carte\n💰 Total: {total} MAD\n⏱️ {tiempo}\nMerci 🙏",
        "card_not_available_for_delivery": "❌ Carte ghir f l'local. Khtar `1` (Naqdiya).",
        "bank_transfer_instructions": "🏦 Transfert bancaire\nĦawel l flous f l'compte:\n\nBanque: XXX\nIBAN: ES00 0000 0000 0000 0000 0000\nConcept: TALAB #{numero}\nMontant: {total} MAD\n\nBaat l'wassl f had l'chat. Talab ghadi ytettfiq men baad.\n\nMerci.",
        "bank_transfer_pending": "✅ Talab #{numero} tsejjel. Katstanna tettfiq d l'paiement.\nGhandek n3lmouk.",
        "bank_transfer_confirmed": "✅ Paiement tettfiq. Talab #{numero} tayb.\n⏱️ {tiempo}\nMerci 🙏",
        "pickup": "Ħed l'local",
        "delivery": "Domicile",
        "res_personas": "👥 Hal ch7al mn personne? (jawb b raqem)",
        "res_fecha": "📅 Anta tarix? (YYYY-MM-DD)",
        "res_hora": "🕐 Anta sa3a? (HH:MM)",
        "res_confirm": "📋 Réservation\n👥 {personas} personnes\n📅 {fecha} 🕐 {hora}\nJawb `si` bach tettfiq",
        "res_saved": "✅ Réservation tsejjel! Code: {codigo}",
        "res_cancel": "❌ Réservation tlagt.",
        "res_error_disabled": "❌ Réservations makaynch mfe3lin.",
        "res_error_date_range": "❌ Hadi {max} nharat.",
        "res_error_hours": "❌ Restaurant msaker f had l'waqt.",
        "res_error_capacity": "❌ Max {max} personnes.",
    },
}


def t(key: str, lang: str = "es", **kwargs) -> str:
    texts = I18N.get(lang, I18N["es"])
    template = texts.get(key, I18N["es"].get(key, key))
    return template.format(**kwargs)


ITEMS_PER_PAGE = 33


async def get_menu_page(
    db: AsyncSession, restaurante_id: uuid.UUID, lang: str, page: int
):
    menu_query = select(Menu.id_menu).where(
        Menu.id_restaurante == restaurante_id, Menu.activo
    )
    menu_ids = (await db.execute(menu_query)).scalars().all()
    if not menu_ids:
        return [], 0
    platos_query = (
        select(Plato)
        .where(Plato.id_menu.in_(menu_ids), Plato.disponible)
        .order_by(Plato.orden)
    )
    platos = (await db.execute(platos_query)).scalars().all()
    total = len(platos)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_platos = platos[start:end]

    plato_ids = [p.id_plato for p in page_platos]
    trans = {}
    if plato_ids:
        trans_stmt = select(PlatoTraduccion).where(
            and_(
                PlatoTraduccion.id_plato.in_(plato_ids),
                PlatoTraduccion.codigo_idioma == lang,
            )
        )
        trans_result = await db.execute(trans_stmt)
        trans = {tr.id_plato: tr for tr in trans_result.scalars().all()}

    menu_items = []
    for idx, p in enumerate(page_platos, start=1):
        global_num = (page - 1) * ITEMS_PER_PAGE + idx
        nombre = (
            trans[p.id_plato].nombre if p.id_plato in trans else f"Plato {p.id_plato}"
        )
        menu_items.append(
            {
                "num": global_num,
                "id_plato": str(p.id_plato),
                "nombre": nombre,
                "precio": float(p.precio),
            }
        )
    return menu_items, total_pages


# ============================================================
# PDF ENDPOINT
# ============================================================
@app.get("/menu/pdf")
async def get_menu_pdf(restaurante_id: uuid.UUID):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        stmt = select(MenuPDF).where(
            MenuPDF.id_restaurante == restaurante_id, MenuPDF.activo
        )
        pdf_record = (await db.execute(stmt)).scalar_one_or_none()
        if not pdf_record:
            raise HTTPException(404, "PDF no encontrado")
        return Response(
            content=pdf_record.pdf_data,
            media_type=pdf_record.mime_type,
            headers={
                "Content-Disposition": f"attachment; filename={pdf_record.nombre_archivo}"
            },
        )


# ============================================================
# SSE EVENT MANAGER
# ============================================================
class EventManager:
    def __init__(self):
        self.connections: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, client_id: str) -> asyncio.Queue:
        async with self._lock:
            queue = asyncio.Queue()
            self.connections[client_id] = queue
            return queue

    async def unsubscribe(self, client_id: str):
        async with self._lock:
            if client_id in self.connections:
                del self.connections[client_id]

    async def publish(self, event_type: str, data: dict):
        message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        async with self._lock:
            for client_id, queue in list(self.connections.items()):
                try:
                    await queue.put(message)
                except Exception:
                    await self.unsubscribe(client_id)


event_manager = EventManager()


@app.get("/api/v1/events")
async def sse_events(
    request: Request, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    client_id = str(uuid.uuid4())
    queue = await event_manager.subscribe(client_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield message
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            await event_manager.unsubscribe(client_id)

    return StreamingResponse(
        content=event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# BOT: PROCESAMIENTO DE MENSAJES (v18.2.8 - BLOQUE FINAL)
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
                rest_stmt = (
                    select(Restaurante)
                    .where(Restaurante.nombre == "Restinga", Restaurante.activo)
                    .limit(1)
                )
                rest = (await db.execute(rest_stmt)).scalar_one_or_none()
                if not rest:
                    logger.error("No se encontró el restaurante Restinga")
                    return
                rid = rest.id_restaurante
                rname = rest.nombre
                cli = Cliente(
                    id_restaurante=rid,
                    wa_id=phone,
                    telefono=phone,
                    language_pref="es",
                    validado=False,
                )
                db.add(cli)
                await db.flush()
                await db.refresh(cli)
            else:
                rid = cli.id_restaurante
                rname_res = await db.execute(
                    select(Restaurante.nombre).where(Restaurante.id_restaurante == rid)
                )
                rname = rname_res.scalar_one_or_none() or "Restaurante"

            lang = cli.language_pref

            # 2. Conversación
            stmt_conv = (
                select(Conversacion)
                .where(Conversacion.id_cliente == cli.id_cliente)
                .limit(1)
            )
            conv = (await db.execute(stmt_conv)).scalar_one_or_none()

            if not conv:
                conv = Conversacion(
                    id_cliente=cli.id_cliente,
                    id_restaurante=rid,
                    contexto_bot={
                        "fase": "lang",
                        "carrito": [],
                        "menu_page": 1,
                        "current_menu_page_dishes": [],
                    },
                )
                db.add(conv)
                await db.flush()
                await db.refresh(conv)

            if conv:
                await guardar_mensaje(db, conv.id_conversacion, "inbound", txt_raw)

            ctx = dict(conv.contexto_bot) if conv.contexto_bot else {}
            ctx.setdefault("fase", "lang")
            ctx.setdefault("carrito", [])
            ctx.setdefault("menu_page", 1)
            ctx.setdefault("current_menu_page_dishes", [])
            ctx.setdefault("pedido_temp", {})

            logger.info(f"FASE: {ctx['fase']} - Msg: '{txt}' - RestID: {rid}")

            # --- RESET GLOBAL ---
            if txt in ("q", "salir", "quit"):
                try:
                    nuevo_ctx = {
                        "fase": "lang",
                        "carrito": [],
                        "menu_page": 1,
                        "current_menu_page_dishes": [],
                        "pedido_temp": {},
                    }
                    stmt = (
                        update(Conversacion)
                        .where(Conversacion.id_cliente == cli.id_cliente)
                        .values(
                            contexto_bot=clean_serializable(nuevo_ctx),
                            last_message_at=now_utc(),
                        )
                    )
                    await db.execute(stmt)
                    await db.commit()
                except Exception as e:
                    logger.error(f"Error en reset: {e}", exc_info=True)
                    await db.rollback()
                await send_wa(phone, t("welcome", cli.language_pref, restaurante=rname))
                return

            # --- PDF ---
            if txt == "menu pdf":
                await send_wa(
                    phone,
                    f" *Menú Digital*\n➡️ https://chatcommerce-bot.onrender.com/menu/pdf?restaurante_id={rid}\n(Copia el enlace)",
                )
                return

            # --- DETECCIÓN IDIOMA ---
            detected_lang = detectar_idioma_por_keyword(txt)
            if detected_lang:
                ctx["fase"] = "lang"
                conv.contexto_bot = clean_serializable(ctx)
                conv.last_message_at = now_utc()
                await db.commit()
                await send_wa(phone, t("welcome", detected_lang, restaurante=rname))
                return

            # --- FLUJO POR FASE ---
            reply = ""
            fase = ctx["fase"]
            lang_map = {
                "s": "es",
                "es": "es",
                "español": "es",
                "1": "es",
                "e": "en",
                "en": "en",
                "english": "en",
                "2": "en",
                "f": "fr",
                "fr": "fr",
                "français": "fr",
                "3": "fr",
                "d": "dar",
                "dar": "dar",
                "darija": "dar",
                "4": "dar",
            }

            # FASES DE PEDIDO
            if fase == "entrega":
                if txt == "1":
                    ctx["pedido_temp"] = {"tipo": "recoger"}
                    ctx["fase"] = "pago"
                    reply = (
                        t("payment_method_with_bank", lang)
                        if cli.validado
                        else t("payment_method", lang)
                    )
                elif txt == "2":
                    ctx["pedido_temp"] = {"tipo": "domicilio"}
                    ctx["fase"] = "direccion"
                    reply = t("address_request", lang)
                else:
                    reply = t("delivery_type", lang)

            elif fase == "direccion":
                if txt in ("1", "recoger", "pick up", "pickup", "recogida"):
                    ctx["pedido_temp"]["tipo"] = "recoger"
                    ctx["pedido_temp"].pop("dir", None)
                    ctx["fase"] = "pago"
                    reply = (
                        t("payment_method_with_bank", lang)
                        if cli.validado
                        else t("payment_method", lang)
                    )
                elif validar_zona(txt_raw):
                    ctx["pedido_temp"]["dir"] = txt_raw.strip()
                    ctx["fase"] = "pago"
                    reply = (
                        t("payment_method_with_bank", lang)
                        if cli.validado
                        else t("payment_method", lang)
                    )
                else:
                    reply = t("invalid_zone", lang)

            elif fase == "pago":
                tipo = ctx["pedido_temp"].get("tipo", "recoger")
                items = ctx.get("carrito", [])
                if not items:
                    reply = t("confirm_empty", lang)
                    ctx["fase"] = "menu"
                    ctx.pop("pedido_temp", None)
                elif txt == "1":  # Efectivo
                    ctx["pedido_temp"]["pago"] = "efectivo"
                    ctx["fase"] = "cash_bill"
                    reply = t("cash_bill_request", lang)
                elif txt == "2":  # Tarjeta
                    if tipo == "domicilio":
                        reply = t("card_not_available_for_delivery", lang)
                    else:
                        ctx["pedido_temp"]["pago"] = "tarjeta"
                        total = sum(i["precio"] for i in items)
                        tiempo = await calc_tiempo(db, rid, tipo, len(items))
                        ped = Pedido(
                            id_restaurante=rid,
                            id_cliente=cli.id_cliente,
                            items=[
                                {"nombre": i["nombre"], "precio": i["precio"]}
                                for i in items
                            ],
                            total=Decimal(str(total)),
                            delivery_type=tipo,
                            direccion=ctx["pedido_temp"].get("dir"),
                            metodo_pago="tarjeta",
                        )
                        db.add(ped)
                        await db.flush()
                        delivery_label = (
                            t("pickup", lang)
                            if tipo == "recoger"
                            else t("delivery", lang)
                        )
                        reply = t(
                            "card_confirm",
                            lang,
                            numero=str(ped.id_pedido)[-6:].upper(),
                            delivery_type=delivery_label,
                            total=total,
                            tiempo=tiempo,
                        )
                        ctx["carrito"] = []
                        ctx.pop("pedido_temp", None)
                        ctx["fase"] = "menu"
                        conv.contexto_bot = clean_serializable(ctx)
                        conv.last_message_at = now_utc()
                        await db.commit()
                        await event_manager.publish(
                            "nuevo_pedido",
                            {
                                "id": str(ped.id_pedido),
                                "total": float(total),
                                "tipo": "tarjeta",
                                "timestamp": now_utc().isoformat(),
                            },
                        )
                        await guardar_mensaje(
                            db, conv.id_conversacion, "outbound", reply
                        )
                        await send_wa(phone, reply)
                        return
                elif txt == "3" and cli.validado:  # Transferencia bancaria
                    ctx["pedido_temp"]["pago"] = "transferencia"
                    total = sum(i["precio"] for i in items)
                    tipo = ctx["pedido_temp"].get("tipo", "recoger")
                    ped = Pedido(
                        id_restaurante=rid,
                        id_cliente=cli.id_cliente,
                        items=[
                            {"nombre": i["nombre"], "precio": i["precio"]}
                            for i in items
                        ],
                        total=Decimal(str(total)),
                        delivery_type=tipo,
                        direccion=ctx["pedido_temp"].get("dir"),
                        metodo_pago="transferencia",
                        estado=EstadoPedido.pendiente_confirmacion,
                    )
                    db.add(ped)
                    await db.flush()
                    instrucciones = t(
                        "bank_transfer_instructions",
                        lang,
                        numero=str(ped.id_pedido)[-6:].upper(),
                        total=total,
                    )
                    await send_wa(phone, instrucciones)
                    reply = t(
                        "bank_transfer_pending",
                        lang,
                        numero=str(ped.id_pedido)[-6:].upper(),
                    )
                    ctx["carrito"] = []
                    ctx.pop("pedido_temp", None)
                    ctx["fase"] = "menu"
                    conv.contexto_bot = clean_serializable(ctx)
                    conv.last_message_at = now_utc()
                    await db.commit()
                    await event_manager.publish(
                        "nuevo_pedido_pendiente",
                        {
                            "id": str(ped.id_pedido),
                            "total": float(total),
                            "tipo": "transferencia",
                            "timestamp": now_utc().isoformat(),
                        },
                    )
                    await guardar_mensaje(db, conv.id_conversacion, "outbound", reply)
                    await send_wa(phone, reply)
                    return
                else:
                    reply = (
                        t("payment_method_with_bank", lang)
                        if cli.validado
                        else t("payment_method", lang)
                    )

            elif fase == "cash_bill":
                try:
                    billete = int(txt_raw)
                    if billete <= 0:
                        raise ValueError
                    items = ctx.get("carrito", [])
                    if not items:
                        reply = t("confirm_empty", lang)
                        ctx["fase"] = "menu"
                        ctx.pop("pedido_temp", None)
                    else:
                        total = sum(i["precio"] for i in items)
                        if billete < total:
                            reply = f"❌ Insuficiente. Total: {total} MAD. Intenta otro billete."
                        else:
                            cambio = billete - total
                            tipo = ctx["pedido_temp"].get("tipo", "recoger")
                            tiempo = await calc_tiempo(db, rid, tipo, len(items))
                            ped = Pedido(
                                id_restaurante=rid,
                                id_cliente=cli.id_cliente,
                                items=[
                                    {"nombre": i["nombre"], "precio": i["precio"]}
                                    for i in items
                                ],
                                total=Decimal(str(total)),
                                delivery_type=tipo,
                                direccion=ctx["pedido_temp"].get("dir"),
                                metodo_pago="efectivo",
                            )
                            db.add(ped)
                            await db.flush()
                            delivery_label = (
                                t("pickup", lang)
                                if tipo == "recoger"
                                else t("delivery", lang)
                            )
                            reply = t(
                                "change_calculated",
                                lang,
                                cambio=cambio,
                                numero=str(ped.id_pedido)[-6:].upper(),
                                delivery_type=delivery_label,
                                total=total,
                                tiempo=tiempo,
                            )
                            ctx["carrito"] = []
                            ctx.pop("pedido_temp", None)
                            ctx["fase"] = "menu"
                            conv.contexto_bot = clean_serializable(ctx)
                            conv.last_message_at = now_utc()
                            await db.commit()
                            await event_manager.publish(
                                "nuevo_pedido",
                                {
                                    "id": str(ped.id_pedido),
                                    "total": float(total),
                                    "tipo": "efectivo",
                                    "timestamp": now_utc().isoformat(),
                                },
                            )
                            await guardar_mensaje(
                                db, conv.id_conversacion, "outbound", reply
                            )
                            await send_wa(phone, reply)
                            return
                except ValueError:
                    reply = t("cash_bill_request", lang)

            # FLUJO MENÚ
            elif fase == "menu":
                if txt in ("v", "pedido", "view", "order"):
                    cart_text, total = format_cart(ctx.get("carrito", []))
                    reply = (
                        t("cart", lang, items=cart_text, total=total)
                        if cart_text != "🛒 Carrito vacío."
                        else t("cart_empty", lang)
                    )
                elif txt in ("c", "confirm", "confirmar"):
                    if ctx.get("carrito"):
                        ctx["fase"] = "entrega"
                        ctx["pedido_temp"] = {}
                        reply = t("delivery_type", lang)
                    else:
                        reply = t("confirm_empty", lang)
                elif txt in ("m", "menu", "menú"):
                    page = ctx.get("menu_page", 1)
                    menu_items, total_pages = await get_menu_page(db, rid, lang, page)
                    ctx["current_menu_page_dishes"] = menu_items
                    reply = t("menu_header", lang, page=page, total_pages=total_pages)
                    reply += "\n".join(
                        t("menu_item", lang, **it) for it in menu_items
                    ) + t("menu_footer", lang)
                elif txt in ("n", "siguiente", "next", ">", "->"):
                    page = ctx.get("menu_page", 1)
                    _, total_pages = await get_menu_page(db, rid, lang, 1)
                    if page < total_pages:
                        page += 1
                        ctx["menu_page"] = page
                        menu_items, _ = await get_menu_page(db, rid, lang, page)
                        ctx["current_menu_page_dishes"] = menu_items
                        reply = (
                            t("menu_header", lang, page=page, total_pages=total_pages)
                            + "\n".join(t("menu_item", lang, **it) for it in menu_items)
                            + t("menu_footer", lang)
                        )
                    else:
                        reply = "📄 Ya estás en la última página."
                elif txt in ("a", "anterior", "prev", "<", "-<"):
                    page = ctx.get("menu_page", 1)
                    if page > 1:
                        page -= 1
                        ctx["menu_page"] = page
                        menu_items, total_pages = await get_menu_page(
                            db, rid, lang, page
                        )
                        ctx["current_menu_page_dishes"] = menu_items
                        reply = (
                            t("menu_header", lang, page=page, total_pages=total_pages)
                            + "\n".join(t("menu_item", lang, **it) for it in menu_items)
                            + t("menu_footer", lang)
                        )
                    else:
                        reply = "📄 Ya estás en la primera página."
                elif txt.isdigit():
                    num = int(txt)
                    menu_items = ctx.get("current_menu_page_dishes", [])
                    selected = next(
                        (item for item in menu_items if item["num"] == num), None
                    )
                    if selected:
                        carrito = list(ctx.get("carrito", []))
                        carrito.append(
                            {
                                "id": str(selected["id_plato"]),
                                "nombre": selected["nombre"],
                                "precio": selected["precio"],
                            }
                        )
                        ctx["carrito"] = carrito
                        total = sum(item["precio"] for item in carrito)
                        reply = t("added", lang, plato=selected["nombre"], total=total)
                    else:
                        reply = t("help", lang)
                elif " " in txt and txt.split()[0].isdigit() and len(txt.split()) == 2:
                    parts = txt.split()
                    cantidad = int(parts[0])
                    num_plato = int(parts[1])
                    menu_items = ctx.get("current_menu_page_dishes", [])
                    selected = next(
                        (item for item in menu_items if item["num"] == num_plato), None
                    )
                    if selected:
                        carrito = list(ctx.get("carrito", []))
                        for _ in range(cantidad):
                            carrito.append(
                                {
                                    "id": str(selected["id_plato"]),
                                    "nombre": selected["nombre"],
                                    "precio": selected["precio"],
                                }
                            )
                        ctx["carrito"] = carrito
                        total = sum(item["precio"] for item in carrito)
                        reply = t("added", lang, plato=selected["nombre"], total=total)
                    else:
                        reply = t("help", lang)
                elif txt.startswith("x "):
                    parts = txt.split()
                    if len(parts) == 2 and parts[1].isdigit():
                        idx = int(parts[1]) - 1
                        carrito = list(ctx.get("carrito", []))
                        if 0 <= idx < len(carrito):
                            removed = carrito.pop(idx)
                            ctx["carrito"] = carrito
                            total = sum(i["precio"] for i in carrito)
                            reply = (
                                f"❌ Eliminado {removed['nombre']}. Total: {total} MAD"
                            )
                        else:
                            reply = t("help", lang)
                    else:
                        reply = t("help", lang)
                elif txt in ("r", "reservar", "reserve", "book"):
                    config_res = await db.execute(
                        select(RestauranteConfig).where(
                            RestauranteConfig.id_restaurante == rid
                        )
                    )
                    config = config_res.scalar_one_or_none()
                    if not config or not config.reservation_enabled:
                        reply = t("res_error_disabled", lang)
                    else:
                        ctx["reserva_config"] = {
                            "max_days": config.max_reservation_days_ahead,
                            "max_guests": config.max_guests_per_reservation,
                            "open_time": config.horario_apertura.strftime("%H:%M")
                            if config.horario_apertura
                            else "09:00",
                            "close_time": config.horario_cierre.strftime("%H:%M")
                            if config.horario_cierre
                            else "23:00",
                            "dias_abierto": config.dias_abierto,
                        }
                        ctx["fase"] = "res_p"
                        reply = t("res_personas", lang)
                else:
                    reply = t("help", lang)

            # FLUJO RESERVAS
            elif fase == "lang":
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
                    reply = t("lang_selected", lang, restaurante=rname)
                else:
                    reply = t("welcome", lang, restaurante=rname)

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
                    max_days = cfg.get("max_days", 7)
                    if fecha_obj > datetime.now(timezone.utc).date() + timedelta(
                        days=max_days
                    ):
                        reply = t("res_error_date_range", lang).replace(
                            "{max}", str(max_days)
                        )
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
                    open_t = datetime.strptime(
                        cfg.get("open_time", "09:00"), "%H:%M"
                    ).time()
                    close_t = datetime.strptime(
                        cfg.get("close_time", "23:00"), "%H:%M"
                    ).time()
                    hoy_weekday = datetime.now(timezone.utc).weekday()
                    if hoy_weekday not in cfg.get("dias_abierto", list(range(7))):
                        reply = " Hoy el restaurante está cerrado."
                    elif not (open_t <= hora_obj <= close_t):
                        reply = t("res_error_hours", lang)
                    else:
                        ctx["res_hora"] = txt_raw
                        ctx["fase"] = "res_c"
                        reply = t(
                            "res_confirm",
                            lang,
                            personas=ctx.get("res_personas", 1),
                            fecha=ctx.get("res_fecha", " "),
                            hora=txt_raw,
                        )
                except ValueError:
                    reply = t("res_hora", lang)

            elif fase == "res_c":
                cfg = ctx.get("reserva_config", {})
                max_guests = cfg.get("max_guests", 10)
                if ctx.get("res_personas", 1) > max_guests:
                    reply = t("res_error_capacity", lang, max=max_guests)
                    ctx["fase"] = "menu"
                    for k in (
                        "res_personas",
                        "res_fecha",
                        "res_hora",
                        "reserva_config",
                    ):
                        ctx.pop(k, None)
                elif txt in ("si", "yes", "oui", "نعم"):
                    codigo = (
                        f"RES-{now_utc().strftime('%Y%m%d')}-{now_utc().second:02d}"
                    )
                    res = Reservacion(
                        id_restaurante=rid,
                        id_cliente=cli.id_cliente,
                        codigo_reserva=codigo,
                        num_personas=ctx["res_personas"],
                        fecha_reserva=datetime.strptime(
                            ctx["res_fecha"], "%Y-%m-%d"
                        ).date(),
                        hora_reserva=datetime.strptime(ctx["res_hora"], "%H:%M").time(),
                        estado=EstadoReserva.confirmada,
                    )
                    db.add(res)
                    await db.flush()
                    reply = t("res_saved", lang, codigo=codigo)
                    ctx["fase"] = "menu"
                    for k in (
                        "res_personas",
                        "res_fecha",
                        "res_hora",
                        "reserva_config",
                    ):
                        ctx.pop(k, None)
                else:
                    reply = t("res_cancel", lang)
                    ctx["fase"] = "menu"
                    for k in (
                        "res_personas",
                        "res_fecha",
                        "res_hora",
                        "reserva_config",
                    ):
                        ctx.pop(k, None)
            else:
                ctx["fase"] = "lang"
                reply = t("welcome", lang, restaurante=rname)

            # Guardar contexto final y enviar respuesta
            if reply:
                conv.contexto_bot = clean_serializable(ctx)
                conv.last_message_at = now_utc()
                try:
                    await db.commit()
                except Exception as e:
                    logger.error(f"❌ Error commit final: {e}", exc_info=True)
                    await db.rollback()
                await guardar_mensaje(db, conv.id_conversacion, "outbound", reply)
                await send_wa(phone, reply)

    except Exception as e:
        logger.error(f"Webhook error (outer): {e}", exc_info=True)


# ============================================================
# ENDPOINTS STAFF (incluyendo conversaciones)
# ============================================================
@app.get("/api/v1/conversaciones")
async def listar_conversaciones(
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    # ... [resto del código de los endpoints] ...

    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        stmt = (
            select(Conversacion, Cliente.wa_id)
            .join(Cliente, Conversacion.id_cliente == Cliente.id_cliente)
            .where(Conversacion.id_restaurante == restaurante_id)
            .order_by(Conversacion.last_message_at.desc())
        )
        results = (await db.execute(stmt)).all()
        return [
            {
                "id": str(c.id_conversacion),
                "wa_id": wa_id,
                "fase": c.contexto_bot.get("fase", "lang"),
                "last_message_at": c.last_message_at.isoformat()
                if c.last_message_at
                else None,
            }
            for c, wa_id in results
        ]


@app.get("/api/v1/conversaciones/{id_conv}/mensajes")
async def obtener_mensajes(
    id_conv: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        valid = await db.execute(
            select(Conversacion.id_conversacion).where(
                Conversacion.id_conversacion == id_conv,
                Conversacion.id_restaurante == restaurante_id,
            )
        )
        if not valid.scalar_one_or_none():
            raise HTTPException(404, "Conversación no encontrada")
        stmt = (
            select(Mensaje)
            .where(Mensaje.id_conversacion == id_conv)
            .order_by(Mensaje.created_at.asc())
        )
        mensajes = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": str(m.id_mensaje),
                "direccion": m.direccion,
                "contenido": m.contenido,
                "created_at": m.created_at.isoformat(),
            }
            for m in mensajes
        ]


@app.patch("/api/v1/reservaciones/{id}/confirmar")
async def confirmar_reserva(
    id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Reservacion).where(
                Reservacion.id_reserva == id,
                Reservacion.id_restaurante == restaurante_id,
            )
        )
        reserva = res.scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if reserva.estado != EstadoReserva.pendiente:
            raise HTTPException(400, "Solo se puede confirmar una reserva pendiente")
        reserva.estado = EstadoReserva.confirmada
        await db.commit()
        return {"status": "ok", "nuevo_estado": reserva.estado.value}


@app.patch("/api/v1/reservaciones/{id}/cancelar")
async def cancelar_reserva(
    id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Reservacion).where(
                Reservacion.id_reserva == id,
                Reservacion.id_restaurante == restaurante_id,
            )
        )
        reserva = res.scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if reserva.estado in (EstadoReserva.cancelada, EstadoReserva.completada):
            raise HTTPException(400, "La reserva ya está cancelada o completada")
        reserva.estado = EstadoReserva.cancelada
        await db.commit()
        return {"status": "ok", "nuevo_estado": reserva.estado.value}


@app.patch("/api/v1/reservaciones/{id}/asignar-mesa")
async def asignar_mesa_reserva(
    id: uuid.UUID,
    request: Request,
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
    mesa: str = Form(...),
    zona: str = Form(None),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Reservacion).where(
                Reservacion.id_reserva == id,
                Reservacion.id_restaurante == restaurante_id,
            )
        )
        reserva = res.scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if reserva.estado not in (EstadoReserva.pendiente, EstadoReserva.confirmada):
            raise HTTPException(400, "No se puede asignar mesa en este estado")
        reserva.mesa_asignada = mesa
        reserva.zona = zona
        await db.commit()
        return {"status": "ok", "mesa": mesa, "zona": zona}


@app.patch("/api/v1/reservaciones/{id}/marcar-sentada")
async def marcar_sentada(
    id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Reservacion).where(
                Reservacion.id_reserva == id,
                Reservacion.id_restaurante == restaurante_id,
            )
        )
        reserva = res.scalar_one_or_none()
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada")
        if not reserva.mesa_asignada:
            raise HTTPException(400, "Primero asigna una mesa")
        if reserva.estado != EstadoReserva.confirmada:
            raise HTTPException(
                400, "Solo se puede marcar sentada una reserva confirmada"
            )
        reserva.estado = EstadoReserva.sentada
        await db.commit()
        return {"status": "ok", "nuevo_estado": reserva.estado.value}


@app.get("/api/v1/pedidos/activos")
async def pedidos_activos(
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Pedido)
            .where(
                Pedido.id_restaurante == restaurante_id,
                Pedido.estado.in_([EstadoPedido.pendiente, EstadoPedido.confirmado]),
            )
            .order_by(Pedido.created_at.desc())
        )
        pedidos = res.scalars().all()
        return [
            {
                "id": str(p.id_pedido),
                "cliente": str(p.id_cliente),
                "total": float(p.total),
                "estado": p.estado.value,
                "delivery_type": p.delivery_type,
                "metodo_pago": p.metodo_pago,
                "direccion": p.direccion,
                "created_at": p.created_at.isoformat(),
            }
            for p in pedidos
        ]


@app.get("/api/v1/pedidos/pendientes")
async def pedidos_pendientes(
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Pedido)
            .where(
                Pedido.id_restaurante == restaurante_id,
                Pedido.estado == EstadoPedido.pendiente_confirmacion,
            )
            .order_by(Pedido.created_at.desc())
        )
        pedidos = res.scalars().all()
        return [
            {
                "id": str(p.id_pedido),
                "cliente": str(p.id_cliente),
                "total": float(p.total),
                "estado": p.estado.value,
                "delivery_type": p.delivery_type,
                "metodo_pago": p.metodo_pago,
                "direccion": p.direccion,
                "created_at": p.created_at.isoformat(),
            }
            for p in pedidos
        ]


@app.patch("/api/v1/pedidos/{id}/confirmar-transferencia")
async def confirmar_transferencia(
    id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Pedido).where(
                Pedido.id_pedido == id, Pedido.id_restaurante == restaurante_id
            )
        )
        pedido = res.scalar_one_or_none()
        if not pedido:
            raise HTTPException(404, "Pedido no encontrado")
        if pedido.estado != EstadoPedido.pendiente_confirmacion:
            raise HTTPException(
                400, "Solo se pueden confirmar pedidos pendientes de confirmación"
            )
        pedido.estado = EstadoPedido.confirmado
        res_cli = await db.execute(
            select(Cliente).where(Cliente.id_cliente == pedido.id_cliente)
        )
        cliente = res_cli.scalar_one()
        lang = cliente.language_pref
        tiempo = await calc_tiempo(
            db, restaurante_id, pedido.delivery_type, len(pedido.items)
        )
        delivery_label = (
            t("pickup", lang)
            if pedido.delivery_type == "recoger"
            else t("delivery", lang)
        )
        mensaje = t(
            "bank_transfer_confirmed",
            lang,
            numero=str(pedido.id_pedido)[-6:].upper(),
            total=float(pedido.total),
            delivery_type=delivery_label,
            tiempo=tiempo,
        )
        await db.commit()
        await send_wa(cliente.wa_id, mensaje)
        return {"status": "ok", "nuevo_estado": pedido.estado.value}


@app.patch("/api/v1/pedidos/{id}/estado")
async def cambiar_estado_pedido(
    id: uuid.UUID,
    nuevo_estado: str,
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Pedido).where(
                Pedido.id_pedido == id, Pedido.id_restaurante == restaurante_id
            )
        )
        pedido = res.scalar_one_or_none()
        if not pedido:
            raise HTTPException(404, "Pedido no encontrado")
        try:
            nuevo = EstadoPedido(nuevo_estado)
        except ValueError:
            raise HTTPException(400, "Estado inválido")
        pedido.estado = nuevo
        await db.commit()
        return {"status": "ok", "nuevo_estado": nuevo.value}


@app.get("/api/v1/menus")
async def list_menus(restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)):
    async with async_session_maker() as db:
        res = await db.execute(
            select(Menu).where(Menu.id_restaurante == restaurante_id, Menu.activo)
        )
        menus = res.scalars().all()
        return [{"id": str(m.id_menu), "nombre": "Menú principal"} for m in menus]


@app.get("/api/v1/platos")
async def list_platos(
    menu_id: uuid.UUID,
    lang: str = "es",
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    async with async_session_maker() as db:
        res = await db.execute(
            select(Plato)
            .where(Plato.id_menu == menu_id, Plato.disponible)
            .order_by(Plato.orden)
        )
        platos = res.scalars().all()
        trans = {}
        if platos:
            trans_res = await db.execute(
                select(PlatoTraduccion).where(
                    and_(
                        PlatoTraduccion.id_plato.in_([p.id_plato for p in platos]),
                        PlatoTraduccion.codigo_idioma == lang,
                    )
                )
            )
            trans = {tr.id_plato: tr.nombre for tr in trans_res.scalars().all()}
        return [
            {
                "id": str(p.id_plato),
                "nombre": trans.get(p.id_plato, f"Plato {p.id_plato}"),
                "precio": float(p.precio),
                "disponible": p.disponible,
                "orden": p.orden,
            }
            for p in platos
        ]


@app.post("/api/v1/platos")
async def create_plato(
    data: PlatoCreate, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    async with async_session_maker() as db:
        res = await db.execute(
            select(Menu).where(
                Menu.id_menu == data.menu_id, Menu.id_restaurante == restaurante_id
            )
        )
        menu = res.scalar_one_or_none()
        if not menu:
            raise HTTPException(404, "Menú no encontrado o no pertenece al restaurante")
        plato = Plato(
            id_menu=data.menu_id,
            precio=Decimal(str(data.precio)),
            disponible=data.disponible,
            orden=data.orden,
        )
        db.add(plato)
        await db.flush()
        for lang, nombre in data.traducciones.items():
            db.add(
                PlatoTraduccion(
                    id_plato=plato.id_plato, codigo_idioma=lang, nombre=nombre
                )
            )
        await db.commit()
        return {"id": str(plato.id_plato), "mensaje": "Plato creado"}


@app.patch("/api/v1/platos/{id}")
async def update_plato(
    id: uuid.UUID,
    data: PlatoUpdate,
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    async with async_session_maker() as db:
        res = await db.execute(
            select(Plato).where(
                Plato.id_plato == id,
                Plato.id_menu == Menu.id_menu,
                Menu.id_restaurante == restaurante_id,
            )
        )
        plato = res.scalar_one_or_none()
        if not plato:
            raise HTTPException(404, "Plato no encontrado")
        if data.precio is not None:
            plato.precio = Decimal(str(data.precio))
        if data.disponible is not None:
            plato.disponible = data.disponible
        if data.orden is not None:
            plato.orden = data.orden
        if data.traducciones:
            for lang, nombre in data.traducciones.items():
                trans_res = await db.execute(
                    select(PlatoTraduccion).where(
                        PlatoTraduccion.id_plato == id,
                        PlatoTraduccion.codigo_idioma == lang,
                    )
                )
                trans = trans_res.scalar_one_or_none()
                if trans:
                    trans.nombre = nombre
                else:
                    db.add(
                        PlatoTraduccion(id_plato=id, codigo_idioma=lang, nombre=nombre)
                    )
        await db.commit()
        return {"status": "ok"}


@app.delete("/api/v1/platos/{id}")
async def delete_plato(
    id: uuid.UUID, restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional)
):
    async with async_session_maker() as db:
        res = await db.execute(
            select(Plato).where(
                Plato.id_plato == id,
                Plato.id_menu == Menu.id_menu,
                Menu.id_restaurante == restaurante_id,
            )
        )
        plato = res.scalar_one_or_none()
        if not plato:
            raise HTTPException(404, "Plato no encontrado")
        plato.disponible = False
        await db.commit()
        return {"status": "ok"}


@app.get("/api/v1/export/clientes")
async def export_clientes_csv(
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        subq_pedidos = (
            select(
                Pedido.id_cliente,
                func.count(Pedido.id_pedido).label("total_pedidos"),
                func.sum(Pedido.total).label("total_gastado"),
            )
            .where(Pedido.id_restaurante == restaurante_id)
            .group_by(Pedido.id_cliente)
            .subquery()
        )
        stmt = (
            select(
                Cliente.id_cliente,
                Cliente.wa_id,
                Cliente.telefono,
                Cliente.language_pref,
                Cliente.validado,
                Cliente.created_at,
                func.coalesce(subq_pedidos.c.total_pedidos, 0).label("total_pedidos"),
                func.coalesce(subq_pedidos.c.total_gastado, 0).label("total_gastado"),
            )
            .outerjoin(subq_pedidos, Cliente.id_cliente == subq_pedidos.c.id_cliente)
            .where(Cliente.id_restaurante == restaurante_id)
            .order_by(Cliente.created_at.desc())
        )
        clientes = (await db.execute(stmt)).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id_cliente",
                "wa_id",
                "telefono",
                "language_pref",
                "validado",
                "created_at",
                "total_pedidos",
                "total_gastado (MAD)",
            ]
        )
        for c in clientes:
            writer.writerow(
                [
                    str(c.id_cliente),
                    c.wa_id,
                    c.telefono,
                    c.language_pref,
                    "Sí" if c.validado else "No",
                    c.created_at.isoformat(),
                    c.total_pedidos,
                    float(c.total_gastado),
                ]
            )
        output.seek(0)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=clientes.csv"},
        )


@app.get("/api/v1/export/pedidos")
async def export_pedidos_csv(
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        res = await db.execute(
            select(Pedido)
            .where(Pedido.id_restaurante == restaurante_id)
            .order_by(Pedido.created_at.desc())
        )
        pedidos = res.scalars().all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id_pedido",
                "id_cliente",
                "estado",
                "delivery_type",
                "metodo_pago",
                "direccion",
                "total",
                "items",
                "created_at",
            ]
        )
        for p in pedidos:
            writer.writerow(
                [
                    str(p.id_pedido),
                    str(p.id_cliente),
                    p.estado.value,
                    p.delivery_type or "",
                    p.metodo_pago or "",
                    p.direccion or "",
                    float(p.total),
                    str(p.items),
                    p.created_at.isoformat(),
                ]
            )
        output.seek(0)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=pedidos.csv"},
        )


@app.post("/api/v1/broadcast")
async def crear_campana(
    req: BroadcastRequest,
    bg: BackgroundTasks,
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if len(req.mensaje) > 1600:
        raise HTTPException(400, "Mensaje demasiado largo (máx 1600 caracteres)")
    async with async_session_maker() as db:
        q = select(Cliente).where(Cliente.id_restaurante == restaurante_id)
        if req.filtro == "activos_30d":
            q = q.where(Cliente.created_at >= now_utc() - timedelta(days=30))
        elif req.filtro == "inactivos_60d":
            q = q.where(Cliente.created_at < now_utc() - timedelta(days=60))
        clientes = (await db.execute(q)).scalars().all()
        if not clientes:
            raise HTTPException(400, "No hay clientes para ese filtro")
        campana = Campana(
            id_restaurante=restaurante_id,
            nombre=req.nombre,
            mensaje=req.mensaje,
            filtro={"tipo": req.filtro},
            total_destinatarios=len(clientes),
            estado="enviando",
        )
        db.add(campana)
        await db.flush()
        for c in clientes:
            db.add(
                CampanaDestinatario(
                    id_campana=campana.id_campana, id_cliente=c.id_cliente
                )
            )
        await db.commit()
        bg.add_task(
            _enviar_campana,
            campana.id_campana,
            restaurante_id,
            req.mensaje,
            [c.wa_id for c in clientes],
        )
        return {
            "campana_id": str(campana.id_campana),
            "total": len(clientes),
            "estado": "enviando",
        }


async def _enviar_campana(
    campana_id: uuid.UUID, restaurante_id: uuid.UUID, mensaje: str, wa_ids: List[str]
):
    enviados = 0
    for i, wa_id in enumerate(wa_ids):
        try:
            await send_wa(wa_id, mensaje)
            enviados += 1
        except Exception as e:
            logger.error(f"Error enviando a {wa_id}: {e}")
        if (i + 1) % 10 == 0:
            await asyncio.sleep(0.1)
    async with async_session_maker() as db:
        await db.execute(
            update(Campana)
            .where(Campana.id_campana == campana_id)
            .values(enviados=enviados, estado="completada")
        )
        await db.commit()


@app.get("/api/v1/dashboard/hoy")
async def dashboard_hoy(
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        today = datetime.now(timezone.utc).date()
        start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(today, datetime.max.time(), tzinfo=timezone.utc)
        total_ingresos = (
            await db.scalar(
                select(func.sum(Pedido.total)).where(
                    Pedido.id_restaurante == restaurante_id,
                    Pedido.created_at.between(start, end),
                    Pedido.estado.in_(
                        [EstadoPedido.confirmado, EstadoPedido.entregado]
                    ),
                )
            )
        ) or Decimal(0)
        total_pedidos = (
            await db.scalar(
                select(func.count(Pedido.id_pedido)).where(
                    Pedido.id_restaurante == restaurante_id,
                    Pedido.created_at.between(start, end),
                )
            )
        ) or 0
        total_reservas = (
            await db.scalar(
                select(func.count(Reservacion.id_reserva)).where(
                    Reservacion.id_restaurante == restaurante_id,
                    Reservacion.fecha_reserva == today,
                )
            )
        ) or 0
        nuevos_clientes = (
            await db.scalar(
                select(func.count(Cliente.id_cliente)).where(
                    Cliente.id_restaurante == restaurante_id,
                    Cliente.created_at
                    >= datetime.now(timezone.utc) - timedelta(days=30),
                )
            )
        ) or 0
        pendientes = (
            await db.scalar(
                select(func.count(Pedido.id_pedido)).where(
                    Pedido.id_restaurante == restaurante_id,
                    Pedido.estado == EstadoPedido.pendiente_confirmacion,
                )
            )
        ) or 0
        return {
            "ingresos_hoy": float(total_ingresos),
            "pedidos_hoy": total_pedidos,
            "reservas_hoy": total_reservas,
            "clientes_nuevos_30d": nuevos_clientes,
            "pendientes_confirmacion": pendientes,
            "fecha": today.isoformat(),
        }


@app.get("/api/v1/reservaciones/hoy")
async def reservas_hoy_api(
    restaurante_id: uuid.UUID = Depends(get_restaurante_id_optional),
):
    if not async_session_maker:
        raise HTTPException(503, "DB offline")
    async with async_session_maker() as db:
        today = datetime.now(timezone.utc).date()
        res = await db.execute(
            select(Reservacion)
            .where(
                Reservacion.id_restaurante == restaurante_id,
                Reservacion.fecha_reserva == today,
            )
            .order_by(Reservacion.hora_reserva)
        )
        reservas = res.scalars().all()
        return [
            {
                "id": str(r.id_reserva),
                "codigo_reserva": r.codigo_reserva,
                "nombre_cliente": None,
                "num_personas": r.num_personas,
                "hora_reserva": r.hora_reserva.strftime("%H:%M"),
                "mesa_asignada": r.mesa_asignada,
                "zona": r.zona,
                "estado": r.estado.value,
            }
            for r in reservas
        ]


# ============================================================
# PANEL HTML (todas las variables definidas en orden)
# ============================================================
# ============================================================
# VARIABLES HTML FALTANTES (Login, Menú y Campañas)
# ============================================================
LOGIN_HTML = textwrap.dedent("""\
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script><title>ISA Panel - Login</title></head>
<body class="bg-gray-100 flex items-center justify-center min-h-screen">
<div class="bg-white p-8 rounded shadow-md w-96"><h1 class="text-2xl font-bold mb-6 text-center">🔐 Panel ISA</h1>
<form action="/panel/login" method="post"><input type="password" name="api_key" placeholder="API Key" class="w-full p-2 border rounded mb-4" required>
<button type="submit" class="w-full bg-blue-600 text-white p-2 rounded hover:bg-blue-700">Ingresar</button></form></div></body></html>""")

MENU_HTML = textwrap.dedent("""\
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script><title>Gestión de Menú - ISA</title></head>
<body class="bg-gray-100 p-4"><div class="container mx-auto"><h1 class="text-2xl font-bold mb-4">🍽️ Gestión de Platos</h1>
<p class="text-gray-600 mb-4">Panel en construcción. Usa la API para gestionar platos.</p>
<a href="/panel/recepcion" class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">← Volver al Panel</a></div></body></html>""")

BROADCAST_HTML = textwrap.dedent("""\
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script><title>Campañas - ISA</title></head>
<body class="bg-gray-100 p-4"><div class="container mx-auto"><h1 class="text-2xl font-bold mb-4">📢 Campañas Masivas</h1>
<p class="text-gray-600 mb-4">Envía mensajes a grupos de clientes usando la API (<code>POST /api/v1/broadcast</code>).</p>
<a href="/panel/recepcion" class="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">← Volver al Panel</a></div></body></html>""")
# ============================================================
# RUTAS DEL PANEL
# ============================================================


@app.get("/panel/menu")
def p_menu(request: Request):
    if request.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(content=MENU_HTML)


@app.get("/panel/broadcast")
def p_broadcast(request: Request):
    if request.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(content=BROADCAST_HTML)


@app.get("/panel/login")
def p_login():
    return HTMLResponse(content=LOGIN_HTML)


@app.post("/panel/login")
async def p_login_post(request: Request, api_key: str = Form(...)):
    if not async_session_maker:
        return HTMLResponse(
            content=LOGIN_HTML + "<p class='text-red-500'>Error de conexión</p>"
        )
    async with async_session_maker() as db:
        res = await db.execute(
            select(ApiKey).where(
                ApiKey.key_value == api_key,
                ApiKey.activo.is_(True),
                (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now_utc()),
            )
        )
        ak = res.scalar_one_or_none()
        if ak:
            request.session["auth"] = "ok"
            request.session["api_key"] = api_key
            return RedirectResponse("/panel/recepcion", status_code=303)
        return HTMLResponse(
            content=LOGIN_HTML + "<p class='text-red-500'>API Key inválida</p>"
        )


# ============================================================
# PANEL HTML (Definición requerida antes de las rutas)
# ============================================================
PANEL_HTML = textwrap.dedent("""\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script>
<title>Panel ISA - Restinga</title>
<style>
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .tab-btn.active { border-bottom: 2px solid #EAB308; color: #EAB308; }
</style>
<script>
function showTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(tabId).classList.add('active');
  document.querySelector(`[data-tab="${tabId}"]`).classList.add('active');
  if(tabId === 'recepcion') loadRecepcion();
  if(tabId === 'metricas') loadMetricas();
  if(tabId === 'chats') loadChatList();
}

async function loadRecepcion() {
  try {
    const [r, p] = await Promise.all([
      fetch('/api/v1/reservaciones/hoy').then(r => r.json()),
      fetch('/api/v1/pedidos/activos').then(r => r.json())
    ]);
    const tbRes = document.getElementById('reservas-tb');
    tbRes.innerHTML = r.length ? r.map(x => `<tr>
      <td class="p-2 border border-gray-600">${x.codigo_reserva}</td>
      <td class="p-2 border border-gray-600">${x.num_personas}</td>
      <td class="p-2 border border-gray-600">${x.hora_reserva}</td>
      <td class="p-2 border border-gray-600">${x.mesa_asignada || '-'}</td>
      <td class="p-2 border border-gray-600"><span class="px-2 py-1 rounded text-xs ${x.estado=='pendiente'?'bg-yellow-600 text-black':'bg-green-600 text-black'}">${x.estado}</span></td>
      <td class="p-2 border border-gray-600">${x.estado=='pendiente' ? `<button onclick="confirmarReserva('${x.id}')" class="bg-blue-500 hover:bg-blue-600 text-white px-2 py-1 rounded text-sm">✓ Confirmar</button>` : '-'}</td>
    </tr>`).join('') : '<tr><td colspan="6" class="p-4 text-center text-gray-400">Sin reservas hoy</td></tr>';

    const tbPed = document.getElementById('pedidos-tb');
    tbPed.innerHTML = p.length ? p.map(x => `<tr>
      <td class="p-2 border border-gray-600">${x.id.slice(0,8)}</td>
      <td class="p-2 border border-gray-600">${x.total} MAD</td>
      <td class="p-2 border border-gray-600">${x.delivery_type||'-'}</td>
      <td class="p-2 border border-gray-600"><span class="px-2 py-1 rounded text-xs bg-blue-600 text-black">${x.estado}</span></td>
    </tr>`).join('') : '<tr><td colspan="4" class="p-4 text-center text-gray-400">Sin pedidos activos</td></tr>';
  } catch(e) { console.error('Error recepción:', e); }
}

async function confirmarReserva(id) {
  if(!confirm('¿Confirmar esta reserva?')) return;
  try {
    const res = await fetch(`/api/v1/reservaciones/${id}/confirmar`, { method: 'PATCH' });
    if(res.ok) {
      alert('✅ Reserva confirmada');
      loadRecepcion();
    } else {
      const err = await res.json().catch(() => 'Error desconocido');
      alert(`❌ Error: ${err.detail || res.statusText}`);
    }
  } catch(e) { alert('❌ Error de red o servidor'); }
}

async function loadMetricas() {
  try {
    const d = await fetch('/api/v1/dashboard/hoy').then(r => r.json());
    document.getElementById('metricas-data').innerHTML = `
      <div class="bg-gray-700 p-4 rounded-lg text-center"><h3 class="text-2xl font-bold text-yellow-400">${d.ingresos_hoy} MAD</h3><p class="text-gray-400">Ingresos hoy</p></div>
      <div class="bg-gray-700 p-4 rounded-lg text-center"><h3 class="text-2xl font-bold text-blue-400">${d.pedidos_hoy}</h3><p class="text-gray-400">Pedidos hoy</p></div>
      <div class="bg-gray-700 p-4 rounded-lg text-center"><h3 class="text-2xl font-bold text-green-400">${d.reservas_hoy}</h3><p class="text-gray-400">Reservas hoy</p></div>
      <div class="bg-gray-700 p-4 rounded-lg text-center"><h3 class="text-2xl font-bold text-purple-400">${d.clientes_nuevos_30d}</h3><p class="text-gray-400">Clientes nuevos (30d)</p></div>
    `;
  } catch(e) { console.error('Error métricas:', e); }
}

async function loadChatList() {
  try {
    const list = await fetch('/api/v1/conversaciones').then(r => r.json());
    const container = document.getElementById('chat-list');
    container.innerHTML = list.length ? list.map(c => `
      <div onclick="openChat('${c.id}','${c.wa_id}')" class="p-3 hover:bg-gray-600 cursor-pointer rounded mb-2 transition">
        <div class="font-bold text-sm">📱 ${c.wa_id}</div>
        <div class="text-xs text-gray-400">Fase: ${c.fase} | ${new Date(c.last_message_at).toLocaleTimeString()}</div>
      </div>
    `).join('') : '<div class="text-gray-400 text-center py-4">Sin conversaciones</div>';
  } catch(e) { console.error('Error chats:', e); }
}

async function openChat(id, wa) {
  document.getElementById('chat-header').innerText = `💬 Chat con ${wa}`;
  try {
    const msgs = await fetch(`/api/v1/conversaciones/${id}/mensajes`).then(r => r.json());
    const container = document.getElementById('chat-messages');
    container.innerHTML = msgs.map(m => `
      <div class="flex ${m.direccion==='inbound'?'justify-start':'justify-end'} mb-2">
        <div class="max-w-[80%] p-3 rounded-lg text-sm ${m.direccion==='inbound'?'bg-gray-600 text-white':'bg-yellow-600 text-black'}">
          ${m.contenido || '<i class="text-gray-300">[Sin contenido]</i>'}
          <div class="text-[10px] text-right mt-1 opacity-70">${new Date(m.created_at).toLocaleTimeString()}</div>
        </div>
      </div>
    `).join('');
    container.scrollTop = container.scrollHeight;
  } catch(e) { console.error('Error mensajes:', e); }
}

document.addEventListener('DOMContentLoaded', () => {
  loadRecepcion();
  setInterval(() => { 
    if(document.getElementById('recepcion').classList.contains('active')) loadRecepcion(); 
  }, 15000);
});
</script>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen p-4">
<div class="max-w-7xl mx-auto bg-gray-800 rounded-lg shadow-xl overflow-hidden">
  <nav class="flex border-b border-gray-700">
    <button data-tab="recepcion" class="tab-btn active px-6 py-3 text-sm font-medium hover:text-yellow-400 transition">📅 Recepción</button>
    <button data-tab="metricas" class="tab-btn px-6 py-3 text-sm font-medium hover:text-yellow-400 transition">📊 Métricas</button>
    <button data-tab="chats" class="tab-btn px-6 py-3 text-sm font-medium hover:text-yellow-400 transition">💬 Chats</button>
    <button data-tab="campanas" class="tab-btn px-6 py-3 text-sm font-medium hover:text-yellow-400 transition">📢 Campañas</button>
    <a href="/panel/logout" class="px-6 py-3 text-sm font-medium hover:text-red-400 transition ml-auto">🚪 Salir</a>
  </nav>

  <div id="recepcion" class="tab-content active p-6">
    <h2 class="text-xl font-bold mb-4">📅 Reservas de hoy</h2>
    <table class="w-full text-left border-collapse"><thead class="bg-gray-700"><tr><th class="p-2 border border-gray-600">Código</th><th class="p-2 border border-gray-600">Personas</th><th class="p-2 border border-gray-600">Hora</th><th class="p-2 border border-gray-600">Mesa</th><th class="p-2 border border-gray-600">Estado</th><th class="p-2 border border-gray-600">Acciones</th></tr></thead><tbody id="reservas-tb"></tbody></table>
    <h2 class="text-xl font-bold mb-4 mt-8">🛒 Pedidos Activos</h2>
    <table class="w-full text-left border-collapse"><thead class="bg-gray-700"><tr><th class="p-2 border border-gray-600">ID</th><th class="p-2 border border-gray-600">Total</th><th class="p-2 border border-gray-600">Tipo</th><th class="p-2 border border-gray-600">Estado</th></tr></thead><tbody id="pedidos-tb"></tbody></table>
  </div>

  <div id="metricas" class="tab-content p-6">
    <h2 class="text-xl font-bold mb-4">📊 Dashboard del día</h2>
    <div id="metricas-data" class="grid grid-cols-1 md:grid-cols-4 gap-4"></div>
  </div>

  <div id="chats" class="tab-content p-6 h-[75vh]">
    <div class="grid grid-cols-3 gap-4 h-full">
      <div class="bg-gray-700 rounded-lg p-3 overflow-y-auto" id="chat-list"></div>
      <div class="col-span-2 bg-gray-700 rounded-lg p-4 flex flex-col">
        <div id="chat-header" class="text-yellow-400 font-bold mb-3 border-b border-gray-600 pb-2">Selecciona una conversación</div>
        <div id="chat-messages" class="flex-1 overflow-y-auto space-y-3 p-2"></div>
      </div>
    </div>
  </div>

  <div id="campanas" class="tab-content p-6">
    <h2 class="text-xl font-bold mb-4">📢 Envío Masivo</h2>
    <p class="text-gray-400">Usa el endpoint <code>POST /api/v1/broadcast</code> con tu API Key o integra el formulario aquí.</p>
  </div>
</div>
</body></html>""")


@app.get("/panel/recepcion")
def p_recep(request: Request):
    if request.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(content=PANEL_HTML)


@app.get("/panel/metricas")
def p_metricas(request: Request):
    if request.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(content=PANEL_HTML)


@app.get("/panel/logout")
def p_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/panel/login")


# ============================================================
# WEBHOOKS Y HEALTH
# ============================================================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "18.2.8",
        "db": "online" if engine else "offline",
    }


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
