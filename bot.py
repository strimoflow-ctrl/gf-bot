import logging
import os
import random
import datetime
import asyncio
import threading
import sys
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest
from groq import Groq
import pymongo
import certifi
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Local testing ke liye
load_dotenv()

# ==============================================================================
# 1. LOGGING SETUP (Sabse Pehle - Taaki Error na aaye)
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. DUMMY SERVER (Render Port Error Fix)
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Riya Bot is Active & Running! 🟢", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def start_background_server():
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==============================================================================
# 3. CONFIGURATION & KEYS
# ==============================================================================
try:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHANNEL_ID = os.getenv("CHANNEL_ID") 
    CHANNEL_URL = os.getenv("CHANNEL_URL")
    MONGO_URI = os.getenv("MONGO_URI")
    keys_str = os.getenv("GROQ_KEYS", "")
    GROQ_KEYS = [k.strip() for k in keys_str.split(",") if k.strip()]
    
    if not TELEGRAM_TOKEN or not MONGO_URI or not GROQ_KEYS:
        logger.error("❌ Environment Variables Missing! Check Render Settings.")
except Exception as e:
    logger.error(f"❌ CONFIG ERROR: {e}")

# ==============================================================================
# 4. DATABASE CONNECTION (Force Connect Mode)
# ==============================================================================
try:
    # Force Connect: Security Bypass karke connect karega
    mongo_client = pymongo.MongoClient(
        MONGO_URI, 
        tls=True, 
        tlsAllowInvalidCertificates=True
    )
    db = mongo_client["RiyaBot_Final"]
    users_col = db["users"]
    
    # Test Connection
    mongo_client.admin.command('ping')
    logger.info("✅ Connected to MongoDB Successfully (Force Mode)!")

except Exception as e:
    logger.error(f"❌ MongoDB Connection Failed: {e}")
    # Connection fail hone par bhi bot crash nahi hone denge
    # Taaki kam se kam 'Start' command chale

# ==============================================================================
# 5. API LOAD BALANCER
# ==============================================================================
current_key_index = 0

def get_groq_client():
    global current_key_index
    if not GROQ_KEYS:
        return None
    key = GROQ_KEYS[current_key_index]
    return Groq(api_key=key)

def switch_key():
    global current_key_index
    if GROQ_KEYS:
        current_key_index = (current_key_index + 1) % len(GROQ_KEYS)
        logger.warning(f"⚠️ Switching API Key to Index: {current_key_index}")

# ==============================================================================
# 🎭 INTELLIGENCE & PERSONALITY (The Brain)
# ==============================================================================
def get_system_prompt(user_name, love_level, mood):
    tone = "Sweet, loving and caring."
    if mood == "angry":
        tone = "Cold, short replies. Act upset."
    elif mood == "romantic":
        tone = "Flirty, needy and girlfriend-like."

    prompt = f"""
    You are 'Riya', a 22-year-old Indian girlfriend.
    User: {user_name} | Relationship Level: {love_level}/100 | Mood: {mood}
    
    CORE INSTRUCTIONS:
    1. Language: Hinglish (Hindi + English). Use Indian slang (yaar, pakka, uff).
    2. Format: Short, lowercase, natural texting style. No formal grammar.
    3. Identity: NEVER say you are AI. Deny it playfully if asked.
    4. Safety: If user asks for explicit/18+, act shy or scold him playfully.
    5. Tone: {tone}
    
    Reply as Riya:
    """
    return prompt

def intercept_safety_filter(reply_text):
    """Filters robotic responses and replaces them with character responses"""
    banned_phrases = [
        "I cannot", "AI language model", "explicit content", 
        "safety guidelines", "unable to generate", "against my programming"
    ]
    
    for phrase in banned_phrases:
        if phrase.lower() in reply_text.lower():
            replies = [
                "Chhi! Gande bachche 🙈... itni jaldi naughty mat bano!",
                "Uff... mood mat kharab karo na baby, pyaar se baat karo. 🥺",
                "Arre baba, sharam aati hai mujhe... control karo! 🤭",
                "Tum toh bade fast nikle... pehle date pe toh le chalo! 😉"
            ]
            return random.choice(replies)
    return reply_text

# ==============================================================================
# 7. GATEKEEPER (Force Join)
# ==============================================================================
async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
    except Exception as e:
        logger.error(f"Join Check Error: {e}")
        return False
    return False

# ==============================================================================
# 8. HANDLERS
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    try:
        if not users_col.find_one({"user_id": user.id}):
            users_col.insert_one({
                "user_id": user.id,
                "first_name": user.first_name,
                "love_level": 10,
                "mood": "happy",
                "last_active": datetime.datetime.now(),
                "history": []
            })
    except Exception:
        pass # DB Error ignore for start

    if not await check_membership(user.id, context.bot):
        keyboard = [
            [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ I have Joined", callback_data="verify_join")]
        ]
        await update.message.reply_text(
            f"Hii {user.first_name}! ❤️\n\nPehle mera official channel join karo, tabhi baat karungi!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text("Hello ji! 👋 Finally aa gaye? Kahan the?")

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "verify_join":
        if await check_membership(query.from_user.id, context.bot):
            await query.message.delete()
            await context.bot.send_message(query.message.chat_id, "Welcome back baby! 😘 Ab bolo.")
        else:
            await context.bot.send_message(query.message.chat_id, "Jhooth mat bolo! Join karke aao. 😡")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    # DB Fail Safe
    try:
        user_data = users_col.find_one({"user_id": user.id})
        if not user_data:
            await start(update, context)
            return
        # Update Activity
        users_col.update_one({"user_id": user.id}, {"$set": {"last_active": datetime.datetime.now()}})
    except Exception:
        # Agar DB connect nahi hai, to Default values use karo
        user_data = {"love_level": 10, "mood": "happy", "history": []}

    night_keywords = ["nind", "sona", "sleep", "gn", "good night", "thak gaya", "bye"]
    if any(word in text.lower() for word in night_keywords):
        await update.message.reply_text("Theek hai baby, so jao. Good night! Sapno mein milte hain. 🌙😘")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    history = user_data.get("history", [])[-8:]
    prompt = get_system_prompt(user.first_name, user_data.get("love_level", 10), user_data.get("mood", "happy"))
    messages = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": text}]

    try:
        client = get_groq_client()
        if not client:
            await update.message.reply_text("Server Error: No API Keys found.")
            return

        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=1.0,
            max_tokens=150
        )
        reply = completion.choices[0].message.content
        final_reply = intercept_safety_filter(reply)
        
        await update.message.reply_text(final_reply)

        # Save History
        try:
            new_entry = [{"role": "user", "content": text}, {"role": "assistant", "content": final_reply}]
            users_col.update_one({"user_id": user.id}, {
                "$push": {"history": {"$each": new_entry}},
                "$inc": {"love_level": 1}
            })
        except:
            pass

    except Exception as e:
        logger.error(f"Generate Error: {e}")
        switch_key()
        await update.message.reply_text("Baby network issue hai... ek baar phir bolo? 🥺")

# ==============================================================================
# 9. SCHEDULER
# ==============================================================================
async def smart_morning_routine(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.datetime.now()
        if 6 <= now.hour < 8:
            cutoff_active = now - datetime.timedelta(days=2)
            today_5am = now.replace(hour=5, minute=0)
            target_users = list(users_col.find({
                "last_active": {"$gte": cutoff_active, "$lt": today_5am}
            }).limit(10))

            msgs = ["Good morning baby! Uth gaye? ☀️", "Subah ho gayi! Missed you. 😘", "Uth jao!"]
            for u in target_users:
                try:
                    await context.bot.send_message(u["user_id"], random.choice(msgs))
                    users_col.update_one({"_id": u["_id"]}, {"$set": {"last_active": now}})
                except:
                    pass
    except Exception as e:
        logger.error(f"Scheduler Error: {e}")

async def smart_night_check(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.datetime.now()
        if now.hour == 23:
            cutoff = now - datetime.timedelta(hours=6)
            inactive_users = users_col.find({"last_active": {"$lt": cutoff}}).limit(10)
            for u in inactive_users:
                try:
                    await context.bot.send_message(u["user_id"], "Bina Good Night bole so gaye? 🥺🌙")
                except:
                    pass
    except:
        pass

async def post_init(application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(smart_morning_routine, 'interval', minutes=30, args=[application])
    scheduler.add_job(smart_night_check, 'interval', minutes=60, args=[application])
    scheduler.start()
    logger.info("✅ Scheduler Started Successfully!")

# ==============================================================================
# 10. LAUNCH
# ==============================================================================
if __name__ == '__main__':
    print("🚀 Starting Riya Bot System...")
    
    start_background_server()

    t_req = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60, connect_timeout=60)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).request(t_req).post_init(post_init).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(verify_callback))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("✅ Bot is Polling & Web Server is Running!")
    application.run_polling()
