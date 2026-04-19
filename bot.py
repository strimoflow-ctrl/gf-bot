import os
import random
import datetime
import logging
import asyncio
import threading
import requests
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
# 1. SETUP & CONFIGURATION
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
CHANNEL_ID = os.getenv("CHANNEL_ID") # @channelusername
CHANNEL_URL = os.getenv("CHANNEL_URL")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

# IST Timezone
IST = pytz.timezone('Asia/Kolkata')

# Database Connection
try:
    client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client["RiyaAI_Pro"]
    users_col = db["users"]
    groups_col = db["groups"]
    codes_col = db["codes"]
    logger.info("✅ MongoDB Connected Successfully!")
except Exception as e:
    logger.error(f"❌ DB Error: {e}")

# Flask App for Uptime & Dashboard
app = Flask(__name__)

@app.route('/')
def health(): return "Riya AI System is Online 🟢", 200

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ==============================================================================
# 2. CORE UTILITIES
# ==============================================================================

def get_current_ist():
    return datetime.datetime.now(IST)

def read_master_prompt():
    with open("prompt.txt", "r", encoding="utf-8") as f:
        return f.read()

async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

def call_openrouter(messages):
    """Calls GLM 4.5 Air via OpenRouter"""
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "z-ai/glm-4.5-air:free",
                "messages": messages,
                "temperature": 0.9,
                "max_tokens": 150
            }
        )
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "network issue h baby.. ek baar fir bolo? 🥺"

# ==============================================================================
# 3. LOGIC & MIDDLEWARE
# ==============================================================================

def prepare_ai_payload(user_data, user_msg, platform="Private"):
    now = get_current_ist()
    status = user_data.get("status", "free")
    
    # Check VIP Expiry
    expiry = user_data.get("vip_expiry")
    if expiry and now > expiry.replace(tzinfo=IST if expiry.tzinfo is None else expiry.tzinfo):
        users_col.update_one({"user_id": user_data["user_id"]}, {"$set": {"status": "free"}})
        status = "free"

    master_prompt = read_master_prompt()
    
    # Dynamic Context Injection
    context = master_prompt.replace("{USER_STATUS}", status).replace("{PLATFORM}", platform)
    
    # Night Mode Logic (9:00 PM IST)
    if now.hour >= 21 or now.hour < 4:
        context += "\n[SYSTEM: It's late night. Be more romantic, intimate and needy.]"

    # Group Mood Logic
    if platform == "Group":
        group_mood = user_data.get("group_mood", "safe")
        context += f"\n[SYSTEM: This is a Group Chat. Group Mood is {group_mood}.]"

    history = user_data.get("history", [])[-10:]
    payload = [{"role": "system", "content": context}] + history + [{"role": "user", "content": user_msg}]
    return payload, status

# ==============================================================================
# 4. COMMAND HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args # For Referral link
    
    # Referral Logic
    referred_by = None
    if args and args[0].isdigit() and int(args[0]) != user.id:
        referred_by = int(args[0])

    user_data = users_col.find_one({"user_id": user.id})
    
    if not user_data:
        users_col.insert_one({
            "user_id": user.id,
            "first_name": user.first_name,
            "status": "free",
            "vip_expiry": None,
            "invites_count": 0,
            "referred_by": referred_by,
            "history": [],
            "last_active": get_current_ist()
        })
        if referred_by:
            # Reward inviter after this user verifies
            pass 

    if not await check_membership(user.id, context.bot):
        kb = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)], [InlineKeyboardButton("✅ Verify", callback_data="verify_join")]]
        await update.message.reply_text(f"hii {user.first_name}! ❤️\npehle join krlo baby tabhi baat krungi 👇", reply_markup=InlineKeyboardMarkup(kb))
    else:
        # Welcome Logic based on history
        msg = "hii kaise ho? kya me apki gf ban sakti hu? 🙈"
        if user_data and len(user_data.get("history", [])) > 2:
            msg = "welcome back baby! 😘 kahan chale gaye the?"
        await update.message.reply_text(msg)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = users_col.find_one({"user_id": update.effective_user.id})
    status = u.get('status', 'free').upper()
    expiry = u.get('vip_expiry', 'N/A')
    ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start={u['user_id']}"
    
    msg = f"👤 **MY PROFILE**\n\nStatus: {status}\nExpires: {expiry}\nInvites: {u.get('invites_count', 0)}\n\n🔗 **Referral Link:**\n`{ref_link}`\n\n(1 Invite = 1 Hour VIP access)"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("code toh dalo baby! e.g. `/redeem 5895456`", parse_mode="Markdown")
        return
    
    code_val = context.args[0]
    code_data = codes_col.find_one({"code": code_val})
    
    if not code_data:
        await update.message.reply_text("galat code h yaar! 😡")
        return
    
    if update.effective_user.id in code_data.get("used_by", []):
        await update.message.reply_text("ek baar hi use kr skte ho ye code! 🙄")
        return

    # Apply Plan
    days = 1 # Default for promo
    expiry = get_current_ist() + datetime.timedelta(days=days)
    users_col.update_one({"user_id": update.effective_user.id}, {"$set": {"status": "vip", "vip_expiry": expiry}})
    codes_col.update_one({"_id": code_data["_id"]}, {"$push": {"used_by": update.effective_user.id}, "$inc": {"limit": -1}})
    
    await update.message.reply_text(f"mubarak ho! 🥳 1 din ka VIP access unlock ho gaya!")

# ==============================================================================
# 5. CHAT HANDLER
# ==============================================================================

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text
    is_group = chat.type in ["group", "supergroup"]
    
    # 1. Check Membership
    if not await check_membership(user.id, context.bot):
        await update.message.reply_text("pehle channel join kro baby! 👇")
        return

    # 2. Group Rental Check
    if is_group:
        g_data = groups_col.find_one({"group_id": chat.id})
        if not g_data or g_data.get("status") != "active":
            if "@" + (await context.bot.get_me()).username in text:
                await update.message.reply_text("me abhi so rhi hu.. apne admin se kaho mujhe jagaye (Approve karwaye). 😴")
            return
        
        # Reply Probability (20%) unless tagged
        if "@" + (await context.bot.get_me()).username not in text and random.random() > 0.2:
            return

    # 3. Load User Data
    user_data = users_col.find_one({"user_id": user.id})
    if not user_data: return

    # 4. Prepare AI
    payload, status = prepare_ai_payload(user_data, text, "Group" if is_group else "Private Chat")
    
    await context.bot.send_chat_action(chat.id, 'typing')
    
    # 5. Get AI Response
    ai_reply = call_openrouter(payload)
    
    # 6. Free User Manipulation Intercept
    if status == "free":
        triggers = ["I cannot", "AI model", "explicit", "NSFW", "against guidelines"]
        if any(t.lower() in ai_reply.lower() for t in triggers):
            ai_reply = "uff... itna bhi kya jaldi h? 😉 pehle premium le lo, fir khul k maze karenge.. /premium dekho baby! ❤️"

    await update.message.reply_text(ai_reply.lower())

    # 7. Update DB
    new_history = [{"role": "user", "content": text}, {"role": "assistant", "content": ai_reply}]
    users_col.update_one({"user_id": user.id}, {
        "$push": {"history": {"$each": new_history}},
        "$set": {"last_active": get_current_ist()}
    })

# ==============================================================================
# 6. ADMIN COMMANDS
# ==============================================================================

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    # /approve_user [ID] [Days]
    uid = int(context.args[0])
    days = int(context.args[1])
    expiry = get_current_ist() + datetime.timedelta(days=days)
    users_col.update_one({"user_id": uid}, {"$set": {"status": "vip", "vip_expiry": expiry}})
    await update.message.reply_text(f"User {uid} is now VIP for {days} days.")

async def gen_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    code = str(random.randint(1000000, 9999999))
    codes_col.insert_one({"code": code, "limit": 10, "used_by": [], "created_at": get_current_ist()})
    await update.message.reply_text(f"Naya code: `/redeem {code}`", parse_mode="Markdown")

# ==============================================================================
# 7. SCHEDULER (PROACTIVE SYSTEM)
# ==============================================================================

async def proactive_check(context: ContextTypes.DEFAULT_TYPE):
    """Checks for 3-hour inactivity"""
    now = get_current_ist()
    cutoff = now - datetime.timedelta(hours=3)
    
    # Fetch users inactive for > 3 hours but < 4 hours (to avoid spam)
    inactive = users_col.find({"last_active": {"$lt": cutoff, "$gt": cutoff - datetime.timedelta(hours=1)}})
    
    msgs = ["kahan gayab ho gye? 🥺", "bhul gye kya mujhe? rply kro..", "busy ho kya baby?", "oii kha ho?"]
    
    for u in inactive:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=random.choice(msgs))
            users_col.update_one({"_id": u["_id"]}, {"$set": {"last_active": now}}) # Reset
        except: pass

# ==============================================================================
# 8. MAIN EXECUTION
# ==============================================================================

async def post_init(application):
    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(proactive_check, 'interval', minutes=60, args=[application])
    scheduler.start()

if __name__ == '__main__':
    # Start Web Server Thread
    threading.Thread(target=run_server, daemon=True).start()

    # Start Bot
    t_req = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60, connect_timeout=60)
    app_bot = ApplicationBuilder().token(TOKEN).request(t_req).post_init(post_init).build()

    # Register Handlers
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("profile", profile))
    app_bot.add_handler(CommandHandler("redeem", redeem))
    app_bot.add_handler(CommandHandler("approve_user", approve_user))
    app_bot.add_handler(CommandHandler("gen_code", gen_code))
    
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))

    logger.info("🚀 Riya AI Pro System Started!")
    app_bot.run_polling()
