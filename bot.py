import os
import base64
import logging
import tempfile
import asyncio
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from google.cloud.dialogflowcx_v3 import SessionsClient, TextInput, QueryInput, DetectIntentRequest
from google.protobuf.json_format import MessageToDict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
PROJECT_ID = os.environ["GCP_PROJECT_ID"]
AGENT_ID = os.environ["DIALOGFLOW_AGENT_ID"]
LOCATION = os.environ["DIALOGFLOW_LOCATION"]
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
if creds_b64:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    tmp.write(base64.b64decode(creds_b64).decode())
    tmp.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name
else:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "service_account.json"
    )

api_endpoint = f"{LOCATION}-dialogflow.googleapis.com" if LOCATION != "global" else "dialogflow.googleapis.com"
client = SessionsClient(client_options={"api_endpoint": api_endpoint})


def extract_chips(payload) -> list[str]:
    """Extract suggestion chips using MessageToDict for correct protobuf parsing."""
    chips = []
    try:
        d = MessageToDict(payload)
        for block in d.get("richContent", []):
            for item in block:
                if item.get("type") == "chips":
                    for opt in item.get("options", []):
                        if opt.get("text"):
                            chips.append(opt["text"])
    except Exception as e:
        log.warning(f"Chip parse error: {e}")
    return chips


def call_dialogflow(text: str, session_id: str):
    """Returns (texts: list[str], chips: list[str])"""
    session = client.session_path(PROJECT_ID, LOCATION, AGENT_ID, session_id)
    request = DetectIntentRequest(
        session=session,
        query_input=QueryInput(text=TextInput(text=text), language_code="en"),
    )
    response = client.detect_intent(request=request)

    texts = []
    chips = []
    for msg in response.query_result.response_messages:
        if msg.text.text:
            t = msg.text.text[0].strip()
            if t:
                texts.append(t)
        if msg.payload:
            chips.extend(extract_chips(msg.payload))

    log.info(f"Texts({len(texts)}): {texts} | Chips({len(chips)}): {chips}")
    return texts, chips


async def handle_dialogflow(update: Update, text: str):
    session_id = str(update.effective_chat.id)
    try:
        texts, chips = call_dialogflow(text, session_id)
    except Exception as e:
        log.error(f"Dialogflow error: {e}", exc_info=True)
        await update.message.reply_text("Sorry, something went wrong. Please try again.")
        return

    if not texts:
        texts = ["Sorry, I didn't understand that."]

    # Build keyboard markup from chips
    if chips:
        rows = [chips[i:i+3] for i in range(0, len(chips), 3)]
        markup = ReplyKeyboardMarkup(
            [[KeyboardButton(c) for c in row] for row in rows],
            resize_keyboard=True,
            one_time_keyboard=False
        )
    else:
        markup = ReplyKeyboardRemove()

    # Send each text separately; attach keyboard only to the last message
    for i, t in enumerate(texts):
        is_last = (i == len(texts) - 1)
        await update.message.reply_text(t, reply_markup=markup if is_last else None)
        if not is_last:
            await asyncio.sleep(0.3)  # small delay so Telegram shows them as separate bubbles


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_dialogflow(update, update.message.text)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_dialogflow(update, "hi")


app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", handle_start))
app.add_handler(MessageHandler(filters.TEXT, handle_message))

if WEBHOOK_URL:
    log.info(f"Webhook mode on port {PORT}")
    app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=WEBHOOK_URL)
else:
    log.info("Polling mode")
    app.run_polling()
