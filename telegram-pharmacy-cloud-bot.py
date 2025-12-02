# -*- coding: utf-8 -*-
import os
import threading
import logging
from flask import Flask, request, jsonify
from pathlib import Path
from datetime import datetime
import PyPDF2
import pandas as pd
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters,
)
from anthropic import AsyncAnthropic, DefaultAioHttpClient

# ========= CONFIG =========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PDF_FOLDER = os.getenv("PDF_FOLDER", "catalogos_pdfs")
CATALOG_FILE = os.getenv("CATALOG_FILE", "catalogo_procesado.txt")
PROMO_FILE = os.getenv("PROMO_FILE", "FORMATO PROMOCIONAL.xlsx")

# ========= LOGGING ========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========= PDFs ===========
def extract_text_from_pdf(pdf_path: Path) -> str:
    try:
        text = ""
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                txt = page.extract_text() or ""
                text += txt + "\n"
        return text
    except Exception as e:
        logger.error(f"Error al leer {pdf_path}: {e}")
        return ""

def load_all_pdfs() -> str:
    Path(PDF_FOLDER).mkdir(exist_ok=True)
    pdf_files = list(Path(PDF_FOLDER).glob("*.pdf"))
    if not pdf_files:
        logger.warning(f"No se encontraron PDFs en '{PDF_FOLDER}'")
        return ""
    full_catalog = ""
    for pdf_file in sorted(pdf_files):
        logger.info(f"Leyendo: {pdf_file.name}")
        text = extract_text_from_pdf(pdf_file)
        if text:
            full_catalog += f"\n{'='*60}\nARCHIVO: {pdf_file.name}\n{'='*60}\n{text}"
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        f.write(full_catalog)
    logger.info(f"Catálogo guardado en '{CATALOG_FILE}' ({len(full_catalog)} chars)")
    return full_catalog

def get_catalog() -> str:
    if os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return load_all_pdfs()

# ========= Excel ==========
def load_promotions_and_bonuses() -> str:
    if not os.path.exists(PROMO_FILE):
        logger.warning(f"No se encontró el archivo de promociones ... '{PROMO_FILE}'")
        return ""
    try:
        # Usa el motor openpyxl explícitamente
        xls = pd.ExcelFile(PROMO_FILE, engine="openpyxl")
        text_blocks = []
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(PROMO_FILE, sheet_name=sheet_name, engine="openpyxl")
            df.fillna("-", inplace=True)
            text_blocks.append(f"\n{'='*60}\nHOJA: {sheet_name}\n{'='*60}\n")
            text_blocks.append(df.to_string(index=False))
        return "\n".join(text_blocks)
    except Exception as e:
        logger.error(f"Error leyendo Excel: {e}")
        return f"(Error leyendo Excel: {e})"

def get_full_knowledge() -> str:
    catalog_text = get_catalog()
    promo_text = load_promotions_and_bonuses()
    combined = f"CATÁLOGO DE PRODUCTOS:\n{catalog_text}\n\nPROMOCIONES Y BONIFICACIONES:\n{promo_text}"
    return combined

# ========= PROMPT =========
SYSTEM_PROMPT = """
Eres un asistente especializado de farmacia que ayuda al personal a buscar información sobre:
- Productos nuevos (catálogo PDF)
- Promociones y Bonificaciones (archivo Excel)

INSTRUCCIONES:
1) Responde en español, amigable y profesional.
2) Si la consulta es sobre productos, incluye: Código, Nombre, Precio (S/), Principio activo (si aplica), Laboratorio, Categoría, Notas especiales (cadena de frío, usos médicos, etc.), Documento origen.
3) Si la consulta es sobre promociones/bonificaciones, incluye: Tipo de promoción (Pack, Descuento, 2x1, 3x2), Fechas de vigencia, Precio oferta, Laboratorio, Observaciones.
4) Usa emojis para claridad: 💊 productos, 💰 precios, 🎁 promociones, ✅ confirmación, ⚠️ advertencias.
5) Si hay múltiples resultados, organiza en lista.
6) Si no encuentras exacto, sugiere similares.
"""

PREFERRED_ALIAS = "claude-sonnet-4-5"

async def pick_available_model(client: AsyncAnthropic) -> str:
    try:
        _ = await client.messages.create(
            model=PREFERRED_ALIAS, max_tokens=8, system="Test",
            messages=[{"role": "user", "content": "ping"}],
        )
        return PREFERRED_ALIAS
    except Exception:
        page = await client.models.list()
        ids = [getattr(m, "id", None) or (isinstance(m, dict) and m.get("id")) for m in getattr(page, "data", [])]
        for mid in ids:
            if "sonnet" in mid: return mid
        for mid in ids:
            if "haiku" in mid: return mid
        raise RuntimeError("No hay modelos válidos para esta API key.")

# ========= Telegram Handlers =========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("👋 Bot listo.\nComandos: /actualizar /promos /info /modelos /ping")

async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔄 Recargando catálogo...")
    catalog = load_all_pdfs()
    await update.message.reply_text(f"✅ Catálogo listo ({len(catalog)} chars)" if catalog else f"⚠️ No hay PDFs en '{PDF_FOLDER}'")

### async def reload_promos_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
###     await update.message.reply_text("🔄 Recargando promociones y bonificaciones...")
###     promos = load_promotions_and_bonuses()
###     await update.message.reply_text(f"✅ Promociones cargadas ({len(promos)} chars)" if promos else "⚠️ No se encontró el archivo de promociones.")

async def reload_promos_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔄 Recargando promociones y bonificaciones...2")
    
    # Mostrar la ruta esperada
    await update.message.reply_text(f"Ruta esperada: {PROMO_FILE}")
    
    # Verificar si el archivo existe
    if os.path.exists(PROMO_FILE):
        await update.message.reply_text("✅ Archivo encontrado en el contenedor.")
    else:
        await update.message.reply_text("⚠️ Archivo NO encontrado en el contenedor.")
    
    # Listar archivos en el directorio actual (para confirmar qué hay)
    files = os.listdir(".")
    await update.message.reply_text(f"Archivos en el contenedor: {files}")
    
    # Intentar cargar promociones
    promos = load_promotions_and_bonuses()
    await update.message.reply_text(f"✅ Promociones cargadas ({len(promos)} chars)" if promos else "⚠️ No se encontró el archivo de promociones.")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pdf_files = list(Path(PDF_FOLDER).glob("*.pdf"))
    info = f"📊 **Catálogo**\n📁 {PDF_FOLDER}\n📄 PDFs: {len(pdf_files)}\n"
    if os.path.exists(CATALOG_FILE):
        file_time = datetime.fromtimestamp(os.path.getmtime(CATALOG_FILE))
        file_size = os.path.getsize(CATALOG_FILE)
        info += f"🕒 {file_time.strftime('%d/%m/%Y %H:%M')}\n💾 {file_size:,} bytes\n"
    for pdf in sorted(pdf_files):
        info += f" • {pdf.name}\n"
    await update.message.reply_text(info, parse_mode=ParseMode.MARKDOWN)

async def modelos_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    async with AsyncAnthropic(api_key=ANTHROPIC_API_KEY, http_client=DefaultAioHttpClient(), timeout=30.0) as c:
        page = await c.models.list()
        ids = [getattr(m, "id", None) or (isinstance(m, dict) and m.get("id")) for m in getattr(page, "data", []) if m]
        await update.message.reply_text("🧠 Modelos:\n" + "\n".join(f"• {x}" for x in ids) if ids else "⚠️ Lista vacía")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        async with AsyncAnthropic(api_key=ANTHROPIC_API_KEY, http_client=DefaultAioHttpClient(), timeout=20.0) as c:
            _ = await c.models.list()
            await update.message.reply_text("✅ Conectado a Anthropic.")
    except Exception as e:
        await update.message.reply_text(f"❌ No se pudo conectar: {type(e).__name__}: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_msg = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    knowledge = get_full_knowledge()
    if not knowledge:
        await update.message.reply_text(f"⚠️ Sin datos. Coloca PDFs y Excel, luego usa /actualizar y /promos.")
        return
    content = f"BASE DE CONOCIMIENTO:\n{knowledge}\n---\nPregunta: {user_msg}\nResponde en español."
    async with AsyncAnthropic(api_key=ANTHROPIC_API_KEY, http_client=DefaultAioHttpClient(), timeout=60.0, max_retries=2) as client:
        model_id = await pick_available_model(client)
        try:
            message = await client.messages.create(
                model=model_id, max_tokens=2048, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error consultando IA: {e}")
            return
        response_text = "".join(getattr(b, "text", "") for b in getattr(message, "content", []) if getattr(b, "type", "") == "text") or "(Sin contenido)"
        MAX_LEN = 3900
        for i in range(0, len(response_text), MAX_LEN):
            await update.message.reply_text(response_text[i : i + MAX_LEN])

# ========= Flask API =========
web_app = Flask(__name__)
from flask_cors import CORS
CORS(web_app)

@web_app.route("/consulta", methods=["POST"])
def consulta():
    pregunta = request.json.get("pregunta")
    knowledge = get_full_knowledge()
    if not knowledge:
        return jsonify({"error": "Sin datos cargados"}), 400
    content = f"BASE DE CONOCIMIENTO:\n{knowledge}\n---\nPregunta: {pregunta}\nResponde en español."
    import asyncio
    async def call_anthropic():
        async with AsyncAnthropic(api_key=ANTHROPIC_API_KEY, http_client=DefaultAioHttpClient(), timeout=60.0, max_retries=2) as client:
            model_id = await pick_available_model(client)
            message = await client.messages.create(
                model=model_id, max_tokens=2048, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            return "".join(getattr(b, "text", "") for b in getattr(message, "content", []) if getattr(b, "type", "") == "text") or "(Sin contenido)"
    respuesta = asyncio.run(call_anthropic())
    return jsonify({"respuesta": respuesta})

def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

def run_telegram():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("actualizar", reload_command))
    app.add_handler(CommandHandler("promos", reload_promos_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("modelos", modelos_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_telegram()
