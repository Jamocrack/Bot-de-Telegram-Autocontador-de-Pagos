"""
processor.py — Análisis de comprobantes de pago con IA.

Flujo:
  1. Leer imagen local → codificar en Base64.
  2. Enviar a la API de OpenRouter (google/gemini-flash-1.5) con visión.
  3. Parsear la respuesta como JSON puro.
  4. Retornar un dict con: emisor, monto, numero_operacion, fecha.
"""

import base64
import json
import logging
import os
import re
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
load_dotenv()

OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
if not OPENROUTER_API_KEY:
    raise ValueError(
        "❌ No se encontró OPENROUTER_API_KEY en el archivo .env."
    )

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.0-flash-001"   # Mejor reconocimiento visual que flash-1.5

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt del sistema
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
Eres un experto en OCR y lectura de comprobantes de pago digitales peruanos.
Puedes leer capturas de pantalla, fotos de pantalla, recortes e imágenes de:

- Yape, Plin (Perú)
- Lemon Cash, Binance (Internacional/Cripto)
- Apps bancarias: BCP, BBVA, Interbank, Scotiabank, Santander, Galicia, etc.
- Capturas de transferencias bancarias internacionales o locales.
- Notificaciones de confirmación de envío de dinero.

EXTRAE los siguientes campos:
  - emisor: nombre de la aplicación o banco DESDE DONDE sale el dinero (ej: "Lemon", "Yape", "Binance"). No lo confundas con el banco de destino.
  - pagador: nombre completo del remitente.
  - monto: SOLO el número decimal del monto de la transacción.
  - numero_operacion: el código único de la transacción (Cód. operación).
  - fecha: fecha en formato YYYY-MM-DD.

REGLAS ESTRICTAS:
1. Si un campo no es legible, usa null.
2. NUNCA inventes nombres ni códigos.
3. Responde SOLAMENTE con el objeto JSON puro.

Ejemplo:
{"emisor": "Yape", "pagador": "Carlos Alberto Ramos", "monto": 25.50, "numero_operacion": "12345678", "fecha": "2024-04-16"}
"""


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------
async def extract_receipt_data(image_path: str | Path) -> dict:
    """
    Analiza un comprobante de pago usando visión por IA y devuelve sus datos.

    Args:
        image_path: Ruta absoluta o relativa a la imagen local.

    Returns:
        Dict con las claves: emisor, monto, numero_operacion, fecha.
        En caso de error, retorna un dict con la clave 'error'.
    """
    image_path = Path(image_path)

    if not image_path.exists():
        logger.error("❌ Imagen no encontrada: %s", image_path)
        return {"error": f"Archivo no encontrado: {image_path}"}

    # --- 1. Codificar imagen en Base64 ---
    try:
        image_b64 = _encode_image_b64(image_path)
        mime_type = _get_mime_type(image_path)
        logger.info("📦 Imagen codificada en Base64 (%s)", image_path.name)
    except Exception as exc:
        logger.exception("Error al codificar la imagen")
        return {"error": f"Error al codificar imagen: {exc}"}

    # --- 2. Construir payload para OpenRouter ---
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}"
                        },
                    },
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                    },
                ],
            }
        ],
        # NO usamos response_format json_object: algunos modelos lo ignoran
        # y la instrucción del prompt es suficiente.
        "temperature": 0,  # Respuestas deterministas y precisas
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Cabeceras recomendadas por OpenRouter para identificar el app
        "HTTP-Referer": "https://github.com/bot_telegram_autocontador",
        "X-Title": "Bot Telegram Autocontador",
    }

    # --- 3. Llamar a la API de forma asíncrona ---
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                response.raise_for_status()
                api_response = await response.json()

        logger.info("✅ Respuesta recibida de OpenRouter")

    except aiohttp.ClientResponseError as exc:
        logger.error("❌ Error HTTP de OpenRouter: %s %s", exc.status, exc.message)
        return {"error": f"Error HTTP {exc.status}: {exc.message}"}
    except aiohttp.ClientError as exc:
        logger.exception("❌ Error de conexión con OpenRouter")
        return {"error": f"Error de conexión: {exc}"}

    # --- 4. Extraer y parsear el contenido JSON ---
    try:
        raw_content: str = (
            api_response["choices"][0]["message"]["content"].strip()
        )
        logger.debug("Respuesta raw de la IA: %s", raw_content)
        data = _parse_json_response(raw_content)
        logger.info("📄 Datos extraídos: %s", data)
        return data

    except (KeyError, IndexError) as exc:
        logger.error("❌ Estructura de respuesta inesperada: %s", api_response)
        return {"error": f"Respuesta inesperada de la API: {exc}"}


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _encode_image_b64(path: Path) -> str:
    """Lee un archivo de imagen y lo devuelve codificado en Base64."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_mime_type(path: Path) -> str:
    """Devuelve el MIME type según la extensión del archivo."""
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return mime_map.get(path.suffix.lower(), "image/jpeg")


def _parse_json_response(raw: str) -> dict:
    """
    Intenta parsear la respuesta de la IA como JSON.

    Maneja dos casos:
      - JSON puro directo.
      - JSON envuelto en bloque ```json ... ``` (por si el modelo ignora la instrucción).
    """
    # Intento directo
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Extracción con regex por si viene en bloque de código
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Búsqueda de cualquier objeto JSON en el texto
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error("❌ No se pudo parsear JSON de la respuesta: %s", raw)
    return {"error": "La IA no devolvió un JSON válido", "raw": raw}
