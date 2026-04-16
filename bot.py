"""
Bot de Telegram - Autocontador
Recibe imágenes de comprobantes en grupos, las analiza con IA (OpenRouter)
y publica los datos estructurados del pago en el mismo grupo.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar .env ANTES de importar cualquier módulo propio que
# valide variables de entorno a nivel de módulo (processor.py).
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)

from processor import extract_receipt_data

# ---------------------------------------------------------------------------
# Configuración de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
if not TELEGRAM_TOKEN:
    raise ValueError(
        "❌ No se encontró TELEGRAM_TOKEN en el archivo .env. "
        "Por favor, configura la variable antes de ejecutar el bot."
    )

WEBAPP_URL: str = os.getenv("WEBAPP_URL", "")

# ---------------------------------------------------------------------------
# Rutas de datos
# ---------------------------------------------------------------------------
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

HISTORY_FILE = Path("history.json")


# ---------------------------------------------------------------------------
# Handler: recepción de imágenes
# ---------------------------------------------------------------------------
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Se dispara cuando el bot recibe un mensaje con foto en el grupo.
    Descarga la imagen, extrae los datos del comprobante con IA y
    publica el resultado formateado en el grupo. Limpia el archivo temporal.
    """
    message = update.effective_message
    chat = update.effective_chat

    # Tomamos la foto de mayor resolución disponible (último elemento de la lista)
    photo = message.photo[-1]
    file_id = photo.file_id

    logger.info(
        "📷 Imagen recibida | chat_id=%s | file_id=%s", chat.id, file_id
    )

    # --- 1. Descargar imagen al directorio temporal ---
    dest_path = TEMP_DIR / f"{file_id}.jpg"
    try:
        telegram_file = await context.bot.get_file(file_id)
        await telegram_file.download_to_drive(dest_path)
        logger.info("✅ Imagen guardada en: %s", dest_path)
    except Exception as exc:
        logger.exception("❌ Error al descargar la imagen de Telegram")
        await message.reply_text(
            "⚠️ No pude descargar la imagen. Por favor, inténtalo de nuevo."
        )
        return

    # --- 2. Extraer datos del comprobante con IA ---
    try:
        data = await extract_receipt_data(dest_path)
    except Exception as exc:
        logger.exception("❌ Error inesperado al llamar a extract_receipt_data")
        data = {"error": f"Error inesperado: {exc}"}
    finally:
        # --- 3. Borrar imagen temporal (siempre, sin importar el resultado) ---
        try:
            dest_path.unlink(missing_ok=True)
            logger.info("🗑️  Imagen temporal eliminada: %s", dest_path)
        except Exception:
            logger.warning("⚠️  No se pudo eliminar el archivo temporal: %s", dest_path)

    # --- 4. Publicar resultado en el grupo ---
    if "error" in data:
        logger.warning("⚠️  La IA devolvió un error: %s", data["error"])
        await message.reply_text(
            "⚠️ No pude extraer los datos del comprobante.\n"
            "Asegúrate de que la imagen sea un recibo de pago legible."
        )
        return

    datos_json = json.dumps(data, ensure_ascii=False, indent=2)

    # --- 5. Persistir en history.json para el dashboard ---
    _append_to_history(data)

    response_text = (
        f"#REGISTRO_PAGO\n"
        f"json\n"
        f"{datos_json}\n"
    )

    await message.reply_text(response_text)
    logger.info("📨 Datos enviados al grupo chat_id=%s", chat.id)


# ---------------------------------------------------------------------------
# Handler: documentos (imagen enviada como archivo = calidad original)
# ---------------------------------------------------------------------------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Acepta imágenes enviadas como documento (sin compresión JPEG de Telegram).
    Preserva la calidad original de la captura de pantalla.
    """
    message = update.effective_message
    doc = message.document

    # Solo procesamos archivos que sean imágenes
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        return

    logger.info("📎 Documento-imagen recibido | mime=%s | file_id=%s", doc.mime_type, doc.file_id)

    # Inferir extensión según mime_type
    ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    ext = ext_map.get(doc.mime_type, ".jpg")
    dest_path = TEMP_DIR / f"{doc.file_id}{ext}"

    try:
        telegram_file = await context.bot.get_file(doc.file_id)
        await telegram_file.download_to_drive(dest_path)
        logger.info("✅ Documento guardado en: %s", dest_path)
    except Exception:
        logger.exception("❌ Error al descargar el documento")
        await message.reply_text("⚠️ No pude descargar la imagen. Inténtalo de nuevo.")
        return

    try:
        data = await extract_receipt_data(dest_path)
    except Exception as exc:
        logger.exception("❌ Error en extract_receipt_data (documento)")
        data = {"error": f"Error inesperado: {exc}"}
    finally:
        dest_path.unlink(missing_ok=True)
        logger.info("🗑️  Archivo temporal eliminado: %s", dest_path)

    if "error" in data:
        await message.reply_text(
            "⚠️ No pude extraer los datos del comprobante.\n"
            "Asegúrate de que la imagen muestre claramente el monto y operación."
        )
        return

    datos_json = json.dumps(data, ensure_ascii=False, indent=2)
    _append_to_history(data)
    await message.reply_text(f"#REGISTRO_PAGO\njson\n{datos_json}\n")
    logger.info("📨 Datos de documento enviados al grupo chat_id=%s", message.chat.id)


# ---------------------------------------------------------------------------
# Handler: comando /dashboard — abre el Mini App de Telegram
# ---------------------------------------------------------------------------
async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Responde con un botón que abre el dashboard como Telegram Mini App.
    Requiere WEBAPP_URL en el .env apuntando a una URL pública HTTPS.
    """
    if not WEBAPP_URL:
        await update.effective_message.reply_text(
            "⚠️ El dashboard no está configurado.\n"
            "Agrega WEBAPP_URL en el archivo .env con la URL pública de tu servidor."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="📊 Ver Dashboard de Pagos",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])

    await update.effective_message.reply_text(
        "📊 *Dashboard de Pagos*\n"
        "Historial completo de comprobantes registrados:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    logger.info("🔗 Dashboard Mini App enviado a chat_id=%s", update.effective_chat.id)


# ---------------------------------------------------------------------------
# Persistencia local
# ---------------------------------------------------------------------------
def _append_to_history(record: dict) -> None:
    """
    Agrega un registro de pago al archivo history.json.
    Si el archivo no existe, lo crea. Si el numero_operacion ya
    existe no duplica el registro.
    """
    try:
        history: list[dict] = []
        if HISTORY_FILE.exists():
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []

        # Evitar duplicados por numero_operacion
        op = record.get("numero_operacion")
        if op and any(r.get("numero_operacion") == op for r in history):
            logger.info("⏭️  Operación %s ya registrada, se omite duplicado.", op)
            return

        history.append(record)

        # Guardar con formato legible y asegurar encoding UTF-8
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
            
        logger.info("💾 Registro guardado en history.json (total: %d)", len(history))
    except Exception:
        logger.exception("❌ No se pudo guardar en history.json")


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------
def main() -> None:
    """Inicializa y arranca el bot en modo polling."""
    logger.info("🚀 Iniciando bot...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Fotos enviadas normalmente (Telegram las comprime a JPEG)
    app.add_handler(
        MessageHandler(
            filters.PHOTO & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            handle_image,
        )
    )

    # Imágenes enviadas como archivo (calidad original, sin compresión)
    app.add_handler(
        MessageHandler(
            filters.Document.IMAGE & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            handle_document,
        )
    )

    # Comando /dashboard en grupos o chats privados
    app.add_handler(CommandHandler("dashboard", dashboard_command))

    logger.info("🤖 Bot activo. Esperando mensajes...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
