#!/usr/bin/env python3
import os
import logging
import re
import json
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from supabase import create_client
from functools import lru_cache
from typing import Dict, List, Optional
import httpx

VERSION = "6.3-MULTI-IDIOMA-FIXED"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("isa-bot")

# ========== CONFIGURACIÓN ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ========== ARCHIVOS ESTÁTICOS ==========
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ========== CARGA DE IDIOMAS ==========
LANG_DIR = Path("lang")
LANGUAGES = {}

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

LANG_MAP = {
    'english': 'en',
    'spanish': 'en',
    'darija_latin': 'dar',
    'darija_arabic': 'ar',
    'french': 'fr',
    'turkish': 'tr',
    'german': 'de'
}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    """Obtiene texto en el idioma solicitado"""
    file_key = LANG_MAP.get(lang_code, 'en')
    texts = LANGUAGES.get(file_key, LANGUAGES.get('en', {}))
    template = texts.get(key, LANGUAGES['en'].get(key, key))
    return template.format(**kwargs) if kwargs else template

# ========== CARITOS Y ESTADOS ==========
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
pedido_estado: Dict[str, dict] = {}

# ========== MAPEO DE TELÉFONOS ==========
phone_to_restaurant: Dict[str, str] = {}

async def load_phone_mapping():
    global phone_to_restaurant
    try:
        if not supabase:
            return
        result = supabase.table("restaurantes").select("id_restaurante, telefono").eq("is_active", True).execute()
        phone_to_restaurant = {}
        for r in result.data:
            telefono = r.get("telefono", "")
            if telefono:
                phone_to_restaurant[telefono.replace("+", "")] = r["id_restaurante"]
                phone_to_restaurant[telefono] = r["id_restaurante"]
        logger.info(f"📞 {len(phone_to_restaurant)} restaurantes mapeados")
    except Exception as e:
        logger.error(f"Error mapeo: {e}")

# ========== SINÓNIMOS PARA COMANDOS ==========
SINONIMOS = {
    'recoge': ['recoge', 'recoger', 'recojo', 'local', 'tienda', 'presencial'],
    'domicilio': ['domicilio', 'envío', 'entrega', 'casa', 'delivery'],
    'efectivo': ['efectivo', 'cash', 'dinero'],
    'transferencia': ['transferencia', 'transfer', 'bancaria']
}

def match_comando(text: str, comando: str) -> bool:
    text_lower = text.lower().strip()
    for sinonimo in SINONIMOS[comando]:
        if sinonimo in text_lower:
            return True
    return False

# ========== NLP: DETECCIÓN DE IDIOMA ==========
class LanguageDetector:
    KEYWORDS = {
        'english': ['hello', 'hi', 'menu', 'thank', 'want'],
        'spanish': ['hola', 'menu', 'gracias', 'quiero', 'plato'],
        'french': ['bonjour', 'salut', 'menu', 'merci'],
        'german': ['hallo', 'menü', 'danke'],
        'turkish': ['merhaba', 'menü', 'teşekkür'],
        'darija_latin': ['salam', 'menu', 'marhba', 'bghit', 'shhal'],
        'darija_arabic': ['سلام', 'قائمة', 'مرحبا', 'بغيت', 'شحال']
    }
    
    @classmethod
    def detect(cls, text: str) -> str:
        text_lower = text.lower().strip()
        if any('\u0600' <= c <= '\u06FF' for c in text):
            return 'darija_arabic'
        scores = {lang: sum(1 for k in keywords if k in text_lower) 
                  for lang, keywords in cls.KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else 'english'
    
    @classmethod
    def get_welcome(cls, lang: str) -> str:
        return get_text(lang, 'welcome')
    
    @classmethod
    def get_help(cls, lang: str) -> str:
        return get_text(lang, 'help')

# ========== MENÚ ==========
async def get_restaurant_menu(client_id: str, user_lang_code: str = 'english') -> tuple:
    try:
        if not supabase:
            return "❌ Error de conexión", []
        result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        if not result.data:
            return "📋 *MENU*\nNo dishes available.", []
        
        if user_lang_code == 'english':
            menu_header = "📋 *MENU*"
        else:
            menu_header = "📋 *MENÚ DE EL REDUCTO*"
        
        menu_lines = [menu_header, ""]
        for i, item in enumerate(result.data, 1):
            menu_lines.append(f"{i}. 🍽️ *{item['dish_name']}* — {item['price']} MAD")
            if item.get('description'):
                menu_lines.append(f"   📝 {item['description']}")
            menu_lines.append("")
        return "\n".join(menu_lines), result.data
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error loading menu", []

# ========== FUNCIONES DEL CARRITO ==========
async def add_to_cart(user_id: str, item_index: int, cantidad: int, client_id: str, lang: str) -> str:
    try:
        _, platos = await get_restaurant_menu(client_id, lang)
        if not platos or item_index > len(platos):
            return get_text(lang, 'help')
        selected = platos[item_index - 1]
        
        if user_id not in carts:
            carts[user_id] = []
        
        for _ in range(cantidad):
            carts[user_id].append({"name": selected["dish_name"], "price": selected["price"]})
        
        total = sum(item["price"] for item in carts[user_id])
        return get_text(lang, 'added_to_cart', 
                       cantidad=cantidad, 
                       nombre=selected['dish_name'], 
                       total=total)
    except Exception as e:
        logger.error(f"Error carrito: {e}")
        return "❌ Error adding to cart"

async def remove_from_cart_by_name(user_id: str, nombre_buscar: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return get_text(lang, 'cart_empty')
    
    nombre_buscar_lower = nombre_buscar.lower().strip()
    eliminados = []
    nuevos_items = []
    
    for item in carts[user_id]:
        if nombre_buscar_lower in item["name"].lower():
            eliminados.append(item)
        else:
            nuevos_items.append(item)
    
    if not eliminados:
        return f"❌ Could not find '{nombre_buscar}' in your cart."
    
    carts[user_id] = nuevos_items
    total = sum(item["price"] for item in carts[user_id])
    cantidad = len(eliminados)
    nombre = eliminados[0]["name"]
    return get_text(lang, 'removed_item', 
                   cantidad=cantidad, 
                   nombre=nombre, 
                   total=total)

async def remove_from_cart_by_index(user_id: str, item_index: int, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return get_text(lang, 'cart_empty')
    
    if item_index < 1 or item_index > len(carts[user_id]):
        return f"❌ Invalid number. Cart has {len(carts[user_id])} items."
    
    removed = carts[user_id].pop(item_index - 1)
    total = sum(item["price"] for item in carts[user_id])
    return get_text(lang, 'removed_item', 
                   cantidad=1, 
                   nombre=removed['name'], 
                   total=total)

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts:
        carts[user_id] = []
        return "🗑️ *Cart cleared* completely."
    return get_text(lang, 'cart_empty')

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
        if data["cantidad"] > 1:
            item_lines.append(f"• {name} x{data['cantidad']} — {data['cantidad'] * data['price']} MAD")
        else:
            item_lines.append(f"• {name} — {data['price']} MAD")
    
    items_text = "\n".join(item_lines)
    confirm_text = "Type *CONFIRM* to checkout" if lang == 'english' else "Escribe *CONFIRMAR* para finalizar"
    return f"🛒 *YOUR ORDER*\n\n{items_text}\n\n💰 *TOTAL: {total} MAD*\n\n{confirm_text}"

# ========== FUNCIONES DE PEDIDOS (TICKETS) ==========
async def guardar_pedido(user_id: str, cliente_nombre: str, items: list, total: int, tipo_entrega: str = None, direccion: str = None, metodo_pago: str = None) -> dict:
    try:
        if not supabase:
            return {"error": "Database not connected"}
        
        items_json = []
        for item in items:
            items_json.append({
                "name": item["name"],
                "price": item["price"],
                "cantidad": item.get("cantidad", 1)
            })
        
        data = {
            "id_cliente": str(uuid.uuid4()),
            "cliente_telefono": user_id,
            "items_json": items_json,
            "total_mad": total,
            "estado": "nuevo",
            "tipo_entrega": tipo_entrega,
            "direccion": direccion,
            "metodo_pago": metodo_pago,
            "pagado": False,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase.table("pedidos").insert(data).execute()
        
        if result.data:
            pedido = result.data[0]
            return {"numero": pedido.get("numero"), "id": pedido.get("id_pedido"), "estado": pedido.get("estado")}
        return {"error": "Failed to create order"}
    except Exception as e:
        logger.error(f"Error guardando pedido: {e}")
        return {"error": str(e)}

# ========== ENVIAR MENÚ EN PDF ==========
async def enviar_menu_pdf(to: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp not configured for PDF")
        return False
    
    pdf_url = "https://isa-bot-prod.onrender.com/static/El_Reducto_Experience.pdf"
    
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "document",
        "document": {
            "link": pdf_url,
            "filename": "Menu_El_Reducto.pdf"
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        if response.status_code == 200:
            logger.info(f"✅ PDF menu sent to {to}")
            return True
        else:
            logger.error(f"❌ Error sending PDF: {response.text}")
            return False

# ========== PROCESAR ELIMINAR ==========
async def process_remove_command(user_id: str, text: str, lang: str) -> str:
    text_lower = text.lower().strip()
    remove_words = ['eliminar', 'elimina', 'quitar', 'borrar', 'remove', 'delete']
    
    is_remove = any(text_lower.startswith(word) for word in remove_words)
    if not is_remove:
        return None
    
    if 'todo' in text_lower or 'all' in text_lower or 'carrito' in text_lower or 'cart' in text_lower:
        return await clear_cart(user_id, lang)
    
    resto = text_lower
    for word in remove_words:
        if text_lower.startswith(word):
            resto = text_lower[len(word):].strip()
            break
    
    if resto.isdigit():
        return await remove_from_cart_by_index(user_id, int(resto), lang)
    if resto:
        return await remove_from_cart_by_name(user_id, resto, lang)
    return None

# ========== PROCESAR CANTIDAD ==========
def parse_quantity_command(text: str, platos: List[dict]) -> tuple:
    match = re.match(r'(\d+)\s+(.+)', text.lower().strip())
    if not match:
        return None, None
    cantidad = int(match.group(1))
    nombre_buscar = match.group(2).strip()
    for i, plato in enumerate(platos, 1):
        if nombre_buscar in plato['dish_name'].lower():
            return i, cantidad
    return None, None

# ========== PROCESAR CONFIRMACIÓN ==========
async def iniciar_entrega(user_id: str, lang: str) -> str:
    pedido_estado[user_id] = {"fase": "entrega"}
    return get_text(lang, 'delivery_type')

async def procesar_entrega(user_id: str, text: str, lang: str) -> str:
    if text == '1' or match_comando(text, 'recoge'):
        pedido_estado[user_id]["tipo_entrega"] = "recoge"
        pedido_estado[user_id]["fase"] = "pago"
        total = sum(item["price"] for item in carts.get(user_id, []))
        pickup_text = "✅ *Pickup at location*" if lang == 'english' else "✅ *Recogida en local*"
        time_text = "⏱️ Time: 5-10 minutes" if lang == 'english' else "⏱️ Tiempo: 5-10 minutos"
        return f"{pickup_text}\n{time_text}\n\n💰 Total: {total} MAD\n\n{get_text(lang, 'payment_method')}"
    elif text == '2' or match_comando(text, 'domicilio'):
        pedido_estado[user_id]["tipo_entrega"] = "domicilio"
        pedido_estado[user_id]["fase"] = "direccion"
        return get_text(lang, 'address_request')
    return "❌ Invalid option. Type *1* (Pickup) or *2* (Delivery)."

async def procesar_direccion(user_id: str, direccion: str, lang: str) -> str:
    pedido_estado[user_id]["direccion"] = direccion
    pedido_estado[user_id]["fase"] = "pago"
    total = sum(item["price"] for item in carts.get(user_id, []))
    address_saved = "📍 Address saved." if lang == 'english' else "📍 Dirección guardada."
    return f"{address_saved}\n\n💰 Total: {total} MAD\n\n{get_text(lang, 'payment_method')}"

async def procesar_pago(user_id: str, text: str, lang: str) -> str:
    if text == '1' or match_comando(text, 'efectivo'):
        metodo = "Cash" if lang == 'english' else "Efectivo"
        pedido_estado[user_id]["metodo_pago"] = "efectivo"
    elif text == '2' or match_comando(text, 'transferencia'):
        metodo = "Bank Transfer" if lang == 'english' else "Transferencia"
        pedido_estado[user_id]["metodo_pago"] = "transferencia"
    else:
        return "❌ Invalid option. Type *1* (Cash) or *2* (Transfer)."
    
    total = sum(item["price"] for item in carts.get(user_id, []))
    
    items_dict = {}
    for item in carts.get(user_id, []):
        name = item["name"]
        if name not in items_dict:
            items_dict[name] = {"name": name, "price": item["price"], "cantidad": 0}
        items_dict[name]["cantidad"] += 1
    items_list = [{"name": v["name"], "price": v["price"], "cantidad": v["cantidad"]} for v in items_dict.values()]
    
    resultado = await guardar_pedido(
        user_id=user_id,
        cliente_nombre=None,
        items=items_list,
        total=total,
        tipo_entrega=pedido_estado[user_id].get("tipo_entrega"),
        direccion=pedido_estado[user_id].get("direccion"),
        metodo_pago=metodo
    )
    
    if user_id in carts:
        carts[user_id] = []
    
    if "error" in resultado:
        return f"❌ Error saving order: {resultado['error']}"
    
    numero = resultado.get("numero", "???")
    tipo = pedido_estado[user_id].get("tipo_entrega", "recoge")
    if lang == 'english':
        tiempo = "5-10 minutes" if tipo == "recoge" else "20-30 minutes"
    else:
        tiempo = "5-10 minutos" if tipo == "recoge" else "20-30 minutos"
    
    if user_id in pedido_estado:
        del pedido_estado[user_id]
    
    return get_text(lang, 'order_confirmed', 
                   numero=numero, total=total, metodo=metodo, tiempo=tiempo)

# ========== WHATSAPP WEBHOOK ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        logger.info("Webhook verified")
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403, "Verification failed")

@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        background_tasks.add_task(process_message, body)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": "error"}

async def process_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                display_phone = metadata.get("display_phone_number", "").replace("+", "")
                client_id = phone_to_restaurant.get(display_phone, "ba4351a0-763f-402d-acf9-30594ce40d87")
                
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        user_id = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        text_lower = text.lower().strip()
                        
                        lang = LanguageDetector.detect(text)
                        user_lang[user_id] = lang
                        logger.info(f"📨 {user_id} [{lang}]: {text[:50]}")
                        
                        estado = pedido_estado.get(user_id, {})
                        fase = estado.get("fase", "inicio")
                        
                        if fase == "entrega":
                            response = await procesar_entrega(user_id, text_lower, lang)
                            await send_message(user_id, response)
                            continue
                        elif fase == "direccion":
                            response = await procesar_direccion(user_id, text, lang)
                            await send_message(user_id, response)
                            continue
                        elif fase == "pago":
                            response = await procesar_pago(user_id, text_lower, lang)
                            await send_message(user_id, response)
                            continue
                        
                        remove_response = await process_remove_command(user_id, text_lower, lang)
                        if remove_response:
                            await send_message(user_id, remove_response)
                            continue
                        
                        if text_lower in ['hola', 'salam', 'hello', 'hi', 'bonjour', 'hallo', 'merhaba', 'سلام']:
                            if user_id in carts:
                                carts[user_id] = []
                            if user_id in pedido_estado:
                                del pedido_estado[user_id]
                            response = LanguageDetector.get_welcome(lang)
                            await send_message(user_id, response)
                            help_text = LanguageDetector.get_help(lang)
                            await send_message(user_id, help_text)
                            response = None
                        
                        elif text_lower in ['menu', 'menú']:
                            menu_text, _ = await get_restaurant_menu(client_id, lang)
                            await send_message(user_id, menu_text)
                            await enviar_menu_pdf(user_id)
                            help_text = LanguageDetector.get_help(lang)
                            await send_message(user_id, help_text)
                            response = None
                        
                        elif text_lower in ['pedido', 'order', 'talab', 'طلب', 'cart']:
                            response = await get_cart(user_id, lang)
                        
                        elif text_lower in ['confirmar', 'confirm', 't2kid', 'تأكيد', 'checkout']:
                            if user_id in carts and carts[user_id]:
                                response = await iniciar_entrega(user_id, lang)
                            else:
                                response = get_text(lang, 'cart_empty')
                        
                        elif text_lower in ['help', 'ayuda', 'aide', 'hilfe', 'yardim', 'مساعدة', 'commands']:
                            response = LanguageDetector.get_help(lang)
                        
                        elif text_lower.isdigit():
                            response = await add_to_cart(user_id, int(text_lower), 1, client_id, lang)
                        
                        else:
                            _, platos = await get_restaurant_menu(client_id, lang)
                            item_num, cantidad = parse_quantity_command(text, platos)
                            if item_num and cantidad:
                                response = await add_to_cart(user_id, item_num, cantidad, client_id, lang)
                            else:
                                response = LanguageDetector.get_help(lang)
                        
                        if response:
                            await send_message(user_id, response)
                        
    except Exception as e:
        logger.error(f"Error procesando: {e}")

async def send_message(to: str, message: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp not configured")
        return
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message[:1600]}}
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

# ========== API ENDPOINTS ==========
@app.get("/")
async def root():
    return {"status": "ok", "version": VERSION, "service": "Orquestrator ISA"}

@app.get("/health")
async def health():
    supabase_status = False
    try:
        supabase.table("restaurantes").select("count", count="exact").limit(1).execute()
        supabase_status = True
    except:
        pass
    return {"status": "healthy", "version": VERSION, "supabase": supabase_status, "whatsapp": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID), "carts_active": len(carts)}

@app.get("/api/version")
async def version():
    return {"version": VERSION, "features": ["Tickets", "PDF", "MultiIdioma", "Carrito", "Entrega", "Pago", "6 Idiomas"]}

@app.get("/api/restaurantes")
async def get_restaurantes():
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    result = supabase.table("restaurantes").select("*").eq("is_active", True).execute()
    return {"restaurantes": result.data, "count": len(result.data)}

@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
    platos = [{"id_plato": r["id"], "nombre": r["dish_name"], "precio": r["price"]} for r in result.data]
    return {"platos": platos, "count": len(platos)}

@app.post("/api/platos")
async def create_plato(item: dict):
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    data = {"client_id": item["client_id"], "dish_name": item["nombre"], "price": item["precio"], "description": item.get("descripcion", ""), "is_available": True}
    result = supabase.table("menu_items").insert(data).execute()
    if result.data:
        return {"plato": {"id_plato": result.data[0]["id"], "nombre": result.data[0]["dish_name"], "precio": result.data[0]["price"]}}
    return {"plato": None}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return await get_platos(client_id)

@app.get("/api/pedidos")
async def get_pedidos(estado: str = None):
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    try:
        hoy = datetime.now().date().isoformat()
        query = supabase.table("pedidos").select("*").gte("created_at", hoy)
        if estado:
            query = query.eq("estado", estado)
        result = query.order("numero", desc=False).execute()
        return {"pedidos": result.data, "count": len(result.data)}
    except Exception as e:
        logger.error(f"Error listing orders: {e}")
        return {"pedidos": [], "count": 0, "error": str(e)}

@app.get("/api/pedido/{numero}")
async def get_pedido(numero: int):
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    try:
        result = supabase.table("pedidos").select("*").eq("numero", numero).execute()
        if not result.data:
            raise HTTPException(404, f"Order #{numero} not found")
        return result.data[0]
    except Exception as e:
        logger.error(f"Error getting order: {e}")
        raise HTTPException(500, str(e))

@app.get("/api/stats/diario")
async def stats_diario():
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    try:
        hoy = datetime.now().date().isoformat()
        result = supabase.table("pedidos").select("*").gte("created_at", hoy).execute()
        pedidos = result.data
        
        total_ventas = sum(p.get("total_mad", 0) for p in pedidos)
        pedidos_por_estado = {estado: len([p for p in pedidos if p.get("estado") == estado]) 
                              for estado in ["nuevo", "confirmado", "listo", "entregado", "cancelado"]}
        
        return {
            "total_pedidos": len(pedidos),
            "total_ventas": total_ventas,
            "pedidos_por_estado": pedidos_por_estado,
            "pedidos_recientes": pedidos[-10:]
        }
    except Exception as e:
        logger.error(f"Error stats: {e}")
        return {"error": str(e)}

@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} starting...")
    await load_phone_mapping()
    logger.info(f"✅ {len(LANGUAGES)} languages loaded: {list(LANGUAGES.keys())}")
    
    dashboard_path = "static/dashboard.html"
    if not os.path.exists(dashboard_path):
        os.makedirs("static", exist_ok=True)
        html_content = """<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard - El Reducto</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui;background:#0f172a;color:#e2e8f0;padding:20px}
h1{color:#22d3ee;margin-bottom:20px}
.pedido{background:#1e293b;border-radius:12px;padding:15px;margin-bottom:15px;border-left:4px solid #22d3ee}
.pedido.nuevo{border-left-color:#fbbf24}
.numero{font-size:1.3rem;font-weight:bold;color:#22d3ee}
.estado{display:inline-block;padding:4px 12px;border-radius:20px;font-size:0.8rem;margin-left:10px}
.estado-nuevo{background:#fbbf24;color:#0f172a}
.total{font-size:1.2rem;font-weight:bold;color:#fbbf24;margin-top:10px}
.btn{background:#0891b2;color:white;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;margin-top:10px;margin-right:10px}
.flex{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
</style>
</head>
<body>
<h1>📋 Dashboard - El Reducto</h1>
<div class="flex"><button class="btn" onclick="cargarPedidos()">🔄 Refresh</button>
<select id="filtro" onchange="cargarPedidos()" style="background:#1e293b;color:white;padding:8px;border-radius:8px">
<option value="">All</option><option value="nuevo">🆕 New</option>
<option value="confirmado">✅ Confirmed</option>
<option value="listo">🍽️ Ready</option>
<option value="entregado">📦 Delivered</option>
</select></div>
<div id="pedidos-container">Loading...</div>
<script>
async function cargarPedidos(){
    const filtro=document.getElementById('filtro').value;
    let url='/api/pedidos';
    if(filtro) url+=`?estado=${filtro}`;
    try{
        const res=await fetch(url);
        const data=await res.json();
        const cont=document.getElementById('pedidos-container');
        if(!data.pedidos||data.pedidos.length===0){
            cont.innerHTML='<div class="pedido">📭 No orders today.</div>';
            return;
        }
        cont.innerHTML=data.pedidos.map(p=>{
            let items=p.items_json||[];
            return `<div class="pedido ${p.estado}">
                <div><span class="numero">#${p.numero}</span>
                <span class="estado estado-${p.estado}">${p.estado.toUpperCase()}</span></div>
                <div>${items.map(i=>`🍽️ ${i.cantidad||1}x ${i.name} — ${(i.price||0)*(i.cantidad||1)} MAD`).join('<br>')}</div>
                <div class="total">💰 Total: ${p.total_mad} MAD</div>
                <div>📞 ${p.cliente_telefono||p.id_cliente} | 🚚 ${p.tipo_entrega||'Pickup'} | 💳 ${p.metodo_pago||'Cash'}</div>
            </div>`;
        }).join('');
    }catch(e){document.getElementById('pedidos-container').innerHTML='<div class="pedido">❌ Error</div>';}
}
cargarPedidos();
setInterval(cargarPedidos,30000);
</script>
</body>
</html>"""
        with open(dashboard_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    
    logger.info(f"✅ Multi-language system ready. Bot {VERSION} running.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
