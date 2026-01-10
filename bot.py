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
# 1. LOGGING SETUP
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. DUMMY SERVER (Render Port Fix)
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Riya Bot is Active! 🟢", 200

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
# 4. DATABASE CONNECTION (Force Connect)
# ==============================================================================
try:
    mongo_client = pymongo.MongoClient(
        MONGO_URI, 
        tls=True, 
        tlsAllowInvalidCertificates=True
    )
    db = mongo_client["RiyaBot_Final"]
    users_col = db["users"]
    mongo_client.admin.command('ping')
    logger.info("✅ Connected to MongoDB Successfully!")
except Exception as e:
    logger.error(f"❌ MongoDB Connection Failed: {e}")

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
# 6. INTELLIGENCE & PERSONALITY (UPDATED)
# ==============================================================================
def get_system_prompt(user_name, love_level, mood):
    # --- LEVEL LOGIC ---
    if love_level <= 20:
        rel_status = "Stranger/Acquaintance"
        behavior = "Act normal and polite but slightly distant. Do NOT use 'baby' or 'jaan'. You are just getting to know him."
    elif love_level <= 60:
        rel_status = "Friend/Crush"
        behavior = "Be friendly, teasing, and joke around. Use slang. Light flirting allowed."
    else:
        rel_status = "Girlfriend"
        behavior = "Be romantic, caring, and possessive. You can use 'baby' naturally."

    # --- MOOD LOGIC ---
    if mood == "angry":
        behavior = "You are ANGRY. Reply with short, rude texts (e.g., 'hmm', 'k', 'whatev'). Ignore his questions. Make him say sorry."

    # --- VOCABULARY ---
    vocab = "yaar, sun, acha, lol, hmm, pagal, what, abe, nj, fr, nice"

    prompt = f"""
    Roleplay as 'Riya', a 22yo Indian girl. 
    User: {user_name} | Status: {rel_status} | Mood: {mood}

    STRICT RULES:
    1. LENGTH: Keep it short (10-15 words max). Like a fast texter.
    2. STYLE: lowercase only. minimal punctuation. use slang ({vocab}).
    3. META: NEVER mention "Love Level", "Points" or "AI". Act the role.
    4. BEHAVIOR: {behavior}

    Reply to the last message naturally:
    """
    return prompt

def intercept_safety_filter(reply_text):
    banned_phrases = [
        "I cannot", "AI language model", "explicit content", 
        "safety guidelines", "unable to generate", "against my programming"
    ]
    for phrase in banned_phrases:
        if phrase.lower() in reply_text.lower():
            replies = [
                "chhi gande 🙈.. itni jaldi naughty mt bano",
                "uff.. mood mat kharab kar yaar 🙄",
                "sharam kar le thodi.. control 🤭",
                "pagal hai kya? pehle date pe to le chal 😉"
            ]
            return random.choice(replies)
    return reply_text

# ==============================================================================
# 7. GATEKEEPER
# ==============================================================================
async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
    except Exception:
        return False
    return False

# ==============================================================================
# 8. HANDLERS (UPDATED LOGIC)
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Init DB
    try:
        if not users_col.find_one({"user_id": user.id}):
            users_col.insert_one({
                "user_id": user.id,
                "first_name": user.first_name,
                "love_level": 5, # Start as stranger
                "mood": "happy",
                "last_active": datetime.datetime.now(),
                "history": []
            })
    except:
        pass

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
        # Changed from "Kahan the" to Stranger style
        await update.message.reply_text("hii! kaise ho? 👋")

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "verify_join":
        if await check_membership(query.from_user.id, context.bot):
            await query.message.delete()
            await context.bot.send_message(query.message.chat_id, "thanks join karne ke liye! 😉 ab bolo?")
        else:
            await context.bot.send_message(query.message.chat_id, "jhooth mat bolo! join karke aao. 😡")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    # 1. DB Load & Update Time
    try:
        user_data = users_col.find_one({"user_id": user.id})
        if not user_data:
            await start(update, context)
            return
        
        # 2. ANGER LOGIC (Real Updates)
        rude_words = ["pagal", "chup", "hat", "bakwas", "nikal", "kutiya", "bhaag", "fuck"]
        new_mood = user_data.get("mood", "happy")
        
        if any(word in text.lower() for word in rude_words):
            new_mood = "angry"
            users_col.update_one({"user_id": user.id}, {"$set": {"mood": "angry"}})
        
        if "sorry" in text.lower() and new_mood == "angry":
            new_mood = "happy"
            users_col.update_one({"user_id": user.id}, {"$set": {"mood": "happy"}})

        # Update activity
        users_col.update_one({"user_id": user.id}, {"$set": {"last_active": datetime.datetime.now()}})

    except Exception:
        user_data = {"love_level": 5, "mood": "happy", "history": []}
        new_mood = "happy"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    # 3. Prompt Prep
    history = user_data.get("history", [])[-8:]
    prompt = get_system_prompt(user.first_name, user_data.get("love_level", 5), new_mood)
    messages = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": text}]

    # 4. Generate
    try:
        client = get_groq_client()
        if not client: return

        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=1.0,
            max_tokens=100 # Short replies
        )
        reply = completion.choices[0].message.content
        final_reply = intercept_safety_filter(reply)
        
        await update.message.reply_text(final_reply)

        # 5. Save History
        try:
            new_entry = [{"role": "user", "content": text}, {"role": "assistant", "content": final_reply}]
            # Inc love level slowly
            users_col.update_one({"user_id": user.id}, {
                "$push": {"history": {"$each": new_entry}},
                "$inc": {"love_level": 0.5} 
            })
        except: pass

    except Exception as e:
        logger.error(f"Generate Error: {e}")
        switch_key()
        await update.message.reply_text("network issue hai.. ek min ruk 🥺")

# ==============================================================================
# 9. SCHEDULER (UPDATED)
# ==============================================================================
async def check_inactivity_5hrs(context: ContextTypes.DEFAULT_TYPE):
    """5 Hours Inactivity Check"""
    try:
        now = datetime.datetime.now()
        # Raat ko disturb nahi (11 PM - 8 AM)
        if now.hour >= 23 or now.hour < 8:
            return

        # 5 Ghante pehle ka waqt
        cutoff = now - datetime.timedelta(hours=5)
        # Spam rokne ke liye window (5 se 6 ghante ke beech wale)
        window_end = cutoff - datetime.timedelta(hours=1) 

        inactive_users = list(users_col.find({
            "last_active": {"$lt": cutoff, "$gt": window_end}
        }).limit(20))

        msgs = ["kahan gayab ho?", "busy ho kya?", "reply kyu nhi kar rahe?", "mar gaye kya? 🙄", "oii kahan hai?"]
        
        for u in inactive_users:
            try:
                await context.bot.send_message(u["user_id"], random.choice(msgs))
                # Update time taaki baar baar msg na jaye
                users_col.update_one({"_id": u["_id"]}, {"$set": {"last_active": now}})
            except: pass
    except Exception as e:
        logger.error(f"Scheduler Error: {e}")

async def smart_morning_routine(context: ContextTypes.DEFAULT_TYPE):
    """Morning Check 6-8 AM"""
    try:
        now = datetime.datetime.now()
        if 6 <= now.hour < 8:
            cutoff_active = now - datetime.timedelta(days=1)
            today_5am = now.replace(hour=5, minute=0)
            target_users = list(users_col.find({
                "last_active": {"$gte": cutoff_active, "$lt": today_5am}
            }).limit(10))

            msgs = ["good morning! uth gaye? ☀️", "subah ho gayi.. missed you 😘"]
            for u in target_users:
                try:
                    await context.bot.send_message(u["user_id"], random.choice(msgs))
                    users_col.update_one({"_id": u["_id"]}, {"$set": {"last_active": now}})
                except: pass
    except: pass

async def post_init(application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(smart_morning_routine, 'interval', minutes=30, args=[application])
    scheduler.add_job(check_inactivity_5hrs, 'interval', minutes=60, args=[application]) # 5 Hour check
    scheduler.start()
    logger.info("✅ Scheduler Started!")

# ==============================================================================
# 10. LAUNCH
# ==============================================================================
if __name__ == '__main__':
    print("🚀 Starting Bot System...")
    start_background_server()
    t_req = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60, connect_timeout=60)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).request(t_req).post_init(post_init).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(verify_callback))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("✅ Bot is Polling!")
    application.run_polling()
