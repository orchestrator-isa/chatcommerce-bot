#!/usr/bin/env python3
import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from functools import lru_cache
from typing import Dict, List
import httpx

VERSION = "3.3-FINAL"

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

# ========== CARITOS E IDIOMAS POR USUARIO ==========
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}

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
        'spanish': '👋 ¡Hola! Bienvenido a El Reducto. Escribe *MENU* para ver nuestros platos.',
        'english': '👋 Hello! Welcome to El Reducto. Type *MENU* to see our dishes.',
        'french': '👋 Bonjour! Bienvenue à El Reducto. Tapez *MENU* pour voir nos plats.',
        'german': '👋 Hallo! Willkommen in El Reducto. Gib *MENU* ein für unsere Gerichte.',
        'turkish': '👋 Merhaba! El Reducto\'ya hoş geldiniz. Yemekler için *MENU* yazın.',
        'darija_latin': '👋 Salam! Marhba bik f El Reducto. Kteb *MENU* bach tchouf lmaakoulat.',
        'darija_arabic': '👋 سلام! مرحبا بيك ف إيل ريدوكتو. اكتب *MENU* باش تشوف الماكولات.'
    }
    
    HELP = {
        'spanish': '📋 *Comandos*\n• *MENU* - Ver carta\n• *NÚMERO* - Añadir plato\n• *PEDIDO* - Mi pedido\n• *CONFIRMAR* - Finalizar',
        'english': '📋 *Commands*\n• *MENU* - View menu\n• *NUMBER* - Add dish\n• *ORDER* - My order\n• *CONFIRM* - Finish',
        'french': '📋 *Commandes*\n• *MENU* - Voir le menu\n• *NUMÉRO* - Ajouter un plat\n• *COMMANDE* - Ma commande\n• *CONFIRMER* - Finaliser',
        'german': '📋 *Befehle*\n• *MENU* - Speisekarte\n• *NUMMER* - Gericht hinzufügen\n• *BESTELLUNG* - Meine Bestellung\n• *BESTÄTIGEN* - Abschließen',
        'turkish': '📋 *Komutlar*\n• *MENU* - Menüyü gör\n• *NUMARA* - Yemek ekle\n• *SİPARİŞ* - Siparişim\n• *ONAYLA* - Bitir',
        'darija_latin': '📋 *Awamir*\n• *MENU* - Chouf lmaakoul\n• *RAQM* - Zid flakla\n• *TALAB* - Talabi\n• *T2KID* - Kmmel',
        'darija_arabic': '📋 *أوامر*\n• *MENU* - شوف الماكولات\n• *رقم* - زيد فلاكلا\n• *طلب* - طلبي\n• *تأكيد* - كمل'
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

# ========== PEDIDOS MULTILINGÜE ==========
CART_ADDED = {
    'spanish': lambda name, total: f"✅ *{name}* añadido a tu pedido.\n💰 Total parcial: {total} MAD\n\nEscribe *PEDIDO* para ver tu carrito.",
    'english': lambda name, total: f"✅ *{name}* added to your order.\n💰 Subtotal: {total} MAD\n\nType *ORDER* to see your cart.",
    'french': lambda name, total: f"✅ *{name}* ajouté à votre commande.\n💰 Sous-total: {total} MAD\n\nTapez *COMMANDE* pour voir votre panier.",
    'german': lambda name, total: f"✅ *{name}* zu Ihrer Bestellung hinzugefügt.\n💰 Zwischensumme: {total} MAD\n\nTippen Sie *BESTELLUNG* um den Warenkorb zu sehen.",
    'turkish': lambda name, total: f"✅ *{name}* siparişinize eklendi.\n💰 Ara toplam: {total} MAD\n\nSiparişinizi görmek için *SİPARİŞ* yazın.",
    'darija_latin': lambda name, total: f"✅ *{name}* tzad f talab dialk.\n💰 Total: {total} MAD\n\nKteb *TALAB* bach tchouf talab kamil.",
    'darija_arabic': lambda name, total: f"✅ *{name}* تزاد ف الطلب ديالك.\n💰 المجموع: {total} درهم\n\nاكتب *طلب* باش تشوف الطلب كامل.",
}

CART_VIEW = {
    'spanish': lambda items, total: f"🛒 *TU PEDIDO*\n\n{items}\n\n💰 *TOTAL: {total} MAD*\n\nEscribe *CONFIRMAR* para finalizar.",
    'english': lambda items, total: f"🛒 *YOUR ORDER*\n\n{items}\n\n💰 *TOTAL: {total} MAD*\n\nType *CONFIRMAR* to finish.",
    'french': lambda items, total: f"🛒 *VOTRE COMMANDE*\n\n{items}\n\n💰 *TOTAL: {total} MAD*\n\nTapez *CONFIRMAR* pour finaliser.",
    'german': lambda items, total: f"🛒 *IHRE BESTELLUNG*\n\n{items}\n\n💰 *GESAMT: {total} MAD*\n\nTippen Sie *CONFIRMAR* um abzuschließen.",
    'turkish': lambda items, total: f"🛒 *SİPARİŞİNİZ*\n\n{items}\n\n💰 *TOPLAM: {total} MAD*\n\nTamamlamak için *CONFIRMAR* yazın.",
    'darija_latin': lambda items, total: f"🛒 *TALAB DIALK*\n\n{items}\n\n💰 *TOTAL: {total} MAD*\n\nKteb *CONFIRMAR* bach tkmml.",
    'darija_arabic': lambda items, total: f"🛒 *طلب ديالك*\n\n{items}\n\n💰 *المجموع: {total} درهم*\n\nاكتب *CONFIRMAR* باش تكمل.",
}

CART_EMPTY = {
    'spanish': "🛒 *Carrito vacío*\n\nEscribe *MENU* para ver nuestros platos.",
    'english': "🛒 *Empty cart*\n\nType *MENU* to see our dishes.",
    'french': "🛒 *Panier vide*\n\nTapez *MENU* pour voir nos plats.",
    'german': "🛒 *Leerer Warenkorb*\n\nTippen Sie *MENU* für unsere Gerichte.",
    'turkish': "🛒 *Boş sepet*\n\nYemeklerimiz için *MENU* yazın.",
    'darija_latin': "🛒 *Talab khawi*\n\nKteb *MENU* bach tchouf lmaakoulat.",
    'darija_arabic': "🛒 *طلب خاوي*\n\nاكتب *MENU* باش تشوف الماكولات.",
}

CONFIRM_OK = {
    'spanish': lambda total: f"✅ *¡PEDIDO CONFIRMADO!*\n💰 Total: {total} MAD\n\n📋 Tu pedido ha sido enviado a la cocina.\n⏱️ Tiempo estimado: 20-30 minutos.\n\n¡Gracias por tu compra! 🙏",
    'english': lambda total: f"✅ *ORDER CONFIRMED!*\n💰 Total: {total} MAD\n\n📋 Your order has been sent to the kitchen.\n⏱️ Estimated time: 20-30 minutes.\n\nThank you for your purchase! 🙏",
    'french': lambda total: f"✅ *COMMANDE CONFIRMÉE!*\n💰 Total: {total} MAD\n\n📋 Votre commande a été envoyée à la cuisine.\n⏱️ Temps estimé: 20-30 minutes.\n\nMerci pour votre achat! 🙏",
    'german': lambda total: f"✅ *BESTELLUNG BESTÄTIGT!*\n💰 Gesamt: {total} MAD\n\n📋 Ihre Bestellung wurde an die Küche gesendet.\n⏱️ Geschätzte Zeit: 20-30 Minuten.\n\nDanke für Ihren Einkauf! 🙏",
    'turkish': lambda total: f"✅ *SİPARİŞ ONAYLANDI!*\n💰 Toplam: {total} MAD\n\n📋 Siparişiniz mutfağa gönderildi.\n⏱️ Tahmini süre: 20-30 dakika.\n\nAlışverişiniz için teşekkürler! 🙏",
    'darija_latin': lambda total: f"✅ *TALAB MQBOUL!*\n💰 Total: {total} MAD\n\n📋 Talab dialk terseel l matbakh.\n⏱️ Wa9t mo9adar: 20-30 dqiqa.\n\nShukran bzaf! 🙏",
    'darija_arabic': lambda total: f"✅ *الطلب مقبول!*\n💰 المجموع: {total} درهم\n\n📋 طلب ديالك ترسل للمطبخ.\n⏱️ الوقت المقدر: 20-30 دقيقة.\n\nشكرا بزاف! 🙏",
}

async def add_to_cart(user_id: str, item_index: int, client_id: str, lang: str) -> str:
    try:
        _, platos = await get_restaurant_menu(client_id)
        if not platos or item_index > len(platos):
            return LanguageDetector.get_help(lang)
        selected = platos[item_index - 1]
        if user_id not in carts:
            carts[user_id] = []
        carts[user_id].append({"name": selected["dish_name"], "price": selected["price"]})
        total = sum(item["price"] for item in carts[user_id])
        template = CART_ADDED.get(lang, CART_ADDED['spanish'])
        return template(selected['dish_name'], total)
    except Exception as e:
        logger.error(f"Error carrito: {e}")
        return "❌ Error al añadir"

async def get_cart(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return CART_EMPTY.get(lang, CART_EMPTY['spanish'])
    items = carts[user_id]
    total = sum(item["price"] for item in items)
    # Construir lista de items
    item_lines = []
    count = {}
    for item in items:
        name = item["name"]
        count[name] = count.get(name, 0) + 1
    for name, qty in count.items():
        price = next(i["price"] for i in items if i["name"] == name)
        if qty > 1:
            item_lines.append(f"• {name} x{qty} — {qty * price} MAD")
        else:
            item_lines.append(f"• {name} — {price} MAD")
    items_text = "\n".join(item_lines)
    template = CART_VIEW.get(lang, CART_VIEW['spanish'])
    return template(items_text, total)

async def confirm_order(user_id: str, lang: str) -> str:
    if user_id not in carts or not carts[user_id]:
        return CART_EMPTY.get(lang, CART_EMPTY['spanish'])
    total = sum(item["price"] for item in carts[user_id])
    carts.pop(user_id, None)
    template = CONFIRM_OK.get(lang, CONFIRM_OK['spanish'])
    return template(total)

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
                        
                        # Detectar y guardar idioma para saludos
                        if text_lower in ['hola', 'hello', 'bonjour', 'hallo', 'merhaba', 'salam', 'سلام']:
                            lang = LanguageDetector.detect(text)
                            user_lang[user_id] = lang
                            # 🔧 LIMPIAR CARRITO AL SALUDAR
                            if user_id in carts:
                                del carts[user_id]
                                logger.info(f"🗑️ Carrito limpiado para {user_id} (nueva sesión)")
                            response = LanguageDetector.get_welcome(lang)
                        
                        elif text_lower in ['menu', 'menú']:
                            lang = user_lang.get(user_id, 'spanish')
                            menu_text, _ = await get_restaurant_menu(client_id)
                            response = menu_text
                        
                        elif text_lower.isdigit():
                            # Usar el idioma guardado del usuario
                            lang = user_lang.get(user_id, 'spanish')
                            item_num = int(text_lower)
                            response = await add_to_cart(user_id, item_num, client_id, lang)
                        
                        elif text_lower in ['pedido', 'order', 'commande', 'bestellung', 'sipariş', 'طلب']:
                            lang = user_lang.get(user_id, 'spanish')
                            response = await get_cart(user_id, lang)
                        
                        elif text_lower in ['confirmar', 'confirm', 'confirmer', 'bestätigen', 'onayla', 'تأكيد']:
                            lang = user_lang.get(user_id, 'spanish')
                            response = await confirm_order(user_id, lang)
                        
                        elif text_lower in ['help', 'ayuda', 'aide', 'hilfe', 'yardım', 'مساعدة']:
                            lang = user_lang.get(user_id, 'spanish')
                            response = LanguageDetector.get_help(lang)
                        
                        else:
                            lang = user_lang.get(user_id, 'spanish')
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
        response = await client.post(url, headers=headers, json=data)
        if response.status_code != 200:
            logger.error(f"Error WhatsApp: {response.text}")

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
    return {
        "status": "healthy",
        "version": VERSION,
        "supabase": supabase_status,
        "whatsapp": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        "carts_active": len(carts),
        "languages_saved": len(user_lang)
    }

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
    data = {
        "client_id": item["client_id"],
        "dish_name": item["nombre"],
        "price": item["precio"],
        "description": item.get("descripcion", ""),
        "is_available": True
    }
    result = supabase.table("menu_items").insert(data).execute()
    if result.data:
        return {"plato": {
            "id_plato": result.data[0]["id"],
            "nombre": result.data[0]["dish_name"],
            "precio": result.data[0]["price"]
        }}
    return {"plato": None}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return await get_platos(client_id)

@app.get("/api/version")
async def version():
    return {"version": VERSION, "features": ["LanguageDetector", "CartCleanup", "MultiLanguage", "SpanishCommands"]}

@app.on_event("startup")
async def startup():
    logger.info(f"🚀 Bot {VERSION} iniciando...")
    await load_phone_mapping()
    logger.info(f"✅ Listo. {len(phone_to_restaurant)} restaurantes mapeados")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
