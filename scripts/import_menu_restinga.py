import asyncio
import os
import uuid
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Datos extraídos de menu_es.md (versión completa)
MENU_RESTINGA = [
    ("Ensalada mezclada", "Mix de lechugas, tomate, cebolla, atún", 40, "Entrantes"),
    ("Ensalada rusa", "Patata, zanahoria, guisantes, atún", 35, "Entrantes"),
    ("Ensalada marroquí", "Tomate, pepino, pimiento, cebolla", 35, "Entrantes"),
    ("Ensalada tropical", "Mix de frutas y verduras", 45, "Entrantes"),
    ("Ensalada Restinga", "Especial de la casa", 65, "Entrantes"),
    ("Sopa de verduras", "Casera del día", 35, "Entrantes"),
    ("Sopa de pescado", "Caldo fresco de mariscos", 45, "Entrantes"),
    ("Concha fina", "Moluscos frescos", 60, "Entrantes"),
    ("Anchoas marinadas", "Aliñadas al estilo local", 50, "Entrantes"),
    ("Croquetas", "Caseras de pollo o jamón", 45, "Entrantes"),
    ("Huevo con papas fritas", "Clásico reconfortante", 40, "Entrantes"),
    ("Tortilla española", "Patatas y cebolla", 45, "Entrantes"),
    ("Tortilla francesa", "Con o sin relleno", 40, "Entrantes"),
    ("Papas fritas", "Ración generosa", 25, "Entrantes"),
    ("Espaguetis", "Salsa básica", 55, "Entrantes"),
    ("Espaguetis con mariscos", "Frescos del día", 75, "Entrantes"),
    ("Espaguetis a la boloñesa", "Salsa de carne", 65, "Entrantes"),
    ("Camarones al ajillo", "Pil Pil tradicional", 75, "Entrantes"),
    ("Tortilla de atún", "Fresca y ligera", 60, "Entrantes"),
    ("Cuscús de carne", "Cordero tradicional", 80, "Especialidades"),
    ("Cuscús de pollo", "Pollo con verduras", 70, "Especialidades"),
    ("Cuscús de verduras", "Opción vegetariana", 65, "Especialidades"),
    ("Tajín de carne", "Guiso marroquí", 85, "Especialidades"),
    ("Tajín de pollo", "Pollo con aceitunas y limón", 70, "Especialidades"),
    ("Tajín de carne picada", "Kefta tradicional", 75, "Especialidades"),
    ("Tajín de hígado", "Receta especial", 95, "Especialidades"),
    ("Pastilla de pollo", "Hojaldre relleno dulce-salado", 90, "Especialidades"),
    ("Brocheta de carne", "A la parrilla", 80, "Especialidades"),
    ("Brocheta de pollo", "Marinada especias", 70, "Especialidades"),
    ("Kebab mixto", "Pollo y carne", 85, "Especialidades"),
    ("Brocheta de carne picada", "Kefta a la parrilla", 75, "Especialidades"),
    ("Filete de res", "A elegir", 110, "Especialidades"),
    ("Filete de pollo", "Jugoso y tierno", 70, "Especialidades"),
    ("Filete de pollo con salsa de champiñones", "Cremoso", 80, "Especialidades"),
    ("Filete de hígado", "A la plancha", 95, "Especialidades"),
    ("Lenguado", "Pescado fresco", 90, "Pescados"),
    ("Calamar", "A la plancha o frito", 95, "Pescados"),
    ("Salmonete", "Del Mediterráneo", 75, "Pescados"),
    ("Pescadilla", "Frita o a la parrilla", 75, "Pescados"),
    ("Pescado frito mixto", "Variedad del día", 95, "Pescados"),
    ("Anchoas", "Fritas o rebozadas", 60, "Pescados"),
    ("Pez espada Rigamontti", "Especialidad", 100, "Pescados"),
    ("Tajín de mariscos", "Salsa especiada", 95, "Pescados"),
    ("Pastilla de pescado", "Hojaldre de mariscos", 100, "Pescados"),
    ("Lenguado a la parrilla", "Con mantequilla", 95, "Pescados"),
    ("Calamares a la plancha", "Tiernos", 95, "Pescados"),
    ("Salmonete a la parrilla", "Clásico", 80, "Pescados"),
    ("Pez espada a la parrilla", "Jugoso", 95, "Pescados"),
    ("Gambas a la plancha", "Al ajillo o naturales", 95, "Pescados"),
    ("Paella (1 persona)", "Arroz con mariscos", 90, "Pescados"),
    ("Brocheta de pescado", "Del día", 95, "Pescados"),
    ("Tajín de anchoa", "Receta local", 70, "Pescados"),
    ("Flan", "Casero de vainilla", 25, "Postres"),
    ("Fruta de temporada", "Fresca", 30, "Postres"),
    ("Festival", "Tarta, fruta y flan", 45, "Postres"),
    ("Helados", "Variedad de sabores", 25, "Postres"),
    ("Magnum", "Clásico", 30, "Postres"),
    ("Tarta de nougat‑limón", "Cítrica", 30, "Postres"),
    ("Tarta Lotus", "Caramelizada", 35, "Postres"),
]

async def import_menu():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("⛔ Falta DATABASE_URL en .env")
    if "postgresql+psycopg" not in db_url and "postgresql+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        # Obtener el menú de Restinga a través de la API key
        res = await conn.execute(text("""
            SELECT m.id_menu
            FROM menus m
            JOIN restaurantes r ON m.id_restaurante = r.id_restaurante
            JOIN restaurante_api_keys ak ON r.id_restaurante = ak.id_restaurante
            WHERE ak.api_key = 'restinga-key-2026'
            LIMIT 1
        """))
        row = res.fetchone()
        if not row:
            print("❌ No se encontró el menú de Restinga. Ejecuta seed_database.py primero.")
            return
        menu_id = row[0]

        print(f"📥 Importando {len(MENU_RESTINGA)} platos a Restinga...")
        # Primero obtener el máximo orden actual para no pisar (opcional)
        for nombre, desc, precio, cat in MENU_RESTINGA:
            await conn.execute(text("""
                INSERT INTO platos (id_plato, id_menu, nombre, descripcion, precio, categoria, disponible, orden)
                VALUES (:id, :menu, :nombre, :desc, :precio, :cat, true, DEFAULT)
            """), {
                "id": str(uuid.uuid4()),
                "menu": str(menu_id),
                "nombre": nombre,
                "desc": desc,
                "precio": precio,
                "cat": cat
            })
        await conn.commit()
        print("✅ Menú completo importado a Supabase.")

if __name__ == "__main__":
    asyncio.run(import_menu())
