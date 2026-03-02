BOT_TOKEN = "8604583606:AAHe5KVCT4t7Nu8RwrAzwead-B0KxHfcnk4"
API_ID = 38042871
API_HASH = "8716597899e920d87d8d1179f5b04f67"

# Admin user IDs who can use /addsessions
ADMIN_IDS = [7243305432]  # Replace with your Telegram user ID(s)

import logging
import asyncio
import sqlite3
from datetime import datetime
from typing import Optional, List
from collections import deque
# Rate limiter for outgoing messages (30 per second)
MESSAGE_SEMAPHORE = asyncio.Semaphore(30)
MESSAGE_RESET_INTERVAL = 1.0  # 1 second

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telethon import TelegramClient, errors
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest
from telethon.sessions import StringSession
from telethon.tl.types import InputReportReasonSpam
import psutil
import os
import time
# -------------------- CONFIGURATION --------------------
REQUIRED_CHANNELS = [
    "@daxbots",
    "@firstdaxlord",
    "@daxlordbio",
    "@daxgrp"
]

# Premium Emoji IDs – replace with your own from @Stickers or @EmojiBot
EMOJI = {
    "star": "5415796104891483565",      # ⭐
    "plus": "4956507094124594921",      # ➕
    "ask": "5452069934089641166",
    "megaphone": "6179070814831251950",
    "pass": "6248827899831391314",
    "rocket": "5395695537687123235",    # 🚨
    "phone": "5453965363286925977",     # 📞
    "inbox": "5406809207947142040",     # 📲
    "lock": "5393302369024882368",      # 🔒
    "check": "6235478849417647339",     # ✅
    "cross": "6179128006615765757",     # ❌
    "warning": "5447644880824181073",   # ⚠️
    "hourglass": "5319190934510904031", # ⏳
    "magnifier": "5319230516929502602", # 🔍
    "wave": "5343984088493599366",      # 👋
    "channel": "5798687882369569347",   # 📢  (replace)
    "group": "5453957997418004470",     # 👥  (replace)
    "document": "5258477770735885832",  # 📄  (replace)
    "add": "6147565374289220368",
    "hehe": "5336957002306301528",
    "lol": "6093467039970629408",
    "loading": "5093619977974448887",
    "gear": "5462921117423384478",
    "chart": "6001546944470587024",
    "back": "5400169738263352182",
    "refresh": "5452002073606384268",
}

def fmt_emoji(name: str, fallback: str = "") -> str:
    if not fallback:
        fallback = "•"
    if name in EMOJI and EMOJI[name]:
        return f'<tg-emoji emoji-id="{EMOJI[name]}">{fallback}</tg-emoji>'
    return fallback

REPORT_COOLDOWN = 10 * 60 * 60  # 10 hours in seconds

def calculate_chance(num_accounts: int) -> int:
    return min(num_accounts * 2, 80)

# -------------------- STRONG DEFAULT REASONS --------------------
STRONG_REASONS = {
    "spam": (
        "Entity is operating an automated adversarial network for high-volume commercial exploitation. "
        "Behavior includes obfuscated URI redirects to circumvent security filters and the deployment "
        "of socially engineered financial lures. This represents a systematic effort to degrade "
        "platform utility and exploit vulnerable users."
    ),
    "harassment": (
        "Account is a primary vector for targeted psychological warfare and the weaponization of "
        "Personally Identifiable Information (PII). It orchestrates cross-platform brigading and "
        "incites violence through dehumanizing rhetoric, creating an actionable safety liability "
        "and a direct violation of international human rights protections."
    ),
    "impersonation": (
        "This is a high-fidelity fraudulent mirror designed for credential harvesting and financial "
        "exfiltration. By spoofing verified cryptographic signatures and metadata, the actor is "
        "executing Man-in-the-Middle (MITM) social attacks. Immediate suspension is required to "
        "mitigate ongoing capital theft and identity compromise."
    ),
    "illegal": (
        "This entity functions as a clearinghouse for prohibited contraband and the dissemination "
        "of terroristic material. It facilitates illicit peer-to-peer transactions that bypass "
        "regulatory AML/KYC frameworks. The content poses an imminent threat to public safety "
        "and constitutes a felony-level breach of international cyber-statutes."
    ),
    "botnet": (
        "Forensic analysis indicates this node is part of a coordinated Sybil infrastructure executing "
        "engagement manipulation. The account exhibits sub-second response latencies and algorithmic "
        "synchronization designed to hijack consensus mechanisms and distort the platform's social "
        "graph, threatening decentralized integrity."
    )
}

# -------------------- DATABASE --------------------
DB_PATH = "bot_database.db"

BOT_START_TIME = time.time()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Create accounts table with active column
    c.execute('''
              CREATE TABLE IF NOT EXISTS accounts
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  user_id
                  INTEGER
                  NOT
                  NULL,
                  session_string
                  TEXT
                  NOT
                  NULL
                  UNIQUE,
                  active
                  INTEGER
                  DEFAULT
                  1,
                  created_at
                  TIMESTAMP
                  DEFAULT
                  CURRENT_TIMESTAMP
              )
              ''')

    # Check if active column exists (for older databases)
    c.execute("PRAGMA table_info(accounts)")
    columns = [col[1] for col in c.fetchall()]
    if 'active' not in columns:
        c.execute("ALTER TABLE accounts ADD COLUMN active INTEGER DEFAULT 1")
        print("Added 'active' column to accounts table.")

    # Users table
    c.execute('''
              CREATE TABLE IF NOT EXISTS users
              (
                  user_id
                  INTEGER
                  PRIMARY
                  KEY,
                  first_seen
                  TIMESTAMP
                  DEFAULT
                  CURRENT_TIMESTAMP,
                  last_active
                  TIMESTAMP
                  DEFAULT
                  CURRENT_TIMESTAMP
              )
              ''')

    c.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON accounts(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session_string ON accounts(session_string)')
    conn.commit()
    conn.close()

def remove_duplicates():
    """Remove duplicate session strings, keeping the oldest one."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Find and delete duplicates, keeping the one with smallest id
    c.execute('''
              DELETE
              FROM accounts
              WHERE id NOT IN (SELECT MIN(id)
                               FROM accounts
                               GROUP BY session_string)
              ''')

    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"Removed {deleted} duplicate entries")

def orphan_account(account_id: int, user_id: int) -> bool:
    """
    Remove user association from an account (set user_id = 0)
    but keep it active for global pool.
    Returns True if successful, False if account not found or not owned by user.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        UPDATE accounts 
        SET user_id = 0 
        WHERE id = ? AND user_id = ? AND active = 1
    ''', (account_id, user_id))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def get_all_user_ids() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def update_user_activity(user_id: int):
    """Insert or update user's last active timestamp."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO users (user_id, last_active) 
        VALUES (?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET last_active = CURRENT_TIMESTAMP
    ''', (user_id,))
    conn.commit()
    conn.close()

def add_account(user_id: int, session_string: str) -> bool:
    """
    Store a new account session for a user.
    Returns True if added, False if duplicate.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO accounts (user_id, session_string) VALUES (?, ?)',
                  (user_id, session_string))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Session string already exists
        conn.rollback()
        return False
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error in add_account: {e}")
        return False
    finally:
        conn.close()

def get_all_accounts() -> List[str]:
    """Return all active session strings (global pool)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT session_string FROM accounts WHERE active = 1')
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def count_all_accounts() -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM accounts WHERE active = 1')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_user_accounts(user_id: int) -> List[str]:
    """Return session strings for a user's active accounts."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT session_string FROM accounts WHERE user_id = ? AND active = 1', (user_id,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def count_user_accounts(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM accounts WHERE user_id = ? AND active = 1', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_user_accounts_with_ids(user_id: int) -> List[tuple]:
    """Return list of (id, session_string, active) for user's accounts (only active ones)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, session_string, active FROM accounts WHERE user_id = ? AND active = 1', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

init_db()

# -------------------- LOGGING --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- CONSTANTS --------------------
PHONE, CODE, PASSWORD = range(3)
REPORT_TARGET, REPORT_TYPE, REPORT_REASON = range(10, 13)

# -------------------- GLOBAL REPORT QUEUE --------------------
class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.tokens = max_calls
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_calls, self.tokens + elapsed * (self.max_calls / self.period))
            self.last_refill = now

            if self.tokens < 1:
                wait = (1 - self.tokens) * (self.period / self.max_calls)
                await asyncio.sleep(wait)
                self.tokens = 0
                self.last_refill = time.monotonic()
            else:
                self.tokens -= 1


MESSAGE_RATE_LIMITER = RateLimiter(30, 1.0)  # 30 messages per second

class ReportQueue:
    def __init__(self):
        self.queue = deque()  # each item: (user_id, target, reason, report_type, estimated_start)
        self.last_report_time: Optional[float] = None
        self.processing = False
        self.lock = asyncio.Lock()
        self.app: Optional[Application] = None

    def set_application(self, app: Application):
        self.app = app

    async def add_report(self, user_id: int, target: str, reason: str, report_type: str):
        async with self.lock:
            if self.last_report_time is None:
                estimated_start = time.time()
            else:
                queue_position = len(self.queue)
                next_slot = max(time.time(), self.last_report_time + REPORT_COOLDOWN) + queue_position * REPORT_COOLDOWN
                estimated_start = next_slot

            self.queue.append((user_id, target, reason, report_type, estimated_start))

            estimated_datetime = datetime.fromtimestamp(estimated_start).strftime("%Y-%m-%d %H:%M:%S UTC")
            await self._safe_send(
                user_id,
                f"{fmt_emoji('hourglass', '⏳')} Your report has been processed.\n"
                f"It will be processed approximately at <b>{estimated_datetime}</b>.\n"
                f"Please come back then to see the result {fmt_emoji('lol', '🥲')}.\n"
                f"<b>Note</b>: Results are more promising when the chances are high."
            )

            if not self.processing:
                asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        self.processing = True
        while True:
            async with self.lock:
                if not self.queue:
                    self.processing = False
                    break
                user_id, target, reason, report_type, estimated_start = self.queue[0]
                now = time.time()
                if self.last_report_time is not None:
                    next_allowed = self.last_report_time + REPORT_COOLDOWN
                    if now < next_allowed:
                        sleep_time = next_allowed - now
                        self.processing = False
                        await asyncio.sleep(sleep_time)
                        async with self.lock:
                            self.processing = True
                        continue

            try:
                await self._execute_report(user_id, target, reason, report_type)
            except Exception as e:
                logger.exception(f"Error processing report for user {user_id}")
                await self._safe_send(
                    user_id,
                    f"{fmt_emoji('cross', '❌')} An error occurred while processing your report. Please try again later."
                )

            async with self.lock:
                self.last_report_time = time.time()
                self.queue.popleft()

    async def _execute_report(self, user_id: int, target: str, reason: str, report_type: str):
        accounts = get_all_accounts()
        if not accounts:
            await self._safe_send(
                user_id,
                f"{fmt_emoji('cross', '❌')} No accounts available. Please add accounts first {fmt_emoji('lol', '')}."
            )
            return

        await self._safe_send(
            user_id,
            f"{fmt_emoji('rocket', '🔄')} Reporting <code>{target}</code> as {report_type} using {len(accounts)} account(s)\n"
            f"This may take a while {fmt_emoji('loading', '🔄')}."
        )

        success_count = 0
        fail_count = 0
        reason_obj = InputReportReasonSpam()  # Using spam reason for all; you could map if needed

        for session_str in accounts:
            client = None
            try:
                client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
                await client.connect()

                if report_type == "message":
                    # Parse message link
                    parts = target.split("/")
                    if "c" in parts:
                        chat_id = int(parts[-2])
                        msg_id = int(parts[-1])
                        entity = await client.get_entity(chat_id)
                    else:
                        chat_username = parts[-2]
                        msg_id = int(parts[-1])
                        entity = await client.get_entity(chat_username)
                    await client(ReportRequest(
                        peer=entity,
                        id=[msg_id],
                        reason=reason_obj,
                        message=reason
                    ))
                else:
                    # For user, channel, group – all use ReportPeerRequest
                    entity = await client.get_entity(target)
                    await client(ReportPeerRequest(
                        peer=entity,
                        reason=reason_obj,
                        message=reason
                    ))
                await client.disconnect()
                success_count += 1
                await asyncio.sleep(2)  # basic rate limit avoidance
            except Exception as e:
                logger.error(f"Failed with account: {e}")
                fail_count += 1
                if client:
                    await client.disconnect()

        await self._safe_send(
            user_id,
            f"{fmt_emoji('check', '✅')} Reporting finished.\n"
            f"Successful reports: {success_count}\n"
            f"Failed: {fail_count}"
        )

    async def _safe_send(self, user_id: int, text: str):
        await MESSAGE_RATE_LIMITER.acquire()
        try:
            await self.app.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.HTML)
        except BadRequest as e:
            logger.error(f"BadRequest sending to {user_id}: {e}. Message: {text}")
            try:
                await self.app.bot.send_message(chat_id=user_id, text=text, parse_mode=None)
            except:
                pass
        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")

report_queue = ReportQueue()

# -------------------- MEMBERSHIP CHECK --------------------
async def is_user_in_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception as e:
            logger.error(f"Error checking membership in {channel}: {e}")
            return False
    return True

def membership_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not await is_user_in_channels(user_id, context):
            keyboard = [
                [InlineKeyboardButton(channel, url=f"https://t.me/{channel.lstrip('@')}")]
                for channel in REQUIRED_CHANNELS
            ]
            keyboard.append([InlineKeyboardButton("✅ I have joined", callback_data="check_join")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = (
                f"{fmt_emoji('warning', '⚠️')} You must join all of the following channels to use this bot:\n\n"
                f"<i>Make sure the bot is also a member of these channels, otherwise I cannot verify.</i>"
            )
            await (update.message or update.callback_query.message).reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await is_user_in_channels(user_id, context):
        await query.edit_message_text(
            f"{fmt_emoji('check', '✅')} Thank you! You now have access. Send /start again.",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text(
            f"{fmt_emoji('cross', '❌')} You haven't joined all channels yet. Please join them and try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(channel, url=f"https://t.me/{channel.lstrip('@')}")]
                for channel in REQUIRED_CHANNELS
            ]),
            parse_mode=ParseMode.HTML
        )

# -------------------- START COMMAND --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_user_activity(user_id)
    if not await is_user_in_channels(user_id, context):
        keyboard = [
            [InlineKeyboardButton(channel, url=f"https://t.me/{channel.lstrip('@')}")]
            for channel in REQUIRED_CHANNELS
        ]
        keyboard.append([InlineKeyboardButton("✅ I have joined", callback_data="check_join")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            f"{fmt_emoji('wave', '👋')} Welcome!\n\n"
            f"To use this bot, you must first join the following channels:\n\n"
            f"<i>Make sure the bot is also a member of these channels, otherwise I cannot verify.</i>"
        )
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return

    text = (
        "<b>Mass Report Bot</b>\n\n"
        "Use the buttons below to manage your accounts and report users.\n\n"
        f"{fmt_emoji('plus', '➕')} <b>Add Account</b> – Pair a new Telegram account.\n"
        f"{fmt_emoji('rocket', '🚨')} <b>Report Entity</b> – Report a user, channel, group, or message using all accounts available."
    )
    keyboard = [
        [InlineKeyboardButton("➕ Add Account", callback_data="add_account")],
        [InlineKeyboardButton("🚨 Report Entity", callback_data="report_user")],
        [InlineKeyboardButton("🔑 Manage Accounts", callback_data="manage_accounts")]

    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        logger.error(f"BadRequest in start: {e}. Falling back to plain text.")
        plain_text = (
            "Mass Report Bot\n\n"
            "Use the buttons below to manage your accounts and report users.\n\n"
            "+ Add Account – Pair a new Telegram account.\n"
            "🚨 Report Entity – Report a user, channel, group, or message using all accounts available"
        )
        await update.message.reply_text(plain_text, reply_markup=reply_markup)

# -------------------- ADD ACCOUNT CONVERSATION --------------------
@membership_required
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"{fmt_emoji('phone', '📞')} Please send your phone number (international format, e.g., +1234567890).\n"
        "You can /cancel at any time.",
        parse_mode=ParseMode.HTML
    )
    return PHONE

async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith('+'):
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Please include the country code with + (e.g., +1234567890)",
            parse_mode=ParseMode.HTML
        )
        return PHONE

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    try:
        await client.send_code_request(str(phone))
    except TypeError as e:
        logger.exception("TypeError sending code")
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Invalid phone number format. Please try again.",
            parse_mode=ParseMode.HTML
        )
        await client.disconnect()
        return PHONE
    except Exception as e:
        logger.exception("Error sending code")
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Failed to send code. Please check the phone number and try again.",
            parse_mode=ParseMode.HTML
        )
        await client.disconnect()
        return PHONE

    context.user_data["temp_client"] = client
    context.user_data["temp_phone"] = str(phone)

    await update.message.reply_text(
        f"{fmt_emoji('inbox', '📲')} A verification code has been sent. Please enter it (you can send it with spaces/dashes).",
        parse_mode=ParseMode.HTML
    )
    return CODE

async def code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    code = raw.replace(" ", "").replace("-", "").replace(".", "")

    client = context.user_data.get("temp_client")
    phone = context.user_data.get("temp_phone")

    if not client or not phone:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Something went wrong. Please start over with /start.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    try:
        await client.sign_in(str(phone), code)
    except errors.SessionPasswordNeededError:
        await update.message.reply_text(
            f"{fmt_emoji('lock', '🔒')} Two-factor authentication is enabled. Please enter your password:",
            parse_mode=ParseMode.HTML
        )
        return PASSWORD
    except errors.PhoneCodeInvalidError:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Invalid code. Please try again (remember: separate digits with spaces), or /cancel.",
            parse_mode=ParseMode.HTML
        )
        return CODE
    except errors.PhoneCodeExpiredError:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Code expired. Please start over with /start.",
            parse_mode=ParseMode.HTML
        )
        await client.disconnect()
        del context.user_data["temp_client"]
        del context.user_data["temp_phone"]
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Error during sign in")
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Login failed. Please try again later.",
            parse_mode=ParseMode.HTML
        )
        await client.disconnect()
        del context.user_data["temp_client"]
        del context.user_data["temp_phone"]
        return ConversationHandler.END
    else:
        return await finalize_login(update, context)

async def password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client = context.user_data.get("temp_client")

    try:
        await client.sign_in(password=password)
    except errors.PasswordHashInvalidError:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Invalid password. Please try again, or /cancel.",
            parse_mode=ParseMode.HTML
        )
        return PASSWORD
    except Exception as e:
        logger.exception("Error during 2FA")
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Login failed. Please start over.",
            parse_mode=ParseMode.HTML
        )
        await client.disconnect()
        del context.user_data["temp_client"]
        del context.user_data["temp_phone"]
        return ConversationHandler.END
    else:
        return await finalize_login(update, context)


async def finalize_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client = context.user_data.get("temp_client")
    if not client:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Something went wrong. Please try again.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    session_str = client.session.save()
    user_id = update.effective_user.id

    # Try to add account (now returns boolean)
    success = add_account(user_id, session_str)

    await client.disconnect()
    del context.user_data["temp_client"]
    del context.user_data["temp_phone"]

    if not success:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} This account session already exists in the pool!\n"
            f"Duplicate sessions are not allowed.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    total = count_all_accounts()
    await update.message.reply_text(
        f"{fmt_emoji('check', '✅')} Account successfully added {fmt_emoji('add', '✅')}!\n"
        f"Now all you need to do is type /start and navigate to 'Report Entity' {fmt_emoji('lol', '🥲')}\n"
        f"Total accounts in pool: {total}",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{fmt_emoji('cross', '❌')} Operation cancelled.",
        parse_mode=ParseMode.HTML
    )
    if "temp_client" in context.user_data:
        await context.user_data["temp_client"].disconnect()
    context.user_data.pop("temp_client", None)
    context.user_data.pop("temp_phone", None)
    return ConversationHandler.END


async def manage_accounts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    accounts = get_user_accounts_with_ids(user_id)
    if not accounts:
        await query.edit_message_text(
            f"{fmt_emoji('cross', '❌')} You have no active accounts.",
            parse_mode=ParseMode.HTML
        )
        return

    text = f"{fmt_emoji('gear', '🔑')} <b>Your Active Accounts</b>\n\n"
    keyboard = []

    for acc_id, session_str, active in accounts:
        # Try to get cached info first (you could add a cache dict to store this)
        display_name = f"Account #{acc_id}"

        # Optional: try to fetch account info with timeout
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await client.connect()
            me = await asyncio.wait_for(client.get_me(), timeout=5.0)

            if me.username:
                display_name = f"@{me.username}"
            elif me.first_name:
                display_name = me.first_name
                if me.last_name:
                    display_name += f" {me.last_name}"
            else:
                display_name = f"ID: {me.id}"

        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting info for account {acc_id}")
            display_name = f"Account #{acc_id} (timeout)"
        except Exception as e:
            logger.error(f"Failed to get account info for ID {acc_id}: {e}")
            display_name = f"Account #{acc_id}"
        finally:
            if client:
                await client.disconnect()

        text += f"• {display_name}\n"
        keyboard.append([InlineKeyboardButton(f"❌ Delete", callback_data=f"delete_{acc_id}")])

    keyboard.append([InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_start")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def delete_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    account_id = int(query.data.split('_')[1])

    if orphan_account(account_id, user_id):
        await query.edit_message_text(
            f"{fmt_emoji('check', '✅')} Account removed from your list.\n"
            f"It will continue to be used in the global reporting pool.",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text(
            f"{fmt_emoji('cross', '❌')} Failed to delete account. It may not exist or belong to you.",
            parse_mode=ParseMode.HTML
        )
    # After a short delay, return to manage accounts view
    await asyncio.sleep(2)
    await manage_accounts_callback(update, context)

# -------------------- REPORT USER --------------------
@membership_required
async def report_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # User must have added at least one account
    if count_user_accounts(user_id) == 0:
        await query.edit_message_text(
            f"{fmt_emoji('cross', '❌')} You need to add at least one account before you can report.\n"
            f"Use the 'Add Account' button first.\nNow please Use /start again {fmt_emoji('hehe', '😑')}",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # Check global pool (should be true if user added one)
    if count_all_accounts() == 0:
        await query.edit_message_text(
            f"{fmt_emoji('cross', '❌')} No accounts available. Please add an account first {fmt_emoji('hehe', '😑')}",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    await query.edit_message_text(
        f"{fmt_emoji('rocket', '👤')} Please send the username (e.g., @username), user ID, or message link of the person/message you want to report.",
        parse_mode=ParseMode.HTML
    )
    return REPORT_TARGET

async def report_target_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    context.user_data["report_target"] = target

    # Auto-detect message link
    if target.startswith("https://t.me/"):
        context.user_data["report_type"] = "message"
        return await ask_reason(update, context)

    # Show type selection
    keyboard = [
        [InlineKeyboardButton(f" User", callback_data="type_user")],
        [InlineKeyboardButton(f" Channel", callback_data="type_channel")],
        [InlineKeyboardButton(f" Group", callback_data="type_group")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "What type of entity are you reporting?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    return REPORT_TYPE

async def type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_type = query.data.replace("type_", "")
    context.user_data["report_type"] = report_type
    await query.edit_message_text(
        f"Selected: {report_type.capitalize()}\nNow choose a reason:",
        parse_mode=ParseMode.HTML
    )
    return await ask_reason(update, context)

async def ask_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(f" Spam & Harassment", callback_data="reason_spam")],
        [InlineKeyboardButton(f" Impersonation/Scams", callback_data="reason_impersonation")],
        [InlineKeyboardButton(f" Illegal Content", callback_data="reason_illegal")],
        [InlineKeyboardButton(f" Botnet/Automated", callback_data="reason_botnet")],
        [InlineKeyboardButton("Custom reason", callback_data="reason_custom")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.message.reply_text(
            "Please select a reason:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "Please select a reason:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    return REPORT_REASON


async def reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("bulk_"):
        # Bulk report reason
        reason_key = data.replace("bulk_", "")
        if reason_key == "reason_custom":
            await query.edit_message_text("Send your custom reason:", parse_mode=ParseMode.HTML)
            return REPORT_REASON
        else:
            reason_map = {
                "reason_spam": STRONG_REASONS["spam"],
                "reason_impersonation": STRONG_REASONS["impersonation"],
                "reason_illegal": STRONG_REASONS["illegal"],
                "reason_botnet": STRONG_REASONS["botnet"],
            }
            reason = reason_map.get(reason_key, STRONG_REASONS["spam"])
            return await queue_bulk_reports(update, context, reason)
    else:
        # Normal report reason (existing code)
        reason_map = {
            "reason_spam": STRONG_REASONS["spam"],
            "reason_impersonation": STRONG_REASONS["impersonation"],
            "reason_illegal": STRONG_REASONS["illegal"],
            "reason_botnet": STRONG_REASONS["botnet"],
        }
        if query.data in reason_map:
            reason = reason_map[query.data]
            return await queue_report(update, context, reason)
        elif query.data == "reason_custom":
            await query.edit_message_text("Send your custom reason:", parse_mode=ParseMode.HTML)
            return REPORT_REASON
        else:
            return ConversationHandler.END

async def report_reason_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    return await queue_report(update, context, reason)

async def queue_report(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str):
    user_id = update.effective_user.id
    target = context.user_data.get("report_target")
    report_type = context.user_data.get("report_type", "user")  # default to user if not set

    await report_queue.add_report(user_id, target, reason, report_type)

    # Clear temporary data
    context.user_data.pop("report_target", None)
    context.user_data.pop("report_type", None)
    return ConversationHandler.END


# -------------------- HELP/TUTORIAL COMMAND --------------------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display comprehensive help menu with interactive buttons."""

    # Create help menu keyboard
    keyboard = [
        [InlineKeyboardButton("📖 Getting Started", callback_data="help_start")],
        [InlineKeyboardButton("➕ Adding Accounts", callback_data="help_add")],
        [InlineKeyboardButton("🚨 How to Report", callback_data="help_report")],
        [InlineKeyboardButton("📊 Report Types", callback_data="help_types")],
        [InlineKeyboardButton("📝 Report Reasons", callback_data="help_reasons")],
        [InlineKeyboardButton("⏱️ Cooldown & Queue", callback_data="help_cooldown")],
        [InlineKeyboardButton("❓ FAQ", callback_data="help_faq")],
        [InlineKeyboardButton("🛠️ Troubleshooting", callback_data="help_trouble")],
        [InlineKeyboardButton("📋 Commands List", callback_data="help_commands")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        f"{fmt_emoji('wave', '👋')} <b>Mass Report Bot - Help Center</b>\n\n"
        f"Welcome to the interactive help system!\n"
        f"Choose a topic below to learn more:"
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle help menu callbacks."""
    query = update.callback_query
    await query.answer()

    help_texts = {
        "help_start": (
            f"{fmt_emoji('rocket', '🚀')} <b>Getting Started</b>\n\n"
            f"1. Use /start to begin\n"
            f"2. Join all required channels\n"
            f"3. Click '✅ I have joined' to verify\n"
            f"4. Add at least ONE account\n"
            f"5. Start reporting!\n\n"
            f"<i>Note: You must add an account to use reporting features</i>"
        ),

        "help_add": (
            f"{fmt_emoji('plus', '➕')} <b>Adding Accounts</b>\n\n"
            f"<b>Steps:</b>\n"
            f"1. Click 'Add Account' button\n"
            f"2. Send phone: +1234567890\n"
            f"3. Enter verification code\n"
            f"4. If 2FA enabled, enter password\n\n"
            f"<b>Important:</b>\n"
            f"• Use international format (+...)\n"
            f"• Codes can have spaces or dashes\n"
            f"• Passwords is NEVER stored\n"
            f"• Sessions stored securely"
        ),

        "help_report": (
            f"{fmt_emoji('rocket', '🚨')} <b>How to Report</b>\n\n"
            f"<b>Process:</b>\n"
            f"1. Click 'Report Entity' button\n"
            f"2. Send target (username/ID/link)\n"
            f"3. Choose entity type (if needed)\n"
            f"4. View chance analysis\n"
            f"5. Select report reason\n"
            f"6. Report sent with ETA\n\n"
            f"<b>Target examples:</b>\n"
            f"• @username (user/channel/group)\n"
            f"• 123456789 (user ID)\n"
            f"• https://t.me/username/123 (message)"
        ),

        "help_types": (
            f"{fmt_emoji('channel', '📢')} <b>Report Types</b>\n\n"
            f"{fmt_emoji('rocket', '👤')} <b>User:</b> Report individual accounts\n"
            f"• Best for: Spam bots, harassers\n\n"
            f"{fmt_emoji('channel', '📢')} <b>Channel:</b> Report public/private channels\n"
            f"• Best for: Scam channels, illegal content\n\n"
            f"{fmt_emoji('group', '👥')} <b>Group:</b> Report groups/supergroups\n"
            f"• Best for: Spam groups, toxic communities\n\n"
            f"{fmt_emoji('inbox', '💬')} <b>Message:</b> Report specific messages\n"
            f"• Auto-detected from message links\n"
            f"• Best for: Individual offensive posts"
        ),

        "help_reasons": (
            f"{fmt_emoji('document', '📄')} <b>Report Reasons</b>\n\n"
            f"<b>Spam & Harassment</b>\n"
            f"• Spam, flooding, harassment\n"
            f"• Hate speech, bullying\n\n"
            f"<b>Impersonation/Scams</b>\n"
            f"• Fake accounts, phishing\n"
            f"• Scams, deception\n\n"
            f"<b>Illegal Content</b>\n"
            f"• Illegal activities\n"
            f"• Prohibited materials\n\n"
            f"<b>Botnet/Automated</b>\n"
            f"• Automated accounts\n"
            f"• Manipulation, botnets\n\n"
            f"<b>Custom:</b> Write your own reason {fmt_emoji('lol', '🥲')}"
        ),

        "help_cooldown": (
            f"{fmt_emoji('hourglass', '⏱️')} <b>Cooldown & Queue</b>\n\n"
            f"• <b>10 hours</b> between reports\n"
            f"• Reports are sent automatically\n"
            f"• You get estimated processing time\n"
            f"• You'll be notified when done\n\n"
            f"<b>Why?</b>\n"
            f"Fair usage for all users\n"
            f"Prevents abuse of reporting system"
        ),

        "help_faq": (
            f"{fmt_emoji('ask', '❓')} <b>Frequently Asked Questions</b>\n\n"
            f"<b>Q: Do I need to add an account?</b>\n"
            f"A: YES! You must add at least one account to report.\n\n"
            f"<b>Q: Are my accounts shared?</b>\n"
            f"A: Only sessions stored. No one can access your account.\n\n"
            f"<b>Q: Is my password stored?</b>\n"
            f"A: NEVER! Passwords used only during login.\n\n"
            f"<b>Q: How is chance calculated?</b>\n"
            f"A: 2% per account (max 80%)\n\n"
            f"<b>Q: What if bot restarts?</b>\n"
            f"A: All data saved in database - continues normally"
        ),

        "help_trouble": (
            f"{fmt_emoji('gear', '🛠️')} <b>Troubleshooting</b>\n\n"
            f"<b>Can't join channels?</b>\n"
            f"• Bot must be in channels too\n"
            f"• Contact @daxbots for help\n\n"
            f"<b>Login fails?</b>\n"
            f"• Use format: +1234567890\n"
            f"• Check verification code\n"
            f"• Verify 2FA password\n\n"
            f"<b>Bot not responding?</b>\n"
            f"• Check internet connection\n"
        ),

        "help_commands": (
            f"{fmt_emoji('gear', '📋')} <b>Commands List</b>\n\n"
            f"<b>Main Commands:</b>\n"
            f"<b>/start</b> - Main menu\n"
            f"<b>/help</b> - This help system\n"
            f"<b>/cancel</b> - Cancel current operation\n"
            f"<b>/stats</b> - View bot statistics\n"
            f"<b>/bot_status</b> - Detailed bot status (uptime, memory, queue)\n\n"

            f"<b>Account Management:</b>\n"
            f"<b>/addsessions</b> - Add session manually (admin only)\n"
            f"<b>/check_account</b> - Check if account is deleted/frozen\n"
            f"<b>/account_info</b> - Get detailed account info\n\n"

            f"<b>Reporting:</b>\n"
            f"<b>/bulk_report</b> - Report multiple targets at once\n\n"

            f"<b>Group Moderation:</b>\n"
            f"<b>/ban</b> - Ban user (group admins only)\n"
            f"<b>/unban</b> - Unban user (group admins only)\n\n"

            f"<b>Admin Only:</b>\n"
            f"<b>/broadcast</b> - Broadcast message to all users\n"
            f"<b>/addsessions</b> - Add session strings manually\n\n"

            f"<b>Buttons:</b>\n"
            f"• Add Account ➕\n"
            f"• Report Entity 🚨\n"
            f"• Manage Accounts 🔑\n"
            f"• Help topics in this menu"
        ),

        "help_stats": (
            f"{fmt_emoji('chart', '📊')} <b>Bot Statistics</b>\n\n"
            f"Total accounts available: {count_all_accounts()}\n"
            f"Reports in queue: {len(report_queue.queue)}\n"
            f"Next report in: {get_next_report_time()}\n"
            f"Ban chance: {calculate_chance(count_all_accounts())}%\n\n"
            f"<i>Note: Statistics are updated in real-time</i>"
        )
    }

    # Get the help topic from callback data
    topic = query.data

    if topic in help_texts:
        # Create back button
        keyboard = [[InlineKeyboardButton("◀️ Back to Help Menu", callback_data="help_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            help_texts[topic],
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )

    elif topic == "help_back":
        # Return to main help menu
        keyboard = [
            [InlineKeyboardButton("📖 Getting Started", callback_data="help_start")],
            [InlineKeyboardButton("➕ Adding Accounts", callback_data="help_add")],
            [InlineKeyboardButton("🚨 How to Report", callback_data="help_report")],
            [InlineKeyboardButton("📊 Report Types", callback_data="help_types")],
            [InlineKeyboardButton("📝 Report Reasons", callback_data="help_reasons")],
            [InlineKeyboardButton("⏱️ Cooldown & Queue", callback_data="help_cooldown")],
            [InlineKeyboardButton("❓ FAQ", callback_data="help_faq")],
            [InlineKeyboardButton("🛠️ Troubleshooting", callback_data="help_trouble")],
            [InlineKeyboardButton("📋 Commands List", callback_data="help_commands")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_text = (
            f"{fmt_emoji('wave', '👋')} <b>Mass Report Bot - Help Center</b>\n\n"
            f"Choose a topic below to learn more {fmt_emoji('smile', '🙂‍↔️')}:"
        )

        await query.edit_message_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )


# Helper function for stats
# Helper function for stats
def get_next_report_time():
    """Calculate time until next report can be processed."""
    if report_queue.last_report_time is None:
        return "Now (no reports in queue)"

    next_time = report_queue.last_report_time + REPORT_COOLDOWN
    now = time.time()

    if now >= next_time:
        return "Now (ready)"

    seconds_left = int(next_time - now)
    hours = seconds_left // 3600
    minutes = (seconds_left % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


# -------------------- STATS COMMAND --------------------
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics."""
    total_accounts = count_all_accounts()
    queue_length = len(report_queue.queue)
    chance = calculate_chance(total_accounts)
    next_report = get_next_report_time()

    stats_text = (
        f"{fmt_emoji('chart', '📊')} <b>Bot Statistics</b>\n\n"
        f"Total accounts available: <b>{total_accounts}</b>\n"
        f"Reports in queue: <b>{queue_length}</b>\n"
        f"Next report available: <b>{next_report}</b>\n"
        f"Ban chance: <b>{chance}%</b>\n"
        f"Cooldown period: <b>10 hours</b>\n\n"
        f"Add more accounts to increase chance!"
    )

    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="stats_refresh")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        stats_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


async def stats_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh stats display."""
    query = update.callback_query
    await query.answer()

    total_accounts = count_all_accounts()
    queue_length = len(report_queue.queue)
    chance = calculate_chance(total_accounts)
    next_report = get_next_report_time()

    stats_text = (
        f"{fmt_emoji('chart', '📊')} <b>Bot Statistics</b>\n\n"
        f"Total accounts available: <b>{total_accounts}</b>\n"
        f"Reports in queue: <b>{queue_length}</b>\n"
        f"Next report available: <b>{next_report}</b>\n"
        f"Ban chance: <b>{chance}%</b>\n"
        f"Cooldown period: <b>10 hours</b>\n\n"
        f"Add more accounts to increase chance!"
    )

    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="stats_refresh")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        stats_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


async def add_sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to manually add a unique session string to the database."""
    user_id = update.effective_user.id

    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} You are not authorized to use this command.",
            parse_mode=ParseMode.HTML
        )
        return

    # Get the session string from the command arguments
    args = context.args
    if not args:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Usage: /addsessions <session_string>\n\n"
            f"Example: /addsessions 1aa2bb3cc4dd5ee..."
            # No parse_mode = plain text
        )
        return

    session_string = ' '.join(args).strip()
    if not session_string:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Session string cannot be empty.",
            parse_mode=ParseMode.HTML
        )
        return

    # Validate session string format
    try:
        # Test if it's a valid session string
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        # No connection needed, just checking format
    except Exception as e:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Invalid session string format: {str(e)}",
            parse_mode=ParseMode.HTML
        )
        return

    # Check if session already exists in database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('SELECT COUNT(*) FROM accounts WHERE session_string = ?', (session_string,))
        count = c.fetchone()[0]

        if count > 0:
            conn.close()
            await update.message.reply_text(
                f"{fmt_emoji('cross', '❌')} This session string already exists in the database!\n"
                f"Duplicate sessions are not allowed.",
                parse_mode=ParseMode.HTML
            )
            return

        # Add the new session
        c.execute('INSERT INTO accounts (user_id, session_string) VALUES (?, ?)',
                  (0, session_string))  # user_id = 0 for manually added
        conn.commit()

    except sqlite3.IntegrityError:
        # This should not happen due to our check, but just in case
        conn.rollback()
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} This session string already exists in the database!",
            parse_mode=ParseMode.HTML
        )
        return
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error in add_sessions: {e}")
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Database error: {str(e)}",
            parse_mode=ParseMode.HTML
        )
        return
    finally:
        conn.close()

    total = count_all_accounts()
    await update.message.reply_text(
        f"{fmt_emoji('check', '✅')} Session added successfully!\n"
        f"This session is now unique in the database.\n"
        f"Total accounts in pool: {total}",
        parse_mode=ParseMode.HTML
    )


from html import escape  # Add this at the top of your file
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban a user from the group (admin/owner only)."""

    # 1. Basic Group & Permission Checks
    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text(f"{fmt_emoji('cross', '❌')} This command can only be used in groups.", parse_mode=ParseMode.HTML)
        return

    try:
        bot_member = await context.bot.get_chat_member(update.effective_chat.id, context.bot.id)
        if bot_member.status not in ["administrator", "creator"]:
            await update.message.reply_text(f"{fmt_emoji('cross', '❌')} I need to be an admin to ban users.", parse_mode=ParseMode.HTML)
            return

        user_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if user_member.status not in ["administrator", "creator"]:
            await update.message.reply_text(f"{fmt_emoji('cross', '❌')} Only group admins can use this command.", parse_mode=ParseMode.HTML)
            return
    except Exception as e:
        logging.error(f"Permission check error: {e}")
        return

    # 2. Parse Target User and Reason
    target_user_id = None
    reason = "No reason provided"
    args = context.args

    # Scenario A: Reply to a message
    if update.message.reply_to_message:
        target_user_id = update.message.reply_to_message.from_user.id
        if args:
            reason = " ".join(args)

    # Scenario B: Mention or ID in the command
    elif args:
        # Check if the first argument is a mention (Telegram provides entities for this)
        mention_entity = next((e for e in update.message.entities if e.type in ["mention", "text_mention"]), None)

        if mention_entity:
            if mention_entity.type == "mention":
                # Handle @username (Bot must have 'seen' them to resolve via get_chat)
                username = update.message.text[mention_entity.offset:mention_entity.offset + mention_entity.length]
                try:
                    chat = await context.bot.get_chat(username)
                    target_user_id = chat.id
                except Exception:
                    await update.message.reply_text(
                        f"{fmt_emoji('cross')} Could not find user {escape(username)}. They must interact with me first{fmt_emoji('lol')}.", parse_mode=ParseMode.HTML)
                    return
            elif mention_entity.type == "text_mention":
                # Handle users without usernames (clickable names)
                target_user_id = mention_entity.user.id

            reason = " ".join(args[1:]) if len(args) > 1 else reason
        else:
            # Try parsing as a numeric ID
            try:
                target_user_id = int(args[0])
                reason = " ".join(args[1:]) if len(args) > 1 else reason
            except ValueError:
                await update.message.reply_text(f"{fmt_emoji('cross', '❌')} Invalid input. Use @username, a numeric ID, or reply to a message.")
                return

    if not target_user_id:
        await update.message.reply_text(f"{fmt_emoji('cross', '❌')} Usage: /ban <@user/ID> [reason] or reply to a message.")
        return

    # 3. Safety Checks
    if target_user_id == context.bot.id:
        await update.message.reply_text(f"{fmt_emoji('cross', '❌')} Bro, I cannot ban myself!", parse_mode=ParseMode.HTML)
        return

    try:
        target_member = await context.bot.get_chat_member(update.effective_chat.id, target_user_id)
        if target_member.status in ["administrator", "creator"]:
            await update.message.reply_text("❌ Cannot ban another admin.")
            return
    except Exception:
        pass  # User might not be in the group currently; proceed with ban anyway

    # 4. Execution
    try:
        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user_id,
            revoke_messages=True
        )

        # Resolve display name for the success message
        try:
            target_chat = await context.bot.get_chat(target_user_id)
            user_display = f"@{target_chat.username}" if target_chat.username else target_chat.first_name
        except:
            user_display = f"User {target_user_id}"

        await update.message.reply_text(
            f"✅ <b>User Banned Successfully</b>\n\n"
            f"<b>User:</b> {escape(user_display)}\n"
            f"<b>Reason:</b> {escape(reason)}\n"
            f"<b>Admin:</b> {escape(update.effective_user.first_name)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to ban: {escape(str(e))}")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban a user from the group (admin/owner only)."""

    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} This command can only be used in groups.",
            parse_mode=ParseMode.HTML
        )
        return

    bot_member = await context.bot.get_chat_member(update.effective_chat.id, context.bot.id)
    if bot_member.status not in ["administrator", "creator"]:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} I need to be an admin to unban users.",
            parse_mode=ParseMode.HTML
        )
        return

    user_id = update.effective_user.id
    try:
        user_member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        if user_member.status not in ["administrator", "creator"]:
            await update.message.reply_text(
                f"{fmt_emoji('cross', '❌')} Only group admins can use this command.",
                parse_mode=ParseMode.HTML
            )
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Error verifying your permissions.",
            parse_mode=ParseMode.HTML
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Usage: /unban <username or user_id>\n"
            f"Examples: /unban @user or /unban 123456789",
            parse_mode=ParseMode.HTML
        )
        return

    target_identifier = args[0].strip()
    target_user_id = None

    # Check if it's a username
    if target_identifier.startswith('@'):
        username = target_identifier[1:]
        try:
            chat = await context.bot.get_chat(f"@{username}")
            target_user_id = chat.id
        except Exception as e:
            await update.message.reply_text(
                f"{fmt_emoji('cross', '❌')} Could not find user: {target_identifier}",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        try:
            target_user_id = int(target_identifier)
        except ValueError:
            await update.message.reply_text(
                f"{fmt_emoji('cross', '❌')} Invalid input. Use @username or numeric ID.",
                parse_mode=ParseMode.HTML
            )
            return

    try:
        await context.bot.unban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user_id
        )

        safe_identifier = escape(target_identifier)
        await update.message.reply_text(
            f"{fmt_emoji('check', '✅')} User {target_identifier} unbanned successfully.",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logger.error(f"Unban error: {e}")
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Failed to unban user: {str(e)}",
            parse_mode=ParseMode.HTML
        )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to all users (admin only). Preserves all formatting and inline buttons."""
    user_id = update.effective_user.id

    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} You are not authorized to use this command.",
            parse_mode=ParseMode.HTML
        )
        return

    # Check if replying to a message
    if not update.message.reply_to_message:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Please reply to the message you want to broadcast.\n"
            f"Usage: Reply to any message with /broadcast",
            parse_mode=ParseMode.HTML
        )
        return

    # Get the message to broadcast
    broadcast_msg = update.message.reply_to_message
    from_chat_id = update.effective_chat.id

    # Get all users
    users = get_all_user_ids()
    if not users:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} No users found in database.",
            parse_mode=ParseMode.HTML
        )
        return

    total = len(users)
    await update.message.reply_text(
        f"{fmt_emoji('rocket', '📢')} Broadcasting to {total} users...\n"
        f"This may take a while.",
        parse_mode=ParseMode.HTML
    )

    success = 0
    failed = 0

    for idx, target_user_id in enumerate(users, 1):
        try:
            # Forward the message exactly as is (preserves buttons, formatting, media)
            await context.bot.forward_message(
                chat_id=target_user_id,
                from_chat_id=from_chat_id,
                message_id=broadcast_msg.message_id
            )
            success += 1

            # Sleep every 20 messages to avoid flood limits
            if idx % 20 == 0:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Failed to broadcast to {target_user_id}: {e}")
            failed += 1

    await update.message.reply_text(
        f"{fmt_emoji('check', '✅')} Broadcast completed!\n"
        f"Successful: {success}\n"
        f"Failed: {failed}",
        parse_mode=ParseMode.HTML
    )


async def back_to_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main start menu."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Regenerate start menu
    text = (
        "<b>Mass Report Bot</b>\n\n"
        "Use the buttons below to manage your accounts and report users.\n\n"
        f"{fmt_emoji('plus', '➕')} <b>Add Account</b> – Pair a new Telegram account.\n"
        f"{fmt_emoji('rocket', '🚨')} <b>Report Entity</b> – Report a user, channel, group, or message using all accounts available."
    )
    keyboard = [
        [InlineKeyboardButton("➕ Add Account", callback_data="add_account")],
        [InlineKeyboardButton("🚨 Report Entity", callback_data="report_user")],
        [InlineKeyboardButton("🔑 Manage Accounts", callback_data="manage_accounts")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def check_account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if count_user_accounts(user_id) == 0:
        await update.message.reply_text("❌ You need at least one added account.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /check_account <username or id>")
        return

    target = args[0].strip()
    await update.message.reply_text(f"{fmt_emoji("magnifier", "🔍")}Checking <i>{escape(target)}</i> {fmt_emoji("loading", "🔄")}", parse_mode=ParseMode.HTML)

    sessions = get_all_accounts()
    if not sessions:
        return

    client = None
    try:
        client = TelegramClient(StringSession(sessions[0]), API_ID, API_HASH)
        await client.connect()

        try:
            entity = await client.get_entity(target)
            # If we get here, the account exists
            if hasattr(entity, 'deleted') and entity.deleted:
                status = "❌ Deleted / Frozen account"
            elif not entity.first_name and not entity.username:
                status = "⚠️ Possibly deleted (no name/username)"
            else:
                status = "✅ Active account"
                if entity.username:
                    status += f"\nUsername: @{entity.username}"
                if entity.first_name:
                    status += f"\nName: {entity.first_name}"
        except errors.UsernameNotOccupiedError:
            status = "❌ Username does not exist – account deleted or never existed."
        except ValueError as e:
            if "No user has" in str(e):
                status = "❌ User not found – likely deleted."
            else:
                status = f"⚠️ Error: {e}"
        except Exception as e:
            status = f"⚠️ Unexpected error: {e}"

        await update.message.reply_text(status, parse_mode=ParseMode.HTML)
    finally:
        if client:
            await client.disconnect()


async def account_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get detailed info about a Telegram account."""
    user_id = update.effective_user.id

    # User must have at least one account
    if count_user_accounts(user_id) == 0:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} You need at least one added account to use this.",
            parse_mode=ParseMode.HTML
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} Usage: /account_info &lt;username or user_id&gt;",
            parse_mode=ParseMode.HTML
        )
        return

    target = args[0].strip()

    # Get a session from user's accounts
    sessions = get_user_accounts(user_id)
    if not sessions:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} No active session found.",
            parse_mode=ParseMode.HTML
        )
        return

    await update.message.reply_text(
        f"{fmt_emoji('loading', '🔍')} Fetching info for {escape(target)}...",
        parse_mode=ParseMode.HTML
    )

    client = None
    try:
        client = TelegramClient(StringSession(sessions[0]), API_ID, API_HASH)
        await client.connect()

        try:
            entity = await client.get_entity(target)

            # Build info message
            info = f"{fmt_emoji('star', '📌')} <b>Account Info</b>\n\n"

            # Basic identifiers
            info += f"<b>ID:</b> <code>{entity.id}</code>\n"
            if hasattr(entity, 'username') and entity.username:
                info += f"<b>Username:</b> @{entity.username}\n"
            if hasattr(entity, 'first_name') and entity.first_name:
                info += f"<b>First name:</b> {escape(entity.first_name)}\n"
            if hasattr(entity, 'last_name') and entity.last_name:
                info += f"<b>Last name:</b> {escape(entity.last_name)}\n"
            if hasattr(entity, 'phone') and entity.phone:
                info += f"<b>Phone:</b> <code>{entity.phone}</code>\n"

            # Account age (if we can get creation date)
            # Telegram doesn't expose creation date directly, but we can estimate from ID?
            # Not reliable, so skip.

            # Last seen / status
            if hasattr(entity, 'status'):
                status = entity.status
                if hasattr(status, 'was_online'):
                    # User was online at specific time
                    last_seen = datetime.fromtimestamp(status.was_online).strftime("%Y-%m-%d %H:%M:%S")
                    info += f"<b>Last seen:</b> {last_seen}\n"
                elif hasattr(status, 'expires'):
                    info += f"<b>Status:</b> Online\n"
                else:
                    info += f"<b>Status:</b> Recently\n"
            else:
                info += f"<b>Status:</b> Unknown\n"

            # Bot?
            if hasattr(entity, 'bot') and entity.bot:
                info += f"<b>Type:</b> 🤖 Bot\n"
            else:
                info += f"<b>Type:</b> 👤 User\n"

            # Profile photo? We can get the photo file, but we can't send it directly here.
            if hasattr(entity, 'photo'):
                info += f"<b>Profile photo:</b> Yes\n"
            else:
                info += f"<b>Profile photo:</b> No\n"

            # Bio – not directly from entity, need to call GetFullUserRequest
            try:
                from telethon.tl.functions.users import GetFullUserRequest
                full = await client(GetFullUserRequest(entity))
                if full.full_user.about:
                    info += f"<b>Bio:</b> {escape(full.full_user.about)}\n"
            except:
                pass

            # Common groups – requires the user to be in a group with us, not easily enumerable.
            # We could try to get mutual groups if the target is also a user, but that's heavy.
            # Skip for now.

            await update.message.reply_text(info, parse_mode=ParseMode.HTML)

        except errors.UsernameNotOccupiedError:
            await update.message.reply_text(
                f"{fmt_emoji('cross', '❌')} Username does not exist.",
                parse_mode=ParseMode.HTML
            )
        except ValueError as e:
            if "No user has" in str(e):
                await update.message.reply_text(
                    f"{fmt_emoji('cross', '❌')} User not found.",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"{fmt_emoji('cross', '❌')} Error: {escape(str(e))}",
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            await update.message.reply_text(
                f"{fmt_emoji('cross', '❌')} Unexpected error: {escape(str(e))}",
                parse_mode=ParseMode.HTML
            )
    finally:
        if client:
            await client.disconnect()

BULK_TARGETS = range(20, 21)  # New state


async def bulk_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start bulk report process."""
    user_id = update.effective_user.id

    if count_user_accounts(user_id) == 0:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} You need at least one added account.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"{fmt_emoji('document', '📄')} Send a list of targets (usernames, IDs, or message links), one per line.\n"
        f"Example:\n"
        f"@spam1\n"
        f"123456789\n"
        f"https://t.me/...\n\n"
        f"Send /cancel to abort.",
        parse_mode=ParseMode.HTML
    )
    return BULK_TARGETS


async def bulk_targets_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the list of targets."""
    text = update.message.text.strip()
    lines = [line.strip() for line in text.split('\n') if line.strip()]

    if not lines:
        await update.message.reply_text(
            f"{fmt_emoji('cross', '❌')} No valid targets found.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # Store the list
    context.user_data["bulk_targets"] = lines

    # Ask for reason (could use the same reason selection flow)
    keyboard = [
        [InlineKeyboardButton(f" Spam & Harassment", callback_data="bulk_reason_spam")],
        [InlineKeyboardButton(f" Impersonation/Scams", callback_data="bulk_reason_impersonation")],
        [InlineKeyboardButton(f" Illegal Content", callback_data="bulk_reason_illegal")],
        [InlineKeyboardButton(f" Botnet/Automated", callback_data="bulk_reason_botnet")],
        [InlineKeyboardButton("Custom reason", callback_data="bulk_reason_custom")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Received {len(lines)} target(s). Now choose a reason (applies to all):",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    return REPORT_REASON  # Reuse the same reason state


async def queue_bulk_reports(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str):
    """Queue multiple reports from bulk list."""
    user_id = update.effective_user.id
    targets = context.user_data.get("bulk_targets", [])

    if not targets:
        await update.callback_query.edit_message_text(
            f"{fmt_emoji('cross', '❌')} No targets found.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    await update.callback_query.edit_message_text(
        f"{fmt_emoji('loading', '📤')} Queuing {len(targets)} reports...",
        parse_mode=ParseMode.HTML
    )

    queued = 0
    for target in targets:
        # Determine report type (simple: if link, message; else user)
        report_type = "message" if target.startswith("https://t.me/") else "user"
        await report_queue.add_report(user_id, target, reason, report_type)
        queued += 1
        # Small delay to avoid overwhelming the queue
        await asyncio.sleep(0.5)

    await context.bot.send_message(
        chat_id=user_id,
        text=f"{fmt_emoji('check', '✅')} Successfully queued {queued} reports.\n"
             f"Each will be processed with 10-hour cooldown between them.",
        parse_mode=ParseMode.HTML
    )

    # Clear bulk data
    context.user_data.pop("bulk_targets", None)
    return ConversationHandler.END


async def bot_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot status and statistics."""
    user_id = update.effective_user.id

    # Optional: restrict to admins? We'll allow all users.

    # Uptime
    uptime_seconds = int(time.time() - BOT_START_TIME)
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

    # Memory usage
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    mem_mb = mem_info.rss / 1024 / 1024
    cpu_percent = process.cpu_percent(interval=0.1)

    # Database stats
    total_accounts = count_all_accounts()
    total_users = len(get_all_user_ids())  # we need a function count_all_users
    queue_size = len(report_queue.queue)
    next_report = get_next_report_time()

    # System info
    system_mem = psutil.virtual_memory()
    system_mem_used = system_mem.used / 1024 / 1024 / 1024
    system_mem_total = system_mem.total / 1024 / 1024 / 1024

    # Build fancy UI with progress bars
    mem_bar = "█" * int(mem_mb / 10) + "░" * (10 - int(mem_mb / 10))
    queue_bar = "█" * min(queue_size, 10) + "░" * (10 - min(queue_size, 10))

    text = (
        f"{fmt_emoji('chart', '📊')} <b>Bot Status</b>\n\n"
        f"<b>Uptime:</b> {uptime_str}\n"
        f"<b>Process Memory:</b> {mem_mb:.1f} MB [{mem_bar}]\n"
        f"<b>CPU Usage:</b> {cpu_percent:.1f}%\n"
        f"<b>System RAM:</b> {system_mem_used:.1f}GB / {system_mem_total:.1f}GB\n\n"

        f"<b>📦 Database</b>\n"
        f"Total accounts: <b>{total_accounts}</b>\n"
        f"Total users: <b>{total_users}</b>\n\n"

        f"<b>⏳ Report Queue</b>\n"
        f"Queue size: <b>{queue_size}</b> [{queue_bar}]\n"
        f"Next report: <b>{next_report}</b>\n"
        f"Cooldown: <b>10 hours</b>\n\n"

        f"{fmt_emoji('loading', '🔄')} <i>Last updated: {datetime.now().strftime('%H:%M:%S')}</i>"
    )

    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def status_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh status display."""
    query = update.callback_query
    await query.answer()

    # Recompute everything
    uptime_seconds = int(time.time() - BOT_START_TIME)
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    mem_mb = mem_info.rss / 1024 / 1024
    cpu_percent = process.cpu_percent(interval=0.1)

    total_accounts = count_all_accounts()
    total_users = len(get_all_user_ids())
    queue_size = len(report_queue.queue)
    next_report = get_next_report_time()

    system_mem = psutil.virtual_memory()
    system_mem_used = system_mem.used / 1024 / 1024 / 1024
    system_mem_total = system_mem.total / 1024 / 1024 / 1024

    mem_bar = "█" * int(mem_mb / 10) + "░" * (10 - int(mem_mb / 10))
    queue_bar = "█" * min(queue_size, 10) + "░" * (10 - min(queue_size, 10))

    text = (
        f"{fmt_emoji('chart', '📊')} <b>Bot Status</b>\n\n"
        f"<b>Uptime:</b> {uptime_str}\n"
        f"<b>Process Memory:</b> {mem_mb:.1f} MB [{mem_bar}]\n"
        f"<b>CPU Usage:</b> {cpu_percent:.1f}%\n"
        f"<b>System RAM:</p> {system_mem_used:.1f}GB / {system_mem_total:.1f}GB\n\n"

        f"<b>📦 Database</b>\n"
        f"Total accounts: <b>{total_accounts}</b>\n"
        f"Total users: <b>{total_users}</b>\n\n"

        f"<b>⏳ Report Queue</b>\n"
        f"Queue size: <b>{queue_size}</b> [{queue_bar}]\n"
        f"Next report: <b>{next_report}</b>\n"
        f"Cooldown: <b>10 hours</b>\n\n"

        f"{fmt_emoji('loading', '🔄')} <i>Last updated: {datetime.now().strftime('%H:%M:%S')}</i>"
    )

    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


# -------------------- ADD TO MAIN FUNCTION --------------------
# -------------------- MAIN --------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    report_queue.set_application(application)

    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^help_"))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("check_account", check_account_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CallbackQueryHandler(stats_refresh_callback, pattern="^stats_refresh$"))
    application.add_handler(CallbackQueryHandler(manage_accounts_callback, pattern="^manage_accounts$"))
    application.add_handler(CallbackQueryHandler(delete_account_callback, pattern="^delete_"))
    application.add_handler(CallbackQueryHandler(back_to_start_callback, pattern="^back_to_start$"))
    application.add_handler(CommandHandler("addsessions", add_sessions_command))
    application.add_handler(CommandHandler("bot_status", bot_status_command))
    application.add_handler(CallbackQueryHandler(status_refresh_callback, pattern="^status_refresh$"))

    # Add account conversation
    add_account_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_account_start, pattern="^add_account$")],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, code_handler)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(add_account_conv)

    # Report conversation
    report_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(report_user_start, pattern="^report_user$")],
        states={
            REPORT_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_target_handler)],
            REPORT_TYPE: [CallbackQueryHandler(type_callback, pattern="^type_")],
            REPORT_REASON: [
                CallbackQueryHandler(reason_callback, pattern="^reason_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, report_reason_text_handler),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(report_conv)

    bulk_conv = ConversationHandler(
        entry_points=[CommandHandler("bulk_report", bulk_report_start)],
        states={
            BULK_TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_targets_handler)],
            REPORT_REASON: [
                CallbackQueryHandler(reason_callback, pattern="^(bulk_)?reason_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, report_reason_text_handler),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(bulk_conv)

    print("Bot started. Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == "__main__":
    main()
