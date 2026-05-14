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

VERSION = "7.3-RESTINGA-FINAL"
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
        lang_code = lang_file.stem
        try:
            with open(lang_file, "r", encoding="utf-8") as f:
                LANGUAGES[lang_code] = json.load(f)
            logger.info(f"✅ Cargado idioma: {lang_code}")
        except Exception as e:
            logger.error(f"❌ Error cargando {lang_file}: {e}")
else:
    LANG_DIR.mkdir(exist_ok=True)

LANG_MAP = {'english':'en','spanish':'es','french':'fr','german':'de','turkish':'tr','darija_latin':'dar','darija_arabic':'ar'}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    file_key = LANG_MAP.get(lang_code, 'es')
    texts = LANGUAGES.get(file_key, LANGUAGES.get('es', {}))
    template = texts.get(key, LANGUAGES['es'].get(key, key))
    # ✅ FIX: Intentar formatear siempre que haya kwargs, sin verificar '{}'
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template
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
    KEYWORDS = {
        'english': ['hello','hi','menu','thank','want'],
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
        if any('\u0600'<=c<='\u06FF' for c in text): return 'darija_arabic'
        scores = {lang:sum(1 for k in kw if k in text_lower) for lang,kw in cls.KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best]>0 else 'spanish'
    @classmethod
    def get_welcome(cls, lang:str) -> str: return get_text(lang,'welcome')
    @classmethod
    def get_help(cls, lang:str) -> str: return get_text(lang,'help')

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
        phone_to_restaurant['5217225529803'] = '44444444-4444-4444-4444-444444444444'
        
        try:
            result = supabase.table("valid_clients").select("telefono").execute()
            for r in result.data: clientes_validados.add(r.get("telefono", ""))
        except: pass
        logger.info(f"📞 {len(phone_to_restaurant)} restaurantes mapeados")
    except Exception as e: logger.error(f"Error mapeo: {e}")

# ========== REGISTRAR MENSAJE ==========
async def registrar_mensaje(user_id: str, direccion: str, mensaje: str, intent: str=None):
    try:
        if not supabase: return
        session_id = session_activa.get(user_id)
        if not session_id:
            session_id = str(uuid.uuid4())
            session_activa[user_id] = session_id
            try:
                supabase.table("sessions").insert({"id":session_id,"user_id":user_id,"inicio":datetime.now().isoformat(),"estado":"activa"}).execute()
            except: pass
        try:
            supabase.table("messages").insert({"session_id":session_id,"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
        except Exception as e:
            if "pgrst204" in str(e).lower() or "session_id" in str(e).lower():
                supabase.table("messages").insert({"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
    except Exception as e: logger.error(f"❌ registrar_mensaje: {e}")

# ========== MENÚ, CARRITO, PEDIDOS ==========
async def get_restaurant_menu(client_id: str, user_lang_code: str = 'spanish', waba: bool = True) -> tuple:
    try:
        if not supabase: return "❌ Error de conexión", []
        query = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True)
        if waba: query = query.eq("show_on_waba", True)
        result = query.execute()
        if not result.data: return "📋 *MENÚ*\nNo hay platos disponibles.", []
        
        lines = ["📋 *MENÚ RESTINGA*", ""]
        for i, item in enumerate(result.data, 1):
            if item['price'] == 0: lines.append(f"{i}. 🍽️ *{item['dish_name']}* — 🆓 GRATIS con bebida")
            else: lines.append(f"{i}. 🍽️ *{item['dish_name']}* — {item['price']} MAD")
            if item.get('description'): lines.append(f"   📝 {item['description']}")
            lines.append("")
        return "\n".join(lines), result.data
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error cargando menú", []

async def add_to_cart(user_id: str, item_index: int, cantidad: int, client_id: str, lang: str) -> str:
    try:
        _, platos = await get_restaurant_menu(client_id, lang, waba=True)
        if not platos or item_index > len(platos): return get_text(lang, 'help')
        selected = platos[item_index - 1]
        if user_id not in carts: carts[user_id] = []
        
        if selected["price"] == 0:
            if not any(it["price"] > 0 for it in carts.get(user_id, [])):
                return f"📌 *{selected['dish_name']}* es GRATIS con bebida u otro producto. Añade una bebida primero (ej: 18. Té a la menta)"
        
        for _ in range(cantidad): carts[user_id].append({"name": selected["dish_name"], "price": selected["price"]})
        total = sum(it["price"] for it in carts[user_id])
        return get_text(lang, 'added_to_cart', cantidad=cantidad, nombre=selected['dish_name'], total=total)
    except Exception as e:
        logger.error(f"Error carrito: {e}")
        return "❌ Error al añadir"

async def get_cart(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]: return get_text(lang, 'cart_empty')
    items_dict = {}
    for it in carts[user_id]:
        if it["name"] not in items_dict: items_dict[it["name"]] = {"price": it["price"], "cantidad": 0}
        items_dict[it["name"]]["cantidad"] += 1
    total = sum(it["price"] for it in carts[user_id])
    
    # ✅ FIX: Separar join para evitar SyntaxError en f-string
    item_lines = []
    for n, d in items_dict.items():
        if d["price"] == 0: item_lines.append(f"• {n} — 🆓 GRATIS (con bebida)")
        elif d["cantidad"] > 1: item_lines.append(f"• {n} x{d['cantidad']} — {d['cantidad']*d['price']} MAD")
        else: item_lines.append(f"• {n} — {d['price']} MAD")
    items_text = "\n".join(item_lines)
    return f"🛒 *TU PEDIDO*\n{items_text}\n💰 *TOTAL: {total} MAD*\nEscribe *CONFIRMAR* para finalizar."

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts: carts[user_id] = []
    return get_text(lang, 'cart_empty')

async def remove_from_cart_by_index(user_id: str, idx: int, lang: str) -> str:
    if user_id not in carts or not carts[user_id] or idx < 1 or idx > len(carts[user_id]): return get_text(lang, 'cart_empty')
    rem = carts[user_id].pop(idx - 1)
    return get_text(lang, 'removed_item', cantidad=1, nombre=rem['name'], total=sum(it["price"] for it in carts[user_id]))

# ========== FLUJO DE ENTREGA/PAGO ==========
async def iniciar_entrega(user_id: str, lang: str) -> str:
    pedido_estado[user_id] = {"fase": "entrega"}
    return get_text(lang, 'delivery_type')

async def procesar_entrega(user_id: str, text: str, lang: str) -> str:
    if text == '1':
        pedido_estado[user_id].update({"tipo_entrega": "recoger", "fase": "pago"})
        total = sum(it["price"] for it in carts.get(user_id, []))
        return f"✅ *Recogida en local*\n⏱️ {TIEMPOS.get(restaurant_status,TIEMPOS['normal'])['recoger']}\n💰 {total} MAD\n{get_text(lang,'payment_method')}"
    elif text == '2':
        pedido_estado[user_id].update({"tipo_entrega": "domicilio", "fase": "check_zona"})
        return get_text(lang, 'delivery_zone_check')
    return "❌ Elige *1* (Recoger) o *2* (Domicilio)."

async def procesar_zona(user_id: str, text: str, lang: str) -> str:
    if text.lower() in ['si','sí','yes','oui']:
        pedido_estado[user_id]["fase"] = "direccion"
        return get_text(lang, 'address_request')
    pedido_estado[user_id]["fase"] = "pago"
    return get_text(lang, 'delivery_out_of_zone')

async def procesar_direccion(user_id: str, text: str, lang: str) -> str:
    pedido_estado[user_id].update({"direccion": text, "fase": "pago"})
    return f"📍 Dirección guardada.\n{get_text(lang, 'payment_method')}"

async def guardar_pedido(user_id: str, items: list, total: int, tipo_entrega: str, direccion: str, metodo_pago: str, billete: str = None) -> dict:
    """Guarda pedido en Supabase y retorna número de pedido"""
    try:
        if not supabase:
            return {"error": "Database not connected"}
        
        client_id = phone_to_restaurant.get(user_id, "44444444-4444-4444-4444-444444444444")
        
        # Formatear items para JSON
        items_json = [{"name": i["name"], "price": i["price"], "cantidad": i.get("cantidad", 1)} for i in items]
        
        data = {
            "client_id": client_id,
            "cliente_telefono": user_id,
            "items_json": items_json,
            "total_mad": total,
            "estado": "nuevo",
            "tipo_entrega": tipo_entrega,
            "direccion": direccion,
            "metodo_pago": metodo_pago,
            "billete": billete,
            "pagado": metodo_pago != "transferencia",  # Transferencia queda pendiente
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase.table("orders").insert(data).execute()
        
        if result.data:
            pedido = result.data[0]
            return {"numero": pedido.get("numero", "???"), "id": pedido.get("id")}
        return {"error": "Failed to create order"}
        
    except Exception as e:
        logger.error(f"❌ Error guardar_pedido: {e}")
        return {"error": str(e)}

async def procesar_pago(user_id: str, text: str, lang: str) -> str:
    """
    Maneja selección de método de pago:
    1 = Efectivo (MAD)
    2 = Tarjeta (Visa/Mastercard) ✅ 
    3 = Transferencia (solo validados)
    """
    if text == '1':  # ✅ EFECTIVO
        pedido_estado[user_id]["metodo_pago"] = "efectivo"
        pedido_estado[user_id]["fase"] = "cash_bill"
        return get_text(lang, 'cash_bill')
    
    elif text == '2':  # ✅ TARJETA (NO requiere validación)
        pedido_estado[user_id]["metodo_pago"] = "tarjeta"
        
        total = sum(item["price"] for item in carts.get(user_id, []))
        tipo = pedido_estado[user_id].get("tipo_entrega", "recoger")
        tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])[tipo]
        direccion = pedido_estado[user_id].get("direccion")
        
        # Guardar pedido directamente
        resultado = await guardar_pedido(user_id, carts[user_id], total, tipo, direccion, "tarjeta")
        
        # Limpieza post-pedido
        carts[user_id] = []
        pedido_estado.pop(user_id, None)
        
        if "error" in resultado:
            return f"❌ Error al guardar: {resultado['error']}"
        
        numero = resultado.get("numero", "???")
        return get_text(lang, 'order_confirmed', 
                       numero=numero, 
                       total=total, 
                       metodo="Tarjeta POS", 
                       tiempo=tiempo)
    
    elif text == '3':  # ✅ TRANSFERENCIA (SÍ requiere validación)
        if user_id not in clientes_validados and user_id not in ['212668087490', '212626282904']:
            return "⚠️ *Cliente no validado*\nLos pagos por transferencia son solo para clientes registrados. Por favor, elige efectivo o tarjeta."
        
        pedido_estado[user_id]["metodo_pago"] = "transferencia"
        pedido_estado[user_id]["fase"] = "transfer_pending"
        return get_text(lang, 'transfer_pending')
    
    return "❌ Opción no válida. Escribe *1* (Efectivo), *2* (Tarjeta) o *3* (Transferencia)."

async def procesar_billete(user_id: str, text: str, lang: str) -> str:
    try:
        billete = int(text)
        total = sum(it["price"] for it in carts.get(user_id, []))
        if total <= 0: return "⚠️ El carrito está vacío o total es 0."
        if billete < total: return f"⚠️ Billete insuficiente. Total: {total} MAD."
        pedido_estado[user_id]["billete"] = str(billete)
        cambio = billete - total
        carts[user_id] = []
        pedido_estado.pop(user_id, None)
        return get_text(lang, 'order_confirmed', numero="107", total=total, metodo=f"Efectivo {billete} MAD (cambio: {cambio})", tiempo="5-10 min")
    except ValueError: return "❌ Responde con el número del billete."

async def procesar_transferencia(user_id: str, lang: str) -> str:
    total = sum(it["price"] for it in carts.get(user_id, []))
    if total <= 0: return "⚠️ El carrito está vacío."
    carts[user_id] = []
    pedido_estado.pop(user_id, None)
    return get_text(lang, 'order_confirmed', numero="107", total=total, metodo="Transferencia (pendiente)", tiempo="5-10 min")

# ========== ENVIAR WHATSAPP ==========
async def send_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, headers=headers, json=data)
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
    try:
        if body.get("object") != "whatsapp_business_account": return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                display_phone = value.get("metadata", {}).get("display_phone_number", "").replace("+", "")
                client_id = phone_to_restaurant.get(display_phone, "44444444-4444-4444-4444-444444444444")
                
                for msg in value.get("messages", []):
                    if msg.get("type") != "text": continue
                    user_id = msg.get("from")
                    text = msg.get("text", {}).get("body", "").strip()
                    text_lower = text.lower()
                    lang = user_lang.get(user_id, LanguageDetector.detect(text))
                    
                    await registrar_mensaje(user_id, "incoming", text)
                    fase = pedido_estado.get(user_id, {}).get("fase", "inicio")
                    
                    if fase == "seleccion_idioma":
                        mapas = {'1':'spanish','2':'english','3':'french','4':'darija_latin','5':'darija_arabic'}
                        if text in mapas:
                            user_lang[user_id] = mapas[text]
                            user_idioma_manual[user_id] = True
                            resp = f"{LanguageDetector.get_welcome(user_lang[user_id])}\n{LanguageDetector.get_help(user_lang[user_id])}"
                            await send_message(user_id, resp)
                            await registrar_mensaje(user_id, "outgoing", resp)
                            pedido_estado.pop(user_id, None)
                        else: await send_message(user_id, "❌ Elige 1-5.")
                        continue
                    if fase in ["entrega","check_zona","direccion","pago","cash_bill","transfer_pending"]:
                        funcs = {"entrega":procesar_entrega,"check_zona":procesar_zona,"direccion":procesar_direccion,"pago":procesar_pago,"cash_bill":procesar_billete,"transfer_pending":procesar_transferencia}
                        inp = text if fase=="direccion" else text_lower
                        resp = await funcs[fase](user_id, inp, lang)
                        await send_message(user_id, resp)
                        await registrar_mensaje(user_id, "outgoing", resp)
                        continue
                    
                    if text_lower in ['hola','hello','salam','hi','bonjour','hallo','merhaba','سلام']:
                        carts[user_id] = []
                        if user_id not in user_lang or not user_idioma_manual.get(user_id, False):
                            await send_message(user_id, "🌍 *Bienvenido*\n1. 🇪🇸 Español 2. 🇬🇧 English 3. 🇫🇷 Français 4. 🇲🇦 Darija 5. 🇲🇦 العربية")
                            pedido_estado[user_id] = {"fase": "seleccion_idioma"}
                        else:
                            resp = f"{LanguageDetector.get_welcome(lang)}\n{LanguageDetector.get_help(lang)}"
                            await send_message(user_id, resp)
                            await registrar_mensaje(user_id, "outgoing", resp)
                        continue
                    
                    elif text_lower in ['menu','menú']:
                        menu_txt, _ = await get_restaurant_menu(client_id, lang, waba=True)
                        await send_message(user_id, menu_txt)
                        await registrar_mensaje(user_id, "outgoing", "menu")
                        continue
                    
                    elif text_lower in ['pedido','order','cart','carrito']:
                        resp = await get_cart(user_id, lang)
                        await send_message(user_id, resp)
                        await registrar_mensaje(user_id, "outgoing", resp)
                        continue
                    
                    elif text_lower in ['confirmar','confirm','checkout']:
                        if not carts.get(user_id) or sum(it["price"] for it in carts[user_id]) <= 0:
                            await send_message(user_id, "⚠️ Carrito vacío. Añade productos primero.")
                        else:
                            resp = await iniciar_entrega(user_id, lang)
                            await send_message(user_id, resp)
                            await registrar_mensaje(user_id, "outgoing", resp)
                        continue
                    
                    elif text_lower in ['help','ayuda','aide','commands']:
                        await send_message(user_id, LanguageDetector.get_help(lang))
                        continue
                    
                    elif text_lower.isdigit():
                        resp = await add_to_cart(user_id, int(text_lower), 1, client_id, lang)
                        await send_message(user_id, resp)
                        await registrar_mensaje(user_id, "outgoing", resp)
                        continue
                    
                    elif re.match(r'(eliminar|quitar|borrar|remove)\s+', text_lower):
                        parts = text_lower.split(maxsplit=1)
                        if len(parts)>1:
                            resto = parts[1].strip()
                            if resto.isdigit(): resp = await remove_from_cart_by_index(user_id, int(resto), lang)
                            else: resp = await clear_cart(user_id, lang)
                            await send_message(user_id, resp)
                            await registrar_mensaje(user_id, "outgoing", resp)
                        continue
                    elif text_lower in ['reservar','reservación','reservation','book','table']:
                        pedido_estado[user_id] = {"fase": "reserva_personas"}
                        await send_message(user_id, "📅 *Reservar mesa en Restinga*\n¿Para cuántas personas? (1-20)")
                        continue
                    
                    elif fase == "reserva_personas":
                        if text_lower.isdigit() and 1 <= int(text_lower) <= 20:
                            pedido_estado[user_id]["people"] = int(text_lower)
                            pedido_estado[user_id]["fase"] = "reserva_fecha"
                            await send_message(user_id, "🕐 ¿Para qué día y hora?\nEj: 'Mañana 20:00' o '15/05 21:30'")
                        else:
                            await send_message(user_id, "❌ Por favor, escribe un número entre 1 y 20.")
                        continue
                    
                    elif fase == "reserva_fecha":
                        # Parseo simple: aceptar "15/05 21:30" o "Mañana 20:00"
                        try:
                            if "mañana" in text_lower or "manana" in text_lower:
                                from datetime import timedelta
                                fecha = (datetime.now() + timedelta(days=1)).date()
                            else:
                                # Intentar parsear "DD/MM HH:MM"
                                partes = text_lower.split()
                                fecha_str, hora_str = partes[0], partes[1] if len(partes)>1 else "20:00"
                                dia, mes = map(int, fecha_str.split('/'))
                                fecha = datetime.now().replace(day=dia, month=mes).date()
                                hora_str = hora_str.replace(':', '')
                                hora, minuto = int(hora_str[:2]), int(hora_str[2:4]) if len(hora_str)>=4 else 0
                            # Guardar reserva (fallback si tabla no existe)
                            if supabase:
                                try:
                                    supabase.table("reservations").insert({
                                        "client_id": client_id,
                                        "cliente_telefono": user_id,
                                        "people_count": pedido_estado[user_id]["people"],
                                        "reservation_date": fecha.isoformat(),
                                        "reservation_time": f"{hora:02d}:{minuto:02d}:00",
                                        "status": "pending",
                                        "created_at": datetime.now().isoformat()
                                    }).execute()
                                except Exception as e:
                                    logger.warning(f"⚠️ Reservas: {e}")
                            # Confirmar al usuario
                            await send_message(user_id, f"✅ *Solicitud recibida*\n👥 {pedido_estado[user_id]['people']} personas | 📅 {fecha} {hora:02d}:{minuto:02d}\n📞 Te confirmaremos en ≤10 minutos.")
                            # Notificar a recepción
                            await send_message('212668087490', f"🆕 *Nueva reserva pendiente*\n👥 {pedido_estado[user_id]['people']} pax | 📅 {fecha} {hora:02d}:{minuto:02d}\n📞 Cliente: {user_id}")
                            pedido_estado.pop(user_id, None)
                        except:
                            await send_message(user_id, "❌ Formato no reconocido. Ej: '15/05 21:30' o 'Mañana 20:00'")
                        continue
                                        
                                        else:
                                            await send_message(user_id, LanguageDetector.get_help(lang))
                                            
                        except Exception as e:
                            logger.error(f"❌ Error process_message: {e}", exc_info=True)
                    
# ========== WEBHOOK & ENDPOINTS ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    p = request.query_params
    if p.get("hub.mode")=="subscribe" and p.get("hub.verify_token")==VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge"))
    raise HTTPException(403, detail="Verification failed")

@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, bg: BackgroundTasks):
    try:
        bg.add_task(process_message, await request.json())
        return JSONResponse({"status":"ok"})
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return JSONResponse({"status":"error"}, status_code=500)

@app.get("/")
async def root(): return {"status":"ok","version":VERSION,"service":"Orquestrator ISA"}

@app.get("/health")
async def health():
    return {"status":"ok","version":VERSION,"supabase":supabase is not None,"whatsapp":bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),"languages":len(LANGUAGES),"timestamp":datetime.utcnow().isoformat()}

@app.post("/admin/refresh-schema")
async def refresh_schema(request: Request):
    if request.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}": raise HTTPException(401)
    if supabase: supabase.table("messages").select("count").limit(1).execute()
    return {"status":"ok","message":"Schema cache refreshed"}

@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} starting...")
    await load_phone_mapping()
    if supabase: supabase.table("messages").select("id").limit(1).execute()
    logger.info(f"✅ {len(LANGUAGES)} languages loaded: {list(LANGUAGES.keys())}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
