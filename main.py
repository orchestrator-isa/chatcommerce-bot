#!/usr/bin/env python3
"""Orquestrator ISA — ChatCommerce Bot v2.3 (FIXED)"""
import os, logging, json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client
import httpx

# ── Logging ────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("isa-bot")

# ── Variables de entorno ───────────────────
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
WEBHOOK_PREFIX = os.getenv("WEBHOOK_PREFIX", "/api/whatsapp/webhook")

# ── Supabase ───────────────────────────────
_supabase: Optional[Client] = None
def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no configurados")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[SUPABASE] Conectado")
    return _supabase

# ── Modelos con aliases bilingües ────────────────────────────────
class ClientCreate(BaseModel):
    name: Optional[str] = Field(None, alias="name")
    nombre: Optional[str] = Field(None, alias="nombre")
    owner_phone: Optional[str] = Field(None, alias="owner_phone")
    telefono: Optional[str] = Field(None, alias="telefono")
    language: str = Field(default="darija_latin")
    
    class Config:
        populate_by_name = True
    
    def to_db(self) -> dict:
        return {
            "name": self.nombre or self.name or "Sin nombre",
            "owner_phone": self.telefono or self.owner_phone or "",
            "language": self.language,
            "is_active": True,
            "trial_ends_at": (datetime.utcnow() + timedelta(days=20)).date().isoformat(),
        }

class MenuItemCreate(BaseModel):
    client_id: Optional[str] = Field(None, alias="client_id")
    menu_id: Optional[str] = Field(None, alias="menu_id")
    dish_name: Optional[str] = Field(None, alias="dish_name")
    nombre: Optional[str] = Field(None, alias="nombre")
    price: Optional[int] = Field(None, alias="price")
    precio: Optional[int] = Field(None, alias="precio")
    
    class Config:
        populate_by_name = True
    
    def to_db(self, fallback_id: str) -> dict:
        return {
            "client_id": self.menu_id or self.client_id or fallback_id,
            "dish_name": self.nombre or self.dish_name or "Sin nombre",
            "price": self.precio or self.price or 0,
            "is_available": True,
        }

# ── NLP: Darija Dual ───────────────────────────────────────
class NLP:
    DARIJA_LATIN = ["salam", "marhba", "bghit", "chno", "wakha", "kifach", "bzaf"]
    DARIJA_ARABIC = ["سلام", "مرحبا", "بغيت", "شنو", "واش", "دابا"]
    
    @classmethod
    def detect(cls, text: str) -> str:
        t = text.lower().strip()
        if any(c in text for c in ["\u0600","\u0601","\u0602","\u0603"]):
            return "darija_arabic" if any(k in text for k in cls.DARIJA_ARABIC) else "arabic"
        if any(k in t for k in cls.DARIJA_LATIN):
            return "darija_latin"
        if any(k in t for k in ["bonjour","merci","oui"]): return "french"
        if any(k in t for k in ["hola","gracias","quiero"]): return "spanish"
        if any(k in t for k in ["hello","thanks","want"]): return "english"
        return "darija_latin"
    
    @classmethod
    def reply(cls, lang: str, text: str) -> str:
        if any(k in text.lower() for k in ["salam","مرحبا","سلام","bonjour","hola","hello"]):
            return {
                "darija_arabic": "👋 *سلام! مرحبا بيك*\n🍴 كتب *menu* باش تشوف الماكول",
                "darija_latin": "👋 *Salam! Marhba bik*\n🍴 Kteb *menu* bach tchouf lmaakoul",
                "spanish": "👋 *¡Hola! Bienvenido*\n🍴 Escribe *menu* para ver los platos",
                "french": "👋 *Bonjour! Bienvenue*\n🍴 Tapez *menu* pour voir le menu",
                "english": "👋 *Hello! Welcome*\n🍴 Type *menu* to see the food",
            }.get(lang, "👋 *Salam!* Kteb *menu*")
        return "😅 Ma fhamteksh. Kteb *menu* bach tchouf lmaakoul."

# ── WhatsApp Service ─────────────────────────────────────────────────
class WA:
    BASE = f"https://graph.facebook.com/v18.0"
    @classmethod
    async def send(cls, to: str, msg: str) -> dict:
        if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
            return {"error": "Config incompleta"}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{cls.BASE}/{PHONE_NUMBER_ID}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
                json={"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":msg}}
            )
            return r.json() if r.status_code==200 else {"error":f"{r.status_code}"}

# ── Lifespan ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] Bot iniciando...")
    yield
    logger.info("[STOP] Bot detenido.")

# ── FastAPI App ─────────────────────────────────────────────────────
app = FastAPI(title="Orquestrator ISA", version="2.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root(): return {"status":"ok","service":"Orquestrator ISA","version":"2.3.0"}

@app.get("/health")
async def health():
    s = {"status":"healthy","supabase":False}
    try:
        get_supabase().table("clients").select("count",count="exact").limit(1).execute()
        s["supabase"] = True
    except: pass
    return s

@app.get(WEBHOOK_PREFIX)
async def wb_get(req: Request):
    p = req.query_params
    if p.get("hub.mode")=="subscribe" and p.get("hub.verify_token")==VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403,"Verification failed")

@app.post(WEBHOOK_PREFIX)
async def wb_post(req: Request, bg: BackgroundTasks):
    try: body = await req.json()
    except: return JSONResponse({"status":"error"},400)
    bg.add_task(process_wa, body)
    return JSONResponse({"status":"ok"},200)

async def process_wa(body: dict):
    try:
        if body.get("object")!="whatsapp_business_account": return
        for e in body.get("entry",[]):
            for ch in e.get("changes",[]):
                for msg in ch.get("value",{}).get("messages",[]):
                    if msg.get("type")=="text":
                        frm = msg.get("from")
                        txt = msg.get("text",{}).get("body","")
                        lang = NLP.detect(txt)
                        await WA.send(frm, NLP.reply(lang, txt))
    except Exception as ex:
        logger.error(f"[WA] Error: {ex}", exc_info=True)

# ── API Endpoints (NIVEL 0 DE INDENTACIÓN) ────────

@app.get("/api/clients")
async def list_clients():
    try:
        sb = get_supabase()
        res = sb.table("clients").select("*").eq("is_active",True).execute()
        return {"clients":res.data or [], "count":len(res.data or [])}
    except Exception as e:
        logger.error(f"[API] list_clients: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

@app.post("/api/clients")
async def create_client(c: ClientCreate):
    try:
        logger.info(f"[API] create_client: {c.dict()}")
        sb = get_supabase()
        res = sb.table("clients").insert(c.to_db()).execute()
        logger.info(f"[API] created: {res.data[0].get('id') if res.data else 'N/A'}")
        return {"client": res.data[0] if res.data else None}
    except Exception as e:
        logger.error(f"[API] create_client error: {e}", exc_info=True)
        raise HTTPException(500, detail=f"{str(e)} - Usa name/nombre y owner_phone/telefono")

@app.get("/api/menu/{cid}")
async def get_menu(cid: str):
    try:
        sb = get_supabase()
        res = sb.table("menu_items").select("*").eq("client_id",cid).eq("is_available",True).execute()
        return {"items":res.data or [], "count":len(res.data or [])}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/menu")
async def create_item(i: MenuItemCreate):
    try:
        sb = get_supabase()
        cid = i.menu_id or i.client_id
        if not cid:
            cr = sb.table("clients").select("id").eq("is_active",True).limit(1).execute()
            if cr.data:  # ✅ FIX: verificar que hay datos
                cid = cr.data[0]["id"]  # ✅ FIX: asignar fuera del if
        if not cid:
            raise HTTPException(400, detail="No client_id found")
        res = sb.table("menu_items").insert(i.to_db(cid)).execute()
        return {"item": res.data[0] if res.data else None}
    except Exception as e:
        logger.error(f"[API] create_item: {e}", exc_info=True)
        raise HTTPException(500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT","8000")), reload=False)
