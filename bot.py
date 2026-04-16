"""
Bot de Telegram - Autocontador (Versión Local)
Extrae datos con IA, guarda localmente y muestra dashboard en texto.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
MODEL = os.getenv("MODEL_NAME", "google/gemini-2.0-flash-001")


if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Falta configurar TELEGRAM_TOKEN u OPENROUTER_API_KEY en el archivo .env")

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)
PAGOS_FILE = Path("pagos.json")

# ---------------------------------------------------------------------------
# Helpers JSON y Procesamiento
# ---------------------------------------------------------------------------

def load_pagos() -> dict:
    if not PAGOS_FILE.exists():
        return {}
    try:
        data = json.loads(PAGOS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def save_pagos(data: dict) -> None:
    try:
        PAGOS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Error guardando datos: %s", e)


def _calculate_hash(file_path: Path) -> str:
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha.update(chunk)
    return sha.hexdigest()


def is_duplicate(user_id: str, image_hash: str, numero_operacion: str) -> bool:
    data = load_pagos()
    user_records = data.get(user_id, [])
    
    for r in user_records:
        if image_hash and r.get("image_hash") == image_hash:
            return True
        if numero_operacion and str(r.get("numero_operacion", "")).strip().lower() == str(numero_operacion).strip().lower():
            if str(numero_operacion).strip().lower() not in ["", "null", "none"]:
                return True
    return False


def escape_markdown(text: str) -> str:
    """Escapa caracteres especiales para MarkdownV2"""
    characters = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    text = str(text)
    for char in characters:
        text = text.replace(char, f'\\{char}')
    return text

# ---------------------------------------------------------------------------
# OpenRouter API Logic
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
Eres un experto leyendo comprobantes de pago peruanos (Yape, Plin, BCP, BBVA, etc.) o transferencias internacionales (Binance, Lemon).
EXTRAE:
  - emisor: banco o app DESDE la cual se envía el dinero.
  - pagador: nombre completo.
  - monto: SOLO el número decimal.
  - numero_operacion: código o número único de transacción.
  - fecha: YYYY-MM-DD.

Si un campo no es legible, usa null. Responde SOLAMENTE con el JSON. Ejemplo:
{"emisor": "Yape", "pagador": "Juan Perez", "monto": 25.50, "numero_operacion": "12345", "fecha": "2024-04-16"}
"""

def process_receipt_with_ai(image_path: Path) -> dict:
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    
    mime_type = "image/jpeg"
    if image_path.suffix.lower() == ".png": 
        mime_type = "image/png"
    
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": SYSTEM_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{img_b64}"}}
                ]
            }
        ],
        "temperature": 0
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    resp = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    content = result["choices"][0]["message"]["content"].strip()
    
    # Extraer el JSON
    match = re.search(r"\{.*?\}", content, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(content)


# ---------------------------------------------------------------------------
# Handlers del Bot
# ---------------------------------------------------------------------------

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id = str(message.from_user.id)
    
    # Obtener archivo
    if message.photo:
        file_id = message.photo[-1].file_id
        ext = ".jpg"
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        file_id = message.document.file_id
        ext = ".jpg" if "jpeg" in message.document.mime_type else ".png"
    else:
        return
        
    status_msg = await message.reply_text("⏳ Procesando...")

    dest_path = TEMP_DIR / f"{file_id}{ext}"
    try:
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(dest_path)
        
        img_hash = _calculate_hash(dest_path)
        if is_duplicate(user_id, img_hash, ""):
            await status_msg.edit_text("⚠️ Este comprobante ya ha sido registrado antes (imagen duplicada).")
            return
            
        # Llamar a requests en un hilo separado para no bloquear el loop de Telegram
        data = await asyncio.to_thread(process_receipt_with_ai, dest_path)
        op = data.get("numero_operacion")
        
        if op and is_duplicate(user_id, "", op):
            await status_msg.edit_text(f"⚠️ El recibo con N° Operación: {op} ya fue registrado antes.")
            return
            
        # Añadir Hash y Guardar
        data["image_hash"] = img_hash
        pagos = load_pagos()
        if user_id not in pagos:
            pagos[user_id] = []
        pagos[user_id].append(data)
        save_pagos(pagos)
        
        await status_msg.edit_text("✅ Pago registrado con éxito. Usa /dashboard para ver tu resumen.")
            
    except Exception:
        logger.exception("Error al procesar.")
        await status_msg.edit_text("❌ Ocurrió un error al procesar el comprobante. Verifica que la imagen sea legible.")
    finally:
        dest_path.unlink(missing_ok=True)


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_message.from_user.id)
    pagos = load_pagos()
    user_records = pagos.get(user_id, [])
    
    if not user_records:
        await update.effective_message.reply_text("📊 Aún no tienes comprobantes registrados.")
        return

    totales_por_emisor = {}
    gran_total = 0.0
    
    for r in user_records:
        emisor = r.get("emisor")
        emisor = str(emisor).strip().title() if emisor and str(emisor) != "null" else "Desconocido"
        try:
            monto = float(r.get("monto", 0))
        except (ValueError, TypeError):
            monto = 0.0
            
        totales_por_emisor[emisor] = totales_por_emisor.get(emisor, 0.0) + monto
        gran_total += monto
        
    num_tx = len(user_records)
    
    # Construir MarkdownV2
    texto_md = "*📊 DASHBOARD DE PAGOS*\n"
    texto_md += "━━━━━━━━━━━━━━━━━━━━━━\n"
    texto_md += f"📝 *Total de Transacciones:* {num_tx}\n\n"
    texto_md += "*Resumen por Banco/App:*\n"
    
    for emisor, total in sorted(totales_por_emisor.items(), key=lambda x: x[1], reverse=True):
        texto_md += f"🔹 {escape_markdown(emisor)}: S/ `{total:,.2f}`\n"
        
    texto_md += "━━━━━━━━━━━━━━━━━━━━━━\n"
    texto_md += f"💰 *GRAN TOTAL:* S/ `{gran_total:,.2f}`\n"
    
    await update.effective_message.reply_text(texto_md, parse_mode=ParseMode.MARKDOWN_V2)

def main() -> None:
    logger.info("🚀 Iniciando Autocontador Local...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    
    # Escucha imágenes en cualquier grupo o chat privado
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_media))
    
    logger.info("🤖 Bot activo...")
    app.run_polling()

if __name__ == "__main__":
    main()
