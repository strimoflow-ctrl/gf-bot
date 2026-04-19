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

# Database Connection (Purana DB Name use kiya h)
try:
    client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client["RiyaBot_Final"] 
    users_col = db["users"]
    groups_col = db["groups"]
    codes_col = db["codes"]
    logger.info("✅ Database Connected: RiyaBot_Final")
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
        return "You are Riya, a friendly Indian girl."

async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

def call_ai(messages):
    """OpenRouter GLM 4.5 Air Call"""
    try:
        res = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
            json={
                "model": "z-ai/glm-4.5-air:free",
                "messages": messages,
                "temperature": 0.9,
                "max_tokens": 150
            }
        )
        return res.json()['choices'][0]['message']['content']
    except: return "network issue h baby.. ruko thoda 🥺"

# ==============================================================================
# 3. ADMIN DASHBOARD API
# ==============================================================================

@app.route('/')
def home(): return "Riya AI Pro is Live 🟢", 200

@app.route('/admin')
def admin_panel():
    if request.args.get('pass') != ADMIN_PASS: return "<h1>Access Denied</h1>"
    return render_template('admin.html')

@app.route('/api/stats')
def api_stats():
    if request.args.get('pass') != ADMIN_PASS: return jsonify({"error": "Auth"})
    total = users_col.count_documents({})
    vip = users_col.count_documents({"status": "vip"})
    groups = groups_col.count_documents({"status": "active"})
    
    # Recent users for the table
    recent = list(users_col.find().sort("last_active", -1).limit(20))
    items = []
    for r in recent:
        items.append({
            "id": r["user_id"], "name": r.get("first_name", "User"),
            "status": r.get("status", "free"), "expiry": str(r.get("vip_expiry", "N/A"))
        })
    return jsonify({"total_users": total, "vip_users": vip, "total_groups": groups, "items": items})

# ==============================================================================
# 4. COMMANDS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref_id = context.args[0] if context.args and context.args[0].isdigit() else None
    
    user_data = users_col.find_one({"user_id": user.id})
    
    if not user_data:
        # Naya User
        users_col.insert_one({
            "user_id": user.id, "first_name": user.first_name,
            "status": "free", "vip_expiry": None, "invites_count": 0,
            "referred_by": int(ref_id) if ref_id else None,
            "history": [], "last_active": get_current_ist()
        })
        # Reward Inviter
        if ref_id:
            inviter = users_col.find_one({"user_id": int(ref_id)})
            if inviter:
                # Add 1 hour VIP to inviter
                now = get_current_ist()
                current_exp = inviter.get("vip_expiry") or now
                if current_exp.tzinfo is None: current_exp = IST.localize(current_exp)
                new_exp = max(current_exp, now) + datetime.timedelta(hours=1)
                
                users_col.update_one({"user_id": int(ref_id)}, {
                    "$inc": {"invites_count": 1},
                    "$set": {"vip_expiry": new_exp, "status": "vip"}
                })
                try: await context.bot.send_message(ref_id, "🎉 Naye user ne join kiya! Aapko 1 ghante ka VIP mila.")
                except: pass

        msg = "hii kaise ho? kya me apki gf ban sakti hu? 🙈"
    else:
        # Purana User
        msg = "welcome back baby! 😘 kahan chale gaye the?"

    if not await check_membership(user.id, context.bot):
        kb = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)], [InlineKeyboardButton("✅ Verify", callback_data="verify_join")]]
        await update.message.reply_text(f"hii {user.first_name}!\njoin krlo tabhi baat krungi 👇", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(msg)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = users_col.find_one({"user_id": update.effective_user.id})
    ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start={u['user_id']}"
    msg = f"👤 **MY PROFILE**\n\nStatus: {u['status'].upper()}\nExpiry: {u.get('vip_expiry', 'N/A')}\nInvites: {u['invites_count']}\n\n🔗 **Your Invite Link:**\n`{ref_link}`"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    text = " ".join(context.args)
    if not text: return
    
    users = users_col.find({})
    count = 0
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"✅ Sent to {count} users.")

# ==============================================================================
# 5. CHAT LOGIC
# ==============================================================================

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text
    is_grp = chat.type in ["group", "supergroup"]

    if not await check_membership(user.id, context.bot):
        await update.message.reply_text("pehle channel join kro baby! 👇")
        return

    # User Data & VIP Check
    u_data = users_col.find_one({"user_id": user.id})
    if not u_data: return
    
    now = get_current_ist()
    status = u_data.get("status", "free")
    exp = u_data.get("vip_expiry")
    if exp and exp.tzinfo is None: exp = IST.localize(exp)
    
    if exp and now > exp:
        users_col.update_one({"user_id": user.id}, {"$set": {"status": "free"}})
        status = "free"

    # Group Logic
    if is_grp:
        g = groups_col.find_one({"group_id": chat.id})
        if not g or g.get("status") != "active":
            if "@" + (await context.bot.get_me()).username in text:
                await update.message.reply_text("me abhi so rhi hu.. admin se kaho approve kre. 😴")
            return
        if "@" + (await context.bot.get_me()).username not in text and random.random() > 0.2: return

    # AI Payload
    prompt = read_master_prompt().replace("{USER_STATUS}", status).replace("{PLATFORM}", "Group" if is_grp else "Private")
    if now.hour >= 21 or now.hour < 4: prompt += "\n[System: Night mode active. Be romantic.]"
    
    history = u_data.get("history", [])[-10:]
    msgs = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": text}]
    
    await context.bot.send_chat_action(chat.id, 'typing')
    reply = call_ai(msgs)

    # Free User Teasing
    if status == "free":
        for t in ["I cannot", "AI model", "explicit", "NSFW"]:
            if t.lower() in reply.lower():
                reply = "uff... itna bhi kya jaldi h? 😉 pehle premium le lo baby.. /premium dekho! ❤️"
                break

    await update.message.reply_text(reply.lower())

    # Update DB
    new_hist = [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    users_col.update_one({"user_id": user.id}, {"$push": {"history": {"$each": new_hist}}, "$set": {"last_active": now}})

# ==============================================================================
# 6. SCHEDULER & RUN
# ==============================================================================

async def proactive_ping(context):
    cutoff = get_current_ist() - datetime.timedelta(hours=3)
    users = users_col.find({"last_active": {"$lt": cutoff, "$gt": cutoff - datetime.timedelta(hours=1)}})
    for u in users:
        try: await context.bot.send_message(u["user_id"], random.choice(["kahan ho baby? 🥺", "bhul gye kya? rply kro.."]))
        except: pass

async def post_init(application):
    s = AsyncIOScheduler(timezone=IST)
    s.add_job(proactive_ping, 'interval', minutes=60, args=[application])
    s.start()

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    t_req = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60, connect_timeout=60)
    bot_app = ApplicationBuilder().token(TOKEN).request(t_req).post_init(post_init).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("profile", profile))
    bot_app.add_handler(CommandHandler("broadcast", broadcast))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
    
    bot_app.run_polling()
