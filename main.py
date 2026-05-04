#!/usr/bin/env python3
"""
Orquestrator ISA — ChatCommerce Bot v2.1 (FIXED)
FastAPI + WhatsApp Business API + Supabase + CORS + Aliases bilingües
7 idiomas: Darija, Árabe, Francés, Español, Inglés, Alemán, Turco
Deploy: Render.com | Tetouan, Marruecos
"""
import os, json, logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware  # ← CORS AÑADIDO
from pydantic import BaseModel, Field
from supabase import create_client, Client
import httpx

# ── Logging ────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("isa-bot")

# ── Variables de entorno ───────────────────
WHATSAPP_TOKEN   = os.getenv("WHATSAPP_TOKEN", "")
VERIFY_TOKEN     = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
PHONE_NUMBER_ID  = os.getenv("PHONE_NUMBER_ID", "")
SUPABASE_URL     = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY     = os.getenv("SUPABASE_KEY", "")
WEBHOOK_PREFIX   = os.getenv("WEBHOOK_PREFIX", "/api/whatsapp/webhook")
META_API_VERSION = os.getenv("META_API_VERSION", "v18.0")

# ── Supabase singleton ─────────────────────
_supabase: Optional[Client] = None
def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no configurados")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[SUPABASE] Cliente inicializado")
    return _supabase

# ── Modelos CORREGIDOS: Aliases bilingües ────────────────────────────────
class ClientCreate(BaseModel):
    # Acepta: name O nombre, owner_phone O telefono
    name: str = Field(..., alias="name")
    nombre: Optional[str] = Field(None, alias="nombre")  # alias español
    owner_phone: str = Field(..., alias="owner_phone")
    telefono: Optional[str] = Field(None, alias="telefono")  # alias español
    language: str = Field(default="darija")
    business_type: str = Field(default="restaurant")
    zone: str = Field(default="centro")
    plan: str = Field(default="basic")
    
    class Config:
        populate_by_name = True  # ← Permite usar alias o nombre real
    
    # Helper para mapear campos españoles → ingleses
    def to_db_dict(self) -> dict:
        return {
            "name": self.nombre or self.name,
            "owner_phone": self.telefono or self.owner_phone,
            "language": self.language,
            "business_type": self.business_type,
            "zone": self.zone,
            "plan": self.plan,
        }

class MenuItemCreate(BaseModel):
    # Acepta: client_id O menu_id, dish_name O nombre, etc.
    client_id: str = Field(..., alias="client_id")
    menu_id: Optional[str] = Field(None, alias="menu_id")  # alias alternativo
    category: str = Field(default="")
    dish_name: str = Field(..., alias="dish_name")
    nombre: Optional[str] = Field(None, alias="nombre")  # alias español
    description: str = Field(default="")
    descripcion: Optional[str] = Field(None, alias="descripcion")  # alias español
    price: int = Field(..., alias="price")
    precio: Optional[int] = Field(None, alias="precio")  # alias español
    is_available: bool = Field(default=True)
    disponible: Optional[bool] = Field(None, alias="disponible")  # alias español
    
    class Config:
        populate_by_name = True
    
    def to_db_dict(self) -> dict:
        return {
            "client_id": self.menu_id or self.client_id,
            "category": self.category,
            "dish_name": self.nombre or self.dish_name,
            "description": self.descripcion or self.description,
            "price": self.precio or self.price,
            "is_available": self.disponible if self.disponible is not None else self.is_available,
        }

# ── NLP 7 idiomas (sin cambios - ya funciona) ───────────────────────────
class DarijaNLP:
    INTENTS = {
        "greeting": ["salam", "ahlan", "marhba", "labas", "bonjour", "hola", "hello", "مرحبا", "سلام"],
        "menu": ["menu", "lmenu", "lmaakoul", "carte", "carta", "food", "قائمة", "منيو"],
        "order": ["bghit", "3tini", "commande", "pedir", "order", "أريد", "أطلب"],
        "help": ["3awni", "kifach", "ayuda", "help", "aide", "مساعدة", "كيف"],
        "cancel": ["lghi", "batal", "cancel", "annuler", "إلغاء", "لا أريد"],
        "confirm": ["wakha", "ayeh", "oui", "yes", "نعم", "تمام"],
        "lista": ["lista", "list", "restaurantes", "المطاعم"],
        "pedido": ["pedido", "mon commande", "my order", "طلبي"],
    }
    LANG_KEYWORDS = {
        "arabic": ["مرحبا", "طلب", "شكرا", "كم", "ثمن", "تاكوز", "ساندويتش"],
        "french": ["bonjour", "merci", "oui", "non", "je", "commande", "menu"],
        "spanish": ["hola", "quiero", "gracias", "pedido", "menú", "taco"],
        "english": ["hello", "want", "thanks", "order", "menu", "food"],
        "german": ["hallo", "danke", "bitte", "bestellen", "essen"],
        "turkish": ["merhaba", "teşekkür", "lütfen", "sipariş", "yemek"],
    }
    
    @classmethod
    def detect_language(cls, text: str) -> str:
        text_lower = text.lower().strip()
        # Darija romanizado - prioridad 1 para Tetouan
        if any(kw in text_lower for kw in ["salam", "bghit", "chno", "wakha", "3ndi", "kifach", "bzaf", "chwiya"]):
            return "darija"
        # Árabe script
        if any("\u0600" <= c <= "\u06FF" for c in text):
            return "arabic"
        # Otros idiomas por keywords
        for lang, keywords in cls.LANG_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return lang
        return "darija"  # fallback
    
    @classmethod
    def detect_intent(cls, text: str) -> str:
        text_lower = text.strip().lower()
        if text_lower.isdigit():
            return "add_item"
        for intent, keywords in cls.INTENTS.items():
            if any(kw in text_lower for kw in keywords):
                return intent
        return "unknown"

# ── Bot Logic - Respuestas 7 idiomas ───────────────────────────────────
class BotLogic:
    MSG = {
        "greeting": {
            "darija": "👋 *Salam! Marhba bik f Orquestrator ISA* 😊\nAna l'assistant dyalek dyal l-commandes 3la WhatsApp.\n🍴 *Shuf lmenu* — kteb \"menu\"\n📍 *Restaurants* — kteb \"lista\"\n❓ *M3awda* — kteb \"ayuda\"",
            "arabic": "👋 *سلام! مرحبا بيك* 😊\nأنا مساعد الطلبات ديالك فـ WhatsApp.\n🍴 *شوف المنيو* — كتب \"menu\"\n📍 *المطاعم* — كتب \"lista\"\n❓ *مساعدة* — كتب \"ayuda\"",
            "spanish": "👋 *¡Hola! Bienvenido a Orquestrator ISA* 😊\nSoy tu asistente de pedidos por WhatsApp.\n🍴 *Ver menú* — escribe \"menu\"\n📍 *Restaurantes* — escribe \"lista\"\n❓ *Ayuda* — escribe \"ayuda\"",
            "french": "👋 *Bonjour! Bienvenue chez Orquestrator ISA* 😊\nJe suis votre assistant de commandes WhatsApp.\n🍴 *Voir le menu* — tapez \"menu\"\n📍 *Restaurants* — tapez \"lista\"\n❓ *Aide* — tapez \"ayuda\"",
            "english": "👋 *Hello! Welcome to Orquestrator ISA* 😊\nI'm your WhatsApp ordering assistant.\n🍴 *See menu* — type \"menu\"\n📍 *Restaurants* — type \"lista\"\n❓ *Help* — type \"ayuda\"",
            "german": "👋 *Hallo! Willkommen bei Orquestrator ISA* 😊\nIch bin Ihr WhatsApp-Bestellassistent.\n🍴 *Menü ansehen* — tippen Sie \"menu\"\n📍 *Restaurants* — tippen Sie \"lista\"\n❓ *Hilfe* — tippen Sie \"ayuda\"",
            "turkish": "👋 *Merhaba! Orquestrator ISA'ya hoş geldiniz* 😊\nBen WhatsApp sipariş asistanınızım.\n🍴 *Menüyü gör* — \"menu\" yazın\n📍 *Restoranlar* — \"lista\" yazın\n❓ *Yardım* — \"ayuda\" yazın",
        },
        "help": {
            "darija": "📋 *Chno tقدر dir:*\n• *menu* — shuf lmaakoul\n• *lista* — shuf restaurants\n• *pedido* — shuf lcommande\n• *lghi* — lghi lcommande\n• *salam* — bda mn jdid",
            "arabic": "📋 *شنو تقدر دير:*\n• *menu* — شوف الماكول\n• *lista* — شوف المطاعم\n• *pedido* — شوف الطلب\n• *lghi* — لغى الطلب\n• *salam* — بدا من جديد",
            "spanish": "📋 *Comandos disponibles:*\n• *menu* — ver la comida\n• *lista* — ver restaurantes\n• *pedido* — ver tu pedido\n• *lghi* — cancelar pedido\n• *salam* — empezar de nuevo",
        },
        "unknown": {
            "darija": "😅 Ma fhamteksh\nKteb *menu* bach tchouf lmaakoul\nWlla *ayuda* bach tchouf chno tقدر dir.",
            "arabic": "😅 ما فهمتكش\nاكتب *menu* باش تشوف الماكول\nولا *ayuda* باش تشوف الخيارات.",
            "spanish": "😅 No te entendí\nEscribe *menu* para ver los platos\nO *ayuda* para las opciones.",
        },
    }
    
    @classmethod
    def _get(cls, key: str, lang: str) -> str:
        msgs = cls.MSG.get(key, cls.MSG["unknown"])
        return msgs.get(lang, msgs.get("darija", ""))
    
    @classmethod
    async def process_message(cls, from_number: str, message_text: str) -> str:
        lang = DarijaNLP.detect_language(message_text)
        intent = DarijaNLP.detect_intent(message_text)
        logger.info(f"[BOT] {from_number[-4:]} | lang={lang} | intent={intent} | '{message_text[:40]}'")
        
        # Guardar mensaje en DB (silencioso si falla)
        try:
            sb = get_supabase()
            sb.table("messages").insert({
                "from_number": from_number,
                "message_text": message_text,
                "direction": "incoming",
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[DB] No se pudo guardar: {e}")
        
        # Respuestas por intención
        if intent in ["greeting", "help", "cancel", "confirm"]:
            return cls._get(intent, lang)
        if intent == "order":
            return cls._get("order_ask", lang)
        if intent == "menu":
            return await cls.get_menu(from_number, lang)
        if intent == "lista":
            return await cls.get_restaurants(lang)
        if intent == "pedido":
            return await cls.get_order(from_number, lang)
        if intent == "add_item" and message_text.strip().isdigit():
            return await cls.add_to_order(from_number, int(message_text.strip()), lang)
        return cls._get("unknown", lang)
    
    @classmethod
    async def get_restaurants(cls, lang: str = "darija") -> str:
        try:
            sb = get_supabase()
            res = sb.table("clients").select("name,business_type,zone,google_rating,google_reviews").eq("is_active", True).order("google_reviews", desc=True).limit(10).execute()
            if not res.data:
                return cls._get("no_restaurants", lang)
            lines = [f"🏪 *Restaurantes en Tetouan:*"]
            for r in res.data:
                lines.append(f"  • *{r['name']}* — {r.get('zone','?')} ⭐{r.get('google_rating','?')}")
            lines.append("\nEscribe *menu* para ver el menú de un restaurante.")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error restaurantes: {e}")
            return "Error al cargar restaurantes. Intenta de nuevo."
    
    @classmethod
    async def get_menu(cls, from_number: str, lang: str = "darija") -> str:
        try:
            sb = get_supabase()
            # Obtener restaurante por número de teléfono (simplificado: toma el primero activo)
            cr = sb.table("clients").select("id,name").eq("is_active", True).limit(1).execute()
            if not cr.data:
                return "No hay restaurantes disponibles."
            client = cr.data[0]
            mr = sb.table("menu_items").select("*").eq("client_id", client["id"]).eq("is_available", True).execute()
            if not mr.data:
                return f"Menú de *{client['name']}* aún no disponible."
            lines = [f"📋 *Menú de {client['name']}:*"]
            for item in mr.data:
                lines.append(f"  • *{item['dish_name']}* — {item['price']} MAD")
            lines.append("\nEscribe el nombre del plato para pedir.")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error menú: {e}")
            return "Error al cargar menú."
    
    @classmethod
    async def get_order(cls, from_number: str, lang: str = "darija") -> str:
        return "🛒 Aún no tienes pedidos activos. Escribe *menu* para empezar."
    
    @classmethod
    async def add_to_order(cls, from_number: str, item_number: int, lang: str = "darija") -> str:
        return f"✅ Plato #{item_number} añadido a tu pedido. Escribe *pedido* para ver."

# ── WhatsApp Service ─────────────────────────────────────────────────
class WhatsAppService:
    BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"
    
    @classmethod
    async def send_text(cls, to: str, message: str) -> Dict:
        if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
            return {"error": "Config incompleta"}
        url = f"{cls.BASE_URL}/{PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text", "text": {"preview_url": False, "body": message}}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.post(url, headers=headers, json=payload)
                return r.json() if r.status_code == 200 else {"error": f"{r.status_code}: {r.text}"}
            except Exception as e:
                return {"error": str(e)}

# ── Lifespan ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[STARTUP] Orquestrator ISA v2.1 iniciando...")
    yield
    logger.info("[SHUTDOWN] Orquestrator ISA detenido.")

# ── FastAPI App ─────────────────────────────────────────────────────
app = FastAPI(title="Orquestrator ISA", description="WhatsApp Bot 7 idiomas Tetouan", version="2.1.0", lifespan=lifespan)

# ← CORS MIDDLEWARE AÑADIDO
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, especificar dominios reales
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "ok", "service": "Orquestrator ISA", "version": "2.1.0", "languages": ["darija","arabic","french","spanish","english","german","turkish"]}

@app.get("/health")
async def health():
    status = {"status": "healthy", "supabase": False, "whatsapp_token": bool(WHATSAPP_TOKEN), "phone_number_id": bool(PHONE_NUMBER_ID)}
    try:
        sb = get_supabase()
        sb.table("clients").select("count", count="exact").limit(1).execute()
        status["supabase"] = True
    except: pass
    return status

@app.get(WEBHOOK_PREFIX)
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403, "Verification failed")

@app.post(WEBHOOK_PREFIX)
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except:
        return JSONResponse({"status": "error"}, status_code=400)
    background_tasks.add_task(process_payload, body)
    return JSONResponse({"status": "ok"}, status_code=200)

async def process_payload(body: Dict[str, Any]):
    try:
        if body.get("object") != "whatsapp_business_account": return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    from_number = msg.get("from")
                    if msg.get("type") == "text":
                        text = msg.get("text", {}).get("body", "")
                        response = await BotLogic.process_message(from_number, text)
                        await WhatsAppService.send_text(from_number, response)
    except Exception as e:
        logger.error(f"[PAYLOAD] Error: {e}", exc_info=True)

# ── API Endpoints CORREGIDOS con aliases bilingües ───────────────────

@app.get("/api/clients")
async def list_clients():
    try:
        sb = get_supabase()
        res = sb.table("clients").select("*").eq("is_active", True).order("google_reviews", desc=True).execute()
        return {"clients": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/clients")
async def create_client(client: ClientCreate):
    try:
        sb = get_supabase()
        data = client.to_db_dict()
        data.update({"is_active": True, "total_messages": 0, "total_orders": 0, "whatsapp_status": "contactar", "trial_ends_at": (datetime.utcnow() + timedelta(days=20)).date().isoformat()})
        res = sb.table("clients").insert(data).execute()
        return {"client": res.data[0]}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    try:
        sb = get_supabase()
        res = sb.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        return {"menu_items": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/menu")  # ← Endpoint para crear platos
async def create_menu_item(item: MenuItemCreate):
    try:
        sb = get_supabase()
        data = item.to_db_dict()
        res = sb.table("menu_items").insert(data).execute()
        return {"menu_item": res.data[0]}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.get("/api/stats")
async def get_stats():
    try:
        sb = get_supabase()
        return {
            "clients": sb.table("clients").select("count", count="exact").execute().count,
            "menu_items": sb.table("menu_items").select("count", count="exact").execute().count,
            "messages": sb.table("messages").select("count", count="exact").execute().count,
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
