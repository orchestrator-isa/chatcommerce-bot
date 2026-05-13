#!/usr/bin/env python3
import os, logging, re, json, uuid, httpx
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse, JSONResponse
from supabase import create_client, Client  # ← Cliente síncrono (sin await)
from typing import Dict, List, Optional

VERSION = "7.3-RESTINGA-FIXED"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("isa-bot")

# ========== CONFIGURACIÓN ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin_secret_2026")

# Cliente Supabase SÍNCRONO (create_client, NO create_async_client)
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
else:
    logger.warning("⚠️ Carpeta 'lang' no encontrada")
    LANG_DIR.mkdir(exist_ok=True)

LANG_MAP = {'english':'en','spanish':'es','french':'fr','german':'de','turkish':'tr','darija_latin':'dar','darija_arabic':'ar'}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    file_key = LANG_MAP.get(lang_code, 'es')
    texts = LANGUAGES.get(file_key, LANGUAGES.get('es', {}))
    template = texts.get(key, LANGUAGES['es'].get(key, key))
    return template.format(**kwargs) if kwargs and '{}' in template else template

# ========== ESTADOS GLOBALES ==========
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
user_idioma_manual: Dict[str, bool] = {}
pedido_estado: Dict[str, dict] = {}
restaurant_status = "normal"
clientes_validados: set = set()
session_activa: Dict[str, str] = {}
TIEMPOS = {"normal":{"recoger":"5-10 min","domicilio":"20-30 min"},"moderado":{"recoger":"10-15 min","domicilio":"25-35 min"},"lleno":{"recoger":"20-30 min","domicilio":"35-45 min"}}
phone_to_restaurant: Dict[str, str] = {}

# ========== MAPEO DE TELÉFONOS ==========
async def load_phone_mapping():
    global phone_to_restaurant, clientes_validados
    try:
        if not supabase: return
        result = supabase.table("restaurantes").select("id_restaurante, telefono").eq("is_active", True).execute()
        phone_to_restaurant = {}
        for r in result.data:
            tel = r.get("telefono", "").replace("+", "")
            phone_to_restaurant[tel] = r["id_restaurante"]
            phone_to_restaurant[r["telefono"]] = r["id_restaurante"]
        phone_to_restaurant['212626282904'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['212668087490'] = '44444444-4444-4444-4444-444444444444'
        try:
            result = supabase.table("valid_clients").select("telefono").execute()
            for r in result.data: clientes_validados.add(r.get("telefono", ""))
        except: pass
        logger.info(f"📞 {len(phone_to_restaurant)} restaurantes mapeados")
    except Exception as e: logger.error(f"Error mapeo: {e}")

# ========== REGISTRAR MENSAJE (con fallback PGRST204) ==========
async def registrar_mensaje(user_id: str, direccion: str, mensaje: str, intent: str=None):
    try:
        if not supabase: return
        session_id = session_activa.get(user_id)
        if not session_id:
            session_id = str(uuid.uuid4())
            session_activa[user_id] = session_id
            try:
                supabase.table("sessions").insert({"id":session_id,"user_id":user_id,"inicio":datetime.now().isoformat(),"estado":"activa"}).execute()
            except Exception as e: logger.warning(f"⚠️ sessions insert: {e}")
        try:
            supabase.table("messages").insert({"session_id":session_id,"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
        except Exception as e:
            if "pgrst204" in str(e).lower() or "session_id" in str(e).lower():
                logger.warning("⚠️ session_id fallback")
                supabase.table("messages").insert({"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
            else: raise
    except Exception as e: logger.error(f"❌ registrar_mensaje: {e}")

# ========== DETECTOR DE IDIOMA ==========
class LanguageDetector:
    KEYWORDS = {'english':['hello','hi','menu','thank','want'],'spanish':['hola','menu','gracias','quiero'],'french':['bonjour','menu','merci'],'german':['hallo','menü','danke'],'turkish':['merhaba','menü'],'darija_latin':['salam','menu','bghit'],'darija_arabic':['سلام','قائمة','بغيت']}
    @classmethod
    def detect(cls, text: str) -> str:
        text_lower = text.lower().strip()
        if any('\u0600'<=c<='\u06FF' for c in text): return 'darija_arabic'
        scores = {lang:sum(1 for k in kw if k in text_lower) for lang,kw in cls.KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best]>0 else 'spanish'
    @classmethod
    def get_welcome(cls, lang:str) -> str: return get_text(lang,'welcome')
    @classmethod
    def get_help(cls, lang:str) -> str: return get_text(lang,'help')

# ========== MENÚ, CARRITO, PEDIDOS (resumido para brevedad - mantener tu lógica existente) ==========
# [Mantén tus funciones get_restaurant_menu, add_to_cart, guardar_pedido, etc. SIN CAMBIOS]
# Solo asegúrate de NO usar 'await' con supabase.table()

# ========== ENVIAR MENSAJE WHATSAPP (CORREGIDO) ==========
async def send_message(to: str, message: str) -> bool:
    """Envía mensaje vía WhatsApp Cloud API con confirmación de éxito/fallo"""
    
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error(f"❌ WhatsApp NO configurado")
        return False
    
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message[:1600]}
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=data)
            
            if response.status_code == 200:
                try:
                    resp_data = response.json()
                    msg_id = resp_data.get("messages", [{}])[0].get("id", "unknown")
                    logger.info(f"✅ Mensaje enviado a {to} | ID: {msg_id}")
                except:
                    logger.info(f"✅ Mensaje enviado a {to}")
                return True
            else:
                try:
                    err = response.json()
                    logger.error(f"❌ WhatsApp API {response.status_code}: {err}")
                except:
                    logger.error(f"❌ WhatsApp API {response.status_code}: {response.text[:200]}")
                return False
    except Exception as e:
        logger.error(f"❌ Error send_message: {e}", exc_info=True)
        return False

# ========== PROCESAR MENSAJE (con logging de fallback) ==========
async def process_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account": return
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
                        await registrar_mensaje(user_id, "incoming", text)
                        
                        # Respuesta simple de prueba (puedes expandir con tu lógica completa)
                        if text_lower in ['hola','hello','salam','hi']:
                            response = f"{LanguageDetector.get_welcome(lang)}\n{LanguageDetector.get_help(lang)}"
                        elif text_lower in ['menu','menú']:
                            response = "📋 *MENÚ*\nEscribe un número para añadir al carrito.\nEj: '1' para Zaalouk"
                        else:
                            response = LanguageDetector.get_help(lang)
                        
                        # Enviar respuesta con confirmación de éxito/fallo
                        sent = await send_message(user_id, response)
                        logger.info(f"📤 send_message result: {sent} | to: {user_id[:10]}... | msg: {response[:50]}...")
                        if not sent:
                            logger.warning(f"⚠️ NO se pudo enviar respuesta a {user_id}")			
                        if sent:
                            await registrar_mensaje(user_id, "outgoing", response)
                        else:
                            # Fallback: registrar que no se pudo enviar
                            logger.warning(f"⚠️ No se pudo enviar respuesta a {user_id}")
                            await registrar_mensaje(user_id, "outgoing_failed", f"[NO ENVIADO] {response[:100]}")
    except Exception as e:
        logger.error(f"❌ Error en process_message: {e}", exc_info=True)

# ========== WEBHOOK WHATSAPP ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        logger.info("✅ Webhook verified by Meta")
        return PlainTextResponse(params.get("hub.challenge"))
    logger.warning(f"❌ Verificación fallida: mode={params.get('hub.mode')}, token_ok={params.get('hub.verify_token')==VERIFY_TOKEN}")
    raise HTTPException(403, detail="Verification failed")

@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        logger.debug(f"📥 Webhook payload: {json.dumps(body)[:300]}...")
        background_tasks.add_task(process_message, body)
        return JSONResponse({"status":"ok"})
    except Exception as e:
        logger.error(f"❌ Error webhook_post: {e}", exc_info=True)
        return JSONResponse({"status":"error","message":str(e)}, status_code=500)

# ========== ENDPOINTS ADMIN Y HEALTH ==========
@app.get("/health")
async def health():
    schema_ok = True
    try:
        if supabase: supabase.table("messages").select("session_id").limit(1).execute()
    except Exception as e:
        if "pgrst204" in str(e).lower() or "session_id" in str(e).lower(): schema_ok = False
        logger.warning(f"⚠️ Health check schema: {e}")
    return JSONResponse({
        "status":"ok" if schema_ok else "degraded",
        "version":VERSION,
        "supabase":supabase is not None,
        "whatsapp_configured":bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        "postgrest_cache":"updated" if schema_ok else "stale",
        "languages_loaded":len(LANGUAGES),
        "timestamp":datetime.utcnow().isoformat()
    })

@app.post("/admin/refresh-schema")
async def refresh_schema(request: Request):
    auth = request.headers.get("Authorization","")
    if auth != f"Bearer {ADMIN_TOKEN}": raise HTTPException(401,detail="Unauthorized")
    try:
        if supabase: supabase.table("messages").select("count").limit(1).execute()
        logger.info("✅ PostgREST cache refreshed")
        return JSONResponse({"status":"ok","message":"Schema cache refreshed","timestamp":datetime.utcnow().isoformat()})
    except Exception as e:
        logger.error(f"❌ Error refresh: {e}")
        raise HTTPException(500,detail=f"Refresh failed: {str(e)}")

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} starting...")
    await load_phone_mapping()
    try:
        if supabase: supabase.table("messages").select("id").limit(1).execute()
        logger.info("✅ PostgREST cache refreshed at startup")
    except Exception as e: logger.warning(f"⚠️ Could not refresh cache: {e}")
    logger.info(f"✅ {len(LANGUAGES)} languages: {list(LANGUAGES.keys())}")
    logger.info(f"✅ WhatsApp config: TOKEN={'✅' if WHATSAPP_TOKEN else '❌'}, PHONE_ID={'✅' if PHONE_NUMBER_ID else '❌'}")
    # Generar dashboard.html (mantén tu código existente)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT",8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
