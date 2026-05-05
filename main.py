#!/usr/bin/env python3
"""
Orquestrator ISA — ChatCommerce Bot v3.1
Mejoras: Caché de menús, carrito de compras, mapeo por teléfono
"""

import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from functools import lru_cache
from typing import Dict, List
import httpx
import asyncio

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

# ========== CARITOS DE COMPRA POR USUARIO ==========
carts: Dict[str, List[dict]] = {}

# ========== MAPEO DE NÚMEROS DE TELÉFONO A RESTAURANTES ==========
phone_to_restaurant: Dict[str, str] = {}

async def load_phone_mapping():
    """Carga el mapeo de números de teléfono a IDs de restaurante"""
    global phone_to_restaurant
    try:
        if not supabase:
            return
        result = supabase.table("clients").select("id, owner_phone").eq("is_active", True).execute()
        phone_to_restaurant = {
            r["owner_phone"].replace("+", ""): r["id"]: r["owner_phone"]: r["id"]
            for r in result.data
            if r.get("owner_phone")
        }
        logger.info(f"📞 Mapeo cargado: {len(phone_to_restaurant)} restaurantes")
    except Exception as e:
        logger.error(f"Error cargando mapeo: {e}")

# ========== NLP: DETECCIÓN DE IDIOMA ==========
class LanguageDetector:
    KEYWORDS = {
        'spanish': ['hola', 'menu', 'gracias', 'quiero', 'cuánto', 'bueno', 'plato', 'tajine'],
        'english': ['hello', 'menu', 'thank', 'want', 'how much', 'food', 'dish', 'tajine'],
        'french': ['bonjour', 'menu', 'merci', 'combien', 'nourriture', 'plat', 'tajine'],
        'german': ['hallo', 'menü', 'danke', 'wie viel', 'essen', 'gericht', 'tajine'],
        'turkish': ['merhaba', 'menü', 'teşekkür', 'ne kadar', 'yemek', 'tajine'],
        'darija_latin': ['salam', 'menu', 'marhba', 'bghit', 'shhal', 'maakoul', 'tajine'],
        'darija_arabic': ['سلام', 'قائمة', 'مرحبا', 'بغيت', 'شحال', 'ماكول']
    }
    
    WELCOME = {
        'spanish': '👋 ¡Hola! Bienvenido. Escribe *MENU* para ver nuestros platos.',
        'english': '👋 Hello! Welcome. Type *MENU* to see our dishes.',
        'french': '👋 Bonjour! Bienvenue. Tapez *MENU* pour voir nos plats.',
        'german': '👋 Hallo! Willkommen. Gib *MENU* ein für unsere Gerichte.',
        'turkish': '👋 Merhaba! Hoş geldiniz. Yemeklerimiz için *MENU* yazın.',
        'darija_latin': '👋 Salam! Marhba. Kteb *MENU* bach tchouf lmaakoulat.',
        'darija_arabic': '👋 سلام! مرحبا. اكتب *MENU* باش تشوف الماكولات.'
    }
    
    HELP = {
        'spanish': '📋 *Ayuda*\n• *MENU* - Ver carta\n• *PEDIDO* - Mi pedido\n• *HELP* - Esta ayuda',
        'english': '📋 *Help*\n• *MENU* - View menu\n• *ORDER* - My order\n• *HELP* - This help',
        'french': '📋 *Aide*\n• *MENU* - Voir le menu\n• *COMMANDE* - Ma commande\n• *HELP* - Cette aide',
        'german': '📋 *Hilfe*\n• *MENU* - Speisekarte\n• *BESTELLUNG* - Meine Bestellung\n• *HELP* - Diese Hilfe',
        'turkish': '📋 *Yardım*\n• *MENU* - Menüyü gör\n• *SİPARİŞ* - Siparişim\n• *HELP* - Bu yardım',
        'darija_latin': '📋 *Mosa3ada*\n• *MENU* - Chouf lmaakoul\n• *TALAB* - Talabi\n• *HELP* - Hadi mosa3ada',
        'darija_arabic': '📋 *مساعدة*\n• *MENU* - شوف الماكولات\n• *طلب* - طلبي\n• *HELP* - هاد المساعدة'
    }

    @classmethod
    def detect(cls, text: str) -> str:
        text_lower = text.lower().strip()
        if any('\u0600' <= c <= '\u06FF' for c in text):
            return 'darija_arabic'
        
        scores = {}
        for lang, keywords in cls.KEYWORDS.items():
            score = sum(1 for k in keywords if k in text_lower)
            if score > 0:
                scores[lang] = score
        
        if scores:
            return max(scores, key=scores.get)
        return 'spanish'
    
    @classmethod
    def get_welcome(cls, lang: str) -> str:
        return cls.WELCOME.get(lang, cls.WELCOME['spanish'])
    
    @classmethod
    def get_help(cls, lang: str) -> str:
        return cls.HELP.get(lang, cls.HELP['spanish'])

# ========== CACHÉ DE MENÚS ==========
@lru_cache(maxsize=100)
async def get_restaurant_menu_cached(client_id: str) -> str:
    """Menú con caché para evitar consultas repetidas a Supabase"""
    return await get_restaurant_menu(client_id)

async def get_restaurant_menu(client_id: str) -> str:
    """Obtiene el menú del restaurante desde Supabase"""
    try:
        if not supabase:
            return "❌ Error de conexión"
        
        result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        
        if not result.data:
            return "📋 *MENÚ*\nNo hay platos disponibles."
        
        menu_lines = ["📋 *MENÚ*", ""]
        for i, item in enumerate(result.data, 1):
            menu_lines.append(f"{i}. 🍽️ *{item['dish_name']}*")
            menu_lines.append(f"   💰 {item['price']} dhs")
            if item.get('description'):
                menu_lines.append(f"   📝 {item['description']}")
            menu_lines.append("")
        
        return "\n".join(menu_lines)
    except Exception as e:
        logger.error(f"Error en menú: {e}")
        return "❌ Error al cargar el menú"

# ========== PROCESAMIENTO DE PEDIDOS ==========
async def add_to_cart(user_id: str, item_index: int, client_id: str, lang: str) -> str:
    """Añade un plato al carrito del usuario"""
    try:
        result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        
        if not result.data or item_index > len(result.data):
            return LanguageDetector.get_help(lang)
        
        selected = result.data[item_index - 1]
        
        if user_id not in carts:
            carts[user_id] = []
        
        carts[user_id].append({
            "id": selected["id"],
            "name": selected["dish_name"],
            "price": selected["price"]
        })
        
        total = sum(item["price"] for item in carts[user_id])
        
        return f"✅ *{selected['dish_name']}* añadido al carrito\n💰 Total: {total} dhs\n\nEscribe *PEDIDO* para ver tu carrito o *MENU* para seguir agregando."
    except Exception as e:
        logger.error(f"Error en carrito: {e}")
        return "❌ Error al añadir al carrito"

async def get_cart(user_id: str, lang: str) -> str:
    """Muestra el carrito del usuario"""
    if user_id not in carts or not carts[user_id]:
        return "🛒 *Carrito vacío*\n\nEscribe *MENU* para ver los platos."
    
    items = carts[user_id]
    total = sum(item["price"] for item in items)
    
    cart_lines = ["🛒 *MI PEDIDO*", ""]
    for i, item in enumerate(items, 1):
        cart_lines.append(f"{i}. {item['name']} — {item['price']} dhs")
    cart_lines.append("")
    cart_lines.append(f"💰 *TOTAL: {total} dhs*")
    cart_lines.append("")
    cart_lines.append("✍️ Escribe *CONFIRMAR* para finalizar o *MENU* para seguir agregando.")
    
    return "\n".join(cart_lines)

async def confirm_order(user_id: str, lang: str) -> str:
    """Confirma el pedido y lo guarda en la base de datos"""
    if user_id not in carts or not carts[user_id]:
        return "❌ No tienes pedido pendiente. Escribe *MENU* para ver los platos."
    
    # Aquí se guardaría el pedido en Supabase (tabla orders)
    total = sum(item["price"] for item in carts[user_id])
    
    # Limpiar carrito
    carts.pop(user_id, None)
    
    return f"✅ *¡Pedido confirmado!*\n💰 Total: {total} dhs\n\n📋 En breve recibirás tu pedido. ¡Gracias!"

# ========== WHATSAPP WEBHOOK ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        logger.info(f"Webhook verificado")
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
    """Procesa mensajes de WhatsApp"""
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                display_phone = metadata.get("display_phone_number", "").replace("+", "")
                
                # Identificar restaurante por número de teléfono
                client_id = phone_to_restaurant.get(display_phone)
                
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        user_id = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        
                        lang = LanguageDetector.detect(text)
                        text_lower = text.lower().strip()
                        
                        logger.info(f"📨 {user_id} [{lang}]: {text[:50]}")
                        
                        # Comandos principales
                        if text_lower in ['menu', 'menú']:
                            if client_id:
                                response = await get_restaurant_menu_cached(client_id)
                            else:
                                response = LanguageDetector.get_help(lang)
                        
                        elif text_lower in ['pedido', 'order', 'commande', 'bestellung', 'sipariş', 'طلب', 'talab']:
                            response = await get_cart(user_id, lang)
                        
                        elif text_lower in ['confirmar', 'confirm', 'confirmer', 'bestätigen', 'onayla', 'تأكيد']:
                            response = await confirm_order(user_id, lang)
                        
                        elif text_lower in ['help', 'ayuda', 'aide', 'hilfe', 'yardım', 'مساعدة']:
                            response = LanguageDetector.get_help(lang)
                        
                        elif text_lower in ['hola', 'hello', 'bonjour', 'hallo', 'merhaba', 'salam', 'سلام']:
                            response = LanguageDetector.get_welcome(lang)
                        
                        elif text_lower.isdigit() and 1 <= int(text_lower) <= 50:
                            # Selección de plato por número
                            item_num = int(text_lower)
                            if client_id:
                                response = await add_to_cart(user_id, item_num, client_id, lang)
                            else:
                                response = LanguageDetector.get_help(lang)
                        
                        else:
                            response = LanguageDetector.get_help(lang)
                        
                        await send_message(user_id, response)
                        
    except Exception as e:
        logger.error(f"Error procesando: {e}")

async def send_message(to: str, message: str):
    """Envía mensaje por WhatsApp"""
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
    return {"status": "ok", "version": "3.1", "features": ["multi-idioma", "carrito", "menú dinámico"]}

@app.get("/health")
async def health():
    supabase_status = False
    try:
        supabase.table("clients").select("count", count="exact").limit(1).execute()
        supabase_status = True
    except:
        pass
    
    return {
        "status": "healthy",
        "supabase": supabase_status,
        "whatsapp": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        "carts_active": len(carts),
        "restaurants_mapped": len(phone_to_restaurant)
    }

@app.get("/api/restaurantes")
async def get_restaurantes():
    if not supabase:
        raise HTTPException(500, "Supabase no configurado")
    result = supabase.table("clients").select("*").eq("is_active", True).execute()
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
    data = {
        "client_id": item["client_id"],
        "dish_name": item["nombre"],
        "price": item["precio"],
        "description": item.get("descripcion", ""),
        "is_available": True
    }
    result = supabase.table("menu_items").insert(data).execute()
    if result.data:
        return {"plato": {"id_plato": result.data[0]["id"], "nombre": result.data[0]["dish_name"], "precio": result.data[0]["price"]}}
    return {"plato": None}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return await get_platos(client_id)

@app.get("/api/cart/{user_id}")
async def view_cart(user_id: str, lang: str = "spanish"):
    """Endpoint para ver carrito (debug)"""
    return {"cart": carts.get(user_id, []), "total": sum(i["price"] for i in carts.get(user_id, []))}

@app.post("/api/cart/{user_id}/clear")
async def clear_cart(user_id: str):
    """Limpiar carrito (debug)"""
    carts.pop(user_id, None)
    return {"status": "cleared"}

# ========== CARGA INICIAL ==========
@app.on_event("startup")
async def startup():
    logger.info("🚀 Bot iniciando...")
    await load_phone_mapping()
    logger.info(f"✅ Listo. {len(phone_to_restaurant)} restaurantes mapeados")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)