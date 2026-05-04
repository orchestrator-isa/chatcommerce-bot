#!/usr/bin/env python3
"""
Orquestrator ISA — ChatCommerce Bot v2.2 (FIXED)
FastAPI + WhatsApp Business API + Supabase
Darija Dual: árabe script + latino romanizado + 6 idiomas adicionales
Deploy: Render.com | Tetouan, Marruecos
"""
import os, json, logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
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
    # Campos en inglés(requeridos)
    name: Optional[str] = Field(None, alias="name")
    owner_phone: Optional[str] = Field(None, alias="owner_phone")

    # Aliases en español (opcionales)
    nombre: Optional[str] = Field(None, alias="nombre")
    telefono: Optional[str] = Field(None, alias="telefono")

    # Campos comunes
    language: str = Field(default="darija_latin")
    business_type: str = Field(default="restaurant")
    zone: str = Field(default="centro")
    plan: str = Field(default="basic")

    class Config:
        populate_by_name = True  # ← Permite usar alias o nombre real

    # Helper para mapear español → inglés
    def to_db_dict(self) -> dict:
        return {
            "name": self.nombre or self.name or "Sin nombre",
            "owner_phone": self.telefono or self.owner_phone or "",
            "language": self.language,
            "business_type": self.business_type,
            "zone": self.zone,
            "plan": self.plan,
        }

class MenuItemCreate(BaseModel):
    # Acepta: client_id O menu_id, dish_name O nombre, etc.
    client_id: Optional[str] = Field(None, alias="client_id")
    menu_id: Optional[str] = Field(None, alias="menu_id")
    category: str = Field(default="")
    dish_name: Optional[str] = Field(None, alias="dish_name")
    nombre: Optional[str] = Field(None, alias="nombre")
    description: str = Field(default="")
    descripcion: Optional[str] = Field(None, alias="descripcion")
    price: Optional[int] = Field(None, alias="price")
    precio: Optional[int] = Field(None, alias="precio")
    is_available: bool = Field(default=True)
    disponible: Optional[bool] = Field(None, alias="disponible")
    
    class Config:
        populate_by_name = True
    
    def to_db_dict(self, client_id_fallback: str) -> dict:
        return {
            "client_id": self.menu_id or self.client_id or client_id_fallback,
            "category": self.category,
            "dish_name": self.nombre or self.dish_name or "Sin nombre",
            "description": self.descripcion or self.description,
            "price": self.precio or self.price or 0,
            "is_available": self.disponible if self.disponible is not None else self.is_available,
        }

# ── NLP: Darija Dual + 6 idiomas ───────────────────────────────────────
class DarijaNLP:
    # Darija en script árabe (letras árabes)
    DARIJA_ARABIC = [
        "سلام", "مرحبا", "بغيت", "شنو", "واش", "كاين", "غير", "دابا", "علاش",
        "ف", "و", "ولا", "عليك", "عندي", "حتا", "قلبي", "بزاف", "شوية"
    ]
    # Darija romanizado (latino)
    DARIJA_LATIN = [
        "salam", "marhba", "bghit", "chno", "wash", "kayn", "ghir", "daba", "3lach",
        "f", "w", "wlla", "3lik", "3ndi", "7ta", "9albi", "bzaf", "chwiya",
        "kifach", "wakha", "ayeh", "la", "kho", "khti", "merci bzzaf", "vale safi"
    ]
    
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
        
        # === PRIORIDAD 1: Darija árabe (script) ===
        if any(char in text for char in ["\u0600", "\u0601", "\u0602", "\u0603"]):
            if any(kw in text for kw in cls.DARIJA_ARABIC):
                return "darija_arabic"
            return "arabic"  # Árabe estándar
        
        # === PRIORIDAD 2: Darija latino (romanizado) ===
        if any(kw in text_lower for kw in cls.DARIJA_LATIN):
            return "darija_latin"
        
        # === Otros idiomas ===
        for lang, keywords in cls.LANG_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return lang
        
        return "darija_latin"  # Fallback: asumimos Tetouan
    
    @classmethod
    def detect_intent(cls, text: str) -> str:
        text_lower = text.strip().lower()
        intents = {
            "greeting": ["salam", "marhba", "مرحبا", "سلام", "bonjour", "hola", "hello"],
            "menu": ["menu", "lmenu", "lmaakoul", "قائمة", "منيو", "carte", "carta", "food"],
            "order": ["bghit", "3tini", "أريد", "أطلب", "commande", "pedir", "order"],
            "help": ["3awni", "kifach", "مساعدة", "كيف", "ayuda", "help", "aide"],
            "cancel": ["lghi", "batal", "إلغاء", "cancel", "annuler"],
            "confirm": ["wakha", "ayeh", "نعم", "oui", "yes", "vale"],
        }
        if text_lower.isdigit():
            return "add_item"
        for intent, keywords in intents.items():
            if any(kw in text_lower for kw in keywords):
                return intent
        return "unknown"

# ── Bot Logic: Respuestas Darija Dual ─────────────────────────────────
class BotLogic:
    MSG = {
        "greeting": {
            "darija_arabic": "👋 *سلام! مرحبا بيك* 😊\nأنا مساعد الطلبات ديالك فـ WhatsApp.\n🍴 *شوف المنيو* — كتب \"menu\"\n📍 *المطاعم* — كتب \"lista\"\n❓ *مساعدة* — كتب \"ayuda\"",
            "darija_latin": "👋 *Salam! Marhba bik* 😊\nAna l'assistant dyalek dyal l-commandes 3la WhatsApp.\n🍴 *Shuf lmenu* — kteb \"menu\"\n📍 *Restaurants* — kteb \"lista\"\n❓ *M3awda* — kteb \"ayuda\"",
            "arabic": "👋 *السلام عليكم* 😊\nأنا مساعدك للطلبات عبر واتساب.\n🍴 *عرض القائمة* — اكتب \"menu\"\n📍 *المطاعم* — اكتب \"lista\"",
            "spanish": "👋 *¡Hola! Bienvenido* 😊\nSoy tu asistente de pedidos por WhatsApp.\n🍴 *Ver menú* — escribe \"menu\"\n📍 *Restaurantes* — escribe \"lista\"",
            "french": "👋 *Bonjour! Bienvenue* 😊\nJe suis votre assistant de commandes WhatsApp.\n🍴 *Voir le menu* — tapez \"menu\"\n📍 *Restaurants* — tapez \"lista\"",
            "english": "👋 *Hello! Welcome* 😊\nI'm your WhatsApp ordering assistant.\n🍴 *See menu* — type \"menu\"\n📍 *Restaurants* — type \"lista\"",
        },
        "help": {
            "darija_arabic": "📋 *شنو تقدر دير:*\n• *menu* — شوف الماكول\n• *lista* — شوف المطاعم",
            "darija_latin": "📋 *Chno tقدر dir:*\n• *menu* — shuf lmaakoul\n• *lista* — shuf restaurants",
            "spanish": "📋 *Comandos:*\n• *menu* — ver comida\n• *lista* — ver restaurantes",
        },
        "unknown": {
            "darija_arabic": "😅 ما فهمتكش. كتب *menu* باش تشوف الماكول.",
            "darija_latin": "😅 Ma fhamteksh. Kteb *menu* bach tchouf lmaakoul.",
            "spanish": "😅 No te entendí. Escribe *menu* para ver los platos.",
        },
    }
    
    @classmethod
    def _get(cls, key: str, lang: str) -> str:
        msgs = cls.MSG.get(key, cls.MSG["unknown"])
        return msgs.get(lang, msgs.get("darija_latin", ""))
    
    @classmethod
    async def process_message(cls, from_number: str, message_text: str) -> str:
        lang = DarijaNLP.detect_language(message_text)
        intent = DarijaNLP.detect_intent(message_text)
        logger.info(f"[BOT] {from_number[-4:]} | lang={lang} | intent={intent}")
        
        # Guardar mensaje (silencioso si falla)
        try:
            sb = get_supabase()
            sb.table("messages").insert({
                "from_number": from_number,
                "message_text": message_text,
                "direction": "incoming",
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
        except: pass
        
        # Respuestas por intención
        if intent in ["greeting", "help", "cancel", "confirm"]:
            return cls._get(intent, lang)
        if intent == "menu":
            return await cls.get_menu(from_number, lang)
        if intent == "lista":
            return await cls.get_restaurants(lang)
        return cls._get("unknown", lang)
    
    @classmethod
    async def get_restaurants(cls, lang: str = "darija_latin") -> str:
        try:
            sb = get_supabase()
            res = sb.table("clients").select("name,business_type,zone").eq("is_active", True).limit(10).execute()
            if not res.data:
                return cls._get("no_restaurants", lang)
            lines = [f"🏪 *Restaurantes en Tetouan:*"]
            for r in res.data:
                lines.append(f"  • *{r['name']}* — {r.get('zone','?')}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error: {e}")
            return "Error al cargar restaurantes."
    
    @classmethod
    async def get_menu(cls, from_number: str, lang: str = "darija_latin") -> str:
        try:
            sb = get_supabase()
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
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error menú: {e}")
            return "Error al cargar menú."

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
    logger.info("[STARTUP] Orquestrator ISA v2.2 iniciando...")
    yield
    logger.info("[SHUTDOWN] Orquestrator ISA detenido.")

# ── FastAPI App ─────────────────────────────────────────────────────
app = FastAPI(title="Orquestrator ISA", description="WhatsApp Bot Darija Dual + 6 idiomas", version="2.2.0", lifespan=lifespan)

# ← CORS MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "ok", "service": "Orquestrator ISA", "version": "2.2.0", "languages": ["darija_arabic", "darija_latin", "arabic", "french", "spanish", "english", "german", "turkish"]}

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

# ── API Endpoints CORREGIDOS ───────────────────────────────────────

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
        data = client.to_db_dict()  # ← Usa el helper
        data.update({
            "is_active": True, "total_messages": 0, "total_orders": 0,
            "whatsapp_status": "contactar",
            "trial_ends_at": (datetime.utcnow() + timedelta(days=20)).date().isoformat(),
        })
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

@app.post("/api/menu")  # ← CREAR PLATO
async def create_menu_item(item: MenuItemCreate):
    try:
        sb = get_supabase()
        # Si no viene client_id, buscar el primer restaurante activo
        client_id = item.menu_id or item.client_id
        if not client_id:
            cr = sb.table("clients").select("id").eq("is_active", True).limit(1).execute()
            if cr.data:
                client_id = cr.data[0]["id"]
        if not client_id:
            raise HTTPException(400, detail="No se encontró client_id ni restaurante activo")
        
        data = item.to_db_dict(client_id)
        res = sb.table("menu_items").insert(data).execute()
        return {"menu_item": res.data[0]}
    except Exception as e:
        logger.error(f"[API] Error create_menu_item: {e}")
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
