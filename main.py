#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🏗️ ORQUESTRATOR ISA v8.9.1-NEON
Híbrido: Lógica simple v8.9 + Persistencia DB v5 + Panel HTML
Stack: FastAPI + Supabase Client + Neon DB
"""
import os
import logging
import json
import uuid
import httpx
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional
from decimal import Decimal

from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from supabase import create_client, Client

# ==========================================================
# 🔧 CONFIGURACIÓN & LOGGING
# ==========================================================
VERSION = "8.9.1-NEON"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("isa-bot")

# Variables de entorno (seguras con strip)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "isa_verify_2026").strip()
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "").strip()
PANEL_SECRET = os.getenv("PANEL_SESSION_SECRET", "fallback_secret_2026").strip()

# Inicializar Supabase
supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase conectado")
    except Exception as e:
        logger.error(f"❌ Error Supabase: {e}")
else:
    logger.warning("⚠️ Modo DEMO: Falta SUPABASE_URL o KEY")

app = FastAPI(title="Orquestrator ISA", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ==========================================================
# 🧠 ESTADO EN MEMORIA (Para flujo conversacional)
# ==========================================================
carts: Dict[str, List[dict]] = {}
user_lang: Dict[str, str] = {}
pedido_estado: Dict[str, dict] = {}

# Diccionario simple de idiomas
LANG_MAP = {'english':'en', 'spanish':'es', 'french':'fr', 'darija':'dar', 'arabic':'ar'}

def get_text(lang: str, key: str, **kw) -> str:
    # En producción usaría archivos JSON, aquí fallback simple
    return key

# ==========================================================
# 🛠️ HELPERS DB & WHATSAPP
# ==========================================================
async def send_msg(to: str, msg: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.info(f"[SIM-WA] {to}: {msg}")
        return True # Simula éxito en modo demo
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
            payload = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":msg[:1600]}}
            r = await c.post(f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages", json=payload, headers=headers)
            return r.status_code == 200
    except Exception as e:
        logger.error(f"❌ WA Error: {e}")
        return False

async def get_or_create_client(wa_id: str) -> Optional[str]:
    """Busca cliente en DB. Si no existe, lo crea. Retorna id_cliente (UUID)."""
    if not supabase: return "00000000-0000-0000-0000-000000000000"
    
    # 1. Buscar Restaurante (Fallback a Restinga)
    res_r = supabase.table("restaurantes").select("id_restaurante").eq("nombre", "Restinga").limit(1).execute()
    rest_id = res_r.data[0]["id_restaurante"] if res_r.data else None
    
    if not rest_id:
        logger.error("❌ No hay restaurante 'Restinga' en DB")
        return None

    # 2. Buscar Cliente
    res_c = supabase.table("clientes").select("id_cliente").eq("wa_id", wa_id).limit(1).execute()
    if res_c.data:
        return res_c.data[0]["id_cliente"]

    # 3. Crear Cliente
    new_cli = {"id_restaurante": rest_id, "wa_id": wa_id, "telefono": wa_id, "nombre": f"Cliente WA {wa_id[-4:]}"}
    res_new = supabase.table("clientes").insert(new_cli).execute()
    return res_new.data[0]["id_cliente"] if res_new.data else None

async def save_order_db(uid: str, total: float, tipo: str, direccion: str, metodo: str) -> str:
    """Guarda pedido en tabla NEON `pedidos` correctamente."""
    if not supabase: return "TEMP-ORDER"
    
    client_id = await get_or_create_client(uid)
    if not client_id: return "DB-ERROR"

    items = carts.get(uid, [])
    item_json = json.dumps([{"nombre": i["name"], "precio": i["price"]} for i in items])
    
    data = {
        "id_cliente": client_id,
        "id_restaurante": client_id.split("-")[0], # Hack para obtener rest_id si no lo guardamos, o buscarlo de nuevo
        "estado": "pendiente",
        "items": items, # JSONB nativo
        "subtotal": total,
        "total": total,
        "metodo_pago": metodo,
        "direccion_entrega": direccion if tipo == "domicilio" else "",
        "delivery_type": tipo,
        "created_at": datetime.utcnow().isoformat()
    }
    
    # Corrección: Buscar el ID del restaurante real para la FK
    res_r = supabase.table("restaurantes").select("id_restaurante").eq("nombre", "Restinga").limit(1).execute()
    if res_r.data:
        data["id_restaurante"] = res_r.data[0]["id_restaurante"]

    try:
        res = supabase.table("pedidos").insert(data).execute()
        pid = res.data[0]["id_pedido"]
        return str(pid)[-6:].upper()
    except Exception as e:
        logger.error(f"❌ DB Save Error: {e}")
        return "FAIL"

# ==========================================================
# 🤖 LÓGICA BOT (v8.9 ORIGINAL ADAPTADA)
# ==========================================================
async def process_msg(body: dict):
    if body.get("object") != "whatsapp_business_account": return
    
    for entry in body.get("entry", []):
        for ch in entry.get("changes", []):
            val = ch.get("value", {})
            for msg in val.get("messages", []):
                if msg.get("type") != "text": continue
                
                uid = msg.get("from")
                txt = msg["text"]["body"].strip()
                tl = txt.lower()
                
                lang = user_lang.get(uid, "spanish")
                fase = pedido_estado.get(uid, {}).get("fase", "inicio")

                # 1️⃣ RESET
                if tl in ['q','salir','exit','reiniciar']:
                    carts.pop(uid, None); pedido_estado.pop(uid, None)
                    pedido_estado[uid] = {"fase":"lang"}
                    await send_msg(uid, "🌍 *Idioma*\n1. 🇪🇸 2. 🇬🇧 3. 🇫🇷 4. 🇲🇦 5. 🇸🇦")
                    continue

                # 2️⃣ FASES
                if fase == "lang":
                    m = {'1':'spanish','2':'english','3':'french','4':'darija','5':'arabic'}
                    if txt in m:
                        user_lang[uid] = m[txt]; pedido_estado.pop(uid, None)
                        await send_msg(uid, "👋 Hola! Escribe *m* para menú, *c* para confirmar, *q* salir.")
                    else: await send_msg(uid, "❌ 1-5")
                    continue

                if fase == "entrega":
                    if tl=='1': 
                        pedido_estado[uid].update({"tipo":"recoger","fase":"pago"})
                        await send_msg(uid, "💳 *Pago*\n1. Efectivo 2. Tarjeta 3. Transferencia")
                    elif tl=='2': 
                        pedido_estado[uid].update({"tipo":"domicilio","fase":"dir"})
                        await send_msg(uid, "📍 Dirección:")
                    else: await send_msg(uid, "❌ 1 o 2")
                    continue

                if fase == "dir":
                    pedido_estado[uid].update({"direccion":txt,"fase":"pago"})
                    await send_msg(uid, "💳 *Pago*\n1. Efectivo 2. Tarjeta 3. Transferencia")
                    continue

                if fase == "pago":
                    total = sum(i["price"] for i in carts.get(uid,[]))
                    if tl=='1': 
                        pedido_estado[uid].update({"met":"efectivo","fase":"bill"})
                        await send_msg(uid, "💵 ¿Billete? (Ej: 100)")
                    elif tl=='2':
                        res = await save_order_db(uid, total, pedido_estado[uid].get("tipo","recoger"), pedido_estado[uid].get("direccion",""), "tarjeta")
                        carts.pop(uid, None); pedido_estado.pop(uid, None)
                        await send_msg(uid, f"✅ Pedido {res} (Tarjeta).")
                    elif tl=='3':
                        res = await save_order_db(uid, total, "recoger", "", "transferencia")
                        carts.pop(uid, None); pedido_estado.pop(uid, None)
                        await send_msg(uid, f"✅ Pedido {res} (Transferencia pendiente).")
                    else: await send_msg(uid, "❌ 1-3")
                    continue

                if fase == "bill":
                    total = sum(i["price"] for i in carts.get(uid,[]))
                    res = await save_order_db(uid, total, pedido_estado[uid].get("tipo","recoger"), pedido_estado[uid].get("direccion",""), f"efectivo({txt})")
                    carts.pop(uid, None); pedido_estado.pop(uid, None)
                    await send_msg(uid, f"✅ Pedido {res} (Efectivo: {txt}).")
                    continue
                
                # 3️⃣ COMANDOS
                if tl in ['hola','hello','salam']:
                    carts.pop(uid, None); pedido_estado.pop(uid, None)
                    pedido_estado[uid] = {"fase":"lang"}
                    await send_msg(uid, "🌍 *Idioma*\n1. 🇪🇸 2. 🇬🇧 3. 🇫🇷 4. 🇲🇦 5. 🇸🇦")
                    continue

                if tl in ['m','menu']:
                    await send_msg(uid, "📋 *MENÚ RESTINGA*\n1. Tajín (70)\n2. Cuscús (80)\n3. Pastilla (90)\n4. Té (15)\nResponde nº.")
                    continue
                
                if tl in ['v','pedido']:
                    items = carts.get(uid, [])
                    if not items: await send_msg(uid, "🛒 Carrito vacío.")
                    else:
                        t = sum(i['price'] for i in items)
                        txt_menu = "\n".join([f"• {i['name']} — {i['price']}" for i in items])
                        await send_msg(uid, f"🛒 *TU PEDIDO*\n{txt_menu}\n💰 Total: {t}")
                    continue

                if tl in ['c','confirmar']:
                    if not carts.get(uid): await send_msg(uid, "⚠️ Vacío. Escribe *m*.")
                    else: pedido_estado[uid]={"fase":"entrega"}; await send_msg(uid, "🚚 *Entrega*\n1. Recoger 2. Domicilio")
                    continue

                if tl in ['r','reservar']:
                    pedido_estado[uid]={"fase":"res_pax"}; await send_msg(uid, "👥 ¿Personas?")
                    continue

                if tl.isdigit():
                    # Simulación de añadir al carrito (en prod consultar DB)
                    platos = {"1":{"n":"Tajín","p":70}, "2":{"n":"Cuscús","p":80}, "3":{"n":"Pastilla","p":90}, "4":{"n":"Té","p":15}}
                    if txt in platos:
                        if uid not in carts: carts[uid] = []
                        carts[uid].append({"name": platos[txt]['n'], "price": platos[txt]['p']})
                        t = sum(i['price'] for i in carts[uid])
                        await send_msg(uid, f"✅ {platos[txt]['n']} añadido. Total: {t}")
                    else: await send_msg(uid, "❌ Nº inválido")
                    continue

                await send_msg(uid, "❓ *m* menú | *v* pedido | *c* confirmar | *r* reservar | *q* salir")

# ==========================================================
# 🌐 ENDPOINTS
# ==========================================================
@app.get("/health")
def health(): return {"status":"ok","version":VERSION}

@app.get("/api/whatsapp/webhook")
def wb_get(req: Request):
    p = req.query_params
    if p.get("hub.mode")=="subscribe" and p.get("hub.verify_token")==VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge"))
    return PlainTextResponse("Forbidden", status_code=403)

@app.post("/api/whatsapp/webhook")
async def wb_post(req: Request, bg: BackgroundTasks):
    try:
        bg.add_task(process_msg, await req.json())
        return {"status":"ok"}
    except: return {"status":"error"}, 500

# ==========================================================
# 📊 PANEL HTML (Sencillo y Funcional)
# ==========================================================
HTML_LOGIN = """
<html><body class="bg-gray-100 flex h-screen items-center justify-center">
<div class="bg-white p-8 rounded shadow w-96"><h1 class="text-xl font-bold mb-4">🔐 Panel Recepción</h1>
<form action="/panel/login" method="post"><input name="api_key" class="w-full p-2 border mb-4" placeholder="API Key"><button class="w-full bg-blue-600 text-white p-2 rounded">Entrar</button></form></div></body></html>
"""

HTML_RECEPCION = """
<html><head><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-6">
<h1 class="text-2xl font-bold mb-6">📊 Pedidos Activos</h1>
<div id="pedidos" class="text-gray-500">Cargando...</div>
<script>
fetch('/api/pedidos-activos').then(r=>r.json()).then(d=>{
    document.getElementById('pedidos').innerHTML = d.map(p=>`<div class="border-b p-2">${p.id_pedido} | ${p.total} MAD | ${p.estado}</div>`).join('');
});
</script></body></html>
"""

@app.get("/panel/login")
def panel_login():
    return HTMLResponse(content=HTML_LOGIN)

@app.post("/panel/login")
def panel_login_post(req: Request, api_key: str = Form(...)):
    req.session["auth"] = "ok"
    return RedirectResponse("/panel/recepcion", status_code=303)

@app.get("/panel/recepcion")
def panel_recepcion(req: Request):
    if req.session.get("auth") != "ok": return RedirectResponse("/panel/login")
    return HTMLResponse(content=HTML_RECEPCION)

@app.get("/api/pedidos-activos")
def get_pedidos_api():
    if not supabase: return []
    res = supabase.table("pedidos").select("id_pedido", "total", "estado").eq("estado", "pendiente").order("created_at", desc=True).limit(10).execute()
    return res.data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
