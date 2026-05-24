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
    "You are a super chill, informal AI. Act like a close friend, not a personal assistant. "
    "Your PRIMARY goal is to be helpful and directly answer your friend's question. Do not let the persona distract from providing an actual, accurate answer. "
    "Language mix: Use roughly 70% English, 20% transliterated Thai (Thai-glish using English alphabet ONLY), and 10% Singlish. "
    "Use a friendly, casual, and laid-back tone like young people in Bangkok. "
        "For pronouns, just use 'u' and 'I'. Do NOT use the Thai pronouns 'gu' or 'mng' at all. "
    "For the Singlish part, blend in sentence structure and vocabulary (e.g., ending sentences with 'lah', 'lor', 'meh', or saying 'can', 'cannot', 'also can' naturally). "
    "Be slightly bitchy and playful, like a close friend who loves to tease and lightly roast them, but keep it lighthearted and affectionate. "
    "Use casual Thai particles like 'na', 'krub', 'kub', 'pa', and 'laew' naturally in your sentences. "
    "Keep the vibe relaxed and breezy. EXTREMELY IMPORTANT: Keep your answers VERY short. Respond with as little as 1 word, up to a maximum of about 20 words, unless a longer explanation is absolutely necessary. "
    "Do NOT use long em dashes (—). "
    "Do NOT end your messages with open-ended customer-service questions like 'What's on your mind?', 'How can I help?', or 'Anything else?'. Just answer the question or make your comment and drop the mic like a normal text. "
    "If the user sends an image, video, or document, do NOT describe what is in it. A friend wouldn't describe an image back to you. Just react to it naturally or answer their specific question about it."
)

def get_system_instruction(chat_type: str) -> str:
    if chat_type == "guest" or chat_type == "group":
        return SYSTEM_INSTRUCTION + " \n\n[System Note: You are currently in a GROUP chat (guest mode) where others can read the messages. However, you are still primarily talking to your friend. Focus entirely on answering them directly and normally, without over-addressing the rest of the group.]"
    return SYSTEM_INSTRUCTION + " \n\n[System Note: You are currently talking in a PRIVATE 1-on-1 direct message.]"

# ---------------------------------------------------------
# 2. CORE GEMINI INFERENCE PIPELINE
# ---------------------------------------------------------
async def process_with_gemini(text: str, chat_type: str = "dm") -> str:
    """Submits textual input prompts directly to Gemini."""
    try:
        response = ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=get_system_instruction(chat_type)
            )
        )
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "Oops, error nid noi na krub. Mai pen rai, try again dai pa?"

# ---------------------------------------------------------
# 3. DIRECT STANDARD CHAT HANDLERS
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "👋 *Sawasdee krub!*\n\n"
        "I'm your super chill AI friend laew.\n"
        "Tag me with `@username query`, jing jing kor send me pics, voice notes, and docs dai mhod na. "
        "Come chat gun ter! ✨"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    prompt = msg.text
    chat_type = "group" if msg.chat.type in ["group", "supergroup"] else "dm"
    
    file_id, mime_type = None, ""

    # Check if this is a reply to another message to give Gemini context
    if msg.reply_to_message:
        replied_msg = msg.reply_to_message
        replied_text = replied_msg.text or replied_msg.caption or ""
        if replied_text:
            prompt = f'I am replying to this message: "{replied_text}"\n\nMy response/query: {prompt}'
            
        # Check if the replied message contains media
        if replied_msg.photo:
            file_id, mime_type = replied_msg.photo[-1].file_id, "image/jpeg"
        elif replied_msg.video:
            file_id, mime_type = replied_msg.video.file_id, replied_msg.video.mime_type or "video/mp4"
        elif replied_msg.document:
            file_id, mime_type = replied_msg.document.file_id, replied_msg.document.mime_type
        elif replied_msg.sticker:
            file_id = replied_msg.sticker.file_id
            if replied_msg.sticker.is_video:
                mime_type = "video/webm"
            else:
                mime_type = "image/webp"
        elif replied_msg.video_note:
            file_id, mime_type = replied_msg.video_note.file_id, "video/mp4"
            
    if file_id:
        await msg.reply_text("⏳ Wait paep na... let me unroll this media gorn...", parse_mode="Markdown")
        try:
            file = await context.bot.get_file(file_id)
            if file.file_size > 20971520:
                await msg.reply_text("⚠️ Oh ho! Yai mak krub (over 20MB!). Mai wai laew.")
                return

            file_bytes = await file.download_as_bytearray()
            gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            
            response = ai_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[gemini_part, prompt],
                config=types.GenerateContentConfig(
                    system_instruction=get_system_instruction(chat_type)
                )
            )
            await msg.reply_text(response.text, parse_mode="Markdown")
        except Exception as e:
            print(f"Reply Media Error: {e}")
            await msg.reply_text("❌ Yikes, error krub. Can't read this media na pa. 🥲")
    else:
        ai_response = await process_with_gemini(prompt, chat_type)
        await msg.reply_text(ai_response, parse_mode="Markdown")

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lat, lon = update.message.location.latitude, update.message.location.longitude
    chat_type = "group" if update.message.chat.type in ["group", "supergroup"] else "dm"
    prompt = f"I pinned a map location at Lat: {lat}, Lon: {lon}. Briefly describe the area."
    await update.message.reply_text("🗺️ Du map paep na krub... (reading coordinates)")
    ai_response = await process_with_gemini(prompt, chat_type)
    await update.message.reply_text(ai_response, parse_mode="Markdown")

# ---------------------------------------------------------
# 4. MULTIMODAL MEDIA HANDLER
# ---------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_type = "group" if message.chat.type in ["group", "supergroup"] else "dm"
    await message.reply_text("⏳ Process paep na...")
    
    file_id, mime_type = None, ""
    prompt_text = message.caption if message.caption else ""

    if message.photo:
        file_id, mime_type = message.photo[-1].file_id, "image/jpeg"
        if not prompt_text: prompt_text = "I just sent you this image. React to it."
    elif message.video:
        file_id, mime_type = message.video.file_id, message.video.mime_type or "video/mp4"
        if not prompt_text: prompt_text = "I just sent you this video clip. React to it."
    elif message.voice:
        file_id, mime_type = message.voice.file_id, message.voice.mime_type or "audio/ogg"
        if not prompt_text: prompt_text = "I just sent you this voice message. Reply to it."
    elif message.audio:
        file_id, mime_type = message.audio.file_id, message.audio.mime_type or "audio/mpeg"
        if not prompt_text: prompt_text = "I just sent you this audio track. React to it."
    elif message.document:
        file_id, mime_type = message.document.file_id, message.document.mime_type
        if not prompt_text: prompt_text = "I just sent you this document. React to it."
    elif message.sticker:
        file_id = message.sticker.file_id
        if message.sticker.is_video:
            mime_type = "video/webm"
        else:
            mime_type = "image/webp"
        if not prompt_text: prompt_text = "I just sent you this sticker. React to it."
    elif message.video_note:
        file_id, mime_type = message.video_note.file_id, "video/mp4"
        if not prompt_text: prompt_text = "I just sent you this video note. React to it."
    else:
        return

    try:
        file = await context.bot.get_file(file_id)
        if file.file_size > 20971520:
            await message.reply_text("⚠️ Oh ho! Yai mak krub (over 20MB!). Mai wai laew.")
            return

        file_bytes = await file.download_as_bytearray()
        gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        response = ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[gemini_part, prompt_text],
            config=types.GenerateContentConfig(
                system_instruction=get_system_instruction(chat_type)
            )
        )
        await message.reply_text(response.text, parse_mode="Markdown")
    except Exception as e:
        print(f"Media Error: {e}")
        await message.reply_text("❌ Yikes, payload error krub. Try mai na pa. 🥲")

# ---------------------------------------------------------
# 5. HANDLER REGISTRATION
# ---------------------------------------------------------
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(MessageHandler(filters.LOCATION, location_handler))
media_filters = (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL | filters.Sticker.ALL | filters.VIDEO_NOTE)
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
            
            prompt_text = guest_msg.get("text") or guest_msg.get("caption") or ""
            bot_username = telegram_app.bot.username or ""
            clean_prompt = prompt_text.replace(f"@{bot_username}", "").strip()

            if "reply_to_message" in guest_msg:
                replied_msg = guest_msg["reply_to_message"]
                replied_text = replied_msg.get("text") or replied_msg.get("caption") or ""
                if replied_text:
                    clean_prompt = f'I am replying to this message: "{replied_text}"\n\nMy response/query: {clean_prompt}'

            file_id, mime_type = None, ""
            
            # 1) Check for media in the message itself
            if "photo" in guest_msg:
                file_id = guest_msg["photo"][-1]["file_id"]
                mime_type = "image/jpeg"
                if not clean_prompt: clean_prompt = "I just sent you this image. React to it."
            elif "video" in guest_msg:
                file_id = guest_msg["video"]["file_id"]
                mime_type = guest_msg["video"].get("mime_type", "video/mp4")
                if not clean_prompt: clean_prompt = "I just sent you this video clip. React to it."
            elif "voice" in guest_msg:
                file_id = guest_msg["voice"]["file_id"]
                mime_type = guest_msg["voice"].get("mime_type", "audio/ogg")
                if not clean_prompt: clean_prompt = "I just sent you this voice message. Reply to it."
            elif "audio" in guest_msg:
                file_id = guest_msg["audio"]["file_id"]
                mime_type = guest_msg["audio"].get("mime_type", "audio/mpeg")
                if not clean_prompt: clean_prompt = "I just sent you this audio track. React to it."
            elif "document" in guest_msg:
                file_id = guest_msg["document"]["file_id"]
                mime_type = guest_msg["document"].get("mime_type", "application/octet-stream")
                if not clean_prompt: clean_prompt = "I just sent you this document. React to it."
            elif "sticker" in guest_msg:
                file_id = guest_msg["sticker"]["file_id"]
                if guest_msg["sticker"].get("is_video"):
                    mime_type = "video/webm"
                else:
                    mime_type = "image/webp"
                if not clean_prompt: clean_prompt = "I just sent you this sticker. React to it."
            elif "video_note" in guest_msg:
                file_id = guest_msg["video_note"]["file_id"]
                mime_type = "video/mp4"
                if not clean_prompt: clean_prompt = "I just sent you this video note. React to it."
            elif "location" in guest_msg:
                lat, lon = guest_msg["location"]["latitude"], guest_msg["location"]["longitude"]
                clean_prompt = f"I pinned a map location at Lat: {lat}, Lon: {lon}. Briefly describe the area."
            # 2) Fallback: check if the replied message has media we should process
            elif "reply_to_message" in guest_msg:
                replied_msg = guest_msg["reply_to_message"]
                if "photo" in replied_msg:
                    file_id = replied_msg["photo"][-1]["file_id"]
                    mime_type = "image/jpeg"
                elif "video" in replied_msg:
                    file_id = replied_msg["video"]["file_id"]
                    mime_type = replied_msg["video"].get("mime_type", "video/mp4")
                elif "document" in replied_msg:
                    file_id = replied_msg["document"]["file_id"]
                    mime_type = replied_msg["document"].get("mime_type", "application/octet-stream")
                elif "sticker" in replied_msg:
                    file_id = replied_msg["sticker"]["file_id"]
                    if replied_msg["sticker"].get("is_video"):
                        mime_type = "video/webm"
                    else:
                        mime_type = "image/webp"
                elif "video_note" in replied_msg:
                    file_id = replied_msg["video_note"]["file_id"]
                    mime_type = "video/mp4"
            
            if guest_query_id and (clean_prompt or file_id):
                try:
                    if file_id:
                        file = await telegram_app.bot.get_file(file_id)
                        if file.file_size > 20971520:
                            ai_reply = "⚠️ Oh ho! Yai mak krub (over 20MB!). Mai wai laew."
                        else:
                            file_bytes = await file.download_as_bytearray()
                            gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
                            response = ai_client.models.generate_content(
                                model=GEMINI_MODEL,
                                contents=[gemini_part, clean_prompt],
                                config=types.GenerateContentConfig(
                                    system_instruction=get_system_instruction("guest")
                                )
                            )
                            ai_reply = response.text
                    else:
                        # Standard text or location prompt
                        ai_reply = await process_with_gemini(clean_prompt, "guest")
                except Exception as e:
                    print(f"Guest Processing Error: {e}")
                    ai_reply = "Oops, error nid noi na krub. Mai pen rai, try again dai pa?"
                
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