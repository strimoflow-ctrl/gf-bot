import os
import random
import datetime
import logging
import asyncio
import threading
import requests
import urllib.parse # Password fix ke liye
from flask import Flask, render_template, jsonify, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest
import pymongo
import certifi
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from dotenv import load_dotenv

load_dotenv()

# ==============================================================================
# 1. CONFIGURATION & LOGGING
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

# Database Connection Fix for Special Characters in Password
try:
    # Ye line password ke symbols ko encode kar degi taaki 'Port Error' na aaye
    mongo_client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where(), tlsAllowInvalidCertificates=True)
    db = mongo_client["RiyaBot_Final"] 
    users_col = db["users"]
    groups_col = db["groups"]
    codes_col = db["codes"]
    # Ping test
    mongo_client.admin.command('ping')
    logger.info("✅ Database Connected Successfully!")
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
        return "You are Riya, a romantic Indian girl. User is your boyfriend."

async def check_membership(user_id, bot):
    try:
        # Strict Admin Check: Bot must be admin in channel
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Member check error: {e}")
        return False

def call_ai(messages):
    try:
        res = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "z-ai/glm-4.5-air:free",
                "messages": messages,
                "temperature": 1.0,
                "max_tokens": 150
            },
            timeout=20
        )
        return res.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "network issue h baby.. ruko thoda 🥺"

# ==============================================================================
# 3. HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = users_col.find_one({"user_id": user.id})
    
    # Membership Check
    is_member = await check_membership(user.id, context.bot)
    
    if not is_member:
        kb = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)], [InlineKeyboardButton("✅ Verify", callback_data="verify_join")]]
        await update.message.reply_text(f"hii {user.first_name}! ❤️\njoin krlo tabhi baat krungi 👇", reply_markup=InlineKeyboardMarkup(kb))
        return

    if not user_data:
        # Naya User
        users_col.insert_one({
            "user_id": user.id, "first_name": user.first_name,
            "status": "free", "vip_expiry": None, "invites_count": 0,
            "history": [], "last_active": get_current_ist()
        })
        msg = "hii kaise ho? kya me apki gf ban sakti hu? 🙈"
    else:
        # Purana User
        msg = "welcome back baby! 😘 kahan chale gaye the?"
    
    await update.message.reply_text(msg)

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if await check_membership(user_id, context.bot):
        await query.message.delete()
        user_data = users_col.find_one({"user_id": user_id})
        if user_data and len(user_data.get("history", [])) > 2:
            msg = "welcome back baby! 😘 Missed you!"
        else:
            msg = "Hello kaise ho? kya me apki gf ban sakti hu? 🙈"
        await context.bot.send_message(query.message.chat_id, msg)
    else:
        await context.bot.send_message(query.message.chat_id, "Jhooth mat bolo! Join karke aao. 😡")

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text
    
    # 1. Forced Join Check (Every message)
    if not await check_membership(user.id, context.bot):
        kb = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)], [InlineKeyboardButton("✅ Verify", callback_data="verify_join")]]
        await update.message.reply_text("Pehle join karo baby.. 🥺", reply_markup=InlineKeyboardMarkup(kb))
        return

    # 2. Data Load
    u_data = users_col.find_one({"user_id": user.id})
    if not u_data:
        users_col.insert_one({"user_id": user.id, "first_name": user.first_name, "status": "free", "history": [], "last_active": get_current_ist()})
        u_data = users_col.find_one({"user_id": user.id})

    # 3. Prepare AI Prompt
    status = u_data.get("status", "free")
    prompt = read_master_prompt().replace("{USER_STATUS}", status).replace("{PLATFORM}", "Private")
    
    # Time based romantic mode
    now = get_current_ist()
    if now.hour >= 21 or now.hour < 4:
        prompt += "\n[SYSTEM: Night mode active. Be extra romantic and naughty.]"

    history = u_data.get("history", [])[-10:]
    msgs = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": text}]
    
    await context.bot.send_chat_action(chat.id, 'typing')
    reply = call_ai(msgs)

    # 4. Free User Wall
    if status == "free":
        for t in ["I cannot", "AI model", "explicit", "NSFW"]:
            if t.lower() in reply.lower():
                reply = "uff... itna bhi kya jaldi h? 😉 pehle premium le lo baby.. /premium dekho! ❤️"
                break

    await update.message.reply_text(reply.lower())

    # 5. Save History
    new_hist = [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    users_col.update_one({"user_id": user.id}, {
        "$push": {"history": {"$each": new_hist}},
        "$set": {"last_active": now}
    })

# ==============================================================================
# 4. SERVER & RUN
# ==============================================================================

@app.route('/')
def home(): return "System Active 🟢", 200

if __name__ == '__main__':
    # Start Flask in background
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    
    # Start Bot
    t_req = HTTPXRequest(connection_pool_size=10, read_timeout=30, write_timeout=30, connect_timeout=30)
    bot_app = ApplicationBuilder().token(TOKEN).request(t_req).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(verify_callback))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
    
    logger.info("🚀 Riya AI Pro is Polling...")
    bot_app.run_polling()
