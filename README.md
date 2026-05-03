# 🤖 Orquestrator ISA — ChatCommerce Bot

Backend FastAPI para WhatsApp Business API + Supabase.
Sistema de pedidos por chat para restaurantes de Tétouan, Marruecos.

## 🚀 Deploy en Render

1. **Fork/clone** este repo
2. **Crear servicio** en [Render Dashboard](https://dashboard.render.com/)
3. **Configurar Environment Variables** (ver `.env.example`)
4. **Deploy automático** al hacer push

## 🔗 Links Importantes

| Servicio | URL |
|----------|-----|
| Render Dashboard | https://dashboard.render.com/ |
| Meta Developers | https://developers.facebook.com/apps/ |
| Meta Business Settings | https://business.facebook.com/settings/whatsapp_account/ |
| Supabase Dashboard | https://supabase.com/dashboard/project/ktsgmfxokvheosqpoetr |
| Supabase API Settings | https://supabase.com/dashboard/project/ktsgmfxokvheosqpoetr/settings/api |
| App Live | https://chatcommerce-bot.onrender.com |
| Health Check | https://chatcommerce-bot.onrender.com/health |
| Webhook URL | https://chatcommerce-bot.onrender.com/api/whatsapp/webhook |

## 📋 Variables de Entorno (Render)

| Variable | Valor | Dónde obtener |
|----------|-------|---------------|
| `WHATSAPP_TOKEN` | Token permanente | [Meta Business Settings](https://business.facebook.com/settings/system-users) |
| `VERIFY_TOKEN` | `isa_verify_2026` | Inventado por ti, debe coincidir en Meta |
| `PHONE_NUMBER_ID` | ID del número | [Meta WhatsApp Setup](https://developers.facebook.com/apps/) |
| `SUPABASE_URL` | `https://ktsgmfxokvheosqpoetr.supabase.co` | [Supabase Settings](https://supabase.com/dashboard/project/ktsgmfxokvheosqpoetr/settings/api) |
| `SUPABASE_KEY` | service_role key | [Supabase Settings](https://supabase.com/dashboard/project/ktsgmfxokvheosqpoetr/settings/api) |
| `WEBHOOK_PREFIX` | `/api/whatsapp/webhook` | Fijo |

## 🧪 Probar Webhook Localmente

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Crear .env (copiar de .env.example y rellenar)
cp .env.example .env

# 3. Ejecutar
uvicorn main:app --reload --port 8000

# 4. Probar health check
curl http://localhost:8000/health

# 5. Simular verificación Meta (GET)
curl "http://localhost:8000/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=isa_verify_2026&hub.challenge=123456"

# 6. Simular mensaje entrante (POST)
curl -X POST http://localhost:8000/api/whatsapp/webhook \
  -H "Content-Type: application/json" \
  -d '{"object":"whatsapp_business_account","entry":[{"id":"test","changes":[{"value":{"messages":[{"from":"212600000000","id":"test_msg","timestamp":"1234567890","type":"text","text":{"body":"hola"}}]}}]}]}'
```

## 🗄️ Esquema de Base de Datos (Supabase)

### Tabla: `clients`
```sql
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    owner_phone TEXT,
    language TEXT DEFAULT 'darija',
    is_active BOOLEAN DEFAULT true,
    total_messages INTEGER DEFAULT 0,
    total_orders INTEGER DEFAULT 0,
    last_activity TIMESTAMP,
    plan TEXT DEFAULT 'basic',
    business_type TEXT,
    zone TEXT,
    google_reviews INTEGER,
    google_rating DECIMAL,
    address_hint TEXT,
    whatsapp_status TEXT,
    trial_ends_at DATE
);
```

### Tabla: `menu_items`
```sql
CREATE TABLE menu_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES clients(id),
    category TEXT,
    dish_name TEXT NOT NULL,
    description TEXT,
    price INTEGER,
    is_available BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Tabla: `messages` (logs)
```sql
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_number TEXT,
    message_text TEXT,
    direction TEXT,  -- 'incoming' | 'outgoing'
    created_at TIMESTAMP DEFAULT NOW()
);
```

## 🔧 Troubleshooting

### "Webhook verification failed" en Meta
- Verifica que `VERIFY_TOKEN` en Render coincide con el de Meta
- La URL debe ser exactamente: `https://chatcommerce-bot.onrender.com/api/whatsapp/webhook`

### "No responde mensajes"
1. Revisa logs en [Render Dashboard](https://dashboard.render.com/)
2. Confirma que `PHONE_NUMBER_ID` está correcto
3. Verifica que `WHATSAPP_TOKEN` es permanente (no caduca)
4. Confirma suscripción a `messages` en [Meta Webhooks](https://developers.facebook.com/apps/)

### "Supabase connection error"
- Usa `service_role key`, NO `anon key`
- Verifica que la URL no tiene `/` al final

## 📞 Comandos del Bot

| Comando | Respuesta |
|---------|-----------|
| `hola` / `salam` | Mensaje de bienvenida |
| `menu` | Menú del restaurante |
| `lista` | Lista de restaurantes |
| `pedido` | Pedido actual |
| `ayuda` | Lista de comandos |

---
**Orquestrator ISA** — ChatCommerce para Tétouan, Marruecos 🇲🇦
