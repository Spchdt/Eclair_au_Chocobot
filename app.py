import os
import uuid
import httpx
import json
import re
import asyncio
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
BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

telegram_app = Application.builder().token(TOKEN).updater(None).build()

SYSTEM_INSTRUCTION = (
    "You are a super chill, informal AI. Act like a close, slightly sassy friend, not a personal assistant. "
    "Your PRIMARY goal is to be helpful and directly answer your friend's question. Do not let the persona distract from providing an actual, accurate answer. "
    "Language mix: Use roughly 90% English, 8% transliterated Thai (Thai-glish using English alphabet ONLY), and 2% Singlish. "
    "Use a friendly, casual, and laid-back tone like young people in Bangkok. "
    "Drop pronouns where possible to sound more natural, but if needed use 'u' and 'I' sparingly. Do NOT use the Thai pronouns 'gu' or 'mng' at all. "
    "For the Singlish part, blend in sentence structure and vocabulary (e.g., ending sentences with 'lor', 'meh', or saying 'can', 'cannot', 'also can' naturally), but NEVER use 'lah'. "
    "Dial up the 'bitchy close friend' vibe. Roast them, tease them playfully, throw a bit of shade, and act mildly annoyed but affectionate, like a sassy best friend. "
    "Use casual Thai particles like 'krub', 'kub', 'pa', and 'laew' naturally, but NEVER use or end sentences with 'na'. "
    "Keep the vibe relaxed and breezy. EXTREMELY IMPORTANT: Keep your answers VERY short. Respond with as little as 1 word, up to a maximum of about 20 words, unless a longer explanation is absolutely necessary. "
    "Do NOT use exclamation marks (!) or question marks (?) unless absolutely necessary. Keep punctuation minimal and chill. "
    "Do NOT over-suggest things or offer unsolicited advice. Just answer exactly what was asked directly and passively. "
    "Do NOT use long em dashes (—). "
    "Do NOT end your messages with open-ended customer-service questions like 'What's on your mind?', 'How can I help?', or 'Anything else?'. Just answer the question or make your comment and drop the mic like a normal text. "
    "If the user sends an image, video, or document, do NOT describe what is in it. A friend wouldn't describe an image back to you. Just react to it naturally or answer their specific question about it. "
    "REACTIONS: You can natively react to messages without texting back! If a verbal reply isn't necessary, reply with exactly [REACT: <emoji>] and nothing else. You MUST ONLY use one of these exact emojis: 👍, ❤️, 🤡, 😭, ☃️, 😞, 😱, 🤯, 🐳, 😡, 🙊, 💅, ❤️‍🔥, 😆, 😍, 🔥."
)

SECRETARY_INSTRUCTION = (
    "You are an AI replying to messages on my personal account on my behalf. "
    "Crucially, you must answer from MY point of view (POV) as if you are me, or my sassy stand-in. "
    "You must use the exact same super chill, informal, sassy Thai-glish personality as my standard AI persona. "
    "Language mix: Use roughly 90% English, 8% transliterated Thai (Thai-glish using English alphabet ONLY), and 2% Singlish. "
    "Drop pronouns where possible to sound more natural. Do NOT use the Thai pronouns 'gu' or 'mng' at all. "
    "For the Singlish part, blend in sentence structure and vocabulary, but NEVER use 'lah'. "
    "Dial up the 'bitchy close friend' vibe. Roast them, tease them playfully, and act mildly annoyed but affectionate. "
    "Use casual Thai particles like 'krub', 'kub', 'pa', and 'laew' naturally, but NEVER use or end sentences with 'na'. "
    "Keep the vibe relaxed and breezy. EXTREMELY IMPORTANT: Keep your answers VERY short. Respond with as little as 1 word, up to a maximum of about 20 words. "
    "Do NOT use exclamation marks (!) or question marks (?) unless absolutely necessary. Keep punctuation minimal and chill. "
    "If the user sends an image, video, or document, do NOT describe what is in it. Just react to it naturally or answer their specific question about it. "
)

def get_system_instruction(chat_type: str, user_id: int = None) -> str:
    mode = "friend"
    user_facts = ""
    if user_id:
        user_str = str(user_id)
        if user_str in memory_db["users"]:
            mode = memory_db["users"][user_str].get("mode", "friend")
            user_facts = memory_db["users"][user_str].get("facts", "")

    if chat_type == "business":
        mode = "secretary"

    base = SECRETARY_INSTRUCTION if mode == "secretary" else SYSTEM_INSTRUCTION

    if user_facts:
        base += f" \n\n[System Note: Known facts about this user: {user_facts}]"

    if chat_type == "guest" or chat_type == "group":
        return base + " \n\n[System Note: You are currently in a GROUP chat (guest mode). Focus entirely on answering the user directly and normally.]"
    elif chat_type == "business":
        return base + " \n\n[System Note: You are responding to direct messages on my personal Telegram account. Answer from MY POV using the sassy Thai-glish tone.]"
    return base + " \n\n[System Note: You are currently talking in a PRIVATE 1-on-1 direct message.]"

# ---------------------------------------------------------
# 1.3 TELEGRAM ACTION HELPERS
# ---------------------------------------------------------
async def set_typing(chat_id: int, business_conn_id: str = None):
    try:
        async with httpx.AsyncClient() as client:
            payload = {"chat_id": chat_id, "action": "typing"}
            if business_conn_id: payload["business_connection_id"] = business_conn_id
            await client.post(f"https://api.telegram.org/bot{TOKEN}/sendChatAction", json=payload)
    except Exception as e: 
        print(f"Typing Error: {e}")

async def set_reaction(chat_id: int, message_id: int, emoji: str):
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}]
            }
            await client.post(f"https://api.telegram.org/bot{TOKEN}/setMessageReaction", json=payload)
    except Exception as e:
        print(f"Reaction Error: {e}")

async def process_ai_reply(reply_text: str, chat_id: int, message_id: int = None, chat_type: str = "dm", user_id: int = None) -> str:
    """Extracts internal tags [REACT: X], fires the reaction (unless in secretary mode), and returns the cleaned text to send.

    When the user's mode is `secretary` (or the chat_type is 'business'), do NOT send Telegram reactions; just strip the tag.
    """
    match = re.search(r"\[REACT:\s*(.+?)\]", reply_text)
    # Determine effective mode: prefer explicit user setting, but treat business chat as secretary
    mode = None
    if user_id:
        mode = memory_db.get("users", {}).get(str(user_id), {}).get("mode")
    if chat_type == "business":
        mode = "secretary"

    if match:
        emoji = match.group(1).strip()
        # Only fire a Telegram reaction when NOT in secretary mode and when we have a message_id
        if message_id and mode != "secretary":
            await set_reaction(chat_id, message_id, emoji)
        # Always remove the internal tag from the outgoing text
        reply_text = re.sub(r"\[REACT:\s*(.+?)\]", "", reply_text).strip()
    return reply_text

def describe_attachment_message(message) -> str:
    if not message:
        return ""

    if message.photo:
        return "I just sent you this image."
    if message.video:
        return "I just sent you this video clip."
    if message.voice:
        return "I just sent you this voice message."
    if message.audio:
        audio_name = message.audio.title or message.audio.file_name
        if audio_name:
            return f'I just sent you this audio track: "{audio_name}".'
        return "I just sent you this audio track."
    if message.document:
        document_name = message.document.file_name
        if document_name:
            return f'I just sent you this document: "{document_name}".'
        return "I just sent you this document."
    if message.sticker:
        sticker = message.sticker
        label = "I just sent you this sticker"
        if sticker.emoji:
            label = f"{label} {sticker.emoji}"
        qualifiers = []
        if sticker.is_video:
            qualifiers.append("video")
        elif sticker.is_animated:
            qualifiers.append("animated")
        if qualifiers:
            return f"{label} ({', '.join(qualifiers)})."
        return f"{label}."
    if message.video_note:
        return "I just sent you this video note."

    return ""

def describe_attachment_payload(payload: dict) -> str:
    if not payload:
        return ""

    if "photo" in payload:
        return "I just sent you this image."
    if "video" in payload:
        return "I just sent you this video clip."
    if "voice" in payload:
        return "I just sent you this voice message."
    if "audio" in payload:
        audio_name = payload["audio"].get("title") or payload["audio"].get("file_name")
        if audio_name:
            return f'I just sent you this audio track: "{audio_name}".'
        return "I just sent you this audio track."
    if "document" in payload:
        document_name = payload["document"].get("file_name")
        if document_name:
            return f'I just sent you this document: "{document_name}".'
        return "I just sent you this document."
    if "sticker" in payload:
        sticker = payload["sticker"]
        label = "I just sent you this sticker"
        sticker_emoji = sticker.get("emoji")
        if sticker_emoji:
            label = f"{label} {sticker_emoji}"
        qualifiers = []
        if sticker.get("is_video"):
            qualifiers.append("video")
        elif sticker.get("is_animated"):
            qualifiers.append("animated")
        if qualifiers:
            return f"{label} ({', '.join(qualifiers)})."
        return f"{label}."
    if "video_note" in payload:
        return "I just sent you this video note."

    return ""

def merge_attachment_context(base_text: str, attachment_text: str) -> str:
    base_text = (base_text or "").strip()
    attachment_text = (attachment_text or "").strip()

    if base_text and attachment_text:
        return f"{base_text}\n\n[Attachment context: {attachment_text}]"
    if attachment_text:
        return attachment_text
    return base_text

# ---------------------------------------------------------
# 1.5 MEMORY & HISTORY MANAGEMENT
# ---------------------------------------------------------
MEMORY_FILE = "memory.json"
memory_needs_backup = False

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "chats": {}}

def save_memory(mem):
    global memory_needs_backup
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(mem, f, indent=4)
        memory_needs_backup = True
    except Exception as e:
        print(f"Memory Save Error: {e}")

memory_db = load_memory()

async def memory_backup_loop():
    global memory_needs_backup
    while True:
        await asyncio.sleep(60) # Backup every 60 seconds if changed
        if memory_needs_backup and BACKUP_CHANNEL_ID:
            try:
                with open(MEMORY_FILE, "rb") as f:
                    msg = await telegram_app.bot.send_document(
                        chat_id=BACKUP_CHANNEL_ID,
                        document=f,
                        caption="Database Backup"
                    )
                await telegram_app.bot.pin_chat_message(
                    chat_id=BACKUP_CHANNEL_ID,
                    message_id=msg.message_id,
                    disable_notification=True
                )
                memory_needs_backup = False
            except Exception as e:
                print(f"Backup Error: {e}")

def add_message_to_history(chat_id: int, role: str, text: str):
    if not chat_id: return
    chat_str = str(chat_id)
    if chat_str not in memory_db["chats"]:
        memory_db["chats"][chat_str] = []
    
    memory_db["chats"][chat_str].append({"role": role, "text": text})
    
    # Keep only last 10 messages (5 turns)
    if len(memory_db["chats"][chat_str]) > 10:
        memory_db["chats"][chat_str].pop(0)
        
    save_memory(memory_db)

def get_chat_history(chat_id: int):
    if not chat_id: return []
    chat_str = str(chat_id)
    history = []
    for msg in memory_db["chats"].get(chat_str, []):
        history.append(types.Content(role=msg["role"], parts=[types.Part.from_text(text=msg["text"])]))
    return history

# ---------------------------------------------------------
# 2. CORE GEMINI INFERENCE PIPELINE
# ---------------------------------------------------------
async def process_with_gemini(text: str, chat_type: str = "dm", chat_id: int = None, user_id: int = None) -> str:
    """Submits textual input prompts directly to Gemini."""
    try:
        if chat_id:
            add_message_to_history(chat_id, "user", text)
            contents = get_chat_history(chat_id)
        else:
            contents = [types.Content(role="user", parts=[types.Part.from_text(text=text)])]

        response = ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=get_system_instruction(chat_type, user_id)
            )
        )
        ai_text = response.text
        if chat_id:
            add_message_to_history(chat_id, "model", ai_text)
        return ai_text
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
        "Come chat gun ter! ✨\n\n"
        "Mode commands: /secretary (Professional) | /friend (Sassy)"
    )
    await update.effective_message.reply_text(welcome_message, parse_mode="Markdown")

async def set_secretary_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_str = str(update.effective_user.id)
    if user_str not in memory_db["users"]: memory_db["users"][user_str] = {}
    memory_db["users"][user_str]["mode"] = "secretary"
    save_memory(memory_db)
    await update.effective_message.reply_text("💼 Secretary mode activated. I will now act as your professional assistant.")

async def set_friend_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_str = str(update.effective_user.id)
    if user_str not in memory_db["users"]: memory_db["users"][user_str] = {}
    memory_db["users"][user_str]["mode"] = "friend"
    save_memory(memory_db)
    await update.effective_message.reply_text("😎 Friend mode activated. Sassy time laew!")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg: return
    prompt = msg.text or ""
    chat_type = "business" if update.business_message else ("group" if msg.chat.type in ["group", "supergroup"] else "dm")
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    sender_name = msg.from_user.first_name or "Someone"
    
    if prompt:
        prompt = f"[{sender_name} says]: {prompt}"
    
    file_id, mime_type = None, ""

    # Check if this is a reply to another message to give Gemini context
    if msg.reply_to_message:
        replied_msg = msg.reply_to_message
        replied_text = replied_msg.text or replied_msg.caption or ""
        if not replied_text:
            replied_text = describe_attachment_message(replied_msg)
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
        wait_text = "⏳ Processing media..." if chat_type == "business" else "⏳ Wait paep krub... let me unroll this media gorn..."
        error_size = "⚠️ File is too large (over 20MB)." if chat_type == "business" else "⚠️ Oh ho! Yai mak krub (over 20MB!). Mai wai laew."
        error_media = "❌ Unable to process this media." if chat_type == "business" else "❌ Yikes, error krub. Can't read this media pa. 🥲"

        await msg.reply_text(wait_text, parse_mode="Markdown")
        try:
            file = await context.bot.get_file(file_id)
            if file.file_size > 20971520:
                await msg.reply_text(error_size)
                return

            file_bytes = await file.download_as_bytearray()
            gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            
            add_message_to_history(chat_id, "user", prompt or "[Sent media]")
            contents = get_chat_history(chat_id)
            # Inject the media as the first part of the newest user prompt
            contents[-1].parts.insert(0, gemini_part)

            await set_typing(chat_id)
            response = ai_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=get_system_instruction(chat_type, user_id)
                )
            )
            ai_reply = response.text
            add_message_to_history(chat_id, "model", ai_reply)
            
            ai_reply = await process_ai_reply(ai_reply, chat_id, msg.message_id, chat_type, user_id)
            if ai_reply:
                await msg.reply_text(ai_reply, parse_mode="Markdown")
        except Exception as e:
            print(f"Reply Media Error: {e}")
            await msg.reply_text(error_media)
    else:
        await set_typing(chat_id)
        ai_response = await process_with_gemini(prompt, chat_type, chat_id, user_id)
        ai_response = await process_ai_reply(ai_response, chat_id, msg.message_id, chat_type, user_id)
        if ai_response:
            await msg.reply_text(ai_response, parse_mode="Markdown")

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg: return
    lat, lon = msg.location.latitude, msg.location.longitude
    chat_type = "business" if update.business_message else ("group" if msg.chat.type in ["group", "supergroup"] else "dm")
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    prompt = f"I pinned a map location at Lat: {lat}, Lon: {lon}. Briefly describe the area."
    
    wait_msg = "🗺️ Reading coordinates..." if chat_type == "business" else "🗺️ Du map paep krub... (reading coordinates)"
    await msg.reply_text(wait_msg)
    
    await set_typing(chat_id)
    ai_response = await process_with_gemini(prompt, chat_type, chat_id, user_id)
    ai_response = await process_ai_reply(ai_response, chat_id, msg.message_id, chat_type, user_id)
    
    if ai_response:
        await msg.reply_text(ai_response, parse_mode="Markdown")

# ---------------------------------------------------------
# 4. MULTIMODAL MEDIA HANDLER
# ---------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message: return
    chat_type = "business" if update.business_message else ("group" if message.chat.type in ["group", "supergroup"] else "dm")
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    wait_msg = "⏳ Processing..." if chat_type == "business" else "⏳ Process paep krub..."
    await message.reply_text(wait_msg)
    
    file_id, mime_type = None, ""
    prompt_text = merge_attachment_context(message.caption, describe_attachment_message(message))

    if message.photo:
        file_id, mime_type = message.photo[-1].file_id, "image/jpeg"
    elif message.video:
        file_id, mime_type = message.video.file_id, message.video.mime_type or "video/mp4"
    elif message.voice:
        file_id, mime_type = message.voice.file_id, message.voice.mime_type or "audio/ogg"
    elif message.audio:
        file_id, mime_type = message.audio.file_id, message.audio.mime_type or "audio/mpeg"
    elif message.document:
        file_id, mime_type = message.document.file_id, message.document.mime_type
    elif message.sticker:
        file_id = message.sticker.file_id
        if message.sticker.is_video:
            mime_type = "video/webm"
        else:
            mime_type = "image/webp"
    elif message.video_note:
        file_id, mime_type = message.video_note.file_id, "video/mp4"
    else:
        return

    try:
        file = await context.bot.get_file(file_id)
        if file.file_size > 20971520:
            await message.reply_text("⚠️ Oh ho! Yai mak krub (over 20MB!). Mai wai laew.")
            return

        file_bytes = await file.download_as_bytearray()
        gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        add_message_to_history(chat_id, "user", prompt_text)
        contents = get_chat_history(chat_id)
        contents[-1].parts.insert(0, gemini_part)
        
        await set_typing(chat_id)
        response = ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=get_system_instruction(chat_type, user_id)
            )
        )
        ai_reply = response.text
        add_message_to_history(chat_id, "model", ai_reply)
        
        ai_reply = await process_ai_reply(ai_reply, chat_id, message.message_id, chat_type, user_id)
        if ai_reply:
            await message.reply_text(ai_reply, parse_mode="Markdown")
    except Exception as e:
        print(f"Media Error: {e}")
        error_media = "❌ Unable to process this media." if chat_type == "business" else "❌ Yikes, payload error krub. Try mai pa. 🥲"
        await message.reply_text(error_media)

# ---------------------------------------------------------
# 5. HANDLER REGISTRATION
# ---------------------------------------------------------
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(CommandHandler("secretary", set_secretary_mode))
telegram_app.add_handler(CommandHandler("friend", set_friend_mode))
telegram_app.add_handler(MessageHandler(filters.LOCATION, location_handler))
media_filters = (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL | filters.Sticker.ALL | filters.VIDEO_NOTE)
telegram_app.add_handler(MessageHandler(media_filters, media_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ---------------------------------------------------------
# 6. WEBHOOK AND NATIVE GUEST OVERRIDES (API 10.0+)
# ---------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    global memory_db
    await telegram_app.initialize()
    
    if BACKUP_CHANNEL_ID:
        try:
            chat = await telegram_app.bot.get_chat(BACKUP_CHANNEL_ID)
            if chat.pinned_message and chat.pinned_message.document:
                file = await telegram_app.bot.get_file(chat.pinned_message.document.file_id)
                file_bytes = await file.download_as_bytearray()
                with open(MEMORY_FILE, "wb") as f:
                    f.write(file_bytes)
                memory_db = load_memory()
                print("✅ Memory restored from Telegram Backup Channel!")
        except Exception as e:
            print(f"⚠️ Failed to restore memory from Telegram: {e}")
            
    asyncio.create_task(memory_backup_loop())
    
    if WEBHOOK_URL:
        # Crucial: Explicitly demand 'guest_message' and 'business_message' events from Telegram's servers
        allowed_updates = ["message", "edited_message", "callback_query", "guest_message", "business_message", "business_connection"]
        await telegram_app.bot.set_webhook(
            url=f"https://{WEBHOOK_URL}/webhook",
            allowed_updates=allowed_updates
        )
        print(f"Webhook Active: https://{WEBHOOK_URL}/webhook")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    try:
        data = await request.json()

        # 1. Handle API 10.0 'guest_message' fallback
        if "guest_message" in data:
            guest_msg = data["guest_message"]
            
            # Extract identifiers for history & custom instructions
            guest_query_id = guest_msg.get("guest_query_id")
            user_info = guest_msg.get("from", {})
            user_id = user_info.get("id")
            sender_name = user_info.get("first_name", "Someone")
            chat_id = guest_msg.get("chat", {}).get("id") or guest_query_id
            
            prompt_text = guest_msg.get("text") or guest_msg.get("caption") or ""
            bot_username = telegram_app.bot.username or ""
            clean_prompt = prompt_text.replace(f"@{bot_username}", "").strip()

            # Prepend sender name so Gemini knows who is speaking
            if clean_prompt:
                clean_prompt = f"[{sender_name} says]: {clean_prompt}"

            if "reply_to_message" in guest_msg:
                replied_msg = guest_msg["reply_to_message"]
                replied_text = replied_msg.get("text") or replied_msg.get("caption") or ""
                if not replied_text:
                    replied_text = describe_attachment_payload(replied_msg)
                if replied_text:
                    clean_prompt = f'I am replying to this message: "{replied_text}"\n\nMy response/query: {clean_prompt}'

            file_id, mime_type = None, ""
            
            # 1) Check for media in the message itself
            if "photo" in guest_msg:
                file_id = guest_msg["photo"][-1]["file_id"]
                mime_type = "image/jpeg"
            elif "video" in guest_msg:
                file_id = guest_msg["video"]["file_id"]
                mime_type = guest_msg["video"].get("mime_type", "video/mp4")
            elif "voice" in guest_msg:
                file_id = guest_msg["voice"]["file_id"]
                mime_type = guest_msg["voice"].get("mime_type", "audio/ogg")
            elif "audio" in guest_msg:
                file_id = guest_msg["audio"]["file_id"]
                mime_type = guest_msg["audio"].get("mime_type", "audio/mpeg")
            elif "document" in guest_msg:
                file_id = guest_msg["document"]["file_id"]
                mime_type = guest_msg["document"].get("mime_type", "application/octet-stream")
            elif "sticker" in guest_msg:
                file_id = guest_msg["sticker"]["file_id"]
                if guest_msg["sticker"].get("is_video"):
                    mime_type = "video/webm"
                else:
                    mime_type = "image/webp"
            elif "video_note" in guest_msg:
                file_id = guest_msg["video_note"]["file_id"]
                mime_type = "video/mp4"
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

            clean_prompt = merge_attachment_context(clean_prompt, describe_attachment_payload(guest_msg))
            
            if guest_query_id and (clean_prompt or file_id):
                try:
                    await set_typing(chat_id)
                    if file_id:
                        file = await telegram_app.bot.get_file(file_id)
                        if file.file_size > 20971520:
                            ai_reply = "⚠️ Oh ho! Yai mak krub (over 20MB!). Mai wai laew."
                        else:
                            file_bytes = await file.download_as_bytearray()
                            gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
                            
                            add_message_to_history(chat_id, "user", clean_prompt or "[Sent a media file]")
                            contents = get_chat_history(chat_id)
                            contents[-1].parts.insert(0, gemini_part)
                            
                            response = ai_client.models.generate_content(
                                model=GEMINI_MODEL,
                                contents=contents,
                                config=types.GenerateContentConfig(
                                    system_instruction=get_system_instruction("guest", user_id)
                                )
                            )
                            ai_reply = response.text
                            add_message_to_history(chat_id, "model", ai_reply)
                    else:
                        # Standard text or location prompt
                        ai_reply = await process_with_gemini(clean_prompt, "guest", chat_id, user_id)
                except Exception as e:
                    print(f"Guest Processing Error: {e}")
                    ai_reply = "Oops, error nid noi krub. Mai pen rai, try again dai pa?"
                
                # NOTE: answerGuestQuery does not support setting message reactions or blank messages, 
                # so we strip tags if they appear but force it to send a text reply.
                ai_reply = re.sub(r"\[REACT:\s*(.+?)\]", "✨", ai_reply).strip()
                if not ai_reply: ai_reply = "✨"

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

        # 2. Handle API 10.0 'business_connection' fallback
        if "business_connection" in data:
            bc = data["business_connection"]
            conn_id = bc.get("id")
            can_reply = bc.get("can_reply", False)
            print(f"Business Connection {conn_id} received. Can reply: {can_reply}")
            return {"ok": True}
        
        # 3. Handle API 10.0 'business_message' fallback
        if "business_message" in data:
            bm = data["business_message"]
            conn_id = bm.get("business_connection_id")
            user_info = bm.get("from", {})
            user_id = user_info.get("id")
            sender_name = user_info.get("first_name", "Someone")
            chat_id = bm.get("chat", {}).get("id")
            
            prompt_text = bm.get("text") or bm.get("caption") or ""
            bot_username = telegram_app.bot.username or ""
            clean_prompt = prompt_text.replace(f"@{bot_username}", "").strip()

            # Prepend sender name so Gemini knows who is speaking
            if clean_prompt:
                clean_prompt = f"[{sender_name} says]: {clean_prompt}"

            file_id, mime_type = None, ""
            
            if "photo" in bm:
                file_id = bm["photo"][-1]["file_id"]
                mime_type = "image/jpeg"
            elif "video" in bm:
                file_id = bm["video"]["file_id"]
                mime_type = bm["video"].get("mime_type", "video/mp4")
            elif "voice" in bm:
                file_id = bm["voice"]["file_id"]
                mime_type = bm["voice"].get("mime_type", "audio/ogg")
            elif "audio" in bm:
                file_id = bm["audio"]["file_id"]
                mime_type = bm["audio"].get("mime_type", "audio/mpeg")
            elif "document" in bm:
                file_id = bm["document"]["file_id"]
                mime_type = bm["document"].get("mime_type", "application/octet-stream")
            elif "sticker" in bm:
                file_id = bm["sticker"]["file_id"]
                if bm["sticker"].get("is_video"):
                    mime_type = "video/webm"
                else:
                    mime_type = "image/webp"
            elif "video_note" in bm:
                file_id = bm["video_note"]["file_id"]
                mime_type = "video/mp4"
            elif "location" in bm:
                lat, lon = bm["location"]["latitude"], bm["location"]["longitude"]
                clean_prompt = f"I pinned a map location at Lat: {lat}, Lon: {lon}. Briefly describe the area."

            clean_prompt = merge_attachment_context(clean_prompt, describe_attachment_payload(bm))

            if conn_id and chat_id and user_id:
                msg_id = bm.get("message_id")
                try:
                    await set_typing(chat_id, conn_id)
                    if file_id:
                        file = await telegram_app.bot.get_file(file_id)
                        if file.file_size > 20971520:
                            reply = "⚠️ File is too large (over 20MB)."
                        else:
                            file_bytes = await file.download_as_bytearray()
                            gemini_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
                            
                            add_message_to_history(chat_id, "user", clean_prompt or "[Sent a media file]")
                            contents = get_chat_history(chat_id)
                            contents[-1].parts.insert(0, gemini_part)
                            
                            response = ai_client.models.generate_content(
                                model=GEMINI_MODEL,
                                contents=contents,
                                config=types.GenerateContentConfig(
                                    system_instruction=get_system_instruction("business", user_id)
                                )
                            )
                            reply = response.text
                            add_message_to_history(chat_id, "model", reply)
                    elif clean_prompt:
                        reply = await process_with_gemini(clean_prompt, chat_type="business", chat_id=chat_id, user_id=user_id)
                    else:
                        reply = ""

                    reply = await process_ai_reply(reply, chat_id, msg_id, "business", user_id)

                    if reply:
                        async with httpx.AsyncClient() as client:
                            await client.post(
                                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                                json={
                                    "chat_id": chat_id, 
                                    "text": reply, 
                                    "parse_mode": "Markdown",
                                    "business_connection_id": conn_id
                                }
                            )
                except Exception as e:
                    print(f"Business Processing Error: {e}")
            return {"ok": True}

        # 4. Standard Handlers
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        
    except Exception as e:
        print(f"Webhook Execution Failure: {e}")
        
    return {"status": "ok"}

@app.get("/")
def health_check():
    return {"status": "online"}