
# -*- coding: utf-8 -*-
import os
import logging
from pathlib import Path
from datetime import datetime

import PyPDF2
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters,
)
from anthropic import AsyncAnthropic, DefaultAioHttpClient

# ========= CONFIG =========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")            # set en Render
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")      # set en Render

PDF_FOLDER = os.getenv("PDF_FOLDER", "catalogos_pdfs")
CATALOG_FILE = os.getenv("CATALOG_FILE", "catalogo_procesado.txt")

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

# ========= PROMPT =========
SYSTEM_PROMPT = """Eres un asistente especializado de farmacia...
(usa el prompt completo que ya teníamos)"""

PREFERRED_ALIAS = "claude-sonnet-4-5"

async def pick_available_model(client: AsyncAnthropic) -> str:
    # 1) probar alias moderno
    try:
        _ = await client.messages.create(
            model=PREFERRED_ALIAS, max_tokens=8, system="Test",
            messages=[{"role": "user", "content": "ping"}],
        )
        return PREFERRED_ALIAS
    except Exception:
        # 2) listar y elegir sonnet/haiku
        page = await client.models.list()
        ids = []
        for m in getattr(page, "data", []):
            mid = getattr(m, "id", None) or (isinstance(m, dict) and m.get("id"))
            if mid:
                ids.append(mid)
        for mid in ids:
            if "sonnet" in mid:
                return mid
        for mid in ids:
            if "haiku" in mid:
                return mid
        raise RuntimeError("No hay modelos válidos para esta API key.")

# ========= HANDLERS =======
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Bot de farmacia listo.\nComandos: /actualizar /info /modelos /ping"
    )

async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔄 Recargando catálogo...")
    catalog = load_all_pdfs()
    if catalog:
        await update.message.reply_text(f"✅ Catálogo listo ({len(catalog)} chars)")
    else:
        await update.message.reply_text(f"⚠️ No hay PDFs en '{PDF_FOLDER}'")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pdf_files = list(Path(PDF_FOLDER).glob("*.pdf"))
    if os.path.exists(CATALOG_FILE):
        file_time = datetime.fromtimestamp(os.path.getmtime(CATALOG_FILE))
        file_size = os.path.getsize(CATALOG_FILE)
        info = (
            f"📈 **Catálogo**\n📁 {PDF_FOLDER}\n📄 PDFs: {len(pdf_files)}\n"
            f"🕒 {file_time.strftime('%d/%m/%Y %H:%M')}\n💾 {file_size:,} bytes\n"
        )
        for pdf in sorted(pdf_files):
            info += f" • {pdf.name}\n"
        await update.message.reply_text(info, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("⚠️ No hay catálogo cargado. Usa /actualizar.")

async def modelos_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    async with AsyncAnthropic(api_key=ANTHROPIC_API_KEY, http_client=DefaultAioHttpClient(), timeout=30.0) as c:
        page = await c.models.list()
        ids = []
        for m in getattr(page, "data", []):
            mid = getattr(m, "id", None) or (isinstance(m, dict) and m.get("id"))
            if mid:
                ids.append(mid)
        await update.message.reply_text(
            "🧠 Modelos:\n" + "\n".join(f"• {x}" for x in ids) if ids else "⚠️ Lista vacía"
        )

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

    catalog = get_catalog()
    if not catalog:
        await update.message.reply_text(f"⚠️ Sin catálogo. Coloca PDFs en '{PDF_FOLDER}' y usa /actualizar.")
        return

    content = f"CATÁLOGO:\n{catalog}\n---\nPregunta: {user_msg}\nResponde en español."

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

    response_text = "".join(
        getattr(b, "text", "") for b in getattr(message, "content", [])
        if getattr(b, "type", "") == "text"
    ) or "(Sin contenido)"

    MAX_LEN = 3900
    for i in range(0, len(response_text), MAX_LEN):
        await update.message.reply_text(response_text[i : i + MAX_LEN])

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.getLogger(__name__).error("Excepción:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Hubo un inconveniente. Intenta nuevamente."
            )
        except Exception:
            pass

def main() -> None:
    Path(PDF_FOLDER).mkdir(exist_ok=True)
    if not os.path.exists(CATALOG_FILE):
        _ = load_all_pdfs()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("actualizar", reload_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("modelos", modelos_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logging.getLogger(__name__).info("Bot iniciado (Render).")
    app.run_polling()

if __name__ == "__main__":
    main()
