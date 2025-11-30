# TotalModBot

TotalModBot is a Telegram moderation assistant that keeps a shared global ban list across every registered chat. It ships with KickBot-style commands for quickly banning raiders, safeguarding trusted members, and reviewing moderation statistics.

## Commands

- `/register` – mark the current chat as managed so it receives global ban updates.
- `/unregister` – remove the current chat from the managed list.
- `/ban` – ban a user from the current chat (reply to a message or pass a username/user ID and optional reason).
- `/globalban` – add a user to the shared global ban list and attempt to ban them from all managed chats.
- `/globalunban` – remove a user from the shared global ban list and unban them from managed chats where possible.
- `/list_managed` – show the chats currently registered with the bot.

## Prerequisites

- Register and unregister chats that should receive synchronized bans.
- Apply single, forced, or bulk bans that propagate to every managed chat.
- Maintain a protected list of users who should never be banned, even when mass banning.
- Log every ban and unban with issuer, reason, permanence flag, and timestamp so statistics stay accurate.
- Look up user IDs, review protected entries, and confirm your access level.

Moderation state is stored in a JSON file (`DATA_FILE`) that captures managed chat IDs, protected users, a detailed global ban ledger, and a rolling audit log of ban actions for stats.

## Getting started

1. **Create a bot** with [@BotFather](https://core.telegram.org/bots#botfather) and grab its token.
2. **Clone** the repository and copy `.env.example` to `.env` (or export variables manually).
3. **Set** the following environment variables:
   - `BOT_TOKEN` – required bot token.
   - `ADMINS` – comma or semicolon separated list of Telegram user IDs allowed to run admin commands. Defaults to `123456789`.
   - Optional overrides: `LOG_LEVEL`, `DATA_DIR`, `DATA_FILE`.
4. **Install dependencies** and launch the bot:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python moderator_bot.py
   ```

   Or build and run the included Docker Compose setup:

   ```bash
   docker compose up --build -d
   ```

Logs are available with `docker compose logs -f`.

## Kubernetes deployment

The `bridge/base` and `bridge/overlays/desktop` manifests mirror the Compose setup
for clusters that support Kustomize. Before applying them, replace the placeholder
`ADMINS` and `BOT_TOKEN` values in `bridge/base/moderator-bot-deployment.yaml` with
your own IDs and bot token. Then deploy with:

```bash
kubectl apply -k bridge/base
# Or for Docker Desktop's built-in Kubernetes:
kubectl apply -k bridge/overlays/desktop
```

Persistent data is stored in the `moderator-bot-bot-data` PVC. To reset state,
delete the claim and the deployment.

5. **On Windows 10 + WSL2 + Docker Desktop**, follow the quick-start steps and troubleshooting notes in [HYBRID_AUTOMATION.md](HYBRID_AUTOMATION.md#windows-10--docker-quick-start) to avoid path and permissions issues.

## Persistent storage

Moderation state (managed chat IDs, ban ledger, protected users, and action logs) is stored in `DATA_FILE`. When running with Docker Compose, the file lives in the `bot_data` named volume so that state survives container restarts. Remove the data by running `docker compose down --volumes`.

## Command reference

| Command | Description |
|---------|-------------|
| `/start` | Display a short introduction. |
| `/help` | Show the KickBot-style quick reference. |
| `/register` / `/unregister` | Add or remove the current chat from the managed list. |
| `/list_managed` | List every chat that receives synchronized bans. |
| `/ban` / `/globalban` | Globally ban a user with an optional reason. |
| `/fban` | Force a ban and mark it as permanent in the logs. |
| `/mban` | Ban multiple users at once (`/mban <user1> <user2> ... -- <reason>`). |
| `/unban` | Remove a user from the global ban list (reason required). |
| `/globalunban` | Legacy alias for `/unban` that allows an optional reason. |
| `/protect` / `/unprotect` | Manage the protected allow-list. |
| `/getprotected` | Display all protected users. |
| `/stats` | Show KickBot-style moderation statistics and recent actions. |
| `/getid` | Resolve a Telegram ID from a username or replied message. |
| `/roles` | Reveal whether you are configured as a Super Admin. |

## Notes

- Commands that modify moderation state require the caller to be listed in `ADMINS`.
- Bans are best-effort: the bot must be an administrator with the right to restrict members in each managed chat.
- Protected users cannot be banned until removed from the protected list.

For an overview of hybrid userbots, Bot API bots, and full userbots—and when to use each—see [HYBRID_AUTOMATION.md](HYBRID_AUTOMATION.md).

Enjoy cleaner chats! 🙌
