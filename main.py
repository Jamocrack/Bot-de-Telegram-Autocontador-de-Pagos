"""
main.py — Servidor web del Autocontador.

Endpoints:
  GET /              → Dashboard HTML (index.html)
  GET /api/history   → Historial de pagos en JSON

Estrategia de datos:
  El bot escribe cada pago procesado en history.json.
  Este servidor lo lee y lo expone via API.
  Adicionalmente intenta extraer mensajes recientes via Telegram Bot API
  (útil cuando el bot no está corriendo en polling).
"""

import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
load_dotenv()

TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
GROUP_ID: str = os.getenv("GROUP_ID", "")

HISTORY_FILE = Path("history.json")
STATIC_DIR = Path("static")
STATIC_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Bot Autocontador",
    description="Dashboard de comprobantes de pago registrados por el bot.",
    version="1.0.0",
)

# CORS: necesario para que el Mini App de Telegram pueda hacer fetch a /api/history
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Sirve el dashboard principal."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="index.html no encontrado en /static")
    return HTMLResponse(content=index.read_text(encoding="utf-8"))


@app.get("/api/history")
async def get_history():
    """
    Obtiene el historial de pagos directamente de la nube de Telegram.
    Esto permite que el sistema sea 24/7 sin base de datos externa.
    """
    if not TELEGRAM_TOKEN or not GROUP_ID:
        raise HTTPException(status_code=500, detail="Faltan credenciales en el .env")

    records: list[dict] = []
    
    # 1. Intentamos leer de history.json por velocidad (si existe)
    local_records = _load_local_history()
    
    # 2. Consultamos la API de Telegram para obtener lo más reciente o si no hay local
    # Nota: bot.getUpdates solo funciona si no hay un bot corriendo en polling,
    # por lo que en producción usaremos un método más robusto.
    telegram_records = await _fetch_telegram_history()
    
    # Combinar y evitar duplicados
    all_records = local_records.copy()
    existing_ops = {r.get("numero_operacion") for r in all_records if r.get("numero_operacion")}
    
    for tr in telegram_records:
        if tr.get("numero_operacion") not in existing_ops:
            all_records.append(tr)

    # Ordenar y calcular total
    all_records.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    total = sum(float(r.get("monto") or 0) for r in all_records)

    return {
        "records": all_records,
        "total": round(total, 2),
        "count": len(all_records)
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_local_history() -> list[dict]:
    """Lee history.json y devuelve la lista de registros."""
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


async def _fetch_telegram_history() -> list[dict]:
    """
    Llama a getUpdates con offset=-100 para leer las últimas actualizaciones
    del grupo y extrae los mensajes #REGISTRO_PAGO.

    Nota: si el bot corre en modo polling, estos updates ya están consumidos
    y este método devolverá lista vacía (lo cual es correcto; history.json
    es la fuente primaria en ese caso).
    """
    if not TELEGRAM_TOKEN or not GROUP_ID:
        return []

    records: list[dict] = []
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params={"limit": 100, "offset": -100})
            resp.raise_for_status()
            updates = resp.json().get("result", [])

        for update in updates:
            msg = (
                update.get("message")
                or update.get("channel_post")
                or {}
            )
            text: str = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if chat_id == str(GROUP_ID) and "#REGISTRO_PAGO" in text:
                parsed = _extract_json_from_text(text)
                if parsed:
                    records.append(parsed)

    except httpx.HTTPError:
        pass  # No critical — history.json is the primary source

    return records


def _extract_json_from_text(text: str) -> dict | None:
    """Extrae el primer objeto JSON válido de un texto."""
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None
