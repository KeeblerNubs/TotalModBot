# moderator_bot.py
import json
import logging
import os
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "<PUT_YOUR_TOKEN_HERE>")
DEFAULT_ADMINS = {123456789}  # set of ints
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))
DATA_FILE = os.environ.get("DATA_FILE", os.path.join(DATA_DIR, "mod_data.json"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# ----------------------------

os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)


def _parse_admins(value: str | None):
    if not value:
        return DEFAULT_ADMINS
    admins = set()
    invalid_values = []
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            admins.add(int(part))
        except ValueError:
            invalid_values.append(part)
    if invalid_values:
        logger.warning("Ignoring invalid ADMINS entries: %s", ", ".join(invalid_values))
    return admins or DEFAULT_ADMINS


ADMINS = _parse_admins(os.environ.get("ADMINS"))
logger.info("Admin IDs configured: %s", sorted(ADMINS))
logger.info("Persisting moderation data at %s", DATA_FILE)

# persistent storage helpers
def _ensure_data_schema(loaded: dict | None) -> dict:
    base = loaded or {}
    base.setdefault("managed_chats", [])
    base.setdefault("global_bans", [])
    base.setdefault("protected_users", [])
    base.setdefault("ban_logs", [])
    return base


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return _ensure_data_schema(json.load(f))
    return _ensure_data_schema(None)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

data = load_data()


def _normalize_key(value: str | int) -> str:
    if isinstance(value, str):
        return value.lower()
    return str(value)


def _get_global_ban_entry(target_arg: str):
    for entry in data["global_bans"]:
        if isinstance(entry, dict) and entry.get("target") == target_arg:
            return entry
        if isinstance(entry, str) and entry == target_arg:
            return entry
    return None


def _remove_global_ban_entry(target_arg: str):
    entry = _get_global_ban_entry(target_arg)
    if entry is not None:
        data["global_bans"].remove(entry)
    return entry


def _is_protected(target_arg: str | int) -> bool:
    normalized = _normalize_key(target_arg)
    return any(_normalize_key(item) == normalized for item in data["protected_users"])


def _add_protected(target_arg: str | int):
    normalized = _normalize_key(target_arg)
    if not any(_normalize_key(item) == normalized for item in data["protected_users"]):
        data["protected_users"].append(target_arg)


def _remove_protected(target_arg: str | int) -> bool:
    normalized = _normalize_key(target_arg)
    for item in list(data["protected_users"]):
        if _normalize_key(item) == normalized:
            data["protected_users"].remove(item)
            return True
    return False


def _record_ban_log(target_arg: str, issuer_id: int, reason: str, permanent: bool):
    data["ban_logs"].append(
        {
            "target": target_arg,
            "issuer": issuer_id,
            "reason": reason,
            "permanent": permanent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


def _date_from_timestamp(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.date()

async def is_admin_user(user_id: int) -> bool:
    return user_id in ADMINS

# register current chat as managed (only works in groups/channels)
async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not await is_admin_user(user.id):
        await ctx.bot.send_message(chat.id, "Only configured bot admins can register chats.")
        return

    cid = chat.id
    if cid in data["managed_chats"]:
        await ctx.bot.send_message(chat.id, "This chat is already managed.")
        return

    data["managed_chats"].append(cid)
    save_data(data)
    await ctx.bot.send_message(chat.id, f"Registered this chat (id={cid}) as managed.")

# unregister current chat
async def unregister(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not await is_admin_user(user.id):
        await ctx.bot.send_message(chat.id, "Only configured bot admins can unregister chats.")
        return

    cid = chat.id
    if cid not in data["managed_chats"]:
        await ctx.bot.send_message(chat.id, "This chat is not managed.")
        return

    data["managed_chats"].remove(cid)
    save_data(data)
    await ctx.bot.send_message(chat.id, f"Unregistered this chat (id={cid}).")

# list managed chats
async def list_managed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin_user(user.id):
        await ctx.bot.send_message(update.effective_chat.id, "Only configured bot admins can view this list.")
        return
    if not data["managed_chats"]:
        await ctx.bot.send_message(update.effective_chat.id, "No managed chats.")
        return
    lines = [f"{i+1}. id={cid}" for i, cid in enumerate(data["managed_chats"])]
    await ctx.bot.send_message(update.effective_chat.id, "Managed chats:\n" + "\n".join(lines))

# helper to resolve a username or user_id from args
def parse_target_arg(arg: str):
    # accept @username or numeric id
    if arg.startswith("@"):
        return arg  # username string
    try:
        return int(arg)
    except ValueError:
        return None

async def _resolve_target_id(ctx: ContextTypes.DEFAULT_TYPE, target):
    if isinstance(target, str) and target.startswith("@"):
        try:
            chat_obj = await ctx.bot.get_chat(target)
            return chat_obj.id
        except Exception:
            return None
    return target


async def _ensure_bot_can_ban(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    try:
        bot_member = await ctx.bot.get_chat_member(chat_id, (await ctx.bot.get_me()).id)
    except Exception:
        return False
    return bot_member.status in ("administrator", "creator") and getattr(bot_member, "can_restrict_members", False)


async def _apply_ban_to_chat(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, target_arg: str):
    try:
        if not await _ensure_bot_can_ban(ctx, chat_id):
            return f"Chat {chat_id}: bot lacks ban permission — skipped."
        await ctx.bot.ban_chat_member(chat_id, user_id)
        return f"Chat {chat_id}: banned user {target_arg}."
    except Exception as exc:
        logger.exception("Ban failed for chat %s", chat_id)
        return f"Chat {chat_id}: failed to ban ({exc})."


async def _ban_targets(update: Update, ctx: ContextTypes.DEFAULT_TYPE, target_args: list[str], reason: str,
                       permanent: bool):
    issuer = update.effective_user
    overall_results = []
    for target_arg in target_args:
        if _is_protected(target_arg):
            overall_results.append(f"{target_arg}: is protected — skipped.")
            continue

        parsed = parse_target_arg(target_arg)
        if parsed is None:
            overall_results.append(f"{target_arg}: could not parse user id.")
            continue

        if _get_global_ban_entry(target_arg) is not None:
            overall_results.append(f"{target_arg}: already globally banned.")
            continue

        resolved_id = await _resolve_target_id(ctx, parsed)
        if resolved_id is None:
            overall_results.append(f"{target_arg}: could not resolve user id.")
            continue

        entry = {
            "target": target_arg,
            "issuer": issuer.id,
            "reason": reason,
            "permanent": permanent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        data["global_bans"].append(entry)

        chat_results = []
        for cid in list(data["managed_chats"]):
            chat_result = await _apply_ban_to_chat(ctx, cid, resolved_id, target_arg)
            chat_results.append(chat_result)

        _record_ban_log(target_arg, issuer.id, reason, permanent)
        overall_results.append(f"Global ban applied for {target_arg}.\n" + "\n".join(chat_results))

    save_data(data)
    message = "No bans processed." if not overall_results else "\n\n".join(overall_results)
    message += f"\nReason: {reason}"
    await ctx.bot.send_message(update.effective_chat.id, message)


# global ban command: /globalban <user_id|@username> <reason (optional)>
async def globalban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /globalban <user_id or @username> [reason]")
        return

    target_arg = ctx.args[0]
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else "No reason provided"
    await _ban_targets(update, ctx, [target_arg], reason, permanent=False)


async def ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /ban <user_id or @username> [reason]")
        return

    target_arg = ctx.args[0]
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else "No reason provided"
    await _ban_targets(update, ctx, [target_arg], reason, permanent=False)


async def mban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /mban <user_id or @username> [additional targets...] [reason]")
        return

    targets = []
    reason_start = None
    for idx, arg in enumerate(ctx.args):
        parsed = parse_target_arg(arg)
        if parsed is None:
            if targets:
                reason_start = idx
                break
            await ctx.bot.send_message(update.effective_chat.id, "Provide at least one valid user id or @username to ban.")
            return
        targets.append(arg)
    if not targets:
        await ctx.bot.send_message(update.effective_chat.id, "Provide at least one valid user id or @username to ban.")
        return

    reason_tokens = ctx.args[reason_start:] if reason_start is not None else []
    if not reason_tokens and len(ctx.args) > len(targets):
        reason_tokens = ctx.args[len(targets):]

    reason = " ".join(reason_tokens) if reason_tokens else "No reason provided"
    await _ban_targets(update, ctx, targets, reason, permanent=False)


async def fban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /fban <user_id or @username> [reason]")
        return

    target_arg = ctx.args[0]
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else "No reason provided"
    await _ban_targets(update, ctx, [target_arg], reason, permanent=True)

# global unban
async def globalunban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /globalunban <user_id or @username>")
        return

    target_arg = ctx.args[0]
    entry = _get_global_ban_entry(target_arg)
    if entry is None:
        await ctx.bot.send_message(update.effective_chat.id, f"{target_arg} is not in the global ban list.")
        return

    await _perform_unban(update, ctx, target_arg)


async def _perform_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE, target_arg: str):
    parsed = parse_target_arg(target_arg)
    if parsed is None:
        await ctx.bot.send_message(update.effective_chat.id, f"Could not parse {target_arg} for unban.")
        return

    entry = _remove_global_ban_entry(target_arg)
    if entry is None:
        await ctx.bot.send_message(update.effective_chat.id, f"{target_arg} is not in the global ban list.")
        return

    save_data(data)

    results = []

    for cid in list(data["managed_chats"]):
        try:
            if isinstance(parsed, str) and parsed.startswith("@"):
                resolved = await _resolve_target_id(ctx, parsed)
                if resolved is None:
                    raise ValueError("could not resolve username")
                uid = resolved
            else:
                uid = int(parsed)
            await ctx.bot.unban_chat_member(cid, uid)
            results.append(f"{cid}: unbanned.")
        except Exception as e:
            results.append(f"{cid}: failed to unban ({e}).")

    await ctx.bot.send_message(update.effective_chat.id, f"Removed {target_arg} from global bans.\nResults:\n" + "\n".join(results))


async def unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if len(ctx.args) < 2:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /unban <user_id or @username> <reason>")
        return

    target_arg = ctx.args[0]
    reason = " ".join(ctx.args[1:])
    await ctx.bot.send_message(update.effective_chat.id, f"Unban requested for {target_arg}. Reason: {reason}")
    await _perform_unban(update, ctx, target_arg)

# simple start
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_message(update.effective_chat.id,
                               "Moderation bot online. Admin commands: /register /unregister /list_managed /ban /mban /fban /unban /stats /protect /help.")


async def protect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /protect <user_id or @username>")
        return

    target_arg = ctx.args[0]
    _add_protected(target_arg)
    save_data(data)
    await ctx.bot.send_message(update.effective_chat.id, f"Added {target_arg} to protected users.")


async def unprotect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /unprotect <user_id or @username>")
        return

    target_arg = ctx.args[0]
    if _remove_protected(target_arg):
        save_data(data)
        await ctx.bot.send_message(update.effective_chat.id, f"Removed {target_arg} from protected users.")
    else:
        await ctx.bot.send_message(update.effective_chat.id, f"{target_arg} was not protected.")


async def getprotected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not data["protected_users"]:
        await ctx.bot.send_message(update.effective_chat.id, "No protected users configured.")
        return

    lines = [f"{i + 1}. {item}" for i, item in enumerate(data["protected_users"])]
    await ctx.bot.send_message(update.effective_chat.id, "Protected users:\n" + "\n".join(lines))


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    protected_count = len(data["protected_users"])
    active_channels = len(data["managed_chats"])
    today = datetime.now(timezone.utc).date()
    banned_today = 0
    for entry in data["ban_logs"]:
        if not isinstance(entry, dict):
            continue
        entry_date = _date_from_timestamp(entry.get("timestamp"))
        if entry_date == today:
            banned_today += 1

    total_blocked = len(data["global_bans"])
    message = (
        "🚀 KickBot v2.0.0 | 🔄 Auto-Sync • 🛡️ Protected\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 KICKBOT STATISTICS\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔐 Protected Users: {protected_count}\n"
        f"📡 Active Channels: {active_channels}\n"
        f"⛔ Banned Today: {banned_today}\n"
        f"🚫 Total Blocked: {total_blocked}\n"
    )
    await ctx.bot.send_message(update.effective_chat.id, message)


async def getid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args:
        target = ctx.args[0]
        try:
            chat_obj = await ctx.bot.get_chat(target)
            await ctx.bot.send_message(update.effective_chat.id, f"ID for {target}: {chat_obj.id}")
        except Exception as exc:
            await ctx.bot.send_message(update.effective_chat.id, f"Failed to resolve {target}: {exc}")
        return

    if update.message and update.message.reply_to_message:
        user = update.message.reply_to_message.from_user
        await ctx.bot.send_message(update.effective_chat.id, f"Replied user ID: {user.id}")
        return

    await ctx.bot.send_message(update.effective_chat.id, "Usage: /getid <@username or user_id> or reply to a user.")


async def roles(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_admin_user(user.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are a configured Super Admin.")
    else:
        await ctx.bot.send_message(update.effective_chat.id, "You have standard user permissions.")


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = (
        "⚡️ QUICK COMMANDS\n"
        "🔴 BAN ACTIONS\n"
        "  └ /ban @user [reason]\n"
        "  └ /mban id1 id2 [reason]\n"
        "  └ /fban @user [reason]\n\n"
        "🟢 UNBAN ACTIONS\n"
        "  └ /unban @user <reason>\n\n"
        "🔵 INFO COMMANDS\n"
        "  └ /help\n  └ /getid @user\n  └ /stats\n  └ /roles\n\n"
        "🛡️ PROTECTION\n"
        "  └ /protect @user\n  └ /unprotect @user\n  └ /getprotected\n"
    )
    await ctx.bot.send_message(update.effective_chat.id, message)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("list_managed", list_managed))
    app.add_handler(CommandHandler("globalban", globalban))
    app.add_handler(CommandHandler("globalunban", globalunban))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("mban", mban))
    app.add_handler(CommandHandler("fban", fban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("protect", protect))
    app.add_handler(CommandHandler("unprotect", unprotect))
    app.add_handler(CommandHandler("getprotected", getprotected))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("roles", roles))
    app.add_handler(CommandHandler("help", help_command))

    print("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
