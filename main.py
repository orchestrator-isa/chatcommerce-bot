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

VERSION = "8.0-RESTINGA-FINAL"
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

# ========== IDIOMAS ==========
LANG_DIR = Path("lang")
LANGUAGES: Dict[str, dict] = {}
if LANG_DIR.exists():
    for f in LANG_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh: LANGUAGES[f.stem] = json.load(fh)
            logger.info(f"✅ Idioma cargado: {f.stem}")
        except Exception as e: logger.error(f"❌ Error cargando {f}: {e}")
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
user_idioma_manual: Dict[str, bool] = {}
pedido_estado: Dict[str, dict] = {}
restaurant_status = "normal"
clientes_validados: set = set()
session_activa: Dict[str, str] = {}
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
            for r in supabase.table("valid_clients").select("telefono").execute().data:
                clientes_validados.add(r.get("telefono",""))
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
        except: pass
    except Exception as e: logger.error(f"❌ registrar: {e}")

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
            mid = len(txt) // 2
            return txt[:mid], res.data, txt[mid:]
        return txt, res.data, ""
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error cargando menú", [], ""

async def add_to_cart(user_id: str, idx: int, cant: int, client_id: str, lang: str) -> str:
    _, platos, _ = await get_restaurant_menu(client_id, lang, waba=True)
    if not platos or idx < 1 or idx > len(platos):
        return f"❌ Número inválido. El menú tiene {len(platos)} platos."
    sel = platos[idx-1]
    if user_id not in carts: carts[user_id] = []
    if sel["price"]==0 and not any(i["price"]>0 for i in carts.get(user_id,[])):
        return f"📌 *{sel['dish_name']}* es GRATIS con bebida u otro producto."
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
        if d["price"]==0: lineas.append(f"• {n} — 🆓 GRATIS (con bebida)")
        elif d["cant"]>1: lineas.append(f"• {n} x{d['cant']} — {d['cant']*d['price']} MAD")
        else: lineas.append(f"• {n} — {d['price']} MAD")
    return f"🛒 *TU PEDIDO*\n{'\n'.join(lineas)}\n💰 *TOTAL: {total} MAD*\nEscribe *c* para confirmar."

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts: carts[user_id] = []
    return get_text(lang, 'cart_empty')

async def remove_from_cart_by_index(user_id: str, idx: int, lang: str) -> str:
    if user_id not in carts or not carts[user_id] or idx < 1 or idx > len(carts[user_id]): return get_text(lang, 'cart_empty')
    rem = carts[user_id].pop(idx - 1)
    return get_text(lang, 'removed_item', cantidad=1, nombre=rem['name'], total=sum(i["price"] for i in carts[user_id]))

# ========== PDF UNIVERSAL ==========
async def enviar_menu_pdf(to: str, lang: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    pdf = 'menu_es.pdf'
    base = os.getenv("RENDER_EXTERNAL_URL", "https://mi-bot-restinga-test.onrender.com")
    url = f"{base}/static/{pdf}"
    data = {"messaging_product":"whatsapp","to":to,"type":"document","document":{"link":url,"filename":"Menu_Restinga.pdf","caption":"📋 Menú completo / Full Menu"}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages", headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}","Content-Type":"application/json"}, json=data)
            return r.status_code == 200
    except: return False

# ========== WHATSAPP ==========
async def send_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":message[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, headers=headers, json=data)
            if r.status_code == 200: return True
            return False
    except: return False

# ========== GUARDAR PEDIDO (FIX SUPABASE) ==========
async def guardar_pedido(user_id: str, items: list, total: int, tipo: str, direccion: str, metodo: str, billete: str = None) -> dict:
    try:
        if not supabase: return {"error": "DB offline", "numero": "TEMP"}
        client_id = phone_to_restaurant.get(user_id, "44444444-4444-4444-4444-444444444444")
        items_json = [{"name": i["name"], "price": i["price"], "cantidad": i.get("cantidad", 1)} for i in items]
        data = {"client_id": client_id, "cliente_telefono": user_id, "items_json": items_json, "total_mad": total, "estado": "nuevo", "tipo_entrega": tipo, "direccion": direccion, "metodo_pago": metodo, "billete": billete, "pagado": metodo != "transferencia", "created_at": datetime.now().isoformat()}
        res = supabase.table("orders").insert(data).execute()
        if res.data:
            num = res.data[0].get("numero")
            return {"numero": str(num) if num else f"ORD-{uuid.uuid4().hex[:6].upper()}", "id": res.data[0].get("id")}
        return {"error": "Failed", "numero": "???"}
    except Exception as e:
        logger.error(f"❌ Error guardar_pedido: {e}")
        return {"error": str(e), "numero": "???"}

# ========== FLUJO PAGO/ENTREGA ==========
async def iniciar_entrega(user_id: str, lang: str) -> str:
    pedido_estado[user_id] = {"fase": "entrega"}
    return get_text(lang, 'delivery_type')

async def procesar_entrega(user_id: str, text: str, lang: str) -> str:
    total = sum(i["price"] for i in carts.get(user_id, []))
    if text == '1':
        pedido_estado[user_id].update({"tipo_entrega": "recoger", "fase": "pago"})
        tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])["recoger"]
        return f"✅ *Recoger en local*\n⏱️ {tiempo}\n💰 {total} MAD\n{get_text(lang, 'payment_method')}"
    elif text == '2':
        pedido_estado[user_id].update({"tipo_entrega": "domicilio", "fase": "check_zona"})
        return get_text(lang, 'delivery_zone_check')
    return "❌ Elige *1* (Recoger) o *2* (Domicilio)."

async def procesar_zona(user_id: str, text: str, lang: str) -> str:
    tl = text.lower().strip()
    if tl in ['no', 'n', 'nah']:
        pedido_estado[user_id].update({"tipo_entrega": "recoger", "fase": "pago"})
        total = sum(i["price"] for i in carts.get(user_id, []))
        return f"⚠️ *Fuera de zona*. Cambiado a recoger.\n⏱️ 5-10 min\n💰 {total} MAD\n{get_text(lang, 'payment_method')}"
    if tl in ['si','sí','yes','oui']:
        pedido_estado[user_id]["fase"] = "direccion"
        return "📍 Escribe tu dirección exacta:"
    return "❌ Responde *Sí* o *No*."

async def procesar_direccion(user_id: str, text: str, lang: str) -> str:
    pedido_estado[user_id].update({"direccion": text, "fase": "pago"})
    total = sum(i["price"] for i in carts.get(user_id, []))
    return f"📍 Dirección guardada.\n💰 {total} MAD\n{get_text(lang, 'payment_method')}"

async def procesar_pago(user_id: str, text: str, lang: str) -> str:
    if text == '1':
        pedido_estado[user_id].update({"metodo_pago": "efectivo", "fase": "cash_bill"})
        return get_text(lang, 'cash_bill')
    elif text == '2':
        total = sum(i["price"] for i in carts.get(user_id, []))
        tipo = pedido_estado[user_id].get("tipo_entrega", "recoger")
        tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])[tipo]
        dir = pedido_estado[user_id].get("direccion")
        res = await guardar_pedido(user_id, carts[user_id], total, tipo, dir, "tarjeta")
        carts[user_id] = []; pedido_estado.pop(user_id, None)
        return get_text(lang, 'order_confirmed', numero=res.get("numero","???"), total=total, metodo="Tarjeta POS", tiempo=tiempo) if "error" not in res else f"❌ Error: {res['error']}"
    elif text == '3':
        if user_id not in clientes_validados and user_id not in ['212668087490','212626282904']:
            return "⚠️ *Cliente no validado*\nTransferencia solo para registrados. Elige 1 o 2."
        pedido_estado[user_id].update({"metodo_pago": "transferencia", "fase": "transfer_pending"})
        return get_text(lang, 'transfer_pending')
    return "❌ Opción no válida. Escribe *1*, *2* o *3*."

async def procesar_billete(user_id: str, text: str, lang: str) -> str:
    try:
        raw = text.strip().upper()
        is_eur = 'EUR' in raw or '€' in raw
        num = int(raw.replace('EUR','').replace('€',''))
        billete_mad = int(num * 10) if is_eur else num
        total = sum(i["price"] for i in carts.get(user_id, []))
        if billete_mad < total: return f"⚠️ Billete insuficiente. Total: {total} MAD."
        pedido_estado[user_id]["billete"] = f"{num}{' EUR' if is_eur else ' MAD'}"
        cambio = billete_mad - total
        tipo = pedido_estado[user_id].get("tipo_entrega", "recoger")
        tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])[tipo]
        await guardar_pedido(user_id, carts[user_id], total, tipo, pedido_estado[user_id].get("direccion"), "efectivo", pedido_estado[user_id]["billete"])
        carts[user_id] = []; pedido_estado.pop(user_id, None)
        met = f"Efectivo {num}{' EUR' if is_eur else ' MAD'}" + (f" (cambio: {cambio} MAD)" if cambio>0 else "")
        return get_text(lang, 'order_confirmed', numero="???", total=total, metodo=met, tiempo=tiempo)
    except: return "❌ Ej: `100` o `20EUR`."

async def procesar_transferencia(user_id: str, lang: str) -> str:
    total = sum(i["price"] for i in carts.get(user_id, []))
    carts[user_id] = []; pedido_estado.pop(user_id, None)
    return get_text(lang, 'order_confirmed', numero="???", total=total, metodo="Transferencia (pendiente)", tiempo="5-10 min")

# ========== PROCESAR MENSAJE (ESTRUCTURA CORRECTA v8.0) ==========
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
                await registrar_mensaje(user_id, "incoming", txt)
                fase = pedido_estado.get(user_id, {}).get("fase", "inicio")

                # 2️⃣ COMANDO Q / SALIR (Reinicia a idiomas)
                if tl in ['q', 'salir', 'exit', 'esc', 'reiniciar', 'cancelar', 'adios']:
                    if user_id in carts: del carts[user_id]
                    if user_id in pedido_estado: del pedido_estado[user_id]
                    pedido_estado[user_id] = {"fase": "seleccion_idioma"}
                    await send_message(user_id, "🌍 *Selecciona tu idioma:*\n1. 🇪🇸 Español 2. 🇬🇧 English 3. 🇫🇷 Français 4. 🇲🇦 Darija 5. 🇲🇦 العربية")
                    await registrar_mensaje(user_id, "outgoing", "reinicio")
                    continue

                # 3️⃣ VALIDACIONES DE FASE (Ahora 'fase' YA existe)
                if fase in ["entrega","check_zona","pago","cash_bill"]:
                    if fase == "entrega" and tl not in ['1','2']: await send_message(user_id, "❌ Responde *1* o *2*."); continue
                    if fase == "check_zona" and tl not in ['si','sí','yes','oui','no','n']: await send_message(user_id, "❌ Responde *Sí* o *No*."); continue
                    if fase == "pago" and tl not in ['1','2','3']: await send_message(user_id, "❌ Elige *1*, *2* o *3*."); continue
                    if fase == "cash_bill" and not tl.isdigit() and 'eur' not in tl and '€' not in tl: await send_message(user_id, "❌ Ej: `100` o `20EUR`."); continue

                # 4️⃣ FASES
                if fase == "seleccion_idioma":
                    mapas = {'1':'spanish','2':'english','3':'french','4':'darija_latin','5':'darija_arabic'}
                    if txt in mapas:
                        user_lang[user_id] = mapas[txt]; user_idioma_manual[user_id] = True
                        await send_message(user_id, f"{LanguageDetector.get_welcome(user_lang[user_id])}\n{LanguageDetector.get_help(user_lang[user_id])}")
                        pedido_estado.pop(user_id, None)
                    else: await send_message(user_id, "❌ Responde *1*, *2*, *3*, *4* o *5*.")
                    continue
                if fase in ["entrega","check_zona","direccion","pago","cash_bill","transfer_pending"]:
                    funcs = {"entrega":procesar_entrega,"check_zona":procesar_zona,"direccion":procesar_direccion,"pago":procesar_pago,"cash_bill":procesar_billete,"transfer_pending":procesar_transferencia}
                    await send_message(user_id, await funcs[fase](user_id, txt if fase=="direccion" else tl, lang))
                    continue
                if fase == "reserva_personas":
                    if tl.isdigit() and 1<=int(tl)<=20:
                        pedido_estado[user_id]["people"] = int(tl); pedido_estado[user_id]["fase"] = "reserva_fecha"
                        await send_message(user_id, "🕐 ¿Para qué día y hora? Ej: 'Mañana 20:00' o '15/05 21:30'")
                    else: await send_message(user_id, "❌ Número entre 1 y 20.")
                    continue
                if fase == "reserva_fecha":
                    try:
                        if "mañana" in tl: fecha = (datetime.now() + timedelta(days=1)).date(); h,m = 20,0
                        else:
                            partes = tl.split(); fecha_str, hora_str = partes[0], partes[1] if len(partes)>1 else "20:00"
                            d,mo = map(int, fecha_str.split('/')); fecha = datetime.now().replace(day=d, month=mo).date()
                            hm = hora_str.replace(':',''); h,m = int(hm[:2]), int(hm[2:4]) if len(hm)>=4 else 0
                        await send_message(user_id, f"✅ *Solicitud recibida*\n👥 {pedido_estado[user_id]['people']} pax | 📅 {fecha} {h:02d}:{m:02d}")
                        pedido_estado.pop(user_id, None)
                    except: await send_message(user_id, "❌ Formato no reconocido.")
                    continue

                # 5️⃣ COMANDOS (Solo 'if', CERO duplicados)
                if tl in ['hola','hello','salam','hi']:
                    carts[user_id] = []; pedido_estado.pop(user_id, None)
                    await send_message(user_id, "🌍 *Bienvenido*\n1. 🇪🇸 Español 2. 🇬🇧 English 3. 🇫🇷 Français 4. 🇲🇦 Darija 5. 🇲🇦 العربية")
                    pedido_estado[user_id] = {"fase":"seleccion_idioma"}
                    continue
                if tl in ['m','menu','menú']:
                    msg1, _, msg2 = await get_restaurant_menu(client_id, lang, waba=True)
                    await send_message(user_id, msg1)
                    if msg2: await send_message(user_id, msg2)
                    await enviar_menu_pdf(user_id, lang)
                    continue
                if tl in ['v','pedido','order','cart']: await send_message(user_id, await get_cart(user_id, lang)); continue
                if tl in ['c','confirmar','confirm']:
                    if not carts.get(user_id) or sum(i['price'] for i in carts[user_id])<=0: await send_message(user_id, "⚠️ Carrito vacío.")
                    else: await send_message(user_id, await iniciar_entrega(user_id, lang))
                    continue
                if tl in ['r','reservar','reservación','reservation','book','table']:
                    pedido_estado[user_id] = {"fase": "reserva_personas"}
                    await send_message(user_id, "📅 *Reservar mesa*\n¿Para cuántas personas? (1-20)")
                    continue
                if tl.isdigit(): await send_message(user_id, await add_to_cart(user_id, int(tl), 1, client_id, lang))
                if tl.startswith('x'):
                    resto = tl[1:].strip()
                    await send_message(user_id, await remove_from_cart_by_index(user_id, int(resto), lang) if resto.isdigit() else await clear_cart(user_id, lang))
                await send_message(user_id, LanguageDetector.get_help(lang))

# ========== ENDPOINTS ==========
@app.get("/")
async def root(): return {"status":"ok","version":VERSION}
@app.get("/health")
async def health(): return {"status":"ok","version":VERSION,"supabase":supabase is not None,"whatsapp":bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),"lang":len(LANGUAGES)}
@app.get("/api/whatsapp/webhook")
async def wb_get(req: Request):
    p = req.query_params
    if p.get("hub.mode")=="subscribe" and p.get("hub.verify_token")==VERIFY_TOKEN: return PlainTextResponse(p.get("hub.challenge"))
    raise HTTPException(403)
@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    try: bg.add_task(process_message, await req.json()); return {"status":"ok"}
    except: return {"status":"error"}, 500
@app.get("/api/orders")
async def get_orders():
    if not supabase: return {"data": []}
    try: return {"data": supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute().data}
    except: return {"data": []}
@app.post("/api/restaurant/status")
async def set_restaurant_status(request: Request):
    global restaurant_status
    data = await request.json()
    if data.get("status") in ["normal", "moderado", "lleno"]: restaurant_status = data["status"]; return {"status":"ok"}
    raise HTTPException(400)
@app.on_event("startup")
async def startup(): await load_phone_mapping()
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)), reload=False)
# v8.0 FINAL DEPLOY
