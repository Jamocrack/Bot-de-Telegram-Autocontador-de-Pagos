import requests
import logging

logger = logging.getLogger(__name__)

def get_exchange_rate():
    """Obtiene el tipo de cambio USD a PEN desde una API gratuita."""
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(url, timeout=5)
        data = response.json()
        return data["rates"].get("PEN", 3.80)
    except Exception as e:
        logger.error(f"Error al obtener tipo de cambio: {e}")
        return 3.80 # Fallback razonable

def format_progress_bar(current, total, length=10):
    """Genera una barra de progreso visual con emojis."""
    progress = min(current / total, 1.0) if total > 0 else 0
    filled = int(length * progress)
    bar = "▓" * filled + "░" * (length - filled)
    return f"[{bar}] {int(progress * 100)}%"
