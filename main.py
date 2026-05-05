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
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    return {
        "message": "Endpoint funciona!",
        "client_id": client_id,
        "platos": [
            {"nombre": "Tajine de Prueba", "precio": 85}
        ]
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main_simple:app", host="0.0.0.0", port=port)
# force deploy Tue May  5 01:16:20 PM +01 2026
