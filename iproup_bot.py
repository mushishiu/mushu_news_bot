#!/usr/bin/env python3
"""Avisa por Telegram cuando hay una noticia nueva en iProUP (resumen corto con Claude).

Uso:
  python3 iproup_bot.py              # revisa el feed y envía las noticias nuevas
  python3 iproup_bot.py --dry-run    # muestra qué enviaría, sin enviar ni guardar
  python3 iproup_bot.py --chat-id    # muestra tu chat_id (antes escribile algo al bot)
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import escape, unescape
from pathlib import Path

BASE = Path(__file__).resolve().parent
FEED_URL = "https://www.iproup.com/rss/home"
SEEN_FILE = BASE / "seen.json"
UA = {"User-Agent": "Mozilla/5.0"}
MODEL = "claude-haiku-5-5"


def load_env():
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def http(url, data=None, headers=None, timeout=45, retries=3):
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers or UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(3)


def strip_html(html):
    html = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def fetch_items():
    root = ET.fromstring(http(FEED_URL))
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        items.append({
            "link": link,
            "title": (it.findtext("title") or "").strip(),
            "desc": strip_html(it.findtext("description") or ""),
        })
    return items


def summarize(item):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return item["desc"][:350]
    try:
        text = strip_html(http(item["link"]))[:6000]
    except Exception:
        text = item["desc"]
    prompt = (
        "Resumí esta noticia en UN solo párrafo muy corto (máximo 2-3 oraciones), "
        "en español rioplatense neutro, sin título ni viñetas, solo lo esencial.\n\n"
        f"Título: {item['title']}\n\nContenido: {text}"
    )
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    out = http(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    return json.loads(out)["content"][0]["text"].strip()


def send_telegram(text):
    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    data = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    http(f"https://api.telegram.org/bot{token}/sendMessage", data=data, headers={})


def format_message(item):
    return (
        f"📰 <b>{escape(item['title'])}</b>\n\n"
        f"<i>{escape(summarize(item))}</i>\n\n"
        f"{escape(item['link'])}"
    )


def show_chat_id():
    token = os.environ["TELEGRAM_TOKEN"]
    res = json.loads(http(f"https://api.telegram.org/bot{token}/getUpdates"))
    ids = {u["message"]["chat"]["id"] for u in res["result"] if "message" in u}
    print("chat_id:", ", ".join(map(str, ids)) or "ninguno (escribile algo al bot primero)")


def main():
    load_env()
    if "--chat-id" in sys.argv:
        return show_chat_id()
    dry = "--dry-run" in sys.argv

    items = fetch_items()
    first_run = not SEEN_FILE.exists()
    seen = set(json.loads(SEEN_FILE.read_text())) if not first_run else set()

    if first_run and not dry:
        # Primera corrida: marcar todo como visto para no mandar un aluvión.
        SEEN_FILE.write_text(json.dumps([i["link"] for i in items]))
        print(f"Primera corrida: {len(items)} noticias marcadas como vistas.")
        return

    new = [i for i in items if i["link"] and i["link"] not in seen]
    print(f"{len(items)} en el feed, {len(new)} nuevas.")
    for item in reversed(new):  # de la más vieja a la más nueva
        msg = format_message(item)
        if dry:
            print("---\n" + msg)
            continue
        send_telegram(msg)
        seen.add(item["link"])
        SEEN_FILE.write_text(json.dumps(sorted(seen)))


if __name__ == "__main__":
    main()
