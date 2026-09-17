"""Telegram: личный аккаунт владельца, только чат «Избранное» (Saved Messages).

Ключи приложения берутся с https://my.telegram.org → API development tools и кладутся в
~/.config/reels-editor/telegram.env:
    TG_API_ID=1234567
    TG_API_HASH=abcdef...
Сессия создаётся один раз командой `python3 -m reels tg-login` (Telegram пришлёт код входа).
Файл сессии — ~/.config/reels-editor/telegram.session, в репозиторий он НЕ попадает.
"""
import os
from .common import CONFIG_DIR, ReelError

ENV = CONFIG_DIR / "telegram.env"
SESSION = CONFIG_DIR / "telegram"   # Telethon сам добавит .session


def creds():
    if not ENV.exists():
        raise ReelError(f"нет {ENV}: положи туда TG_API_ID и TG_API_HASH (см. INSTALL.md, шаг Telegram)")
    env = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    try:
        return int(env["TG_API_ID"]), env["TG_API_HASH"]
    except KeyError as e:
        raise ReelError(f"в {ENV} нет {e.args[0]}") from None


def client():
    try:
        from telethon import TelegramClient
    except ImportError:
        raise ReelError("не установлен telethon: python3 -m pip install --user telethon") from None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    api_id, api_hash = creds()
    return TelegramClient(str(SESSION), api_id, api_hash)


async def login():
    c = client()
    await c.start()          # спросит телефон и код из Telegram
    me = await c.get_me()
    await c.disconnect()
    os.chmod(str(SESSION) + ".session", 0o600)
    return f"вход выполнен: {me.first_name} (id {me.id})"
