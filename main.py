from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "ok", "message": "Render deploy working"}

@app.get("/health")
async def health():
    return {"status": "healthy", "deploy": "2026-05-05"}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return {
        "success": True,
        "message": f"Endpoint /api/menu funciona!",
        "client_id": client_id,
        "data": {
            "platos": [
                {"id": 1, "nombre": "Tajine de Prueba", "precio": 85},
                {"id": 2, "nombre": "Cuscús de Prueba", "precio": 70}
            ]
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main_render:app", host="0.0.0.0", port=port)

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    """Alias en inglés para /api/platos/{client_id}"""
    return await get_platos(client_id)

