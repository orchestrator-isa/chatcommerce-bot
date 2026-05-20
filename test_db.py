# test_db.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

async def test():
    engine = create_async_engine("TU_DATABASE_URL", echo=True)
    async with engine.connect() as conn:
        result = await conn.execute("SELECT 1")
        print("✅ Conexión exitosa:", result.fetchone())
    await engine.dispose()

asyncio.run(test())
