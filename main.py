#!/usr/bin/env python3
import os, logging, re, json, uuid, httpx
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse, JSONResponse
from supabase import create_client, Client
from typing import Dict, List, Optional

VERSION = "7.3-RESTINGA-OPTIMIZED"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("isa-bot")

# ========== CONFIGURACIÓN ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin_secret_2026")

# Cliente síncrono (NO usar await con supabase.table())
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
        try:
            with open(lang_file, "r", encoding="utf-8") as f:
                LANGUAGES[lang_file.stem] = json.load(f)
            logger.info(f"✅ Idioma cargado: {lang_file.stem}")
        except Exception as e:
            logger.error(f"❌ Error {lang_file}: {e}")
else:
    LANG_DIR.mkdir(exist_ok=True)

LANG_MAP = {'english':'en','spanish':'es','french':'fr','german':'de','turkish':'tr','darija_latin':'dar','darija_arabic':'ar'}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    file_key = LANG_MAP.get(lang_code, 'es')
    texts = LANGUAGES.get(file_key, LANGUAGES.get('es', {}))
    template = texts.get(key, LANGUAGES['es'].get(key, key))
    # ✅ FIX: Intenta formatear siempre que haya kwargs, sin verificar '{}'
    if kwargs:
        try: return template.format(**kwargs)
        except: return template
    return template

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

# ========== DETECTOR DE IDIOMA ==========
class LanguageDetector:
    KEYWORDS = {'english':['hello','hi','menu','thank'],'spanish':['hola','menu','gracias','quiero'],'french':['bonjour','menu','merci'],'german':['hallo','menü','danke'],'turkish':['merhaba','menü'],'darija_latin':['salam','menu','bghit'],'darija_arabic':['سلام','قائمة','بغيت']}
    @classmethod
    def detect(cls, text: str) -> str:
        t = text.lower().strip()
        if any('\u0600'<=c<='\u06FF' for c in text): return 'darija_arabic'
        scores = {l:sum(1 for k in kw if k in t) for l,kw in cls.KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best]>0 else 'spanish'
    @classmethod
    def get_welcome(cls, lang:str) -> str: return get_text(lang,'welcome')
    @classmethod
    def get_help(cls, lang:str) -> str: return get_text(lang,'help')

# ========== MAPEO TELÉFONOS ==========
async def load_phone_mapping():
    global phone_to_restaurant, clientes_validados
    if not supabase: return
    try:
        res = supabase.table("restaurantes").select("id_restaurante, telefono").eq("is_active", True).execute()
        phone_to_restaurant = {}
        for r in res.data:
            t = r.get("telefono","").replace("+","")
            phone_to_restaurant[t] = r["id_restaurante"]
            phone_to_restaurant[r["telefono"]] = r["id_restaurante"]
        phone_to_restaurant['212626282904'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['212668087490'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['5217225529803'] = '44444444-4444-4444-4444-444444444444'
        try:
            res2 = supabase.table("valid_clients").select("telefono").execute()
            for r in res2.data: clientes_validados.add(r.get("telefono",""))
        except: pass
        logger.info(f"📞 {len(phone_to_restaurant)} restaurantes mapeados")
    except Exception as e: logger.error(f"Error mapeo: {e}")

# ========== REGISTRAR MENSAJE ==========
async def registrar_mensaje(user_id: str, direccion: str, mensaje: str, intent: str=None):
    if not supabase: return
    try:
        sid = session_activa.get(user_id)
        if not sid:
            sid = str(uuid.uuid4()); session_activa[user_id] = sid
            supabase.table("sessions").insert({"id":sid,"user_id":user_id,"inicio":datetime.now().isoformat(),"estado":"activa"}).execute()
        try:
            supabase.table("messages").insert({"session_id":sid,"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
        except Exception as e:
            if "pgrst204" in str(e).lower() or "session_id" in str(e).lower():
                supabase.table("messages").insert({"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
    except Exception as e: logger.error(f"❌ registrar: {e}")

# ========== MENÚ ==========
async def get_restaurant_menu(client_id: str, lang: str = 'spanish', waba: bool = True) -> tuple:
    if not supabase: return "❌ Error de conexión", []
    try:
        q = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True)
        if waba: q = q.eq("show_on_waba", True)
        res = q.execute()
        if not res.data: return "📋 *MENÚ*\nNo hay platos disponibles.", []
        lines = ["📋 *MENÚ RESTINGA*", ""]
        for i, item in enumerate(res.data, 1):
            price_txt = "🆓 GRATIS con bebida" if item['price']==0 else f"{item['price']} MAD"
            lines.append(f"{i}. 🍽️ *{item['dish_name']}* — {price_txt}")
            if item.get('description'): lines.append(f"   📝 {item['description']}")
            lines.append("")
        return "\n".join(lines), res.data
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error cargando menú", []

# ========== CARRITO ==========
async def add_to_cart(user_id: str, idx: int, cant: int, client_id: str, lang: str) -> str:
    _, platos = await get_restaurant_menu(client_id, lang, waba=True)
    if not platos or idx > len(platos): return get_text(lang, 'help')
    sel = platos[idx-1]
    if user_id not in carts: carts[user_id] = []
    if sel["price"]==0 and not any(i["price"]>0 for i in carts.get(user_id,[])):
        return f"📌 *{sel['dish_name']}* es GRATIS con bebida u otro producto. Añade una bebida primero (ej: 18. Té a la menta)"
    for _ in range(cant): carts[user_id].append({"name":sel["dish_name"],"price":sel["price"]})
    total = sum(i["price"] for i in carts[user_id])
    return get_text(lang, 'added_to_cart', cantidad=cant, nombre=sel['dish_name'], total=total)

async def get_cart(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]: return get_text(lang, 'cart_empty')
    items = {}
    for i in carts[user_id]:
        if i["name"] not in items: items[i["name"]] = {"price":i["price"],"cant":0}
        items[i["name"]]["cant"] += 1
    total = sum(i["price"] for i in carts[user_id])
    lines = []
    for n, d in items.items():
        if d["price"]==0: lines.append(f"• {n} — 🆓 GRATIS (con bebida)")
        elif d["cant"]>1: lines.append(f"• {n} x{d['cant']} — {d['cant']*d['price']} MAD")
        else: lines.append(f"• {n} — {d['price']} MAD")
    items_text = "\n".join(lines)  # ✅ FIX: fuera del f-string
    return f"🛒 *TU PEDIDO*\n{items_text}\n💰 *TOTAL: {total} MAD*\nEscribe *CONFIRMAR* para finalizar."

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts: carts[user_id] = []
    return get_text(lang, 'cart_empty')

# ========== ENVIAR PDF ==========
async def enviar_menu_pdf(to: str, lang: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    pdf_map = {'spanish':'menu_es.pdf','english':'menu_en.pdf','french':'menu_fr.pdf','darija_latin':'menu_dar.pdf','darija_arabic':'menu_ar.pdf','es':'menu_es.pdf','en':'menu_en.pdf','fr':'menu_fr.pdf','dar':'menu_dar.pdf','ar':'menu_ar.pdf'}
    pdf_file = pdf_map.get(lang, 'menu_es.pdf')
    # ⚠️ Cambia esta URL si tu dominio de Render cambia en producción
    base_url = os.getenv("RENDER_EXTERNAL_URL", "https://mi-bot-restinga-test.onrender.com")
    pdf_url = f"{base_url}/static/{pdf_file}"
    
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to,"type":"document","document":{"link":pdf_url,"filename":f"Menu_Restinga_{lang}.pdf","caption":"📋 Menú completo de Restinga Restaurant"}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(url, headers=headers, json=data)
            if r.status_code == 200:
                logger.info(f"✅ PDF {pdf_file} enviado a {to}")
                return True
            logger.error(f"❌ Error PDF: {r.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"❌ Error enviar_menu_pdf: {e}")
        return False

# ========== ENVIAR WHATSAPP ==========
async def send_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":message[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(url, headers=headers, json=data)
            if r.status_code == 200:
                logger.info(f"✅ Enviado a {to}")
                return True
            logger.error(f"❌ WA {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"❌ Error send_message: {e}")
        return False

# ========== PROCESAR MENSAJE ==========
async def process_message(body: dict):
    if body.get("object") != "whatsapp_business_account": return
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            val = change.get("value", {})
            meta = val.get("metadata", {})
            phone = meta.get("display_phone_number", "").replace("+","")
            client_id = phone_to_restaurant.get(phone, "44444444-4444-4444-4444-444444444444")
            
            for msg in val.get("messages", []):
                if msg.get("type") != "text": continue
                user_id = msg.get("from")
                txt = msg.get("text", {}).get("body", "").strip()
                tl = txt.lower()
                lang = user_lang.get(user_id, LanguageDetector.detect(txt))
                await registrar_mensaje(user_id, "incoming", txt)
                fase = pedido_estado.get(user_id, {}).get("fase", "inicio")

                if fase == "seleccion_idioma":
                    mapas = {'1':'spanish','2':'english','3':'french','4':'darija_latin','5':'darija_arabic'}
                    if txt in mapas:
                        user_lang[user_id] = mapas[txt]; user_idioma_manual[user_id] = True
                        resp = f"{LanguageDetector.get_welcome(user_lang[user_id])}\n{LanguageDetector.get_help(user_lang[user_id])}"
                        await send_message(user_id, resp); await registrar_mensaje(user_id, "outgoing", resp)
                        pedido_estado.pop(user_id, None)
                    else: await send_message(user_id, "❌ Elige 1-5.")
                    continue

                if tl in ['hola','hello','salam','hi']:
                    carts[user_id] = []; pedido_estado.pop(user_id, None)
                    if user_id not in user_lang or not user_idioma_manual.get(user_id, False):
                        await send_message(user_id, "🌍 *Bienvenido*\n1. 🇪🇸 Español 2. 🇬🇧 English 3. 🇫🇷 Français 4. 🇲🇦 Darija 5. 🇲🇦 العربية")
                        pedido_estado[user_id] = {"fase":"seleccion_idioma"}
                    else:
                        resp = f"{LanguageDetector.get_welcome(lang)}\n{LanguageDetector.get_help(lang)}"
                        await send_message(user_id, resp)
                    continue

                if tl in ['menu','menú']:
                    mt, _ = await get_restaurant_menu(client_id, lang, waba=True)
                    await send_message(user_id, mt)
                    await enviar_menu_pdf(user_id, lang)  # ✅ PDF automático
                    await send_message(user_id, LanguageDetector.get_help(lang))
                    await registrar_mensaje(user_id, "outgoing", "menu+pdf")
                    continue

                if tl in ['pedido','order','cart']:
                    resp = await get_cart(user_id, lang)
                    await send_message(user_id, resp)
                    continue

                if tl in ['confirmar','confirm']:
                    if not carts.get(user_id) or sum(i['price'] for i in carts[user_id])<=0:
                        await send_message(user_id, "⚠️ Carrito vacío")
                    else:
                        pedido_estado[user_id] = {"fase":"entrega"}
                        await send_message(user_id, get_text(lang, 'delivery_type'))
                    continue

                if tl.isdigit():
                    resp = await add_to_cart(user_id, int(tl), 1, client_id, lang)
                    await send_message(user_id, resp)
                    continue

                if re.match(r'(eliminar|quitar)\s+', tl):
                    await send_message(user_id, await clear_cart(user_id, lang))
                    continue

                await send_message(user_id, LanguageDetector.get_help(lang))

# ========== WEBHOOKS ==========
@app.get("/api/whatsapp/webhook")
async def wb_get(req: Request):
    p = req.query_params
    if p.get("hub.mode")=="subscribe" and p.get("hub.verify_token")==VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge"))
    raise HTTPException(403)

@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    try:
        bg.add_task(process_message, await req.json())
        return {"status":"ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status":"error"}, 500

# ========== HEALTH & ADMIN ==========
@app.get("/health")
async def health():
    return {"status":"ok","version":VERSION,"supabase":supabase is not None,"whatsapp":bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),"lang":len(LANGUAGES)}

@app.post("/admin/refresh-schema")
async def refresh(req: Request):
    if req.headers.get("Authorization")!=f"Bearer {ADMIN_TOKEN}": raise HTTPException(401)
    if supabase: supabase.table("messages").select("count").limit(1).execute()
    return {"status":"ok","msg":"cache refreshed"}

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} starting...")
    await load_phone_mapping()
    if supabase: supabase.table("messages").select("id").limit(1).execute()
    logger.info(f"✅ {len(LANGUAGES)} languages: {list(LANGUAGES.keys())}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT",8000)), reload=False)
