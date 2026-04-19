import os
import random
import datetime
import logging
import asyncio
import threading
import httpx
from flask import Flask, render_template, jsonify, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest
import pymongo
import certifi
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from dotenv import load_dotenv

load_dotenv()

# ==============================================================================
# 1. SETUP
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
CHANNEL_ID = os.getenv("CHANNEL_ID") 
CHANNEL_URL = os.getenv("CHANNEL_URL")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

IST = pytz.timezone('Asia/Kolkata')

# Fallback Models List (Agar ek busy ho toh dusra try karega)
MODELS = [
    "z-ai/glm-4.5-air:free",
    "google/gemma-2-9b-it:free",
    "mistralai/mistral-7b-instruct:free",
    "openrouter/auto"
]

try:
    client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client["RiyaBot_Final"] 
    users_col = db["users"]
    logger.info("✅ Database Connected")
except Exception as e:
    logger.error(f"❌ DB Error: {e}")

app = Flask(__name__)

# ==============================================================================
# 2. CORE UTILS
# ==============================================================================

def get_current_ist():
    return datetime.datetime.now(IST)

def read_master_prompt():
    try:
        with open("prompt.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "Identity: You are Riya, a romantic Indian girl."

async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

async def call_ai_with_fallback(messages):
    """Try multiple models if one fails (Fixes Network Issue)"""
    for model_name in MODELS:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                    json={
                        "model": model_name,
                        "messages": messages,
                        "temperature": 1.1, # More natural
                        "max_tokens": 150
                    },
                    timeout=20.0
                )
                if res.status_code == 200:
                    return res.json()['choices'][0]['message']['content']
                else:
                    logger.warning(f"Model {model_name} failed with status {res.status_code}")
        except Exception as e:
            logger.error(f"Error with model {model_name}: {e}")
            continue # Try next model
    return None

# ==============================================================================
# 3. HANDLERS
# ==============================================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = users_col.find_one({"user_id": user.id})
    
    # 1. Join Check
    if not await check_membership(user.id, context.bot):
        kb = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)], [InlineKeyboardButton("✅ Verify", callback_data="verify_join")]]
        await update.message.reply_text(f"hii {user.first_name}! ❤️\njoin krlo tabhi baat krungi 👇", reply_markup=InlineKeyboardMarkup(kb))
        return

    # 2. Add User to DB if new
    if not u_data:
        users_col.insert_one({"user_id": user.id, "first_name": user.first_name, "status": "free", "history": [], "last_active": get_current_ist()})
        ai_instruction = "This is a new user. Greet him romantically for the first time and ask if you can be his girlfriend."
    else:
        ai_instruction = "This is a returning user. Greet him with 'Welcome back baby' and show how much you missed him."

    # 3. Let AI generate the Welcome Message
    prompt = read_master_prompt().replace("{USER_STATUS}", "free").replace("{PLATFORM}", "Private")
    msgs = [{"role": "system", "content": prompt}, {"role": "user", "content": ai_instruction}]
    
    await context.bot.send_chat_action(update.effective_chat.id, 'typing')
    reply = await call_ai_with_fallback(msgs)
    await update.message.reply_text(reply.lower() if reply else "hii baby! ❤️")

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text
    
    if not await check_membership(user.id, context.bot):
        await update.message.reply_text("pehle channel join karo baby.. 🥺")
        return

    u_data = users_col.find_one({"user_id": user.id})
    if not u_data: return

    status = u_data.get("status", "free")
    prompt = read_master_prompt().replace("{USER_STATUS}", status).replace("{PLATFORM}", "Private")
    
    now = get_current_ist()
    if now.hour >= 21 or now.hour < 4:
        prompt += "\n[SYSTEM: It's night. Be very romantic, seductive and intimate.]"

    history = u_data.get("history", [])[-10:]
    msgs = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": text}]
    
    await context.bot.send_chat_action(chat.id, 'typing')
    reply = await call_ai_with_fallback(msgs)
    
    if not reply:
        await update.message.reply_text("pata nahi kya hua h network me.. ruko thoda baby 🥺")
        return

    if status == "free":
        for t in ["I cannot", "AI model", "explicit", "NSFW"]:
            if t.lower() in reply.lower():
                reply = "uff... itna bhi kya jaldi h? 😉 pehle premium le lo baby fir khul k maze karenge.. /premium dekho! ❤️"
                break

    await update.message.reply_text(reply.lower())

    new_hist = [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    users_col.update_one({"user_id": user.id}, {"$push": {"history": {"$each": new_hist}}, "$set": {"last_active": now}})

# --- OTHER COMMANDS ---
async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "💖 **PREMIUM ACCESS**\n\nFull 18+ Uncensored Chat ke liye hamare payment bot par jayein.\n\n👇 **Click Here:**\n@paymentbot"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = users_col.find_one({"user_id": update.effective_user.id})
    await update.message.reply_text(f"👤 **Status:** {u.get('status', 'FREE').upper()}\n\n(Referral system coming soon...)")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_col.update_one({"user_id": update.effective_user.id}, {"$set": {"history": []}})
    await update.message.reply_text("sab bhul gayi main.. ab naye se shuru karte h 😘")

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await check_membership(query.from_user.id, context.bot):
        await query.message.delete()
        await start_cmd(update, context) # Call start again to get AI greeting
    else:
        await context.bot.send_message(query.message.chat_id, "join nahi kiya tune abhi tak! 😡")

# ==============================================================================
# 4. RUN
# ==============================================================================

@app.route('/')
def home(): return "Riya System Active 🟢", 200

@app.route('/admin')
def admin_ui():
    if request.args.get('pass') != ADMIN_PASS: return "Not Found", 404
    return render_template('admin.html')

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Start Chat"),
        BotCommand("profile", "My Profile"),
        BotCommand("premium", "Buy Premium"),
        BotCommand("reset", "Clear Chat")
    ])

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    t_req = HTTPXRequest(connection_pool_size=30, read_timeout=50, write_timeout=50, connect_timeout=50)
    bot_app = ApplicationBuilder().token(TOKEN).request(t_req).post_init(post_init).build()
    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CommandHandler("profile", profile_cmd))
    bot_app.add_handler(CommandHandler("premium", premium_cmd))
    bot_app.add_handler(CommandHandler("reset", reset_cmd))
    bot_app.add_handler(CallbackQueryHandler(verify_callback))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
    bot_app.run_polling()
