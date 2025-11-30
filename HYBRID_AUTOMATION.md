# Telegram Automation Models

This project can run in different automation modes depending on your operational and security needs. Use this guide to pick the right model and understand trade-offs.

## Hybrid Userbot (Bot + User Session)
- **Engine:** Runs a Bot API client and a user MTProto client in the same process so they share logic, database, and permissions.
- **Bot side:** Handles menus/buttons, safe public actions, dashboards, logging, permissions, and command parsing.
- **User side:** Executes powerful or restricted actions such as reinstates/unbans, shadow actions, f-bans, GodLike Protect enforcement, anti-clone detection, invisible monitoring, and acting in groups where bots are limited.
- **Advantages:** Maximum capability, works everywhere, bypasses restrictions, strongest protection layer; ideal for GodLike Protect, F-Ban networks, shadow-ban logic, anti-clone systems.
- **Weaknesses:** More complex to code, must avoid flood triggers, requires secure storage of tokens and session files.

## Regular Telegram Bot (Bot Token)
- **Engine:** Uses a BotFather-issued bot token with the Telegram Bot API over HTTPS.
- **Capabilities:** Send/delete messages, ban/unban, pin, handle commands/buttons/menus; safe from Telegram bans; integrates well with dashboards/webhooks.
- **Limitations:** Cannot join chats on its own, cannot DM users first, limited chat visibility, cannot mimic humans or access contacts/stories/calls.
- **Ideal for:** Moderation bots, utility services, group management, logging, dashboards, and admin tools.

## Userbot (Full Account Automation)
- **Engine:** Logs into a real Telegram account via MTProto (Telethon/Pyrogram) using phone number or session string.
- **Capabilities:** Auto-join chats, read all chats, DM anyone, bypass bot restrictions, view stories, perform ghost/invisible actions, manage contacts, reinstate/unban/mute/unmute at a human level.
- **Risks/Limitations:** High ban risk if misconfigured, can trigger anti-spam, session leak equals full account compromise, no inline mode, requires careful anti-flood logic.
- **Ideal for:** Stealth automation, hybrid protection systems, full account control, ghost tools, shadow actions, anti-clone detection.

---

## Windows 10 + Docker quick start
The steps below assume Docker Desktop with WSL2 back-end. Use PowerShell unless noted.

1. **Enable virtualization & WSL2**
   - In an elevated PowerShell window:
     ```powershell
     dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
     dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
     wsl --install -d Ubuntu
     ```
   - Reboot when prompted; ensure virtualization is enabled in BIOS/UEFI.

2. **Install Docker Desktop**
   - Download Docker Desktop for Windows and during setup choose **WSL 2** as the back-end (not Hyper-V if you want lighter resource usage).
   - After installation, open **Settings → Resources → WSL Integration** and enable your Ubuntu distro.
   - Verify Docker CLI works inside WSL:
     ```bash
     docker version
     docker compose version
     ```

3. **Clone the project inside WSL** (avoids path-performance issues with `/mnt/c` mounts):
   ```bash
   cd ~
   git clone https://github.com/your-org/TotalModBot.git
   cd TotalModBot
   ```

4. **Configure environment**
   - Copy `.env.example` to `.env` and set `BOT_TOKEN`, `ADMINS`, and optional `LOG_LEVEL`, `DATA_DIR`, `DATA_FILE`.
   - Keep tokens/session strings out of Windows paths synced to OneDrive to prevent accidental leaks.

5. **Run with Docker Compose**
   ```bash
   docker compose up --build -d
   docker compose logs -f
   ```
   - Docker Desktop automatically exposes the container to your host. If Windows Firewall prompts, allow the access for Docker.
   - State is stored in the `bot_data` named volume. Remove everything with:
     ```bash
     docker compose down --volumes
     ```

6. **Debug common issues**
   - **Port conflicts:** Stop services using the same port (`netstat -ano | findstr :PORT` in PowerShell, then `Stop-Process -Id <pid>` if safe).
   - **Volume permission errors:** Run `wsl --shutdown` then relaunch Docker Desktop to reset WSL mount state; keep the repo under `/home/<user>`.
   - **Network restrictions:** If behind a corporate proxy, set `HTTP_PROXY`/`HTTPS_PROXY` in `.env` and Docker Desktop settings.

7. **Local Python run (optional)**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python moderator_bot.py
   ```
   - On Windows (outside WSL), prefer PowerShell and long paths like `C:\code\TotalModBot` to avoid spaces in user directories.

Following these steps gives a clean Windows 10/WSL2 + Docker workflow while keeping credentials secure and avoiding common host-path pitfalls.
