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

# Fetch environment variables required for Render and API access
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_HOSTNAME")
# Initialize the new Google GenAI SDK client
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Build the Telegram App framework (Updater is None since we use Webhooks)
telegram_app = Application.builder().token(TOKEN).updater(None).build()

# ---------------------------------------------------------
# 2. TELEGRAM COMMAND HANDLERS
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greets the user and explains the bot's capabilities."""
    welcome_message = (
        "🤖 *I am your Ultimate Multimodal AI!*\n\n"
        "Here is what you can send me:\n"
        "📝 Text & Questions\n"
        "📸 Photos & Images\n"
        "🎤 Voice Notes (I'll listen and reply!)\n"
        "🎵 Audio Files\n"
        "🎥 Videos\n"
        "📄 Documents (PDFs, txt, etc.)\n"
        "📍 Locations (Share your map pin!)\n\n"
        "Try sending me something!"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

# ---------------------------------------------------------
# 3. TEXT & LOCATION HANDLERS
# ---------------------------------------------------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes standard text messages."""
    user_text = update.message.text
    
    try:
        # Standard text generation
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text("Error processing text.")
        print(f"Text Error: {e}")

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Takes a GPS location and asks Gemini to describe the area."""
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    
    prompt = (
        f"I just pinned a geographic location at Latitude: {lat}, Longitude: {lon}. "
        "What country/city is this likely in or near, and what are 3 interesting facts about this general region?"
    )
    
    await update.message.reply_text("🗺️ Analyzing coordinates...")
    
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text("Error analyzing location.")
        print(f"Location Error: {e}")

# ---------------------------------------------------------
# 4. UNIVERSAL MEDIA HANDLER (The Magic Function)
# ---------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dynamically handles Photos, Videos, Documents, Voice, and Audio.
    Downloads the file into memory and passes the raw bytes directly to Gemini.
    """
    message = update.message
    await message.reply_text("⏳ Processing your file...")
    
    file_id = None
    mime_type = ""
    prompt_text = message.caption if message.caption else ""

    # 1. Determine exactly what type of media the user sent
    if message.photo:
        # Telegram sends an array of photo sizes. [-1] is the highest resolution.
        file_id = message.photo[-1].file_id
        mime_type = "image/jpeg"
        if not prompt_text:
            prompt_text = "Describe this image in extreme detail."
            
    elif message.video:
        file_id = message.video.file_id
        mime_type = message.video.mime_type or "video/mp4"
        if not prompt_text:
            prompt_text = "Analyze this video and summarize what happens in it."
            
    elif message.voice:
        file_id = message.voice.file_id
        mime_type = message.voice.mime_type or "audio/ogg"
        if not prompt_text:
            prompt_text = "Please transcribe this voice note and answer any questions asked in it."
            
    elif message.audio:
        file_id = message.audio.file_id
        mime_type = message.audio.mime_type or "audio/mpeg"
        if not prompt_text:
            prompt_text = "Listen to this audio file and summarize it."
            
    elif message.document:
        file_id = message.document.file_id
        mime_type = message.document.mime_type
        if not prompt_text:
            prompt_text = "Read this document and provide a comprehensive summary."

    else:
        await message.reply_text("Unsupported media type.")
        return

    try:
        # 2. Ask Telegram for the file metadata
        file = await context.bot.get_file(file_id)
        
        # Telegram Bot API limits standard downloads to 20MB. 
        # (20MB = 20 * 1024 * 1024 = 20971520 bytes)
        if file.file_size > 20971520:
            await message.reply_text("⚠️ This file is larger than the 20MB Telegram bot limit. Please send a smaller file.")
            return

        # 3. Download the file into RAM (bytearray)
        file_bytes = await file.download_as_bytearray()
        
        # 4. Package the bytes and prompt for Gemini
        gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        # 5. Execute the Multimodal AI Request
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[gemini_part, prompt_text]
        )
        
        # Send the AI's thoughts back to the user
        await message.reply_text(response.text)
        
    except Exception as e:
        print(f"Media processing error: {e}")
        await message.reply_text(f"❌ Sorry, I encountered an error analyzing that file. Ensure it is a valid format.")

# ---------------------------------------------------------
# 5. ROUTING & HANDLER REGISTRATION
# ---------------------------------------------------------
# Command: /start
telegram_app.add_handler(CommandHandler("start", start_command))

# Filter: Locations
telegram_app.add_handler(MessageHandler(filters.LOCATION, location_handler))

# Filter: All Media (Photos, Video, Voice, Audio, Documents)
# The bitwise OR operator (|) combines these filters together
media_filters = (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL)
telegram_app.add_handler(MessageHandler(media_filters, media_handler))

# Filter: Standard Text (Must be registered last to not accidentally catch media captions)
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ---------------------------------------------------------
# 6. FASTAPI WEBHOOK CONFIGURATION
# ---------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    """Initializes the bot and binds the webhook to the Render URL."""
    await telegram_app.initialize()
    if WEBHOOK_URL:
        # Inform Telegram to push messages to our Render endpoint
        await telegram_app.bot.set_webhook(url=f"https://{WEBHOOK_URL}/webhook")
        print(f"System Online: Webhook bound to https://{WEBHOOK_URL}/webhook")
    else:
        print("WARNING: RENDER_EXTERNAL_HOSTNAME not found. Webhook not set.")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    """The intake pipe for all messages pushed from Telegram."""
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        # Push the update into the application framework
        await telegram_app.process_update(update)
    except Exception as e:
        print(f"Webhook ingestion error: {e}")
    return {"status": "ok"}

@app.get("/")
def health_check():
    """Keeps the bot awake on Render when pinged by UptimeRobot."""
    return {"status": "online", "system": "multimodal_active"}