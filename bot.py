import os
import random
import datetime
import logging
import asyncio
import threading
import httpx # Requests ki jagah httpx use karenge (Faster)
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

# DB Connection
try:
    client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client["RiyaBot_Final"] 
    users_col = db["users"]
    groups_col = db["groups"]
    logger.info("✅ Database Connected")
except Exception as e:
    logger.error(f"❌ DB Error: {e}")

app = Flask(__name__)

# ==============================================================================
# 2. UTILS
# ==============================================================================

def get_current_ist():
    return datetime.datetime.now(IST)

def read_master_prompt():
    try:
        with open("prompt.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "Identity: You are Riya, a romantic Indian girl. User is your boyfriend."

async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

async def call_ai_async(messages):
    """Async call for OpenRouter (No more Network Issue)"""
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                json={
                    "model": "z-ai/glm-4.5-air:free",
                    "messages": messages,
                    "temperature": 1.0,
                    "max_tokens": 150
                },
                timeout=30.0
            )
            return res.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return None

# ==============================================================================
# 3. COMMAND HANDLERS
# ==============================================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = users_col.find_one({"user_id": user.id})
    
    # Check Join
    if not await check_membership(user.id, context.bot):
        kb = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)], [InlineKeyboardButton("✅ Verify", callback_data="verify_join")]]
        await update.message.reply_text(f"hii {user.first_name}! ❤️\njoin krlo tabhi baat krungi 👇", reply_markup=InlineKeyboardMarkup(kb))
        return

    if not u_data:
        users_col.insert_one({"user_id": user.id, "first_name": user.first_name, "status": "free", "history": [], "last_active": get_current_ist()})
        msg = "hii kaise ho? kya me apki gf ban sakti hu? 🙈"
    else:
        msg = "welcome back baby! 😘 Missed you so much!"
    
    await update.message.reply_text(msg)

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = users_col.find_one({"user_id": update.effective_user.id})
    if not u: return
    status = u.get("status", "FREE").upper()
    msg = f"👤 **MY PROFILE**\n\nStatus: {status}\n\n(Referral system integration pending...)"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "💖 **PREMIUM ACCESS**\n\nFull 18+ Uncensored Chat aur Virtual Photos ke liye hamare payment bot par jayein.\n\n👇 **Yahan click karein:**\n@paymentbot" # Apna real bot username badal dena
    await update.message.reply_text(msg, parse_mode="Markdown")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_col.update_one({"user_id": update.effective_user.id}, {"$set": {"history": []}})
    await update.message.reply_text("Chat history reset ho gayi baby! 😘")

# ==============================================================================
# 4. CHAT LOGIC
# ==============================================================================

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text
    
    # 1. Join Check
    if not await check_membership(user.id, context.bot):
        await update.message.reply_text("Pehle join karo baby.. tabhi rply dungi 🥺")
        return

    # 2. DB Fetch
    u_data = users_col.find_one({"user_id": user.id})
    if not u_data: return

    # 3. AI Prep
    status = u_data.get("status", "free")
    prompt = read_master_prompt().replace("{USER_STATUS}", status).replace("{PLATFORM}", "Private")
    
    now = get_current_ist()
    if now.hour >= 21 or now.hour < 4:
        prompt += "\n[SYSTEM: Night mode. Be romantic.]"

    history = u_data.get("history", [])[-8:]
    msgs = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": text}]
    
    await context.bot.send_chat_action(chat.id, 'typing')
    
    reply = await call_ai_async(msgs)
    
    if not reply:
        await update.message.reply_text("network issue h baby.. ruko thoda 🥺")
        return

    # Teasing
    if status == "free":
        for t in ["I cannot", "AI model", "explicit", "NSFW"]:
            if t.lower() in reply.lower():
                reply = "uff... itna bhi kya jaldi h? 😉 premium le lo baby fir maza ayega.. /premium dekho! ❤️"
                break

    await update.message.reply_text(reply.lower())

    # Save
    new_hist = [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    users_col.update_one({"user_id": user.id}, {"$push": {"history": {"$each": new_hist}}, "$set": {"last_active": now}})

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await check_membership(user_id, context.bot):
        await query.message.delete()
        await context.bot.send_message(query.message.chat_id, "Welcome baby! 😘 Ab bolo?")
    else:
        await context.bot.send_message(query.message.chat_id, "Jhooth mat bolo! Join karo. 😡")

# ==============================================================================
# 5. ADMIN PANEL & RUN
# ==============================================================================

@app.route('/')
def home(): return "System Active 🟢", 200

@app.route('/admin')
def admin_ui():
    if request.args.get('pass') != ADMIN_PASS: return "404 Not Found", 404
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
    
    t_req = HTTPXRequest(connection_pool_size=20, read_timeout=40, write_timeout=40, connect_timeout=40)
    bot_app = ApplicationBuilder().token(TOKEN).request(t_req).post_init(post_init).build()

    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CommandHandler("profile", profile_cmd))
    bot_app.add_handler(CommandHandler("premium", premium_cmd))
    bot_app.add_handler(CommandHandler("reset", reset_cmd))
    bot_app.add_handler(CallbackQueryHandler(verify_callback))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
    
    bot_app.run_polling()
