
import logging
import asyncio
import os
import uuid
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from urllib.parse import urlparse

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardMarkup,
    KeyboardButton,
    Bot
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# Flask for Replit health check
from flask import Flask
from threading import Thread

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# REPLIT CONFIGURATION - ADD THESE IN SECRETS
# ============================================
# Click the "Secrets" tool (lock icon) in left sidebar
# Add these keys:
#   BOT_TOKEN = your_bot_token_from_botfather
#   ADMIN_USER_ID = your_telegram_user_id
#   DATABASE_URL = your_supabase_postgresql_connection_string
#   SUPPORT_EMAIL = your_support_email

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
DATABASE_URL = os.environ["DATABASE_URL"]
MIN_WITHDRAWAL = 100  # USD
REFERRAL_BONUS = 10  # USD
SPECIAL_OFFER_MULTIPLIER = 2
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@yourcompany.com")

# Flask app for health check (keeps Replit running)
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "iBoost Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# ============================================
# POSTGRESQL DATABASE FUNCTIONS
# ============================================

def get_db_connection():
    """Get PostgreSQL connection from DATABASE_URL"""
    result = urlparse(DATABASE_URL)
    connection = psycopg2.connect(
        database=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port,
        sslmode='require'
    )
    return connection

def init_db():
    """Initialize PostgreSQL tables"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            email TEXT UNIQUE,
            username TEXT,
            balance DECIMAL(10,2) DEFAULT 0,
            referral_bonus DECIMAL(10,2) DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            special_offer_status TEXT DEFAULT 'Inactive',
            special_offer_expiry TEXT,
            withdrawal_cooldown TEXT,
            referred_by BIGINT,
            join_date TEXT
        )
    ''')
    
    # Trades table
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            user_id BIGINT,
            card_type TEXT,
            entered_amount DECIMAL(10,2),
            credit_amount DECIMAL(10,2),
            special_offer_applied INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Pending',
            image_file_id TEXT,
            decline_reason TEXT,
            created_at TEXT
        )
    ''')
    
    # Withdrawals table
    c.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            withdrawal_id TEXT PRIMARY KEY,
            user_id BIGINT,
            amount DECIMAL(10,2),
            method TEXT,
            details TEXT,
            status TEXT DEFAULT 'Pending',
            decline_reason TEXT,
            created_at TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("PostgreSQL database initialized successfully")

# Initialize on startup
init_db()

# Database helper functions
def get_user(user_id: int) -> Optional[Dict]:
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def create_user(user_id: int, email: str, username: str, referred_by: int = None):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO users (user_id, email, username, join_date, referred_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
    ''', (user_id, email, username, datetime.now().isoformat(), referred_by))
    conn.commit()
    conn.close()

def update_user(user_id: int, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()
    for key, value in kwargs.items():
        c.execute(f"UPDATE users SET {key} = %s WHERE user_id = %s", (value, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT user_id, username, balance, special_offer_status FROM users")
    users = c.fetchall()
    conn.close()
    return users

def create_trade(user_id: int, card_type: str, amount: float, image_file_id: str) -> str:
    trade_id = str(uuid.uuid4())[:8].upper()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO trades (trade_id, user_id, card_type, entered_amount, image_file_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (trade_id, user_id, card_type, amount, image_file_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return trade_id

def get_trade(trade_id: str) -> Optional[Dict]:
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT * FROM trades WHERE trade_id = %s", (trade_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_trade(trade_id: str, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()
    for key, value in kwargs.items():
        c.execute(f"UPDATE trades SET {key} = %s WHERE trade_id = %s", (value, trade_id))
    conn.commit()
    conn.close()

def create_withdrawal(user_id: int, amount: float, method: str, details: str) -> str:
    withdrawal_id = str(uuid.uuid4())[:8].upper()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO withdrawals (withdrawal_id, user_id, amount, method, details, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (withdrawal_id, user_id, amount, method, details, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return withdrawal_id

def get_withdrawal(withdrawal_id: str) -> Optional[Dict]:
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT * FROM withdrawals WHERE withdrawal_id = %s", (withdrawal_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_withdrawal(withdrawal_id: str, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()
    for key, value in kwargs.items():
        c.execute(f"UPDATE withdrawals SET {key} = %s WHERE withdrawal_id = %s", (value, withdrawal_id))
    conn.commit()
    conn.close()

# ============================================
# KEYBOARD LAYOUTS
# ============================================

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🏠 Home"), KeyboardButton("👤 Profile")],
        [KeyboardButton("💳 Trade"), KeyboardButton("💰 Withdraw")],
        [KeyboardButton("👥 Refer Friends"), KeyboardButton("❓ Help & Support")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_home_back_keyboard():
    keyboard = [
        [KeyboardButton("🏠 Home"), KeyboardButton("⬅️ Back")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_card_types_keyboard():
    keyboard = [
        [KeyboardButton("Amazon"), KeyboardButton("Apple / iTunes")],
        [KeyboardButton("Google Play"), KeyboardButton("Steam")],
        [KeyboardButton("Visa"), KeyboardButton("Others")],
        [KeyboardButton("🏠 Home"), KeyboardButton("⬅️ Back")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_withdraw_source_keyboard():
    keyboard = [
        [KeyboardButton("💵 Balance"), KeyboardButton("🎁 Referral Bonus")],
        [KeyboardButton("🏠 Home"), KeyboardButton("⬅️ Back")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_withdraw_methods_keyboard():
    keyboard = [
        [KeyboardButton("PayPal"), KeyboardButton("Cash App")],
        [KeyboardButton("Credit/Debit Card"), KeyboardButton("OPay")],
        [KeyboardButton("PalmPay"), KeyboardButton("Bank Transfer")],
        [KeyboardButton("Crypto"), KeyboardButton("Wise")],
        [KeyboardButton("Payoneer"), KeyboardButton("Skrill")],
        [KeyboardButton("🏠 Home"), KeyboardButton("⬅️ Back")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ============================================
# CONVERSATION STATES
# ============================================

(
    EMAIL_INPUT, USERNAME_INPUT, CARD_AMOUNT_INPUT, 
    CARD_IMAGE_UPLOAD, CREDIT_AMOUNT_INPUT, WITHDRAW_AMOUNT_INPUT,
    WITHDRAW_DETAILS_INPUT, REFERRAL_CHECK
) = range(8)

# ============================================
# COMMAND HANDLERS
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - Entry point"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    # Check if user came from referral link
    args = context.args
    if args and not user:
        referred_by = int(args[0])
        context.user_data['referred_by'] = referred_by
    
    if user:
        await update.message.reply_text(
            f"👋 Welcome back, {user['username']}!\n\n"
            f"Your account has been restored.",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "🎉 Welcome to iBoost Card Trades!\n\n"
            "To get started, please enter your email address:"
        )
        return EMAIL_INPUT

async def email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle email input"""
    email = update.message.text.strip()
    
    if "@" not in email or "." not in email:
        await update.message.reply_text(
            "❌ Invalid email format. Please enter a valid email:"
        )
        return EMAIL_INPUT
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = %s", (email,))
    existing = c.fetchone()
    conn.close()
    
    if existing:
        await update.message.reply_text(
            "📧 This email exists in our system.\n"
            "Your account has been restored!",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    
    context.user_data['email'] = email
    await update.message.reply_text(
        "✅ Email received!\n\n"
        "Now please enter your username:"
    )
    return USERNAME_INPUT

async def username_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle username input and create profile"""
    username = update.message.text.strip()
    user_id = update.effective_user.id
    email = context.user_data.get('email')
    referred_by = context.user_data.get('referred_by')
    
    create_user(user_id, email, username, referred_by)
    
    admin_message = (
        f"🆕 New User Registered\n\n"
        f"👤 Username: {username}\n"
        f"📧 Email: {email}\n"
        f"🆔 User ID: {user_id}\n"
        f"💰 Balance: $0.00\n"
        f"🎁 Referral Bonus: $0.00\n"
        f"📊 Total Trades: 0\n"
        f"👥 Referral Count: 0\n"
        f"⭐ Special Offer Status: Inactive"
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ Activate Special Offer", callback_data=f"activate_special_{user_id}"),
            InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_special_{user_id}")
        ]
    ])
    
    await context.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text=admin_message,
        reply_markup=keyboard
    )
    
    await update.message.reply_text(
        f"🎉 Account created successfully!\n\n"
        f"Welcome, {username}!\n"
        f"You can now start trading gift cards.",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# ============================================
# MAIN MENU HANDLERS
# ============================================

async def home_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle home button"""
    await update.message.reply_text(
        "🏠 Main Menu\n\n"
        "Select an option below:",
        reply_markup=get_main_keyboard()
    )

async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user profile"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        await update.message.reply_text("❌ Please start the bot first with /start")
        return
    
    expiry = user['special_offer_expiry'] if user['special_offer_expiry'] else "None"
    cooldown = user['withdrawal_cooldown'] if user['withdrawal_cooldown'] else "None"
    
    profile_text = (
        f"👤 Your Profile\n\n"
        f"👤 Username: {user['username']}\n"
        f"📧 Email: {user['email']}\n"
        f"🆔 User ID: {user_id}\n"
        f"💰 Balance: ${float(user['balance']):.2f}\n"
        f"🎁 Referral Bonus: ${float(user['referral_bonus']):.2f}\n"
        f"📊 Total Trades: {user['total_trades']}\n"
        f"👥 Referral Count: {user['referral_count']}\n"
        f"⭐ Special Offer: {user['special_offer_status']}\n"
        f"⏰ Special Offer Expiry: {expiry}\n"
        f"🚫 Withdrawal Cooldown: {cooldown}"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Withdraw", callback_data="withdraw_profile")]
    ])
    
    await update.message.reply_text(profile_text, reply_markup=keyboard)

# ============================================
# TRADE FLOW
# ============================================

async def trade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start trade flow"""
    await update.message.reply_text(
        "💳 Select Gift Card Type:\n\n"
        "Choose the type of gift card you want to trade:",
        reply_markup=get_card_types_keyboard()
    )

async def card_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle card type selection"""
    card_type = update.message.text
    
    if card_type in ["🏠 Home", "⬅️ Back"]:
        if card_type == "🏠 Home":
            await home_handler(update, context)
        return ConversationHandler.END
    
    valid_types = ["Amazon", "Apple / iTunes", "Google Play", "Steam", "Visa", "Others"]
    if card_type not in valid_types:
        await update.message.reply_text("❌ Please select a valid card type.")
        return
    
    context.user_data['card_type'] = card_type
    await update.message.reply_text(
        f"✅ {card_type} selected!\n\n"
        f"Please enter the gift card amount (in USD):",
        reply_markup=get_home_back_keyboard()
    )
    return CARD_AMOUNT_INPUT

async def card_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle card amount input"""
    text = update.message.text
    
    if text == "🏠 Home":
        await home_handler(update, context)
        return ConversationHandler.END
    elif text == "⬅️ Back":
        await trade_handler(update, context)
        return ConversationHandler.END
    
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a valid amount (numbers only):"
        )
        return CARD_AMOUNT_INPUT
    
    context.user_data['card_amount'] = amount
    await update.message.reply_text(
        f"💳 Amount: ${amount:.2f}\n\n"
        f"Please upload a clear image of your gift card:",
        reply_markup=get_home_back_keyboard()
    )
    return CARD_IMAGE_UPLOAD

async def card_image_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle gift card image upload"""
    if update.message.text:
        if update.message.text == "🏠 Home":
            await home_handler(update, context)
            return ConversationHandler.END
        elif update.message.text == "⬅️ Back":
            await update.message.reply_text(
                "Please enter the gift card amount (in USD):"
            )
            return CARD_AMOUNT_INPUT
    
    if not update.message.photo:
        await update.message.reply_text(
            "❌ Please upload an image of your gift card:"
        )
        return CARD_IMAGE_UPLOAD
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    user_id = update.effective_user.id
    card_type = context.user_data.get('card_type')
    amount = context.user_data.get('card_amount')
    
    trade_id = create_trade(user_id, card_type, amount, file_id)
    user = get_user(user_id)
    
    admin_message = (
        f"🆕 New Trade Request\n\n"
        f"👤 Username: {user['username']}\n"
        f"🆔 User ID: {user_id}\n"
        f"💳 Card Type: {card_type}\n"
        f"💰 Entered Amount: ${amount:.2f}\n"
        f"⭐ Special Offer Status: {user['special_offer_status']}\n"
        f"🆔 Trade ID: {trade_id}"
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_trade_{trade_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline_trade_{trade_id}")
        ]
    ])
    
    await context.bot.send_photo(
        chat_id=ADMIN_USER_ID,
        photo=file_id,
        caption=admin_message,
        reply_markup=keyboard
    )
    
    await update.message.reply_text(
        f"✅ Trade Submitted!\n\n"
        f"Trade ID: {trade_id}\n"
        f"Status: Waiting for admin review\n\n"
        f"You'll be notified once reviewed.",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# ============================================
# WITHDRAWAL FLOW
# ============================================

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start withdrawal flow"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        await update.message.reply_text("❌ Please start the bot first with /start")
        return
    
    if user['withdrawal_cooldown']:
        cooldown_time = datetime.fromisoformat(user['withdrawal_cooldown'])
        if datetime.now() < cooldown_time:
            remaining = cooldown_time - datetime.now()
            hours = int(remaining.total_seconds() / 3600)
            await update.message.reply_text(
                f"⏳ Withdrawal Cooldown Active\n\n"
                f"You cannot withdraw for another {hours} hours.\n"
                f"Please try again later.",
                reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
    
    await update.message.reply_text(
        "💰 Withdraw From:\n\n"
        f"💵 Balance: ${float(user['balance']):.2f}\n"
        f"🎁 Referral Bonus: ${float(user['referral_bonus']):.2f}\n\n"
        "Select source:",
        reply_markup=get_withdraw_source_keyboard()
    )

async def withdraw_source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal source selection"""
    text = update.message.text
    
    if text == "🏠 Home":
        await home_handler(update, context)
        return ConversationHandler.END
    elif text == "⬅️ Back":
        await update.message.reply_text(
            "Select an option:",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if text == "🎁 Referral Bonus":
        if float(user['referral_bonus']) > 0:
            new_balance = float(user['balance']) + float(user['referral_bonus'])
            update_user(
                user_id, 
                balance=new_balance,
                referral_bonus=0
            )
            await update.message.reply_text(
                f"✅ Referral Bonus Transferred!\n\n"
                f"${float(user['referral_bonus']):.2f} added to your balance.\n"
                f"New Balance: ${new_balance:.2f}",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text(
                "❌ No referral bonus available.",
                reply_markup=get_main_keyboard()
            )
        return ConversationHandler.END
    
    if text == "💵 Balance":
        if float(user['balance']) < MIN_WITHDRAWAL:
            await update.message.reply_text(
                f"❌ Insufficient Balance\n\n"
                f"Minimum withdrawal: ${MIN_WITHDRAWAL}\n"
                f"Your balance: ${float(user['balance']):.2f}",
                reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
        
        await update.message.reply_text(
            "💳 Select Withdrawal Method:\n\n"
            "Your balance is in USD\n"
            "Withdrawal will be converted to local currency\n"
            "No charges applied",
            reply_markup=get_withdraw_methods_keyboard()
        )
        return WITHDRAW_AMOUNT_INPUT

async def withdraw_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal method selection"""
    method = update.message.text
    
    if method == "🏠 Home":
        await home_handler(update, context)
        return ConversationHandler.END
    elif method == "⬅️ Back":
        await withdraw_handler(update, context)
        return ConversationHandler.END
    
    valid_methods = ["PayPal", "Cash App", "Credit/Debit Card", "OPay", 
                     "PalmPay", "Bank Transfer", "Crypto", "Wise", 
                     "Payoneer", "Skrill"]
    
    if method not in valid_methods:
        await update.message.reply_text("❌ Please select a valid method.")
        return WITHDRAW_AMOUNT_INPUT
    
    context.user_data['withdraw_method'] = method
    user = get_user(update.effective_user.id)
    
    await update.message.reply_text(
        f"💰 Withdrawal Method: {method}\n\n"
        f"Available Balance: ${float(user['balance']):.2f}\n"
        f"Minimum: ${MIN_WITHDRAWAL}\n\n"
        f"Enter amount to withdraw (USD):",
        reply_markup=get_home_back_keyboard()
    )
    return WITHDRAW_DETAILS_INPUT

async def withdraw_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal amount input"""
    text = update.message.text
    
    if text == "🏠 Home":
        await home_handler(update, context)
        return ConversationHandler.END
    elif text == "⬅️ Back":
        await withdraw_handler(update, context)
        return ConversationHandler.END
    
    try:
        amount = float(text)
        if amount < MIN_WITHDRAWAL:
            raise ValueError(f"Minimum withdrawal is ${MIN_WITHDRAWAL}")
    except ValueError as e:
        await update.message.reply_text(
            f"❌ Invalid amount. {str(e)}\n\nEnter amount:"
        )
        return WITHDRAW_DETAILS_INPUT
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if amount > float(user['balance']):
        await update.message.reply_text(
            f"❌ Insufficient balance!\n"
            f"Your balance: ${float(user['balance']):.2f}\n\n"
            f"Enter amount:"
        )
        return WITHDRAW_DETAILS_INPUT
    
    context.user_data['withdraw_amount'] = amount
    await update.message.reply_text(
        f"💰 Amount: ${amount:.2f}\n\n"
        f"Please enter your payout details ({context.user_data['withdraw_method']}):",
        reply_markup=get_home_back_keyboard()
    )
    return WITHDRAW_DETAILS_INPUT

async def withdraw_details_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal details and submit"""
    text = update.message.text
    
    if text == "🏠 Home":
        await home_handler(update, context)
        return ConversationHandler.END
    elif text == "⬅️ Back":
        await update.message.reply_text("Enter amount to withdraw:")
        return WITHDRAW_DETAILS_INPUT
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    amount = context.user_data.get('withdraw_amount')
    method = context.user_data.get('withdraw_method')
    
    withdrawal_id = create_withdrawal(user_id, amount, method, text)
    
    admin_message = (
        f"💰 New Withdrawal Request\n\n"
        f"👤 Username: {user['username']}\n"
        f"🆔 User ID: {user_id}\n"
        f"💰 Balance: ${float(user['balance']):.2f}\n"
        f"💸 Withdrawal Amount: ${amount:.2f}\n"
        f"💳 Method: {method}\n"
        f"📋 Details: {text}\n\n"
        f"🆔 Withdrawal ID: {withdrawal_id}"
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_withdraw_{withdrawal_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline_withdraw_{withdrawal_id}")
        ]
    ])
    
    await context.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text=admin_message,
        reply_markup=keyboard
    )
    
    await update.message.reply_text(
        f"✅ Withdrawal Request Submitted!\n\n"
        f"Withdrawal ID: {withdrawal_id}\n"
        f"Amount: ${amount:.2f}\n"
        f"Method: {method}\n\n"
        f"Status: Pending admin review",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# ============================================
# REFERRAL SYSTEM
# ============================================

async def refer_friends_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral information"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    bot_info = await context.bot.get_me()
    
    referral_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    await update.message.reply_text(
        f"👥 Refer Friends & Earn!\n\n"
        f"💰 Earn ${REFERRAL_BONUS} per referral!\n\n"
        f"Your Referral Link:\n`{referral_link}`\n\n"
        f"📊 Stats:\n"
        f"👥 Referral Count: {user['referral_count']}\n"
        f"🎁 Referral Bonus: ${float(user['referral_bonus']):.2f}\n\n"
        f"Bonus credited when referred user completes first trade!",
        reply_markup=get_home_back_keyboard(),
        parse_mode='Markdown'
    )

# ============================================
# HELP & SUPPORT
# ============================================

async def help_support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help and support information"""
    await update.message.reply_text(
        f"❓ Help & Support\n\n"
        f"Thank you for using iBoost Card Trades.\n\n"
        f"If you are experiencing any issues with trades, withdrawals, "
        f"referrals, or your account, our support team is available to assist you.\n\n"
        f"For any enquiries, complaints, or assistance, please contact:\n"
        f"📧 {SUPPORT_EMAIL}\n\n"
        f"To help us resolve your issue faster, please include:\n"
        f"• Username\n"
        f"• User ID\n"
        f"• Description of issue\n"
        f"• Screenshot (if available)\n\n"
        f"Our support team typically responds within 24 hours.\n\n"
        f"Thank you for choosing iBoost Card Trades!",
        reply_markup=get_home_back_keyboard()
    )

# ============================================
# CALLBACK QUERY HANDLERS (ADMIN ACTIONS)
# ============================================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Special offer activation
    if data.startswith("activate_special_"):
        user_id = int(data.split("_")[2])
        expiry_date = "2026-08-31"
        
        update_user(
            user_id,
            special_offer_status="Active",
            special_offer_expiry=expiry_date
        )
        
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"🎉 Special Offer Activated!\n\n"
                f"You are among our first set of users.\n\n"
                f"You now have access to a special offer which allows you to "
                f"boost your gift cards and receive 2x the credited amount.\n\n"
                f"This offer expires on August 31, 2026.\n\n"
                f"Start trading now to enjoy this limited-time offer!"
            )
        )
        
        await query.edit_message_text(
            query.message.text + "\n\n✅ Special Offer Activated!"
        )
    
    elif data.startswith("ignore_special_"):
        await query.edit_message_text(
            query.message.text + "\n\n❌ Ignored"
        )
    
    # Trade approval
    elif data.startswith("accept_trade_"):
        trade_id = data.split("_")[2]
        context.user_data['pending_trade_id'] = trade_id
        
        await query.edit_message_text(
            query.message.text + "\n\n💰 Enter amount to credit user:"
        )
        
        context.user_data['admin_message_id'] = query.message.message_id
        context.user_data['admin_chat_id'] = query.message.chat_id
        
        return CREDIT_AMOUNT_INPUT
    
    elif data.startswith("decline_trade_"):
        trade_id = data.split("_")[2]
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📷 Blurry Image", callback_data=f"decline_reason_blurry_{trade_id}"),
                InlineKeyboardButton("🚫 Invalid/Used Card", callback_data=f"decline_reason_invalid_{trade_id}")
            ]
        ])
        
        await query.edit_message_text(
            query.message.text + "\n\n❌ Select decline reason:",
            reply_markup=keyboard
        )
    
    elif data.startswith("decline_reason_"):
        parts = data.split("_")
        reason = parts[2]
        trade_id = parts[3]
        
        reason_text = "Blurry Image" if reason == "blurry" else "Invalid or Already Used Card"
        
        update_trade(trade_id, status="Declined", decline_reason=reason_text)
        trade = get_trade(trade_id)
        
        await context.bot.send_message(
            chat_id=trade['user_id'],
            text=(
                f"❌ Trade Declined\n\n"
                f"Trade ID: {trade_id}\n"
                f"Reason: {reason_text}\n\n"
                f"Please submit a new trade with a valid card."
            )
        )
        
        await query.edit_message_text(
            query.message.text + f"\n\n❌ Declined: {reason_text}"
        )
    
    # Withdrawal approval
    elif data.startswith("accept_withdraw_"):
        withdrawal_id = data.split("_")[2]
        withdrawal = get_withdrawal(withdrawal_id)
        user = get_user(withdrawal['user_id'])
        
        new_balance = float(user['balance']) - float(withdrawal['amount'])
        update_user(withdrawal['user_id'], balance=new_balance)
        update_withdrawal(withdrawal_id, status="Approved")
        
        await context.bot.send_message(
            chat_id=withdrawal['user_id'],
            text=(
                f"✅ Withdrawal Approved!\n\n"
                f"Withdrawal ID: {withdrawal_id}\n"
                f"Amount: ${float(withdrawal['amount']):.2f}\n"
                f"Method: {withdrawal['method']}\n\n"
                f"Your funds are being processed manually.\n"
                f"New Balance: ${new_balance:.2f}"
            )
        )
        
        await query.edit_message_text(
            query.message.text + "\n\n✅ Withdrawal Approved & Balance Deducted"
        )
    
    elif data.startswith("decline_withdraw_"):
        withdrawal_id = data.split("_")[2]
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💰 Insufficient Balance", callback_data=f"wdecline_insufficient_{withdrawal_id}"),
                InlineKeyboardButton("📉 Below Minimum ($100)", callback_data=f"wdecline_minimum_{withdrawal_id}")
            ],
            [
                InlineKeyboardButton("⚠️ System Error (+$10)", callback_data=f"wdecline_system_{withdrawal_id}")
            ]
        ])
        
        await query.edit_message_text(
            query.message.text + "\n\n❌ Select decline reason:",
            reply_markup=keyboard
        )
    
    elif data.startswith("wdecline_"):
        parts = data.split("_")
        reason_type = parts[1]
        withdrawal_id = parts[2]
        
        withdrawal = get_withdrawal(withdrawal_id)
        user_id = withdrawal['user_id']
        
        if reason_type == "insufficient":
            reason_text = "Insufficient Balance"
        elif reason_type == "minimum":
            reason_text = "Below Minimum Withdrawal ($100)"
        else:
            reason_text = "System Error"
            user = get_user(user_id)
            new_balance = float(user['balance']) + 10
            update_user(user_id, balance=new_balance)
            
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"⚠️ System Error Compensation\n\n"
                    f"We apologize for the inconvenience.\n"
                    f"$10 has been credited to your account.\n"
                    f"New Balance: ${new_balance:.2f}"
                )
            )
        
        if reason_type != "system":
            cooldown_time = (datetime.now() + timedelta(hours=24)).isoformat()
            update_user(user_id, withdrawal_cooldown=cooldown_time)
        
        update_withdrawal(withdrawal_id, status="Declined", decline_reason=reason_text)
        
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"❌ Withdrawal Declined\n\n"
                f"Withdrawal ID: {withdrawal_id}\n"
                f"Reason: {reason_text}\n\n"
                + (f"⏳ 24-hour withdrawal cooldown applied.\n" if reason_type != "system" else "")
            )
        )
        
        await query.edit_message_text(
            query.message.text + f"\n\n❌ Declined: {reason_text}"
        )
    
    elif data == "withdraw_profile":
        await withdraw_handler(update, context)

async def admin_credit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin credit amount input"""
    if update.effective_user.id != ADMIN_USER_ID:
        return
    
    text = update.message.text
    trade_id = context.user_data.get('pending_trade_id')
    
    if not trade_id:
        return
    
    try:
        credit_amount = float(text)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number:")
        return CREDIT_AMOUNT_INPUT
    
    trade = get_trade(trade_id)
    user = get_user(trade['user_id'])
    
    final_amount = credit_amount
    special_applied = False
    
    if user['special_offer_status'] == "Active":
        final_amount = credit_amount * SPECIAL_OFFER_MULTIPLIER
        special_applied = True
    
    new_balance = float(user['balance']) + final_amount
    new_total_trades = user['total_trades'] + 1
    
    update_user(
        trade['user_id'],
        balance=new_balance,
        total_trades=new_total_trades
    )
    
    update_trade(
        trade_id,
        credit_amount=final_amount,
        special_offer_applied=1 if special_applied else 0,
        status="Approved"
    )
    
    if user['referred_by']:
        referrer = get_user(user['referred_by'])
        if referrer and user['total_trades'] == 0:
            new_ref_bonus = float(referrer['referral_bonus']) + REFERRAL_BONUS
            new_ref_count = referrer['referral_count'] + 1
            update_user(
                user['referred_by'],
                referral_bonus=new_ref_bonus,
                referral_count=new_ref_count
            )
            
            await context.bot.send_message(
                chat_id=user['referred_by'],
                text=(
                    f"🎉 Referral Bonus Earned!\n\n"
                    f"Your referral {user['username']} completed their first trade!\n"
                    f"${REFERRAL_BONUS} added to your referral bonus."
                )
            )
    
    if special_applied:
        message = (
            f"✅ Trade Approved!\n\n"
            f"Original Credit: ${credit_amount:.2f}\n"
            f"⭐ Special Offer Boost: 2x\n"
            f"💰 Total Credited: ${final_amount:.2f}\n\n"
            f"Balance Updated: ${new_balance:.2f}"
        )
    else:
        message = (
            f"✅ Trade Approved!\n\n"
            f"Amount Credited: ${final_amount:.2f}\n\n"
            f"Balance Updated: ${new_balance:.2f}"
        )
    
    await context.bot.send_message(chat_id=trade['user_id'], text=message)
    
    admin_msg_id = context.user_data.get('admin_message_id')
    admin_chat_id = context.user_data.get('admin_chat_id')
    
    if admin_msg_id and admin_chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=admin_msg_id,
                text=(
                    f"✅ Trade Approved\n\n"
                    f"Trade ID: {trade_id}\n"
                    f"Credited: ${final_amount:.2f}"
                    + (f" (2x Special Offer)" if special_applied else "")
                )
            )
        except:
            pass
    
    await update.message.reply_text(f"✅ Trade {trade_id} approved. User credited ${final_amount:.2f}")
    
    context.user_data.pop('pending_trade_id', None)
    context.user_data.pop('admin_message_id', None)
    context.user_data.pop('admin_chat_id', None)
    
    return ConversationHandler.END

# ============================================
# ADMIN COMMANDS
# ============================================

async def admin_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /user command"""
    if update.effective_user.id != ADMIN_USER_ID:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /user <USER_ID>")
        return
    
    user_id = int(context.args[0])
    user = get_user(user_id)
    
    if not user:
        await update.message.reply_text("❌ User not found")
        return
    
    await update.message.reply_text(
        f"👤 User Profile\n\n"
        f"Username: {user['username']}\n"
        f"Email: {user['email']}\n"
        f"User ID: {user_id}\n"
        f"Balance: ${float(user['balance']):.2f}\n"
        f"Referral Bonus: ${float(user['referral_bonus']):.2f}\n"
        f"Total Trades: {user['total_trades']}\n"
        f"Special Offer: {user['special_offer_status']}"
    )

async def admin_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /message command"""
    if update.effective_user.id != ADMIN_USER_ID:
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /message <USER_ID> <MESSAGE>")
        return
    
    user_id = int(context.args[0])
    message = " ".join(context.args[1:])
    
    try:
        await context.bot.send_message(chat_id=user_id, text=f"📩 Message from Admin:\n\n{message}")
        await update.message.reply_text("✅ Message sent")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send: {str(e)}")

async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /broadcast command"""
    if update.effective_user.id != ADMIN_USER_ID:
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <MESSAGE>")
        return
    
    message = " ".join(context.args)
    users = get_all_users()
    
    sent = 0
    failed = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0], 
                text=f"📢 Broadcast Message:\n\n{message}"
            )
            sent += 1
        except:
            failed += 1
    
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users\n❌ Failed: {failed}")

async def admin_allusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /allusers command"""
    if update.effective_user.id != ADMIN_USER_ID:
        return
    
    users = get_all_users()
    
    if not users:
        await update.message.reply_text("No users found")
        return
    
    text = "📊 All Users:\n\n"
    for user in users:
        text += f"👤 {user[1]} | ID: {user[0]} | ${float(user[2]):.2f} | {user[3]}\n"
    
    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(text)

async def admin_addbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /addbalance command"""
    if update.effective_user.id != ADMIN_USER_ID:
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /addbalance <USER_ID> <AMOUNT>")
        return
    
    user_id = int(context.args[0])
    amount = float(context.args[1])
    
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("❌ User not found")
        return
    
    new_balance = float(user['balance']) + amount
    update_user(user_id, balance=new_balance)
    
    await context.bot.send_message(
        chat_id=user_id,
        text=f"💰 Balance Updated!\n\n${amount:.2f} added.\nNew Balance: ${new_balance:.2f}"
    )
    
    await update.message.reply_text(f"✅ Added ${amount:.2f} to user {user_id}")

async def admin_deductbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deductbalance command"""
    if update.effective_user.id != ADMIN_USER_ID:
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /deductbalance <USER_ID> <AMOUNT>")
        return
    
    user_id = int(context.args[0])
    amount = float(context.args[1])
    
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("❌ User not found")
        return
    
    new_balance = max(0, float(user['balance']) - amount)
    update_user(user_id, balance=new_balance)
    
    await context.bot.send_message(
        chat_id=user_id,
        text=f"⚠️ Balance Updated!\n\n${amount:.2f} deducted.\nNew Balance: ${new_balance:.2f}"
    )
    
    await update.message.reply_text(f"✅ Deducted ${amount:.2f} from user {user_id}")

# ============================================
# TEXT MESSAGE HANDLER
# ============================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages based on current state or keyboard buttons"""
    text = update.message.text
    
    if text == "🏠 Home":
        await home_handler(update, context)
    elif text == "👤 Profile":
        await profile_handler(update, context)
    elif text == "💳 Trade":
        await trade_handler(update, context)
    elif text == "💰 Withdraw":
        await withdraw_handler(update, context)
    elif text == "👥 Refer Friends":
        await refer_friends_handler(update, context)
    elif text == "❓ Help & Support":
        await help_support_handler(update, context)
    elif text in ["Amazon", "Apple / iTunes", "Google Play", "Steam", "Visa", "Others"]:
        await card_type_selected(update, context)
    elif text in ["💵 Balance", "🎁 Referral Bonus"]:
        await withdraw_source_selected(update, context)
    elif text in ["PayPal", "Cash App", "Credit/Debit Card", "OPay", "PalmPay", 
                  "Bank Transfer", "Crypto", "Wise", "Payoneer", "Skrill"]:
        await withdraw_method_selected(update, context)
    elif text == "⬅️ Back":
        await update.message.reply_text(
            "Select an option:",
            reply_markup=get_main_keyboard()
        )

# ============================================
# MAIN FUNCTION
# ============================================

def main():
    """Start the bot"""
    # Start Flask health check server in background
    Thread(target=run_flask, daemon=True).start()
    logger.info("Health check server started on port 8080")
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler for registration and trade flow
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
        ],
        states={
            EMAIL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, email_input)],
            USERNAME_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, username_input)],
            CARD_AMOUNT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_amount_input)],
            CARD_IMAGE_UPLOAD: [
                MessageHandler(filters.PHOTO, card_image_upload),
                MessageHandler(filters.TEXT & ~filters.COMMAND, card_image_upload)
            ],
            CREDIT_AMOUNT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_credit_input)
            ],
            WITHDRAW_AMOUNT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_method_selected)
            ],
            WITHDRAW_DETAILS_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_details_input)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    
    application.add_handler(conv_handler)
    
    # Admin commands
    application.add_handler(CommandHandler("user", admin_user_command))
    application.add_handler(CommandHandler("message", admin_message_command))
    application.add_handler(CommandHandler("broadcast", admin_broadcast_command))
    application.add_handler(CommandHandler("allusers", admin_allusers_command))
    application.add_handler(CommandHandler("addbalance", admin_addbalance_command))
    application.add_handler(CommandHandler("deductbalance", admin_deductbalance_command))
    
    # Callback queries
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
