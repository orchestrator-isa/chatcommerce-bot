# -*- coding: utf-8 -*-
"""
🏗️ ORQUESTRATOR ISA v13.2-STABLE
Stack: FastAPI + SQLAlchemy 2.0 (psycopg) + Neon DB + WhatsApp Cloud API
Fixes: Menú Restinga paginado (8 platos/página), 5 idiomas, 5 categorías.
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

from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import (
    Enum as SAEnum, String, Boolean, DECIMAL, DateTime,
    Integer, select
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
PANEL_SECRET = os.getenv(
    "PANEL_SESSION_SECRET", "fallback_secret_2026"
).strip()
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
    if (
        "postgresql://" in DATABASE_URL
        and "psycopg" not in DATABASE_URL
    ):
        DATABASE_URL = DATABASE_URL.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

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
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True)
    )
    wa_id: Mapped[str] = mapped_column(String, unique=True)
    telefono: Mapped[str] = mapped_column(String)
    language_pref: Mapped[str] = mapped_column(
        String, default="es"
    )


class Conversacion(Base):
    __tablename__ = "conversaciones"
    id_conversacion: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True)
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True)
    )
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
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True)
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True)
    )
    estado: Mapped[EstadoPedido] = mapped_column(
        SAEnum(
            EstadoPedido,
            name="estado_pedido",
            create_type=False,
        ),
        default=EstadoPedido.pendiente,
    )
    items: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), default=Decimal("0.00")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class Reservacion(Base):
    __tablename__ = "reservaciones"
    id_reserva: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_restaurante: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True)
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True)
    )
    id_conversacion: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    codigo_reserva: Mapped[str] = mapped_column(
        String, unique=True, nullable=False
    )
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
    hora_reserva: Mapped[str] = mapped_column(
        String, nullable=False
    )
    num_personas: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )
    mesa_asignada: Mapped[str] = mapped_column(
        String, default="", nullable=True
    )
    zona: Mapped[str] = mapped_column(
        String, default="", nullable=True
    )
    ai_confirmada: Mapped[bool] = mapped_column(
        Boolean, default=False
    )
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
    url = (
        f"https://graph.facebook.com/v18.0/"
        f"{WA_PHONE_ID}/messages"
    )
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


def check_rate_limit(
    ip: str, max_req: int = 100, window_sec: int = 60
) -> bool:
    """Retorna True si el IP está dentro del límite."""
    now = now_utc()
    if ip not in _rate_limits:
        _rate_limits[ip] = []
    _rate_limits[ip] = [
        t
        for t in _rate_limits[ip]
        if (now - t).total_seconds() < window_sec
    ]
    if len(_rate_limits[ip]) >= max_req:
        return False
    _rate_limits[ip].append(now)
    return True


# ═══════════════════════════════════════════════════════════════════
# 🍽️ MENÚ RESTINGA (estructurado por categorías)
# ═══════════════════════════════════════════════════════════════════
# Cada plato: k=código, n=nombre, p=precio MAD, c=categoría idx

MENU_RESTINGA = {
    "es": {
        "cats": [
            "🥗 Entrantes / Tapas",
            "🥘 Carnes / Especialidades",
            "🐟 Pescados y Mariscos",
            "🍹 Bebidas",
            "🍰 Postres",
        ],
        "items": [
            {"k": "1", "n": "Ensalada mezclada", "p": 40, "c": 0},
            {"k": "2", "n": "Ensalada rusa", "p": 35, "c": 0},
            {"k": "3", "n": "Ensalada marroquí", "p": 35, "c": 0},
            {"k": "4", "n": "Ensalada tropical", "p": 45, "c": 0},
            {"k": "5", "n": "Ensalada Restinga", "p": 65, "c": 0},
            {"k": "6", "n": "Sopa de verduras", "p": 35, "c": 0},
            {"k": "7", "n": "Sopa de pescado", "p": 45, "c": 0},
            {"k": "8", "n": "Concha fina", "p": 60, "c": 0},
            {"k": "9", "n": "Anchoas marinadas", "p": 50, "c": 0},
            {"k": "10", "n": "Sopa del día", "p": 35, "c": 0},
            {"k": "11", "n": "Croquetas", "p": 45, "c": 0},
            {"k": "12", "n": "Huevo con papas fritas", "p": 40, "c": 0},
            {"k": "13", "n": "Tortilla española", "p": 45, "c": 0},
            {"k": "14", "n": "Tortilla francesa", "p": 40, "c": 0},
            {"k": "15", "n": "Papas fritas", "p": 25, "c": 0},
            {"k": "16", "n": "Espaguetis", "p": 55, "c": 0},
            {"k": "17", "n": "Espaguetis con mariscos", "p": 75, "c": 0},
            {"k": "18", "n": "Espaguetis a la boloñesa", "p": 65, "c": 0},
            {"k": "19", "n": "Camarones al ajillo", "p": 75, "c": 0},
            {"k": "20", "n": "Tortilla de atún", "p": 60, "c": 0},
            {"k": "21", "n": "Cuscús de carne", "p": 80, "c": 1},
            {"k": "22", "n": "Cuscús de pollo", "p": 70, "c": 1},
            {"k": "23", "n": "Cuscús de verduras", "p": 65, "c": 1},
            {"k": "24", "n": "Tajín de carne", "p": 85, "c": 1},
            {"k": "25", "n": "Tajín de pollo", "p": 70, "c": 1},
            {"k": "26", "n": "Tajín de carne picada", "p": 75, "c": 1},
            {"k": "27", "n": "Tajín de hígado", "p": 95, "c": 1},
            {"k": "28", "n": "Pastilla de pollo", "p": 90, "c": 1},
            {"k": "29", "n": "Brocheta de carne", "p": 80, "c": 1},
            {"k": "30", "n": "Brocheta de pollo", "p": 70, "c": 1},
            {"k": "31", "n": "Kebab mixto", "p": 85, "c": 1},
            {"k": "32", "n": "Brocheta carne picada", "p": 75, "c": 1},
            {"k": "33", "n": "Filete de res", "p": 110, "c": 1},
            {"k": "34", "n": "Filete de pollo", "p": 70, "c": 1},
            {"k": "35", "n": "Filete pollo champiñones", "p": 80, "c": 1},
            {"k": "36", "n": "Filete de hígado", "p": 95, "c": 1},
            {"k": "37", "n": "Lenguado", "p": 90, "c": 2},
            {"k": "38", "n": "Calamar", "p": 95, "c": 2},
            {"k": "39", "n": "Salmonete", "p": 75, "c": 2},
            {"k": "40", "n": "Pescadilla", "p": 75, "c": 2},
            {"k": "41", "n": "Pescado frito mixto", "p": 95, "c": 2},
            {"k": "42", "n": "Anchoas fritas", "p": 60, "c": 2},
            {"k": "43", "n": "Pez espada Rigamontti", "p": 100, "c": 2},
            {"k": "44", "n": "Tajín de mariscos", "p": 95, "c": 2},
            {"k": "45", "n": "Pastilla de pescado", "p": 100, "c": 2},
            {"k": "46", "n": "Lenguado a la parrilla", "p": 95, "c": 2},
            {"k": "47", "n": "Calamares a la plancha", "p": 95, "c": 2},
            {"k": "48", "n": "Salmonete a la parrilla", "p": 80, "c": 2},
            {"k": "49", "n": "Pez espada a la parrilla", "p": 95, "c": 2},
            {"k": "50", "n": "Pescado frito a la parrilla", "p": 100, "c": 2},
            {"k": "51", "n": "Gambas a la plancha", "p": 95, "c": 2},
            {"k": "52", "n": "Paella (1 persona)", "p": 90, "c": 2},
            {"k": "53", "n": "Brocheta de pescado", "p": 95, "c": 2},
            {"k": "54", "n": "Refrescos", "p": 15, "c": 3},
            {"k": "55", "n": "Café / té", "p": 20, "c": 3},
            {"k": "56", "n": "Agua mineral 0.5L", "p": 10, "c": 3},
            {"k": "57", "n": "Agua mineral 1.5L", "p": 15, "c": 3},
            {"k": "58", "n": "Oulmes 1L", "p": 20, "c": 3},
            {"k": "59", "n": "Zumo fruta natural", "p": 20, "c": 3},
            {"k": "60", "n": "Toro rojo", "p": 35, "c": 3},
            {"k": "61", "n": "Flag especial", "p": 30, "c": 3},
            {"k": "62", "n": "Cigüeña", "p": 30, "c": 3},
            {"k": "63", "n": "Cerveza sin alcohol", "p": 30, "c": 3},
            {"k": "64", "n": "Flag oro", "p": 35, "c": 3},
            {"k": "65", "n": "Heineken", "p": 38, "c": 3},
            {"k": "66", "n": "Carlsberg", "p": 38, "c": 3},
            {"k": "67", "n": "Casablanca", "p": 38, "c": 3},
            {"k": "68", "n": "Budweiser", "p": 37, "c": 3},
            {"k": "69", "n": "Mahou", "p": 37, "c": 3},
            {"k": "70", "n": "Cruzcampo", "p": 37, "c": 3},
            {"k": "71", "n": "Coronita", "p": 40, "c": 3},
            {"k": "72", "n": "Desperados", "p": 45, "c": 3},
            {"k": "73", "n": "Tinto de terraza", "p": 60, "c": 3},
            {"k": "74", "n": "Vino ½ botella", "p": 135, "c": 3},
            {"k": "75", "n": "Vino ¾ botella", "p": 260, "c": 3},
            {"k": "76", "n": "Selección vinos ½", "p": 165, "c": 3},
            {"k": "77", "n": "Selección vinos ¾", "p": 320, "c": 3},
            {"k": "78", "n": "Copa de vino", "p": 45, "c": 3},
            {"k": "79", "n": "Doble munich", "p": 37, "c": 3},
            {"k": "80", "n": "Flan", "p": 25, "c": 4},
            {"k": "81", "n": "Fruta de temporada", "p": 30, "c": 4},
            {"k": "82", "n": "Festival", "p": 45, "c": 4},
            {"k": "83", "n": "Helados", "p": 25, "c": 4},
            {"k": "84", "n": "Magnum", "p": 30, "c": 4},
            {"k": "85", "n": "Tarta nougat-limón", "p": 30, "c": 4},
            {"k": "86", "n": "Tarta Lotus", "p": 35, "c": 4},
        ],
    },
}


# ── PLATOS como lookup rápido ───────────────────────────────────
def build_platos_lookup():
    """Construye diccionario {lang: {k: item}} para búsqueda rápida."""
    lookup = {}
    for lang, data in MENU_RESTINGA.items():
        lookup[lang] = {item["k"]: item for item in data["items"]}
    return lookup


PLATOS_LOOKUP = build_platos_lookup()


# ── Paginación del menú ─────────────────────────────────────────
PAGE_SIZE = 8


def get_menu_page(lang: str, page: int = 0) -> tuple:
    """Retorna (texto_mensaje, total_paginas) para una página."""
    data = MENU_RESTINGA.get(lang, MENU_RESTINGA["es"])
    items = data["items"]
    cats = data["cats"]
    total_pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(items))
    page_items = items[start:end]

    lines = [
        f"📋 *MENÚ RESTINGA* — Página {page + 1}/{total_pages}"
    ]
    current_cat = None
    for it in page_items:
        if it["c"] != current_cat:
            current_cat = it["c"]
            lines.append(f"\n{cats[current_cat]}")
        lines.append(f"{it['k']}. {it['n']} ({it['p']} MAD)")

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
        "cart": (
            "🛒 *PEDIDO*\n{items}\n"
            "💰 Total: {total} MAD"
        ),
        "cart_empty": "🛒 Carrito vacío.",
        "confirm": "✅ Pedido guardado! ID: {id_pedido}",
        "confirm_empty": (
            "⚠️ Carrito vacío. Nada que confirmar."
        ),
        "removed": (
            "🗑️ {plato} eliminado. Total: {total} MAD."
        ),
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
        "res_personas": (
            "👥 ¿Cuántas personas? (responde un número)"
        ),
        "res_fecha": (
            "📅 ¿Qué fecha? (YYYY-MM-DD)\n"
            "Ej: 2026-05-25"
        ),
        "res_hora": (
            "🕐 ¿Qué hora? (HH:MM)\n" "Ej: 19:30"
        ),
        "res_confirm": (
            "📋 *Reserva*\n"
            "👥 {personas} personas\n"
            "📅 {fecha} 🕐 {hora}\n"
            "\nResponde `si` para confirmar"
        ),
        "res_saved": (
            "✅ Reserva guardada!\n" "Código: {codigo}"
        ),
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
        "cart": (
            "🛒 *ORDER*\n{items}\n"
            "💰 Total: {total} MAD"
        ),
        "cart_empty": "🛒 Cart is empty.",
        "confirm": "✅ Order saved! ID: {id_pedido}",
        "confirm_empty": (
            "⚠️ Cart empty. Nothing to confirm."
        ),
        "removed": (
            "🗑️ {plato} removed. Total: {total} MAD."
        ),
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
        "res_personas": (
            "👥 How many people? (reply a number)"
        ),
        "res_fecha": (
            "📅 What date? (YYYY-MM-DD)\n"
            "Ex: 2026-05-25"
        ),
        "res_hora": (
            "🕐 What time? (HH:MM)\n" "Ex: 19:30"
        ),
        "res_confirm": (
            "📋 *Reservation*\n"
            "👥 {personas} people\n"
            "📅 {fecha} 🕐 {hora}\n"
            "\nReply `yes` to confirm"
        ),
        "res_saved": (
            "✅ Reservation saved!\n" "Code: {codigo}"
        ),
        "res_cancel": "❌ Reservation cancelled.",
    },
}


def t(key: str, lang: str = "es", **kwargs) -> str:
    """Traducción con fallback a español."""
    text = (
        I18N.get(lang, I18N["es"]).get(key, I18N["es"][key])
    )
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
            res_c = await db.execute(
                select(Cliente).where(Cliente.wa_id == phone)
            )
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
                else:
                    reply = t("welcome", lang, restaurante=rname)

            # ═══════════════════════════════════════════════════
            # FLUJO: MENÚ PRINCIPAL
            # ═══════════════════════════════════════════════════
            elif fase == "menu":
                lookup = PLATOS_LOOKUP.get(lang, PLATOS_LOOKUP["es"])

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
                            [
                                f"• {i['n']} ({i['p']} MAD)"
                                for i in items
                            ]
                        )
                        reply = t(
                            "cart", lang, items=items_txt, total=total
                        )
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
                    codigo = (
                        f"RES-{now.strftime('%Y%m%d')}-{sec:02d}"
                    )
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
app = FastAPI(title="Orquestrator ISA v13.2")
app.add_middleware(SessionMiddleware, secret_key=PANEL_SECRET)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "13.2",
        "db": "online" if engine else "offline",
    }


@app.get("/api/whatsapp/webhook")
def wb_verify(req: Request):
    if req.query_params.get("hub.verify_token") == WEBHOOK_VERIFY:
        return int(req.query_params.get("hub.challenge", 0))
    return JSONResponse(
        content={"status": "forbidden"}, status_code=403
    )


@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    client_ip = (
        req.headers.get("x-forwarded-for", req.client.host)
        or "unknown"
    )
    if not check_rate_limit(client_ip.split(",")[0].strip()):
        return JSONResponse(
            content={"status": "rate_limited"}, status_code=429
        )
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
    "<h1>📊 Orquestrator ISA v13.2</h1>"
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

    uvicorn.run(
        app, host="0.0.0.0", port=int(os.getenv("PORT", 10000))
    )

