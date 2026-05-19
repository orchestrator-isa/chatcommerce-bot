import os
import json
import httpx
import logging
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from supabase import create_client, Client
from datetime import datetime, timezone

# ---------------------------------------------------------
# CONFIGURACIÓN & LOGGER
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("orquestrator_bot")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WA_TOKEN = os.getenv("WA_TOKEN")
WA_PHONE_ID = os.getenv("WA_PHONE_NUMBER_ID")
WA_VERIFY = os.getenv("WA_VERIFY_TOKEN")

if not all([SUPABASE_URL, SUPABASE_KEY, WA_TOKEN, WA_PHONE_ID]):
    logger.error("Faltan variables de entorno críticas. Revisa configuración en Render.")
    raise ValueError("Environment variables missing")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI(title="Orquestrator ISA Bot", version="2.0.0")

HEADERS = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
WA_BASE = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}"

# ---------------------------------------------------------
# GESTIÓN DE ESTADO EN MEMORIA (Por teléfono)
# ---------------------------------------------------------
user_sessions: Dict[str, Dict[str, Any]] = {}

def init_session(phone: str, lang: str = "1") -> Dict[str, Any]:
    user_sessions[phone] = {
        "lang": lang,
        "state": "LANG_SELECT",
        "cart": {},
        "temp_data": {}
    }
    return user_sessions[phone]

# ---------------------------------------------------------
# TRADUCCIONES & MENÚ (57 items compactos)
# ---------------------------------------------------------
LANG_DICT = {
    "1": {"welcome": "¡Bienvenido! Elige idioma:\n1. Español\n2. English\n3. Français\n4. Darija\n5. العربية", "menu": "📋 MENÚ COMPLETO", "cart": "🛒 Ver carrito", "reserve": "📅 Reserva", "pay_method": "💳 Método de pago:\n1. Efectivo (pregunta billete)\n2. Tarjeta\n3. Transferencia", "cash_bill": "💵 ¿Qué billete entregarás? (20, 50, 100, 200)", "addr_q": "📍 Dirección de entrega y referencia:", "success": "✅ Pedido guardado. Te contactaremos pronto.", "res_people": "👥 ¿Cuántas personas?", "res_time": "🕒 Fecha y hora (ej: 20-05-2026 20:30)", "not_understood": "🤔 Ma fhamteksh. Usa los números del menú.", "total": "Total", "items": "Platos"},
    "2": {"welcome": "Welcome! Choose language:\n1. Español\n2. English\n3. Français\n4. Darija\n5. العربية", "menu": "📋 FULL MENU", "cart": "🛒 View cart", "reserve": "📅 Reservation", "pay_method": "💳 Payment method:\n1. Cash\n2. Card\n3. Bank Transfer", "cash_bill": "💵 Which bill will you give? (20, 50, 100, 200)", "addr_q": "📍 Delivery address & landmark:", "success": "✅ Order saved. We will contact you shortly.", "res_people": "👥 How many people?", "res_time": "🕒 Date & time (e.g. 20-05-2026 20:30)", "not_understood": "🤔 I didn't understand. Use menu numbers.", "total": "Total", "items": "Items"},
    "3": {"welcome": "Bienvenue! Choisissez la langue:\n1. Español\n2. English\n3. Français\n4. Darija\n5. العربية", "menu": "📋 MENU COMPLET", "cart": "🛒 Voir panier", "reserve": "📅 Réservation", "pay_method": "💳 Méthode de paiement:\n1. Espèces\n2. Carte\n3. Virement", "cash_bill": "💵 Quel billet donnerez-vous? (20, 50, 100, 200)", "addr_q": "📍 Adresse de livraison:", "success": "✅ Commande enregistrée.", "res_people": "👥 Combien de personnes?", "res_time": "🕒 Date et heure (ex: 20-05-2026 20:30)", "not_understood": "🤔 Je n'ai pas compris.", "total": "Total", "items": "Plats"},
    "4": {"welcome": "Merhba! Khtari logha:\n1. Español\n2. English\n3. Français\n4. Darija\n5. العربية", "menu": "📋 MENYU KAMIL", "cart": "🛒 Chof lpanier", "reserve": "📅 Rezervasyon", "pay_method": "💳 Tariqa dyal lkhlasa:\n1. Cash\n2. Carte\n3. Virement", "cash_bill": "💵 Chno lbiye li ghadi t3ti? (20, 50, 100, 200)", "addr_q": "📍 Fin bach nwasluk?", "success": "✅ Lcomande tktbat. Ghadi n3awduk.", "res_people": "👥 Sh7al dyal nass?", "res_time": "🕒 Tarikh w sa3a (ex: 20-05-2026 20:30)", "not_understood": "🤔 Ma fhamteksh. St3mel rakam.", "total": "Majmou3", "items": "Plats"},
    "5": {"welcome": "مرحباً! اختر اللغة:\n1. Español\n2. English\n3. Français\n4. Darija\n5. العربية", "menu": "📋 القائمة الكاملة", "cart": "🛒 عرض السلة", "reserve": "📅 حجز", "pay_method": "💳 طريقة الدفع:\n1. نقداً\n2. بطاقة\n3. تحويل", "cash_bill": "💵 ما الفئة النقدية؟ (20, 50, 100, 200)", "addr_q": "📍 عنوان التوصيل:", "success": "✅ تم حفظ الطلب.", "res_people": "👥 كم شخص؟", "res_time": "🕒 التاريخ والوقت (مثال: 20-05-2026 20:30)", "not_understood": "🤔 لم أفهم. استخدم الأرقام.", "total": "المجموع", "items": "الأطباق"}
}

# Menú compacto 57 items
BASE_MENU = [
    "Tagine Pollo", "Tagine Cordero", "Tagine Ternera", "Tagine Kefta", "Tagine Verduras",
    "Couscous Pollo", "Couscous Cordero", "Couscous 7 verduras", "Couscous Tfaya", "Couscous Seffa",
    "Pastilla Pollo", "Pastilla Marisco", "Pastilla Cordero", "Rfissa Pollo", "Rfissa Ternera",
    "Harira Clásica", "Harira Especial", "Bissara", "Chorba Frik", "Zaalouk",
    "Briouat Kefta", "Briouat Pollo", "Briouat Queso", "Samosa Ternera", "Sfenj",
    "Baghrir", "Msemen", "Harcha", "Tajine Mrouzia", "Mechoui",
    "Tanjiya Marrakech", "Tanjia Fez", "Kebab Pollo", "Kebab Kefta", "Brochetas Mixtas",
    "Filete de Ternera", "Pollo a la brasa", "Pescado Frito", "Sardinas a la plancha", "Calamares",
    "Gambas al Ajillo", "Marisco Mixto", "Pizza Margherita", "Pizza 4 Quesos", "Pizza Hawaiana",
    "Ensalada Marroquí", "Ensalada Mixta", "Ensalada César", "Patatas Fritas", "Arroz Blanco",
    "Sémola", "Pan Artesanal", "Batido Aguacate", "Zumo Naranja", "Té a la Menta",
    "Café Solo", "Café con Leche"
]
MENU = [f"{i+1}. {name} - {50 + (i%10)*10} MAD" for i, name in enumerate(BASE_MENU)]

# ---------------------------------------------------------
# HELPERS: WHATSAPP & DB
# ---------------------------------------------------------
async def send_wa(phone: str, text: str):
    """Envía mensajes respetando el límite de ~1500 chars por chunk"""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 1500:
            chunks.append(current)
            current = line
        else:
            current += ("\n" + line) if current else line
    if current: chunks.append(current)

    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone,
                "type": "text",
                "text": {"body": chunk}
            }
            try:
                res = await client.post(f"{WA_BASE}/messages", json=payload, headers=HEADERS)
                res.raise_for_status()
            except Exception as e:
                logger.error(f"WA Send Error: {e}")

def format_cart(cart: Dict, lang: str) -> str:
    if not cart: return LANG_DICT[lang]["cart"] + ": Vacío"
    t = LANG_DICT[lang]
    lines = [f"🛒 {t['cart']}:"]
    total = 0
    for item, data in cart.items():
        subtotal = data["price"] * data["qty"]
        total += subtotal
        lines.append(f"• {data['name']} x{data['qty']} = {subtotal} MAD")
    lines.append(f"\n💰 {t['total']}: {total} MAD")
    return "\n".join(lines)

async def save_order(phone: str, items: Dict, total: float, metodo: str, billete: str, direccion: str):
    try:
        data = {
            "customer_phone": phone,
            "items_json": json.dumps(items),
            "total_mad": total,
            "estado": "pendiente",
            "tipo_entrega": "delivery",
            "direccion": direccion,
            "metodo_pago": metodo,
            "billete": billete,
            "pagado": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        supabase.table("orders").insert(data).execute()
        logger.info(f"Pedido guardado en Supabase para {phone}")
    except Exception as e:
        logger.error(f"DB Insert Error: {e}")
        raise

# ---------------------------------------------------------
# FASTAPI: WEBHOOKS
# ---------------------------------------------------------
@app.get("/api/whatsapp/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == WA_VERIFY:
        return JSONResponse(content=int(challenge), status_code=200)
    return JSONResponse(status_code=403)

@app.post("/api/whatsapp/webhook")
async def handle_wa_message(request: Request):
    payload = await request.json()
    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]["value"]
        msg_obj = changes.get("messages", [{}])[0]
        if not msg_obj or msg_obj["type"] != "text":
            return JSONResponse(status_code=200)
        
        phone = changes["contacts"][0]["wa_id"]
        text = msg_obj["text"]["body"].strip().lower()

        # Inicializar si es nuevo o reset (q)
        if phone not in user_sessions or text in ("q", "reset", "inicio"):
            init_session(phone)
            await send_wa(phone, LANG_DICT["1"]["welcome"])
            return JSONResponse(status_code=200)

        sess = user_sessions[phone]
        lang = sess["lang"]
        t = LANG_DICT.get(lang, LANG_DICT["1"])

        # -----------------------------------------------------
        # MÁQUINA DE ESTADOS
        # -----------------------------------------------------
        if sess["state"] == "LANG_SELECT":
            if text in LANG_DICT:
                sess["lang"] = text
                t = LANG_DICT[text]
                sess["state"] = "MAIN"
                menu_header = f"{t['menu']} (57 platos)\nEscribe 'menu' para verlos."
                await send_wa(phone, f"{menu_header}\n\n{t['cart']} (v)\n{t['reserve']} (r)\nReiniciar (q)")
            else:
                await send_wa(phone, "1️⃣/2️⃣/3️⃣/4️⃣/5️⃣")
            return JSONResponse(status_code=200)

        if text in ("v", "pedido"):
            await send_wa(phone, format_cart(sess["cart"], lang))
            return JSONResponse(status_code=200)

        if text in ("c", "confirmar"):
            if not sess["cart"]:
                await send_wa(phone, "🛒 " + t["not_understood"])
                return JSONResponse(status_code=200)
            sess["state"] = "PAY_METHOD"
            await send_wa(phone, t["pay_method"])
            return JSONResponse(status_code=200)

        if text == "menu" or text == "m":
            await send_wa(phone, "\n".join(MENU))
            return JSONResponse(status_code=200)

        # Añadir al carrito
        if text.isdigit() and 1 <= int(text) <= 57:
            idx = int(text) - 1
            item_name = BASE_MENU[idx]
            price = 50 + (idx % 10) * 10
            sess["cart"][item_name] = {"name": item_name, "price": price, "qty": sess["cart"].get(item_name, {}).get("qty", 0) + 1}
            await send_wa(phone, f"✅ {item_name} añadido.\n{format_cart(sess['cart'], lang)}")
            return JSONResponse(status_code=200)

        # Flujo de Pago
        if sess["state"] == "PAY_METHOD":
            if text == "1":
                sess["state"] = "CASH_BILL"
                await send_wa(phone, t["cash_bill"])
            elif text in ("2", "3"):
                sess["temp_data"]["metodo"] = "tarjeta" if text == "2" else "transferencia"
                sess["state"] = "DELIVERY"
                await send_wa(phone, t["addr_q"])
            else:
                await send_wa(phone, "1️⃣/2️⃣/3️⃣")
            return JSONResponse(status_code=200)

        if sess["state"] == "CASH_BILL":
            sess["temp_data"]["metodo"] = "efectivo"
            sess["temp_data"]["billete"] = text
            sess["state"] = "DELIVERY"
            await send_wa(phone, t["addr_q"])
            return JSONResponse(status_code=200)

        if sess["state"] == "DELIVERY":
            total = sum(d["price"] * d["qty"] for d in sess["cart"].values())
            await save_order(phone, sess["cart"], total, sess["temp_data"]["metodo"], sess["temp_data"].get("billete", "N/A"), text)
            await send_wa(phone, t["success"])
            sess["cart"] = {}
            sess["temp_data"] = {}
            sess["state"] = "MAIN"
            return JSONResponse(status_code=200)

        # Reserva
        if text in ("r", "reserva"):
            sess["state"] = "RES_PEOPLE"
            await send_wa(phone, t["res_people"])
            return JSONResponse(status_code=200)

        if sess["state"] == "RES_PEOPLE":
            if text.isdigit():
                sess["temp_data"]["res_people"] = text
                sess["state"] = "RES_DATE"
                await send_wa(phone, t["res_time"])
            else:
                await send_wa(phone, "🔢")
            return JSONResponse(status_code=200)

        if sess["state"] == "RES_DATE":
            await send_wa(phone, f"📅 Reserva confirmada: {sess['temp_data']['res_people']} personas el {text}. Te esperamos.")
            sess["temp_data"] = {}
            sess["state"] = "MAIN"
            return JSONResponse(status_code=200)

        # Fallback
        sess["state"] = "MAIN"
        await send_wa(phone, t["not_understood"])
        return JSONResponse(status_code=200)

    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return JSONResponse(status_code=500)

# ---------------------------------------------------------
# HEALTHCHECK
# ---------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
