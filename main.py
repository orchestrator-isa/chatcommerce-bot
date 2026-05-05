#!/usr/bin/env python3
import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("isa-bot")

# Configuración
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ========== NLP: DETECCIÓN DE IDIOMA ==========
class LanguageDetector:
    # Palabras clave por idioma
    KEYWORDS = {
        'spanish': ['hola', 'menu', 'gracias', 'quiero', 'cuánto', 'bueno', 'restaurante', 'comida', 'plato', 'tajine'],
        'english': ['hello', 'menu', 'thank', 'want', 'how much', 'restaurant', 'food', 'dish', 'tajine'],
        'french': ['bonjour', 'menu', 'merci', 'combien', 'restaurant', 'nourriture', 'plat', 'tajine'],
        'german': ['hallo', 'menü', 'danke', 'wie viel', 'restaurant', 'essen', 'gericht', 'tajine'],
        'turkish': ['merhaba', 'menü', 'teşekkür', 'ne kadar', 'restoran', 'yemek', 'tajine'],
        'darija_latin': ['salam', 'menu', 'marhba', 'bghit', 'shhal', 'restaurant', 'maakoul', 'tajine'],
        'darija_arabic': ['سلام', 'قائمة', 'مرحبا', 'بغيت', 'شحال', 'مطعم', 'ماكول']
    }
    
    # Respuestas de bienvenida por idioma
    WELCOME_RESPONSES = {
        'spanish': '👋 ¡Hola! Bienvenido al restaurante. Escribe *MENU* para ver nuestros platos.',
        'english': '👋 Hello! Welcome to the restaurant. Type *MENU* to see our dishes.',
        'french': '👋 Bonjour! Bienvenue au restaurant. Tapez *MENU* pour voir nos plats.',
        'german': '👋 Hallo! Willkommen im Restaurant. Gib *MENU* ein, um unsere Gerichte zu sehen.',
        'turkish': '👋 Merhaba! Restorana hoş geldiniz. Yemeklerimizi görmek için *MENU* yazın.',
        'darija_latin': '👋 Salam! Marhba bik f lmat3am. Kteb *MENU* bach tchouf lmaakoulat.',
        'darija_arabic': '👋 سلام! مرحبا بيك فالمطعم. اكتب *MENU* باش تشوف الماكولات.'
    }
    
    # Respuestas de ayuda por idioma
    HELP_RESPONSES = {
        'spanish': '📋 *Ayuda*\n• Escribe *MENU* para ver el menú\n• Escribe *HOLA* para saludo\n• Escribe tu pedido directamente',
        'english': '📋 *Help*\n• Type *MENU* to see the menu\n• Type *HELLO* for greeting\n• Type your order directly',
        'french': '📋 *Aide*\n• Tapez *MENU* pour voir le menu\n• Tapez *BONJOUR* pour saluer\n• Tapez votre commande directement',
        'german': '📋 *Hilfe*\n• Gib *MENU* ein für die Speisekarte\n• Gib *HALLO* für eine Begrüßung\n• Gib deine Bestellung direkt ein',
        'turkish': '📋 *Yardım*\n• Menüyü görmek için *MENU* yazın\n• Selamlaşmak için *MERHABA* yazın\n• Siparişinizi doğrudan yazın',
        'darija_latin': '📋 *Mosa3ada*\n• Kteb *MENU* bach tchouf lmaakoulat\n• Kteb *SALAM* bach tsleem\n• Kteb talabek directement',
        'darija_arabic': '📋 *مساعدة*\n• اكتب *MENU* باش تشوف الماكولات\n• اكتب *سلام* باش تسليم\n• اكتب طلبك مباشرة'
    }

    @classmethod
    def detect(cls, text: str) -> str:
        """Detecta el idioma del mensaje"""
        text_lower = text.lower().strip()
        
        # Detectar árabe primero
        if any('\u0600' <= c <= '\u06FF' for c in text):
            return 'darija_arabic'
        
        # Detectar por palabras clave
        scores = {}
        for lang, keywords in cls.KEYWORDS.items():
            score = sum(1 for k in keywords if k in text_lower)
            if score > 0:
                scores[lang] = score
        
        if scores:
            return max(scores, key=scores.get)
        
        # Default a español
        return 'spanish'
    
    @classmethod
    def get_welcome(cls, lang: str) -> str:
        return cls.WELCOME_RESPONSES.get(lang, cls.WELCOME_RESPONSES['spanish'])
    
    @classmethod
    def get_help(cls, lang: str) -> str:
        return cls.HELP_RESPONSES.get(lang, cls.HELP_RESPONSES['spanish'])

# ========== WHATSAPP WEBHOOK ==========
@app.get("/api/whatsapp/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info(f"Webhook verificado: {challenge}")
        return Response(content=challenge, media_type="text/plain")
    
    logger.warning(f"Verificación fallida: mode={mode}, token={token}")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/api/whatsapp/webhook")
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        logger.info(f"Mensaje recibido: {body}")
        background_tasks.add_task(process_message, body)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": "error"}

async def get_restaurant_menu(client_id: str) -> str:
    """Obtiene el menú del restaurante desde Supabase"""
    try:
        if not supabase:
            return "❌ Error de conexión con la base de datos"
        
        result = supabase.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        
        if not result.data:
            return "📋 *Menú*\nNo hay platos disponibles en este momento."
        
        menu_lines = ["📋 *MENÚ*", ""]
        for item in result.data:
            menu_lines.append(f"🍽️ *{item['dish_name']}*")
            menu_lines.append(f"   💰 {item['price']} dhs")
            if item.get('description'):
                menu_lines.append(f"   📝 {item['description']}")
            menu_lines.append("")
        
        return "\n".join(menu_lines)
    except Exception as e:
        logger.error(f"Error obteniendo menú: {e}")
        return "❌ Error al cargar el menú. Intenta más tarde."

async def process_message(body: dict):
    """Procesa mensajes de WhatsApp con detección de idioma"""
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Obtener el número de teléfono del negocio (para identificar el restaurante)
                metadata = value.get("metadata", {})
                display_phone = metadata.get("display_phone_number", "")
                
                # Buscar el restaurante por teléfono (si implementas mapeo)
                client_id = None
                if supabase:
                    # Buscar cliente por teléfono
                    result = supabase.table("clients").select("id").eq("owner_phone", display_phone).execute()
                    if result.data:
                        client_id = result.data[0]["id"]
                
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        from_number = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        
                        # Detectar idioma
                        lang = LanguageDetector.detect(text)
                        logger.info(f"Mensaje de {from_number} ({lang}): {text}")
                        
                        # Procesar según el contenido
                        text_lower = text.lower().strip()
                        
                        if text_lower == 'menu' or text_lower == 'menú':
                            if client_id:
                                response = await get_restaurant_menu(client_id)
                            else:
                                # Respuesta demo si no hay restaurante identificado
                                response = LanguageDetector.get_help(lang)
                        
                        elif text_lower in ['hola', 'hello', 'bonjour', 'hallo', 'merhaba', 'salam', 'سلام']:
                            response = LanguageDetector.get_welcome(lang)
                        
                        elif text_lower in ['help', 'ayuda', 'aide', 'hilfe', 'yardım', 'مساعدة']:
                            response = LanguageDetector.get_help(lang)
                        
                        else:
                            # Respuesta por defecto
                            response = f"🤖 *Bot Inteligente*\n\nNo entendí: \"{text}\"\n\nEscribe *MENU* para ver el menú o *HELP* para ayuda."
                        
                        # Enviar respuesta
                        await send_message(from_number, response)
                        
    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}")

async def send_message(to: str, message: str):
    """Envía un mensaje por WhatsApp"""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp no configurado")
        return
    
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message[:1600]}  # Límite de WhatsApp
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=data)
            if response.status_code != 200:
                logger.error(f"Error enviando mensaje: {response.text}")
            else:
                logger.info(f"Mensaje enviado a {to}")
    except Exception as e:
        logger.error(f"Error en send_message: {e}")

# ========== API ENDPOINTS ==========
@app.get("/")
async def root():
    return {"status": "ok", "service": "ISA ChatCommerce Bot", "version": "2.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "supabase": supabase is not None}

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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
