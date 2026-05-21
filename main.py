#!/usr/bin/env python3
import os, logging, json, httpx
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse
from supabase import create_client, Client
from typing import Dict, List, Optional

VERSION = "8.9-PRODUCCIÓN"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("isa-bot")

# ========== CONFIGURACIÓN ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")

supabase: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
app = FastAPI(title="Orquestrator ISA", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ========== IDIOMAS ==========
LANG_DIR = Path("lang")
LANGUAGES: Dict[str, dict] = {}
if LANG_DIR.exists():
    for f in LANG_DIR.glob("*.json"):
        try: LANGUAGES[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except: pass
else: LANG_DIR.mkdir(exist_ok=True)

LANG_MAP = {'english':'en','spanish':'es','french':'fr','darija':'dar','arabic':'ar'}
def get_text(lang: str, key: str, **kw) -> str:
    t = LANGUAGES.get(LANG_MAP.get(lang, 'es'), LANGUAGES.get('es', {})).get(key, key)
    return t.format(**kw) if kw else t

# ========== ESTADOS ==========
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
pedido_estado: Dict[str, dict] = {}
TIEMPOS = {"normal":{"recoger":"5-10 min","domicilio":"20-30 min"}}

# ========== MENÚ & CARRITO ==========
async def get_menu(cid: str, lang: str) -> tuple:
    if not supabase: return "❌ DB offline", []
    lk = LANG_MAP.get(lang, 'es')
    res = supabase.table("menu_items").select("*").eq("client_id", cid).eq("is_available", True).execute()
    if not res.data: return "📋 *MENÚ*\nNo hay platos.", []
    lines = ["📋 *MENÚ RESTINGA*", ""]
    for i, it in enumerate(res.data, 1):
        tr = it.get("translations", {}) or {}
        name = tr.get(lk) or tr.get('es') or it.get("dish_name", "Plato")
        p = "🆓" if it.get("price",0)==0 else f"{it['price']} MAD"
        lines.append(f"{i}. *{name}* — {p}")
    txt = "\n".join(lines)
    if len(txt) > 1500:
        mid = txt[:1500].rfind("\n")
        return txt[:mid if mid>0 else 750], res.data, txt[mid if mid>0 else 750:]
    return txt, res.data, ""

async def add_to_cart(uid: str, idx: int, cid: str, lang: str) -> str:
    _, platos, _ = await get_menu(cid, lang)
    if not platos or idx < 1 or idx > len(platos): return f"❌ Número inválido. Menú tiene {len(platos)}."
    sel = platos[idx-1]
    if uid not in carts: carts[uid] = []
    carts[uid].append({"name": sel["dish_name"], "price": sel["price"]})
    total = sum(i["price"] for i in carts[uid])
    return f"✅ {sel['dish_name']} añadido.\n💰 Subtotal: {total} MAD. Escribe *pedido* o *c*."

async def get_cart(uid: str, lang: str) -> str:
    if uid not in carts or not carts[uid]: return "🛒 Carrito vacío. Escribe *m*."
    items = {}
    for i in carts[uid]:
        items[i["name"]] = items.get(i["name"], 0) + i["price"]
    lines = [f"• {k} — {v} MAD" for k, v in items.items()]
    total = sum(items.values())
    items_txt = "\n".join(lines)
    return f"🛒 *TU PEDIDO*\n{items_txt}\n💰 *TOTAL: {total} MAD*\nEscribe *c* para confirmar."

# ========== GUARDAR PEDIDO (DB REAL) ==========
async def save_order(uid: str, total: int, tipo: str, dir: str, met: str) -> dict:
    try:
        if not supabase: return {"numero": "TEMP"}
        data = {
            "customer_phone": uid,
            "items_json": [{"name": i["name"], "price": i["price"]} for i in carts.get(uid, [])],
            "total_mad": total,
            "estado": "nuevo",
            "tipo_entrega": tipo,
            "direccion": dir,
            "metodo_pago": met,
            "created_at": datetime.now().isoformat()
        }
        res = supabase.table("orders").insert(data).execute()
        num = res.data[0].get("id", "")[-6:].upper() if res.data else "???"
        return {"numero": f"ORD-{num}"}
    except Exception as e:
        logger.error(f"❌ DB Error: {e}")
        return {"numero": "FAIL"}

async def send_msg(to: str, msg: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
                json={"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":msg[:1600]}})
            return r.status_code == 200
    except: return False

# ========== FLUJO PRINCIPAL ==========
async def process_msg(body: dict):
    if body.get("object") != "whatsapp_business_account": return
    for entry in body.get("entry", []):
        for ch in entry.get("changes", []):
            val = ch.get("value", {})
            cid = "44444444-4444-4444-4444-444444444444" # Fallback client_id
            for msg in val.get("messages", []):
                if msg.get("type") != "text": continue
                uid, txt = msg.get("from"), msg["text"]["body"].strip()
                tl = txt.lower()
                lang = user_lang.get(uid, "spanish")
                fase = pedido_estado.get(uid, {}).get("fase", "inicio")

                # 1️⃣ RESET
                if tl in ['q','salir','exit','reiniciar']:
                    carts.pop(uid, None); pedido_estado.pop(uid, None)
                    pedido_estado[uid] = {"fase":"lang"}
                    await send_msg(uid, "🌍 *Idioma*\n1. 🇪🇸 2. 🇬🇧 3. 🇫🇷 4. 🇲🇦 5. 🇸🇦"); continue

                # 2️⃣ FASES
                if fase == "lang":
                    m = {'1':'spanish','2':'english','3':'french','4':'darija','5':'arabic'}
                    if txt in m:
                        user_lang[uid] = m[txt]; pedido_estado.pop(uid, None)
                        await send_msg(uid, f"👋 Hola! Escribe *m* para menú, *c* para confirmar, *q* salir.")
                    else: await send_msg(uid, "❌ 1-5"); continue
                if fase == "entrega":
                    if tl=='1': pedido_estado[uid].update({"tipo":"recoger","fase":"pago"}); await send_msg(uid, "💳 *Pago*\n1. Efectivo 2. Tarjeta 3. Transferencia")
                    elif tl=='2': pedido_estado[uid].update({"tipo":"domicilio","fase":"dir"}); await send_msg(uid, "📍 Dirección:")
                    else: await send_msg(uid, "❌ 1 o 2"); continue
                if fase == "dir":
                    pedido_estado[uid].update({"direccion":txt,"fase":"pago"}); await send_msg(uid, "💳 *Pago*\n1. Efectivo 2. Tarjeta 3. Transferencia"); continue
                if fase == "pago":
                    total = sum(i["price"] for i in carts.get(uid,[]))
                    if tl=='1': pedido_estado[uid].update({"met":"efectivo","fase":"bill"}); await send_msg(uid, "💵 ¿Billete? (Ej: 100)")
                    elif tl=='2':
                        res = await save_order(uid, total, pedido_estado[uid].get("tipo","recoger"), pedido_estado[uid].get("direccion",""), "tarjeta")
                        carts.pop(uid, None); pedido_estado.pop(uid, None)
                        await send_msg(uid, f"✅ Pedido {res['numero']} (Tarjeta).")
                    elif tl=='3':
                        res = await save_order(uid, total, "recoger", "", "transferencia")
                        carts.pop(uid, None); pedido_estado.pop(uid, None)
                        await send_msg(uid, f"✅ Pedido {res['numero']} (Transferencia pendiente).")
                    else: await send_msg(uid, "❌ 1-3"); continue
                if fase == "bill":
                    total = sum(i["price"] for i in carts.get(uid,[]))
                    res = await save_order(uid, total, pedido_estado[uid].get("tipo","recoger"), pedido_estado[uid].get("direccion",""), f"efectivo({txt})")
                    carts.pop(uid, None); pedido_estado.pop(uid, None)
                    await send_msg(uid, f"✅ Pedido {res['numero']} (Efectivo: {txt})."); continue
                if fase == "res_pax":
                    if tl.isdigit() and 1<=int(tl)<=50: pedido_estado[uid]["pax"]=int(tl); pedido_estado[uid]["fase"]="res_time"; await send_msg(uid, "📅 Día y hora (Ej: Mañana 20:00)"); continue
                if fase == "res_time":
                    pedido_estado.pop(uid, None); await send_msg(uid, f"✅ Reserva para {pedido_estado.get(uid,{}).get('pax','?')} personas registrada."); continue

                # 3️⃣ COMANDOS
                if tl in ['hola','hello','salam']:
                    carts.pop(uid, None); pedido_estado.pop(uid, None)
                    pedido_estado[uid] = {"fase":"lang"}
                    await send_msg(uid, "🌍 *Idioma*\n1. 🇪🇸 2. 🇬🇧 3. 🇫🇷 4. 🇲🇦 5. 🇸🇦"); continue
                if tl in ['m','menu']:
                    m1, _, m2 = await get_menu(cid, lang); await send_msg(uid, m1)
                    if m2: await send_msg(uid, m2); continue
                if tl in ['v','pedido']:
                    await send_msg(uid, await get_cart(uid, lang)); continue
                if tl in ['c','confirmar']:
                    if not carts.get(uid) or sum(i['price'] for i in carts[uid])==0: await send_msg(uid, "⚠️ Vacío. Escribe *m*.")
                    else: pedido_estado[uid]={"fase":"entrega"}; await send_msg(uid, "🚚 *Entrega*\n1. Recoger 2. Domicilio")
                    continue
                if tl in ['r','reservar']:
                    pedido_estado[uid]={"fase":"res_pax"}; await send_msg(uid, "👥 ¿Cuántas personas? (1-50)"); continue
                if tl.isdigit():
                    await send_msg(uid, await add_to_cart(uid, int(tl), cid, lang)); continue
                await send_msg(uid, "❓ *m* menú | *v* pedido | *c* confirmar | *r* reservar | *q* salir")

# ========== ENDPOINTS ==========
@app.get("/health")
async def health(): return {"status":"ok","version":VERSION}
@app.get("/api/whatsapp/webhook")
async def wb_get(req: Request):
    p = req.query_params
    if p.get("hub.mode")=="subscribe" and p.get("hub.verify_token")==VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge"))
    raise HTTPException(403)
@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    try: bg.add_task(process_msg, await req.json()); return {"status":"ok"}
    except: return {"status":"error"}, 500
@app.on_event("startup")
async def startup(): logger.info("🚀 Bot iniciado.")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)), reload=False)

