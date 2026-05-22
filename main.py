# -*- coding: utf-8 -*-
"""
🏗️ ORQUESTRATOR ISA v13.4-FINAL
Stack: FastAPI + SQLAlchemy 2.0 (psycopg) + Neon DB + WhatsApp Cloud API
Menú Restinga: 86 platos, 5 idiomas (ES/EN/FR/AR/Darija), paginado 35+8.
"""

import os
import uuid
import httpx
import logging
from datetime import datetime, timezone
from enum import Enum
from decimal import Decimal

from fastapi import FastAPI, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

# ═══════════════════════════════════════════════════════════════════
# 🔧 CONFIGURACIÓN SEGURA
# ═══════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════
# 🗄️ ENGINE DB (NEON + POOL SEGURO)
# ═══════════════════════════════════════════════════════════════════
engine = None
async_session_maker = None

if DATABASE_URL:
    if "postgresql://" in DATABASE_URL and "psycopg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_async_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False,
    )
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    logger.info("✅ Engine DB inicializado (Neon-compatible)")


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════════
# 📦 MODELOS
# ═══════════════════════════════════════════════════════════════════
class EstadoPedido(str, Enum):
    pendiente = "pendiente"
    confirmado = "confirmado"
    entregado = "entregado"
    cancelado = "cancelado"


class EstadoReserva(str, Enum):
    pendiente = "pendiente"
    confirmada = "confirmada"
    cancelada = "cancelada"
    completada = "completada"


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
    language_pref: Mapped[str] = mapped_column(String, default="es")


class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    contexto_bot: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class Pedido(Base):
    __tablename__ = "pedidos"
    id_pedido: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    estado: Mapped[EstadoPedido] = mapped_column(
        SAEnum(
            EstadoPedido,
            name="estado_pedido",
            create_type=False,
        ),
        default=EstadoPedido.pendiente,
    )
    items: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class Reservacion(Base):
    __tablename__ = "reservaciones"
    id_reserva: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_cliente: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    id_conversacion: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    codigo_reserva: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    estado: Mapped[EstadoReserva] = mapped_column(
        SAEnum(
            EstadoReserva,
            name="estado_reserva",
            create_type=False,
        ),
        default=EstadoReserva.pendiente,
    )
    fecha_reserva: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    hora_reserva: Mapped[str] = mapped_column(String, nullable=False)
    num_personas: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    mesa_asignada: Mapped[str] = mapped_column(String, default="", nullable=True)
    zona: Mapped[str] = mapped_column(String, default="", nullable=True)
    ai_confirmada: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════════
# 🛠️ HELPERS
# ═══════════════════════════════════════════════════════════════════
async def send_wa(phone: str, text: str):
    """Envía mensaje por WhatsApp Cloud API o simula en modo DEMO."""
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
        logger.error(f"WA Ex: {e}")


def now_utc() -> datetime:
    """Retorna datetime timezone-aware en UTC."""
    return datetime.now(timezone.utc)


# ── RATE LIMITING BÁSICO (en memoria) ────────────────────────────
_rate_limits: dict = {}


def check_rate_limit(ip: str, max_req: int = 100, window_sec: int = 60) -> bool:
    """Retorna True si el IP está dentro del límite."""
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


# ═══════════════════════════════════════════════════════════════════
# 🍽️ MENÚ RESTINGA — 86 platos, 5 idiomas, 5 categorías
# ═══════════════════════════════════════════════════════════════════
# Estructura: (código, nombre, precio, categoría_idx)
# Categorías: 0=Entrantes, 1=Carnes, 2=Pescados, 3=Bebidas, 4=Postres

_MENU_DATA = {
    "es": {
        "cats": [
            "🥗 Entrantes",
            "🥘 Carnes",
            "🐟 Pescados",
            "🍹 Bebidas",
            "🍰 Postres",
        ],
        "items": [
            ("1", "Ensalada mezclada", 40, 0),
            ("2", "Ensalada rusa", 35, 0),
            ("3", "Ensalada marroquí", 35, 0),
            ("4", "Ensalada tropical", 45, 0),
            ("5", "Ensalada Restinga", 65, 0),
            ("6", "Sopa de verduras", 35, 0),
            ("7", "Sopa de pescado", 45, 0),
            ("8", "Concha fina", 60, 0),
            ("9", "Anchoas marinadas", 50, 0),
            ("10", "Sopa del día", 35, 0),
            ("11", "Croquetas", 45, 0),
            ("12", "Huevo con papas", 40, 0),
            ("13", "Tortilla española", 45, 0),
            ("14", "Tortilla francesa", 40, 0),
            ("15", "Papas fritas", 25, 0),
            ("16", "Espaguetis", 55, 0),
            ("17", "Espaguetis mariscos", 75, 0),
            ("18", "Espaguetis boloñesa", 65, 0),
            ("19", "Camarones al ajillo", 75, 0),
            ("20", "Tortilla de atún", 60, 0),
            ("21", "Cuscús de carne", 80, 1),
            ("22", "Cuscús de pollo", 70, 1),
            ("23", "Cuscús de verduras", 65, 1),
            ("24", "Tajín de carne", 85, 1),
            ("25", "Tajín de pollo", 70, 1),
            ("26", "Tajín carne picada", 75, 1),
            ("27", "Tajín de hígado", 95, 1),
            ("28", "Pastilla de pollo", 90, 1),
            ("29", "Brocheta de carne", 80, 1),
            ("30", "Brocheta de pollo", 70, 1),
            ("31", "Kebab mixto", 85, 1),
            ("32", "Brocheta carne picada", 75, 1),
            ("33", "Filete de res", 110, 1),
            ("34", "Filete de pollo", 70, 1),
            ("35", "Filete pollo champiñones", 80, 1),
            ("36", "Filete de hígado", 95, 1),
            ("37", "Lenguado", 90, 2),
            ("38", "Calamar", 95, 2),
            ("39", "Salmonete", 75, 2),
            ("40", "Pescadilla", 75, 2),
            ("41", "Pescado frito mixto", 95, 2),
            ("42", "Anchoas fritas", 60, 2),
            ("43", "Pez espada Rigamontti", 100, 2),
            ("44", "Tajín de mariscos", 95, 2),
            ("45", "Pastilla de pescado", 100, 2),
            ("46", "Lenguado parrilla", 95, 2),
            ("47", "Calamares plancha", 95, 2),
            ("48", "Salmonete parrilla", 80, 2),
            ("49", "Pez espada parrilla", 95, 2),
            ("50", "Pescado frito parrilla", 100, 2),
            ("51", "Gambas plancha", 95, 2),
            ("52", "Paella (1p)", 90, 2),
            ("53", "Brocheta de pescado", 95, 2),
            ("54", "Refrescos", 15, 3),
            ("55", "Café/té", 20, 3),
            ("56", "Agua 0.5L", 10, 3),
            ("57", "Agua 1.5L", 15, 3),
            ("58", "Oulmes 1L", 20, 3),
            ("59", "Zumo natural", 20, 3),
            ("60", "Toro rojo", 35, 3),
            ("61", "Flag especial", 30, 3),
            ("62", "Cigüeña", 30, 3),
            ("63", "Cerveza s/alcohol", 30, 3),
            ("64", "Flag oro", 35, 3),
            ("65", "Heineken", 38, 3),
            ("66", "Carlsberg", 38, 3),
            ("67", "Casablanca", 38, 3),
            ("68", "Budweiser", 37, 3),
            ("69", "Mahou", 37, 3),
            ("70", "Cruzcampo", 37, 3),
            ("71", "Coronita", 40, 3),
            ("72", "Desperados", 45, 3),
            ("73", "Tinto de terraza", 60, 3),
            ("74", "Vino ½ botella", 135, 3),
            ("75", "Vino ¾ botella", 260, 3),
            ("76", "Selección vinos ½", 165, 3),
            ("77", "Selección vinos ¾", 320, 3),
            ("78", "Copa de vino", 45, 3),
            ("79", "Doble munich", 37, 3),
            ("80", "Flan", 25, 4),
            ("81", "Fruta temporada", 30, 4),
            ("82", "Festival", 45, 4),
            ("83", "Helados", 25, 4),
            ("84", "Magnum", 30, 4),
            ("85", "Tarta nougat-limón", 30, 4),
            ("86", "Tarta Lotus", 35, 4),
        ],
    },
    "en": {
        "cats": ["🥗 Starters", "🥘 Meats", "🐟 Fish", "🍹 Drinks", "🍰 Desserts"],
        "items": [
            ("1", "Mixed salad", 40, 0),
            ("2", "Russian salad", 35, 0),
            ("3", "Moroccan salad", 35, 0),
            ("4", "Tropical salad", 45, 0),
            ("5", "Restinga salad", 65, 0),
            ("6", "Vegetable soup", 35, 0),
            ("7", "Fish soup", 45, 0),
            ("8", "Concha fina", 60, 0),
            ("9", "Marinated anchovies", 50, 0),
            ("10", "Soup of the day", 35, 0),
            ("11", "Croquettes", 45, 0),
            ("12", "Egg with fries", 40, 0),
            ("13", "Spanish tortilla", 45, 0),
            ("14", "French omelette", 40, 0),
            ("15", "French fries", 25, 0),
            ("16", "Spaghetti", 55, 0),
            ("17", "Spaghetti seafood", 75, 0),
            ("18", "Spaghetti bolognese", 65, 0),
            ("19", "Shrimp pil pil", 75, 0),
            ("20", "Tuna omelette", 60, 0),
            ("21", "Meat couscous", 80, 1),
            ("22", "Chicken couscous", 70, 1),
            ("23", "Vegetable couscous", 65, 1),
            ("24", "Meat tagine", 85, 1),
            ("25", "Chicken tagine", 70, 1),
            ("26", "Minced meat tagine", 75, 1),
            ("27", "Liver tagine", 95, 1),
            ("28", "Chicken pastilla", 90, 1),
            ("29", "Meat kebab", 80, 1),
            ("30", "Chicken kebab", 70, 1),
            ("31", "Mixed kebab", 85, 1),
            ("32", "Minced meat kebab", 75, 1),
            ("33", "Beef fillet", 110, 1),
            ("34", "Chicken fillet", 70, 1),
            ("35", "Chicken fillet mushroom", 80, 1),
            ("36", "Liver fillet", 95, 1),
            ("37", "Sole", 90, 2),
            ("38", "Squid", 95, 2),
            ("39", "Red mullet", 75, 2),
            ("40", "Whiting", 75, 2),
            ("41", "Mixed fried fish", 95, 2),
            ("42", "Anchovies", 60, 2),
            ("43", "Swordfish Rigamontti", 100, 2),
            ("44", "Seafood tagine", 95, 2),
            ("45", "Fish pastilla", 100, 2),
            ("46", "Grilled sole", 95, 2),
            ("47", "Grilled squid", 95, 2),
            ("48", "Grilled red mullet", 80, 2),
            ("49", "Grilled swordfish", 95, 2),
            ("50", "Grilled mixed fry", 100, 2),
            ("51", "Grilled shrimp", 95, 2),
            ("52", "Paella (1p)", 90, 2),
            ("53", "Fish skewer", 95, 2),
            ("54", "Soft drinks", 15, 3),
            ("55", "Coffee/tea", 20, 3),
            ("56", "Mineral water 0.5L", 10, 3),
            ("57", "Mineral water 1.5L", 15, 3),
            ("58", "Oulmes 1L", 20, 3),
            ("59", "Fresh juice", 20, 3),
            ("60", "Red Bull", 35, 3),
            ("61", "Special flag", 30, 3),
            ("62", "Stork", 30, 3),
            ("63", "Non-alcoholic beer", 30, 3),
            ("64", "Flag gold", 35, 3),
            ("65", "Heineken", 38, 3),
            ("66", "Carlsberg", 38, 3),
            ("67", "Casablanca", 38, 3),
            ("68", "Budweiser", 37, 3),
            ("69", "Mahou", 37, 3),
            ("70", "Cruzcampo", 37, 3),
            ("71", "Coronita", 40, 3),
            ("72", "Desperados", 45, 3),
            ("73", "Tinto de terraza", 60, 3),
            ("74", "Wine ½ bottle", 135, 3),
            ("75", "Wine ¾ bottle", 260, 3),
            ("76", "Wine selection ½", 165, 3),
            ("77", "Wine selection ¾", 320, 3),
            ("78", "Glass of wine", 45, 3),
            ("79", "Doppel Munich", 37, 3),
            ("80", "Flan", 25, 4),
            ("81", "Seasonal fruit", 30, 4),
            ("82", "Festival", 45, 4),
            ("83", "Ice cream", 25, 4),
            ("84", "Magnum", 30, 4),
            ("85", "Nougat-lemon tart", 30, 4),
            ("86", "Lotus tart", 35, 4),
        ],
    },
    "fr": {
        "cats": [
            "🥗 Entrées",
            "🥘 Viandes",
            "🐟 Poissons",
            "🍹 Boissons",
            "🍰 Desserts",
        ],
        "items": [
            ("1", "Salade variée", 40, 0),
            ("2", "Salade russe", 35, 0),
            ("3", "Salade marocaine", 35, 0),
            ("4", "Salade tropicale", 45, 0),
            ("5", "Salade Restinga", 65, 0),
            ("6", "Soupe de légumes", 35, 0),
            ("7", "Soupe de poisson", 45, 0),
            ("8", "Concha fina", 60, 0),
            ("9", "Anchois marinés", 50, 0),
            ("10", "Potage du jour", 35, 0),
            ("11", "Croquettes", 45, 0),
            ("12", "Œuf avec frites", 40, 0),
            ("13", "Tortilla espagnole", 45, 0),
            ("14", "Omelette française", 40, 0),
            ("15", "Frites", 25, 0),
            ("16", "Spaghetti", 55, 0),
            ("17", "Spaghetti fruits de mer", 75, 0),
            ("18", "Spaghetti bolognaise", 65, 0),
            ("19", "Crevettes à l'ail", 75, 0),
            ("20", "Omelette au thon", 60, 0),
            ("21", "Couscous viande", 80, 1),
            ("22", "Couscous poulet", 70, 1),
            ("23", "Couscous végétarien", 65, 1),
            ("24", "Tajine viande", 85, 1),
            ("25", "Tajine poulet", 70, 1),
            ("26", "Tajine viande hachée", 75, 1),
            ("27", "Tajine de foie", 95, 1),
            ("28", "Pastilla poulet", 90, 1),
            ("29", "Brochette viande", 80, 1),
            ("30", "Brochette poulet", 70, 1),
            ("31", "Kebab mixte", 85, 1),
            ("32", "Brochette viande hachée", 75, 1),
            ("33", "Filet de bœuf", 110, 1),
            ("34", "Filet de poulet", 70, 1),
            ("35", "Filet poulet champignons", 80, 1),
            ("36", "Filet de foie", 95, 1),
            ("37", "Sole", 90, 2),
            ("38", "Calamar", 95, 2),
            ("39", "Rouget", 75, 2),
            ("40", "Merlan", 75, 2),
            ("41", "Friture mixte", 95, 2),
            ("42", "Anchois", 60, 2),
            ("43", "Espadon Rigamontti", 100, 2),
            ("44", "Tajine fruits de mer", 95, 2),
            ("45", "Pastilla de poisson", 100, 2),
            ("46", "Soles grillées", 95, 2),
            ("47", "Calamars grillés", 95, 2),
            ("48", "Rougets grillés", 80, 2),
            ("49", "Espadon grillé", 95, 2),
            ("50", "Friture grillée", 100, 2),
            ("51", "Crevettes grillées", 95, 2),
            ("52", "Paella (1p)", 90, 2),
            ("53", "Brochette de poisson", 95, 2),
            ("54", "Sodas", 15, 3),
            ("55", "Café/thé", 20, 3),
            ("56", "Eau minérale 0.5L", 10, 3),
            ("57", "Eau minérale 1.5L", 15, 3),
            ("58", "Oulmes 1L", 20, 3),
            ("59", "Jus (bio)", 20, 3),
            ("60", "Red Bull", 35, 3),
            ("61", "Flag spécial", 30, 3),
            ("62", "Cigogne", 30, 3),
            ("63", "Bière sans alcool", 30, 3),
            ("64", "Flag or", 35, 3),
            ("65", "Heineken", 38, 3),
            ("66", "Carlsberg", 38, 3),
            ("67", "Casablanca", 38, 3),
            ("68", "Budweiser", 37, 3),
            ("69", "Mahou", 37, 3),
            ("70", "Cruzcampo", 37, 3),
            ("71", "Coronita", 40, 3),
            ("72", "Desperados", 45, 3),
            ("73", "Tinto de terrasse", 60, 3),
            ("74", "Vin ½", 135, 3),
            ("75", "Vin ¾", 260, 3),
            ("76", "Sélection vin ½", 165, 3),
            ("77", "Sélection vin ¾", 320, 3),
            ("78", "Verre de vin", 45, 3),
            ("79", "Doppel Munich", 37, 3),
            ("80", "Flan", 25, 4),
            ("81", "Fruits de saison", 30, 4),
            ("82", "Festival", 45, 4),
            ("83", "Glaces", 25, 4),
            ("84", "Magnum", 30, 4),
            ("85", "Tarte nougat-citron", 30, 4),
            ("86", "Tarte Lotus", 35, 4),
        ],
    },
    "ar": {
        "cats": ["🥗 المقبلات", "🥘 اللحوم", "🐟 السمك", "🍹 المشروبات", "🍰 الحلويات"],
        "items": [
            ("1", "سلطة مختلطة", 40, 0),
            ("2", "سلطة روسية", 35, 0),
            ("3", "سلطة مغربية", 35, 0),
            ("4", "سلطة استوائية", 45, 0),
            ("5", "سلطة ريستينجا", 65, 0),
            ("6", "شوربة خضار", 35, 0),
            ("7", "حساء السمك", 45, 0),
            ("8", "كونشا فينا", 60, 0),
            ("9", "الأنشوجة المتبلة", 50, 0),
            ("10", "حساء اليوم", 35, 0),
            ("11", "كروكيت", 45, 0),
            ("12", "بيض مع بطاطس", 40, 0),
            ("13", "التورتيلا الإسبانية", 45, 0),
            ("14", "عجة فرنسية", 40, 0),
            ("15", "بطاطس مقلية", 25, 0),
            ("16", "معكرونة", 55, 0),
            ("17", "سباغيتي بالمأكولات البحرية", 75, 0),
            ("18", "سباغيتي بولونيز", 65, 0),
            ("19", "جمبري بصلصة الثوم", 75, 0),
            ("20", "أومليت بالتونة", 60, 0),
            ("21", "كسكس باللحم", 80, 1),
            ("22", "كسكس بالدجاج", 70, 1),
            ("23", "كسكس نباتي", 65, 1),
            ("24", "طاجن لحم", 85, 1),
            ("25", "طاجين الدجاج", 70, 1),
            ("26", "طاجن اللحم المفروم", 75, 1),
            ("27", "طاجين الكبدة", 95, 1),
            ("28", "بسطيلة دجاج", 90, 1),
            ("29", "كباب لحم", 80, 1),
            ("30", "كباب دجاج", 70, 1),
            ("31", "كباب مشكل", 85, 1),
            ("32", "كباب لحم مفروم", 75, 1),
            ("33", "لحم فيليه", 110, 1),
            ("34", "فيليه دجاج", 70, 1),
            ("35", "فيليه دجاج بالفطر", 80, 1),
            ("36", "فيليه الكبد", 95, 1),
            ("37", "نعل", 90, 2),
            ("38", "الحبار", 95, 2),
            ("39", "البوري الأحمر", 75, 2),
            ("40", "البياض", 75, 2),
            ("41", "قلي مشكل", 95, 2),
            ("42", "أنشوجة مقلية", 60, 2),
            ("43", "سمك أبو سيف", 100, 2),
            ("44", "طاجين المأكولات البحرية", 95, 2),
            ("45", "بسطيلة السمك", 100, 2),
            ("46", "نعل مشوي", 95, 2),
            ("47", "حبار مشوي", 95, 2),
            ("48", "بوري أحمر مشوي", 80, 2),
            ("49", "أبو سيف مشوي", 95, 2),
            ("50", "قلي مشوي", 100, 2),
            ("51", "جمبري مشوي", 95, 2),
            ("52", "البايلا", 90, 2),
            ("53", "سيخ السمك", 95, 2),
            ("54", "مشروبات غازية", 15, 3),
            ("55", "قهوة/شاي", 20, 3),
            ("56", "مياه معدنية 0.5L", 10, 3),
            ("57", "مياه معدنية 1.5L", 15, 3),
            ("58", "أولمس 1L", 20, 3),
            ("59", "عصير فواكه", 20, 3),
            ("60", "ريد بول", 35, 3),
            ("61", "فلاج خاص", 30, 3),
            ("62", "اللقلق", 30, 3),
            ("63", "بيرة خالية من الكحول", 30, 3),
            ("64", "فلاج ذهبي", 35, 3),
            ("65", "هاينكن", 38, 3),
            ("66", "كارلسبيرغ", 38, 3),
            ("67", "الدار البيضاء", 38, 3),
            ("68", "بودوايزر", 37, 3),
            ("69", "ماهو", 37, 3),
            ("70", "كروزكامبو", 37, 3),
            ("71", "كورونيتا", 40, 3),
            ("72", "ديسبيرادوس", 45, 3),
            ("73", "تينتو دي تيرازا", 60, 3),
            ("74", "نبيذ ½ زجاجة", 135, 3),
            ("75", "نبيذ ¾ زجاجة", 260, 3),
            ("76", "تشكيلة نبيذ ½", 165, 3),
            ("77", "تشكيلة نبيذ ¾", 320, 3),
            ("78", "كأس نبيذ", 45, 3),
            ("79", "دوبل ميونيخ", 37, 3),
            ("80", "فلان", 25, 4),
            ("81", "فواكه موسمية", 30, 4),
            ("82", "مهرجان", 45, 4),
            ("83", "آيس كريم", 25, 4),
            ("84", "ماغنوم", 30, 4),
            ("85", "تارت نوجا-ليمون", 30, 4),
            ("86", "تارت اللوتس", 35, 4),
        ],
    },
    "darija": {
        "cats": ["🥗 Lmqblat", "🥘 L7em", "🐟 L7out", "🍹 Mashrubat", "🍰 Desserts"],
        "items": [
            ("1", "Salada mkhltata", 40, 0),
            ("2", "Salada rusiya", 35, 0),
            ("3", "Salada maghribia", 35, 0),
            ("4", "Salada tropikal", 45, 0),
            ("5", "Salada Restinga", 65, 0),
            ("6", "Chourba khodra", 35, 0),
            ("7", "Chourba l7out", 45, 0),
            ("8", "Concha fina", 60, 0),
            ("9", "Anchwa mratba", 50, 0),
            ("10", "Chourba nhar", 35, 0),
            ("11", "Krokit", 45, 0),
            ("12", "Byd m3a batata", 40, 0),
            ("13", "Tortiya ispania", 45, 0),
            ("14", "Omlet fransawia", 40, 0),
            ("15", "Batata mqliya", 25, 0),
            ("16", "Spagheti", 55, 0),
            ("17", "Spagheti b l7out", 75, 0),
            ("18", "Spagheti bolognaise", 65, 0),
            ("19", "Chambre bi toum", 75, 0),
            ("20", "Omlet b thon", 60, 0),
            ("21", "Ksks b l7em", 80, 1),
            ("22", "Ksks b djej", 70, 1),
            ("23", "Ksks b khodra", 65, 1),
            ("24", "Tajine dyal l7em", 85, 1),
            ("25", "Tajine dyal djej", 70, 1),
            ("26", "Tajine l7em mchermel", 75, 1),
            ("27", "Tajine dyal kebda", 95, 1),
            ("28", "Bastila dyal djej", 90, 1),
            ("29", "Brocheta dyal l7em", 80, 1),
            ("30", "Brocheta dyal djej", 70, 1),
            ("31", "Kebab mchkhel", 85, 1),
            ("32", "Brocheta l7em mchermel", 75, 1),
            ("33", "Filet dyal bovin", 110, 1),
            ("34", "Filet dyal djej", 70, 1),
            ("35", "Filet djej b sos lfkih", 80, 1),
            ("36", "Filet dyal kebda", 95, 1),
            ("37", "Sole", 90, 2),
            ("38", "Calamar", 95, 2),
            ("39", "Rouget", 75, 2),
            ("40", "Merlan", 75, 2),
            ("41", "Friture mkhlta", 95, 2),
            ("42", "Anchwa mqliya", 60, 2),
            ("43", "Sayfich Rigamontti", 100, 2),
            ("44", "Tajine lmaakolat lba7ria", 95, 2),
            ("45", "Bastila dyal l7out", 100, 2),
            ("46", "Sole machwi", 95, 2),
            ("47", "Calamar machwi", 95, 2),
            ("48", "Rouget machwi", 80, 2),
            ("49", "Sayfich machwi", 95, 2),
            ("50", "Friture machwiya", 100, 2),
            ("51", "Chambar machwi", 95, 2),
            ("52", "Paella 1 persona", 90, 2),
            ("53", "Brocheta dyal l7out", 95, 2),
            ("54", "Sodas", 15, 3),
            ("55", "Café / atay", 20, 3),
            ("56", "Ma miniral 0.5L", 10, 3),
            ("57", "Ma miniral 1.5L", 15, 3),
            ("58", "Oulmes 1L", 20, 3),
            ("59", "Jousse bio", 20, 3),
            ("60", "Toro rojo", 35, 3),
            ("61", "Flag special", 30, 3),
            ("62", "Cigogne", 30, 3),
            ("63", "Bira bla lcohol", 30, 3),
            ("64", "Flag gold", 35, 3),
            ("65", "Heineken", 38, 3),
            ("66", "Calsberg", 38, 3),
            ("67", "Casablanca", 38, 3),
            ("68", "Budweiser", 37, 3),
            ("69", "Mahou", 37, 3),
            ("70", "Cruzcampo", 37, 3),
            ("71", "Coronita", 40, 3),
            ("72", "Desperados", 45, 3),
            ("73", "Tinto dyal terrazza", 60, 3),
            ("74", "Vin 1/2", 135, 3),
            ("75", "Vin 3/4", 260, 3),
            ("76", "Selection vin 1/2", 165, 3),
            ("77", "Selection vin 3/4", 320, 3),
            ("78", "Kass dyal vin", 45, 3),
            ("79", "Doppel Munich", 37, 3),
            ("80", "Flan", 25, 4),
            ("81", "Fawakih moussimiya", 30, 4),
            ("82", "Festival", 45, 4),
            ("83", "Glaces", 25, 4),
            ("84", "Magnum", 30, 4),
            ("85", "Tarte nougat-limon", 30, 4),
            ("86", "Tarte Lotus", 35, 4),
        ],
    },
}


# ── Construir lookups ────────────────────────────────────────────
def _build_lookups():
    """Construye diccionarios de búsqueda rápida."""
    platos = {}
    for lang, data in _MENU_DATA.items():
        platos[lang] = {
            it[0]: {"k": it[0], "n": it[1], "p": it[2], "c": it[3]}
            for it in data["items"]
        }
    return platos


PLATOS = _build_lookups()


# ── Paginación ─────────────────────────────────────────────────
PAGE_1_SIZE = 35
PAGE_N_SIZE = 8


def get_menu_page(lang: str, page: int = 0) -> tuple:
    """Retorna (texto_mensaje, total_paginas)."""
    data = _MENU_DATA.get(lang, _MENU_DATA["es"])
    items = data["items"]
    cats = data["cats"]

    # Calcular total de páginas
    rest = len(items) - PAGE_1_SIZE
    extra = max(0, (rest + PAGE_N_SIZE - 1) // PAGE_N_SIZE)
    total_pages = 1 + extra

    # Calcular índices
    if page == 0:
        start, end = 0, min(PAGE_1_SIZE, len(items))
    else:
        start = PAGE_1_SIZE + (page - 1) * PAGE_N_SIZE
        end = min(start + PAGE_N_SIZE, len(items))

    if start >= len(items):
        page = 0
        start, end = 0, min(PAGE_1_SIZE, len(items))

    page_items = items[start:end]

    lines = [f"📋 *MENU* — Página {page + 1}/{total_pages}"]
    current_cat = None
    for it in page_items:
        if it[3] != current_cat:
            current_cat = it[3]
            lines.append(f"\n{cats[current_cat]}")
        lines.append(f"{it[0]}. {it[1]} ({it[2]} MAD)")

    nav = []
    if page > 0:
        nav.append("`a` ← Anterior")
    if page < total_pages - 1:
        nav.append("`s` → Siguiente")
    nav.append("`v` Ver pedido | `c` Confirmar | `q` Salir")

    lines.append("\n" + " | ".join(nav))
    lines.append("\nResponde nº para añadir al carrito")

    return ("\n".join(lines), total_pages)


# ═══════════════════════════════════════════════════════════════════
# 🌐 TRADUCCIONES
# ═══════════════════════════════════════════════════════════════════
I18N = {
    "es": {
        "welcome": (
            "🌍 Bienvenido a {restaurante}\n"
            "Elige tu idioma:\n"
            "1. 🇪🇸 Español\n"
            "2. 🇬🇧 English\n"
            "3. 🇫🇷 Français\n"
            "4. 🇲🇦 الدارجة\n"
            "5. 🇸🇦 العربية"
        ),
        "added": "✅ {plato} añadido. Total: {total} MAD.",
        "cart": ("🛒 *PEDIDO*\n{items}\n💰 Total: {total} MAD"),
        "cart_empty": "🛒 Carrito vacío.",
        "confirm": "✅ Pedido guardado! ID: {id_pedido}",
        "confirm_empty": ("⚠️ Carrito vacío. Nada que confirmar."),
        "removed": ("🗑️ {plato} eliminado. Total: {total} MAD."),
        "invalid": "❌ Nº inválido.",
        "reset": "🔄 Sesión reiniciada.",
        "help": (
            "🤔 *Comandos:*\n"
            "`m` → Menú\n"
            "`a` / `s` → Páginas\n"
            "`v` → Ver pedido\n"
            "`c` → Confirmar\n"
            "`x N` → Quitar ítem N\n"
            "`r` → Reservar\n"
            "`q` → Salir"
        ),
        "res_personas": ("👥 ¿Cuántas personas? (responde un número)"),
        "res_fecha": ("📅 ¿Qué fecha? (YYYY-MM-DD)\nEj: 2026-05-25"),
        "res_hora": ("🕐 ¿Qué hora? (HH:MM)\nEj: 19:30"),
        "res_confirm": (
            "📋 *Reserva*\n"
            "👥 {personas} personas\n"
            "📅 {fecha} 🕐 {hora}\n"
            "\nResponde `si` para confirmar"
        ),
        "res_saved": ("✅ Reserva guardada!\nCódigo: {codigo}"),
        "res_cancel": "❌ Reserva cancelada.",
    },
    "en": {
        "welcome": (
            "🌍 Welcome to {restaurante}\n"
            "Choose your language:\n"
            "1. 🇪🇸 Español\n"
            "2. 🇬🇧 English\n"
            "3. 🇫🇷 Français\n"
            "4. 🇲🇦 الدارجة\n"
            "5. 🇸🇦 العربية"
        ),
        "added": "✅ {plato} added. Total: {total} MAD.",
        "cart": ("🛒 *ORDER*\n{items}\n💰 Total: {total} MAD"),
        "cart_empty": "🛒 Cart is empty.",
        "confirm": "✅ Order saved! ID: {id_pedido}",
        "confirm_empty": ("⚠️ Cart empty. Nothing to confirm."),
        "removed": ("🗑️ {plato} removed. Total: {total} MAD."),
        "invalid": "❌ Invalid number.",
        "reset": "🔄 Session restarted.",
        "help": (
            "🤔 *Commands:*\n"
            "`m` → Menu\n"
            "`a` / `s` → Pages\n"
            "`v` → View order\n"
            "`c` → Confirm\n"
            "`x N` → Remove item N\n"
            "`r` → Reserve\n"
            "`q` → Quit"
        ),
        "res_personas": ("👥 How many people? (reply a number)"),
        "res_fecha": ("📅 What date? (YYYY-MM-DD)\nEx: 2026-05-25"),
        "res_hora": ("🕐 What time? (HH:MM)\nEx: 19:30"),
        "res_confirm": (
            "📋 *Reservation*\n"
            "👥 {personas} people\n"
            "📅 {fecha} 🕐 {hora}\n"
            "\nReply `yes` to confirm"
        ),
        "res_saved": ("✅ Reservation saved!\nCode: {codigo}"),
        "res_cancel": "❌ Reservation cancelled.",
    },
}


def t(key: str, lang: str = "es", **kwargs) -> str:
    """Traducción con fallback a español."""
    text = I18N.get(lang, I18N["es"]).get(key, I18N["es"][key])
    return text.format(**kwargs)


# ═══════════════════════════════════════════════════════════════════
# 🤖 BOT LOGIC
# ═══════════════════════════════════════════════════════════════════
async def process_msg(payload: dict):
    """Procesa un mensaje entrante de WhatsApp Cloud API."""
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
            # 1. Restaurante
            res_r = await db.execute(select(Restaurante).limit(1))
            rest = res_r.scalar_one_or_none()
            if not rest:
                return
            rid = rest.id_restaurante
            rname = rest.nombre

            # 2. Cliente
            res_c = await db.execute(select(Cliente).where(Cliente.wa_id == phone))
            cli = res_c.scalar_one_or_none()
            if not cli:
                cli = Cliente(
                    id_restaurante=rid,
                    wa_id=phone,
                    telefono=phone,
                    language_pref="es",
                )
                db.add(cli)
                await db.flush()

            lang = cli.language_pref or "es"

            # 3. Conversación
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
                    contexto_bot={
                        "fase": "lang",
                        "carrito": [],
                        "lang": "es",
                        "menu_page": 0,
                    },
                )
                db.add(conv)
                await db.flush()
                await db.refresh(conv)

            ctx = dict(conv.contexto_bot) if conv.contexto_bot else {}
            ctx.setdefault("fase", "lang")
            ctx.setdefault("carrito", [])
            ctx.setdefault("lang", "es")
            ctx.setdefault("menu_page", 0)

            reply = t("help", lang)
            fase = ctx.get("fase", "lang")

            # ═══════════════════════════════════════════════════
            # FLUJO: SELECCIÓN DE IDIOMA
            # ═══════════════════════════════════════════════════
            if fase == "lang" or txt in ("q", "salir", "quit"):
                if txt in ("1", "es", "español"):
                    lang = "es"
                    ctx["lang"] = "es"
                    ctx["fase"] = "menu"
                    ctx["menu_page"] = 0
                    cli.language_pref = "es"
                    reply, _ = get_menu_page("es", 0)
                elif txt in ("2", "en", "english"):
                    lang = "en"
                    ctx["lang"] = "en"
                    ctx["fase"] = "menu"
                    ctx["menu_page"] = 0
                    cli.language_pref = "en"
                    reply, _ = get_menu_page("en", 0)
                elif txt in ("3", "fr", "français", "frances"):
                    lang = "fr"
                    ctx["lang"] = "fr"
                    ctx["fase"] = "menu"
                    ctx["menu_page"] = 0
                    cli.language_pref = "fr"
                    reply, _ = get_menu_page("fr", 0)
                elif txt in ("4", "darija", "الدارجة"):
                    lang = "darija"
                    ctx["lang"] = "darija"
                    ctx["fase"] = "menu"
                    ctx["menu_page"] = 0
                    cli.language_pref = "darija"
                    reply, _ = get_menu_page("darija", 0)
                elif txt in ("5", "ar", "العربية", "arabic"):
                    lang = "ar"
                    ctx["lang"] = "ar"
                    ctx["fase"] = "menu"
                    ctx["menu_page"] = 0
                    cli.language_pref = "ar"
                    reply, _ = get_menu_page("ar", 0)
                else:
                    reply = t("welcome", lang, restaurante=rname)

            # ═══════════════════════════════════════════════════
            # FLUJO: MENÚ PRINCIPAL
            # ═══════════════════════════════════════════════════
            elif fase == "menu":
                lookup = PLATOS.get(lang, PLATOS["es"])

                if txt in ("m", "menu", "menú"):
                    ctx["menu_page"] = 0
                    reply, _ = get_menu_page(lang, 0)

                elif txt == "s":
                    page = ctx.get("menu_page", 0) + 1
                    reply, total = get_menu_page(lang, page)
                    if page < total:
                        ctx["menu_page"] = page

                elif txt == "a":
                    page = max(0, ctx.get("menu_page", 0) - 1)
                    reply, _ = get_menu_page(lang, page)
                    ctx["menu_page"] = page

                elif txt.isdigit() and txt in lookup:
                    carrito = list(ctx.get("carrito", []))
                    carrito.append(lookup[txt])
                    ctx["carrito"] = carrito
                    total = sum(i["p"] for i in carrito)
                    reply = t(
                        "added",
                        lang,
                        plato=lookup[txt]["n"],
                        total=total,
                    )

                elif txt in ("v", "pedido", "view", "order"):
                    items = ctx.get("carrito", [])
                    if items:
                        total = sum(i["p"] for i in items)
                        items_txt = "\n".join(
                            [f"• {i['n']} ({i['p']} MAD)" for i in items]
                        )
                        reply = t("cart", lang, items=items_txt, total=total)
                    else:
                        reply = t("cart_empty", lang)

                elif txt in ("c", "confirm", "confirmar"):
                    items = ctx.get("carrito", [])
                    if items:
                        total = sum(i["p"] for i in items)
                        ped = Pedido(
                            id_restaurante=rid,
                            id_cliente=cli.id_cliente,
                            items=list(items),
                            total=Decimal(str(total)),
                        )
                        db.add(ped)
                        await db.flush()
                        await db.refresh(ped)
                        reply = t(
                            "confirm",
                            lang,
                            id_pedido=str(ped.id_pedido)[-6:],
                        )
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
                            total = sum(i["p"] for i in carrito)
                            reply = t(
                                "removed",
                                lang,
                                plato=removed["n"],
                                total=total,
                            )
                        else:
                            reply = t("invalid", lang)
                    else:
                        reply = t("invalid", lang)

                elif txt in ("r", "reservar", "reserve"):
                    ctx["fase"] = "res_p"
                    reply = t("res_personas", lang)

                elif txt in ("q", "salir", "quit"):
                    ctx["fase"] = "lang"
                    ctx["carrito"] = []
                    reply = t("reset", lang)

                else:
                    reply = t("help", lang)

            # ═══════════════════════════════════════════════════
            # FLUJO: RESERVAS
            # ═══════════════════════════════════════════════════
            elif fase == "res_p":
                if txt.isdigit():
                    ctx["res_personas"] = int(txt)
                    ctx["fase"] = "res_f"
                    reply = t("res_fecha", lang)
                else:
                    reply = t("res_personas", lang)

            elif fase == "res_f":
                try:
                    datetime.strptime(txt_raw, "%Y-%m-%d")
                    ctx["res_fecha"] = txt_raw
                    ctx["fase"] = "res_h"
                    reply = t("res_hora", lang)
                except ValueError:
                    reply = t("res_fecha", lang)

            elif fase == "res_h":
                try:
                    datetime.strptime(txt_raw, "%H:%M")
                    ctx["res_hora"] = txt_raw
                    ctx["fase"] = "res_c"
                    reply = t(
                        "res_confirm",
                        lang,
                        personas=ctx.get("res_personas", 1),
                        fecha=ctx.get("res_fecha", ""),
                        hora=txt_raw,
                    )
                except ValueError:
                    reply = t("res_hora", lang)

            elif fase == "res_c":
                if txt in ("si", "yes", "oui", "نعم"):
                    now = now_utc()
                    sec = now.second
                    codigo = f"RES-{now.strftime('%Y%m%d')}-{sec:02d}"
                    fecha_dt = datetime.strptime(
                        f"{ctx['res_fecha']} {ctx['res_hora']}",
                        "%Y-%m-%d %H:%M",
                    ).replace(tzinfo=timezone.utc)
                    res = Reservacion(
                        id_restaurante=rid,
                        id_cliente=cli.id_cliente,
                        id_conversacion=conv.id_conversacion,
                        codigo_reserva=codigo,
                        num_personas=ctx.get("res_personas", 1),
                        fecha_reserva=fecha_dt,
                        hora_reserva=ctx["res_hora"],
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
                    ):
                        ctx.pop(k, None)
                else:
                    reply = t("res_cancel", lang)
                    ctx["fase"] = "menu"
                    for k in (
                        "res_personas",
                        "res_fecha",
                        "res_hora",
                    ):
                        ctx.pop(k, None)

            else:
                ctx["fase"] = "lang"
                ctx["carrito"] = []
                reply = t("reset", lang)

            # ═══════════════════════════════════════════════════
            # ✅ PERSISTENCIA
            # ═══════════════════════════════════════════════════
            conv.contexto_bot = dict(ctx)
            conv.last_message_at = now_utc()
            await db.commit()
            await send_wa(phone, reply)

    except Exception as e:
        logger.error(f"Webhook err: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════════
# 🌐 APP & ROUTES
# ═══════════════════════════════════════════════════════════════════
app = FastAPI(title="Orquestrator ISA v13.4")
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "13.4",
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


# ── PANEL ADMIN ──────────────────────────────────────────────────
LOGIN_HTML = (
    "<html><body>"
    "<h1>🔐 Panel ISA</h1>"
    "<form action='/panel/login' method='post'>"
    "<input name='api_key' placeholder='API Key'>"
    "<button>Entrar</button>"
    "</form></body></html>"
)

DASH_HTML = (
    "<html><head>"
    "<meta charset='utf-8'>"
    "<title>ISA Panel</title>"
    "<style>"
    "body{font-family:system-ui;margin:2rem;background:#0f172a;color:#fff}"
    ".card{background:#1e293b;border-radius:12px;padding:1.5rem;margin:1rem 0}"
    "h1{color:#00e5ff}"
    "</style>"
    "</head><body>"
    "<h1>📊 Orquestrator ISA v13.4</h1>"
    "<div class='card'><h2>🟢 Operativo</h2>"
    "<p>Webhook: /api/whatsapp/webhook</p>"
    "<p>Health: /health</p>"
    "</div>"
    "<div class='card'><h2>📈 Métricas (próximamente)</h2>"
    "<p>Chart.js integration en v14</p>"
    "</div>"
    "</body></html>"
)


@app.get("/panel/login")
def p_login():
    return HTMLResponse(content=LOGIN_HTML)


@app.post("/panel/login")
def p_login_post(req: Request, api_key: str = Form(...)):
    req.session["auth"] = "ok"
    return RedirectResponse("/panel/recepcion", status_code=303)


@app.get("/panel/recepcion")
def p_recep(req: Request):
    if req.session.get("auth") != "ok":
        return RedirectResponse("/panel/login")
    return HTMLResponse(content=DASH_HTML)


# ═══════════════════════════════════════════════════════════════════
# 🚀 ENTRY POINT
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
