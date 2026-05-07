#!/usr/bin/env python3
import os
import logging
import re
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from functools import lru_cache
from typing import Dict, List, Optional
import httpx

VERSION = "5.0-ROBUSTO"

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

# ========== CARITOS Y ESTADOS ==========
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
pedido_estado: Dict[str, dict] = {}

# ========== SINÓNIMOS PARA COMANDOS (ROBUSTO) ==========
SINONIMOS = {
    'recoge': ['recoge', 'recoger', 'recojo', 'local', 'tienda', 'presencial', 'recogida', 'en local'],
    'domicilio': ['domicilio', 'envío', 'entrega', 'casa', 'delivery', 'reparto', 'a domicilio', 'a casa'],
    'efectivo': ['efectivo', 'cash', 'dinero', 'pago en efectivo', 'contra reembolso', 'efectivo'],
    'transferencia': ['transferencia', 'transfer', 'bancaria', 'tarjeta', 'bizum', 'ingreso', 'transferencia bancaria']
}

def match_comando(text: str, comando: str) -> bool:
    """Verifica si el texto coincide con algún sinónimo del comando"""
    text_lower = text.lower().strip()
    for sinonimo in SINONIMOS[comando]:
        if sinonimo in text_lower:
            return True
    return False

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

# ========== NLP: DETECCIÓN DE IDIOMA ==========
class LanguageDetector:
    KEYWORDS = {
        'spanish': ['hola', 'menu', 'gracias', 'quiero', 'plato'],
        'english': ['hello', 'menu', 'thank', 'want'],
        'french': ['bonjour', 'menu', 'merci'],
        'german': ['hallo', 'menü', 'danke'],
        'turkish': ['merhaba', 'menü', 'teşekkür'],
        'darija_latin': ['salam', 'menu', 'marhba', 'bghit', 'shhal'],
        'darija_arabic': ['سلام', 'قائمة', 'مرحبا', 'بغيت', 'شحال']
    }
    
    WELCOME = {
        'spanish': '👋 ¡Hola! Bienvenido a El Reducto. Escribe *MENU* para ver nuestros platos.',
        'english': '👋 Hello! Welcome to El Reducto. Type *MENU* to see our dishes.',
        'french': '👋 Bonjour! Bienvenue à El Reducto. Tapez *MENU* pour voir nos plats.',
        'german': '👋 Hallo! Willkommen in El Reducto. Gib *MENU* ein für unsere Gerichte.',
        'turkish': '👋 Merhaba! El Reducto\'ya hoş geldiniz. Yemekler için *MENU* yazın.',
        'darija_latin': '👋 Salam! Marhba bik f El Reducto. Kteb *MENU* bach tchouf lmaakoulat.',
        'darija_arabic': '👋 سلام! مرحبا بيك ف إيل ريدوكتو. اكتب *MENU* باش تشوف الماكولات.'
    }
    
    HELP = {
        'spanish': '📋 *Comandos*\n• *MENU* - Ver carta\n• *NÚMERO* - Añadir plato\n• *CANTIDAD* - Ej: "3 coca"\n• *PEDIDO* - Ver carrito\n• *ELIMINAR X* - Quitar plato\n• *ELIMINAR TODO* - Vaciar\n• *CONFIRMAR* - Finalizar pedido',
        'darija_latin': '📋 *Awamir*\n• *MENU* - Chouf lmaakoul\n• *RAQM* - Zid flakla\n• *KAMYA* - Matalan "3 coca"\n• *TALAB* - Chouf talab\n• *ELIMINAR X* - Hedi flakla\n• *ELIMINAR TODO* - Fergi talab\n• *CONFIRMAR* - Kmml talab'
    }

    @classmethod
    def detect(cls, text: str) -> str:
        text_lower = text.lower().strip()
        if any('\u0600' <= c <= '\u06FF' for c in text):
            return 'darija_arabic'
        scores = {lang: sum(1 for k in keywords if k in text_lower) 
                  for lang, keywords in cls.KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else 'spanish'
    
    @classmethod
    def get_welcome(cls, lang: str) -> str:
        return cls.WELCOME.get(lang, cls.WELCOME['spanish'])
    
    @classmethod
    def get_help(cls, lang: str) -> str:
        return cls.HELP.get(lang, cls.HELP['spanish'])

# ========== MENÚ ==========
@lru_cache(maxsize=100)
async def get_restaurant_menu_cached(client_id: str) -> tuple:
    return await get_restaurant_menu(client_id)

async def get_restaurant_menu(client_id: str) -> tuple:
    try:
        if not supabase:
            return "❌ Error de conexión", []
        result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        if not result.data:
            return "📋 *MENÚ*\nNo hay platos disponibles.", []
        menu_lines = ["📋 *MENÚ DE EL REDUCTO*", ""]
        for i, item in enumerate(result.data, 1):
            menu_lines.append(f"{i}. 🍽️ *{item['dish_name']}* — {item['price']} MAD")
            if item.get('description'):
                menu_lines.append(f"   📝 {item['description']}")
            menu_lines.append("")
        return "\n".join(menu_lines), result.data
    except Exception as e:
        logger.error(f"Error menú: {e}")
        return "❌ Error al cargar el menú", []

# ========== FUNCIONES DEL CARRITO ==========
async def add_to_cart(user_id: str, item_index: int, cantidad: int, client_id: str, lang: str) -> str:
    try:
        _, platos = await get_restaurant_menu(client_id)
        if not platos or item_index > len(platos):
            return LanguageDetector.get_help(lang)
        selected = platos[item_index - 1]
        
        if user_id not in carts:
            carts[user_id] = []
        
        for _ in range(cantidad):
            carts[user_id].append({"name": selected["dish_name"], "price": selected["price"]})
        
        total = sum(item["price"] for item in carts[user_id])
        
        return f"✅ *{cantidad} x {selected['dish_name']}* añadido a tu pedido.\n💰 Total parcial: {total} MAD\n\nEscribe *PEDIDO* para ver tu carrito."
    except Exception as e:
        logger.error(f"Error carrito: {e}")
        return "❌ Error al añadir"

async def remove_from_cart_by_name(user_id: str, nombre_buscar: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return "🛒 No hay nada en tu carrito."
    
    nombre_buscar_lower = nombre_buscar.lower().strip()
    eliminados = []
    nuevos_items = []
    
    for item in carts[user_id]:
        if nombre_buscar_lower in item["name"].lower():
            eliminados.append(item)
        else:
            nuevos_items.append(item)
    
    if not eliminados:
        return f"❌ No encontré '{nombre_buscar}' en tu carrito."
    
    carts[user_id] = nuevos_items
    total = sum(item["price"] for item in carts[user_id])
    cantidad = len(eliminados)
    nombre = eliminados[0]["name"]
    
    return f"✅ *{cantidad} x {nombre}* eliminado del carrito.\n💰 Total actual: {total} MAD\n\nEscribe *PEDIDO* para ver tu carrito."

async def remove_from_cart_by_index(user_id: str, item_index: int, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return "🛒 No hay nada en tu carrito."
    
    if item_index < 1 or item_index > len(carts[user_id]):
        items_count = len(carts[user_id])
        return f"❌ Número inválido. El carrito tiene {items_count} platos."
    
    removed = carts[user_id].pop(item_index - 1)
    total = sum(item["price"] for item in carts[user_id])
    
    return f"✅ *{removed['name']}* eliminado del carrito.\n💰 Total actual: {total} MAD\n\nEscribe *PEDIDO* para ver tu carrito."

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts:
        carts[user_id] = []
        return "🗑️ *Carrito vaciado* completamente.\n\nEscribe *MENU* para ver los platos."
    return "🛒 Tu carrito ya está vacío."

async def get_cart(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return "🛒 *Carrito vacío*\n\nEscribe *MENU* para ver nuestros platos."
    
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
    
    return f"🛒 *TU PEDIDO*\n\n{items_text}\n\n💰 *TOTAL: {total} MAD*\n\nEscribe *CONFIRMAR* para continuar con la entrega.\n\n✍️ Para eliminar un plato: *ELIMINAR 1* (por número) o *ELIMINAR TÉ* (por nombre)"

# ========== PROCESAR COMANDO ELIMINAR ==========
async def process_remove_command(user_id: str, text: str, lang: str) -> str:
    text_lower = text.lower().strip()
    remove_words = ['eliminar', 'elimina', 'quitar', 'borrar']
    
    is_remove = any(text_lower.startswith(word) for word in remove_words)
    if not is_remove:
        return None
    
    if 'todo' in text_lower or 'carrito' in text_lower or 'todos' in text_lower:
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
    text_lower = text.lower().strip()
    match = re.match(r'(\d+)\s+(.+)', text_lower)
    if not match:
        return None, None
    
    cantidad = int(match.group(1))
    nombre_buscar = match.group(2).strip()
    
    for i, plato in enumerate(platos, 1):
        if nombre_buscar in plato['dish_name'].lower():
            return i, cantidad
    
    return None, None

# ========== FASES DE ENTREGA Y PAGO (ROBUSTO CON SINÓNIMOS) ==========

async def iniciar_entrega(user_id: str, lang: str) -> str:
    pedido_estado[user_id] = {"fase": "entrega"}
    return "🚚 *Tipo de entrega*\n\n• *1* - Recoge en local\n• *2* - Envío a domicilio\n\nEscribe el número de tu opción:"

async def procesar_entrega(user_id: str, text: str, lang: str) -> str:
    text_lower = text.lower().strip()
    
    if text_lower == '1' or text_lower == 'recoge' or text_lower == 'local':
        pedido_estado[user_id]["tipo_entrega"] = "recoge"
        pedido_estado[user_id]["fase"] = "pago"
        total = sum(item["price"] for item in carts.get(user_id, []))
        return f"✅ *Recogida en local*\n⏱️ Tiempo: 5-10 minutos\n\n💰 Total: {total} MAD\n\n💳 *Método de pago*\n\n• *1* - Efectivo\n• *2* - Transferencia\n\nEscribe el número de tu opción:"
    
    elif text_lower == '2' or text_lower == 'domicilio' or text_lower == 'envío':
        pedido_estado[user_id]["tipo_entrega"] = "domicilio"
        pedido_estado[user_id]["fase"] = "direccion"
        return "📍 *Dirección de envío*\n\nPor favor, escribe tu dirección completa:"
    
    else:
        return "❌ Opción no válida. Escribe *1* (Recoge) o *2* (Domicilio)."


async def procesar_pago(user_id: str, text: str, lang: str) -> str:
    text_lower = text.lower().strip()
    
    if text_lower == '1' or text_lower == 'efectivo' or text_lower == 'cash':
        metodo = "Efectivo"
        pedido_estado[user_id]["metodo_pago"] = "efectivo"
        total = sum(item["price"] for item in carts.get(user_id, []))
        
        # Limpiar carrito
        if user_id in carts:
            carts[user_id] = []
        
        tipo = pedido_estado[user_id].get("tipo_entrega", "recoge")
        direccion = pedido_estado[user_id].get("direccion", "").strip()
        
        # Construir mensaje sin f-strings complejos
        mensaje = f"✅ *¡PEDIDO CONFIRMADO!*\n\n"
        mensaje += f"💰 Total: {total} MAD\n"
        mensaje += f"💳 Método: {metodo}\n"
        
        if tipo == "recoge":
            mensaje += "📍 Recogida en local\n"
            mensaje += "⏱️ Tiempo estimado: 5-10 minutos\n"
        else:
            mensaje += f"📍 Dirección: {direccion}\n"
            mensaje += "⏱️ Tiempo estimado: 20-30 minutos\n"
        
        mensaje += "\n📋 Tu pedido ha sido enviado a la cocina.\n\n"
        mensaje += "¡Gracias por tu compra! 🙏\n\n"
        mensaje += "Escribe *HOLA* para un nuevo pedido."
        
        # Limpiar estado
        if user_id in pedido_estado:
            del pedido_estado[user_id]
        
        return mensaje
    
    elif text_lower == '2' or text_lower == 'transferencia' or text_lower == 'transfer':
        metodo = "Transferencia"
        pedido_estado[user_id]["metodo_pago"] = "transferencia"
        total = sum(item["price"] for item in carts.get(user_id, []))
        
        if user_id in carts:
            carts[user_id] = []
        
        tipo = pedido_estado[user_id].get("tipo_entrega", "recoge")
        
        mensaje = f"✅ *¡PEDIDO CONFIRMADO!*\n\n"
        mensaje += f"💰 Total: {total} MAD\n"
        mensaje += f"💳 Método: {metodo}\n"
        
        if tipo == "recoge":
            mensaje += "📍 Recogida en local\n"
        else:
            mensaje += "📍 Envío a domicilio\n"
        
        mensaje += "\n📋 Enviaremos los datos bancarios por separado.\n\n"
        mensaje += "¡Gracias por tu compra! 🙏"
        
        if user_id in pedido_estado:
            del pedido_estado[user_id]
        
        return mensaje
    
    else:
        return "❌ Opción no válida. Escribe *1* (Efectivo) o *2* (Transferencia)."
# ========== WHATSAPP WEBHOOK ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        logger.info("Webhook verificado")
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
                        
                        # Verificar estado del pedido
                        estado = pedido_estado.get(user_id, {})
                        fase = estado.get("fase", "inicio")
                        
                        if fase == "entrega":
                            response = await procesar_entrega(user_id, text, lang)
                            await send_message(user_id, response)
                            continue
                        elif fase == "direccion":
                            response = await procesar_direccion(user_id, text, lang)
                            await send_message(user_id, response)
                            continue
                        elif fase == "pago":
                            response = await procesar_pago(user_id, text, lang)
                            await send_message(user_id, response)
                            continue
                        
                        # Comandos de eliminación
                        remove_response = await process_remove_command(user_id, text, lang)
                        if remove_response:
                            await send_message(user_id, remove_response)
                            continue
                        
                        # Comandos principales
                        if text_lower in ['hola', 'salam', 'hello', 'bonjour', 'hallo', 'merhaba', 'سلام']:
                            if user_id in carts:
                                carts[user_id] = []
                                logger.info(f"🗑️ Carrito limpiado para {user_id}")
                            if user_id in pedido_estado:
                                del pedido_estado[user_id]
                            response = LanguageDetector.get_welcome(lang)
                        
                        elif text_lower in ['menu', 'menú']:
                            menu_text, _ = await get_restaurant_menu(client_id)
                            response = menu_text
                        
                        elif text_lower in ['pedido', 'order', 'command', 'talab', 'طلب']:
                            response = await get_cart(user_id, lang)
                        
                        elif text_lower in ['confirmar', 'confirm', 'confirmer', 't2kid', 'تأكيد']:
                            if user_id in carts and carts[user_id]:
                                response = await iniciar_entrega(user_id, lang)
                            else:
                                response = "🛒 *Carrito vacío*\n\nEscribe *MENU* para agregar platos."
                        
                        elif text_lower in ['help', 'ayuda', 'aide', 'hilfe', 'yardim', 'مساعدة']:
                            response = LanguageDetector.get_help(lang)
                        
                        elif text_lower.isdigit():
                            response = await add_to_cart(user_id, int(text_lower), 1, client_id, lang)
                        
                        else:
                            _, platos = await get_restaurant_menu(client_id)
                            item_num, cantidad = parse_quantity_command(text, platos)
                            if item_num and cantidad:
                                response = await add_to_cart(user_id, item_num, cantidad, client_id, lang)
                            else:
                                response = LanguageDetector.get_help(lang)
                        
                        await send_message(user_id, response)
                        
    except Exception as e:
        logger.error(f"Error procesando: {e}")

async def send_message(to: str, message: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp no configurado")
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
    return {"version": VERSION, "features": ["Sinonimos", "EliminarPorNombre", "EliminarPorNumero", "Cantidades", "Entrega", "Pago"]}

@app.get("/api/restaurantes")
async def get_restaurantes():
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    result = supabase.table("restaurantes").select("*").eq("is_active", True).execute()
    return {"restaurantes": result.data, "count": len(result.data)}

@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
    platos = [{"id_plato": r["id"], "nombre": r["dish_name"], "precio": r["price"]} for r in result.data]
    return {"platos": platos, "count": len(platos)}

@app.post("/api/platos")
async def create_plato(item: dict):
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    data = {"client_id": item["client_id"], "dish_name": item["nombre"], "price": item["precio"], "description": item.get("descripcion", ""), "is_available": True}
    result = supabase.table("menu_items").insert(data).execute()
    if result.data:
        return {"plato": {"id_plato": result.data[0]["id"], "nombre": result.data[0]["dish_name"], "precio": result.data[0]["price"]}}
    return {"plato": None}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return await get_platos(client_id)

@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} iniciando...")
    await load_phone_mapping()
    logger.info(f"✅ Listo. {len(phone_to_restaurant)} restaurantes mapeados")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
