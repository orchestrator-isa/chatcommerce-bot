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

VERSION = "7.4-RESTINGA-PROD"
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
            logger.info(f"✅ Idioma: {f.stem}")
        except Exception as e: logger.error(f"❌ {f}: {e}")
else: LANG_DIR.mkdir(exist_ok=True)

LANG_MAP = {'english':'en','spanish':'es','french':'fr','german':'de','turkish':'tr','darija_latin':'dar','darija_arabic':'ar'}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    file_key = LANG_MAP.get(lang_code, 'es')
    texts = LANGUAGES.get(file_key, LANGUAGES.get('es', {}))
    template = texts.get(key, LANGUAGES['es'].get(key, key))
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
        except Exception as e:
            if "pgrst204" in str(e).lower() or "session_id" in str(e).lower():
                supabase.table("messages").insert({"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
    except Exception as e: logger.error(f"❌ registrar: {e}")

# ========== MENÚ & CARRITO ==========
async def get_restaurant_menu(client_id: str, lang: str = 'spanish', waba: bool = True) -> tuple:
    if not supabase: return "❌ Error de conexión", []
    try:
        q = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True)
        if waba: q = q.eq("show_on_waba", True)
        res = q.execute()
        if not res.data: return "📋 *MENÚ*\nNo hay platos disponibles.", []
        lines = ["📋 *MENÚ RESTINGA*", ""]
        for i, item in enumerate(res.data, 1):
            p = "🆓 GRATIS con bebida" if item['price']==0 else f"{item['price']} MAD"
            lines.append(f"{i}. *{item['dish_name']}* — {p}")
            if item.get('description'): lines.append(f"   {item['description']}")
            lines.append("")
        return "\n".join(lines), res.data
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error cargando menú", []

async def add_to_cart(user_id: str, idx: int, cant: int, client_id: str, lang: str) -> str:
    _, platos = await get_restaurant_menu(client_id, lang, waba=True)
    if not platos or idx > len(platos): return get_text(lang, 'help')
    sel = platos[idx-1]
    if user_id not in carts: carts[user_id] = []
    if sel["price"]==0 and not any(i["price"]>0 for i in carts.get(user_id,[])):
        return f"📌 *{sel['dish_name']}* es GRATIS con bebida u otro producto. Añade una bebida primero."
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
    items_text = "\n".join(lineas)
    return f"🛒 *TU PEDIDO*\n{items_text}\n💰 *TOTAL: {total} MAD*\nEscribe *c* para confirmar."

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts: carts[user_id] = []
    return get_text(lang, 'cart_empty')

async def remove_from_cart_by_index(user_id: str, idx: int, lang: str) -> str:
    if user_id not in carts or not carts[user_id] or idx < 1 or idx > len(carts[user_id]): return get_text(lang, 'cart_empty')
    rem = carts[user_id].pop(idx - 1)
    return get_text(lang, 'removed_item', cantidad=1, nombre=rem['name'], total=sum(i["price"] for i in carts[user_id]))

# ========== PDF ==========
async def enviar_menu_pdf(to: str, lang: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    pdf_map = {'spanish':'menu_es.pdf','english':'menu_en.pdf','french':'menu_fr.pdf','darija_latin':'menu_dar.pdf','darija_arabic':'menu_ar.pdf'}
    pdf = pdf_map.get(lang, 'menu_es.pdf')
    base = os.getenv("RENDER_EXTERNAL_URL", "https://mi-bot-restinga-test.onrender.com")
    url = f"{base}/static/{pdf}"
    data = {"messaging_product":"whatsapp","to":to,"type":"document","document":{"link":url,"filename":f"Menu_{lang}.pdf","caption":"📋 Menú completo"}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages", headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}","Content-Type":"application/json"}, json=data)
            return r.status_code == 200
    except Exception as e:
        logger.error(f"❌ Error PDF: {e}")
        return False

# ========== WHATSAPP ==========
async def send_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":message[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, headers=headers, json=data)
            if r.status_code == 200:
                logger.info(f"✅ Enviado a {to}")
                return True
            logger.error(f"❌ WA {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"❌ Error send_message: {e}")
        return False

# ========== GUARDAR PEDIDO ==========
async def guardar_pedido(user_id: str, items: list, total: int, tipo: str, direccion: str, metodo: str, billete: str = None) -> dict:
    try:
        if not supabase: return {"error": "DB offline", "numero": "TEMP"}
        client_id = phone_to_restaurant.get(user_id, "44444444-4444-4444-4444-444444444444")
        items_json = [{"name": i["name"], "price": i["price"], "cantidad": i.get("cantidad", 1)} for i in items]
        data = {"client_id": client_id, "cliente_telefono": user_id, "items_json": items_json, "total_mad": total, "estado": "nuevo", "tipo_entrega": tipo, "direccion": direccion, "metodo_pago": metodo, "billete": billete, "pagado": metodo != "transferencia", "created_at": datetime.now().isoformat()}
        
        # ✅ FIX: Seleccionar explícitamente 'numero' después del insert
        res = supabase.table("orders").insert(data).select("numero,id").execute()
        
        if res.data:
            return {"numero": str(res.data[0].get("numero")), "id": res.data[0].get("id")}
        return {"error": "Failed to create order", "numero": "???"}
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
    if text.lower() in ['si','sí','yes','oui']:
        pedido_estado[user_id]["fase"] = "direccion"
        return "📍 Escribe tu dirección exacta:"
    pedido_estado[user_id]["fase"] = "pago"
    return get_text(lang, 'delivery_out_of_zone')

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
    return "❌ Opción no válida. Escribe *1* (Efectivo), *2* (Tarjeta) o *3* (Transferencia)."

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
    except ValueError: return "❌ Responde con el número (ej: `100` o `20EUR`)."

async def procesar_transferencia(user_id: str, lang: str) -> str:
    total = sum(i["price"] for i in carts.get(user_id, []))
    if total <= 0: return "⚠️ Carrito vacío."
    carts[user_id] = []; pedido_estado.pop(user_id, None)
    return get_text(lang, 'order_confirmed', numero="???", total=total, metodo="Transferencia (pendiente)", tiempo="5-10 min")

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
                # 🚪 COMANDO GLOBAL: Salir/Reiniciar (funciona en CUALQUIER fase)
                if tl in ['salir', 'exit', 'q', 'esc', 'reiniciar', 'cancelar', 'adios']:
                    if user_id in carts: del carts[user_id]
                    if user_id in pedido_estado: del pedido_estado[user_id]
                    await send_message(user_id, "🔄 *Conversación reiniciada.*\nEscribe *HOLA* o *m* para empezar.")
                    await registrar_mensaje(user_id, "outgoing", "reinicio")
                    continue
                lang = user_lang.get(user_id, LanguageDetector.detect(txt))
                await registrar_mensaje(user_id, "incoming", txt)
                fase = pedido_estado.get(user_id, {}).get("fase", "inicio")
                # Fases
                if fase == "seleccion_idioma":
                    clean_txt = text.strip()  # ← Limpia espacios/retornos
                    mapas = {'1':'spanish','2':'english','3':'french','4':'darija_latin','5':'darija_arabic'}
                    if clean_txt in mapas:
                        user_lang[user_id] = mapas[clean_txt]
                        user_idioma_manual[user_id] = True
                        resp = f"{LanguageDetector.get_welcome(user_lang[user_id])}\n{LanguageDetector.get_help(user_lang[user_id])}"
                        await send_message(user_id, resp)
                        await registrar_mensaje(user_id, "outgoing", resp)
                        pedido_estado.pop(user_id, None)  # ← Limpia la fase
                        continue
                    else:
                        await send_message(user_id, "❌ Opción no válida. Responde solo con el número: *1*, *2*, *3*, *4* o *5*.")
                        continueif fase in ["entrega","check_zona","direccion","pago","cash_bill","transfer_pending"]:
                    funcs = {"entrega":procesar_entrega,"check_zona":procesar_zona,"direccion":procesar_direccion,"pago":procesar_pago,"cash_bill":procesar_billete,"transfer_pending":procesar_transferencia}
                    inp = txt if fase=="direccion" else tl
                    resp = await funcs[fase](user_id, inp, lang)
                    await send_message(user_id, resp); await registrar_mensaje(user_id, "outgoing", resp)
                    continue
                if fase == "reserva_personas":
                    if tl.isdigit() and 1<=int(tl)<=20:
                        pedido_estado[user_id]["people"] = int(tl); pedido_estado[user_id]["fase"] = "reserva_fecha"
                        await send_message(user_id, "🕐 ¿Para qué día y hora? Ej: 'Mañana 20:00' o '15/05 21:30'")
                    else: await send_message(user_id, "❌ Número entre 1 y 20.")
                    continue
                if fase == "reserva_fecha":
                    try:
                        if "mañana" in tl or "manana" in tl:
                            from datetime import timedelta; fecha = (datetime.now() + timedelta(days=1)).date(); h,m = 20,0
                        else:
                            partes = tl.split(); fecha_str, hora_str = partes[0], partes[1] if len(partes)>1 else "20:00"
                            d,mo = map(int, fecha_str.split('/')); fecha = datetime.now().replace(day=d, month=mo).date()
                            hm = hora_str.replace(':',''); h,m = int(hm[:2]), int(hm[2:4]) if len(hm)>=4 else 0
                        if supabase:
                            try:
                                supabase.table("reservations").insert({"client_id":client_id,"cliente_telefono":user_id,"people_count":pedido_estado[user_id]["people"],"reservation_date":fecha.isoformat(),"reservation_time":f"{h:02d}:{m:02d}:00","status":"pending","created_at":datetime.now().isoformat()}).execute()
                            except Exception as e: logger.warning(f"⚠️ Reservas: {e}")
                        await send_message(user_id, f"✅ *Solicitud recibida*\n👥 {pedido_estado[user_id]['people']} pax | 📅 {fecha} {h:02d}:{m:02d}\n📞 Te confirmaremos en ≤10 minutos.")
                        pedido_estado.pop(user_id, None)
                    except: await send_message(user_id, "❌ Formato no reconocido.")
                    continue

                # Comandos
                if tl in ['hola','hello','salam','hi']:
                    carts[user_id] = []; pedido_estado.pop(user_id, None)
                    if user_id not in user_lang or not user_idioma_manual.get(user_id, False):
                        await send_message(user_id, "🌍 *Bienvenido*\n1. 🇪🇸 Español 2. 🇬🇧 English 3. 🇫🇷 Français 4. 🇲🇦 Darija 5. 🇲🇦 العربية")
                        pedido_estado[user_id] = {"fase":"seleccion_idioma"}
                    else:
                        resp = f"{LanguageDetector.get_welcome(lang)}\n{LanguageDetector.get_help(lang)}"
                        await send_message(user_id, resp)
                    continue
                if tl in ['m','menu','menú']:
                    mt, _ = await get_restaurant_menu(client_id, lang, waba=True)
                    await send_message(user_id, mt)
                    await enviar_menu_pdf(user_id, lang)
                    await send_message(user_id, LanguageDetector.get_help(lang))
                    await registrar_mensaje(user_id, "outgoing", "menu")
                    continue
                if tl in ['v','pedido','order','cart']:
                    resp = await get_cart(user_id, lang)
                    await send_message(user_id, resp); await registrar_mensaje(user_id, "outgoing", resp)
                    continue
                if tl in ['c','confirmar','confirm']:
                    if not carts.get(user_id) or sum(i['price'] for i in carts[user_id])<=0:
                        await send_message(user_id, "⚠️ Carrito vacío. Añade productos primero.")
                    else:
                        resp = await iniciar_entrega(user_id, lang)
                        await send_message(user_id, resp); await registrar_mensaje(user_id, "outgoing", resp)
                    continue
                if tl in ['reservar','reservación','reservation','book','table']:
                    pedido_estado[user_id] = {"fase": "reserva_personas"}
                    await send_message(user_id, "📅 *Reservar mesa*\n¿Para cuántas personas? (1-20)")
                    continue
                if tl.isdigit():
                    resp = await add_to_cart(user_id, int(tl), 1, client_id, lang)
                    await send_message(user_id, resp); await registrar_mensaje(user_id, "outgoing", resp)
                    continue
                if tl.startswith('x'):
                    resto = tl[1:].strip()
                    if resto.isdigit(): resp = await remove_from_cart_by_index(user_id, int(resto), lang)
                    else: resp = await clear_cart(user_id, lang)
                    await send_message(user_id, resp); await registrar_mensaje(user_id, "outgoing", resp)
                    continue
                # 1. MENÚ (Busca esta línea y agrégale 'm')
                elif text_lower in ['m', 'menu', 'menú']:
                    menu_txt, _ = await get_restaurant_menu(client_id, lang, waba=True)
                    await send_message(user_id, menu_txt)
                    await send_message(user_id, LanguageDetector.get_help(lang)) # Esto usa el JSON nuevo
                    continue
            
                # 2. CARRITO (Busca esta línea y agrégale 'v')
                elif text_lower in ['v', 'pedido', 'order', 'cart', 'carrito']:
                    resp = await get_cart(user_id, lang)
                    await send_message(user_id, resp)
                    continue
                # 🔢 Números para añadir al carrito
                elif text_lower.isdigit():
                    item_index = int(text_lower)
                    # ✅ Verificar que el índice es válido
                    _, platos = await get_restaurant_menu(client_id, lang, waba=True)
                    if not platos or item_index < 1 or item_index > len(platos):
                        max_plato = len(platos) if platos else 0
                        await send_message(user_id, f"❌ Número inválido. El menú tiene {max_plato} platos.\n💡 Escribe *m* para ver el menú completo.")
                    else:
                        resp = await add_to_cart(user_id, item_index, 1, client_id, lang)
                        await send_message(user_id, resp)
                        await registrar_mensaje(user_id, "outgoing", resp)
                    continue
            
                # 3. ELIMINAR (Busca esta línea y cámbiala por startswith('x'))
                elif text_lower.startswith('x'):
                    # Lógica de eliminar...
                    if user_id in carts: carts[user_id] = [] # Ejemplo simple
                    await send_message(user_id, "🗑️ Carrito vaciado/ítem eliminado") 
                    continue
            
                # 4. CONFIRMAR (Busca esta línea y agrégale 'c')
                # ✅ Confirmar pedido
                elif text_lower in ['c','confirmar','confirm']:
                    if not carts.get(user_id) or sum(i['price'] for i in carts[user_id])<=0:
                        # 💡 Mensaje más útil con guía
                        await send_message(user_id, "⚠️ Carrito vacío.\n💡 Escribe *m* para ver el menú y añade platos con su número (ej: '1', '2', '11').")
                    else:
                        resp = await iniciar_entrega(user_id, lang)
                        await send_message(user_id, resp)
                        await registrar_mensaje(user_id, "outgoing", resp)
                    continue
# ========== ENDPOINTS ==========
# ========== ENDPOINT RAÍZ (para evitar 404) ==========
@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Orquestrator ISA - Restinga",
        "version": VERSION,
        "docs": "/docs",
        "health": "/health"
    }
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

@app.get("/health")
async def health():
    return {"status":"ok","version":VERSION,"supabase":supabase is not None,"whatsapp":bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),"lang":len(LANGUAGES)}

@app.post("/admin/refresh-schema")
async def refresh(req: Request):
    if req.headers.get("Authorization")!=f"Bearer {ADMIN_TOKEN}": raise HTTPException(401)
    if supabase: supabase.table("messages").select("count").limit(1).execute()
    return {"status":"ok","msg":"cache refreshed"}

@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} starting...")
    await load_phone_mapping()
    if supabase: supabase.table("messages").select("id").limit(1).execute()
    logger.info(f"✅ {len(LANGUAGES)} languages: {list(LANGUAGES.keys())}")
# ===== ENDPOINTS PARA DASHBOARD (agregar en main.py) =====
@app.get("/api/orders")
async def get_orders():
    """Devuelve pedidos recientes para el dashboard"""
    if not supabase: return {"data": []}
    try:
        # Últimos 50 pedidos, ordenados por fecha
        res = supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute()
        return {"data": res.data}
    except Exception as e:
        return {"error": str(e), "data": []}

@app.post("/api/restaurant/status")
async def set_restaurant_status(request: Request):
    """Cambia estado: normal / moderado / lleno"""
    global restaurant_status
    try:
        data = await request.json()
        new_status = data.get("status", "normal")
        if new_status in ["normal", "moderado", "lleno"]:
            restaurant_status = new_status
            logger.info(f"🟢 Estado cambiado a: {new_status}")
            return {"status": "ok", "new_status": new_status}
        raise HTTPException(400, detail="Estado inválido")
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)), reload=False)
