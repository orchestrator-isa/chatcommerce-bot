#!/usr/bin/env python3
import os, logging, json, httpx
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse, JSONResponse
from supabase import create_client, Client
from typing import Dict, Optional

VERSION = "7.3-RESTINGA-MINIMAL"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("isa-bot")

# ========== CONFIGURACIÓN ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin_secret_2026")

supabase: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI(title="Orquestrator ISA", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ========== CARGA DE IDIOMAS ==========
LANG_DIR = Path("lang")
LANGUAGES: Dict[str, dict] = {}
if LANG_DIR.exists():
    for lang_file in LANG_DIR.glob("*.json"):
        lang_code = lang_file.stem
        try:
            with open(lang_file, "r", encoding="utf-8") as f:
                LANGUAGES[lang_code] = json.load(f)
            logger.info(f"✅ Cargado idioma: {lang_code}")
        except Exception as e:
            logger.error(f"❌ Error cargando {lang_file}: {e}")

LANG_MAP = {'english':'en','spanish':'es','french':'fr','german':'de','turkish':'tr','darija_latin':'dar','darija_arabic':'ar'}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    file_key = LANG_MAP.get(lang_code, 'es')
    texts = LANGUAGES.get(file_key, LANGUAGES.get('es', {}))
    template = texts.get(key, LANGUAGES['es'].get(key, key))
    return template.format(**kwargs) if kwargs and '{}' in template else template

# ========== ESTADOS GLOBALES ==========
carts: Dict[str, list] = {}
user_lang: Dict[str, str] = {}
pedido_estado: Dict[str, dict] = {}
restaurant_status = "normal"
phone_to_restaurant: Dict[str, str] = {
    '212626282904': '44444444-4444-4444-4444-444444444444',
    '212668087490': '44444444-4444-4444-4444-444444444444',
    '5217225529803': '44444444-4444-4444-4444-444444444444'
}

# ========== DETECTOR DE IDIOMA ==========
class LanguageDetector:
    KEYWORDS = {
        'english': ['hello','hi','menu','thank'],
        'spanish': ['hola','menu','gracias','quiero'],
        'french': ['bonjour','menu','merci'],
        'german': ['hallo','menü','danke'],
        'turkish': ['merhaba','menü'],
        'darija_latin': ['salam','menu','bghit'],
        'darija_arabic': ['سلام','قائمة','بغيت']
    }
    
    @classmethod
    def detect(cls, text: str) -> str:
        text_lower = text.lower().strip()
        if any('\u0600'<=c<='\u06FF' for c in text):
            return 'darija_arabic'
        scores = {lang:sum(1 for k in kw if k in text_lower) for lang,kw in cls.KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best]>0 else 'spanish'
    
    @classmethod
    def get_welcome(cls, lang:str) -> str:
        return get_text(lang,'welcome')
    
    @classmethod
    def get_help(cls, lang:str) -> str:
        return get_text(lang,'help')

# ========== ENVIAR MENSAJE WHATSAPP ==========
async def send_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("❌ WhatsApp NO configurado")
        return False
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=data)
            if response.status_code == 200:
                logger.info(f"✅ Mensaje enviado a {to}")
                return True
            else:
                logger.error(f"❌ WhatsApp API {response.status_code}: {response.text[:200]}")
                return False
    except Exception as e:
        logger.error(f"❌ Error send_message: {e}")
        return False

# ========== PROCESAR MENSAJE (versión mínima) ==========
async def process_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                display_phone = metadata.get("display_phone_number", "").replace("+", "")
                client_id = phone_to_restaurant.get(display_phone, "44444444-4444-4444-4444-444444444444")
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        user_id = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        text_lower = text.lower().strip()
                        lang = LanguageDetector.detect(text)
                        user_lang_code = user_lang.get(user_id, lang)
                        
                        if text_lower in ['hola','hello','salam','hi']:
                            response = f"{LanguageDetector.get_welcome(user_lang_code)}\n{LanguageDetector.get_help(user_lang_code)}"
                        elif text_lower in ['menu','menú']:
                            response = "📋 *MENÚ*\nEscribe un número para añadir al carrito.\nEj: '1' para Zaalouk"
                        else:
                            response = LanguageDetector.get_help(user_lang_code)
                        
                        sent = await send_message(user_id, response)
                        logger.info(f"📤 send_message result: {sent} | to: {user_id[:10]}...")
    except Exception as e:
        logger.error(f"❌ Error en process_message: {e}", exc_info=True)

# ========== WEBHOOK WHATSAPP ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        logger.info("✅ Webhook verified by Meta")
        return PlainTextResponse(params.get("hub.challenge"))
    raise HTTPException(403, detail="Verification failed")

@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        background_tasks.add_task(process_message, body)
        return JSONResponse({"status":"ok"})
    except Exception as e:
        logger.error(f"❌ Error webhook_post: {e}")
        return JSONResponse({"status":"error"}, status_code=500)

# ========== ENDPOINTS PÚBLICOS ==========
@app.get("/")
async def root():
    return {"status": "ok", "version": VERSION, "service": "Orquestrator ISA"}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": VERSION,
        "supabase": supabase is not None,
        "whatsapp_configured": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        "languages_loaded": len(LANGUAGES),
        "timestamp": datetime.utcnow().isoformat()
    }

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} starting...")
    logger.info(f"✅ {len(LANGUAGES)} languages: {list(LANGUAGES.keys())}")
    logger.info(f"✅ WhatsApp config: TOKEN={'✅' if WHATSAPP_TOKEN else '❌'}, PHONE_ID={'✅' if PHONE_NUMBER_ID else '❌'}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
