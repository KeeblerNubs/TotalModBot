"""Telegram moderation bot with KickBot-style commands."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import wraps
from typing import Any, Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "<PUT_YOUR_TOKEN_HERE>")
DEFAULT_ADMINS = {123456789}
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))
DATA_FILE = os.environ.get("DATA_FILE", os.path.join(DATA_DIR, "mod_data.json"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
ADMIN_LOG_CHAT_ID = os.environ.get("ADMIN_LOG_CHAT_ID")
BANNED_WORDS = os.environ.get("BANNED_WORDS", "")
# ----------------------------

os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)


def _parse_admins(value: Optional[str]) -> set[int]:
    if not value:
        return set(DEFAULT_ADMINS)
    admins: set[int] = set()
    invalid_values: list[str] = []
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
    return admins or set(DEFAULT_ADMINS)


ADMINS = _parse_admins(os.environ.get("ADMINS"))
logger.info("Admin IDs configured: %s", sorted(ADMINS))
logger.info("Persisting moderation data at %s", DATA_FILE)


@dataclass
class Target:
    raw: str
    user_id: Optional[int]
    label: str
    from_reply: bool = False
    consumed_args: int = 0


_data_lock = asyncio.Lock()


def _normalize_key(value: Any) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    base: dict[str, Any] = raw or {}
    env_banned_words = [_normalize_key(word) for word in BANNED_WORDS.replace(";", ",").split(",") if word.strip()]
    managed = base.get("managed_chats", [])
    if not isinstance(managed, list):
        managed = []
    # store as unique ints preserving order
    unique_managed: list[int] = []
    for item in managed:
        try:
            cid = int(item)
        except (TypeError, ValueError):
            continue
        if cid not in unique_managed:
            unique_managed.append(cid)
    base["managed_chats"] = unique_managed

    protected = base.get("protected_users", [])
    if not isinstance(protected, list):
        protected = []
    cleaned_protected: list[Any] = []
    seen_keys: set[str] = set()
    for entry in protected:
        key = _normalize_key(entry)
        if key not in seen_keys:
            cleaned_protected.append(entry)
            seen_keys.add(key)
    base["protected_users"] = cleaned_protected

    bans = base.get("global_bans", [])
    if not isinstance(bans, list):
        bans = []
    cleaned_bans: list[dict[str, Any]] = []
    for entry in bans:
        if isinstance(entry, dict):
            target = entry.get("target")
            if not target:
                continue
            id_key = entry.get("id_key")
            if id_key is not None:
                id_key = _normalize_key(id_key)
            cleaned_bans.append(
                {
                    "target": str(target),
                    "key": entry.get("key", _normalize_key(target)),
                    "reason": entry.get("reason", "No reason provided."),
                    "issuer": entry.get("issuer"),
                    "permanent": bool(entry.get("permanent", False)),
                    "timestamp": entry.get("timestamp", _utcnow_iso()),
                    "id_key": id_key,
                }
            )
        elif isinstance(entry, str):
            cleaned_bans.append(
                {
                    "target": entry,
                    "key": _normalize_key(entry),
                    "reason": "No reason provided.",
                    "issuer": None,
                    "permanent": False,
                    "timestamp": _utcnow_iso(),
                    "id_key": None,
                }
            )
    base["global_bans"] = cleaned_bans

    logs = base.get("ban_logs", [])
    if not isinstance(logs, list):
        logs = []
    cleaned_logs: list[dict[str, Any]] = []
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action", "ban")
        cleaned_logs.append(
            {
                "action": action,
                "target": str(entry.get("target")),
                "issuer": entry.get("issuer"),
                "reason": entry.get("reason", ""),
                "permanent": bool(entry.get("permanent", False)),
                "timestamp": entry.get("timestamp", _utcnow_iso()),
            }
        )
    base["ban_logs"] = cleaned_logs

    banned_words = base.get("banned_words", env_banned_words)
    if not isinstance(banned_words, list):
        banned_words = env_banned_words
    cleaned_words: list[str] = []
    for word in banned_words:
        if not isinstance(word, str):
            continue
        normalized = _normalize_key(word)
        if normalized and normalized not in cleaned_words:
            cleaned_words.append(normalized)
    base["banned_words"] = cleaned_words
    return base


def load_data() -> dict[str, Any]:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return _ensure_schema(json.load(f))
    return _ensure_schema(None)


def save_data(data: dict[str, Any]) -> None:
    tmp_path = f"{DATA_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, DATA_FILE)


data = load_data()


def _find_ban_entry(key: str) -> Optional[dict[str, Any]]:
    normalized = _normalize_key(key)
    for entry in data["global_bans"]:
        if entry.get("key") == normalized:
            return entry
        id_key = entry.get("id_key")
        if id_key is not None and id_key == normalized:
            return entry
    return None


def _is_protected(target: Target | str | int) -> bool:
    key = _normalize_key(target.raw if isinstance(target, Target) else target)
    return any(_normalize_key(item) == key for item in data["protected_users"])


def _record_log(action: str, target: str, issuer: Optional[int], reason: str, *, permanent: bool = False) -> None:
    data["ban_logs"].append(
        {
            "action": action,
            "target": target,
            "issuer": issuer,
            "reason": reason,
            "permanent": permanent,
            "timestamp": _utcnow_iso(),
        }
    )


def _extract_reason(
    ctx: ContextTypes.DEFAULT_TYPE,
    target: Target,
    *,
    default: str,
    require: bool,
) -> Optional[str]:
    tokens: list[str]
    if target.from_reply:
        tokens = ctx.args
    else:
        tokens = ctx.args[target.consumed_args :]
    reason = " ".join(tokens).strip()
    if not reason:
        if require:
            return None
        return default
    return reason


def _normalize_word(word: str) -> str:
    return _normalize_key(word)


async def _add_banned_word(entry: str) -> tuple[bool, str]:
    normalized = _normalize_word(entry)
    if not normalized:
        return False, "No banned word provided."
    async with _data_lock:
        if normalized in data["banned_words"]:
            return False, f"'{entry}' is already in the banned words list."
        data["banned_words"].append(normalized)
        save_data(data)
    return True, f"Added '{normalized}' to the banned words list."


async def _remove_banned_word(entry: str) -> tuple[bool, str]:
    normalized = _normalize_word(entry)
    async with _data_lock:
        if normalized not in data["banned_words"]:
            return False, f"'{entry}' is not in the banned words list."
        data["banned_words"].remove(normalized)
        save_data(data)
    return True, f"Removed '{normalized}' from the banned words list."


def _parse_chat_id(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid ADMIN_LOG_CHAT_ID provided; logging to admin channel disabled.")
        return None


ADMIN_LOG_CHAT_ID_VALUE = _parse_chat_id(ADMIN_LOG_CHAT_ID)


async def _notify_admin_channel(ctx: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    if ADMIN_LOG_CHAT_ID_VALUE is None:
        return
    try:
        await ctx.bot.send_message(ADMIN_LOG_CHAT_ID_VALUE, message)
    except TelegramError as exc:
        logger.warning("Failed to send admin log message: %s", exc)


def admin_only(handler):
    @wraps(handler)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        chat = update.effective_chat
        if not user or user.id not in ADMINS:
            if chat:
                await ctx.bot.send_message(chat.id, "You are not authorized to run that command.")
            return
        return await handler(update, ctx, *args, **kwargs)
    return wrapper


@admin_only
async def addbannedword(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args:
        await ctx.bot.send_message(chat.id, "Usage: /addbannedword <word>")
        return
    success, message = await _add_banned_word(" ".join(ctx.args))
    await ctx.bot.send_message(chat.id, message)
    if success:
        await _notify_admin_channel(ctx, f"🛑 Banned word added by {update.effective_user.id if update.effective_user else 'unknown'}: {message}")


@admin_only
async def removebannedword(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args:
        await ctx.bot.send_message(chat.id, "Usage: /removebannedword <word>")
        return
    success, message = await _remove_banned_word(" ".join(ctx.args))
    await ctx.bot.send_message(chat.id, message)
    if success:
        await _notify_admin_channel(ctx, f"⚠️ Banned word removed by {update.effective_user.id if update.effective_user else 'unknown'}: {message}")


@admin_only
async def listbannedwords(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    async with _data_lock:
        banned_words = list(data["banned_words"])
    if not banned_words:
        await ctx.bot.send_message(chat.id, "No banned words are configured.")
        return
    lines = [f"• {word}" for word in sorted(banned_words)]
    await ctx.bot.send_message(chat.id, "Banned words:\n" + "\n".join(lines))


async def _resolve_target_from_args(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Optional[Target]:
    message = update.effective_message
    raw: Optional[str] = None
    label_hint: Optional[str] = None
    user_hint: Optional[int] = None
    from_reply = False

    if ctx.args:
        raw = ctx.args[0]
        consumed = 1
    elif message and message.reply_to_message and message.reply_to_message.from_user:
        replied = message.reply_to_message.from_user
        raw = str(replied.id)
        label_hint = f"{replied.full_name} ({replied.id})"
        user_hint = replied.id
        from_reply = True
        consumed = 0
    if raw is None:
        return None

    return await _resolve_target_from_value(
        update,
        ctx,
        raw,
        label_hint=label_hint,
        user_hint=user_hint,
        from_reply=from_reply,
        consumed_args=consumed,
    )


async def _resolve_target_from_value(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    raw_value: str,
    *,
    label_hint: Optional[str] = None,
    user_hint: Optional[int] = None,
    from_reply: bool = False,
    consumed_args: int = 1,
) -> Optional[Target]:
    raw_value = raw_value.strip()
    if not raw_value:
        return None

    label = label_hint or raw_value
    user_id = user_hint

    if raw_value.startswith("@"):
        try:
            chat_obj = await ctx.bot.get_chat(raw_value)
            user_id = chat_obj.id
            display = chat_obj.full_name or chat_obj.username or raw_value
            label = f"{display} ({raw_value})"
        except TelegramError as exc:
            logger.warning("Failed to resolve username %s: %s", raw_value, exc)
    else:
        try:
            user_id = int(raw_value)
            label = f"User {user_id}"
        except ValueError:
            pass

    return Target(raw=raw_value, user_id=user_id, label=label, from_reply=from_reply, consumed_args=consumed_args)


async def _propagate_ban(ctx: ContextTypes.DEFAULT_TYPE, target: Target) -> list[str]:
    if not data["managed_chats"]:
        return ["No managed chats registered; nothing to sync."]

    results: list[str] = []
    try:
        bot_user = await ctx.bot.get_me()
    except TelegramError:
        bot_user = None

    for cid in list(data["managed_chats"]):
        try:
            if bot_user is not None:
                bot_member = await ctx.bot.get_chat_member(cid, bot_user.id)
                can_restrict = getattr(bot_member, "can_restrict_members", False)
                if bot_member.status not in ("administrator", "creator") or not can_restrict:
                    results.append(f"Chat {cid}: missing ban permission.")
                    continue
            if target.user_id is None:
                results.append(f"Chat {cid}: skipped (could not resolve user ID for {target.raw}).")
                continue
            await ctx.bot.ban_chat_member(cid, target.user_id)
            results.append(f"Chat {cid}: banned {target.raw}.")
        except TelegramError as exc:
            logger.warning("Failed to ban %s in chat %s: %s", target.raw, cid, exc)
            results.append(f"Chat {cid}: failed to ban ({exc}).")
    return results


async def _propagate_unban(ctx: ContextTypes.DEFAULT_TYPE, target: Target) -> list[str]:
    if not data["managed_chats"]:
        return ["No managed chats registered; nothing to sync."]

    results: list[str] = []
    try:
        bot_user = await ctx.bot.get_me()
    except TelegramError:
        bot_user = None

    for cid in list(data["managed_chats"]):
        try:
            if bot_user is not None:
                bot_member = await ctx.bot.get_chat_member(cid, bot_user.id)
                can_restrict = getattr(bot_member, "can_restrict_members", False)
                if bot_member.status not in ("administrator", "creator") or not can_restrict:
                    results.append(f"Chat {cid}: missing unban permission.")
                    continue
            if target.user_id is None:
                results.append(f"Chat {cid}: skipped (could not resolve user ID for {target.raw}).")
                continue
            await ctx.bot.unban_chat_member(cid, target.user_id)
            results.append(f"Chat {cid}: unbanned {target.raw}.")
        except TelegramError as exc:
            logger.warning("Failed to unban %s in chat %s: %s", target.raw, cid, exc)
            results.append(f"Chat {cid}: failed to unban ({exc}).")
    return results


async def _handle_single_ban(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    *,
    target: Target,
    issuer_id: int,
    reason: str,
    permanent: bool,
) -> str:
    async with _data_lock:
        if _is_protected(target):
            return f"{target.raw} is protected and cannot be banned."
        existing = _find_ban_entry(target.raw)
        if existing:
            return f"{target.raw} is already globally banned."

        entry = {
            "target": target.raw,
            "key": _normalize_key(target.raw),
            "reason": reason,
            "issuer": issuer_id,
            "permanent": permanent,
            "timestamp": _utcnow_iso(),
            "id_key": _normalize_key(str(target.user_id)) if target.user_id is not None else None,
        }
        data["global_bans"].append(entry)
        _record_log("ban", target.raw, issuer_id, reason, permanent=permanent)
        save_data(data)

    results = await _propagate_ban(ctx, target)
    summary_lines = [f"Global ban applied to {target.label}.", f"Reason: {reason}"]
    if permanent:
        summary_lines.append("Flagged as permanent ban.")
    summary_lines.append("")
    summary_lines.extend(results)
    return "\n".join(summary_lines)


async def _auto_ban_for_words(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user or user.is_bot:
        return

    content_parts = []
    if message.text:
        content_parts.append(message.text)
    if message.caption:
        content_parts.append(message.caption)

    combined_text = " ".join(part for part in content_parts if part).strip()
    if not combined_text:
        return

    lowered_text = combined_text.lower()
    async with _data_lock:
        banned_words = list(data["banned_words"])
        chat_is_managed = chat.id in data["managed_chats"]

    matched_words = sorted({word for word in banned_words if word and word in lowered_text})
    if not matched_words or _is_protected(user.id) or user.id in ADMINS:
        return

    if not chat_is_managed:
        try:
            await ctx.bot.ban_chat_member(chat.id, user.id)
        except TelegramError as exc:
            logger.warning("Failed to ban user %s in chat %s for banned words: %s", user.id, chat.id, exc)

    target = Target(raw=str(user.id), user_id=user.id, label=f"{user.full_name} ({user.id})")
    reason = f"Banned for banned word(s): {', '.join(matched_words)}"
    ban_summary = await _handle_single_ban(
        update,
        ctx,
        target=target,
        issuer_id=0,
        reason=reason,
        permanent=False,
    )
    await ctx.bot.send_message(chat.id, ban_summary)

    admin_log_message = (
        "🚫 Auto-ban triggered by banned words\n"
        f"Chat: {chat.title or chat.id} ({chat.id})\n"
        f"User: {user.full_name} (@{user.username or '-'} | {user.id})\n"
        f"Matched: {', '.join(matched_words)}\n"
        f"Message: {combined_text}"
    )
    await _notify_admin_channel(ctx, admin_log_message)


@admin_only
async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    async with _data_lock:
        if chat.id in data["managed_chats"]:
            await ctx.bot.send_message(chat.id, "This chat is already managed.")
            return
        data["managed_chats"].append(chat.id)
        save_data(data)
    await ctx.bot.send_message(chat.id, f"Registered this chat (id={chat.id}) for global moderation.")


@admin_only
async def unregister(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    async with _data_lock:
        if chat.id not in data["managed_chats"]:
            await ctx.bot.send_message(chat.id, "This chat is not managed.")
            return
        data["managed_chats"].remove(chat.id)
        save_data(data)
    await ctx.bot.send_message(chat.id, f"Unregistered this chat (id={chat.id}).")


@admin_only
async def list_managed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    async with _data_lock:
        managed = list(data["managed_chats"])
    if not managed:
        await ctx.bot.send_message(chat.id, "No managed chats registered.")
        return
    lines = [f"{index + 1}. {cid}" for index, cid in enumerate(managed)]
    await ctx.bot.send_message(chat.id, "Managed chats:\n" + "\n".join(lines))


@admin_only
async def globalban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args and not (update.effective_message and update.effective_message.reply_to_message):
        await ctx.bot.send_message(chat.id, "Usage: /globalban <user_id|@username> [reason]")
        return
    target = await _resolve_target_from_args(update, ctx)
    if not target:
        await ctx.bot.send_message(chat.id, "Could not determine a target user.")
        return
    reason = _extract_reason(ctx, target, default="No reason provided.", require=False)
    issuer = update.effective_user.id
    message = await _handle_single_ban(update, ctx, target=target, issuer_id=issuer, reason=reason, permanent=False)
    await ctx.bot.send_message(chat.id, message)


@admin_only
async def ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    issuer = update.effective_user
    chat = update.effective_chat

    target_id = None
    target_label = None

    if message and message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        target_id = target_user.id
        target_label = f"{target_user.full_name} ({target_user.id})"
        reason = " ".join(ctx.args).strip() or "No reason provided"
    elif ctx.args:
        target_arg = ctx.args[0]
        reason = " ".join(ctx.args[1:]).strip() or "No reason provided"
        parsed = parse_target_arg(target_arg)

        if parsed is None:
            await ctx.bot.send_message(chat.id,
                                       "❌ Invalid Usage\n\nPlease specify a user to target.\n\nUsage:\n• Reply: /ban <reason>\n• Username: /ban @username <reason>\n• User ID: /ban user_id <reason>")
            return

        if isinstance(parsed, str) and parsed.startswith("@"):
            try:
                chat_obj = await ctx.bot.get_chat(parsed)
            except Exception as exc:  # pragma: no cover - network failure surface
                logger.warning("Failed to resolve username %s: %s", parsed, exc)
                await ctx.bot.send_message(chat.id, f"Could not resolve username {parsed}.")
                return
            target_id = chat_obj.id
            display_name = chat_obj.full_name or chat_obj.username or parsed
            target_label = f"{display_name} ({target_id})"
        else:
            target_id = parsed
            target_label = str(parsed)
    else:
        await ctx.bot.send_message(chat.id,
                                   "❌ Invalid Usage\n\nPlease specify a user to target.\n\nUsage:\n• Reply: /ban <reason>\n• Username: /ban @username <reason>\n• User ID: /ban user_id <reason>")
        return

    try:
        await ctx.bot.ban_chat_member(chat.id, target_id)
        await ctx.bot.send_message(chat.id, f"Banned {target_label}. Reason: {reason}")
    except Exception as exc:  # pragma: no cover - network failure surface
        logger.exception("Failed to ban user %s in chat %s", target_id, chat.id)
        await ctx.bot.send_message(chat.id, f"Failed to ban user: {exc}")


# helper to resolve a username or user_id from args
def parse_target_arg(arg: str):
    # accept @username or numeric id
    if arg.startswith("@"):
        return arg  # username string
    try:
        return int(arg)
    except ValueError:
        return None

@admin_only
async def fban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args and not (update.effective_message and update.effective_message.reply_to_message):
        await ctx.bot.send_message(chat.id, "Usage: /fban <user_id|@username> [reason]")
        return
    target = await _resolve_target_from_args(update, ctx)
    if not target:
        await ctx.bot.send_message(chat.id, "Could not determine a target user.")
        return
    reason = _extract_reason(ctx, target, default="No reason provided.", require=False)
    issuer = update.effective_user.id
    message = await _handle_single_ban(update, ctx, target=target, issuer_id=issuer, reason=reason, permanent=True)
    await ctx.bot.send_message(chat.id, message)


def _split_mass_ban_args(args: list[str]) -> tuple[list[str], str]:
    if "--" in args:
        idx = args.index("--")
        targets = args[:idx]
        reason = " ".join(args[idx + 1 :]).strip()
    else:
        targets = args
        reason = "Mass ban issued."
    return targets, reason or "Mass ban issued."


@admin_only
async def mban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args or len(ctx.args) < 2 and "--" not in ctx.args:
        await ctx.bot.send_message(chat.id, "Usage: /mban <user1> <user2> [...] -- <reason>")
        return
    raw_targets, reason = _split_mass_ban_args(ctx.args)
    if not raw_targets:
        await ctx.bot.send_message(chat.id, "Usage: /mban <user1> <user2> [...] -- <reason>")
        return

    issuer_id = update.effective_user.id
    results: list[str] = []
    for raw in raw_targets:
        target = await _resolve_target_from_value(update, ctx, raw)
        if not target:
            results.append(f"{raw}: could not resolve target.")
            continue
        message = await _handle_single_ban(update, ctx, target=target, issuer_id=issuer_id, reason=reason, permanent=False)
        results.append(message)
    await ctx.bot.send_message(chat.id, "\n\n".join(results))


async def _handle_unban(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    *,
    target: Target,
    issuer_id: int,
    reason: str,
) -> str:
    async with _data_lock:
        entry = _find_ban_entry(target.raw)
        if not entry:
            return f"{target.raw} is not globally banned."
        if target.user_id is None and entry.get("id_key"):
            try:
                target.user_id = int(entry["id_key"])
            except ValueError:
                target.user_id = None
        data["global_bans"].remove(entry)
        _record_log("unban", target.raw, issuer_id, reason)
        save_data(data)
    results = await _propagate_unban(ctx, target)
    summary_lines = [f"Removed {target.label} from the global ban list.", f"Reason: {reason}", ""]
    summary_lines.extend(results)
    return "\n".join(summary_lines)


@admin_only
async def unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args and not (update.effective_message and update.effective_message.reply_to_message):
        await ctx.bot.send_message(chat.id, "Usage: /unban <user_id|@username> <reason>")
        return
    target = await _resolve_target_from_args(update, ctx)
    if not target:
        await ctx.bot.send_message(chat.id, "Could not determine a target user.")
        return
    reason = _extract_reason(ctx, target, default="", require=True)
    if reason is None:
        await ctx.bot.send_message(chat.id, "Unban requires a reason. Usage: /unban <user> <reason>")
        return
    issuer = update.effective_user.id
    message = await _handle_unban(update, ctx, target=target, issuer_id=issuer, reason=reason)
    await ctx.bot.send_message(chat.id, message)


@admin_only
async def globalunban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args and not (update.effective_message and update.effective_message.reply_to_message):
        await ctx.bot.send_message(chat.id, "Usage: /globalunban <user_id|@username> [reason]")
        return
    target = await _resolve_target_from_args(update, ctx)
    if not target:
        await ctx.bot.send_message(chat.id, "Could not determine a target user.")
        return
    reason = _extract_reason(ctx, target, default="No reason provided.", require=False)
    issuer = update.effective_user.id
    message = await _handle_unban(update, ctx, target=target, issuer_id=issuer, reason=reason)
    await ctx.bot.send_message(chat.id, message)


@admin_only
async def protect(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args and not (update.effective_message and update.effective_message.reply_to_message):
        await ctx.bot.send_message(chat.id, "Usage: /protect <user_id|@username>")
        return
    target = await _resolve_target_from_args(update, ctx)
    if not target:
        await ctx.bot.send_message(chat.id, "Could not determine a target user.")
        return
    async with _data_lock:
        key = _normalize_key(target.raw)
        if any(_normalize_key(item) == key for item in data["protected_users"]):
            await ctx.bot.send_message(chat.id, f"{target.raw} is already protected.")
            return
        data["protected_users"].append(target.raw)
        save_data(data)
    await ctx.bot.send_message(chat.id, f"Added {target.label} to the protected list.")


@admin_only
async def unprotect(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if not ctx.args and not (update.effective_message and update.effective_message.reply_to_message):
        await ctx.bot.send_message(chat.id, "Usage: /unprotect <user_id|@username>")
        return
    target = await _resolve_target_from_args(update, ctx)
    if not target:
        await ctx.bot.send_message(chat.id, "Could not determine a target user.")
        return
    async with _data_lock:
        key = _normalize_key(target.raw)
        for entry in list(data["protected_users"]):
            if _normalize_key(entry) == key:
                data["protected_users"].remove(entry)
                save_data(data)
                await ctx.bot.send_message(chat.id, f"Removed {target.label} from the protected list.")
                return
    await ctx.bot.send_message(chat.id, f"{target.raw} was not protected.")


@admin_only
async def get_protected(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    async with _data_lock:
        protected = list(data["protected_users"])
    if not protected:
        await ctx.bot.send_message(chat.id, "No protected users configured.")
        return
    lines = [f"• {entry}" for entry in protected]
    await ctx.bot.send_message(chat.id, "Protected users:\n" + "\n".join(lines))


def _parse_iso_date(value: str) -> Optional[date]:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.date()


@admin_only
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    async with _data_lock:
        bans = list(data["global_bans"])
        logs = list(data["ban_logs"])
        managed_count = len(data["managed_chats"])
        protected_count = len(data["protected_users"])
    today = datetime.now(timezone.utc).date()
    total_banned = len(bans)
    total_actions = sum(1 for entry in logs if entry.get("action") == "ban")
    permanent_bans = sum(1 for entry in logs if entry.get("action") == "ban" and entry.get("permanent"))
    today_bans = sum(
        1
        for entry in logs
        if entry.get("action") == "ban" and _parse_iso_date(entry.get("timestamp")) == today
    )
    recent = logs[-5:]

    lines = [
        "📊 KickBot Statistics",
        f"• Active managed chats: {managed_count}",
        f"• Currently banned users: {total_banned}",
        f"• Protected users: {protected_count}",
        f"• Total ban actions: {total_actions}",
        f"• Permanent bans: {permanent_bans}",
        f"• Bans today: {today_bans}",
    ]
    if recent:
        lines.append("\nRecent actions:")
        for entry in reversed(recent):
            timestamp = entry.get("timestamp", "")
            action = entry.get("action", "ban").upper()
            reason = entry.get("reason") or "-"
            lines.append(f"• [{action}] {entry.get('target')} — {reason} ({timestamp})")
    await ctx.bot.send_message(chat.id, "\n".join(lines))


async def getid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    message = update.effective_message
    if ctx.args:
        identifier = ctx.args[0]
        if identifier.startswith("@"):
            try:
                chat_obj = await ctx.bot.get_chat(identifier)
                await ctx.bot.send_message(chat.id, f"{identifier} → {chat_obj.id}")
            except TelegramError as exc:
                await ctx.bot.send_message(chat.id, f"Failed to resolve {identifier}: {exc}")
        else:
            await ctx.bot.send_message(chat.id, f"Provided ID: {identifier}")
        return
    if message and message.reply_to_message and message.reply_to_message.from_user:
        replied = message.reply_to_message.from_user
        display = replied.full_name
        if replied.username:
            display += f" (@{replied.username})"
        await ctx.bot.send_message(chat.id, f"{display} → {replied.id}")
        return
    await ctx.bot.send_message(chat.id, "Usage: /getid @username or reply to a user.")


async def roles(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user:
        return
    if not chat:
        return
    role = "Super Admin" if user.id in ADMINS else "User"
    await ctx.bot.send_message(chat.id, f"You are classified as: {role}")


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return

    lines = [
        "🛡️ Moderation commands:",
        "/register — Register this chat for global ban propagation.",
        "/unregister — Remove this chat from global ban propagation.",
        "/list_managed — Show chats currently registered.",
        "/ban <user> <reason> — Ban a user in the current chat.",
        "/unban <user> <reason> — Unban a user in the current chat.",
        "/globalban <user> <reason> — Ban a user across all managed chats.",
        "/globalunban <user> [reason] — Remove a global ban.",
        "/protect <user> — Prevent a user from being banned globally.",
        "/unprotect <user> — Remove a user from the protected list.",
        "/getprotected — List protected users.",
        "/addbannedword <word> — Add a word to the OCR auto-ban list.",
        "/removebannedword <word> — Remove a word from the OCR auto-ban list.",
        "/listbannedwords — Show all OCR auto-ban words.",
        "/stats — Show bot statistics.",
        "/getid — Resolve a user's ID (reply or provide @username/ID).",
        "/roles — Show your access level.",
    ]

    await ctx.bot.send_message(chat.id, "\n".join(lines))


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    text = (
        "🚀 TotalModBot online!\n"
        "Admins can use /help to view moderation commands.\n"
        "Register chats with /register and sync bans across every managed group."
    )
    await ctx.bot.send_message(chat.id, text)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("list_managed", list_managed))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("globalban", globalban))
    app.add_handler(CommandHandler("globalunban", globalunban))
    app.add_handler(CommandHandler("protect", protect))
    app.add_handler(CommandHandler("unprotect", unprotect))
    app.add_handler(CommandHandler("getprotected", get_protected))
    app.add_handler(CommandHandler("addbannedword", addbannedword))
    app.add_handler(CommandHandler("removebannedword", removebannedword))
    app.add_handler(CommandHandler("listbannedwords", listbannedwords))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("roles", roles))

    app.add_handler(MessageHandler(~filters.COMMAND, _auto_ban_for_words))

    logger.info("Starting TotalModBot polling loop...")
    app.run_polling()


if __name__ == "__main__":
    main()
