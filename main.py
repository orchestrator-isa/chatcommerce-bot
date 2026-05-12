#!/usr/bin/env python3
import os
import logging
import re
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from supabase import create_client
from typing import Dict, List, Optional
import httpx

VERSION = "7.2-RESTINGA-COMPLETA"

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
    'spanish': 'es',
    'french': 'fr',
    'german': 'de',
    'turkish': 'tr',
    'darija_latin': 'dar',
    'darija_arabic': 'ar'
}

def get_text(lang_code: str, key: str, **kwargs) -> str:
    file_key = LANG_MAP.get(lang_code, 'es')
    texts = LANGUAGES.get(file_key, LANGUAGES.get('es', {}))
    template = texts.get(key, LANGUAGES['es'].get(key, key))
    return template.format(**kwargs) if kwargs else template

# ========== ESTADOS GLOBALES ==========
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
user_idioma_manual: Dict[str, bool] = {}
pedido_estado: Dict[str, dict] = {}
restaurant_status = "normal"  # normal, moderado, lleno
clientes_validados: set = set()  # Teléfonos que pueden pagar por transferencia
session_activa: Dict[str, str] = {}  # user_id -> session_id

# Tiempos estimados según estado y tipo
TIEMPOS = {
    "normal": {"recoger": "5-10 minutos", "domicilio": "20-30 minutos"},
    "moderado": {"recoger": "10-15 minutos", "domicilio": "25-35 minutos"},
    "lleno": {"recoger": "20-30 minutos", "domicilio": "35-45 minutos"}
}

# ========== MAPEO DE TELÉFONOS ==========
phone_to_restaurant: Dict[str, str] = {}

async def load_phone_mapping():
    global phone_to_restaurant, clientes_validados
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
        
        # Hardcode para Restinga
        phone_to_restaurant['212626282904'] = '44444444-4444-4444-4444-444444444444'
        phone_to_restaurant['212668087490'] = '44444444-4444-4444-4444-444444444444'
        
        # Cargar clientes validados
        try:
            result = supabase.table("valid_clients").select("telefono").execute()
            for r in result.data:
                clientes_validados.add(r.get("telefono", ""))
        except:
            pass
        
        logger.info(f"📞 {len(phone_to_restaurant)} restaurantes mapeados")
    except Exception as e:
        logger.error(f"Error mapeo: {e}")

async def registrar_mensaje(user_id: str, direccion: str, mensaje: str, intent: str = None):
    """Registra mensaje en la tabla messages"""
    try:
        if not supabase:
            return
        # Buscar sesión activa o crear una
        session_id = session_activa.get(user_id)
        if not session_id:
            session_id = str(uuid.uuid4())
            session_activa[user_id] = session_id
            supabase.table("sessions").insert({
                "id": session_id,
                "user_id": user_id,
                "inicio": datetime.now().isoformat(),
                "estado": "activa"
            }).execute()
        
        supabase.table("messages").insert({
            "session_id": session_id,
            "direccion": direccion,
            "message": mensaje[:500],
            "intent": intent,
            "created_at": datetime.now().isoformat()
        }).execute()
    except Exception as e:
        logger.error(f"Error registrando mensaje: {e}")

# ========== DETECTOR DE IDIOMA ==========
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
        return best if scores[best] > 0 else 'spanish'
    
    @classmethod
    def get_welcome(cls, lang: str) -> str:
        return get_text(lang, 'welcome')
    
    @classmethod
    def get_help(cls, lang: str) -> str:
        return get_text(lang, 'help')

# ========== MENÚ ==========
async def get_restaurant_menu(client_id: str, user_lang_code: str = 'spanish', waba: bool = True) -> tuple:
    try:
        if not supabase:
            return "❌ Error de conexión", []
        
        query = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True)
        
        # Para WABA, ocultar productos que no deben mostrarse (como los "secreto" gratis)
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

# ========== CARRITO ==========
async def add_to_cart(user_id: str, item_index: int, cantidad: int, client_id: str, lang: str) -> str:
    try:
        _, platos = await get_restaurant_menu(client_id, lang, waba=True)
        if not platos or item_index > len(platos):
            return get_text(lang, 'help')
        selected = platos[item_index - 1]
        
        if user_id not in carts:
            carts[user_id] = []
        
        # Verificar si es producto gratis (precio 0)
        if selected["price"] == 0:
            tiene_producto_pago = any(item["price"] > 0 for item in carts.get(user_id, []))
            if not tiene_producto_pago:
                return f"📌 *{selected['dish_name']}* es GRATIS con la compra de una bebida u otro producto.\n\nPor favor, añade una bebida al carrito primero (ej: escribe '18' para Té a la menta - 10 MAD)"
       
        for _ in range(cantidad):
            carts[user_id].append({"name": selected["dish_name"], "price": selected["price"]})
        
        total = sum(item["price"] for item in carts[user_id])
        return get_text(lang, 'added_to_cart', 
                       cantidad=cantidad, 
                       nombre=selected['dish_name'], 
                       total=total)
    except Exception as e:
        logger.error(f"Error carrito: {e}")
        return "❌ Error al añadir"

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
        return f"❌ No encontré '{nombre_buscar}' en tu carrito."
    
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
        return f"❌ Número inválido. El carrito tiene {len(carts[user_id])} platos."
    
    removed = carts[user_id].pop(item_index - 1)
    total = sum(item["price"] for item in carts[user_id])
    return get_text(lang, 'removed_item', 
                   cantidad=1, 
                   nombre=removed['name'], 
                   total=total)

async def clear_cart(user_id: str, lang: str) -> str:
    if user_id in carts:
        carts[user_id] = []
        return "🗑️ *Carrito vaciado* completamente."
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
        if data["price"] == 0:
            item_lines.append(f"• {name} — 🆓 GRATIS (con bebida)")
        elif data["cantidad"] > 1:
            item_lines.append(f"• {name} x{data['cantidad']} — {data['cantidad'] * data['price']} MAD")
        else:
            item_lines.append(f"• {name} — {data['price']} MAD")
    
    items_text = "\n".join(item_lines)
    return f"🛒 *TU PEDIDO*\n\n{items_text}\n\n💰 *TOTAL: {total} MAD*\n\nEscribe *CONFIRMAR* para finalizar."

# ========== PEDIDOS ==========
async def guardar_pedido(user_id: str, items: list, total: int, tipo_entrega: str = None, direccion: str = None, metodo_pago: str = None, billete: str = None) -> dict:
    try:
        if not supabase:
            return {"error": "Database not connected"}
        
        # Obtener client_id del usuario
        client_id = phone_to_restaurant.get(user_id, "44444444-4444-4444-4444-444444444444")
        
        items_json = []
        for item in items:
            items_json.append({
                "name": item["name"],
                "price": item["price"],
                "cantidad": item.get("cantidad", 1)
            })
        
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
            "pagado": False,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase.table("orders").insert(data).execute()
        
        if result.data:
            pedido = result.data[0]
            return {"numero": pedido.get("numero"), "id": pedido.get("id")}
        return {"error": "Failed to create order"}
    except Exception as e:
        logger.error(f"Error guardando pedido: {e}")
        return {"error": str(e)}

# ========== PDF ==========
async def enviar_menu_pdf(to: str, lang: str):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp not configured for PDF")
        return False
    
    # Mapeo de idioma a PDF (futuro)
    pdf_files = {
        'spanish': 'menu_es.pdf',
        'english': 'menu_en.pdf',
        'french': 'menu_fr.pdf',
        'darija_latin': 'menu_dar.pdf',
        'darija_arabic': 'menu_ar.pdf'
    }
    
    pdf_file = pdf_files.get(lang, 'menu_es.pdf')
    pdf_url = f"https://isa-bot-prod.onrender.com/static/{pdf_file}"
    filename = f"Menu_Restinga_{lang}.pdf"
    
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
            "filename": filename
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

# ========== FLUJO DE CONFIRMACIÓN ==========
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
        return f"✅ *Recogida en local*\n📊 Estado: {status_text}\n⏱️ Tiempo estimado: {tiempo}\n\n💰 Total: {total} MAD\n\n{get_text(lang, 'payment_method')}"
    
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
    return f"📍 Dirección guardada.\n\n💰 Total: {total} MAD\n\n{get_text(lang, 'payment_method')}"

async def procesar_pago(user_id: str, text: str, lang: str) -> str:
    if text == '1':  # Efectivo
        pedido_estado[user_id]["metodo_pago"] = "efectivo"
        pedido_estado[user_id]["fase"] = "cash_bill"
        return get_text(lang, 'cash_bill')
    
    elif text == '2':  # Transferencia
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
        # Verificar si hay cambio
        if billete in [100, 200] and restaurant_status == "lleno":
            return get_text(lang, 'cash_no_change', bill=billete)
        
        pedido_estado[user_id]["billete"] = str(billete)
        
        total = sum(item["price"] for item in carts.get(user_id, []))
        
        # Validar que total > 0
        if total <= 0:
            return "⚠️ *No se puede confirmar el pedido*\n\nEl total es 0 MAD. Por favor, añade productos con precio antes de confirmar."
        
        cambio = billete - total
        if cambio < 0:
            return f"⚠️ *El billete de {billete} MAD no es suficiente.*\nEl total es {total} MAD. Por favor, usa un billete más grande."
        
        tipo = pedido_estado[user_id].get("tipo_entrega", "recoger")
        tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])[tipo]
        
        items_dict = {}
        for item in carts.get(user_id, []):
            name = item["name"]
            if name not in items_dict:
                items_dict[name] = {"name": name, "price": item["price"], "cantidad": 0}
            items_dict[name]["cantidad"] += 1
        items_list = [{"name": v["name"], "price": v["price"], "cantidad": v["cantidad"]} for v in items_dict.values()]
        
        resultado = await guardar_pedido(
            user_id=user_id,
            items=items_list,
            total=total,
            tipo_entrega=tipo,
            direccion=pedido_estado[user_id].get("direccion"),
            metodo_pago="efectivo",
            billete=str(billete)
        )
        
        carts[user_id] = []
        
        if "error" in resultado:
            return f"❌ Error al guardar: {resultado['error']}"
        
        numero = resultado.get("numero", "???")
        
        if user_id in pedido_estado:
            del pedido_estado[user_id]
        
        metodo_texto = f"Efectivo con {billete} MAD" + (f" (cambio: {cambio} MAD)" if cambio > 0 else "")
        
        # Registrar pago
        if supabase and resultado.get("id"):
            supabase.table("payments").insert({
                "order_id": resultado.get("id"),
                "amount": total,
                "method": "efectivo",
                "status": "completado",
                "created_at": datetime.now().isoformat()
            }).execute()
        
        return get_text(lang, 'order_confirmed', 
                       numero=numero, total=total, metodo=metodo_texto, tiempo=tiempo)
        
    except ValueError:
        return "❌ Por favor, responde con el número del billete (ej: 50, 100, 200)"

async def procesar_transferencia(user_id: str, lang: str) -> str:
    global restaurant_status
    
    total = sum(item["price"] for item in carts.get(user_id, []))
    
    # Validar que total > 0
    if total <= 0:
        return "⚠️ *No se puede confirmar el pedido*\n\nEl total es 0 MAD. Por favor, añade productos con precio antes de confirmar."
    
    tipo = pedido_estado[user_id].get("tipo_entrega", "recoger")
    tiempo = TIEMPOS.get(restaurant_status, TIEMPOS["normal"])[tipo]
    
    items_dict = {}
    for item in carts.get(user_id, []):
        name = item["name"]
        if name not in items_dict:
            items_dict[name] = {"name": name, "price": item["price"], "cantidad": 0}
        items_dict[name]["cantidad"] += 1
    items_list = [{"name": v["name"], "price": v["price"], "cantidad": v["cantidad"]} for v in items_dict.values()]
    
    resultado = await guardar_pedido(
        user_id=user_id,
        items=items_list,
        total=total,
        tipo_entrega=tipo,
        direccion=pedido_estado[user_id].get("direccion"),
        metodo_pago="transferencia"
    )
    
    carts[user_id] = []
    
    if "error" in resultado:
        return f"❌ Error al guardar: {resultado['error']}"
    
    numero = resultado.get("numero", "???")
    
    # Registrar pago pendiente
    if supabase and resultado.get("id"):
        supabase.table("payments").insert({
            "order_id": resultado.get("id"),
            "amount": total,
            "method": "transferencia",
            "status": "pendiente",
            "created_at": datetime.now().isoformat()
        }).execute()
    
    # Notificar a recepción
    await send_message('212668087490', 
        f"🆕 *Nuevo pedido #{numero} pendiente de validación*\n"
        f"💰 Total: {total} MAD\n"
        f"📞 Cliente: {user_id}\n"
        f"🚚 Tipo: {tipo}\n"
        f"💳 Pago: Transferencia pendiente")
    
    if user_id in pedido_estado:
        del pedido_estado[user_id]
    
    return get_text(lang, 'order_confirmed', 
                   numero=numero, total=total, metodo="Transferencia (pendiente validación)", tiempo=tiempo)

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
                client_id = phone_to_restaurant.get(display_phone, "44444444-4444-4444-4444-444444444444")
                
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        user_id = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        text_lower = text.lower().strip()
                        
                        lang = LanguageDetector.detect(text)
                        
                        # Registrar mensaje entrante
                        await registrar_mensaje(user_id, "incoming", text)
                        
                        estado = pedido_estado.get(user_id, {})
                        fase = estado.get("fase", "inicio")
                        
                        # Manejo de fases del flujo
                        if fase == "entrega":
                            response = await procesar_entrega(user_id, text_lower, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        elif fase == "check_zona":
                            response = await procesar_zona(user_id, text, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        elif fase == "direccion":
                            response = await procesar_direccion(user_id, text, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        elif fase == "pago":
                            response = await procesar_pago(user_id, text_lower, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        elif fase == "cash_bill":
                            response = await procesar_billete(user_id, text, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        elif fase == "transfer_pending":
                            response = await procesar_transferencia(user_id, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        elif fase == "pago_recoger_forzado":
                            if text_lower in ['confirmar', 'confirm']:
                                pedido_estado[user_id]["fase"] = "pago"
                                response = get_text(lang, 'payment_method')
                            else:
                                response = "Escribe *CONFIRMAR* para continuar con recogida."
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        # Comandos principales
                        if text_lower in ['hola', 'salam', 'hello', 'hi', 'bonjour', 'hallo', 'merhaba', 'سلام']:
                            if user_id in carts:
                                carts[user_id] = []
                            if user_id in pedido_estado:
                                del pedido_estado[user_id]
                            
                            if user_id not in user_lang or not user_idioma_manual.get(user_id, False):
                                lang_options = """
🌍 *Bienvenido a Restinga Restaurant*

*Selecciona tu idioma / Choose your language:*

1. 🇪🇸 Español
2. 🇬🇧 English  
3. 🇫🇷 Français
4. 🇲🇦 Darija
5. 🇲🇦 العربية

Responde con el número de tu idioma:
"""
                                await send_message(user_id, lang_options)
                                pedido_estado[user_id] = {"fase": "seleccion_idioma"}
                                continue
                            else:
				else:
			        user_lang_code = user_lang[user_id]
			        response = f"{LanguageDetector.get_welcome(user_lang_code)}\n\n{LanguageDetector.get_help(user_lang_code)}"
                                continue
                        
                        elif fase == "seleccion_idioma":
                            idiomas = {
                                '1': 'spanish', '2': 'english', '3': 'french',
                                '4': 'darija_latin', '5': 'darija_arabic'
                            }
                            if text in idiomas:
                                user_lang[user_id] = idiomas[text]
                                user_idioma_manual[user_id] = True
                                welcome_text = LanguageDetector.get_welcome(user_lang[user_id])
                                help_text = LanguageDetector.get_help(user_lang[user_id])
                                combined = f"{welcome_text}\n\n{help_text}"
                                await send_message(user_id, combined)
                                await registrar_mensaje(user_id, "outgoing", combined)
                                if user_id in pedido_estado:
                                    del pedido_estado[user_id]
                            else:
                                await send_message(user_id, "❌ Opción no válida. Elige un número del 1 al 5.")
                            continue
                        
                        elif text_lower in ['menu', 'menú']:
                            user_lang_code = user_lang.get(user_id, 'spanish')
                            menu_text, _ = await get_restaurant_menu(client_id, user_lang_code, waba=True)
                            await send_message(user_id, menu_text)
                            await enviar_menu_pdf(user_id, user_lang_code)
                            help_text = LanguageDetector.get_help(user_lang_code)
                            await send_message(user_id, help_text)
                            continue
                        
                        elif text_lower in ['pedido', 'order', 'talab', 'طلب', 'cart']:
                            response = await get_cart(user_id, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif text_lower in ['confirmar', 'confirm', 't2kid', 'تأكيد', 'checkout']:
                            total = sum(item["price"] for item in carts.get(user_id, []))
                            if total <= 0:
                                response = "⚠️ *No se puede confirmar el pedido*\n\nTu carrito está vacío o el total es 0 MAD. Por favor, añade productos con precio antes de confirmar."
                                await send_message(user_id, response)
                                await registrar_mensaje(user_id, "outgoing", response)
                                continue
                            if user_id in carts and carts[user_id]:
                                response = await iniciar_entrega(user_id, lang)
                            else:
                                response = get_text(lang, 'cart_empty')
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif text_lower in ['help', 'ayuda', 'aide', 'hilfe', 'yardim', 'مساعدة', 'commands']:
                            response = LanguageDetector.get_help(lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        elif text_lower.isdigit():
                            response = await add_to_cart(user_id, int(text_lower), 1, client_id, lang)
                            await send_message(user_id, response)
                            await registrar_mensaje(user_id, "outgoing", response)
                            continue
                        
                        else:
                            _, platos = await get_restaurant_menu(client_id, lang, waba=True)
                            quantity_match = re.match(r'(\d+)\s+(.+)', text_lower)
                            if quantity_match:
                                cantidad = int(quantity_match.group(1))
                                nombre = quantity_match.group(2).strip()
                                for i, plato in enumerate(platos, 1):
                                    if nombre in plato['dish_name'].lower():
                                        response = await add_to_cart(user_id, i, cantidad, client_id, lang)
                                        await send_message(user_id, response)
                                        await registrar_mensaje(user_id, "outgoing", response)
                                        break
                                else:
                                    response = LanguageDetector.get_help(lang)
                                    await send_message(user_id, response)
                                    await registrar_mensaje(user_id, "outgoing", response)
                            else:
                                # Verificar si es comando de eliminar
                                remove_match = re.match(r'(eliminar|quitar|borrar|remove|delete)\s+(.+)', text_lower)
                                if remove_match:
                                    resto = remove_match.group(2).strip()
                                    if resto == 'todo' or resto == 'all':
                                        response = await clear_cart(user_id, lang)
                                    elif resto.isdigit():
                                        response = await remove_from_cart_by_index(user_id, int(resto), lang)
                                    else:
                                        response = await remove_from_cart_by_name(user_id, resto, lang)
                                    await send_message(user_id, response)
                                    await registrar_mensaje(user_id, "outgoing", response)
                                else:
                                    response = LanguageDetector.get_help(lang)
                                    await send_message(user_id, response)
                                    await registrar_mensaje(user_id, "outgoing", response)
                        
    except Exception as e:
        logger.error(f"Error procesando: {e}")
        # Registrar error
        if supabase:
            supabase.table("logs_registro").insert({
                "level": "ERROR",
                "message": str(e),
                "source": "process_message",
                "created_at": datetime.now().isoformat()
            }).execute()

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
    return {"status": "healthy", "version": VERSION, "supabase": supabase is not None, "whatsapp": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID), "carts_active": len(carts), "restaurant_status": restaurant_status}

@app.post("/api/restaurant/status")
async def set_restaurant_status(request: Request):
    global restaurant_status
    data = await request.json()
    nuevo_estado = data.get("status", "normal")
    if nuevo_estado in ["normal", "moderado", "lleno"]:
        restaurant_status = nuevo_estado
        logger.info(f"📊 Estado del restaurante cambiado a: {restaurant_status}")
        return {"status": "ok", "restaurant_status": restaurant_status}
    return {"error": "Estado inválido"}

@app.get("/api/restaurant/status")
async def get_restaurant_status():
    return {"status": restaurant_status, "tiempos": TIEMPOS.get(restaurant_status, TIEMPOS["normal"])}

@app.post("/api/pedido/autorizar")
async def autorizar_pedido(request: Request):
    data = await request.json()
    order_id = data.get("order_id")
    user_id = data.get("user_id")
    
    # Actualizar estado del pedido en orders
    supabase.table("orders").update({"estado": "confirmado", "pagado": True}).eq("id", order_id).execute()
    
    # Actualizar pago
    supabase.table("payments").update({"status": "completado", "verified_by": "dashboard"}).eq("order_id", order_id).execute()
    
    # Notificar al cliente
    await send_message(user_id, "✅ *Pago verificado!* Tu pedido ha sido confirmado y enviado a cocina.")
    
    return {"status": "ok"}

@app.post("/api/pedido/estado")
async def cambiar_estado_pedido(request: Request):
    data = await request.json()
    order_id = data.get("order_id")
    nuevo_estado = data.get("estado")
    
    update_data = {"estado": nuevo_estado}
    if nuevo_estado == "entregado":
        update_data["delivered_at"] = datetime.now().isoformat()
    
    supabase.table("orders").update(update_data).eq("id", order_id).execute()
    
    return {"status": "ok"}

@app.post("/api/cliente/validar")
async def validar_cliente(request: Request):
    data = await request.json()
    telefono = data.get("telefono")
    nombre = data.get("nombre", "")
    email = data.get("email", "")
    
    clientes_validados.add(telefono)
    
    supabase.table("valid_clients").insert({
        "telefono": telefono,
        "nombre": nombre,
        "email": email,
        "verified_at": datetime.now().isoformat()
    }).execute()
    
    return {"status": "ok"}

@app.get("/api/orders")
async def get_orders(estado: str = None, fecha: str = None):
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    try:
        query = supabase.table("orders").select("*")
        if estado:
            query = query.eq("estado", estado)
        if fecha:
            query = query.gte("created_at", f"{fecha}T00:00:00").lt("created_at", f"{fecha}T23:59:59")
        else:
            hoy = datetime.now().date().isoformat()
            query = query.gte("created_at", f"{hoy}T00:00:00")
        
        result = query.order("numero", desc=False).execute()
        return {"orders": result.data, "count": len(result.data)}
    except Exception as e:
        logger.error(f"Error listing orders: {e}")
        return {"orders": [], "count": 0}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str, waba: bool = True):
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    
    query = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True)
    if waba:
        query = query.eq("show_on_waba", True)
    
    result = query.execute()
    return {"items": result.data, "count": len(result.data)}

@app.get("/api/stats/diario")
async def stats_diario():
    if not supabase:
        raise HTTPException(500, "Supabase not configured")
    try:
        hoy = datetime.now().date().isoformat()
        result = supabase.table("orders").select("*").gte("created_at", hoy).execute()
        orders = result.data
        
        total_ventas = sum(o.get("total_mad", 0) for o in orders)
        pedidos_por_estado = {estado: len([o for o in orders if o.get("estado") == estado]) 
                              for estado in ["nuevo", "confirmado", "cocina", "listo", "entregado", "cancelado"]}
        
        return {
            "total_orders": len(orders),
            "total_sales": total_ventas,
            "orders_by_status": pedidos_por_estado,
            "recent_orders": orders[-10:]
        }
    except Exception as e:
        logger.error(f"Error stats: {e}")
        return {"error": str(e)}

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} starting...")
    await load_phone_mapping()
    logger.info(f"✅ {len(LANGUAGES)} languages loaded: {list(LANGUAGES.keys())}")
    
    # Crear dashboard completo
    dashboard_path = "static/dashboard.html"
    if not os.path.exists(dashboard_path):
        os.makedirs("static", exist_ok=True)
        html_content = '''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard Restinga Restaurant</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui;background:#0f172a;color:#e2e8f0;padding:20px}
.login{max-width:400px;margin:100px auto;background:#1e293b;padding:30px;border-radius:16px}
input{width:100%;padding:12px;margin:10px 0;border-radius:8px;border:none}
button{background:#0891b2;color:white;padding:12px;border:none;border-radius:8px;cursor:pointer}
.pedido{background:#1e293b;border-radius:12px;padding:15px;margin-bottom:15px;border-left:4px solid #22d3ee}
.pedido.cocina{border-left-color:#f97316}
.pedido.listo{border-left-color:#22c55e}
.numero{font-size:1.3rem;font-weight:bold;color:#22d3ee}
.estado{padding:4px 12px;border-radius:20px;font-size:0.8rem;margin-left:10px}
.estado-nuevo{background:#fbbf24;color:#0f172a}
.estado-confirmado{background:#0891b2}
.estado-cocina{background:#f97316}
.estado-listo{background:#22c55e}
.estado-entregado{background:#64748b}
.total{font-size:1.2rem;font-weight:bold;color:#fbbf24;margin-top:10px}
.btn{background:#0891b2;color:white;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;margin-right:10px}
.flex{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:20px}
.status-toggles{display:flex;gap:10px;margin-bottom:20px}
.status-btn{padding:8px 20px;border-radius:20px;cursor:pointer;border:none;font-weight:bold}
.status-normal{background:#22c55e;color:white}
.status-moderado{background:#f97316;color:white}
.status-lleno{background:#ef4444;color:white}
table{width:100%;border-collapse:collapse}
th,td{padding:10px;text-align:left;border-bottom:1px solid #334155}
</style>
</head>
<body>
<div id="login" class="login">
    <h2>🔐 Dashboard Restinga</h2>
    <input type="password" id="password" placeholder="Contraseña">
    <button onclick="verificar()">Acceder</button>
    <p style="color:#94a3b8; margin-top:10px">Contacta con el administrador para la contraseña</p>
</div>
<div id="dashboard" style="display:none">
    <h1>📋 Dashboard - Restinga Restaurant</h1>
    
    <div class="status-toggles">
        <button class="status-btn status-normal" onclick="cambiarStatus('normal')">🟢 NORMAL (5-10 min)</button>
        <button class="status-btn status-moderado" onclick="cambiarStatus('moderado')">🟡 MODERADO (10-15 min)</button>
        <button class="status-btn status-lleno" onclick="cambiarStatus('lleno')">🔴 LLENO (20-30 min)</button>
    </div>
    
    <div class="flex">
        <button class="btn" onclick="cargarPedidos()">🔄 Actualizar</button>
        <select id="filtro" onchange="cargarPedidos()" style="background:#1e293b;color:white;padding:8px;border-radius:8px">
            <option value="">Todos</option>
            <option value="nuevo">🆕 Nuevos</option>
            <option value="confirmado">✅ Pendiente validación</option>
            <option value="cocina">👨‍🍳 En cocina</option>
            <option value="listo">🍽️ Listo</option>
            <option value="entregado">📦 Entregados</option>
        </select>
    </div>
    
    <div id="pedidos-container">Cargando...</div>
    
    <h2 style="margin-top:30px">📊 Estadísticas del día</h2>
    <div id="stats-container">Cargando...</div>
</div>
<script>
const PASS = "restinga2026";
function verificar() {
    if(document.getElementById('password').value === PASS) {
        localStorage.setItem('auth', 'true');
        document.getElementById('login').style.display = 'none';
        document.getElementById('dashboard').style.display = 'block';
        cargarPedidos();
        cargarStats();
    } else { alert('❌ Contraseña incorrecta'); }
}
if(localStorage.getItem('auth') === 'true') {
    document.getElementById('login').style.display = 'none';
    document.getElementById('dashboard').style.display = 'block';
    cargarPedidos();
    cargarStats();
}
async function cambiarStatus(status) {
    await fetch('/api/restaurant/status', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({status: status})
    });
    alert('✅ Estado cambiado a ' + status.toUpperCase());
}
async function cargarPedidos() {
    const filtro = document.getElementById('filtro').value;
    let url = '/api/orders';
    if(filtro) url += `?estado=${filtro}`;
    try {
        const res = await fetch(url);
        const data = await res.json();
        const cont = document.getElementById('pedidos-container');
        if(!data.orders || data.orders.length === 0) {
            cont.innerHTML = '<div class="pedido">📭 No hay pedidos hoy.</div>';
            return;
        }
        cont.innerHTML = data.orders.map(p => {
            let items = p.items_json || [];
            let billeteInfo = p.billete ? ` | Billete: ${p.billete} MAD` : '';
            return `<div class="pedido ${p.estado}">
                <div><span class="numero">#${p.numero}</span>
                <span class="estado estado-${p.estado}">${p.estado.toUpperCase()}</span></div>
                <div>${items.map(i => `🍽️ ${i.cantidad||1}x ${i.name} — ${(i.price||0)*(i.cantidad||1)} MAD`).join('<br>')}</div>
                <div class="total">💰 Total: ${p.total_mad} MAD${billeteInfo}</div>
                <div>📞 ${p.cliente_telefono} | 🚚 ${p.tipo_entrega||'Recoge'} | 💳 ${p.metodo_pago||'Efectivo'}</div>
                <div style="margin-top:10px">
                    ${p.metodo_pago === 'transferencia' && (p.estado === 'nuevo' || p.estado === 'confirmado') ? 
                        `<button class="btn" onclick="autorizarPago('${p.id}', '${p.cliente_telefono}')">✅ Autorizar pago</button>` : ''}
                    ${p.estado !== 'cocina' && p.estado !== 'listo' && p.estado !== 'entregado' && p.estado !== 'nuevo' ?
                        `<button class="btn" onclick="cambiarEstado('${p.id}', 'cocina')">👨‍🍳 Enviar a cocina</button>` : ''}
                    ${p.estado === 'cocina' ?
                        `<button class="btn" onclick="cambiarEstado('${p.id}', 'listo')">🍽️ Marcar listo</button>` : ''}
                    ${p.estado === 'listo' ?
                        `<button class="btn" onclick="cambiarEstado('${p.id}', 'entregado')">📦 Entregado</button>` : ''}
                </div>
            </div>`;
        }).join('');
    } catch(e) { console.error(e); }
}
async function cargarStats() {
    try {
        const res = await fetch('/api/stats/diario');
        const data = await res.json();
        const cont = document.getElementById('stats-container');
        cont.innerHTML = `
            <table>
                <tr><th>Métrica</th><th>Valor</th></tr>
                <tr><td>📦 Total pedidos</td><td>${data.total_orders || 0}</td></tr>
                <tr><td>💰 Total ventas</td><td>${data.total_sales || 0} MAD</td></tr>
                <tr><td>🆕 Nuevos</td><td>${(data.orders_by_status || {}).nuevo || 0}</td></tr>
                <tr><td>👨‍🍳 En cocina</td><td>${(data.orders_by_status || {}).cocina || 0}</td></tr>
                <tr><td>🍽️ Listos</td><td>${(data.orders_by_status || {}).listo || 0}</td></tr>
                <tr><td>📦 Entregados</td><td>${(data.orders_by_status || {}).entregado || 0}</td></tr>
            </table>
        `;
    } catch(e) { console.error(e); }
}
async function autorizarPago(order_id, user_id) {
    await fetch('/api/pedido/autorizar', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({order_id: order_id, user_id: user_id})
    });
    cargarPedidos();
}
async function cambiarEstado(order_id, estado) {
    await fetch('/api/pedido/estado', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({order_id: order_id, estado: estado})
    });
    cargarPedidos();
}
setInterval(() => { cargarPedidos(); cargarStats(); }, 30000);
</script>
</body>
</html>'''
        with open(dashboard_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    
    logger.info(f"✅ Sistema listo. Bot {VERSION} funcionando.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
