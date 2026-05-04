#!/usr/bin/env python3
"""Orquestrator ISA — ChatCommerce Bot v3.0
Corregido: GET /api/platos/{id} y alias /api/menu/{id} funcionando
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import httpx

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("isa-bot")

# ──────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT VARIABLES
# ──────────────────────────────────────────────────────────────────────────────
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
WEBHOOK_PREFIX = os.getenv("WEBHOOK_PREFIX", "/api/whatsapp/webhook")

# ──────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT
# ──────────────────────────────────────────────────────────────────────────────
_supabase: Optional[Client] = None

def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no configurados")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[SUPABASE] Conectado")
    return _supabase

# ──────────────────────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ──────────────────────────────────────────────────────────────────────────────
class ClientCreate(BaseModel):
    name: Optional[str] = None
    nombre: Optional[str] = None
    owner_phone: Optional[str] = None
    telefono: Optional[str] = None
    language: str = "darija_latin"
    
    def to_db(self) -> dict:
        return {
            "name": self.nombre or self.name or "Sin nombre",
            "owner_phone": self.telefono or self.owner_phone or "",
            "language": self.language,
            "is_active": True,
            "trial_ends_at": (datetime.utcnow() + timedelta(days=20)).date().isoformat(),
        }

# ──────────────────────────────────────────────────────────────────────────────
# NLP MULTI-IDIOMA (8 idiomas)
# ──────────────────────────────────────────────────────────────────────────────
class NLP:
    KEYWORDS = {
        'darija_arabic': ['سلام', 'مرحبا', 'بغيت', 'شنو', 'قائمة'],
        'darija_latin': ['salam', 'marhba', 'bghit', 'menu'],
        'spanish': ['hola', 'gracias', 'quiero', 'menú', 'menu'],
        'french': ['bonjour', 'merci', 'menu'],
        'english': ['hello', 'thank', 'want', 'menu'],
        'german': ['hallo', 'danke', 'menü'],
        'turkish': ['merhaba', 'menü']
    }
    
    RESPONSES = {
        'darija_arabic': {'welcome': '👋 سلام! مرحبا بيك\n🍴 اكتب menu لمشاهدة الأطباق', 'menu_prompt': '📋 القائمة:\n{}', 'not_understood': '😅 ما فهمتكش. اكتب menu'},
        'darija_latin': {'welcome': '👋 Salam! Marhba bik\n🍴 Kteb menu bach tchouf lmaakoulat', 'menu_prompt': '📋 Lmaakoulat:\n{}', 'not_understood': '😅 Ma fhamtekch. Kteb menu'},
        'spanish': {'welcome': '👋 ¡Hola! Bienvenido\n🍴 Escribe menu para ver los platos', 'menu_prompt': '📋 Menú:\n{}', 'not_understood': '😅 No te entendí. Escribe menu'},
        'french': {'welcome': '👋 Bonjour! Bienvenue\n🍴 Tapez menu pour voir les plats', 'menu_prompt': '📋 Menu:\n{}', 'not_understood': '😅 Je n\'ai pas compris. Tapez menu'},
        'english': {'welcome': '👋 Hello! Welcome\n🍴 Type menu to see dishes', 'menu_prompt': '📋 Menu:\n{}', 'not_understood': '😅 I didn\'t understand. Type menu'},
        'german': {'welcome': '👋 Hallo! Willkommen\n🍴 Gib menu ein für Gerichte', 'menu_prompt': '📋 Speisekarte:\n{}', 'not_understood': '😅 Nicht verstanden. Gib menu ein'},
        'turkish': {'welcome': '👋 Merhaba! Hoş geldiniz\n🍴 Yemekler için menu yazın', 'menu_prompt': '📋 Menü:\n{}', 'not_understood': '😅 Anlamadım. menu yazın'}
    }
    
    @classmethod
    def detect(cls, text: str) -> str:
        text_lower = text.lower().strip()
        for lang, keywords in cls.KEYWORDS.items():
            if any(k in text_lower for k in keywords):
                return lang
        return 'darija_latin'
    
    @classmethod
    def get_response(cls, lang: str, intent: str, data: dict = None) -> str:
        responses = cls.RESPONSES.get(lang, cls.RESPONSES['darija_latin'])
        if intent == 'welcome':
            return responses['welcome']
        elif intent == 'menu':
            menu_text = data.get('menu_text', 'No hay platos') if data else 'No hay platos'
            return responses['menu_prompt'].format(menu_text)
        return responses['not_understood']
    
    @classmethod
    async def get_restaurant_menu(cls, client_id: str, lang: str) -> str:
        try:
            sb = get_supabase()
            res = sb.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
            if not res.data:
                return cls.get_response(lang, 'menu', {'menu_text': 'No hay platos disponibles'})
            menu_lines = [f"• *{item['dish_name']}* - {item['price']} dhs" for item in res.data]
            return cls.get_response(lang, 'menu', {'menu_text': "\n".join(menu_lines)})
        except Exception as e:
            logger.error(f"[MENU] Error: {e}")
            return "❌ Error al cargar el menú"
    
    @classmethod
    async def reply(cls, lang: str, text: str, client_id: str = None) -> str:
        text_lower = text.lower().strip()
        if 'menu' in text_lower:
            if client_id:
                return await cls.get_restaurant_menu(client_id, lang)
            return "❌ Restaurante no identificado"
        elif any(g in text_lower for g in ['hola', 'salam', 'hello', 'bonjour', 'hallo', 'merhaba']):
            return cls.get_response(lang, 'welcome')
        return cls.get_response(lang, 'not_understood')

# ──────────────────────────────────────────────────────────────────────────────
# WHATSAPP SERVICE
# ──────────────────────────────────────────────────────────────────────────────
class WA:
    BASE = "https://graph.facebook.com/v18.0"
    @classmethod
    async def send(cls, to: str, msg: str) -> dict:
        if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
            return {"error": "Config incompleta"}
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.post(f"{cls.BASE}/{PHONE_NUMBER_ID}/messages",
                    headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
                    json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": msg[:1600]}})
                return r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            except Exception as e:
                return {"error": str(e)}

# ──────────────────────────────────────────────────────────────────────────────
# WEBHOOK PROCESSING
# ──────────────────────────────────────────────────────────────────────────────
async def process_wa_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    if msg.get("type") == "text":
                        from_number = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        detected_lang = NLP.detect(text)
                        response = await NLP.reply(detected_lang, text, None)
                        await WA.send(from_number, response)
    except Exception as e:
        logger.error(f"[WA] Error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] Bot iniciando...")
    yield
    logger.info("[STOP] Bot detenido.")

app = FastAPI(title="Orquestrator ISA", version="3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root():
    return {"status": "ok", "version": "3.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "supabase": bool(SUPABASE_URL), "whatsapp": bool(WHATSAPP_TOKEN)}

@app.get(WEBHOOK_PREFIX)
async def webhook_verify(req: Request):
    p = req.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403, "Verification failed")

@app.post(WEBHOOK_PREFIX)
async def webhook_post(req: Request, bg: BackgroundTasks):
    try:
        bg.add_task(process_wa_message, await req.json())
        return JSONResponse({"status": "ok"}, 200)
    except:
        return JSONResponse({"status": "error"}, 400)

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - RESTAURANTES
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/restaurantes")
async def list_restaurantes():
    try:
        sb = get_supabase()
        res = sb.table("clients").select("*").eq("is_active", True).execute()
        restaurantes = [{"id_restaurante": r.get("id"), "nombre": r.get("name"), "telefono": r.get("owner_phone")} for r in res.data]
        return {"restaurantes": restaurantes, "count": len(restaurantes)}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/restaurantes")
async def create_restaurante(c: ClientCreate):
    try:
        sb = get_supabase()
        res = sb.table("clients").insert(c.to_db()).execute()
        return {"restaurante": res.data[0] if res.data else None}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - PLATOS (CORREGIDO - ESTE ES EL IMPORTANTE)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    """Devuelve todos los platos de un restaurante"""
    try:
        sb = get_supabase()
        logger.info(f"🔍 Buscando platos para client_id: {client_id}")
        
        response = sb.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        
        logger.info(f"✅ Encontrados {len(response.data)} platos")
        
        platos = []
        for item in response.data:
            platos.append({
                "id_plato": item.get("id"),
                "nombre": item.get("dish_name"),
                "precio": item.get("price"),
                "descripcion": item.get("description", ""),
                "is_available": item.get("is_available")
            })
        
        return {"platos": platos, "count": len(platos)}
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

@app.post("/api/platos")
async def create_plato(item: dict):
    """Crea un nuevo plato"""
    try:
        sb = get_supabase()
        data = {
            "client_id": item.get("client_id"),
            "dish_name": item.get("nombre"),
            "price": item.get("precio", 0),
            "description": item.get("descripcion", ""),
            "is_available": True
        }
        logger.info(f"📝 Creando plato: {data}")
        response = sb.table("menu_items").insert(data).execute()
        if response.data:
            return {"plato": {"id_plato": response.data[0].get("id"), "nombre": response.data[0].get("dish_name"), "precio": response.data[0].get("price")}}
        return {"plato": None}
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# ALIAS EN INGLÉS (ESTE ES EL QUE TE FALTA)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    """Alias en inglés para /api/platos/{client_id}"""
    return await get_platos(client_id)

@app.post("/api/menu")
async def create_menu_item(item: dict):
    """Alias en inglés para crear plato"""
    plato_data = {
        "client_id": item.get("client_id"),
        "nombre": item.get("dish_name"),
        "precio": item.get("price"),
        "descripcion": item.get("description", "")
    }
    return await create_plato(plato_data)

# ──────────────────────────────────────────────────────────────────────────────
# DEBUG ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/debug/todos_platos")
async def debug_todos_platos():
    try:
        sb = get_supabase()
        response = sb.table("menu_items").select("*").execute()
        return {"total": len(response.data), "platos": response.data[:20]}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/stats")
async def get_stats():
    try:
        sb = get_supabase()
        restaurantes = sb.table("clients").select("count", count="exact").execute()
        platos = sb.table("menu_items").select("count", count="exact").execute()
        return {"restaurantes": restaurantes.count, "platos_totales": platos.count}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
