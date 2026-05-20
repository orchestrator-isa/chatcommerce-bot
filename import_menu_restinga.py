import asyncio
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select
from models import Restaurante, Menu, Plato  # Ajusta imports

ENTRANTES = [
    ("Ensalada mezclada", "Mix de lechugas, tomate, cebolla", 40),
    ("Ensalada rusa", "Patata, zanahoria, guisantes, atún", 35),
    ("Ensalada marroquí", "Tomate, pepino, pimiento, cebolla", 35),
    # ... añadir todos del menu_es.md
]

async def main():
    engine = create_async_engine("TU_DATABASE_URL")
    async with AsyncSession(engine) as db:
        # Obtener restaurante
        result = await db.execute(select(Restaurante).where(Restaurante.api_key == "restinga-key-2026"))
        rest = result.scalar_one()
        
        # Obtener menú principal
        result = await db.execute(select(Menu).where(Menu.id_restaurante == rest.id_restaurante))
        menu = result.scalar_one()
        
        # Insertar platos
        for nombre, desc, precio in ENTRANTES:
            plato = Plato(
                id_menu=menu.id_menu,
                nombre=nombre,
                descripcion=desc,
                precio=precio,
                categoria="Entrantes",
                disponible=True
            )
            db.add(plato)
        
        await db.commit()
        print(f"✅ {len(ENTRANTES)} platos insertados")

asyncio.run(main())
