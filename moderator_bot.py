# moderator_bot.py
import json
import logging
import os

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
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"managed_chats": [], "global_bans": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

data = load_data()

# ----- ROLE MATRIX --------------------------------------------------------

RoleName = str

ROLE_DISPLAY_NAMES: dict[RoleName, str] = {
    "super_admin": "Super Admin",
    "kb_admin": "KB Admin",
    "kb_limited_admin": "KB Limited Admin",
    "kb_moderator": "KB Moderator",
    "kb_lead": "KB Lead",
    "protected": "Protected",
    "local_admin": "Local Admin",
    "non_admin": "Non-Admin",
}

ROLE_ORDER: list[RoleName] = [
    "super_admin",
    "kb_admin",
    "kb_limited_admin",
    "kb_moderator",
    "kb_lead",
    "protected",
    "local_admin",
    "non_admin",
]

COMMAND_PERMISSIONS: dict[str, set[RoleName]] = {
    # role management
    "promote": {"super_admin", "kb_admin"},
    "demote": {"super_admin", "kb_admin"},
    "listroles": {"super_admin", "kb_admin", "kb_limited_admin"},
    # protection management
    "getprotected": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "kb_lead"},
    "getleader": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "kb_lead"},
    # ban operations
    "ban_authorized": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator"},
    "ban_any_chat": {"super_admin", "kb_admin"},
    "ban_own_group": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "local_admin"},
    "unban": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "local_admin"},
    # data export / information
    "gbanned": {"super_admin", "kb_admin", "kb_limited_admin"},
    "getadmins": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "kb_lead"},
    "health": set(ROLE_DISPLAY_NAMES.keys()),
    "getid": set(ROLE_DISPLAY_NAMES.keys()),
    # monitoring
    "monitoring": {"super_admin", "kb_admin", "kb_limited_admin"},
    "monitor": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator"},
    "watch": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "kb_lead"},
    # legacy bot management commands
    "register": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "local_admin"},
    "unregister": {"super_admin", "kb_admin", "kb_limited_admin", "kb_moderator", "local_admin"},
    "list_managed": {"super_admin", "kb_admin", "kb_limited_admin"},
    "globalban": {"super_admin", "kb_admin"},
    "globalunban": {"super_admin", "kb_admin"},
}


def ensure_defaults() -> None:
    data.setdefault("user_roles", {})
    data.setdefault("monitoring_chats", [])
    # make sure all configured ADMINS are stored as super admins in persistence
    changed = False
    for admin_id in ADMINS:
        key = str(admin_id)
        if data["user_roles"].get(key) != "super_admin":
            data["user_roles"][key] = "super_admin"
            changed = True
    if changed:
        save_data(data)


ensure_defaults()


def get_user_role(user_id: int) -> RoleName:
    key = str(user_id)
    if key in data.get("user_roles", {}):
        return data["user_roles"][key]
    if user_id in ADMINS:
        data.setdefault("user_roles", {})[key] = "super_admin"
        save_data(data)
        return "super_admin"
    return "non_admin"


def set_user_role(user_id: int, role: RoleName) -> None:
    data.setdefault("user_roles", {})[str(user_id)] = role
    save_data(data)


def list_users_with_role(role: RoleName) -> list[int]:
    return [int(uid) for uid, r in data.get("user_roles", {}).items() if r == role]


def has_permission(role: RoleName, permission: str) -> bool:
    if role == "super_admin":
        return True
    allowed = COMMAND_PERMISSIONS.get(permission)
    if allowed is None:
        return False
    return role in allowed


def require_permission(permission: str):
    def decorator(func):
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            role = get_user_role(update.effective_user.id)
            if not has_permission(role, permission):
                await ctx.bot.send_message(
                    update.effective_chat.id,
                    "You do not have permission to run this command."
                )
                return
            return await func(update, ctx)

        return wrapper

    return decorator

# register current chat as managed (only works in groups/channels)
@require_permission("register")
async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    cid = chat.id
    if cid in data["managed_chats"]:
        await ctx.bot.send_message(chat.id, "This chat is already managed.")
        return

    data["managed_chats"].append(cid)
    save_data(data)
    await ctx.bot.send_message(chat.id, f"Registered this chat (id={cid}) as managed.")

# unregister current chat
@require_permission("unregister")
async def unregister(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    cid = chat.id
    if cid not in data["managed_chats"]:
        await ctx.bot.send_message(chat.id, "This chat is not managed.")
        return

    data["managed_chats"].remove(cid)
    save_data(data)
    await ctx.bot.send_message(chat.id, f"Unregistered this chat (id={cid}).")

# list managed chats
@require_permission("list_managed")
async def list_managed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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


async def resolve_user_id(
    ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, target: int | str
) -> int | None:
    if isinstance(target, int):
        return target
    if isinstance(target, str) and target.startswith("@"):
        try:
            member = await ctx.bot.get_chat_member(chat_id, target)
            return member.user.id
        except Exception:
            return None
    return None


def render_role_name(role: RoleName) -> str:
    return ROLE_DISPLAY_NAMES.get(role, role)


def format_user_list(user_ids: list[int]) -> str:
    if not user_ids:
        return "(none)"
    return ", ".join(str(uid) for uid in sorted(user_ids))


@require_permission("promote")
async def promote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        await ctx.bot.send_message(
            update.effective_chat.id,
            "Usage: /promote <user_id> <role>. Available roles: "
            + ", ".join(f"{r} ({render_role_name(r)})" for r in ROLE_ORDER),
        )
        return

    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await ctx.bot.send_message(update.effective_chat.id, "Target must be a numeric user ID.")
        return

    role_key = ctx.args[1].lower()
    if role_key not in ROLE_DISPLAY_NAMES:
        await ctx.bot.send_message(
            update.effective_chat.id,
            "Unknown role. Valid roles: " + ", ".join(ROLE_DISPLAY_NAMES),
        )
        return

    set_user_role(target_id, role_key)
    await ctx.bot.send_message(
        update.effective_chat.id,
        f"Promoted {target_id} to {render_role_name(role_key)}.",
    )


@require_permission("demote")
async def demote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /demote <user_id>.")
        return
    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await ctx.bot.send_message(update.effective_chat.id, "Target must be a numeric user ID.")
        return

    set_user_role(target_id, "non_admin")
    await ctx.bot.send_message(update.effective_chat.id, f"Demoted {target_id} to Non-Admin.")


@require_permission("listroles")
async def list_roles(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = []
    for role in ROLE_ORDER:
        if role == "non_admin":
            continue
        users = format_user_list(list_users_with_role(role))
        lines.append(f"{render_role_name(role)}: {users}")
    await ctx.bot.send_message(update.effective_chat.id, "Configured roles:\n" + "\n".join(lines))


@require_permission("getprotected")
async def get_protected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = format_user_list(list_users_with_role("protected"))
    await ctx.bot.send_message(update.effective_chat.id, f"Protected users: {users}")


@require_permission("getleader")
async def get_leader(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = format_user_list(list_users_with_role("kb_lead"))
    await ctx.bot.send_message(update.effective_chat.id, f"KB Leads: {users}")


@require_permission("ban_authorized")
async def ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await ctx.bot.send_message(
            update.effective_chat.id,
            "Usage: /ban <user_id|@username> [--any] [reason]",
        )
        return

    role = get_user_role(update.effective_user.id)
    target_arg = ctx.args[0]
    flags = {arg for arg in ctx.args[1:] if arg.startswith("--")}
    reason_parts = [arg for arg in ctx.args[1:] if not arg.startswith("--")]
    reason = " ".join(reason_parts) if reason_parts else "No reason provided"
    scope = "own"
    if "--any" in flags:
        if not has_permission(role, "ban_any_chat"):
            await ctx.bot.send_message(update.effective_chat.id, "You lack permission to ban in every managed chat.")
            return
        scope = "any"
    elif not has_permission(role, "ban_own_group"):
        await ctx.bot.send_message(update.effective_chat.id, "You lack permission to ban users in this chat.")
        return

    target = parse_target_arg(target_arg)
    if target is None:
        await ctx.bot.send_message(update.effective_chat.id, "Could not parse user id. Provide numeric id or @username.")
        return

    if scope == "own":
        chat_id = update.effective_chat.id
        uid = await resolve_user_id(ctx, chat_id, target)
        if uid is None:
            await ctx.bot.send_message(update.effective_chat.id, "Could not resolve that user in this chat.")
            return
        await ctx.bot.ban_chat_member(chat_id, uid)
        await ctx.bot.send_message(
            update.effective_chat.id,
            f"Banned {target_arg} from this chat. Reason: {reason}",
        )
        return

    results = []
    for cid in list(data["managed_chats"]):
        try:
            uid = await resolve_user_id(ctx, cid, target)
            if uid is None:
                results.append(f"Chat {cid}: user not found.")
                continue
            await ctx.bot.ban_chat_member(cid, uid)
            results.append(f"Chat {cid}: banned {target_arg}.")
        except Exception as exc:
            logger.exception("Failed banning in chat %s", cid)
            results.append(f"Chat {cid}: failed ({exc}).")

    await ctx.bot.send_message(
        update.effective_chat.id,
        f"Ban attempt for {target_arg}. Reason: {reason}\n\n" + "\n".join(results),
    )


@require_permission("unban")
async def unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await ctx.bot.send_message(
            update.effective_chat.id,
            "Usage: /unban <user_id|@username> [--any]",
        )
        return

    role = get_user_role(update.effective_user.id)
    target_arg = ctx.args[0]
    flags = {arg for arg in ctx.args[1:] if arg.startswith("--")}
    target = parse_target_arg(target_arg)
    if target is None:
        await ctx.bot.send_message(update.effective_chat.id, "Could not parse user id. Provide numeric id or @username.")
        return

    scope = "own"
    if "--any" in flags:
        if not has_permission(role, "ban_any_chat"):
            await ctx.bot.send_message(update.effective_chat.id, "You lack permission to unban in every managed chat.")
            return
        scope = "any"
    elif not has_permission(role, "ban_own_group"):
        await ctx.bot.send_message(update.effective_chat.id, "You lack permission to unban in this chat.")
        return

    if scope == "own":
        uid = await resolve_user_id(ctx, update.effective_chat.id, target)
        if uid is None:
            await ctx.bot.send_message(update.effective_chat.id, "Could not resolve that user in this chat.")
            return
        await ctx.bot.unban_chat_member(update.effective_chat.id, uid)
        await ctx.bot.send_message(update.effective_chat.id, f"Unbanned {target_arg} in this chat.")
        return

    results = []
    for cid in list(data["managed_chats"]):
        try:
            uid = await resolve_user_id(ctx, cid, target)
            if uid is None:
                results.append(f"Chat {cid}: user not found.")
                continue
            await ctx.bot.unban_chat_member(cid, uid)
            results.append(f"Chat {cid}: unbanned {target_arg}.")
        except Exception as exc:
            results.append(f"Chat {cid}: failed ({exc}).")

    await ctx.bot.send_message(
        update.effective_chat.id,
        f"Unban attempt for {target_arg}.\n" + "\n".join(results),
    )


@require_permission("gbanned")
async def gbanned(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bans = data.get("global_bans", [])
    if not bans:
        await ctx.bot.send_message(update.effective_chat.id, "Global ban list is empty.")
        return
    await ctx.bot.send_message(
        update.effective_chat.id,
        "Global bans:\n" + "\n".join(f"- {entry}" for entry in bans),
    )


@require_permission("getadmins")
async def get_admins(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = []
    for role in ROLE_ORDER:
        if role in {"non_admin", "protected"}:
            continue
        users = format_user_list(list_users_with_role(role))
        if users != "(none)":
            lines.append(f"{render_role_name(role)}: {users}")
    if not lines:
        lines.append("No admins configured.")
    await ctx.bot.send_message(update.effective_chat.id, "Admins:\n" + "\n".join(lines))


@require_permission("health")
async def health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_message(
        update.effective_chat.id,
        "Bot is running. Managed chats: {}. Monitoring: {}.".format(
            len(data.get("managed_chats", [])), len(data.get("monitoring_chats", []))
        ),
    )


@require_permission("getid")
async def get_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    await ctx.bot.send_message(
        chat.id,
        f"Chat ID: {chat.id}\nYour user ID: {user.id}",
    )


@require_permission("monitoring")
async def monitoring(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chats = data.get("monitoring_chats", [])
    if not chats:
        await ctx.bot.send_message(update.effective_chat.id, "No chats are being monitored.")
        return
    await ctx.bot.send_message(
        update.effective_chat.id,
        "Monitoring chats:\n" + "\n".join(str(cid) for cid in chats),
    )


@require_permission("monitor")
async def monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = [arg.lower() for arg in ctx.args]
    if args and args[0] in {"off", "disable", "stop"}:
        if chat_id in data.get("monitoring_chats", []):
            data["monitoring_chats"].remove(chat_id)
            save_data(data)
            await ctx.bot.send_message(chat_id, "Monitoring disabled for this chat.")
        else:
            await ctx.bot.send_message(chat_id, "This chat was not monitored.")
        return

    if chat_id not in data.get("monitoring_chats", []):
        data.setdefault("monitoring_chats", []).append(chat_id)
        save_data(data)
    await ctx.bot.send_message(chat_id, "Monitoring enabled for this chat.")


@require_permission("watch")
async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chats = data.get("monitoring_chats", [])
    await ctx.bot.send_message(
        update.effective_chat.id,
        "Currently watched chats: " + (", ".join(str(cid) for cid in chats) or "none"),
    )

# global ban command: /globalban <user_id|@username> <reason (optional)>
@require_permission("globalban")
async def globalban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /globalban <user_id or @username> [reason]")
        return

    target_arg = ctx.args[0]
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else "No reason provided"
    target = parse_target_arg(target_arg)
    if target is None:
        await ctx.bot.send_message(update.effective_chat.id, "Could not parse user id. Provide numeric id or @username.")
        return

    # Add to persistent global blacklist (store raw arg)
    if target_arg in data["global_bans"]:
        await ctx.bot.send_message(update.effective_chat.id, f"{target_arg} is already globally banned.")
        return

    data["global_bans"].append(target_arg)
    save_data(data)

    # Attempt to ban from each managed chat where bot has permission
    results = []
    for cid in list(data["managed_chats"]):
        try:
            # First try to resolve username to id if target is username
            if isinstance(target, str) and target.startswith("@"):
                member = await ctx.bot.get_chat_member(cid, target)  # may raise
                uid = member.user.id
            else:
                uid = target
            # Ensure bot is admin in the chat by checking its status
            bot_member = await ctx.bot.get_chat_member(cid, (await ctx.bot.get_me()).id)
            if not (bot_member.status in ("administrator", "creator") and bot_member.can_restrict_members):
                results.append(f"Chat {cid}: bot lacks ban permission — skipped.")
                continue

            await ctx.bot.ban_chat_member(cid, uid)
            results.append(f"Chat {cid}: banned user {target_arg}.")
        except Exception as e:
            logger.exception("Ban failed for chat %s", cid)
            results.append(f"Chat {cid}: failed to ban ({e}).")

    await ctx.bot.send_message(update.effective_chat.id,
                               f"Global ban applied for {target_arg}.\nReason: {reason}\n\nResults:\n" + "\n".join(results))

# global unban
@require_permission("globalunban")
async def globalunban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /globalunban <user_id or @username>")
        return

    target_arg = ctx.args[0]
    if target_arg not in data["global_bans"]:
        await ctx.bot.send_message(update.effective_chat.id, f"{target_arg} is not in the global ban list.")
        return

    data["global_bans"].remove(target_arg)
    save_data(data)

    results = []
    for cid in list(data["managed_chats"]):
        try:
            if target_arg.startswith("@"):
                member = await ctx.bot.get_chat_member(cid, target_arg)
                uid = member.user.id
            else:
                uid = int(target_arg)
            await ctx.bot.unban_chat_member(cid, uid)
            results.append(f"{cid}: unbanned.")
        except Exception as e:
            results.append(f"{cid}: failed to unban ({e}).")

    await ctx.bot.send_message(update.effective_chat.id, f"Removed {target_arg} from global bans.\nResults:\n" + "\n".join(results))

# simple start
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_message(
        update.effective_chat.id,
        "Moderation bot online. Key commands: /promote /demote /listroles /register /ban /unban /globalban /globalunban /monitor.",
    )


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("demote", demote))
    app.add_handler(CommandHandler("listroles", list_roles))
    app.add_handler(CommandHandler("getprotected", get_protected))
    app.add_handler(CommandHandler("getleader", get_leader))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("list_managed", list_managed))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("globalban", globalban))
    app.add_handler(CommandHandler("globalunban", globalunban))
    app.add_handler(CommandHandler("gbanned", gbanned))
    app.add_handler(CommandHandler("getadmins", get_admins))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("getid", get_id))
    app.add_handler(CommandHandler("monitoring", monitoring))
    app.add_handler(CommandHandler("monitor", monitor))
    app.add_handler(CommandHandler("watch", watch))

    print("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
