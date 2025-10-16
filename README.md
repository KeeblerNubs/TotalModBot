# TotalModBot

TotalModBot is a simple Telegram moderation bot that lets trusted admins manage a shared ban list across multiple groups. It uses long polling via `python-telegram-bot` v20.

## Prerequisites

- A Telegram bot token from [BotFather](https://core.telegram.org/bots#botfather).
- Docker Desktop for Windows 10/11 Pro with the WSL2 backend enabled.

## Quick start (Docker)

1. **Clone** or download this repository to your PC.
2. **Copy** the example environment file and add your real values:
   ```powershell
   Copy-Item .env.example .env
   ```
   Edit `.env` in your preferred editor and set:
   - `BOT_TOKEN` to the token you received from BotFather.
   - `ADMINS` to a comma- or semicolon-separated list of Telegram user IDs that can run moderator commands.
   - Optionally tweak `LOG_LEVEL` (INFO, DEBUG, WARNING, ...).
3. **Build and start** the container from a PowerShell or Windows Terminal window opened inside the project directory:
   ```powershell
   docker compose up --build -d
   ```
   The bot will start in the background and connect to Telegram using long polling.
4. **Check the logs** to verify the bot started successfully:
   ```powershell
   docker compose logs -f
   ```
5. **Stop** the bot when you are done:
   ```powershell
   docker compose down
   ```

### Persistent data

The container stores moderation data (managed chat IDs and global bans) inside a named Docker volume (`bot_data`). The data persists across restarts and container rebuilds. To remove it completely, run `docker compose down --volumes`.

## Configuration options

You can override the following environment variables in `.env` or when starting the container:

| Variable    | Description |
|-------------|-------------|
| `BOT_TOKEN` | **Required.** Telegram bot token from BotFather. |
| `ADMINS`    | Optional. Comma/semicolon separated list of Telegram user IDs allowed to run admin commands. Defaults to `123456789`. |
| `LOG_LEVEL` | Optional. Python logging level (e.g. `INFO`, `DEBUG`). Defaults to `INFO`. |
| `DATA_DIR`  | Optional. Directory for persistent JSON state inside the container. Defaults to `/app/data`. |
| `DATA_FILE` | Optional. Custom JSON file path for persistent state. Defaults to `<DATA_DIR>/mod_data.json`. |

## Role-based permission model

TotalModBot supports eight access levels that govern who can run each moderation command. User IDs listed in the `ADMINS` environment variable start as **Super Admins**; you can promote or demote other users at runtime with `/promote` and `/demote` (numeric Telegram IDs only).

| Feature/Command | Super Admin | KB Admin | KB Limited Admin | KB Moderator | KB Lead | Protected | Local Admin | Non-Admin |
|-----------------|:-----------:|:--------:|:----------------:|:------------:|:-------:|:---------:|:-----------:|:---------:|
| **Role Management** | | | | | | | | |
| `/promote` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `/demote` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `/listroles` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Protection Management** | | | | | | | | |
| `/getprotected` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `/getleader` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Ban Operations** | | | | | | | | |
| `/ban` (default scope) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| `/ban --any` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `/unban` (default scope) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| `/unban --any` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `/globalban` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `/globalunban` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `/gbanned` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Data & System** | | | | | | | | |
| `/getadmins` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `/health` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/getid` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Monitoring** | | | | | | | | |
| `/monitoring` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `/monitor` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `/watch` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |

*Default scope* for `/ban` and `/unban` applies to the chat where the command is issued. Adding `--any` attempts the action across every managed chat. Use `/register` and `/unregister` to control which chat IDs are in the managed list.

## Development without Docker (optional)

If you prefer running the bot directly on your machine:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:BOT_TOKEN = "123456:YOUR_TOKEN"
python moderator_bot.py
```

Remember to keep your bot token private.
