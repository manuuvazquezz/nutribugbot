import os
import json
import base64
import logging
import tempfile
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from google.cloud.dialogflowcx_v3 import SessionsClient, TextInput, QueryInput, DetectIntentRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
PROJECT_ID = os.environ["GCP_PROJECT_ID"]
AGENT_ID = os.environ["DIALOGFLOW_AGENT_ID"]
LOCATION = os.environ["DIALOGFLOW_LOCATION"]

# Support credentials from file or base64 env var (for cloud deployment)
creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
if creds_b64:
    creds_data = base64.b64decode(creds_b64).decode()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    tmp.write(creds_data)
    tmp.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name
else:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "service_account.json"
    )

api_endpoint = f"{LOCATION}-dialogflow.googleapis.com" if LOCATION != "global" else "dialogflow.googleapis.com"
client = SessionsClient(client_options={"api_endpoint": api_endpoint})
log.info("Dialogflow client ready")

def get_dialogflow_response(text: str, session_id: str) -> str:
    session = client.session_path(PROJECT_ID, LOCATION, AGENT_ID, session_id)
    request = DetectIntentRequest(
        session=session,
        query_input=QueryInput(text=TextInput(text=text), language_code="en"),
    )
    response = client.detect_intent(request=request)
    messages = [msg.text.text[0] for msg in response.query_result.response_messages if msg.text.text]
    return "\n".join(messages) if messages else "Sorry, I didn't understand that."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    session_id = str(update.effective_chat.id)
    try:
        reply = get_dialogflow_response(text, session_id)
    except Exception as e:
        log.error(f"Dialogflow error: {e}", exc_info=True)
        reply = "Sorry, something went wrong. Please try again."
    await update.message.reply_text(reply)

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.text = "hi"
    await handle_message(update, context)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", handle_start))
app.add_handler(MessageHandler(filters.TEXT, handle_message))
log.info("NutriBugBot running...")
app.run_polling()
