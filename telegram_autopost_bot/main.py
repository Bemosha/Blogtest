import asyncio
import base64
import hashlib
import html
import io
import json
import logging
import os
import random
import re
import subprocess
import tempfile
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes


load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram_autopost_bot")

LOG_FILE = os.path.join(BASE_DIR, "bot.log")
if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(file_handler)


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable is required: {name}")
    return value


BOT_TOKEN = env_required("TELEGRAM_BOT_TOKEN")
TARGET_CHAT_ID = env_required("TELEGRAM_CHAT_ID")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))
MAX_POSTS_PER_CYCLE = int(os.getenv("MAX_POSTS_PER_CYCLE", "3"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))
MAX_TITLE_LENGTH = int(os.getenv("MAX_TITLE_LENGTH", "120"))
MAX_SUMMARY_LENGTH = int(os.getenv("MAX_SUMMARY_LENGTH", "180"))
IT_ONLY = os.getenv("IT_ONLY", "1").strip().lower() in {"1", "true", "yes", "on"}
TOPIC_KEYWORDS = [
    k.strip().lower()
    for k in os.getenv(
        "TOPIC_KEYWORDS",
        "it,айти,технолог,программ,разработк,python,javascript,java,go,ai,ml,devops,backend,frontend,linux,кибербезопас,security,cloud,data",
    ).split(",")
    if k.strip()
]
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; TelegramAutoPostBot/1.0; +https://t.me/)"
)
SOURCES_FILE = os.getenv("SOURCES_FILE", os.path.join(BASE_DIR, "sources.json"))
STATE_FILE = os.getenv("STATE_FILE", os.path.join(BASE_DIR, "state.json"))

# YouTube API video mode (for educational channels)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YOUTUBE_MAX_DURATION_SECONDS = int(os.getenv("YOUTUBE_MAX_DURATION_SECONDS", "900"))
VIDEO_REQUIRED_PHRASES = [
    p.strip().lower()
    for p in os.getenv("VIDEO_REQUIRED_PHRASES", "имя прилагательное,2 класс").split(",")
    if p.strip()
]

# Optional AI image posting ("Nano Banana" provider, OpenAI-like API)
ENABLE_AI_IMAGE_POSTS = os.getenv("ENABLE_AI_IMAGE_POSTS", "0").strip().lower() in {"1", "true", "yes", "on"}
AI_IMAGE_POST_CHANCE = float(os.getenv("AI_IMAGE_POST_CHANCE", "0.2"))
NANO_BANANA_API_URL = os.getenv("NANO_BANANA_API_URL", "https://openrouter.ai/api/v1/images/generations").strip()
NANO_BANANA_API_KEY = os.getenv("NANO_BANANA_API_KEY", "").strip()
NANO_BANANA_MODEL = os.getenv("NANO_BANANA_MODEL", "google/gemini-2.5-flash-image-preview")
AI_IMAGE_SIZE = os.getenv("AI_IMAGE_SIZE", "1024x1024")
AI_IMAGE_STYLE_PROMPT = os.getenv(
    "AI_IMAGE_STYLE_PROMPT",
    "clean modern IT illustration, channel logo concept, vector style, bright contrast, no watermark, no readable text",
)
LINK_ENCRYPTION_ENABLED = os.getenv("LINK_ENCRYPTION_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
LINK_ENCRYPTION_KEY = os.getenv("LINK_ENCRYPTION_KEY", "change_me_secret_key")
YTDLP_ENABLED = os.getenv("YTDLP_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
YTDLP_PATH = os.getenv("YTDLP_PATH", "yt-dlp").strip() or "yt-dlp"
DISABLE_UPDATES_POLLING = os.getenv("DISABLE_UPDATES_POLLING", "1").strip().lower() in {"1", "true", "yes", "on"}

# Post readability + subscribe CTA
FOLLOW_CTA_ENABLED = os.getenv("FOLLOW_CTA_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
CHANNEL_HANDLE = os.getenv("CHANNEL_HANDLE", "").strip().lstrip("@")
AUTHOR_HANDLE = os.getenv("AUTHOR_HANDLE", "").strip().lstrip("@")
SUBSCRIBE_CTA_TEXT = os.getenv(
    "SUBSCRIBE_CTA_TEXT",
    "Подпишись, чтобы не пропускать полезные IT-разборы и быстрые новости.",
).strip()


@dataclass
class PostItem:
    uid: str
    title: str
    link: str
    summary: str
    source_name: str
    timestamp: float


def load_json(path: str, fallback: Any) -> Any:
    if not os.path.exists(path):
        return fallback
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_sources() -> List[Dict[str, Any]]:
    data = load_json(SOURCES_FILE, [])
    if not isinstance(data, list):
        raise RuntimeError(f"{SOURCES_FILE} must be a JSON array")
    return data


def load_state() -> Dict[str, Any]:
    state = load_json(STATE_FILE, {"posted_ids": []})
    if "posted_ids" not in state or not isinstance(state["posted_ids"], list):
        state["posted_ids"] = []
    return state


def compact_posted_ids(state: Dict[str, Any], keep_last: int = 2000) -> None:
    posted = state.get("posted_ids", [])
    if len(posted) > keep_last:
        state["posted_ids"] = posted[-keep_last:]


def mk_uid(*parts: str) -> str:
    source = "|".join(parts)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def clean_text(value: str, limit: int = 350) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def simplify_title(title: str) -> str:
    """Make titles easier to read: remove noisy tails like '| Site' / '— Site'."""
    text = clean_text(title, MAX_TITLE_LENGTH)
    for sep in (" | ", " — ", " - "):
        parts = text.split(sep)
        if len(parts) >= 2 and len(parts[-1]) <= 24:
            text = sep.join(parts[:-1]).strip()
            break
    return clean_text(text, MAX_TITLE_LENGTH)


def simplify_summary(summary: str) -> str:
    """Return a short, readable summary (1-2 concise sentences)."""
    text = clean_text(summary or "", MAX_SUMMARY_LENGTH)
    if not text:
        return "Коротко: откройте материал по ссылке ниже."

    # Keep only first 1-2 sentences for readability
    chunks: List[str] = []
    for sentence in text.replace("!", ".").replace("?", ".").split("."):
        sentence = sentence.strip(" ,;:-")
        if not sentence:
            continue
        chunks.append(sentence)
        if len(chunks) >= 2:
            break
    short = ". ".join(chunks).strip()
    if short and not short.endswith("."):
        short += "."
    return clean_text(short or text, MAX_SUMMARY_LENGTH)


def extract_tags(item: PostItem, max_tags: int = 3) -> List[str]:
    """Build small readable hashtag list from title/summary keywords."""
    haystack = f"{item.title} {item.summary}".lower()
    mapping = [
        ("python", "#Python"),
        ("javascript", "#JavaScript"),
        ("java", "#Java"),
        ("golang", "#Go"),
        ("go ", "#Go"),
        ("ai", "#AI"),
        ("ии", "#ИИ"),
        ("ml", "#ML"),
        ("devops", "#DevOps"),
        ("backend", "#Backend"),
        ("frontend", "#Frontend"),
        ("linux", "#Linux"),
        ("security", "#Security"),
        ("кибербезопас", "#Кибербезопасность"),
        ("cloud", "#Cloud"),
        ("данные", "#Data"),
        ("data", "#Data"),
    ]
    tags: List[str] = []
    for needle, tag in mapping:
        if needle in haystack and tag not in tags:
            tags.append(tag)
        if len(tags) >= max_tags:
            break
    if not tags:
        tags = ["#IT", "#Новости"]
    return tags[:max_tags]


def build_cta_block() -> str:
    if not FOLLOW_CTA_ENABLED:
        return ""

    lines: List[str] = ["📌 <b>Поддержи канал:</b>"]
    if SUBSCRIBE_CTA_TEXT:
        lines.append(html.escape(SUBSCRIBE_CTA_TEXT))
    if CHANNEL_HANDLE:
        lines.append(f"👉 Канал: @{html.escape(CHANNEL_HANDLE)}")
    if AUTHOR_HANDLE:
        lines.append(f"👤 Автор: @{html.escape(AUTHOR_HANDLE)}")
    return "\n".join(lines)


def parse_rss(source: Dict[str, Any]) -> List[PostItem]:
    url = source["url"]
    name = source.get("name", url)
    limit = int(source.get("limit", 10))

    parsed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
    items: List[PostItem] = []

    for entry in parsed.entries[:limit]:
        title = clean_text(entry.get("title", "Без названия"), MAX_TITLE_LENGTH)
        link = entry.get("link", "").strip()
        summary = clean_text(
            BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" "),
            MAX_SUMMARY_LENGTH,
        )
        raw_id = str(entry.get("id") or link or title)
        ts = datetime.now(tz=timezone.utc).timestamp()
        if getattr(entry, "published_parsed", None):
            ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).timestamp()

        items.append(
            PostItem(
                uid=mk_uid(name, raw_id),
                title=title,
                link=link,
                summary=summary,
                source_name=name,
                timestamp=ts,
            )
        )
    return items


def parse_website(source: Dict[str, Any]) -> List[PostItem]:
    url = source["url"]
    name = source.get("name", url)
    limit = int(source.get("limit", 10))
    item_selector = source.get("item_selector", "article, .post, .news-item, li")
    link_selector = source.get("link_selector", "a[href]")
    title_selector = source.get("title_selector")
    summary_selector = source.get("summary_selector")

    resp = requests.get(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    blocks = soup.select(item_selector)
    if not blocks:
        blocks = soup.select("a[href]")

    items: List[PostItem] = []
    for block in blocks:
        link_el = block.select_one(link_selector) if hasattr(block, "select_one") else block
        if not link_el:
            continue

        href = link_el.get("href", "").strip()
        if not href:
            continue
        full_link = urljoin(url, href)

        if title_selector:
            title_el = block.select_one(title_selector)
            title = title_el.get_text(" ", strip=True) if title_el else link_el.get_text(" ", strip=True)
        else:
            title = link_el.get_text(" ", strip=True)
        title = clean_text(title or "Без названия", MAX_TITLE_LENGTH)

        summary = ""
        if summary_selector:
            summary_el = block.select_one(summary_selector)
            if summary_el:
                summary = clean_text(summary_el.get_text(" ", strip=True), MAX_SUMMARY_LENGTH)

        items.append(
            PostItem(
                uid=mk_uid(name, full_link),
                title=title,
                link=full_link,
                summary=summary,
                source_name=name,
                timestamp=datetime.now(tz=timezone.utc).timestamp(),
            )
        )

        if len(items) >= limit:
            break

    return items


def parse_iso8601_duration_to_seconds(value: str) -> int:
    # Examples: PT15M, PT9M30S, PT1H2M10S
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", (value or "").strip())
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    sec = int(m.group(3) or 0)
    return h * 3600 + mins * 60 + sec


def get_ytdlp_base_cmd() -> List[str]:
    # Prefer explicit path/binary; fallback to python module if installed in current env.
    if YTDLP_PATH and YTDLP_PATH != "yt-dlp":
        return [YTDLP_PATH]
    return ["python", "-m", "yt_dlp"]


def parse_youtube_api(source: Dict[str, Any]) -> List[PostItem]:
    if not YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY is missing; youtube_api source skipped")
        return []

    query = source.get("query", "русский язык 2 класс имя прилагательное").strip()
    name = source.get("name", "YouTube")
    limit = int(source.get("limit", 10))
    order = source.get("order", "date")

    search_url = "https://www.googleapis.com/youtube/v3/search"
    videos_url = "https://www.googleapis.com/youtube/v3/videos"

    search_resp = requests.get(
        search_url,
        params={
            "key": YOUTUBE_API_KEY,
            "part": "snippet",
            "type": "video",
            "q": query,
            "maxResults": min(50, max(1, limit)),
            "order": order,
            "relevanceLanguage": "ru",
            "safeSearch": "strict",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    search_resp.raise_for_status()
    search_data = search_resp.json()

    video_ids: List[str] = []
    for entry in search_data.get("items", []):
        vid = (entry.get("id") or {}).get("videoId")
        if vid:
            video_ids.append(vid)

    if not video_ids:
        return []

    details_resp = requests.get(
        videos_url,
        params={
            "key": YOUTUBE_API_KEY,
            "part": "snippet,contentDetails",
            "id": ",".join(video_ids),
            "maxResults": len(video_ids),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    details_resp.raise_for_status()
    details_data = details_resp.json()

    items: List[PostItem] = []
    for v in details_data.get("items", []):
        vid = v.get("id", "").strip()
        if not vid:
            continue

        snippet = v.get("snippet") or {}
        title = clean_text(snippet.get("title", "Без названия"), MAX_TITLE_LENGTH)
        description = clean_text(snippet.get("description", ""), MAX_SUMMARY_LENGTH)
        published_at = snippet.get("publishedAt", "")
        duration_iso = ((v.get("contentDetails") or {}).get("duration") or "").strip()
        duration_seconds = parse_iso8601_duration_to_seconds(duration_iso)

        if duration_seconds <= 0 or duration_seconds > YOUTUBE_MAX_DURATION_SECONDS:
            continue

        haystack = f"{title} {description}".lower()
        if VIDEO_REQUIRED_PHRASES and not all(p in haystack for p in VIDEO_REQUIRED_PHRASES):
            continue

        link = f"https://www.youtube.com/watch?v={vid}"
        mins = duration_seconds // 60
        sec = duration_seconds % 60
        summary = f"Видео по теме «имя прилагательное» для 2 класса. Длительность: {mins}:{sec:02d}."

        ts = datetime.now(tz=timezone.utc).timestamp()
        try:
            if published_at:
                ts = datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass

        items.append(
            PostItem(
                uid=mk_uid(name, vid),
                title=title,
                link=link,
                summary=summary,
                source_name=name,
                timestamp=ts,
            )
        )

    return items[:limit]


def parse_youtube_search(source: Dict[str, Any]) -> List[PostItem]:
    """Collect YouTube videos via yt-dlp search (no YouTube API key required)."""
    query = source.get("query", "русский язык 2 класс имя прилагательное").strip()
    name = source.get("name", "YouTube Search")
    limit = int(source.get("limit", 10))

    cmd = get_ytdlp_base_cmd() + [
        "--dump-json",
        f"ytsearch{max(1, min(50, limit))}:{query}",
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            logger.warning("yt-dlp search failed (%s): %s", proc.returncode, (proc.stderr or proc.stdout)[-500:])
            return []
    except Exception as e:
        logger.warning("yt-dlp search error: %s", e)
        return []

    items: List[PostItem] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        vid = str(entry.get("id") or "").strip()
        if not vid:
            continue
        title = clean_text(str(entry.get("title") or "Без названия"), MAX_TITLE_LENGTH)
        description = clean_text(str(entry.get("description") or ""), MAX_SUMMARY_LENGTH)
        duration_seconds = int(entry.get("duration") or 0)
        link = str(entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}").strip()

        if duration_seconds <= 0 or duration_seconds > YOUTUBE_MAX_DURATION_SECONDS:
            continue

        haystack = f"{title} {description}".lower()
        if VIDEO_REQUIRED_PHRASES and not all(p in haystack for p in VIDEO_REQUIRED_PHRASES):
            continue

        mins = duration_seconds // 60
        sec = duration_seconds % 60
        summary = f"Видео по теме «имя прилагательное» для 2 класса. Длительность: {mins}:{sec:02d}."

        ts = datetime.now(tz=timezone.utc).timestamp()
        up = str(entry.get("upload_date") or "").strip()
        try:
            if len(up) == 8 and up.isdigit():
                ts = datetime(int(up[0:4]), int(up[4:6]), int(up[6:8]), tzinfo=timezone.utc).timestamp()
        except Exception:
            pass

        items.append(
            PostItem(
                uid=mk_uid(name, vid),
                title=title,
                link=link,
                summary=summary,
                source_name=name,
                timestamp=ts,
            )
        )

    return sorted(items, key=lambda x: x.timestamp, reverse=True)[:limit]


def collect_items(sources: List[Dict[str, Any]]) -> List[PostItem]:
    all_items: List[PostItem] = []
    for source in sources:
        try:
            source_type = source.get("type", "rss").lower().strip()
            if source_type == "rss":
                all_items.extend(parse_rss(source))
            elif source_type == "website":
                all_items.extend(parse_website(source))
            elif source_type == "youtube_api":
                all_items.extend(parse_youtube_api(source))
            elif source_type == "youtube_search":
                all_items.extend(parse_youtube_search(source))
            else:
                logger.warning("Unknown source type '%s' in %s", source_type, source)
        except Exception as e:
            logger.exception("Source parse failed (%s): %s", source.get("name", source.get("url")), e)

    unique: Dict[str, PostItem] = {}
    for item in all_items:
        unique[item.uid] = item

    return sorted(unique.values(), key=lambda x: x.timestamp, reverse=True)


def format_message(item: PostItem) -> str:
    safe_title = html.escape(simplify_title(item.title))
    safe_summary = html.escape(simplify_summary(item.summary))
    safe_source = html.escape(item.source_name)
    tags = " ".join(extract_tags(item))

    parts = [
        f"📰 <b>{safe_title}</b>",
        "",
        f"🧠 <b>Коротко:</b> {safe_summary}",
        f"🔎 <b>Источник:</b> {safe_source}",
    ]

    if item.link:
        link_value = encrypt_link(item.link)
        if link_value:
            if LINK_ENCRYPTION_ENABLED:
                parts.append(f"🔗 <b>Ссылка (зашифрована):</b> <code>{html.escape(link_value)}</code>")
            else:
                parts.append(f"🔗 <b>Ссылка:</b> {html.escape(link_value)}")

    if tags:
        parts.extend(["", tags])

    cta = build_cta_block()
    if cta:
        parts.extend(["", cta])

    return "\n".join(parts)


def try_download_youtube_video(item: PostItem) -> Optional[str]:
    """Download YouTube video to a temp mp4 (for Telegram send_video).
    Returns local file path or None.
    """
    if not YTDLP_ENABLED:
        return None
    if "youtube.com/watch" not in (item.link or "") and "youtu.be/" not in (item.link or ""):
        return None

    tmp_dir = tempfile.mkdtemp(prefix="tg_autopost_")
    out_tmpl = os.path.join(tmp_dir, "%(id)s.%(ext)s")
    cmd = get_ytdlp_base_cmd() + [
        "--no-playlist",
        "-f",
        "mp4[height<=480]/best[ext=mp4]/best",
        "--max-filesize",
        "49M",
        "-o",
        out_tmpl,
        item.link,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            logger.warning("yt-dlp failed (%s): %s", proc.returncode, (proc.stderr or proc.stdout)[-500:])
            return None

        for name in os.listdir(tmp_dir):
            path = os.path.join(tmp_dir, name)
            if os.path.isfile(path) and name.lower().endswith((".mp4", ".mkv", ".webm")):
                return path
    except Exception as e:
        logger.warning("yt-dlp download failed: %s", e)

    return None


def encrypt_link(link: str) -> str:
    if not link:
        return ""
    if not LINK_ENCRYPTION_ENABLED:
        return link

    data = link.encode("utf-8")
    key = (LINK_ENCRYPTION_KEY or "change_me_secret_key").encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    token = base64.urlsafe_b64encode(xored).decode("ascii")
    return f"enc:{token}"


def is_relevant_item(item: PostItem) -> bool:
    if not IT_ONLY:
        return True
    haystack = f"{item.title} {item.summary}".lower()
    return any(keyword in haystack for keyword in TOPIC_KEYWORDS)


def should_try_ai_image() -> bool:
    if not ENABLE_AI_IMAGE_POSTS:
        return False
    key = NANO_BANANA_API_KEY.strip()
    if (not key) or ("your_api_key" in key.lower()) or ("put_key" in key.lower()):
        return False
    chance = max(0.0, min(1.0, AI_IMAGE_POST_CHANCE))
    return random.random() < chance


def build_ai_image_prompt(item: PostItem) -> str:
    return (
        "Create a single square image for a Telegram IT channel post. "
        "Theme: information technology and channel logo concept. "
        f"Title context: {simplify_title(item.title)}. "
        f"Short context: {simplify_summary(item.summary)}. "
        f"Style requirements: {AI_IMAGE_STYLE_PROMPT}."
    )


def generate_ai_image_bytes(item: PostItem) -> Optional[bytes]:
    prompt = build_ai_image_prompt(item)
    headers = {
        "Authorization": f"Bearer {NANO_BANANA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": NANO_BANANA_MODEL,
        "prompt": prompt,
        "size": AI_IMAGE_SIZE,
    }

    try:
        resp = requests.post(
            NANO_BANANA_API_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS + 20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("AI image generation request failed: %s", e)
        return None

    b64 = None
    if isinstance(data, dict):
        b64 = data.get("image_base64") or data.get("b64_json")
        if not b64 and isinstance(data.get("data"), list) and data["data"]:
            first = data["data"][0]
            if isinstance(first, dict):
                b64 = first.get("b64_json") or first.get("image_base64")
                image_url = first.get("url")
                if image_url:
                    try:
                        r = requests.get(image_url, timeout=REQUEST_TIMEOUT_SECONDS + 10)
                        r.raise_for_status()
                        return r.content
                    except Exception as e:
                        logger.warning("AI image URL download failed: %s", e)
        if not b64 and isinstance(data.get("output"), dict):
            b64 = data["output"].get("image_base64")

    if not b64:
        logger.warning("AI image response has no image payload")
        return None

    try:
        return base64.b64decode(b64)
    except Exception as e:
        logger.warning("AI image base64 decode failed: %s", e)
        return None


async def post_cycle(application: Application, manual_reply_chat_id: Optional[int] = None) -> int:
    sources = load_sources()
    state = load_state()
    posted_ids = set(state.get("posted_ids", []))

    items = [i for i in collect_items(sources) if is_relevant_item(i)]
    sent = 0

    for item in items:
        if item.uid in posted_ids:
            continue
        if sent >= MAX_POSTS_PER_CYCLE:
            break

        msg = format_message(item)
        delivered = False
        for attempt in range(1, 4):
            try:
                video_sent = False

                video_path = try_download_youtube_video(item)
                if video_path and os.path.exists(video_path):
                    try:
                        with open(video_path, "rb") as vf:
                            await application.bot.send_video(
                                chat_id=TARGET_CHAT_ID,
                                video=vf,
                                caption=msg,
                                parse_mode=ParseMode.HTML,
                                supports_streaming=True,
                            )
                        video_sent = True
                        logger.info("Posted video item: %s", item.title)
                    finally:
                        try:
                            os.remove(video_path)
                        except Exception:
                            pass
                image_sent = False
                if (not video_sent) and should_try_ai_image():
                    image_bytes = generate_ai_image_bytes(item)
                    if image_bytes:
                        await application.bot.send_photo(
                            chat_id=TARGET_CHAT_ID,
                            photo=io.BytesIO(image_bytes),
                            caption=msg,
                            parse_mode=ParseMode.HTML,
                        )
                        image_sent = True
                        logger.info("Posted item with AI image: %s", item.title)

                # For YouTube items we must publish a video, not text-only fallback.
                if ("youtube.com/watch" in (item.link or "") or "youtu.be/" in (item.link or "")) and (not video_sent):
                    raise TelegramError("Video is required for YouTube item, but video upload failed")

                if (not video_sent) and (not image_sent):
                    await application.bot.send_message(
                        chat_id=TARGET_CHAT_ID,
                        text=msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                delivered = True
                break
            except TelegramError as e:
                logger.warning("send_message failed (attempt %s/3): %s", attempt, e)
                await asyncio.sleep(1.2 * attempt)

        if not delivered:
            logger.error("Skipping item after 3 failed attempts: %s", item.title)
            continue
        sent += 1
        posted_ids.add(item.uid)
        state["posted_ids"].append(item.uid)

        await asyncio.sleep(0.4)

    compact_posted_ids(state)
    save_json(STATE_FILE, state)

    if manual_reply_chat_id is not None:
        await application.bot.send_message(
            chat_id=manual_reply_chat_id,
            text=f"Проверка завершена. Опубликовано: {sent}",
        )
    return sent


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Бот автопостинга запущен.\n"
        "Команды:\n"
        "/check — запустить проверку сейчас\n"
        "/status — показать настройки"
    )


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Запускаю ручную проверку источников...")
    await post_cycle(context.application, manual_reply_chat_id=update.effective_chat.id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sources = load_sources()
    ai_key_ok = bool(NANO_BANANA_API_KEY and "your_api_key" not in NANO_BANANA_API_KEY.lower())
    await update.message.reply_text(
        "Статус:\n"
        f"- Источников: {len(sources)}\n"
        f"- Интервал: {CHECK_INTERVAL_MINUTES} мин\n"
        f"- Макс. постов за цикл: {MAX_POSTS_PER_CYCLE}\n"
        f"- Целевой чат: {TARGET_CHAT_ID}\n"
        f"- AI-фото: {'ON' if ENABLE_AI_IMAGE_POSTS else 'OFF'} (шанс {int(max(0,min(1,AI_IMAGE_POST_CHANCE))*100)}%)\n"
        f"- Nano Banana API key: {'OK' if ai_key_ok else 'MISSING'}\n"
        f"- Шифрование ссылок: {'ON' if LINK_ENCRYPTION_ENABLED else 'OFF'}"
    )


async def scheduled_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    sent = await post_cycle(context.application)
    if sent:
        logger.info("Scheduled cycle sent %s posts", sent)
    else:
        logger.info("Scheduled cycle: no new posts")


def main() -> None:
    if not os.path.exists(SOURCES_FILE):
        raise RuntimeError(
            f"File not found: {SOURCES_FILE}. Copy sources.example.json -> {SOURCES_FILE} and edit sources."
        )

    if ENABLE_AI_IMAGE_POSTS and (not NANO_BANANA_API_KEY or "your_api_key" in NANO_BANANA_API_KEY.lower()):
        logger.warning("AI image posting is enabled, but NANO_BANANA_API_KEY is missing. Bot will post text-only fallback.")

    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()

            if not DISABLE_UPDATES_POLLING:
                app.add_handler(CommandHandler("start", cmd_start))
                app.add_handler(CommandHandler("check", cmd_check))
                app.add_handler(CommandHandler("status", cmd_status))

            app.job_queue.run_repeating(
                scheduled_job,
                interval=CHECK_INTERVAL_MINUTES * 60,
                first=2,
            )

            logger.info("Bot started. Interval=%s min, target=%s", CHECK_INTERVAL_MINUTES, TARGET_CHAT_ID)
            # Python 3.14+: make sure there is an active event loop in main thread
            asyncio.set_event_loop(asyncio.new_event_loop())
            if DISABLE_UPDATES_POLLING:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(app.initialize())
                loop.run_until_complete(app.start())
                logger.info("Running in scheduler-only mode (updates polling disabled)")
                loop.run_until_complete(asyncio.Event().wait())
            else:
                app.run_polling(close_loop=False)
            break
        except TelegramError as e:
            logger.exception("Telegram startup/runtime error, restart in 15s: %s", e)
            import time
            time.sleep(15)
        except Exception as e:
            logger.exception("Unexpected bot error, restart in 15s: %s", e)
            import time
            time.sleep(15)


if __name__ == "__main__":
    main()
