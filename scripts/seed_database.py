import asyncio
import os
import uuid
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def seed():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("⛔ DATABASE_URL no configurada en .env")
    # Asegurar driver psycopg3 (si usas psycopg3)
    # Si usas asyncpg, cambia a postgresql+asyncpg://
    if "postgresql+psycopg" not in db_url and "postgresql+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        # Verificar si ya hay restaurantes
        res = await conn.execute(text("SELECT count(*) FROM restaurantes"))
        if res.scalar() > 0:
            print("⏭️ Base de datos ya poblada. Omitiendo seed.")
            return

        print("🌱 Iniciando seed...")
        r1_id = str(uuid.uuid4())
        r2_id = str(uuid.uuid4())

        # 1. Restaurantes (sin api_key aquí)
        await conn.execute(text("""
            INSERT INTO restaurantes (id_restaurante, nombre, telefono, ciudad, pais, currency_code, default_language, plan, activo)
            VALUES 
            (:r1, 'Restinga', '+212668087490', 'Tetuán', 'Marruecos', 'MAD', 'es', 'basico', true),
            (:r2, 'Café Al Hizam', '+212600000000', 'Marrakech', 'Marruecos', 'MAD', 'es', 'pro', true)
        """), {"r1": r1_id, "r2": r2_id})

        # 2. Configuraciones
        await conn.execute(text("""
            INSERT INTO restaurante_config (id_restaurante, welcome_message, tax_rate, delivery_enabled, pickup_enabled, reservation_enabled, max_reservation_days_ahead, max_guests_per_reservation)
            VALUES 
            (:r1, 'Marhaba bi Restinga!', 0.10, false, true, true, 30, 10),
            (:r2, 'Bienvenido a Al Hizam', 0.10, true, true, true, 30, 8)
        """), {"r1": r1_id, "r2": r2_id})

        # 3. API Keys
        await conn.execute(text("""
            INSERT INTO restaurante_api_keys (id_restaurante, api_key, descripcion)
            VALUES 
            (:r1, 'restinga-key-2026', 'Clave para Restinga'),
            (:r2, 'hizam-key-2026', 'Clave para Café Al Hizam')
        """), {"r1": r1_id, "r2": r2_id})

        # 4. Menús
        m1_id = str(uuid.uuid4())
        m2_id = str(uuid.uuid4())
        await conn.execute(text("""
            INSERT INTO menus (id_menu, id_restaurante, nombre, descripcion, activo, orden)
            VALUES 
            (:m1, :r1, 'Menú Principal', 'Carta completa de Restinga', true, 1),
            (:m2, :r2, 'Menú Principal', 'Carta completa de Al Hizam', true, 1)
        """), {"m1": m1_id, "m2": m2_id, "r1": r1_id, "r2": r2_id})

        # 5. Platos de prueba (Restinga)
        await conn.execute(text("""
            INSERT INTO platos (id_plato, id_menu, nombre, descripcion, precio, categoria, disponible, orden)
            VALUES 
            (gen_random_uuid(), :m1, 'Tajín de Pollo', 'Pollo con verduras y especias', 70.00, 'Especialidades', true, 1),
            (gen_random_uuid(), :m1, 'Cuscús Real', 'Cordero, pollo y verduras', 80.00, 'Especialidades', true, 2),
            (gen_random_uuid(), :m1, 'Paella', 'Paella mixta para 1 persona', 90.00, 'Pescados', true, 3),
            (gen_random_uuid(), :m1, 'Ensalada Restinga', 'Mix especial de la casa', 65.00, 'Entrantes', true, 4)
        """), {"m1": m1_id})

        await conn.commit()
        print("✅ Seed completado: 2 restaurantes, configs, api_keys, menús y 4 platos de prueba.")

if __name__ == "__main__":
    asyncio.run(seed())
