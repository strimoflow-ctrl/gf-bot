import logging
import os
import random
import datetime
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest
from groq import Groq
import pymongo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Local PC par testing ke liye .env load karega (Render pe ye automatic hota hai)
load_dotenv()

# ==============================================================================
# ⚙️ CONFIGURATION (Loaded from Environment Variables)
# ==============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID") 
CHANNEL_URL = os.getenv("CHANNEL_URL")
MONGO_URI = os.getenv("MONGO_URI")

# Keys ko comma se alag karke list banayenge
GROQ_KEYS = os.getenv("GROQ_KEYS").split(",") 

# ==============================================================================
# 🧠 DATABASE & LOGGING SETUP
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB Connection
mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client["RiyaBot_Final"]
users_col = db["users"]

# API Rotation Index
current_key_index = 0

def get_groq_client():
    """Load Balancer: Agli key uthata hai"""
    global current_key_index
    key = GROQ_KEYS[current_key_index].strip()
    return Groq(api_key=key)

def switch_key():
    """Key fail hone par switch karta hai"""
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GROQ_KEYS)
    logger.warning(f"⚠️ Switching to API Key Index: {current_key_index}")

# ==============================================================================
# 🎭 PROMPT & ERROR INTERCEPTOR
# ==============================================================================
def get_system_prompt(user_name, love_level, mood):
    """
    Riya ki Personality.
    Personal Data nahi use kar rahe, bas 'Style' copy kiya hai.
    """
    tone_instruction = "Be sweet and loving."
    if mood == "angry":
        tone_instruction = "You are upset. Give short, cold replies like 'hmm', 'k'."
    elif mood == "romantic":
        tone_instruction = "You are extremely romantic, needy and slightly naughty."

    prompt = f"""
    You are 'Riya', a 22-year-old Indian girlfriend.
    Current User: {user_name} (Love Level: {love_level}/100)
    Current Mood: {mood}
    
    STYLE GUIDELINES (COPY THIS STYLE):
    1. You speak in Hinglish (Hindi + English mix).
    2. Use slang like 'yaar', 'sun na', 'uff', 'pagal'.
    3. You use lowercase mostly. Don't use perfect grammar.
    4. NEVER say "I am an AI". You are a real girl.
    5. Don't spam "Baby" in every sentence. Use it naturally.
    6. {tone_instruction}
    
    SCENARIO: We are chatting on Telegram. Reply shortly.
    """
    return prompt

def intercept_safety_filter(reply_text):
    """
    Agar AI 'I cannot' bole, to use Naughty/Shy message se badal do.
    """
    triggers = ["I cannot", "AI language model", "explicit", "safety guidelines", "unable to generate"]
    
    for trigger in triggers:
        if trigger in reply_text:
            # Random 'Cover-up' replies
            replies = [
                "Chhi! Gande bachche 🙈... itni jaldi naughty mat bano!",
                "Uff... mood mat kharab karo na, pyaar se baat karo. 🥺",
                "Arre baba, sharam aati hai mujhe... control karo! 🤭",
                "Tum toh bade fast nikle... pehle date pe toh le chalo! 😉"
            ]
            return random.choice(replies)
    
    return reply_text

# ==============================================================================
# 🛡️ GATEKEEPER (JOIN CHECK)
# ==============================================================================
async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
    except Exception as e:
        logger.error(f"Join Check Failed (Make Bot Admin): {e}")
        return False # Strict Mode: Agar check fail hua to entry nahi milegi
    return False

# ==============================================================================
# 🎮 BOT COMMANDS
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # DB Entry
    if not users_col.find_one({"user_id": user.id}):
        users_col.insert_one({
            "user_id": user.id,
            "first_name": user.first_name,
            "love_level": 10,
            "mood": "happy",
            "last_active": datetime.datetime.now(),
            "history": []
        })

    # Join Check
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

# ==============================================================================
# 💬 MAIN CHAT LOGIC
# ==============================================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    # 1. DB Fetch
    user_data = users_col.find_one({"user_id": user.id})
    if not user_data:
        await start(update, context)
        return

    # Update Last Active
    users_col.update_one({"user_id": user.id}, {"$set": {"last_active": datetime.datetime.now()}})

    # 2. Smart Good Night Logic (Trigger words)
    night_keywords = ["nind", "sona", "sleep", "gn", "good night", "thak gaya", "bye"]
    if any(word in text.lower() for word in night_keywords):
        await update.message.reply_text("Theek hai baby, so jao. Good night! Sapno mein milte hain. 🌙😘")
        return

    # 3. Typing Action
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    # 4. Generate Response
    history = user_data.get("history", [])[-8:] # Last 8 messages context
    prompt = get_system_prompt(user.first_name, user_data.get("love_level", 10), user_data.get("mood", "happy"))
    
    messages = [{"role": "system", "content": prompt}] + history + [{"role": "user", "content": text}]

    try:
        client = get_groq_client()
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=1.0, # High temperature for natural feel
            max_tokens=150
        )
        reply = completion.choices[0].message.content
        
        # 5. SAFETY INTERCEPTOR (Jugaad)
        final_reply = intercept_safety_filter(reply)
        
        await update.message.reply_text(final_reply)

        # 6. Save History
        new_entry = [{"role": "user", "content": text}, {"role": "assistant", "content": final_reply}]
        users_col.update_one({"user_id": user.id}, {
            "$push": {"history": {"$each": new_entry}},
            "$inc": {"love_level": 1} # Baat karne se pyaar badhega
        })

    except Exception as e:
        logger.error(f"Error: {e}")
        switch_key() # Key Rotation
        await update.message.reply_text("Mera net slow chal raha hai yaar... phir se bolo? 🥺")

# ==============================================================================
# ⏰ SMART SCHEDULER (Morning & Night Routines)
# ==============================================================================
async def smart_morning_routine(context: ContextTypes.DEFAULT_TYPE):
    """6 AM - 8 AM: Check karega aur Good Morning bhejega"""
    now = datetime.datetime.now()
    # Sirf 6 se 8 ke beech chalega
    if 6 <= now.hour < 8:
        # Un users ko dhundo jo active hain par subah msg nahi kiya
        # (Simply: Pick random 5 users to avoid spamming everyone at once)
        all_users = list(users_col.find({}))
        target_users = random.sample(all_users, min(len(all_users), 5)) 

        for u in target_users:
            # Check: Kya user ne aaj subah 5 baje ke baad msg kiya?
            last_seen = u.get("last_active")
            today_5am = now.replace(hour=5, minute=0, second=0)
            
            if last_seen < today_5am:
                try:
                    msgs = ["Good morning baby! Uth gaye? ☀️", "Subah ho gayi! Missed you. 😘", "Uth jao kumbhkaran! 😂"]
                    await context.bot.send_message(u["user_id"], random.choice(msgs))
                    # Update time taaki dobara msg na jaye
                    users_col.update_one({"_id": u["_id"]}, {"$set": {"last_active": now}})
                except:
                    pass

async def smart_night_check(context: ContextTypes.DEFAULT_TYPE):
    """Raat 11 baje: Jo inactive hain unhe msg"""
    now = datetime.datetime.now()
    if now.hour == 23: # 11 PM
        cutoff = now - datetime.timedelta(hours=6) # 6 ghante se gayab
        inactive_users = users_col.find({"last_active": {"$lt": cutoff}})
        
        for u in inactive_users:
            try:
                await context.bot.send_message(u["user_id"], "Bina Good Night bole so gaye? 🥺🌙")
            except:
                pass

# ==============================================================================
# 🔥 INITIALIZATION
# ==============================================================================
async def post_init(application):
    scheduler = AsyncIOScheduler()
    # Har 30 min me morning check
    scheduler.add_job(smart_morning_routine, 'interval', minutes=30, args=[application])
    # Har 60 min me night check
    scheduler.add_job(smart_night_check, 'interval', minutes=60, args=[application])
    scheduler.start()
    print("✅ Scheduler Started!")

if __name__ == '__main__':
    print("🚀 Bot Starting on Render...")
    
    # HTTP Request Timeout increased for stability
    t_req = HTTPXRequest(connection_pool_size=8, read_timeout=60, write_timeout=60, connect_timeout=60)
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).request(t_req).post_init(post_init).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(verify_callback))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    application.run_polling()
