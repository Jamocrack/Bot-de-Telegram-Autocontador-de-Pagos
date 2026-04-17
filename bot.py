"""
Bot de Telegram - Autocontador (Versión Local)
Extrae datos con IA, guarda localmente y muestra dashboard en texto.
"""

import asyncio
import base64
import csv
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from utils import get_exchange_rate, format_progress_bar

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
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
OPENROUTER_API_KEY:str = os.getenv("OPENROUTER_API_KEY", "")
MODEL = os.getenv("MODEL_NAME", "google/gemini-2.0-flash-001")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
_auth_str = os.getenv("AUTHORIZED_CHATS", "")
AUTHORIZED_CHATS = [c.strip() for c in _auth_str.split(",") if c.strip()]


if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Falta configurar TELEGRAM_TOKEN u OPENROUTER_API_KEY en el archivo .env")

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)
PAGOS_FILE = Path("pagos.json")
SETTINGS_FILE = Path("settings.json")

# ---------------------------------------------------------------------------
# Helpers JSON y Procesamiento
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    if not SETTINGS_FILE.exists(): return {"is_active": True}
    try: return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except: return {"is_active": True}

def save_settings(data: dict) -> None:
    try: SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass

def is_bot_active(user_id: str) -> bool:
    if ADMIN_USER_ID and str(user_id) == str(ADMIN_USER_ID):
        return True
    return load_settings().get("is_active", True)

async def manage_disabled_warning(chat_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el mensaje de advertencia 'bot desactivado' borrando el anterior y mandándolo al último."""
    prev_msg_id = context.bot_data.get(f"disabled_msg_{chat_id}")
    if prev_msg_id:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=prev_msg_id)
        except: pass
    msg = await context.bot.send_message(
        chat_id=chat_id, 
        text="⛔ *El Bot se encuentra DESACTIVADO por el administrador\\.*\n_Ningún comprobante, mensaje o comando será procesado hasta nuevo aviso\\._", 
        parse_mode=ParseMode.MARKDOWN_V2
    )
    context.bot_data[f"disabled_msg_{chat_id}"] = msg.message_id

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
Eres un experto en OCR y lectura de comprobantes de pago digitales peruanos (Yape, Plin, BCP, BBVA, Interbank, etc.) y billeteras cripto (Binance, Lemon).
Tu tarea es EXTRAER exactamente estos campos en formato JSON:

  - emisor: nombre de la App o Banco (Yape, Plin, PayPal, Lemon, Binance, Interbank, etc.).
  - pagador: nombre completo del emisor.
  - monto: valor numérico decimal.
  - moneda: nombre o símbolo de la moneda (Soles, Dólares, Euros, USDT, etc.).
  - pais: país de origen de la transacción.
  - numero_operacion: ID único o código de seguimiento.
  - fecha: YYYY-MM-DD.
  - hora: HH:MM:SS.
  - destino: Nombre del receptor o cuenta de destino.
  - categoria: Tipo de gasto/ingreso (Ventas, Servicios, Remesa, etc.).
  - referencia: Concepto o nota que acompaña al pago (si existe).

REGLAS:
1. Responde ÚNICAMENTE con JSON.
2. Si un campo no es visible, usa null.
3. Para PayPal/Binance, extrae el ID de transacción con precisión.
4. Detecta el PAÍS basándote en el formato del comprobante.

Ejemplo:
{"emisor": "PayPal", "pagador": "John Doe", "monto": 100.00, "moneda": "Dólares", "pais": "USA", "numero_operacion": "TX99281", "fecha": "2024-04-16", "hora": "15:30:22", "destino": "Vendedor XYZ", "categoria": "Servicios", "referencia": "Pago de Invoice #12"}
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
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    user_caption = message.caption if message.caption else ""
    
    # Validar Autorización de Chat
    if not is_private and chat_id not in AUTHORIZED_CHATS:
        # Si es un grupo no autorizado, ignorar o avisar una vez
        logger.warning(f"Intento de uso en chat no autorizado: {chat_id}")
        return

    # Obtener archivo
    if message.photo:
        file_id = message.photo[-1].file_id
        ext = ".jpg"
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        file_id = message.document.file_id
        ext = ".jpg" if "jpeg" in message.document.mime_type else ".png"
    else:
        return
        
    if not is_bot_active(user_id):
        if not is_private:
            try: await message.delete()
            except Exception: pass
            await manage_disabled_warning(chat_id, context)
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*\\.", parse_mode=ParseMode.MARKDOWN_V2)
            context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return
        
    if is_private:
        status_msg = await message.reply_text(f"`{format_progress_bar(10, 100)}` 📥 Descargando...")
    else:
        status_msg = await context.bot.send_message(chat_id=chat_id, text=f"`{format_progress_bar(10, 100)}` 📥 Descargando...", parse_mode=ParseMode.MARKDOWN_V2)
        try:
            await message.delete()
        except Exception:
            logger.warning(f"No se pudo borrar el mensaje original en {chat_id}.")

    dest_path = TEMP_DIR / f"{file_id}{ext}"
    try:
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(dest_path)
        
        await status_msg.edit_text(f"`{format_progress_bar(40, 100)}` 🔍 Analizando huella...")
        img_hash = _calculate_hash(dest_path)
        if is_duplicate(user_id, img_hash, ""):
            await status_msg.edit_text("⚠️ Este comprobante ya existe en tu historial.")
            if not is_private:
                context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=status_msg.message_id)
            return
            
        await status_msg.edit_text(f"`{format_progress_bar(70, 100)}` 🧠 Consultando IA...")
        data = await asyncio.to_thread(process_receipt_with_ai, dest_path)
        
        if user_caption:
            current_ref = str(data.get("referencia", "")).strip()
            if current_ref and current_ref.lower() != "null":
                data["referencia"] = f"{user_caption} (Nota IA: {current_ref})"
            else:
                data["referencia"] = user_caption
        
        await status_msg.edit_text(f"`{format_progress_bar(90, 100)}` 💾 Guardando datos...")
        
        op_original = str(data.get("numero_operacion", "")).strip()
        if not op_original or op_original.lower() in ["null", "s/n", "none", ""]:
            op = "SYS-" + str(uuid.uuid4()).upper()[:6]
            data["numero_operacion"] = op
        else:
            op = op_original
            
        if op_original and is_duplicate(user_id, "", op_original):
            await status_msg.edit_text(f"⚠️ El N° Operación: {op_original} ya fue registrado antes.")
            if not is_private:
                context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=status_msg.message_id)
            return
            
        monto = float(data.get("monto", 0) or 0)
        moneda = data.get("moneda", "Soles")
        if moneda == "Dólares":
            rate = await asyncio.to_thread(get_exchange_rate)
            data["monto_original"] = f"{monto} USD"
            data["monto"] = round(monto * rate, 2)
            data["tipo_cambio"] = rate
            
        data["image_hash"] = img_hash
        data["file_id"] = file_id
        data["_username"] = update.effective_user.username or "S/N"
        
        pagos = load_pagos()
        if user_id not in pagos:
            pagos[user_id] = []
        pagos[user_id].append(data)
        save_pagos(pagos)
        
        ticket = _generate_ticket(data)
        
        keyboard = [
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="dash_menu"),
                InlineKeyboardButton("🗑️ Borrar Este", callback_data="dash_delete_conf")
            ]
        ]
        
        await status_msg.delete()
        if is_private:
            reply_msg = await context.bot.send_message(chat_id=chat_id, text=ticket, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            ticket += "\n\n⏳ _Este mensaje se auto\\-destruirá en 10 segundos por privacidad\\._"
            reply_msg = await context.bot.send_message(chat_id=chat_id, text=ticket, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
            context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=reply_msg.message_id)
            
    except Exception:
        logger.exception("Error al procesar.")
        err_msg = await status_msg.edit_text("❌ Error al procesar. Verifica que la imagen sea legible.")
        if not is_private:
            context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=err_msg.message_id)
    finally:
        dest_path.unlink(missing_ok=True)


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Borra un mensaje programado (usado para limpiar el grupo)"""
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except:
        pass


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el menú principal del Dashboard interactivo"""
    query = update.callback_query
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_bot_active(user_id):
        if not is_private:
            try: await update.message.delete()
            except Exception: pass
            await manage_disabled_warning(chat_id, context)
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*\\.", parse_mode=ParseMode.MARKDOWN_V2)
            context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return

    keyboard = [
        [
            InlineKeyboardButton("📊 Resumen", callback_data="dash_resumen"),
            InlineKeyboardButton("📅 Stats", callback_data="dash_stats_date"),
        ],
        [
            InlineKeyboardButton("🕒 Últimos 10", callback_data="dash_recientes"),
            InlineKeyboardButton("🔍 Buscar Pago", callback_data="dash_search_info"),
        ],
        [
            InlineKeyboardButton("📁 Exportar Datos", callback_data="dash_export_menu"),
            InlineKeyboardButton("🖼️ Ver Recibo", callback_data="dash_ver_recibo_menu"),
        ],
        [
            InlineKeyboardButton("❌ Cerrar", callback_data="dash_close")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    txt = (
        "🛠️ *CENTRO DE CONTROL DE PAGOS*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Selecciona una opción para gestionar tus registros personales\\."
    )
    if not is_private:
        txt += "\n\n⏳ _Este menú se auto\\-destruirá pronto por privacidad\\._"

    if query:
        await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
    else:
        msg = await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
        if not is_private:
            context.job_queue.run_once(_delete_message_job, 30, chat_id=chat_id, data=msg.message_id)
            try:
                await update.message.delete()
            except Exception: pass


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja las interacciones del dashboard"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    data = query.data
    pagos = load_pagos()
    user_records = pagos.get(user_id, [])

    if not user_records and data != "dash_help":
        await query.edit_message_text("📊 Aún no tienes datos registrados. ¡Envíame una foto para empezar!")
        return

    if data == "dash_resumen":
        await _show_resumen(query, user_records)
    elif data == "dash_recientes":
        await _show_recientes(query, user_records)
    elif data == "dash_export_menu":
        await _show_export_menu(query)
    elif data == "dash_export_all":
        await _export_to_csv(query, context, user_records, "todo")
    elif data == "dash_export_month":
        await _export_to_csv(query, context, user_records, "mes")
    elif data == "dash_stats_date":
        await _show_stats_date_menu(query)
    elif data == "dash_stats_list":
        await _show_stats_detailed(query, user_records)
    elif data == "dash_ver_recibo_menu":
        from telegram import ForceReply
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🖼️ *BUSCADOR DE RECIBOS*\nResponde a este mensaje con el NÚMERO DE OPERACIÓN del recibo que quieres ver:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=ForceReply(selective=True)
        )
    elif data == "dash_search_info":
        from telegram import ForceReply
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🔍 *BUSCAR PAGO*\nResponde a este mensaje escribiendo el Nombre o Monto que deseas buscar:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=ForceReply(selective=True)
        )
    elif data == "dash_delete_conf":
        await _show_delete_confirmation(query)
    elif data == "dash_delete_now":
        await _delete_last_record(query, user_id)
    elif data == "dash_admin_global":
        await _show_admin_global(query)
    elif data == "dash_admin_search_info":
        if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
        from telegram import ForceReply
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="👑 *BÚSQUEDA GLOBAL*\nResponde a este mensaje con la palabra, monto o nombre que buscas:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=ForceReply(selective=True)
        )
    elif data == "dash_admin_toggle_status":
        if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
        settings = load_settings()
        new_status = not settings.get("is_active", True)
        settings["is_active"] = new_status
        save_settings(settings)
        
        status_txt = "🟢 ACTIVADO" if new_status else "🔴 DESACTIVADO"
        bot_msg = "ACTIVADO" if new_status else "DESACTIVADO"
        
        for c_id in AUTHORIZED_CHATS:
            last_msg_id = context.bot_data.get(f"disabled_msg_{c_id}")
            if last_msg_id:
                try: await context.bot.delete_message(chat_id=c_id, message_id=last_msg_id)
                except: pass
                context.bot_data.pop(f"disabled_msg_{c_id}", None)
                
            try:
                ann = await context.bot.send_message(chat_id=c_id, text=f"📢 *AVISO DEL ADMINISTRADOR*\n━━━━━━━━━━━━━━━━━━━━━━\nEl bot se encuentra *{bot_msg}*\\.", parse_mode=ParseMode.MARKDOWN_V2)
                context.job_queue.run_once(_delete_message_job, 30, chat_id=c_id, data=ann.message_id)
            except Exception: pass
            
        keyboard = [
            [
                InlineKeyboardButton("🌍 REPORTE GLOBAL", callback_data="dash_admin_global"),
                InlineKeyboardButton("👑 Búsqueda Global", callback_data="dash_admin_search_info")
            ],
            [
                InlineKeyboardButton(f"🕹️ Estado: {status_txt}", callback_data="dash_admin_toggle_status")
            ],
            [
                InlineKeyboardButton("❌ Cerrar", callback_data="dash_close")
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "dash_admin_menu":
        if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
        settings = load_settings()
        is_active = settings.get("is_active", True)
        status_emoji = "🟢 ACTIVADO" if is_active else "🔴 DESACTIVADO"
        keyboard = [
            [
                InlineKeyboardButton("🌍 REPORTE GLOBAL", callback_data="dash_admin_global"),
                InlineKeyboardButton("👑 Búsqueda Global", callback_data="dash_admin_search_info")
            ],
            [
                InlineKeyboardButton(f"🕹️ Estado: {status_emoji}", callback_data="dash_admin_toggle_status")
            ],
            [
                InlineKeyboardButton("❌ Cerrar", callback_data="dash_close")
            ]
        ]
        txt = "👑 *PANEL EXCLUSIVO DE ADMINISTRADOR*\n━━━━━━━━━━━━━━━━━━━━━━\nConfigura el bot y revisa todo:"
        await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "dash_close":
        try: await query.message.delete()
        except: pass
        return
    elif data == "dash_help":
        await start_command(update, context)
    elif data == "dash_menu":
        await dashboard_command(update, context) # Edita el mensaje actual sin enviar uno nuevo
    elif data == "dash_commands":
        await commands_command(update, context) # Regresa al Menú de Comandos
    elif data == "dash_back_cat":
        await _show_categories_menu(query)
        try:
            await query.delete_message()
        except:
            pass


async def _show_export_menu(query):
    keyboard = [
        [InlineKeyboardButton("📄 Todo el historial (CSV)", callback_data="dash_export_all")],
        [InlineKeyboardButton("📅 Solo este mes (CSV)", callback_data="dash_export_month")],
        [InlineKeyboardButton("🔙 Volver", callback_data="dash_menu")]
    ]
    txt = "*📁 OPCIONES DE EXPORTACIÓN*\n━━━━━━━━━━━━━━━━━━━━━━\nSelecciona el rango de datos que deseas descargar en formato CSV compatible con Excel:"
    await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


async def _export_to_csv(query, context, user_records, mode):
    import io
    
    if mode == "mes":
        now = datetime.now()
        current_period = now.strftime("%Y-%m")
        filtered = [r for r in user_records if str(r.get("fecha", "")).startswith(current_period)]
        filename = f"reporte_{current_period}.csv"
    else:
        filtered = user_records
        filename = "historial_completo.csv"

    if not filtered:
        await query.answer("⚠️ No hay datos para el periodo seleccionado.", show_alert=True)
        return

    output = io.StringIO()
    fields = ["fecha", "hora", "pais", "emisor", "pagador", "monto", "moneda", "numero_operacion", "destino", "categoria", "referencia"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    
    for r in filtered:
        # Limpiar dict para el CSV
        row = {k: r.get(k, "") for k in fields}
        writer.writerow(row)
    
    bio = io.BytesIO(output.getvalue().encode('utf-8-sig')) # utf-8-sig para Excel
    bio.name = filename
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=bio,
        caption=f"✅ Aquí tienes tu reporte: {filename}"
    )
    await query.answer("Enviando reporte...")


async def _show_stats_date_menu(query):
    keyboard = [
        [InlineKeyboardButton("📊 Ver por Meses", callback_data="dash_stats_list")],
        [InlineKeyboardButton("🔙 Volver", callback_data="dash_menu")]
    ]
    txt = "*📅 ESTADÍSTICAS TEMPORALES*\n━━━━━━━━━━━━━━━━━━━━━━\nAnaliza tus finanzas por periodos de tiempo:"
    await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_stats_detailed(query, user_records):
    # Agrupar por Mes
    stats = {}
    for r in user_records:
        fecha = r.get("fecha")
        if not fecha or fecha == "null": continue
        mes = fecha[:7] # YYYY-MM
        stats[mes] = stats.get(mes, 0.0) + float(r.get("monto", 0) or 0)
    
    txt = "*📊 INGRESOS POR MES*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for mes, total in sorted(stats.items(), reverse=True):
        txt += f"📅 *{escape_markdown(mes)}*: S/ `{total:,.2f}`\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data="dash_stats_date")]]
    await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


async def buscar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Busca en los registros del usuario"""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_bot_active(user_id):
        if not is_private:
            try: await update.message.delete()
            except: pass
        msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*.", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return

    if not is_private:
        try: await update.message.delete()
        except: pass

    if not context.args:
        msg = await update.effective_message.reply_text("💡 Uso: `/buscar palabra` o `/buscar monto`", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=msg.message_id)
        return
        
    term = " ".join(context.args).lower()
    user_id = str(update.effective_user.id)
    pagos = load_pagos()
    user_records = pagos.get(user_id, [])
    
    results = []
    for r in user_records:
        match = False
        if term in str(r.get("pagador", "")).lower(): match = True
        if term in str(r.get("monto", "")).lower(): match = True
        if term in str(r.get("emisor", "")).lower(): match = True
        if term in str(r.get("numero_operacion", "")).lower(): match = True
        if match:
            results.append(r)
            
    if not results:
        msg = await update.effective_message.reply_text(f"❌ No encontré nada para: `{escape_markdown(term)}`", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=msg.message_id)
        return
        
    txt = f"🔍 *RESULTADOS PARA: {escape_markdown(term)}*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for r in results[-10:]: # Top 10 matches
        p = escape_markdown(r.get("pagador", "S/N"))
        m = float(r.get("monto", 0) or 0)
        e = escape_markdown(r.get("emisor", "S/E"))
        op = escape_markdown(r.get("numero_operacion", "S/N"))
        txt += f"👤 *{p}* \\({e}\\)\n└ S/ `{m:,.2f}` \\| Op: `{op}`\n"
        
    if not is_private:
        txt += "\n⏳ _Se destruirá en 20s_"
        
    msg = await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2)
    if not is_private: context.job_queue.run_once(_delete_message_job, 20, chat_id=chat_id, data=msg.message_id)


async def _show_delete_confirmation(query):
    keyboard = [
        [InlineKeyboardButton("✅ SÍ, borrar último", callback_data="dash_delete_now")],
        [InlineKeyboardButton("❌ NO, cancelar", callback_data="dash_menu")]
    ]
    txt = "⚠️ *¿Deseas eliminar el último pago registrado?*\nEsta acción no se puede deshacer\\."
    await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


async def _delete_last_record(query, user_id):
    pagos = load_pagos()
    if user_id in pagos and pagos[user_id]:
        eliminado = pagos[user_id].pop()
        save_pagos(pagos)
        await query.answer(f"✅ Registro de S/ {eliminado.get('monto')} eliminado.", show_alert=True)
    else:
        await query.answer("❌ No hay nada que eliminar.", show_alert=True)
    await dashboard_command(query, None)


async def _show_resumen(query, user_records):
    totales = {}
    total_total = 0.0
    for r in user_records:
        emisor = str(r.get("emisor", "Otros")).strip().title()
        monto = float(r.get("monto", 0) or 0)
        totales[emisor] = totales.get(emisor, 0.0) + monto
        total_total += monto

    txt = "*📊 RESUMEN POR EMISOR*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for emisor, monto in sorted(totales.items(), key=lambda x: x[1], reverse=True):
        txt += f"🔸 {escape_markdown(emisor)}: S/ `{monto:,.2f}`\n"
    
    txt += "━━━━━━━━━━━━━━━━━━━━━━\n"
    txt += f"💰 *GRAN TOTAL:* S/ `{total_total:,.2f}`\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data="dash_menu")]]
    await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_recientes(query, user_records):
    recientes = user_records[-10:][::-1]
    txt = "*🕒 ÚLTIMOS 10 MOVIMIENTOS*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    
    for i, r in enumerate(recientes, 1):
        emisor = escape_markdown(r.get("emisor", "Desconocido"))
        monto = float(r.get("monto", 0) or 0)
        simbolo = "S/" if str(r.get("moneda")).lower() in ["soles", "pen"] else "$"
        txt += f"{i}\\. *{emisor}* \\- {escape_markdown(r.get('fecha',''))}\n└ {simbolo} `{monto:,.2f}`\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data="dash_menu")]]
    await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


async def _export_data(query, context, user_records):
    # Función obsoleta, reemplazada por _export_to_csv
    pass


async def restart_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.data == "dash_menu":
        await dashboard_command(update, context)


async def _show_admin_global(query):
    """Muestra estadísticas consolidadas de todos los usuarios (Solo Admin) Sustituye el ID manual"""
    pagos = load_pagos()
    total_general = 0.0
    total_registros = 0
    usuarios_activos = len(pagos.keys())
    
    for u_id, records in pagos.items():
        total_registros += len(records)
        total_general += sum(float(r.get("monto", 0) or 0) for r in records)
        
    txt = (
        "👑 *PANEL DE CONTROL GLOBAL*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 *Usuarios Activos:* {usuarios_activos}\n"
        f"📝 *Registros Totales:* {total_registros}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *RECAUDACIÓN TOTAL:* S/ `{total_general:,.2f}`\n\n"
        "💡 _Este reporte es privado y visible solo para el dueño del bot\\._"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Volver al Admin", callback_data="dash_admin_menu")]]
    await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /start y /help"""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_bot_active(user_id):
        if not is_private:
            try: await update.message.delete()
            except Exception: pass
            await manage_disabled_warning(chat_id, context)
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*\\.", parse_mode=ParseMode.MARKDOWN_V2)
            context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return

    user_name = update.effective_user.first_name
    help_text = (
        f"👋 ¡Hola {user_name}! Soy tu asistente de Autocontador\\.\n\n"
        "*¿Cómo funciono?*\n"
        "1\\. Envíame una **foto o captura** de un comprobante de pago \\(Yape, Plin, Transferencia, etc\\.\\)\\.\n"
        "2\\. Analizaré la imagen y guardaré los datos automáticamente\\.\n\n"
        "*Comandos disponibles:*\n"
        "🚀 /start \\- Ver este mensaje de ayuda\\.\n"
        "📊 /dashboard \\- Ver tu resumen total de pagos acumulados\\.\n\n"
        "💡 _Tip: Puedes enviarme las fotos en chats privados o en grupos donde esté presente\\._"
    )
    await update.effective_message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)


async def commands_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el menú interactivo de comandos y opciones"""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_bot_active(user_id):
        if not is_private:
            try: await update.message.delete()
            except Exception: pass
            await manage_disabled_warning(chat_id, context)
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*\\.", parse_mode=ParseMode.MARKDOWN_V2)
            context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return
    keyboard = [
        [
            InlineKeyboardButton("📈 Reportes y Stats", callback_data="dash_stats_date"),
            InlineKeyboardButton("🔍 Búsqueda IA", callback_data="dash_search_info"),
        ],
        [
            InlineKeyboardButton("⚙️ Gestión y Datos", callback_data="dash_export_menu"),
            InlineKeyboardButton("🛒 Dashboard", callback_data="dash_menu"),
        ]
    ]
    txt = (
        "🎮 *CENTRO DE COMANDOS INTERACTIVO*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Selecciona una categoría para explorar las capacidades del bot:"
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


def _generate_ticket(data: dict) -> str:
    """Genera una ficha visual tipo ticket para el recibo procesado."""
    e = escape_markdown(data.get("emisor", "DESCONOCIDO"))
    p = escape_markdown(data.get("pagador", "S/N"))
    m = float(data.get("monto", 0) or 0)
    op = escape_markdown(data.get("numero_operacion", "null"))
    cat = escape_markdown(data.get("categoria", "Otros"))
    f = escape_markdown(data.get("fecha", ""))
    h = escape_markdown(data.get("hora", ""))
    dest = escape_markdown(data.get("destino", "N/D"))
    pa = escape_markdown(data.get("pais", "N/D"))
    ref = escape_markdown(data.get("referencia", ""))
    mon = escape_markdown(data.get("moneda", "Soles"))
    
    ticket = (
        "✅ *PAGO REGISTRADO EXITOSAMENTE*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 *País:* {pa} | 🏷️ {cat}\n"
        f"🏦 *Entidad:* {e}\n"
        f"👤 *Emisor:* {p}\n"
        f"🎯 *Destino:* {dest}\n"
        f"💰 *Monto:* `{mon}` `{m:,.2f}`\n"
    )
    
    if ref and ref != "null":
        ticket += f"📝 *Ref:* {ref}\n"
    
    if "monto_original" in data:
        ticket += f"💱 _Equivalente a {escape_markdown(data['monto_original'])}_ \n"
    
    ticket += (
        f"🆔 *ID:* `{op}`\n"
        f"📅 *Fecha:* {f} {h}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ _Usa /dashboard para ver el total_"
    )
    return ticket


async def consultar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Analiza los pagos con lenguaje natural simple"""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_bot_active(user_id):
        if not is_private:
            try: await update.message.delete()
            except: pass
        msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*.", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return

    if not context.args:
        await update.effective_message.reply_text("💡 Uso: `/consultar Maria` o `/consultar Ventas`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    term = " ".join(context.args).lower()
    user_id = str(update.effective_message.from_user.id)
    pagos = load_pagos()
    user_records = pagos.get(user_id, [])
    
    matches = [r for r in user_records if term in str(r.get("pagador", "")).lower() or term in str(r.get("categoria", "")).lower()]
    
    if not matches:
        await update.effective_message.reply_text(f"❌ No encontré pagos para '{escape_markdown(term)}'", parse_mode=ParseMode.MARKDOWN_V2)
        return
        
    total = sum(float(r.get("monto", 0) or 0) for r in matches)
    txt = f"🔍 *REPORTE PARA: {escape_markdown(term.title())}*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    txt += f"📈 *Monto acumulado:* S/ `{total:,.2f}`\n"
    txt += f"📝 *Total de registros:* {len(matches)}\n"
    txt += "━━━━━━━━━━━━━━━━━━━━━━\n"
    txt += "💡 _Usa /buscar para ver la lista completa_"
    
    await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2)


async def recibo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envía la imagen del recibo según el número de operación."""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_bot_active(user_id):
        if not is_private:
            try: await update.message.delete()
            except: pass
        msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*.", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return

    if not is_private:
        try:
            await update.message.delete()
        except Exception: pass
        
    if not context.args:
        msg = await update.effective_message.reply_text("💡 Uso: `/recibo NUMERO_OPERACION`", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=msg.message_id)
        return
        
    term = context.args[0].lower()
    user_id = str(update.effective_user.id)
    pagos = load_pagos()
    user_records = pagos.get(user_id, [])
    
    found_file_id = None
    if ADMIN_USER_ID and user_id == str(ADMIN_USER_ID):
        for u_id, records in pagos.items():
            for r in records:
                if term == str(r.get("numero_operacion", "")).lower():
                    found_file_id = r.get("file_id")
                    break
            if found_file_id: break
    else:
        for r in user_records:
            if term == str(r.get("numero_operacion", "")).lower():
                found_file_id = r.get("file_id")
                break
                
    if not found_file_id:
        msg = await update.effective_message.reply_text(f"❌ No encontré un recibo con la operación: `{escape_markdown(term)}`", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=msg.message_id)
        return
        
    rep_msg = await update.effective_message.reply_photo(photo=found_file_id, caption="🖼️ Aquí tienes tu recibo\\." + ("\n⏳ _Se borrará en 30s_" if not is_private else ""), parse_mode=ParseMode.MARKDOWN_V2)
    if not is_private: context.job_queue.run_once(_delete_message_job, 30, chat_id=chat_id, data=rep_msg.message_id)


async def buscar_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Búsqueda global cruzando datos de todos los usuarios (Solo Admin)"""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_private:
        try: await update.message.delete()
        except: pass
        
    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return
        
    if not context.args:
        msg = await update.effective_message.reply_text("💡 Uso: `/buscar_admin palabra o monto`", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 10, chat_id=chat_id, data=msg.message_id)
        return
        
    term = " ".join(context.args).lower()
    pagos = load_pagos()
    
    results = []
    for u_id, records in pagos.items():
        for r in records:
            match = False
            if term in str(r.get("pagador", "")).lower(): match = True
            if term in str(r.get("monto", "")).lower(): match = True
            if term in str(r.get("emisor", "")).lower(): match = True
            if term in str(r.get("numero_operacion", "")).lower(): match = True
            if term in u_id: match = True
            if match:
                r_copy = r.copy()
                r_copy["_beneficiario_id"] = u_id
                results.append(r_copy)
                
    if not results:
        msg = await update.effective_message.reply_text(f"❌ No encontré nada en la DB global para: `{escape_markdown(term)}`", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return
        
    txt = f"👑 *BÚSQUEDA GLOBAL: {escape_markdown(term)}*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for r in results[-10:]:
        p = escape_markdown(r.get("pagador", "S/N"))
        m = float(r.get("monto", 0) or 0)
        ben = escape_markdown(r.get("_beneficiario_id", ""))
        uname = escape_markdown(r.get("_username", ""))
        op = escape_markdown(r.get("numero_operacion", "S/N"))
        
        user_display = f"@{uname}" if uname and uname != "S/N" else f"ID: {ben}"
        txt += f"👤 *Pagador:* {p}\n└ S/ `{m:,.2f}` \\| Op: `{op}`\n└ 🏦 *Emisor:* `{user_display}`\n"
        
    if not is_private:
        txt += "\n⏳ _Este reporte se destruirá en 30s_"
        
    msg = await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2)
    if not is_private: context.job_queue.run_once(_delete_message_job, 30, chat_id=chat_id, data=msg.message_id)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Panel Exclusivo de Administrador"""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    is_private = update.effective_chat.type == "private"
    
    if not is_private:
        try: await update.message.delete()
        except: pass
        
    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return
        
    settings = load_settings()
    is_active = settings.get("is_active", True)
    status_emoji = "🟢 ACTIVADO" if is_active else "🔴 DESACTIVADO"
    
    keyboard = [
        [
            InlineKeyboardButton("🌍 REPORTE GLOBAL", callback_data="dash_admin_global"),
            InlineKeyboardButton("👑 Búsqueda Global", callback_data="dash_admin_search_info")
        ],
        [
            InlineKeyboardButton(f"🕹️ Estado: {status_emoji}", callback_data="dash_admin_toggle_status")
        ],
        [
            InlineKeyboardButton("❌ Cerrar", callback_data="dash_close")
        ]
    ]
    txt = "👑 *PANEL EXCLUSIVO DE ADMINISTRADOR*\n━━━━━━━━━━━━━━━━━━━━━━\nConfigura el bot y revisa todo:"
    msg = await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
    if not is_private:
        context.job_queue.run_once(_delete_message_job, 60, chat_id=chat_id, data=msg.message_id)


async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja las respuestas (ForceReply) para prompts interactivos"""
    message = update.effective_message
    if not message.reply_to_message or not message.text: return
    prompt = message.reply_to_message.text
    
    if "BÚSQUEDA GLOBAL" in prompt:
        context.args = message.text.split()
        await buscar_admin_command(update, context)
        try: await message.reply_to_message.delete()
        except: pass
    elif "BUSCADOR DE RECIBOS" in prompt:
        context.args = message.text.split()
        await recibo_command(update, context)
        try: await message.reply_to_message.delete()
        except: pass
    elif "BUSCAR PAGO" in prompt:
        context.args = message.text.split()
        await buscar_command(update, context)
        try: await message.reply_to_message.delete()
        except: pass


def main() -> None:
    logger.info("🚀 Iniciando Autocontador Local...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("commands", commands_command))
    app.add_handler(CommandHandler("buscar", buscar_command))
    app.add_handler(CommandHandler("consultar", consultar_command))
    app.add_handler(CommandHandler("recibo", recibo_command))
    app.add_handler(CommandHandler("buscar_admin", buscar_admin_command))
    app.add_handler(CommandHandler("admin", admin_command))
    
    # Handler unificado para botones interactivos
    app.add_handler(CallbackQueryHandler(handle_callback, pattern='^dash_'))
    
    # Escucha imágenes en cualquier grupo o chat privado
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_media))
    
    # Manejador de respuestas de ForceReply
    app.add_handler(MessageHandler(filters.REPLY, reply_handler))
    
    logger.info("🤖 Bot activo...")
    app.run_polling()

if __name__ == "__main__":
    main()
