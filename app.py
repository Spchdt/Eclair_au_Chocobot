import os
import httpx
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. SERVER AND CLIENT INITIALIZATION
# ---------------------------------------------------------
app = FastAPI()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_HOSTNAME")
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

telegram_app = Application.builder().token(TOKEN).updater(None).build()

# ---------------------------------------------------------
# 2. CORE GEMINI INFERENCE PIPELINE
# ---------------------------------------------------------
async def process_with_gemini(text: str) -> str:
    """Submits text prompts cleanly to the Gemini 2.5 Flash model."""
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=text,
        )
        return response.text
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "Sorry, I had an issue processing that request with my AI engine."

# ---------------------------------------------------------
# 3. DIRECT STANDARD CHAT HANDLERS
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "🤖 *I am your Ultimate Multimodal AI Assistant!*\n\n"
        "✨ *Features Active*:\n"
        "👥 *Guest Bot Enabled* (Tag me `@username query` anywhere without adding me!)\n"
        "📝 Text & Multimodal media (Photos, Voice, Audio, Videos, Docs)\n"
        "📍 Location Awareness"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_response = await process_with_gemini(update.message.text)
    await update.message.reply_text(ai_response)

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lat, lon = update.message.location.latitude, update.message.location.longitude
    prompt = f"I pinned a map location at Lat: {lat}, Lon: {lon}. Briefly state what city/region this is, and 2 unique details about it."
    await update.message.reply_text("🗺️ Reading coordinates...")
    ai_response = await process_with_gemini(prompt)
    await update.message.reply_text(ai_response)

# ---------------------------------------------------------
# 4. MULTIMODAL MEDIA HANDLER
# ---------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    await message.reply_text("⏳ Processing file...")
    
    file_id, mime_type = None, ""
    prompt_text = message.caption if message.caption else ""

    if message.photo:
        file_id, mime_type = message.photo[-1].file_id, "image/jpeg"
        if not prompt_text: prompt_text = "Describe this image in detail."
    elif message.video:
        file_id, mime_type = message.video.file_id, message.video.mime_type or "video/mp4"
        if not prompt_text: prompt_text = "Summarize what happens in this video clip."
    elif message.voice:
        file_id, mime_type = message.voice.file_id, message.voice.mime_type or "audio/ogg"
        if not prompt_text: prompt_text = "Transcribe and answer this voice message."
    elif message.audio:
        file_id, mime_type = message.audio.file_id, message.audio.mime_type or "audio/mpeg"
        if not prompt_text: prompt_text = "Analyze this audio track."
    elif message.document:
        file_id, mime_type = message.document.file_id, message.document.mime_type
        if not prompt_text: prompt_text = "Read and summarize this document."
    else:
        return

    try:
        file = await context.bot.get_file(file_id)
        if file.file_size > 20971520:
            await message.reply_text("⚠️ File exceeds Telegram's 20MB limit.")
            return

        file_bytes = await file.download_as_bytearray()
        gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[gemini_part, prompt_text]
        )
        await message.reply_text(response.text)
    except Exception as e:
        print(f"Media Error: {e}")
        await message.reply_text("❌ Error processing media payload.")

# ---------------------------------------------------------
# 5. HANDLER REGISTRATION
# ---------------------------------------------------------
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(MessageHandler(filters.LOCATION, location_handler))
media_filters = (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL)
telegram_app.add_handler(MessageHandler(media_filters, media_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ---------------------------------------------------------
# 6. WEBHOOK AND RAW INTERCEPT OVERRIDES
# ---------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    """Initializes webhook and forces registration of the core 'guest_message' payload string."""
    await telegram_app.initialize()
    if WEBHOOK_URL:
        # Forcing raw subscription parameter lists across Telegram engine endpoints
        allowed_updates_list = ["message", "edited_message", "callback_query", "guest_message"]
        await telegram_app.bot.set_webhook(
            url=f"https://{WEBHOOK_URL}/webhook",
            allowed_updates=allowed_updates_list
        )
        print(f"Webhook assigned with Guest Mode to: https://{WEBHOOK_URL}/webhook")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    """Intercepts and process raw payload buffers before standard wrapper handlers see them."""
    try:
        data = await request.json()
        print(f"Incoming Webhook Payload: {data}")  # Diagnostic visibility log

        # RAW INTERCEPT METHOD: Process guest updates directly from JSON structure
        guest_query_id = None
        chat_id = None
        text_prompt = ""

        # Check for direct guest_message payload array structures
        if "guest_message" in data:
            gm = data["guest_message"]
            guest_query_id = gm.get("guest_query_id")
            chat_id = gm.get("chat", {}).get("id")
            text_prompt = gm.get("text", "")
        # Fallback tracking wrapper structures
        elif "message" in data and "guest_query_id" in data["message"]:
            gm = data["message"]
            guest_query_id = gm.get("guest_query_id")
            chat_id = gm.get("chat", {}).get("id")
            text_prompt = gm.get("text", "")

        if guest_query_id and chat_id and text_prompt:
            # Strip target bot mentions cleanly
            bot_username = telegram_app.bot.username or ""
            clean_prompt = text_prompt.replace(f"@{bot_username}", "").strip()
            
            # Fetch AI inference context
            ai_reply = await process_with_gemini(clean_prompt)
            
            # Formulate full direct HTTP fallback requests to ensure delivery compatibility
            async with httpx.AsyncClient() as client:
                url = f"https://api.telegram.org/bot{TOKEN}/answerGuestQuery"
                payload = {
                    "guest_query_id": str(guest_query_id),
                    "chat_id": int(chat_id),
                    "text": f"🤖 *AI Assistant Guest Answer*:\n\n{ai_reply}",
                    "parse_mode": "Markdown"
                }
                res = await client.post(url, json=payload)
                print(f"Direct answerGuestQuery Response status: {res.status_code}, data: {res.text}")
            return {"status": "guest_processed_raw"}

        # Normal standard chat framework operational route mapping
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        
    except Exception as e:
        print(f"Webhook routing variance trace: {e}")
    return {"status": "ok"}

@app.get("/")
def health_check():
    return {"status": "online", "system": "guest_ready_multimodal"}