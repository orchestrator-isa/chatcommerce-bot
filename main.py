#!/usr/bin/env python3
import os, logging, re, json, uuid, httpx
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
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

# Cliente Supabase SÍNCRONO (NO usar await con supabase.table())
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

# ========== DETECTOR DE IDIOMA (DEFINIDO ANTES DE USAR) ==========
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

# ========== MAPEO DE TELÉFONOS ==========
async def load_phone_mapping():
    global phone_to_restaurant, clientes_validados
    try:
        if not supabase:
            return
        result = supabase.table("restaurantes").select("id_restaurante, telefono").eq("is_active", True).execute()
        phone_to_restaurant = {}
        for r in result.data:
            tel = r.get("telefono", "").replace("+", "")
            phone_to_restaurant[tel] = r["id_restaurante"]
            phone_to_restaurant[r["telefono"]] = r["id_restaurante"]
        
        # Hardcode para Restinga y números de prueba
        phone_to_restaurant['212626282904'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['212668087490'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['5217225529803'] = '44444444-4444-4444-4444-444444444444'
        
        try:
            result = supabase.table("valid_clients").select("telefono").execute()
            for r in result.data:
                clientes_validados.add(r.get("telefono", ""))
        except:
            pass
        logger.info(f"📞 {len(phone_to_restaurant)} restaurantes mapeados")
    except Exception as e:
        logger.error(f"Error mapeo: {e}")

# ========== REGISTRAR MENSAJE (con fallback PGRST204) ==========
async def registrar_mensaje(user_id: str, direccion: str, mensaje: str, intent: str=None):
    try:
        if not supabase:
            return
        session_id = session_activa.get(user_id)
        if not session_id:
            session_id = str(uuid.uuid4())
            session_activa[user_id] = session_id
            try:
                supabase.table("sessions").insert({"id":session_id,"user_id":user_id,"inicio":datetime.now().isoformat(),"estado":"activa"}).execute()
            except Exception as e:
                logger.warning(f"⚠️ sessions insert: {e}")
        try:
            supabase.table("messages").insert({"session_id":session_id,"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
        except Exception as e:
            if "pgrst204" in str(e).lower() or "session_id" in str(e).lower():
                logger.warning("⚠️ session_id fallback")
                supabase.table("messages").insert({"direccion":direccion,"message":mensaje[:500],"intent":intent,"created_at":datetime.now().isoformat()}).execute()
            else:
                raise
    except Exception as e:
        logger.error(f"❌ registrar_mensaje: {e}")

# ========== FUNCIONES DE MENÚ, CARRITO Y PEDIDOS ==========
async def get_restaurant_menu(client_id: str, user_lang_code: str = 'spanish', waba: bool = True) -> tuple:
    try:
        if not supabase:
            return "❌ Error de conexión", []
        query = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True)
        if waba:
            query = query.eq("show_on_waba", True)
        result = query.execute()
        if not result.data:
            return "📋 *MENÚ*\nNo hay platos disponibles.", []
        menu_lines = ["📋 *MENÚ RESTINGA*", ""]
        for i, item in enumerate(result.data, 1):
            if item['price'] == 0:
                menu_lines.append(f"{i}. 🍽️ *{item['dish_name']}* — 🆓 GRATIS con bebida")
            else:
                menu_lines.append(f"{i}. 🍽️ *{item['dish_name']}* — {item['price']} MAD")
            if item.get('description'):
                menu_lines.append(f"   📝 {item['description']}")
            menu_lines.append("")
        return "\n".join(menu_lines), result.data
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error cargando menú", []

async def add_to_cart(user_id: str, item_index: int, cantidad: int, client_id: str, lang: str) -> str:
    try:
        _, platos = await get_restaurant_menu(client_id, lang, waba=True)
        if not platos or item_index > len(platos):
            return get_text(lang, 'help')
        selected = platos[item_index - 1]
        if user_id not in carts:
            carts[user_id] = []
        if selected["price"] == 0:
            tiene_producto_pago = any(item["price"] > 0 for item in carts.get(user_id, []))
            if not tiene_producto_pago:
                return f"📌 *{selected['dish_name']}* es GRATIS con la compra de una bebida u otro producto.\nPor favor, añade una bebida al carrito primero (ej: escribe '18' para Té a la menta - 10 MAD)"
        for _ in range(cantidad):
            carts[user_id].append({"name": selected["dish_name"], "price": selected["price"]})
        total = sum(item["price"] for item in carts[user_id])
        return get_text(lang, 'added_to_cart', cantidad=cantidad, nombre=selected['dish_name'], total=total)
    except Exception as e:
        logger.error(f"Error carrito: {e}")
        return "❌ Error al añadir"

async def get_cart(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return get_text(lang, 'cart_empty')
    items_dict = {}
    for item in carts[user_id]:
        name = item["name"]
        if name not in items_dict:
            items_dict[name] = {"price": item["price"], "cantidad": 0}
        items_dict[name]["cantidad"] += 1
    total = sum(item["price"] for item in carts[user_id])
    item_lines = []
    for name, data in items_dict.items():
        if data["price"] == 0:
            item_lines.append(f"• {name} — 🆓 GRATIS (con bebida)")
        elif data["cantidad"] > 1:
            item_lines.append(f"• {name} x{data['cantidad']} — {data['cantidad'] * data['price']} MAD")
        else:
            item_lines.append(f"• {name} — {data['price']} MAD")
    items_text = "\n".join(item_lines)
    return f"🛒 *TU PEDIDO*\n{items_text}\n💰 *TOTAL: {total} MAD*\nEscribe *CONFIRMAR* para finalizar."

async def iniciar_entrega(user_id: str, lang: str) -> str:
    pedido_estado[user_id] = {"fase": "entrega"}
    return get_text(lang, 'delivery_type')

async def procesar_entrega(user_id: str, text: str, lang: str) -> str:
    global restaurant_status
    if text == '1':
        pedido_estado[user_id]["tipo_entrega"] = "recoger"
        pedido_estado[user_id]["fase"] = "pago"
        total = sum(item["price"] for item in carts.get(user_id, []))
        tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])["recoger"]
        status_text = {"normal": "🟢 Normal", "moderado": "🟡 Moderado", "lleno": "🔴 Lleno"}.get(restaurant_status, "🟢 Normal")
        return f"✅ *Recogida en local*\n📊 Estado: {status_text}\n⏱️ Tiempo estimado: {tiempo}\n💰 Total: {total} MAD\n{get_text(lang, 'payment_method')}"
    elif text == '2':
        pedido_estado[user_id]["tipo_entrega"] = "domicilio"
        pedido_estado[user_id]["fase"] = "check_zona"
        return get_text(lang, 'delivery_zone_check')
    return "❌ Opción no válida. Escribe *1* (Recoger) o *2* (Domicilio)."

async def procesar_zona(user_id: str, text: str, lang: str) -> str:
    text_lower = text.lower().strip()
    if text_lower in ['si', 'sí', 'yes', 'oui']:
        pedido_estado[user_id]["fase"] = "direccion"
        return get_text(lang, 'address_request')
    else:
        pedido_estado[user_id]["fase"] = "pago_recoger_forzado"
        return get_text(lang, 'delivery_out_of_zone')

async def procesar_direccion(user_id: str, direccion: str, lang: str) -> str:
    pedido_estado[user_id]["direccion"] = direccion
    pedido_estado[user_id]["fase"] = "pago"
    total = sum(item["price"] for item in carts.get(user_id, []))
    return f"📍 Dirección guardada.\n💰 Total: {total} MAD\n{get_text(lang, 'payment_method')}"

async def procesar_pago(user_id: str, text: str, lang: str) -> str:
    if text == '1':
        pedido_estado[user_id]["metodo_pago"] = "efectivo"
        pedido_estado[user_id]["fase"] = "cash_bill"
        return get_text(lang, 'cash_bill')
    elif text == '2':
        if user_id not in clientes_validados and user_id not in ['212668087490', '212626282904']:
            return get_text(lang, 'transfer_unverified')
        pedido_estado[user_id]["metodo_pago"] = "transferencia"
        pedido_estado[user_id]["fase"] = "transfer_pending"
        return get_text(lang, 'transfer_pending')
    return "❌ Opción no válida. Escribe *1* (Efectivo) o *2* (Transferencia)."

async def procesar_billete(user_id: str, text: str, lang: str) -> str:
    global restaurant_status
    try:
        billete = int(text)
        if billete in [100, 200] and restaurant_status == "lleno":
            return get_text(lang, 'cash_no_change', bill=billete)
        pedido_estado[user_id]["billete"] = str(billete)
        total = sum(item["price"] for item in carts.get(user_id, []))
        if total <= 0:
            return "⚠️ *No se puede confirmar el pedido*\nEl total es 0 MAD."
        cambio = billete - total
        if cambio < 0:
            return f"⚠️ *El billete de {billete} MAD no es suficiente.*\nEl total es {total} MAD."
        tipo = pedido_estado[user_id].get("tipo_entrega", "recoger")
        tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])[tipo]
        carts[user_id] = []
        if user_id in pedido_estado:
            del pedido_estado[user_id]
        metodo_texto = f"Efectivo con {billete} MAD" + (f" (cambio: {cambio} MAD)" if cambio > 0 else "")
        return get_text(lang, 'order_confirmed', numero="107", total=total, metodo=metodo_texto, tiempo=tiempo)
    except ValueError:
        return "❌ Por favor, responde con el número del billete (ej: 50, 100, 200)"

async def procesar_transferencia(user_id: str, lang: str) -> str:
    global restaurant_status
    total = sum(item["price"] for item in carts.get(user_id, []))
    if total <= 0:
        return "⚠️ *No se puede confirmar el pedido*\nEl total es 0 MAD."
    tipo = pedido_estado[user_id].get("tipo_entrega", "recoger")
    tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])[tipo]
    carts[user_id] = []
    if user_id in pedido_estado:
        del pedido_estado[user_id]
    return get_text(lang, 'order_confirmed', numero="107", total=total, metodo="Transferencia (pendiente validación)", tiempo=tiempo)

async def remove_from_cart_by_name(user_id: str, nombre_buscar: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return get_text(lang, 'cart_empty')
    nombre_buscar_lower = nombre_buscar.lower().strip()
    eliminados, nuevos_items = [], []
    for item in carts[user_id]:
        if nombre_buscar_lower in item["name"].lower():
            eliminados.append(item)
        else:
            nuevos_items.append(item)
    if not eliminados:
        return f"❌ No encontré '{nombre_buscar}' en tu carrito."
    carts[user_id] = nuevos_items
    total = sum(item["price"] for item in carts[user_id])
    return get_text(lang, 'removed_item', cantidad=len(eliminados), nombre=eliminados[0]["name"], total=total)

async def remove_from_cart_by_index(user_id: str, item_index: int, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return get_text(lang, 'cart_empty')
    if item_index < 1 or item_index > len(carts[user_id]):
        return f"❌ Número inválido. El carrito tiene {len(carts[user_id])} platos."
    removed = carts[user_id].pop(item_index - 1)
    total = sum(item["price"] for item in carts[user_id])
    return get_text(lang, 'removed_item', cantidad=1, nombre=removed['name'], total=total)

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts:
        carts[user_id] = []
        return "🗑️ *Carrito vaciado* completamente."
    return get_text(lang, 'cart_empty')

# ========== ENVIAR MENSAJE WHATSAPP ==========
async def send_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error(f"❌ WhatsApp NO configurado")
        return False
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message[:1600]}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=data)
            logger.info(f"📡 WhatsApp API: {response.status_code} | {response.text[:200]}")
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

# ========== PROCESAR MENSAJE ==========
async def process_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                display_phone = metadata.get("display_phone_number", "").replace("+", "")
                client_id = phone_to_restaurant.get(display_phone) or phone_to_restaurant.get('212626282904') or "44444444-4444-4444-4444-444444444444"
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        user_id = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        text_lower = text.lower().strip()
                        lang = LanguageDetector.detect(text)
                        user_lang_code = user_lang.get(user_id, lang)
                        await registrar_mensaje(user_id, "incoming", text)
                        estado = pedido_estado.get(user_id, {})
                        fase = estado.get("fase", "inicio")
                        
                        # Manejo de fases
                        if fase == "seleccion_idioma":
                            idiomas = {'1':'spanish','2':'english','3':'french','4':'darija_latin','5':'darija_arabic'}
                            if text in idiomas:
                                user_lang[user_id] = idiomas[text]
                                user_idioma_manual[user_id] = True
                                response = f"{LanguageDetector.get_welcome(user_lang[user_id])}\n{LanguageDetector.get_help(user_lang[user_id])}"
                                sent = await send_message(user_id, response)
                                if sent: await registrar_mensaje(user_id, "outgoing", response)
                                if user_id in pedido_estado: del pedido_estado[user_id]
                            else:
                                await send_message(user_id, "❌ Opción no válida. Elige 1-5.")
                            continue
                        
                        if fase in ["entrega","check_zona","direccion","pago","cash_bill","transfer_pending"]:
                            funcs = {"entrega":procesar_entrega,"check_zona":procesar_zona,"direccion":procesar_direccion,"pago":procesar_pago,"cash_bill":procesar_billete,"transfer_pending":procesar_transferencia}
                            response = await funcs[fase](user_id, text_lower if fase!="direccion" else text, user_lang_code)
                            sent = await send_message(user_id, response)
                            if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        # Comandos principales
                        if text_lower in ['hola','hello','salam','hi','bonjour','hallo','merhaba','سلام']:
                            if user_id in carts: carts[user_id] = []
                            if user_id in pedido_estado: del pedido_estado[user_id]
                            if user_id not in user_lang or not user_idioma_manual.get(user_id, False):
                                lang_options = """🌍 *Bienvenido a Restinga Restaurant*
*Selecciona tu idioma:*
1. 🇪🇸 Español  2. 🇬🇧 English  3. 🇫🇷 Français
4. 🇲🇦 Darija  5. 🇲🇦 العربية
Responde con el número:"""
                                await send_message(user_id, lang_options)
                                pedido_estado[user_id] = {"fase": "seleccion_idioma"}
                            else:
                                response = f"{LanguageDetector.get_welcome(user_lang_code)}\n{LanguageDetector.get_help(user_lang_code)}"
                                sent = await send_message(user_id, response)
                                if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif text_lower in ['menu','menú']:
                            menu_text, _ = await get_restaurant_menu(client_id, user_lang_code, waba=True)
                            sent = await send_message(user_id, menu_text)
                            if sent: await registrar_mensaje(user_id, "outgoing", menu_text[:200]+"...")
                            await send_message(user_id, LanguageDetector.get_help(user_lang_code))
                            continue
                        
                        elif text_lower in ['pedido','order','cart','carrito']:
                            response = await get_cart(user_id, user_lang_code)
                            sent = await send_message(user_id, response)
                            if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif text_lower in ['confirmar','confirm','checkout']:
                            total = sum(item["price"] for item in carts.get(user_id, []))
                            if total <= 0:
                                response = "⚠️ *No se puede confirmar*\nTu carrito está vacío."
                            elif user_id in carts and carts[user_id]:
                                response = await iniciar_entrega(user_id, user_lang_code)
                            else:
                                response = get_text(user_lang_code, 'cart_empty')
                            sent = await send_message(user_id, response)
                            if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif text_lower in ['help','ayuda','aide','commands']:
                            response = LanguageDetector.get_help(user_lang_code)
                            sent = await send_message(user_id, response)
                            if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif text_lower.isdigit():
                            response = await add_to_cart(user_id, int(text_lower), 1, client_id, user_lang_code)
                            sent = await send_message(user_id, response)
                            if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif re.match(r'(eliminar|quitar|borrar|remove)\s+', text_lower):
                            parts = text_lower.split(maxsplit=1)
                            if len(parts) > 1:
                                resto = parts[1].strip()
                                if resto in ['todo','all']:
                                    response = await clear_cart(user_id, user_lang_code)
                                elif resto.isdigit():
                                    response = await remove_from_cart_by_index(user_id, int(resto), user_lang_code)
                                else:
                                    response = await remove_from_cart_by_name(user_id, resto, user_lang_code)
                                sent = await send_message(user_id, response)
                                if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        else:
                            match = re.match(r'(\d+)\s+(.+)', text_lower)
                            if match:
                                cantidad, nombre = int(match.group(1)), match.group(2).strip()
                                _, platos = await get_restaurant_menu(client_id, user_lang_code, waba=True)
                                for i, plato in enumerate(platos, 1):
                                    if nombre in plato['dish_name'].lower():
                                        response = await add_to_cart(user_id, i, cantidad, client_id, user_lang_code)
                                        sent = await send_message(user_id, response)
                                        if sent: await registrar_mensaje(user_id, "outgoing", response)
                                        break
                                else:
                                    response = LanguageDetector.get_help(user_lang_code)
                                    sent = await send_message(user_id, response)
                                    if sent: await registrar_mensaje(user_id, "outgoing", response)
                            else:
                                response = LanguageDetector.get_help(user_lang_code)
                                sent = await send_message(user_id, response)
                                if sent: await registrar_mensaje(user_id, "outgoing", response)
                            continue
    except Exception as e:
        logger.error(f"❌ Error en process_message: {e}", exc_info=True)
        if supabase:
            try:
                supabase.table("logs_registro").insert({"level":"ERROR","message":str(e)[:500],"source":"process_message","created_at":datetime.now().isoformat()}).execute()
            except: pass

# ========== WEBHOOK WHATSAPP ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        logger.info("✅ Webhook verified by Meta")
        return PlainTextResponse(params.get("hub.challenge"))
    logger.warning(f"❌ Verificación fallida")
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT",8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
