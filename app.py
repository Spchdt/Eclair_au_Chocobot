import os
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from google import genai

app = FastAPI()

# Read production configurations from environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("SPACE_HOST")  # Injected dynamically by Hugging Face
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Construct the Telegram application framework without standard long-polling
telegram_app = Application.builder().token(TOKEN).updater(None).build()

async def start_command(update: Update, context):
    """Handles the /start command."""
    await update.message.reply_text("Hello! I am your 24/7 cloud-hosted AI assistant. Fire away with any questions!")

async def message_handler(update: Update, context):
    """Processes incoming text and returns the streaming response from Gemini."""
    if not update.message or not update.message.text:
        return
        
    user_text = update.message.text
    
    try:
        # Call the light, lightning-fast Gemini 2.5 Flash model
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text("Sorry, I encountered an error computing that response.")
        print(f"Error logic trace: {e}")

# Register Telegram structural handlers
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

@app.on_event("startup")
async def on_startup():
    """Executes when the server launches; configures the system webhook."""
    await telegram_app.initialize()
    # Explicitly direct Telegram servers to route messages to our secure Hugging Face domain
    await telegram_app.bot.set_webhook(url=f"https://{WEBHOOK_URL}/webhook")
    print(f"Webhook securely routed to: https://{WEBHOOK_URL}/webhook")

@app.post("/webhook")
async def webhook_endpoint(request: Request):
    """Direct webhook intake for real-time traffic from Telegram."""
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
    except Exception as e:
        print(f"Webhook ingestion failure: {e}")
    return {"status": "optimized"}

@app.get("/")
def health_check():
    """A baseline browser check to verify container status."""
    return {"status": "online", "system": "healthy"}