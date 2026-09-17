"""Забирает свежее видео из Telegram «Избранное» владельца.

  python3 -m reels.tg_fetch --list
  python3 -m reels.tg_fetch --out ~/Movies/Монтаж/inbox [--id 123]
"""
import argparse, asyncio, json, os, shutil, sys
from .common import WORK_ROOT, ReelError
from .tg import client


def is_video(msg):
    if msg.video or msg.video_note:
        return True
    f = msg.file
    return bool(f and f.mime_type and f.mime_type.startswith("video/"))


async def run(a):
    c = client()
    try:
        await c.connect()
        if not await c.is_user_authorized():
            print(json.dumps({"error": "Telegram не авторизован: python3 -m reels tg-login"}, ensure_ascii=False))
            return 2
        found = [m async for m in c.iter_messages("me", limit=a.scan) if is_video(m)]
        if a.list:
            print(json.dumps([{
                "id": m.id, "date": m.date.isoformat(), "name": m.file.name,
                "mb": round((m.file.size or 0) / 1e6, 1), "duration": m.file.duration,
                "as_file": bool(m.document and not m.video), "caption": m.message or "",
            } for m in found], ensure_ascii=False, indent=1))
            return 0
        pick = next((m for m in found if m.id == a.id), None) if a.id else (found[0] if found else None)
        if not pick:
            print(json.dumps({"error": "в «Избранном» нет видео" if not a.id else f"сообщение {a.id} не найдено"},
                             ensure_ascii=False))
            return 1
        os.makedirs(a.out, exist_ok=True)
        ext = os.path.splitext(pick.file.name or "")[1] or pick.file.ext or ".mp4"
        dst = os.path.join(a.out, f"tg{pick.id}_{pick.date.strftime('%Y%m%d_%H%M%S')}{ext}")
        if os.path.exists(dst) and os.path.getsize(dst) == pick.file.size:
            print(json.dumps({"path": dst, "cached": True}, ensure_ascii=False))
            return 0
        need = (pick.file.size or 0) * 3
        free = shutil.disk_usage(a.out).free
        if need and free < need:
            print(json.dumps({"error": f"мало места: свободно {free/1e9:.1f} ГБ, нужно ~{need/1e9:.1f} ГБ"},
                             ensure_ascii=False))
            return 3
        await c.download_media(pick, dst + ".part")
        os.replace(dst + ".part", dst)
        print(json.dumps({"path": dst, "id": pick.id, "caption": pick.message or ""}, ensure_ascii=False))
        return 0
    finally:
        await c.disconnect()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true")
    p.add_argument("--id", type=int)
    p.add_argument("--scan", type=int, default=30)
    p.add_argument("--out", default=str(WORK_ROOT / "inbox"))
    try:
        sys.exit(asyncio.run(run(p.parse_args())))
    except ReelError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
