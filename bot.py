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
ELIMINADOS_FILE = Path("eliminados.json")
PENDIENTES_FILE = Path("pendientes.json")

# ---------------------------------------------------------------------------
# Helpers JSON y Procesamiento
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {"is_active": True}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Error leyendo settings, usando defaults: %s", e)
        return {"is_active": True}

def save_settings(data: dict) -> None:
    try:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    except Exception as e:
        logger.error("Error guardando settings: %s", e)

def is_bot_active(user_id: str) -> bool:
    if ADMIN_USER_ID and str(user_id) == str(ADMIN_USER_ID):
        return True
    return load_settings().get("is_active", True)

async def manage_disabled_warning(chat_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el mensaje de advertencia 'bot desactivado' con contador de cola."""
    prev_msg_id = context.bot_data.get(f"disabled_msg_{chat_id}")
    if prev_msg_id:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=prev_msg_id)
        except: pass
    
    count = 0
    if PENDIENTES_FILE.exists():
        try: count = len(json.loads(PENDIENTES_FILE.read_text(encoding="utf-8")))
        except: pass
    
    txt = "⛔ *SISTEMA EN PAUSA (MODO SIGILO)*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    if count > 0:
        txt += f"📥 Hay *{count}* recibos capturados en cola\\.\n"
        txt += "💡 _El Administrador los procesará al activar el bot\\._\n"
    else:
        txt += "💡 _Ningún dato será procesado públicamente hasta que el administrador lo active\\._\n"
    
    txt += "━━━━━━━━━━━━━━━━━━━━━━\n🛡️ _Tu privacidad está protegida: las fotos se eliminan al instante del chat\\._"
    
    msg = await context.bot.send_message(
        chat_id=chat_id, 
        text=txt, 
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
    """Guarda el JSON de pagos de forma atómica (evita corrupción por corte de energía)."""
    try:
        tmp = PAGOS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, PAGOS_FILE)
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


def escape_markdown(text: str, is_code: bool = False) -> str:
    """Escapa caracteres especiales para MarkdownV2. 
    Si is_code=True, solo escapa backticks y backslashes."""
    text = str(text)
    if is_code:
        return text.replace("\\", "\\\\").replace("`", "\\`")
    
    characters = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in characters:
        text = text.replace(char, f'\\{char}')
    return text

def log_deletion(original_user_id: str, record: dict, deleted_by_id: str):
    """Guarda rastro de qué se borró, quién lo hizo y los datos originales."""
    logs = []
    if ELIMINADOS_FILE.exists():
        try:
            logs = json.loads(ELIMINADOS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Error leyendo log de eliminados: %s", e)
    
    entry = {
        "fecha_borrado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "borrado_por": deleted_by_id,
        "usuario_original": original_user_id,
        "username_original": record.get("_username", "N/A"),
        "datos_pago": {
            "monto": record.get("monto"),
            "emisor": record.get("emisor"),
            "op": record.get("numero_operacion"),
            "pagador": record.get("pagador")
        }
    }
    logs.append(entry)
    try:
        tmp = ELIMINADOS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, ELIMINADOS_FILE)
    except Exception as e:
        logger.error("Error guardando log de eliminados: %s", e)


# ---------------------------------------------------------------------------
# OpenRouter API Logic
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
Eres un experto en OCR y lectura de comprobantes de pago digitales peruanos (Yape, Plin, BCP, BBVA, Interbank, etc.) y billeteras cripto (Binance, Lemon).
Tu tarea es EXTRAER exactamente estos campos en formato JSON:

  - emisor: nombre de la App o Banco (Yape, Plin, PayPal, Lemon, Binance, Interbank, etc.).
  - pagador: nombre completo de quien envía el dinero.
  - monto: valor numérico decimal puro (ej: 10.50). SIN símbolos de moneda.
  - moneda: nombre de la moneda (Soles, Dólares, Euros, USDT, etc.).
  - pais: país de origen.
  - numero_operacion: ID único de la transacción.
  - fecha: YYYY-MM-DD.
  - hora: HH:MM:SS.
  - destino: Nombre del receptor.
  - categoria: Tipo de movimiento (Ventas, Servicios, etc.).
  - referencia: Notas del pago (si hay).

REGLAS CRÍTICAS:
1. Responde ÚNICAMENTE con el objeto JSON puro.
2. NUNCA incluyas texto antes o después del JSON.
3. Si un dato no está, usa null (tipo null, no string "null").
4. El monto debe ser un NÚMERO, no un string.
"""

def _clean_numeric_value(val) -> float:
    """Intenta convertir cualquier valor a float, limpiando símbolos de moneda o texto."""
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    
    # Si es string, limpiar caracteres no numéricos excepto punto y coma
    s = str(val).replace(",", ".").replace("S/", "").replace("$", "").replace(" ", "")
    # Extraer el primer número decimal que encontremos
    match = re.search(r"[-+]?\d*\.?\d+", s)
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass
    return 0.0

def process_receipt_with_ai(image_path: Path) -> dict:
    """Envía la imagen a la IA para extraer datos estructurados."""
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
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/autocontador", # Referer opcional para OpenRouter
    }

    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        
        if "choices" not in result or not result["choices"]:
            logger.error("Respuesta inesperada de OpenRouter: %s", result)
            raise ValueError("No se obtuvo respuesta de la IA")
            
        content = result["choices"][0]["message"]["content"].strip()
        logger.debug("Raw AI Response: %s", content)
        
        # Extraer el JSON del contenido (buscamos el bloque más grande entre llaves)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        json_str = match.group(0) if match else content
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Error al decodificar JSON de la IA. Contenido original: %s", content)
            raise e
            
    except Exception as e:
        logger.error("Fallo durante el procesamiento con IA: %s", e)
        # Fallback seguro para no romper el flujo
        return {
            "emisor": "ERROR", 
            "pagador": "No se pudo extraer", 
            "monto": 0.0, 
            "moneda": "Soles", 
            "numero_operacion": f"ERR-{str(uuid.uuid4())[:6]}",
            "referencia": "Error en el análisis de la IA"
        }


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
        
    # 2. Definir ruta temporal
    dest_path = TEMP_DIR / f"{file_id}{ext}"
    
    # 3. Flujo Diferenciado: Inactivo (Sigilo) vs Activo (Visual)
    if not is_bot_active(user_id):
        # ────────── MODO INACTIVO (SIGILO) ──────────
        try:
            tg_file = await context.bot.get_file(file_id)
            await tg_file.download_to_drive(dest_path)
            img_hash = _calculate_hash(dest_path)
            
            if is_duplicate(user_id, img_hash, ""):
                if not is_private:
                    try: await message.delete()
                    except: pass
                return

            pendientes = []
            if PENDIENTES_FILE.exists():
                try: pendientes = json.loads(PENDIENTES_FILE.read_text(encoding="utf-8"))
                except: pass
            
            pendientes.append({
                "file_id": file_id,
                "message_id": message.message_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "is_private": is_private,
                "caption": user_caption,
                "hash": img_hash,
                "ext": ext,
                "username": update.effective_user.username or "S/N"
            })
            
            tmp = PENDIENTES_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(pendientes, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, PENDIENTES_FILE)
            
            if is_private:
                await message.reply_text("⏳ El bot está en pausa\\. He capturado tu recibo sigilosamente y se procesará automáticamente cuando el ADMIN active el bot\\.", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await manage_disabled_warning(chat_id, context)
            return
            
        except Exception as e:
            logger.error("Error en modo sigilo: %s", e)
            return

    # ────────── MODO ACTIVO (VISUAL) ──────────
    try:
        # Borrar del grupo inmediatamente por privacidad
        if not is_private:
            try: await message.delete()
            except: pass

        # Feedback Visual Inicial
        status_text = f"`{format_progress_bar(10, 100)}` 📥 Descargando\\.\\.\\."
        if is_private:
            status_msg = await message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            status_msg = await context.bot.send_message(chat_id=chat_id, text=status_text, parse_mode=ParseMode.MARKDOWN_V2)

        # Descarga Real
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(dest_path)
        img_hash = _calculate_hash(dest_path)
        
        # Verificar duplicados por hash
        if is_duplicate(user_id, img_hash, ""):
            await status_msg.edit_text("⚠️ Este comprobante ya existe en tu historial.")
            return

        # Procesar con IA
        await status_msg.edit_text(f"`{format_progress_bar(50, 100)}` 🧠 Consultando IA\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        data = await asyncio.to_thread(process_receipt_with_ai, dest_path)
        
        # Fusionar caption sin etiquetas "Nota IA"
        if user_caption:
            ref = str(data.get("referencia", "")).strip()
            if ref and ref.lower() != "null":
                data["referencia"] = f"{user_caption} | {ref}"
            else:
                data["referencia"] = user_caption
        
        await status_msg.edit_text(f"`{format_progress_bar(90, 100)}` 💾 Guardando datos\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        
        if not data.get("numero_operacion"):
            data["numero_operacion"] = "SYS-" + str(uuid.uuid4()).upper()[:6]
        
        op = data["numero_operacion"]
        if is_duplicate(user_id, "", op):
            await status_msg.edit_text(f"⚠️ El N° Operación: {op} ya existe.")
            return

        # Limpieza y conversión de moneda
        monto = _clean_numeric_value(data.get("monto"))
        data["monto"] = monto # Normalizar a número limpio
        if data.get("moneda") == "Dólares":
            rate = await asyncio.to_thread(get_exchange_rate)
            data["monto_original"] = f"{monto} USD"
            data["monto"] = round(monto * rate, 2)

        data.update({"image_hash": img_hash, "file_id": file_id, "_username": update.effective_user.username or "S/N"})
        
        pagos = load_pagos()
        if user_id not in pagos: pagos[user_id] = []
        pagos[user_id].append(data)
        save_pagos(pagos)
        
        ticket = _generate_ticket(data)
        kbd = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Dashboard", callback_data="dash_menu"), InlineKeyboardButton("🗑️ Borrar", callback_data="dash_delete_conf")]])
        
        await status_msg.delete()
        rep = await context.bot.send_message(chat_id=chat_id, text=ticket, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kbd)
        if not is_private:
            context.job_queue.run_once(_delete_message_job, 30, chat_id=chat_id, data=rep.message_id)

    except Exception:
        logger.exception("Error en media")
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
            InlineKeyboardButton("🗑️ Eliminar Pagos", callback_data="dash_delete_conf"),
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

    if not user_records:
        # Permitir que el admin vea sus cosas y el usuario vea ayuda/cierre aunque no tengan pagos
        is_admin_data = data.startswith("dash_admin_")
        if data not in ["dash_help", "dash_menu", "dash_close", "dash_commands", "dash_admin_menu"] and not is_admin_data:
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
        recientes = list(enumerate(user_records))[-10:][::-1]
        keyboard = []
        for idx, r in recientes:
            e = str(r.get("emisor", ""))[:10]
            m = float(r.get("monto", 0) or 0)
            btn_txt = f"🗑️ {e} | S/ {m:.2f} | {r.get('fecha','')}"
            keyboard.append([InlineKeyboardButton(btn_txt, callback_data=f"dash_del_spec_{idx}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="dash_menu")])
        txt = "🗑️ *ELIMINACIÓN SELECTIVA*\nSelecciona exactamente qué pago deseas borrar:"
        # Corrección de guiones y paréntesis para MarkdownV2
        txt = txt.replace("-", "\\-").replace("(", "\\(").replace(")", "\\)").replace(".", "\\.")
        await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("dash_del_spec_"):
        try:
            idx = int(data.replace("dash_del_spec_", ""))
            if 0 <= idx < len(user_records):
                eliminado = user_records.pop(idx)
                save_pagos(pagos)
                log_deletion(user_id, eliminado, user_id)
                await query.answer(f"✅ Borrado: S/ {eliminado.get('monto')}", show_alert=True)
        except Exception as e:
            logger.warning("Error en borrado selectivo: %s", e)
        await dashboard_command(update, context)
    elif data == "dash_admin_search_info":
        if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
        from telegram import ForceReply
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="👑 *BÚSQUEDA GLOBAL*\nResponde a este mensaje con la palabra, monto o nombre que buscas:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=ForceReply(selective=True)
        )
    elif data == "dash_admin_global":
        await _show_admin_global(query)
    elif data == "dash_admin_del_op_prompt":
        if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
        from telegram import ForceReply
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🚫 *BORRADO MAESTRO*\nResponde a este mensaje con el NÚMERO DE OPERACIÓN que deseas eliminar de la DB global:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=ForceReply(selective=True)
        )
    elif data == "dash_admin_show_logs":
        if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
        if not ELIMINADOS_FILE.exists():
            await query.answer("No hay logs aún\\.", show_alert=True)
            return
        try:
            logs = json.loads(ELIMINADOS_FILE.read_text(encoding="utf-8"))[-10:][::-1]
        except Exception as e:
            logger.error("Error leyendo log de eliminados: %s", e)
            await query.answer("Error leyendo el log\\.", show_alert=True)
            return
        
        if not logs:
            await query.answer("No hay registros de eliminación aún\\.", show_alert=True)
            return
        
        txt = "🗑️ *HISTORIAL DE ELIMINACIONES*\n━━━━━━━━━━━━━━━━━━━━━━\n"
        # Escapar guiones y otros caracteres reservados
        txt = txt.replace("-", "\\-").replace("(", "\\(").replace(")", "\\)")
        for log_entry in logs:
            fecha = escape_markdown(str(log_entry.get("fecha_borrado", "S/F")))
            u = escape_markdown(str(log_entry.get("username_original", "S/N")))
            m = log_entry.get("datos_pago", {}).get("monto", 0)
            by_id = str(log_entry.get("borrado_por") or "?")[:8]
            txt += f"• `{fecha}`\n  └ @{u} \\| S/ `{m}` \\| por `{escape_markdown(by_id)}`\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data="dash_admin_global")]]
        try:
            await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error("Error mostrando logs de eliminación: %s", e)
            await query.answer("Error al mostrar logs\\.", show_alert=True)
    elif data == "dash_admin_toggle_status":
        if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
        settings = load_settings()
        new_status = not settings.get("is_active", True)
        settings["is_active"] = new_status
        save_settings(settings)
        
        status_txt = "🟢 ACTIVADO" if new_status else "🔴 DESACTIVADO"
        bot_msg = "ACTIVADO" if new_status else "DESACTIVADO"
        
        count = 0
        if PENDIENTES_FILE.exists():
            try:
                count = len(json.loads(PENDIENTES_FILE.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning("Error contando pendientes: %s", e)

        for c_id in AUTHORIZED_CHATS:
            # Limpiar warnings anteriores
            last_msg_id = context.bot_data.get(f"disabled_msg_{c_id}")
            if last_msg_id:
                try: await context.bot.delete_message(chat_id=c_id, message_id=last_msg_id)
                except: pass
                context.bot_data.pop(f"disabled_msg_{c_id}", None)
            
            # Anuncio de activación/desactivación
            txt = "🟢 *BOT ACTIVADO*" if new_status else "🔴 *BOT DESACTIVADO*"
            if new_status and count > 0:
                txt += f"\n📥 *{count}* recibos aceptados para análisis\\."
            elif new_status:
                txt += "\n_Listo para recibir nuevos comprobantes\\._"
            else:
                txt += "\n_Modo sigilo activado: procesando solo en cola\\._"
            
            try:
                ann = await context.bot.send_message(chat_id=c_id, text=txt, parse_mode=ParseMode.MARKDOWN_V2)
                context.job_queue.run_once(_delete_message_job, 15, chat_id=c_id, data=ann.message_id)
            except Exception: pass
        
        if new_status:
            context.job_queue.run_once(process_pending_queue, 1)
            
        await admin_command(update, context)

    elif data == "dash_admin_menu":
        await admin_command(update, context)

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
        # Limpiar dict para el CSV y asegurar que el país esté bien redactado (Capitalizado)
        row = {k: r.get(k, "") for k in fields}
        if row.get("pais"):
            row["pais"] = str(row["pais"]).strip().title()
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
        msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*\\.", parse_mode=ParseMode.MARKDOWN_V2)
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
    )
    
    # Auditoría de eliminación
    logs_count = 0
    if ELIMINADOS_FILE.exists():
        try: logs_count = len(json.loads(ELIMINADOS_FILE.read_text(encoding="utf-8")))
        except: pass
    
    txt += f"🗑️ *Registros Eliminados \\(Auditados\\):* {logs_count}\n"
    txt += "💡 _Este reporte es privado y visible solo para el dueño del bot\\._"
    
    # Escapar guiones en el separador
    txt = txt.replace("━━━━", "\\━━━━")
    
    keyboard = [
        [InlineKeyboardButton("📜 Ver Log de Eliminados", callback_data="dash_admin_show_logs")],
        [InlineKeyboardButton("🔙 Volver al Admin", callback_data="dash_admin_menu")]
    ]
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

    user_name = escape_markdown(update.effective_user.first_name)
    help_text = (
        f"👋 ¡Hola {user_name}\\! Soy tu asistente de Autocontador\\.\n\n"
        "*¿Cómo funciono?*\n"
        "1\\. Envíame una *foto o captura* de un comprobante \\(Yape, Plin, Transferencia, etc\\.\\)\\.\n"
        "2\\. Analizaré la imagen y guardaré los datos automáticamente\\.\n\n"
        "*Comandos disponibles:*\n"
        "🚀 /start \\- Ver este mensaje de ayuda\\.\n"
        "📊 /dashboard \\- Ver tu resumen de pagos\\.\n\n"
        "💡 _Tip: Puedes enviarme fotos en chat privado o en grupos autorizados\\._"
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
    
    if ADMIN_USER_ID and user_id == str(ADMIN_USER_ID):
        keyboard.append([InlineKeyboardButton("👑 Panel Administrativo", callback_data="dash_admin_menu")])
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
    op = escape_markdown(data.get("numero_operacion", "null"), is_code=True)
    cat = escape_markdown(data.get("categoria", "Otros"))
    f = escape_markdown(data.get("fecha", ""))
    h = escape_markdown(data.get("hora", ""))
    dest = escape_markdown(data.get("destino", "N/D"))
    pa = escape_markdown(data.get("pais", "N/D"))
    ref = escape_markdown(data.get("referencia", ""))
    mon = escape_markdown(data.get("moneda", "Soles"), is_code=True)
    
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
        msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*\\.", parse_mode=ParseMode.MARKDOWN_V2)
        if not is_private: context.job_queue.run_once(_delete_message_job, 15, chat_id=chat_id, data=msg.message_id)
        return

    if not context.args:
        await update.effective_message.reply_text("💡 Uso: `/consultar Maria` o `/consultar Ventas`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    term = " ".join(context.args).lower()
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
        msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ El Bot se encuentra temporalmente *DESACTIVADO*\\.", parse_mode=ParseMode.MARKDOWN_V2)
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
        ben = escape_markdown(r.get("_beneficiario_id", ""), is_code=True)
        uname = escape_markdown(r.get("_username", ""), is_code=True)
        op = escape_markdown(r.get("numero_operacion", "S/N"), is_code=True)
        
        user_display = f"@{uname}" if uname and uname != "S/N" else f"ID: {ben}"
        txt += f"👤 *Pagador:* {p}\n└ S/ `{m:,.2f}` \\| Op: `{op}`\n└ 🏦 *Emisor:* `{user_display}`\n"
        
    if not is_private:
        txt += "\n⏳ _Este reporte se destruirá en 30s_"
        
    msg = await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2)
    if not is_private: context.job_queue.run_once(_delete_message_job, 30, chat_id=chat_id, data=msg.message_id)


async def borrar_op_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Permite al admin borrar un registro por N° de operación (Global)"""
    user_id = str(update.effective_user.id)
    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID): return
    
    if not context.args:
        await update.message.reply_text("💡 Uso: `/borrar_op NUMERO_OPERACION`")
        return
        
    op_to_del = context.args[0].strip().lower()
    pagos = load_pagos()
    found = False
    
    for u_id, records in pagos.items():
        for i, r in enumerate(records):
            if str(r.get("numero_operacion", "")).lower() == op_to_del:
                eliminado = records.pop(i)
                log_deletion(u_id, eliminado, user_id)
                save_pagos(pagos)
                found = True
                await update.message.reply_text(f"✅ Registro '{op_to_del}' borrado exitosamente del usuario {u_id}.")
                break
        if found: break
        
    if not found:
        await update.message.reply_text(f"❌ No encontré ningún pago con N° Operación: {op_to_del}")


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
            InlineKeyboardButton("📊 Reporte Avanzado", callback_data="dash_admin_global"),
            InlineKeyboardButton("🎯 Búsqueda Maestro", callback_data="dash_admin_search_info")
        ],
        [
            InlineKeyboardButton("🗑️ Borrado Global", callback_data="dash_admin_del_op_prompt"),
            InlineKeyboardButton(f"⚡ {status_emoji}", callback_data="dash_admin_toggle_status")
        ],
        [
            InlineKeyboardButton("📜 Historial de Auditoría", callback_data="dash_admin_show_logs")
        ],
        [
            InlineKeyboardButton("💎 Command Center", callback_data="dash_commands"),
            InlineKeyboardButton("🚪 Salir", callback_data="dash_close")
        ]
    ]
    txt = (
        "💎 *ELITE FINANCIAL TERMINAL*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 *Owner:* {escape_markdown(update.effective_user.first_name)}\n"
        f"📡 *Estado Sistema:* {status_emoji}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Acceso maestro concedido\\. Selecciona un módulo:"
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        msg = await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))
        if not is_private:
            context.job_queue.run_once(_delete_message_job, 45, chat_id=chat_id, data=msg.message_id)


async def process_pending_queue(context: ContextTypes.DEFAULT_TYPE):
    """Procesa recibos acumulados mientras el bot estaba OFF.
    
    Estrategia segura: procesa primero, luego elimina solo los exitosos.
    Si el bot crashea a mitad, los no procesados permanecen en la cola.
    """
    if not PENDIENTES_FILE.exists():
        return
    try:
        pendientes = json.loads(PENDIENTES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Error leyendo cola de pendientes: %s", e)
        return
    
    if not pendientes:
        return
    
    logger.info("Procesando %d recibos en cola...", len(pendientes))
    processed_ids = set()
    
    for p in pendientes:
        d_path = None
        try:
            f_id = p["file_id"]
            u_id, c_id = p["user_id"], p["chat_id"]
            cap, h, ext = p.get("caption", ""), p["hash"], p.get("ext", ".jpg")
            d_path = TEMP_DIR / f"q_{f_id}{ext}"
            
            tg_f = await context.bot.get_file(f_id)
            await tg_f.download_to_drive(d_path)
            data = await asyncio.to_thread(process_receipt_with_ai, d_path)
            
            if cap:
                ref = str(data.get("referencia", "")).strip()
                if ref and ref.lower() != "null":
                    data["referencia"] = f"{cap} | {ref}"
                else:
                    data["referencia"] = cap
            
            if not data.get("numero_operacion"):
                data["numero_operacion"] = "SYS-" + str(uuid.uuid4()).upper()[:6]
            
            op = data["numero_operacion"]
            # Verificar duplicado antes de guardar (protege contra re-procesamiento)
            if is_duplicate(u_id, h, op):
                logger.warning("Pendiente duplicado ignorado: hash=%s op=%s", h[:8], op)
                processed_ids.add(f_id)  # Marcar como procesado para que salga de la cola
                continue
            
            # Limpieza y conversión de moneda
            monto = _clean_numeric_value(data.get("monto"))
            data["monto"] = monto # Normalizar a número limpio
            if data.get("moneda") == "Dólares":
                rate = await asyncio.to_thread(get_exchange_rate)
                data["monto_original"] = f"{monto} USD"
                data["monto"] = round(monto * rate, 2)
            
            data.update({"image_hash": h, "file_id": f_id, "_username": p.get("username", "S/N")})
            
            pagos = load_pagos()
            if u_id not in pagos:
                pagos[u_id] = []
            pagos[u_id].append(data)
            save_pagos(pagos)
            
            txt = _generate_ticket(data) + "\n\n📥 _Procesado desde la cola sigilosa\\._"
            await context.bot.send_message(chat_id=c_id, text=txt, parse_mode=ParseMode.MARKDOWN_V2)
            
            # Borrar el mensaje original del grupo AHORA que ya fue procesado
            if not p.get("is_private", True) and p.get("message_id"):
                try:
                    await context.bot.delete_message(chat_id=c_id, message_id=p["message_id"])
                    logger.info("Mensaje original borrado del grupo: chat=%s msg=%s", c_id, p['message_id'])
                except Exception as del_err:
                    logger.warning("No se pudo borrar mensaje del grupo (puede ya no existir): %s", del_err)
            
            processed_ids.add(f_id)
            logger.info("Pendiente procesado exitosamente: %s", f_id)
        except Exception:
            logger.exception("Error procesando pendiente %s", p.get("file_id", "?"))
        finally:
            if d_path:
                d_path.unlink(missing_ok=True)
    
    # Solo eliminar los que se procesaron exitosamente
    remaining = [p for p in pendientes if p.get("file_id") not in processed_ids]
    if remaining:
        logger.warning("%d recibos no pudieron procesarse, se mantienen en cola.", len(remaining))
        try:
            tmp = PENDIENTES_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, PENDIENTES_FILE)
        except Exception as e:
            logger.error("Error actualizando cola de pendientes: %s", e)
    else:
        PENDIENTES_FILE.unlink(missing_ok=True)
        logger.info("Cola de pendientes procesada y limpiada.")


async def group_cleanup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elimina cualquier mensaje no deseado en grupos (texto, stickers, etc) para mantener limpieza total."""
    if update.effective_chat.type in ["group", "supergroup"]:
        chat_id = str(update.effective_chat.id)
        if chat_id in AUTHORIZED_CHATS:
            user_id = str(update.effective_user.id)
            # Ignorar si es el admin configurado
            if ADMIN_USER_ID and user_id == str(ADMIN_USER_ID):
                return
            
            try:
                await update.effective_message.delete()
            except:
                pass


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
    elif "BORRADO MAESTRO" in prompt:
        context.args = message.text.split()
        await borrar_op_command(update, context)
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


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja y filtra errores globales para evitar caídas catastróficas y loops infinitos."""
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logger.critical("==================================================================")
        logger.critical(" ❌ ERROR CRÍTICO: DETECCIÓN DE INSTANCIAS DUPLICADAS")
        logger.critical(" Otro proceso ya está usando este bot (quizás otra ventana de consola).")
        logger.critical(" Telegram no permite dos clones al mismo tiempo. Apagando este clon...")
        logger.critical("==================================================================")
        import os
        os._exit(1)
    else:
        logger.error("Excepción no manejada en el ciclo de eventos:", exc_info=context.error)


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
    app.add_handler(CommandHandler("borrar_op", borrar_op_command))
    app.add_handler(CommandHandler("admin", admin_command))
    
    # Handler unificado para botones interactivos
    app.add_handler(CallbackQueryHandler(handle_callback, pattern='^dash_'))
    
    # Escucha imágenes en cualquier grupo o chat privado
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_media))
    
    # Manejador de respuestas de ForceReply
    app.add_handler(MessageHandler(filters.REPLY, reply_handler))
    
    # Manejador de Limpieza de Grupo (Debe ir después de los comandos específicos)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND & ~filters.PHOTO & ~filters.Document.IMAGE, group_cleanup_handler))
    
    # Manejador de Errores Global Inteligente
    app.add_error_handler(global_error_handler)
    
    # Notificación de arranque para el Admin
    if ADMIN_USER_ID:
        async def notify_startup(context):
            try: 
                await context.bot.send_message(
                    chat_id=ADMIN_USER_ID, 
                    text="⚙️ *SISTEMA REINICIADO*\nEl bot se ha encendido correctamente\\. Accede al panel con /admin\\.",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except: pass
        app.job_queue.run_once(notify_startup, 2)
    
    logger.info("🤖 Bot activo...")
    app.run_polling()

if __name__ == "__main__":
    import asyncio
    import sys

    # Python 3.14 no crea un event loop por defecto en el thread principal.
    # python-telegram-bot requiere uno existente al llamar run_polling().
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot detenido.")
    except Exception:
        logger.exception("Error crítico:")
    finally:
        loop.close()
