import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from google import genai

app = FastAPI()

# Read production configurations from environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Render provides its public URL dynamically via this variable
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_HOSTNAME") 
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

telegram_app = Application.builder().token(TOKEN).updater(None).build()

async def start_command(update: Update, context):
    await update.message.reply_text("Hello! I am your 24/7 cloud-hosted AI assistant. Fire away with any questions!")

async def message_handler(update: Update, context):
    if not update.message or not update.message.text:
        return
        
    user_text = update.message.text
    
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text("Sorry, I encountered an error computing that response.")
        print(f"Error logic trace: {e}")

telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(url=f"https://{WEBHOOK_URL}/webhook")
    print(f"Webhook securely routed to: https://{WEBHOOK_URL}/webhook")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
    except Exception as e:
        print(f"Webhook ingestion failure: {e}")
    return {"status": "optimized"}

@app.get("/")
def health_check():
    """This route is crucial—it allows us to ping the server to keep it awake!"""
    return {"status": "online", "system": "healthy"}