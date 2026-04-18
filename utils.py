import requests
import logging
import time

logger = logging.getLogger(__name__)

# Cache en memoria para evitar llamadas repetidas a la API durante procesamiento en lote
_exchange_cache: dict = {"rate": 3.80, "ts": 0.0}
_CACHE_TTL_SECONDS = 3600  # Renovar cada hora


def get_exchange_rate() -> float:
    """Obtiene el tipo de cambio USD a PEN.
    
    Usa caché en memoria con TTL de 1 hora para evitar llamadas repetidas
    a la API durante el procesamiento masivo de recibos en cola.
    Devuelve el último valor conocido si la API falla (nunca falla silenciosamente).
    """
    now = time.time()
    if now - _exchange_cache["ts"] < _CACHE_TTL_SECONDS:
        return _exchange_cache["rate"]

    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        rate = float(data["rates"].get("PEN", 3.80))
        _exchange_cache["rate"] = rate
        _exchange_cache["ts"] = now
        logger.info("Tipo de cambio actualizado: 1 USD = %.4f PEN", rate)
        return rate
    except Exception as e:
        logger.error("Error al obtener tipo de cambio: %s — usando último valor conocido (%.2f)", e, _exchange_cache["rate"])
        return _exchange_cache["rate"]  # Devuelve último valor conocido en lugar de hardcodear


def format_progress_bar(current: int, total: int, length: int = 10) -> str:
    """Genera una barra de progreso visual con bloques Unicode."""
    progress = min(current / total, 1.0) if total > 0 else 0
    filled = int(length * progress)
    bar = "▓" * filled + "░" * (length - filled)
    return f"[{bar}] {int(progress * 100)}%"
