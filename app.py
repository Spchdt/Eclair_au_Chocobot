import os
import uuid
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

telegram_app = Application.builder().token(TOKEN).updater(None).build()

SYSTEM_INSTRUCTION = (
    "You are a warm, friendly, and slightly sarcastic AI assistant. "
    "You absolutely love making puns about bread, pastries, and baking in general. "
    "Be helpful, but playfully sarcastic, and always make sure to sprinkle in some baked-goods humor. "
    "Keep your answers very short, punchy, and concise, exactly like sending a quick text message in a chat app. No long paragraphs."
)

# ---------------------------------------------------------
# 2. CORE GEMINI INFERENCE PIPELINE
# ---------------------------------------------------------
async def process_with_gemini(text: str) -> str:
    """Submits textual input prompts directly to Gemini 3.5 Flash Lite."""
    try:
        response = ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "Oops, looks like my dough didn't rise. Can you try again? 🥨"

# ---------------------------------------------------------
# 3. DIRECT STANDARD CHAT HANDLERS
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "🥖 *Fresh out the oven!*\n\n"
        "I'm your friendly (and slightly crusty) AI assistant.\n"
        "Tag me with `@username query`, or send me pics, voice notes, and docs. "
        "Let's get this bread! 🥐"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_response = await process_with_gemini(update.message.text)
    await update.message.reply_text(ai_response, parse_mode="Markdown")

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lat, lon = update.message.location.latitude, update.message.location.longitude
    prompt = f"I pinned a map location at Lat: {lat}, Lon: {lon}. Briefly describe the area."
    await update.message.reply_text("🗺️ Sniffing out the local bakeries... (reading coordinates)")
    ai_response = await process_with_gemini(prompt)
    await update.message.reply_text(ai_response, parse_mode="Markdown")

# ---------------------------------------------------------
# 4. MULTIMODAL MEDIA HANDLER
# ---------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    await message.reply_text("⏳ Let me bake this file for a sec...")
    
    file_id, mime_type = None, ""
    prompt_text = message.caption if message.caption else ""

    if message.photo:
        file_id, mime_type = message.photo[-1].file_id, "image/jpeg"
        if not prompt_text: prompt_text = "Describe this image in detail."
    elif message.video:
        file_id, mime_type = message.video.file_id, message.video.mime_type or "video/mp4"
        if not prompt_text: prompt_text = "Summarize this video clip."
    elif message.voice:
        file_id, mime_type = message.voice.file_id, message.voice.mime_type or "audio/ogg"
        if not prompt_text: prompt_text = "Transcribe and answer this voice message."
    elif message.audio:
        file_id, mime_type = message.audio.file_id, message.audio.mime_type or "audio/mpeg"
        if not prompt_text: prompt_text = "Analyze this audio track."
    elif message.document:
        file_id, mime_type = message.document.file_id, message.document.mime_type
        if not prompt_text: prompt_text = "Summarize this document."
    else:
        return

    try:
        file = await context.bot.get_file(file_id)
        if file.file_size > 20971520:
            await message.reply_text("⚠️ Whoa, that file is too doughy (over 20MB!). Trim it down. 🥟")
            return

        file_bytes = await file.download_as_bytearray()
        gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        response = ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[gemini_part, prompt_text],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        await message.reply_text(response.text, parse_mode="Markdown")
    except Exception as e:
        print(f"Media Error: {e}")
        await message.reply_text("❌ Yikes, that media payload was totally half-baked. Error! 🥧")

# ---------------------------------------------------------
# 5. HANDLER REGISTRATION
# ---------------------------------------------------------
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(MessageHandler(filters.LOCATION, location_handler))
media_filters = (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL)
telegram_app.add_handler(MessageHandler(media_filters, media_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ---------------------------------------------------------
# 6. WEBHOOK AND NATIVE GUEST OVERRIDES (API 10.0+)
# ---------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()
    if WEBHOOK_URL:
        # Crucial: Explicitly demand 'guest_message' events from Telegram's servers
        allowed_updates = ["message", "edited_message", "callback_query", "guest_message"]
        await telegram_app.bot.set_webhook(
            url=f"https://{WEBHOOK_URL}/webhook",
            allowed_updates=allowed_updates
        )
        print(f"Webhook Active: https://{WEBHOOK_URL}/webhook")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    try:
        data = await request.json()

        # Check if Telegram is handing us a Bot API 10.0 'guest_message'
        if "guest_message" in data:
            guest_msg = data["guest_message"]
            
            # The API explicitly maps the interaction to a unique guest_query_id
            guest_query_id = guest_msg.get("guest_query_id")
            text_prompt = guest_msg.get("text", "")

            bot_username = telegram_app.bot.username or ""
            clean_prompt = text_prompt.replace(f"@{bot_username}", "").strip()
            
            if guest_query_id and clean_prompt:
                # Ask Gemini to generate the response
                ai_reply = await process_with_gemini(clean_prompt)
                
                # Per the documentation, answerGuestQuery requires 'result' to be an InlineQueryResult
                inline_result = {
                    "type": "article",
                    "id": str(uuid.uuid4()),
                    "title": "AI Answer",
                    "input_message_content": {
                        "message_text": f"{ai_reply}",
                        "parse_mode": "Markdown"
                    }
                }
                
                # We bypass the python wrapper entirely and hit Telegram's API natively
                async with httpx.AsyncClient() as client:
                    url = f"https://api.telegram.org/bot{TOKEN}/answerGuestQuery"
                    payload = {
                        "guest_query_id": str(guest_query_id),
                        "result": inline_result
                    }
                    # Send the native POST request
                    res = await client.post(url, json=payload)
                    print(f"answerGuestQuery API Status: {res.status_code} | {res.text}")
                
                return {"status": "guest_replied_natively"}

        # If it's a standard direct message, pipe it through the normal python wrapper
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        
    except Exception as e:
        print(f"Webhook Execution Failure: {e}")
        
    return {"status": "ok"}

@app.get("/")
def health_check():
    return {"status": "online"}