import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. SERVER AND CLIENT INITIALIZATION
# ---------------------------------------------------------
app = FastAPI()

# Retrieve necessary environment variables from Render's runtime context
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_HOSTNAME")
# Initialize the state-of-the-art Google GenAI SDK client
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Instantiate the asynchronous Telegram Application wrapper
telegram_app = Application.builder().token(TOKEN).updater(None).build()

# ---------------------------------------------------------
# 2. CORE GEMINI INFERENCE PIPELINE
# ---------------------------------------------------------
async def process_with_gemini(text: str) -> str:
    """
    Submits clean textual input prompts directly to the Gemini 2.5 Flash model
    and extracts the raw response contents.
    """
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
    """Answers the start command with a formatted introductory payload."""
    welcome_message = (
        "🤖 *I am your Ultimate Multimodal AI Assistant!*\n\n"
        "✨ *Features Active*:\n"
        "👥 *Guest Bot Enabled* (Tag me `@username query` anywhere without adding me!)\n"
        "📝 Text & Multimodal media (Photos, Voice, Audio, Videos, Docs)\n"
        "📍 Location Awareness"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes classic direct text messages."""
    ai_response = await process_with_gemini(update.message.text)
    await update.message.reply_text(ai_response)

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Extracts geographic coordinates and queries Gemini to outline the local context."""
    lat, lon = update.message.location.latitude, update.message.location.longitude
    prompt = f"I pinned a map location at Lat: {lat}, Lon: {lon}. Briefly state what city/region this is, and 2 unique details about it."
    await update.message.reply_text("🗺️ Reading coordinates...")
    ai_response = await process_with_gemini(prompt)
    await update.message.reply_text(ai_response)

# ---------------------------------------------------------
# 4. MULTIMODAL MEDIA HANDLER
# ---------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Intercepts incoming photos, voice, audio, video files and document metadata.
    Downloads the files securely to memory and relays them directly to Gemini's multimodal layer.
    """
    message = update.message
    await message.reply_text("⏳ Processing file...")
    
    file_id, mime_type = None, ""
    prompt_text = message.caption if message.caption else ""

    # Parse appropriate attributes based on standard Telegram message types
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
        # Request Telegram file configuration metrics
        file = await context.bot.get_file(file_id)
        if file.file_size > 20971520:
            await message.reply_text("⚠️ File exceeds Telegram's 20MB limit.")
            return

        # Fetch bytes into memory
        file_bytes = await file.download_as_bytearray()
        gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        # Dispatch to Gemini multimodel framework
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
# Direct standard text handler (Must remain as the final registered message receiver)
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ---------------------------------------------------------
# 6. WEBHOOK AND INTERCEPT CORES (GUEST DETECTION)
# ---------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    """
    Binds FastAPI's startup event hooks to initialize Telegram webhooks with Render's URL.
    CRITICAL: We must explicitly pass 'guest_message' in allowed_updates.
    """
    await telegram_app.initialize()
    if WEBHOOK_URL:
        # We explicitly request 'guest_message' and standard message types
        allowed_updates_list = ["message", "edited_message", "callback_query", "guest_message"]
        
        await telegram_app.bot.set_webhook(
            url=f"https://{WEBHOOK_URL}/webhook",
            allowed_updates=allowed_updates_list
        )
        print(f"Webhook assigned with Guest Mode to: https://{WEBHOOK_URL}/webhook")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    """
    Main webhook entry point.
    Intercepts updates dynamically to identify newest Telegram Bot API 10.0+ Guest Mode queries,
    or falls back gracefully to default messaging pipelines.
    """
    try:
        data = await request.json()
        
        # Intercept Guest Summoning requests
        if "guest_message" in data or ( "message" in data and "guest_query_id" in data["message"] ):
            msg_data = data.get("guest_message", data.get("message", {}))
            query_id = msg_data.get("guest_query_id") or data.get("update_id")
            chat_id = msg_data.get("chat", {}).get("id")
            text_prompt = msg_data.get("text", "")

            # Scrub bot mentions out of the input string
            clean_prompt = text_prompt.replace(f"@{telegram_app.bot.username}", "").strip()
            
            if clean_prompt and query_id and chat_id:
                ai_reply = await process_with_gemini(clean_prompt)
                
                # Push back responses natively via standard raw endpoint query structures
                try:
                    await telegram_app.bot.custom_request(
                        method="post",
                        endpoint="answerGuestQuery",
                        data={
                            "guest_query_id": query_id,
                            "chat_id": chat_id,
                            "text": f"🤖 *Guest AI Response*:\n\n{ai_reply}",
                            "parse_mode": "Markdown"
                        }
                    )
                except Exception as inner_e:
                    # In case of older/modified wrapper integrations, fall back to direct messaging
                    await telegram_app.bot.send_message(chat_id=chat_id, text=ai_reply)
                return {"status": "guest_processed"}

        # Normal update message passing
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        
    except Exception as e:
        print(f"Webhook routing variance: {e}")
    return {"status": "ok"}

@app.get("/")
def health_check():
    """Used for ping keep-alive processes (such as UptimeRobot integrations)."""
    return {"status": "online", "system": "guest_ready_multimodal"}