-- ============================================================
-- Orquestrator ISA — Setup de Base de Datos Supabase
-- Ejecutar en SQL Editor de Supabase
-- ============================================================

-- Tabla: clients (restaurantes)
CREATE TABLE IF NOT EXISTS clients (
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

-- Tabla: menu_items (platos)
CREATE TABLE IF NOT EXISTS menu_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
    category TEXT,
    dish_name TEXT NOT NULL,
    description TEXT,
    price INTEGER,
    is_available BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Tabla: messages (logs de conversación)
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_number TEXT,
    message_text TEXT,
    direction TEXT CHECK (direction IN ('incoming', 'outgoing')),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Tabla: orders (pedidos)
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES clients(id),
    customer_phone TEXT,
    items JSONB DEFAULT '[]',
    total_amount INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_clients_active ON clients(is_active);
CREATE INDEX IF NOT EXISTS idx_clients_zone ON clients(zone);
CREATE INDEX IF NOT EXISTS idx_menu_client ON menu_items(client_id);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_number);
CREATE INDEX IF NOT EXISTS idx_orders_phone ON orders(customer_phone);

-- Políticas RLS (deshabilitar si usas service_role key desde backend)
-- Si usas service_role, RLS no aplica. Si usas anon key, activa estas:
-- ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE menu_items ENABLE ROW LEVEL SECURITY;
