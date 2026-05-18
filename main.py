#!/usr/bin/env python3
import os, logging, re, json, uuid, httpx
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse, JSONResponse
from supabase import create_client, Client
from typing import Dict, List, Optional

VERSION = "8.5-STABLE"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("isa-bot")

# ========== CONFIGURACIÓN ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

supabase: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI(title="Orquestrator ISA", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ========== IDIOMAS ==========
LANG_DIR = Path("lang")
LANGUAGES: Dict[str, dict] = {}
if LANG_DIR.exists():
    for f in LANG_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh: LANGUAGES[f.stem] = json.load(fh)
        except Exception as e: logger.error(f"❌ Error cargando idioma {f}: {e}")
else: LANG_DIR.mkdir(exist_ok=True)

LANG_MAP = {'english':'en','spanish':'es','french':'fr','german':'de','turkish':'tr','darija_latin':'dar','darija_arabic':'ar'}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    file_key = LANG_MAP.get(lang_code, 'es')
    es_texts = LANGUAGES.get('es', {})
    texts = LANGUAGES.get(file_key, es_texts)
    template = texts.get(key, es_texts.get(key, key))
    if kwargs:
        try: return template.format(**kwargs)
        except: return template
    return template

# ========== ESTADOS ==========
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
pedido_estado: Dict[str, dict] = {}
restaurant_status = "normal"
clientes_validados: set = set()
TIEMPOS = {"normal":{"recoger":"5-10 min","domicilio":"20-30 min"},"moderado":{"recoger":"10-15 min","domicilio":"25-35 min"},"lleno":{"recoger":"20-30 min","domicilio":"35-45 min"}}
phone_to_restaurant: Dict[str, str] = {}

# ========== DETECTOR IDIOMA ==========
class LanguageDetector:
    KEYWORDS = {'english':['hello','hi','menu','thank'],'spanish':['hola','menu','gracias','quiero'],'french':['bonjour','menu','merci'],'german':['hallo','menü','danke'],'turkish':['merhaba','menü'],'darija_latin':['salam','menu','bghit'],'darija_arabic':['سلام','قائمة','بغيت']}
    @classmethod
    def detect(cls, text: str) -> str:
        t = text.lower().strip()
        if any('\u0600'<=c<='\u06FF' for c in text): return 'darija_arabic'
        scores = {l:sum(1 for k in kw if k in t) for l,kw in cls.KEYWORDS.items()}
        return max(scores, key=scores.get) if max(scores.values()) > 0 else 'spanish'
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
        phone_to_restaurant['212626282904'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['212668087490'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['5217225529803'] = '44444444-4444-4444-4444-444444444444'
        try:
            for r in supabase.table("valid_clients").select("telefono").execute().data:
                clientes_validados.add(r.get("telefono",""))
        except: pass
    except Exception as e: logger.error(f"Error mapeo: {e}")

# ========== MENÚ & CARRITO ==========
async def get_restaurant_menu(client_id: str, lang: str = 'spanish', waba: bool = True) -> tuple:
    if not supabase: return "❌ Error de conexión", []
    try:
        lang_key = LANG_MAP.get(lang, 'es')
        q = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True)
        if waba: q = q.eq("show_on_waba", True)
        res = q.execute()
        if not res.data: return "📋 *MENÚ*\nNo hay platos disponibles.", []
        lines = ["📋 *MENÚ RESTINGA*", ""]
        for i, item in enumerate(res.data, 1):
            trad = item.get("translations", {}) or {}
            nombre = trad.get(lang_key) or trad.get('es') or item.get("dish_name", "Plato")
            desc = trad.get(f"desc_{lang_key}") or item.get("description", "")
            p = "🆓 GRATIS con bebida" if item.get("price",0)==0 else f"{item['price']} MAD"
            lines.append(f"{i}. *{nombre}* — {p}")
            if desc: lines.append(f"   {desc}")
            lines.append("")
        txt = "\n".join(lines)
        if len(txt) > 1500:
            mid = txt[:1500].rfind("\n")
            return txt[:mid if mid>0 else 750], res.data, txt[mid if mid>0 else 750:]
        return txt, res.data, ""
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error cargando menú", [], ""

async def add_to_cart(user_id: str, idx: int, cant: int, client_id: str, lang: str) -> str:
    _, platos, _ = await get_restaurant_menu(client_id, lang, waba=True)
    if not platos or idx < 1 or idx > len(platos): return f"❌ Número inválido. El menú tiene {len(platos)} platos."
    sel = platos[idx-1]
    if user_id not in carts: carts[user_id] = []
    if sel["price"]==0 and not any(i["price"]>0 for i in carts.get(user_id,[])): return f"📌 *{sel['dish_name']}* es GRATIS con bebida."
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
    lineas = []
    for n, d in items.items():
        if d["price"]==0: lineas.append(f"• {n} — 🆓 GRATIS")
        elif d["cant"]>1: lineas.append(f"• {n} x{d['cant']} — {d['cant']*d['price']} MAD")
        else: lineas.append(f"• {n} — {d['price']} MAD")
    return f"🛒 *TU PEDIDO*\n{'\n'.join(lineas)}\n💰 *TOTAL: {total} MAD*\nEscribe *c* para confirmar."

# ========== GUARDAR PEDIDO (CORREGIDO v8.5) ==========
async def guardar_pedido(user_id: str, items: list, total: int, tipo: str, direccion: str, metodo: str) -> dict:
    try:
        if not supabase: return {"error": "DB offline", "numero": "TEMP"}
        client_id = phone_to_restaurant.get(user_id, "44444444-4444-4444-4444-444444444444")
        # ✅ NOMBRES EXACTOS DE TU BD (Supabase 'orders')
        data = {
            "client_id": client_id, 
            "customer_phone": user_id, 
            "items": json.dumps(items), 
            "total_amount": total, 
            "status": "nuevo", 
            "delivery_type": tipo, 
            "address": direccion, 
            "payment_method": metodo, 
            "created_at": datetime.now().isoformat()
        }
        # ✅ SIN .select()
        res = supabase.table("orders").insert(data).execute()
        if res.data:
            num = res.data[0].get("id", "")[-6:].upper()
            return {"numero": f"ORD-{num}", "id": res.data[0].get("id")}
        return {"error": "Failed", "numero": "???"}
    except Exception as e:
        logger.error(f"❌ Error guardar_pedido: {e}")
        return {"error": str(e), "numero": "???"}

# ========== WHATSAPP ==========
async def send_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":message[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, headers=headers, json=data)
            if r.status_code != 200:
                logger.error(f"[WA] Error {r.status_code}: {r.text}")
            return r.status_code == 200
    except Exception as e:
        logger.error(f"[WA] Excepción: {e}")
        return False

# ========== PROCESAR MENSAJE (v8.5) ==========
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

                # 1️⃣ VARIABLES CRÍTICAS (DEFINIDAS PRIMERO)
                lang = user_lang.get(user_id, LanguageDetector.detect(txt))
                fase = pedido_estado.get(user_id, {}).get("fase", "inicio")

                # 2️⃣ COMANDO Q
                if tl in ['q', 'salir', 'exit', 'reiniciar']:
                    carts[user_id] = []
                    pedido_estado[user_id] = {"fase": "seleccion_idioma"}
                    await send_message(user_id, "🌍 *Selecciona tu idioma:*\n1. 🇪🇸 Español 2. 🇬🇧 English 3. 🇫🇷 Français 4. 🇲🇦 Darija 5. 🇲🇦 العربية")
                    continue

                # 3️⃣ VALIDACIONES
                if fase in ["entrega","check_zona","pago","cash_bill"]:
                    if fase == "entrega" and tl not in ['1','2']: await send_message(user_id, "❌ Responde *1* o *2*."); continue
                    if fase == "check_zona" and tl not in ['si','sí','yes','oui','no','n']: await send_message(user_id, "❌ Responde *Sí* o *No*."); continue
                    if fase == "pago" and tl not in ['1','2','3']: await send_message(user_id, "❌ Elige *1*, *2* o *3*."); continue
                    if fase == "cash_bill" and not tl.isdigit() and 'eur' not in tl and '€' not in tl: await send_message(user_id, "❌ Ej: `100` o `20EUR`."); continue

                # 4️⃣ FASES
                if fase == "seleccion_idioma":
                    mapas = {'1':'spanish','2':'english','3':'french','4':'darija_latin','5':'darija_arabic'}
                    if txt in mapas:
                        user_lang[user_id] = mapas[txt]
                        await send_message(user_id, f"👋 ¡Hola! Bienvenido.\n{LanguageDetector.get_help(user_lang[user_id])}")
                        pedido_estado.pop(user_id, None)
                    else: await send_message(user_id, "❌ Responde *1*, *2*, *3*, *4* o *5*.")
                    continue
                if fase in ["entrega","check_zona","direccion","pago","cash_bill","transfer_pending"]:
                    total = sum(i["price"] for i in carts.get(user_id, []))
                    if fase == "entrega":
                        if tl == '1': pedido_estado[user_id] = {"tipo": "recoger", "fase": "pago"}; await send_message(user_id, f"✅ *Recoger*\n{get_text(lang, 'payment_method')}")
                        elif tl == '2': pedido_estado[user_id] = {"tipo": "domicilio", "fase": "check_zona"}; await send_message(user_id, get_text(lang, 'delivery_zone_check'))
                    elif fase == "check_zona":
                        if tl in ['si','sí','yes']: pedido_estado[user_id]["fase"] = "direccion"; await send_message(user_id, "📍 Escribe tu dirección:")
                        else: pedido_estado[user_id].update({"tipo": "recoger", "fase": "pago"}); await send_message(user_id, get_text(lang, 'payment_method'))
                    elif fase == "direccion":
                        pedido_estado[user_id].update({"dir": txt, "fase": "pago"}); await send_message(user_id, get_text(lang, 'payment_method'))
                    elif fase == "pago":
                        if tl == '1': pedido_estado[user_id]["met"] = "efectivo"; pedido_estado[user_id]["fase"] = "cash_bill"; await send_message(user_id, "💵 ¿Con qué billete pagarás?")
                        elif tl == '2': 
                            res = await guardar_pedido(user_id, carts[user_id], total, pedido_estado[user_id].get("tipo","recoger"), pedido_estado[user_id].get("dir",""), "tarjeta")
                            carts[user_id] = []; pedido_estado.pop(user_id, None)
                            await send_message(user_id, f"✅ Pedido {res['numero']} confirmado (Tarjeta).")
                        elif tl == '3': await send_message(user_id, "💳 Transferencia (Solo clientes validados)")
                    elif fase == "cash_bill":
                        await send_message(user_id, f"✅ Pago en efectivo registrado ({txt}). Tu pedido está en camino.")
                        carts[user_id] = []; pedido_estado.pop(user_id, None)
                    continue

                # 5️⃣ COMANDOS
                if tl in ['hola','hello','salam']:
                    carts[user_id] = []; pedido_estado.pop(user_id, None)
                    await send_message(user_id, "🌍 *Bienvenido*\n1. 🇪🇸 Español 2. 🇬🇧 English 3. 🇫🇷 Français 4. 🇲🇦 Darija 5. 🇲🇦 العربية")
                    pedido_estado[user_id] = {"fase":"seleccion_idioma"}
                    continue
                if tl in ['m','menu']:
                    msg1, _, msg2 = await get_restaurant_menu(client_id, lang)
                    await send_message(user_id, msg1)
                    if msg2: await send_message(user_id, msg2)
                    continue
                if tl in ['v','pedido','order','cart']: await send_message(user_id, await get_cart(user_id, lang)); continue
                if tl in ['c','confirmar','confirm']:
                    if not carts.get(user_id) or sum(i['price'] for i in carts[user_id])<=0: await send_message(user_id, "⚠️ Carrito vacío.")
                    else:
                        pedido_estado[user_id] = {"fase": "entrega"}
                        await send_message(user_id, get_text(lang, 'delivery_type'))
                    continue
                if tl in ['r','reservar']:
                    await send_message(user_id, "📅 *Reserva*\nEscribe: `Hoy 21:00` o `18/05 20:30`")
                    continue
                if tl.isdigit(): await send_message(user_id, await add_to_cart(user_id, int(tl), 1, client_id, lang))
                await send_message(user_id, LanguageDetector.get_help(lang))

# ========== ENDPOINTS ==========
@app.get("/health")
async def health(): return {"status":"ok","version":VERSION,"supabase":supabase is not None}
@app.get("/api/whatsapp/webhook")
async def wb_get(req: Request):
    p = req.query_params
    if p.get("hub.mode")=="subscribe" and p.get("hub.verify_token")==VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge"))
    raise HTTPException(403)
@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    try: bg.add_task(process_message, await req.json()); return {"status":"ok"}
    except: return {"status":"error"}, 500

@app.on_event("startup")
async def startup(): await load_phone_mapping()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)), reload=False)
