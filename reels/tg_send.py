"""Отправляет готовый ролик владельцу в Telegram «Избранное». Адресат зашит: только "me", другим не шлёт.

Свой отправщик без таймаута: ролик 15-20 МБ на медленной сети грузится минуты.
  python3 -m reels.tg_send <файл> [--caption "..."] [--document]
"""
import argparse, asyncio, json, os, sys
from .common import ReelError
from .tg import client

TARGET = "me"  # намеренно не параметр


async def run(a):
    if not os.path.isfile(a.file):
        print(json.dumps({"error": f"нет файла {a.file}"}, ensure_ascii=False))
        return 1
    c = client()
    last = {"p": -1}

    def progress(done, total):
        p = int(done * 100 / max(total, 1)) // 10 * 10
        if p != last["p"]:
            last["p"] = p
            print(f"загрузка {p}%", file=sys.stderr, flush=True)

    try:
        await c.connect()
        if not await c.is_user_authorized():
            print(json.dumps({"error": "Telegram не авторизован: python3 -m reels tg-login"}, ensure_ascii=False))
            return 2
        msg = await c.send_file(TARGET, a.file, caption=a.caption or None, force_document=a.document,
                                supports_streaming=not a.document, progress_callback=progress)
        print(json.dumps({"sent": True, "message_id": msg.id, "target": "Избранное"}, ensure_ascii=False))
        return 0
    finally:
        await c.disconnect()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--caption", default="")
    p.add_argument("--document", action="store_true", help="файлом (для PNG с прозрачностью)")
    try:
        sys.exit(asyncio.run(run(p.parse_args())))
    except ReelError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
