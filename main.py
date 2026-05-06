#!/usr/bin/env python3
import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from functools import lru_cache
from typing import Dict, List
import httpx
VERSION = "3.2-LanguageDetector"


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

# ========== CARITOS ==========
carts: Dict[str, List[dict]] = {}

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
        'spanish': ['hola', 'menu', 'gracias', 'quiero', 'cuánto', 'bueno', 'plato', 'tajine'],
        'english': ['hello', 'menu', 'thank', 'want', 'how much', 'food', 'dish', 'tajine'],
        'french': ['bonjour', 'menu', 'merci', 'combien', 'nourriture', 'plat', 'tajine'],
        'german': ['hallo', 'menü', 'danke', 'wie viel', 'essen', 'gericht', 'tajine'],
        'turkish': ['merhaba', 'menü', 'teşekkür', 'ne kadar', 'yemek', 'tajine'],
        'darija_latin': ['salam', 'menu', 'marhba', 'bghit', 'shhal', 'maakoul', 'tajine', 'labas', 'mzyan'],
        'darija_arabic': ['سلام', 'قائمة', 'مرحبا', 'بغيت', 'شحال', 'ماكول', 'تاجين', 'مزيان']
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
            for keyword in cls.KEYWORDS['darija_arabic']:
                if keyword in text:
                    return 'darija_arabic'
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
async def get_restaurant_menu_cached(client_id: str) -> str:
    return await get_restaurant_menu(client_id)

async def get_restaurant_menu(client_id: str) -> str:
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
        logger.error(f"Error menú: {e}")
        return "❌ Error al cargar el menú"

# ========== PEDIDOS ==========
async def add_to_cart(user_id: str, item_index: int, client_id: str, lang: str) -> str:
    try:
        result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        if not result.data or item_index > len(result.data):
            return LanguageDetector.get_help(lang)
        selected = result.data[item_index - 1]
        if user_id not in carts:
            carts[user_id] = []
        carts[user_id].append({"id": selected["id"], "name": selected["dish_name"], "price": selected["price"]})
        total = sum(item["price"] for item in carts[user_id])
        return f"✅ *{selected['dish_name']}* añadido\n💰 Total: {total} dhs\n\nEscribe *PEDIDO* para ver tu carrito."
    except Exception as e:
        logger.error(f"Error carrito: {e}")
        return "❌ Error al añadir"

async def get_cart(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return "🛒 *Carrito vacío*\n\nEscribe *MENU* para ver los platos."
    items = carts[user_id]
    total = sum(item["price"] for item in items)
    cart_lines = ["🛒 *MI PEDIDO*", ""]
    for i, item in enumerate(items, 1):
        cart_lines.append(f"{i}. {item['name']} — {item['price']} dhs")
    cart_lines.extend(["", f"💰 *TOTAL: {total} dhs*", "", "✍️ Escribe *CONFIRMAR* para finalizar."])
    return "\n".join(cart_lines)

async def confirm_order(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return "❌ No hay pedido pendiente."
    total = sum(item["price"] for item in carts[user_id])
    carts.pop(user_id, None)
    return f"✅ *¡Pedido confirmado!*\n💰 Total: {total} dhs\n\n📋 ¡Gracias!"

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
                client_id = phone_to_restaurant.get(display_phone)
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        user_id = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        lang = LanguageDetector.detect(text)
                        text_lower = text.lower().strip()
                        logger.info(f"📨 {user_id} [{lang}]: {text[:50]}")
                        if text_lower in ['menu', 'menú']:
                            if client_id:
                                response = await get_restaurant_menu_cached(client_id)
                            else:
                                response = LanguageDetector.get_help(lang)
                        elif text_lower in ['pedido', 'order', 'commande', 'bestellung', 'sipariş', 'طلب']:
                            response = await get_cart(user_id, lang)
                        elif text_lower in ['confirmar', 'confirm', 'confirmer', 'bestätigen', 'onayla', 'تأكيد']:
                            response = await confirm_order(user_id, lang)
                        elif text_lower in ['help', 'ayuda', 'aide', 'hilfe', 'yardım', 'مساعدة']:
                            response = LanguageDetector.get_help(lang)
                        elif text_lower in ['hola', 'hello', 'bonjour', 'hallo', 'merhaba', 'salam', 'سلام']:
                            response = LanguageDetector.get_welcome(lang)
                        elif text_lower.isdigit() and 1 <= int(text_lower) <= 50:
                            if client_id:
                                response = await add_to_cart(user_id, int(text_lower), client_id, lang)
                            else:
                                response = LanguageDetector.get_help(lang)
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
    return {"status": "ok", "version": "3.2"}

@app.get("/health")
async def health():
    supabase_status = False
    try:
        supabase.table("restaurantes").select("count", count="exact").limit(1).execute()
        supabase_status = True
    except:
        pass
    return {"status": "healthy", "supabase": supabase_status, "whatsapp": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID), "carts_active": len(carts)}

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
    logger.info("🚀 Bot iniciando...")
    await load_phone_mapping()
    logger.info(f"✅ Listo. {len(phone_to_restaurant)} restaurantes mapeados")
@app.get("/api/version")
async def version():
    return {"version": "3.2", "has_language_detector": True}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
