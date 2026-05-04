#!/usr/bin/env python3
"""Orquestrator ISA — ChatCommerce Bot v2.5
Multi-idioma: Darija (árabe + latin), Árabe, Español, Francés, Inglés, Alemán, Turco
Menú dinámico desde Supabase
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client
import httpx

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("isa-bot")

# ──────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT VARIABLES
# ──────────────────────────────────────────────────────────────────────────────
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
WEBHOOK_PREFIX = os.getenv("WEBHOOK_PREFIX", "/api/whatsapp/webhook")

# ──────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT
# ──────────────────────────────────────────────────────────────────────────────
_supabase: Optional[Client] = None

def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no configurados")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[SUPABASE] Conectado")
    return _supabase

# ──────────────────────────────────────────────────────────────────────────────
# RESTAURANT PHONE MAP (carga al inicio)
# ──────────────────────────────────────────────────────────────────────────────
RESTAURANT_PHONE_MAP: Dict[str, str] = {}

async def load_restaurant_phone_map():
    """Carga el mapeo teléfono -> restaurante desde Supabase"""
    global RESTAURANT_PHONE_MAP
    try:
        sb = get_supabase()
        res = sb.table("restaurants")\
            .select("id_restaurante, telefono")\
            .eq("is_active", True)\
            .execute()
        
        RESTAURANT_PHONE_MAP = {}
        for r in res.data:
            telefono = r.get('telefono', '')
            if telefono:
                # Guardar con y sin '+' para mejor matching
                clean = telefono.replace('+', '')
                RESTAURANT_PHONE_MAP[clean] = r['id_restaurante']
                RESTAURANT_PHONE_MAP[telefono] = r['id_restaurante']
        
        logger.info(f"[MAP] Cargados {len(RESTAURANT_PHONE_MAP)//2} restaurantes")
    except Exception as e:
        logger.error(f"[MAP] Error cargando mapeo: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ──────────────────────────────────────────────────────────────────────────────
class RestaurantCreate(BaseModel):
    nombre: str
    telefono: str
    direccion: Optional[str] = None
    horario: Optional[str] = None
    zone: str = "centro"
    business_type: str = "restaurant"
    
    class Config:
        populate_by_name = True

class MenuItemCreate(BaseModel):
    client_id: str
    dish_name: str
    price: int
    description: Optional[str] = ""
    category: Optional[str] = "general"

# ──────────────────────────────────────────────────────────────────────────────
# NLP MULTI-IDIOMA
# ──────────────────────────────────────────────────────────────────────────────
class NLP:
    KEYWORDS = {
        'darija_arabic': ['سلام', 'مرحبا', 'بغيت', 'شنو', 'واش', 'دابا', 'شحال', 'مزيان', 'قائمة'],
        'darija_latin': ['salam', 'marhba', 'bghit', 'chno', 'wakha', 'kifach', 'bzaf', 'shhal', 'mzyan', 'menu'],
        'arabic': ['السلام', 'مرحباً', 'أريد', 'كم', 'جيد', 'شكراً', 'قائمة'],
        'spanish': ['hola', 'gracias', 'quiero', 'cuánto', 'bueno', 'menú', 'restaurante', 'comida', 'menu'],
        'french': ['bonjour', 'merci', 'je veux', 'combien', 'menu', 'restaurant', 'nourriture'],
        'english': ['hello', 'thank', 'want', 'how much', 'menu', 'restaurant', 'food', 'good'],
        'german': ['hallo', 'danke', 'ich möchte', 'wie viel', 'menü', 'restaurant', 'essen', 'gut'],
        'turkish': ['merhaba', 'teşekkür', 'istiyorum', 'ne kadar', 'menü', 'restoran', 'yemek', 'iyi']
    }
    
    RESPONSES = {
        'darija_arabic': {
            'welcome': '👋 *سلام! مرحبا بيك فالمطعم*\n🍴 كتب *menu* باش تشوف الماكولات',
            'menu_prompt': '📋 *القائمة:*\n{}',
            'order_prompt': '🛒 *طلبك:* {}\n💰 السعر: {} درهم\n✍️ كتب *تأكيد* باش تأكد الطلب',
            'not_understood': '😅 *ما فهمتكش.* كتب *menu* باش تشوف الماكولات ولا *help* للمساعدة',
            'help': '🤖 *المساعدة:*\n• كتب *menu* باش تشوف الماكولات\n• كتب *order* باش تطلب\n• كتب *status* باش تشوف الطلب\n• كتب *help* باش تظهر هاد المساعدة'
        },
        'darija_latin': {
            'welcome': '👋 *Salam! Marhba bik f lmat3am*\n🍴 Kteb *menu* bach tchouf lmaakoulat',
            'menu_prompt': '📋 *Lmaakoulat:*\n{}',
            'order_prompt': '🛒 *Tlabek:* {}\n💰 Ssoum: {} dhs\n✍️ Kteb *t2kid* bach t2eked ltlaba',
            'not_understood': '😅 *Ma fhamtekch.* Kteb *menu* bach tchouf lmaakoulat awla *help* l mosa3ada',
            'help': '🤖 *Mosa3ada:*\n• Kteb *menu* bach tchouf lmaakoulat\n• Kteb *order* bach ttlob\n• Kteb *status* bach tchouf ltlaba\n• Kteb *help* bach tzhar had l mosa3ada'
        },
        'spanish': {
            'welcome': '👋 *¡Hola! Bienvenido al restaurante*\n🍴 Escribe *menu* para ver los platos disponibles',
            'menu_prompt': '📋 *Menú:*\n{}',
            'order_prompt': '🛒 *Tu pedido:* {}\n💰 Precio: {} dirhams\n✍️ Escribe *confirmar* para confirmar el pedido',
            'not_understood': '😅 *No te entendí.* Escribe *menu* para ver el menú o *help* para ayuda',
            'help': '🤖 *Ayuda:*\n• Escribe *menu* para ver el menú\n• Escribe *pedido* para hacer un pedido\n• Escribe *estado* para ver tu pedido\n• Escribe *help* para mostrar esta ayuda'
        },
        'french': {
            'welcome': '👋 *Bonjour! Bienvenue au restaurant*\n🍴 Tapez *menu* pour voir les plats disponibles',
            'menu_prompt': '📋 *Menu:*\n{}',
            'order_prompt': '🛒 *Votre commande:* {}\n💰 Prix: {} dirhams\n✍️ Tapez *confirmer* pour valider la commande',
            'not_understood': '😅 *Je n\'ai pas compris.* Tapez *menu* pour voir le menu ou *help* pour aide',
            'help': '🤖 *Aide:*\n• Tapez *menu* pour voir le menu\n• Tapez *commande* pour passer commande\n• Tapez *statut* pour voir votre commande\n• Tapez *help* pour afficher cette aide'
        },
        'english': {
            'welcome': '👋 *Hello! Welcome to the restaurant*\n🍴 Type *menu* to see available dishes',
            'menu_prompt': '📋 *Menu:*\n{}',
            'order_prompt': '🛒 *Your order:* {}\n💰 Price: {} dirhams\n✍️ Type *confirm* to confirm the order',
            'not_understood': '😅 *I didn\'t understand.* Type *menu* to see the menu or *help* for assistance',
            'help': '🤖 *Help:*\n• Type *menu* to see the menu\n• Type *order* to place an order\n• Type *status* to see your order\n• Type *help* to show this help'
        },
        'german': {
            'welcome': '👋 *Hallo! Willkommen im Restaurant*\n🍴 Gib *menu* ein, um die Gerichte zu sehen',
            'menu_prompt': '📋 *Speisekarte:*\n{}',
            'order_prompt': '🛒 *Ihre Bestellung:* {}\n💰 Preis: {} Dirham\n✍️ Gib *bestätigen* ein, um die Bestellung zu bestätigen',
            'not_understood': '😅 *Ich habe nicht verstanden.* Gib *menu* ein für die Speisekarte oder *help* für Hilfe',
            'help': '🤖 *Hilfe:*\n• Gib *menu* ein für die Speisekarte\n• Gib *bestellung* ein für eine Bestellung\n• Gib *status* ein für Ihren Bestellstatus\n• Gib *help* ein für diese Hilfe'
        },
        'turkish': {
            'welcome': '👋 *Merhaba! Restorana hoş geldiniz*\n🍴 Yemekleri görmek için *menu* yazın',
            'menu_prompt': '📋 *Menü:*\n{}',
            'order_prompt': '🛒 *Siparişiniz:* {}\n💰 Fiyat: {} dirhem\n✍️ Siparişi onaylamak için *onayla* yazın',
            'not_understood': '😅 *Anlamadım.* Menüyü görmek için *menu* veya yardım için *help* yazın',
            'help': '🤖 *Yardım:*\n• Menüyü görmek için *menu* yazın\n• Sipariş vermek için *sipariş* yazın\n• Sipariş durumunu görmek için *durum* yazın\n• Bu yardımı göstermek için *help* yazın'
        }
    }
    
    @classmethod
    def detect(cls, text: str) -> str:
        """Detecta el idioma del mensaje"""
        text_lower = text.lower().strip()
        
        # Detectar por escritura árabe
        if any('\u0600' <= c <= '\u06FF' for c in text):
            if any(k in text for k in cls.KEYWORDS['darija_arabic']):
                return 'darija_arabic'
            return 'arabic'
        
        # Detectar por palabras clave
        for lang, keywords in cls.KEYWORDS.items():
            if any(k in text_lower for k in keywords):
                return lang
        
        # Detectar por saludos comunes adicionales
        if any(g in text_lower for g in ['hallo', 'guten tag', 'servus']):
            return 'german'
        if any(g in text_lower for g in ['merhaba', 'selam', 'iyi']):
            return 'turkish'
        
        return 'darija_latin'
    
    @classmethod
    def get_response(cls, lang: str, intent: str, data: dict = None) -> str:
        """Obtiene respuesta en el idioma específico"""
        responses = cls.RESPONSES.get(lang, cls.RESPONSES['darija_latin'])
        
        if intent == 'welcome':
            return responses['welcome']
        elif intent == 'menu':
            menu_text = data.get('menu_text', 'No hay platos disponibles') if data else 'No hay platos disponibles'
            return responses['menu_prompt'].format(menu_text)
        elif intent == 'order':
            return responses['order_prompt'].format(
                data.get('items', 'Sin items') if data else 'Sin items',
                data.get('price', 0) if data else 0
            )
        elif intent == 'help':
            return responses['help']
        else:
            return responses['not_understood']
    
    @classmethod
    async def get_restaurant_menu(cls, client_id: str, lang: str) -> str:
        """Obtiene el menú del restaurante desde Supabase"""
        try:
            sb = get_supabase()
            res = sb.table("menu_items")\
                .select("dish_name, price, description")\
                .eq("client_id", client_id)\
                .eq("is_available", True)\
                .execute()
            
            if not res.data:
                return cls.get_response(lang, 'menu', {'menu_text': 'No hay platos disponibles temporalmente'})
            
            # Formatear menú
            menu_lines = []
            for item in res.data:
                price_dhs = item['price']
                menu_lines.append(f"• *{item['dish_name']}* - {price_dhs} dhs")
                if item.get('description'):
                    menu_lines.append(f"  _{item['description']}_")
            
            menu_text = "\n".join(menu_lines)
            return cls.get_response(lang, 'menu', {'menu_text': menu_text})
            
        except Exception as e:
            logger.error(f"[MENU] Error: {e}")
            return "❌ Error al cargar el menú. Por favor intenta más tarde."
    
    @classmethod
    async def reply(cls, lang: str, text: str, client_id: str = None) -> str:
        """Genera respuesta dinámica con menú de BD"""
        text_lower = text.lower().strip()
        
        # Detectar comandos
        menu_keywords = ['menu', 'menú', 'قائمة', 'carte', 'speisekarte', 'menü']
        if any(k in text_lower for k in menu_keywords):
            if client_id:
                return await cls.get_restaurant_menu(client_id, lang)
            else:
                return "❌ No se ha identificado el restaurante. Por favor contacta con el establecimiento."
        
        elif any(k in text_lower for k in ['help', 'مساعدة', 'ayuda', 'aide', 'hilfe', 'yardım']):
            return cls.get_response(lang, 'help')
        
        elif any(k in text_lower for k in ['order', 'pedido', 'طلب', 'commande', 'bestellung', 'sipariş']):
            return cls.get_response(lang, 'order', {'items': 'Selecciona un plato del menú primero', 'price': 0})
        
        elif any(k in text_lower for k in ['salam', 'سلام', 'hola', 'bonjour', 'hello', 'hallo', 'merhaba']):
            welcome = cls.get_response(lang, 'welcome')
            if client_id:
                return f"{welcome}\n\n📌 Escribe *menu* para ver nuestros platos"
            return welcome
        
        else:
            # Buscar si el mensaje contiene un plato del menú
            if client_id:
                try:
                    sb = get_supabase()
                    res = sb.table("menu_items")\
                        .select("dish_name, price")\
                        .eq("client_id", client_id)\
                        .ilike("dish_name", f"%{text_lower}%")\
                        .eq("is_available", True)\
                        .limit(1)\
                        .execute()
                    
                    if res.data:
                        item = res.data[0]
                        return f"🛒 *{item['dish_name']}* añadido al carrito\n💰 Precio: {item['price']} dhs\n✍️ Escribe *confirmar* para terminar el pedido"
                except Exception as e:
                    logger.error(f"[MENU] Error buscando plato: {e}")
            
            return cls.get_response(lang, 'not_understood')

# ──────────────────────────────────────────────────────────────────────────────
# WHATSAPP SERVICE
# ──────────────────────────────────────────────────────────────────────────────
class WA:
    BASE = "https://graph.facebook.com/v18.0"
    
    @classmethod
    async def send(cls, to: str, msg: str) -> dict:
        if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
            logger.error("[WA] Token o Phone Number ID no configurados")
            return {"error": "Config incompleta"}
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(
                    f"{cls.BASE}/{PHONE_NUMBER_ID}/messages",
                    headers={
                        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "text",
                        "text": {"body": msg[:1600]}  # Límite de WhatsApp
                    }
                )
                
                if response.status_code == 200:
                    logger.info(f"[WA] Mensaje enviado a {to}")
                    return response.json()
                else:
                    logger.error(f"[WA] Error {response.status_code}: {response.text}")
                    return {"error": f"HTTP {response.status_code}"}
                    
            except Exception as e:
                logger.error(f"[WA] Excepción: {e}")
                return {"error": str(e)}

# ──────────────────────────────────────────────────────────────────────────────
# PROCESS WHATSAPP MESSAGE
# ──────────────────────────────────────────────────────────────────────────────
async def process_wa_message(body: dict):
    """Procesa mensajes de WhatsApp con detección multi-idioma"""
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Obtener el número de teléfono del negocio que recibe el mensaje
                business_phone = value.get("metadata", {}).get("display_phone_number", "")
                clean_business = business_phone.replace('+', '')
                
                # Identificar restaurante por el número de teléfono
                client_id = RESTAURANT_PHONE_MAP.get(clean_business) or RESTAURANT_PHONE_MAP.get(business_phone)
                
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        from_number = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        
                        # Detectar idioma
                        detected_lang = NLP.detect(text)
                        logger.info(f"[WA] {from_number} -> {client_id} | {detected_lang}: {text[:50]}")
                        
                        # Generar respuesta
                        response = await NLP.reply(detected_lang, text, client_id)
                        
                        # Enviar respuesta
                        await WA.send(from_number, response)
                        
                        # Guardar en base de datos
                        try:
                            sb = get_supabase()
                            sb.table("messages").insert({
                                "from_number": from_number,
                                "client_id": client_id,
                                "message": text,
                                "language": detected_lang,
                                "response": response[:500],
                                "created_at": datetime.utcnow().isoformat()
                            }).execute()
                        except Exception as db_error:
                            logger.error(f"[DB] Error guardando: {db_error}")
                            
    except Exception as e:
        logger.error(f"[WA] Error procesando: {e}", exc_info=True)

# ──────────────────────────────────────────────────────────────────────────────
# LIFESPAN
# ──────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] Bot iniciando...")
    await load_restaurant_phone_map()
    logger.info("[START] Sistema listo")
    yield
    logger.info("[STOP] Bot detenido.")

# ──────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Orquestrator ISA",
    version="2.5.0",
    description="Bot WhatsApp multi-idioma para restaurantes en Tetouan",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINTS BÁSICOS
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Orquestrator ISA",
        "version": "2.5.0",
        "languages": ["darija_arabic", "darija_latin", "arabic", "spanish", "french", "english", "german", "turkish"]
    }

@app.get("/health")
async def health():
    status = {"status": "healthy", "supabase": False, "whatsapp": False}
    try:
        get_supabase().table("restaurants").select("count", count="exact").limit(1).execute()
        status["supabase"] = True
    except Exception as e:
        status["supabase_error"] = str(e)
    
    status["whatsapp"] = bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID)
    return status

# ──────────────────────────────────────────────────────────────────────────────
# WEBHOOK WHATSAPP
# ──────────────────────────────────────────────────────────────────────────────
@app.get(WEBHOOK_PREFIX)
async def webhook_verify(req: Request):
    params = req.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("[WEBHOOK] Verificado correctamente")
        return Response(content=challenge, media_type="text/plain")
    
    logger.warning(f"[WEBHOOK] Verificación fallida: mode={mode}, token={token}")
    raise HTTPException(403, "Verification failed")

@app.post(WEBHOOK_PREFIX)
async def webhook_post(req: Request, bg: BackgroundTasks):
    try:
        body = await req.json()
    except Exception as e:
        logger.error(f"[WEBHOOK] Error parsing JSON: {e}")
        return JSONResponse({"status": "error"}, 400)
    
    bg.add_task(process_wa_message, body)
    return JSONResponse({"status": "ok"}, 200)

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - RESTAURANTES
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/restaurantes")
async def list_restaurantes():
    try:
        sb = get_supabase()
        res = sb.table("restaurants").select("*").eq("is_active", True).execute()
        return {"restaurantes": res.data or [], "count": len(res.data or [])}
    except Exception as e:
        logger.error(f"[API] list_restaurantes: {e}")
        raise HTTPException(500, detail=str(e))

@app.post("/api/restaurantes")
async def create_restaurante(restaurante: RestaurantCreate):
    try:
        sb = get_supabase()
        data = restaurante.dict()
        data["is_active"] = True
        data["trial_ends_at"] = (datetime.utcnow() + timedelta(days=20)).date().isoformat()
        
        res = sb.table("restaurants").insert(data).execute()
        return {"restaurante": res.data[0] if res.data else None}
    except Exception as e:
        logger.error(f"[API] create_restaurante: {e}")
        raise HTTPException(500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - MENÚ
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    try:
        sb = get_supabase()
        res = sb.table("menu_items").select("*").eq("client_id", client_id).eq("is_available", True).execute()
        return {"items": res.data or [], "count": len(res.data or [])}
    except Exception as e:
        logger.error(f"[API] get_platos: {e}")
        raise HTTPException(500, detail=str(e))

@app.post("/api/platos")
async def create_plato(item: MenuItemCreate):
    try:
        sb = get_supabase()
        res = sb.table("menu_items").insert(item.dict()).execute()
        return {"item": res.data[0] if res.data else None}
    except Exception as e:
        logger.error(f"[API] create_plato: {e}")
        raise HTTPException(500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - ESTADÍSTICAS
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    try:
        sb = get_supabase()
        
        restaurantes = sb.table("restaurants").select("count", count="exact").execute()
        mensajes = sb.table("messages").select("count", count="exact").execute()
        pedidos = sb.table("orders").select("count", count="exact").execute() if "orders" in sb.table else {"count": 0}
        
        return {
            "restaurantes": restaurantes.count,
            "clientes": 0,
            "mensajes": mensajes.count,
            "pedidos": pedidos.count if hasattr(pedidos, 'count') else 0,
            "whatsapp_configured": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID)
        }
    except Exception as e:
        logger.error(f"[API] get_stats: {e}")
        return {
            "restaurantes": 0,
            "clientes": 0,
            "mensajes": 0,
            "pedidos": 0,
            "whatsapp_configured": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
            "error": str(e)
        }

# ──────────────────────────────────────────────────────────────────────────────
# ALIAS EN INGLÉS (Compatibilidad)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/clients")
async def get_clients():
    return await list_restaurantes()

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return await get_platos(client_id)

@app.post("/api/menu")
async def create_menu_item(item: dict):
    # Convertir formato inglés a español
    plato = MenuItemCreate(
        client_id=item.get("client_id") or item.get("menu_id"),
        dish_name=item.get("dish_name") or item.get("nombre"),
        price=item.get("price") or item.get("precio"),
        description=item.get("description", ""),
        category=item.get("category", "general")
    )
    return await create_plato(plato)

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
