#!/usr/bin/env python3
"""Orquestrator ISA — ChatCommerce Bot v2.7
Estructura real de Supabase:
- Tabla clients: id, name, owner_phone, is_active, trial_ends_at
- Tabla menu_items: id, client_id, dish_name, price, description, is_available
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
# RESTAURANT PHONE MAP
# ──────────────────────────────────────────────────────────────────────────────
RESTAURANT_PHONE_MAP: Dict[str, str] = {}

async def load_restaurant_phone_map():
    global RESTAURANT_PHONE_MAP
    try:
        sb = get_supabase()
        res = sb.table("clients").select("id, owner_phone").eq("is_active", True).execute()
        
        RESTAURANT_PHONE_MAP = {}
        for r in res.data:
            telefono = r.get('owner_phone', '')
            if telefono:
                clean = telefono.replace('+', '')
                RESTAURANT_PHONE_MAP[clean] = r['id']
                RESTAURANT_PHONE_MAP[telefono] = r['id']
        
        logger.info(f"[MAP] Cargados {len(RESTAURANT_PHONE_MAP)//2} restaurantes")
    except Exception as e:
        logger.error(f"[MAP] Error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ──────────────────────────────────────────────────────────────────────────────
class ClientCreate(BaseModel):
    name: Optional[str] = None
    nombre: Optional[str] = None
    owner_phone: Optional[str] = None
    telefono: Optional[str] = None
    language: str = "darija_latin"
    
    def to_db(self) -> dict:
        return {
            "name": self.nombre or self.name or "Sin nombre",
            "owner_phone": self.telefono or self.owner_phone or "",
            "language": self.language,
            "is_active": True,
            "trial_ends_at": (datetime.utcnow() + timedelta(days=20)).date().isoformat(),
        }

# Modelo con nombres en español (opcional para la API) pero mapea a inglés en BD
class PlatoCreate(BaseModel):
    client_id: str
    nombre: str  # ← se mapeará a dish_name en BD
    precio: int  # ← se mapeará a price en BD
    descripcion: Optional[str] = ""  # ← se mapeará a description en BD
    is_available: bool = True

# ──────────────────────────────────────────────────────────────────────────────
# NLP MULTI-IDIOMA (8 idiomas)
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
            'not_understood': '😅 *ما فهمتكش.* كتب *menu* باش تشوف الماكولات ولا *help* للمساعدة',
            'help': '🤖 *المساعدة:*\n• كتب *menu* باش تشوف الماكولات\n• كتب *order* باش تطلب'
        },
        'darija_latin': {
            'welcome': '👋 *Salam! Marhba bik f lmat3am*\n🍴 Kteb *menu* bach tchouf lmaakoulat',
            'menu_prompt': '📋 *Lmaakoulat:*\n{}',
            'not_understood': '😅 *Ma fhamtekch.* Kteb *menu* bach tchouf lmaakoulat awla *help*',
            'help': '🤖 *Mosa3ada:*\n• Kteb *menu* bach tchouf lmaakoulat\n• Kteb *order* bach ttlob'
        },
        'arabic': {
            'welcome': '👋 *مرحباً! أهلاً بك في المطعم*\n🍴 اكتب *menu* لمشاهدة الأطباق',
            'menu_prompt': '📋 *القائمة:*\n{}',
            'not_understood': '😅 *لم أفهم.* اكتب *menu* لمشاهدة القائمة',
            'help': '🤖 *مساعدة:*\n• اكتب *menu* للقائمة\n• اكتب *order* للطلب'
        },
        'spanish': {
            'welcome': '👋 *¡Hola! Bienvenido al restaurante*\n🍴 Escribe *menu* para ver los platos',
            'menu_prompt': '📋 *Menú:*\n{}',
            'not_understood': '😅 *No te entendí.* Escribe *menu* para ver el menú',
            'help': '🤖 *Ayuda:*\n• Escribe *menu* para ver el menú\n• Escribe *pedido* para hacer un pedido'
        },
        'french': {
            'welcome': '👋 *Bonjour! Bienvenue au restaurant*\n🍴 Tapez *menu* pour voir les plats',
            'menu_prompt': '📋 *Menu:*\n{}',
            'not_understood': '😅 *Je n\'ai pas compris.* Tapez *menu*',
            'help': '🤖 *Aide:*\n• Tapez *menu* pour voir le menu\n• Tapez *commande* pour passer commande'
        },
        'english': {
            'welcome': '👋 *Hello! Welcome to the restaurant*\n🍴 Type *menu* to see dishes',
            'menu_prompt': '📋 *Menu:*\n{}',
            'not_understood': '😅 *I didn\'t understand.* Type *menu*',
            'help': '🤖 *Help:*\n• Type *menu* to see the menu\n• Type *order* to place an order'
        },
        'german': {
            'welcome': '👋 *Hallo! Willkommen im Restaurant*\n🍴 Gib *menu* ein für die Gerichte',
            'menu_prompt': '📋 *Speisekarte:*\n{}',
            'not_understood': '😅 *Ich habe nicht verstanden.* Gib *menu* ein',
            'help': '🤖 *Hilfe:*\n• Gib *menu* ein für die Speisekarte\n• Gib *bestellung* ein für eine Bestellung'
        },
        'turkish': {
            'welcome': '👋 *Merhaba! Restorana hoş geldiniz*\n🍴 Yemekleri görmek için *menu* yazın',
            'menu_prompt': '📋 *Menü:*\n{}',
            'not_understood': '😅 *Anlamadım.* Menüyü görmek için *menu* yazın',
            'help': '🤖 *Yardım:*\n• Menüyü görmek için *menu* yazın\n• Sipariş vermek için *sipariş* yazın'
        }
    }
    
    @classmethod
    def detect(cls, text: str) -> str:
        text_lower = text.lower().strip()
        if any('\u0600' <= c <= '\u06FF' for c in text):
            if any(k in text for k in cls.KEYWORDS['darija_arabic']):
                return 'darija_arabic'
            return 'arabic'
        for lang, keywords in cls.KEYWORDS.items():
            if any(k in text_lower for k in keywords):
                return lang
        return 'darija_latin'
    
    @classmethod
    def get_response(cls, lang: str, intent: str, data: dict = None) -> str:
        responses = cls.RESPONSES.get(lang, cls.RESPONSES['darija_latin'])
        if intent == 'welcome':
            return responses['welcome']
        elif intent == 'menu':
            menu_text = data.get('menu_text', 'No hay platos') if data else 'No hay platos'
            return responses['menu_prompt'].format(menu_text)
        elif intent == 'help':
            return responses['help']
        else:
            return responses['not_understood']
    
    @classmethod
    async def get_restaurant_menu(cls, client_id: str, lang: str) -> str:
        try:
            sb = get_supabase()
            res = sb.table("menu_items")\
                .select("id, dish_name, price, description")\
                .eq("client_id", client_id)\
                .eq("is_available", True)\
                .execute()
            
            if not res.data:
                return cls.get_response(lang, 'menu', {'menu_text': 'No hay platos disponibles'})
            
            menu_lines = [f"• *{item['dish_name']}* - {item['price']} dhs" for item in res.data]
            menu_text = "\n".join(menu_lines)
            return cls.get_response(lang, 'menu', {'menu_text': menu_text})
        except Exception as e:
            logger.error(f"[MENU] Error: {e}")
            return "❌ Error al cargar el menú"
    
    @classmethod
    async def reply(cls, lang: str, text: str, client_id: str = None) -> str:
        text_lower = text.lower().strip()
        
        if any(k in text_lower for k in ['menu', 'menú', 'قائمة', 'carte', 'speisekarte', 'menü']):
            if client_id:
                return await cls.get_restaurant_menu(client_id, lang)
            return "❌ No se ha identificado el restaurante"
        
        elif any(k in text_lower for k in ['help', 'مساعدة', 'ayuda', 'aide', 'hilfe', 'yardım']):
            return cls.get_response(lang, 'help')
        
        elif any(k in text_lower for k in ['salam', 'سلام', 'hola', 'bonjour', 'hello', 'hallo', 'merhaba']):
            return cls.get_response(lang, 'welcome')
        
        return cls.get_response(lang, 'not_understood')

# ──────────────────────────────────────────────────────────────────────────────
# WHATSAPP SERVICE
# ──────────────────────────────────────────────────────────────────────────────
class WA:
    BASE = "https://graph.facebook.com/v18.0"
    
    @classmethod
    async def send(cls, to: str, msg: str) -> dict:
        if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
            return {"error": "Config incompleta"}
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(
                    f"{cls.BASE}/{PHONE_NUMBER_ID}/messages",
                    headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
                    json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": msg[:1600]}}
                )
                return response.json() if response.status_code == 200 else {"error": f"HTTP {response.status_code}"}
            except Exception as e:
                return {"error": str(e)}

# ──────────────────────────────────────────────────────────────────────────────
# PROCESS WHATSAPP MESSAGE
# ──────────────────────────────────────────────────────────────────────────────
async def process_wa_message(body: dict):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        from_number = msg.get("from")
                        text = msg.get("text", {}).get("body", "")
                        
                        detected_lang = NLP.detect(text)
                        client_id = RESTAURANT_PHONE_MAP.get(from_number.replace('+', ''))
                        
                        response = await NLP.reply(detected_lang, text, client_id)
                        await WA.send(from_number, response)
            except Exception as e:
                logger.error(f"[WA] Error: {e}")
    except Exception as e:
        logger.error(f"[WA] Error procesando: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] Bot iniciando...")
    await load_restaurant_phone_map()
    yield
    logger.info("[STOP] Bot detenido.")

app = FastAPI(title="Orquestrator ISA", version="2.7.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root():
    return {"status": "ok", "service": "Orquestrator ISA", "version": "2.7.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "supabase": True, "whatsapp": bool(WHATSAPP_TOKEN)}

@app.get(WEBHOOK_PREFIX)
async def webhook_verify(req: Request):
    params = req.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403, "Verification failed")

@app.post(WEBHOOK_PREFIX)
async def webhook_post(req: Request, bg: BackgroundTasks):
    try:
        body = await req.json()
    except:
        return JSONResponse({"status": "error"}, 400)
    bg.add_task(process_wa_message, body)
    return JSONResponse({"status": "ok"}, 200)

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - CLIENTES (tabla 'clients')
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/restaurantes")
async def list_restaurantes():
    try:
        sb = get_supabase()
        res = sb.table("clients").select("*").eq("is_active", True).execute()
        
        restaurantes = []
        for r in res.data:
            restaurantes.append({
                "id_restaurante": r.get("id"),
                "nombre": r.get("name"),
                "telefono": r.get("owner_phone"),
                "is_active": r.get("is_active")
            })
        
        return {"restaurantes": restaurantes, "count": len(restaurantes)}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/restaurantes")
async def create_restaurante(c: ClientCreate):
    try:
        sb = get_supabase()
        res = sb.table("clients").insert(c.to_db()).execute()
        return {"restaurante": res.data[0] if res.data else None}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - PLATOS (tabla 'menu_items')
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    try:
        sb = get_supabase()
        
        # Intentar diferentes formatos de UUID
        possible_ids = [client_id]
        
        # Si el UUID viene sin guiones, intentar con guiones
        if '-' not in client_id and len(client_id) == 32:
            formatted = f"{client_id[:8]}-{client_id[8:12]}-{client_id[12:16]}-{client_id[16:20]}-{client_id[20:]}"
            possible_ids.append(formatted)
        
        logger.info(f"[API] Buscando platos para: {possible_ids}")
        
        all_platos = []
        for cid in possible_ids:
            res = sb.table("menu_items").select("*").eq("client_id", cid).eq("is_available", True).execute()
            all_platos.extend(res.data)
            if res.data:
                logger.info(f"[API] Encontrados {len(res.data)} platos con {cid}")
                break
        
        platos = []
        for p in all_platos:
            platos.append({
                "id_plato": p.get("id"),
                "dish_name": p.get("dish_name"),
                "price": p.get("price"),
                "description": p.get("description", ""),
                "is_available": p.get("is_available")
            })
        
        return {"platos": platos, "count": len(platos)}
    except Exception as e:
        logger.error(f"[API] get_platos error: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

@app.get("/api/platos/{client_id}")
async def get_platos(client_id: str):
    """Obtiene todos los platos de un restaurante"""
    try:
        sb = get_supabase()
        
        # Limpiar el client_id (quitar espacios, convertir a minúsculas)
        clean_id = client_id.strip().lower()
        
        logger.info(f"[API] Buscando platos para restaurant: {clean_id}")
        
        # Buscar platos con el client_id exacto
        response = sb.table("menu_items")\
            .select("*")\
            .eq("client_id", clean_id)\
            .eq("is_available", True)\
            .execute()
        
        logger.info(f"[API] Encontrados {len(response.data)} platos")
        
        # Formatear la respuesta
        platos = []
        for item in response.data:
            platos.append({
                "id_plato": item.get("id"),
                "nombre": item.get("dish_name"),  # ← mapeo dish_name -> nombre
                "precio": item.get("price"),       # ← mapeo price -> precio
                "descripcion": item.get("description", ""),
                "is_available": item.get("is_available"),
                "is_star": item.get("is_star", False),
                "created_at": item.get("created_at")
            })
        
        return {"platos": platos, "count": len(platos)}
        
    except Exception as e:
        logger.error(f"[API] Error en get_platos: {e}", exc_info=True)
        raise HTTPException(500, detail=f"Error al obtener platos: {str(e)}")

@app.post("/api/debug/crear_plato_directo")
async def debug_crear_plato(client_id: str, dish_name: str, price: int):
    """Endpoint temporal para debug - crear plato con parámetros directos"""
    try:
        sb = get_supabase()
        
        # Intentar diferentes formatos de columna
        data = {
            "client_id": client_id,
            "dish_name": dish_name,
            "price": price,
            "description": "Debug creation",
            "is_available": True
        }
        
        logger.info(f"[DEBUG] Insertando: {data}")
        res = sb.table("menu_items").insert(data).execute()
        
        return {
            "success": True,
            "data": res.data[0] if res.data else None,
            "inserted_data": data
        }
    except Exception as e:
        logger.error(f"[DEBUG] Error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "type": type(e).__name__
        }
@app.get("/api/debug/verificar_platos/{client_id}")
async def verificar_platos(client_id: str):
    """Verifica si existen platos para un client_id específico"""
    try:
        sb = get_supabase()
        
        # Verificar si el restaurante existe
        client_check = sb.table("clients").select("id, name").eq("id", client_id).execute()
        
        # Buscar platos
        platos = sb.table("menu_items").select("*").eq("client_id", client_id).execute()
        
        return {
            "restaurante_existe": len(client_check.data) > 0,
            "datos_restaurante": client_check.data[0] if client_check.data else None,
            "total_platos_encontrados": len(platos.data),
            "platos": platos.data[:5] if platos.data else [],
            "client_id_buscado": client_id
        }
    except Exception as e:
        return {
            "error": str(e),
            "client_id_buscado": client_id
        }

# ──────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS - ESTADÍSTICAS
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    try:
        sb = get_supabase()
        restaurantes = sb.table("clients").select("count", count="exact").execute()
        platos = sb.table("menu_items").select("count", count="exact").execute()
        
        return {
            "restaurantes": restaurantes.count,
            "platos_totales": platos.count,
            "whatsapp_configured": bool(WHATSAPP_TOKEN)
        }
    except Exception as e:
        return {"error": str(e)}

# ──────────────────────────────────────────────────────────────────────────────
# ALIAS EN INGLÉS
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/clients")
async def get_clients():
    return await list_restaurantes()

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return await get_platos(client_id)

@app.post("/api/menu")
async def create_menu_item(item: dict):
    """Alias en inglés - convierte a formato español que entiende create_plato"""
    plato = PlatoCreate(
        client_id=item.get("client_id") or item.get("menu_id"),
        nombre=item.get("dish_name") or item.get("nombre"),
        precio=item.get("price") or item.get("precio"),
        descripcion=item.get("description", "")
    )
    return await create_plato(plato)
@app.get("/api/debug/platos")
async def debug_all_platos():
    """Endpoint temporal para debug - ver todos los platos"""
    try:
        sb = get_supabase()
        res = sb.table("menu_items").select("*").execute()
        
        logger.info(f"[DEBUG] Total platos en BD: {len(res.data)}")
        
        # Mostrar primeros 5 para debug
        for p in res.data[:5]:
            logger.info(f"[DEBUG] Plato: {p.get('dish_name')} - client_id: {p.get('client_id')}")
        
        return {
            "total": len(res.data),
            "platos": res.data[:20],  # primeros 20
            "todos_client_ids": list(set([p.get('client_id') for p in res.data]))
        }
    except Exception as e:
        logger.error(f"[DEBUG] Error: {e}")
        raise HTTPException(500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
