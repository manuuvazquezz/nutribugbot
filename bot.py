import os
import json
import base64
import logging
import tempfile
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from google.cloud.dialogflowcx_v3 import SessionsClient, TextInput, QueryInput, DetectIntentRequest

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


def extract_chips(payload_struct) -> list[str]:
    """Extract suggestion chips from Dialogflow CX payload."""
    chips = []
    try:
        payload = type(payload_struct).to_dict(payload_struct)
        rich = payload.get("richContent", [])
        for block in rich:
            for item in block:
                if item.get("type") == "chips":
                    for opt in item.get("options", []):
                        if opt.get("text"):
                            chips.append(opt["text"])
    except Exception as e:
        log.warning(f"Could not parse chips: {e}")
    return chips


def parse_dialogflow_response(response):
    """Return (text_lines, chips_list) from a DetectIntent response."""
    texts = []
    chips = []
    for msg in response.query_result.response_messages:
        if msg.text.text:
            texts.extend(msg.text.text)
        if msg.payload:
            chips.extend(extract_chips(msg.payload))
    return "\n".join(t for t in texts if t), chips


def get_dialogflow_response(text: str, session_id: str):
    session = client.session_path(PROJECT_ID, LOCATION, AGENT_ID, session_id)
    request = DetectIntentRequest(
        session=session,
        query_input=QueryInput(text=TextInput(text=text), language_code="en"),
    )
    response = client.detect_intent(request=request)
    return parse_dialogflow_response(response)


async def send_reply(update: Update, text: str, chips: list[str]):
    if not text:
        text = "Sorry, I didn't understand that."

    if chips:
        # Build rows of up to 3 buttons each
        rows = [chips[i:i+3] for i in range(0, len(chips), 3)]
        keyboard = [[KeyboardButton(c) for c in row] for row in rows]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        await update.message.reply_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=ReplyKeyboardRemove())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    session_id = str(update.effective_chat.id)
    log.info(f"User {session_id}: {text}")
    try:
        reply_text, chips = get_dialogflow_response(text, session_id)
        log.info(f"Reply: {reply_text[:80]} | Chips: {chips}")
    except Exception as e:
        log.error(f"Dialogflow error: {e}", exc_info=True)
        reply_text, chips = "Sorry, something went wrong. Please try again.", []
    await send_reply(update, reply_text, chips)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.text = "hi"
    await handle_message(update, context)


app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", handle_start))
app.add_handler(MessageHandler(filters.TEXT, handle_message))

if WEBHOOK_URL:
    log.info(f"Starting webhook on port {PORT}")
    app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=WEBHOOK_URL)
else:
    log.info("Starting polling...")
    app.run_polling()
