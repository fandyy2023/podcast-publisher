"""Podcast RSS generator CLI.

Usage examples:

    python publisher.py --log-level INFO           # обычная генерация RSS
    python publisher.py --dry-run                  # вывести RSS в stdout
    python publisher.py --force                    # принудительное транскодирование WAV → MP3
    python publisher.py --check-domain             # убедиться, что feed_url доступен (200 OK)

"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

import requests
from feedgen.feed import FeedGenerator

from utils import load_env, generate_guid, transcode_wav_to_mp3, send_email

BASE_DIR = Path(__file__).resolve().parent
EPISODES_DIR = BASE_DIR / "episodes"
CONFIG_FILE = BASE_DIR / "feedgen_config.json"

logger = logging.getLogger(__name__)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or update podcast RSS feed.")
    parser.add_argument("--force", action="store_true", help="Transcode audio to MP3 (192 kbps)")
    parser.add_argument("--dry-run", action="store_true", help="Print RSS to stdout without writing feed.xml")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("--check-domain", action="store_true", help="Verify that feed_url is reachable (HTTP 200)")
    parser.add_argument("--enable-analytics", action="store_true", help="(Reserved) Enable Spotipy analytics integration")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        logger.error("Config file %s not found. Create it based on feedgen_config.example.json", CONFIG_FILE)
        sys.exit(1)
    with CONFIG_FILE.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def verify_domain(feed_url: str) -> None:
    try:
        resp = requests.get(feed_url, timeout=5)
        if resp.status_code == 200:
            logger.info("Domain check passed: %s", feed_url)
        else:
            logger.warning("Domain check failed (%s): HTTP %s", feed_url, resp.status_code)
    except requests.RequestException as exc:
        logger.error("Domain check error: %s", exc)


def scan_episodes(force_transcode: bool, feed_url_base: str, options: dict) -> List[dict]:
    """Return sorted list of episode dicts ready for feedgen."""
    episodes: List[dict] = []
    if not EPISODES_DIR.exists():
        logger.warning("Episodes directory %s does not exist", EPISODES_DIR)
        return episodes

    AUDIO_EXTS = ('.mp3', '.wav', '.aac', '.m4a', '.ogg', '.oga', '.flac', '.opus')
    IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    MIME_MAP = {
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.aac': 'audio/aac',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg',
        '.oga': 'audio/ogg',
        '.flac': 'audio/flac',
        '.opus': 'audio/opus',
    }

    for ep_dir in sorted(EPISODES_DIR.iterdir()):
        if not ep_dir.is_dir():
            continue
        meta_file = ep_dir / "metadata.json"
        if not meta_file.exists():
            logger.warning("metadata.json missing in %s", ep_dir)
            continue
        with meta_file.open("r", encoding="utf-8") as fp:
            meta = json.load(fp)

        # Найти первый аудиофайл любого поддерживаемого формата
        audio_path = None
        for ext in AUDIO_EXTS:
            found = list(ep_dir.glob(f'*{ext}'))
            if found:
                audio_path = found[0]
                break
        if not audio_path:
            logger.warning("Audio file not found in %s", ep_dir)
            continue

        # MIME-тип для enclosure
        enclosure_type = MIME_MAP.get(audio_path.suffix.lower(), 'audio/mpeg')

        # Транскодирование если нужно
        if force_transcode and audio_path.suffix.lower() != '.mp3':
            audio_path = transcode_wav_to_mp3(audio_path)
            enclosure_type = 'audio/mpeg'

        file_size = audio_path.stat().st_size
        ep_id = ep_dir.name
        media_url = f"{feed_url_base}/episodes/{ep_id}/{audio_path.name}"

        # Найти первую картинку любого формата
        episode_image = None
        for ext in IMAGE_EXTS:
            found = list(ep_dir.glob(f'*{ext}'))
            if found:
                episode_image = found[0].name
                break

        meta.update({
            "id": ep_id,
            "audio_path": audio_path,
            "file_size": file_size,
            "media_url": media_url,
            "enclosure_type": enclosure_type,
            "episode_image": episode_image,
        })
        episodes.append(meta)

    # Sort by pubDate descending
    episodes.sort(key=lambda x: x.get("pubDate", ""), reverse=True)
    return episodes


def generate_rss(config: dict, episodes: List[dict]) -> str:
    fg = FeedGenerator()
    fg.load_extension("podcast")  # feedgen >=0.9.0

    show_cfg = config["show"]
    rss_cfg = config["rss"]

    fg.id(show_cfg["link"])
    fg.title(show_cfg["title"])
    fg.description(show_cfg["description"])
    fg.link(href=show_cfg["link"], rel="alternate")
    fg.language(show_cfg.get("language", "en-US"))
    # Handle categories (Feedgen requires individual calls)
    categories = show_cfg.get("category", [])
    if isinstance(categories, list):
        for item in categories:
            if isinstance(item, str):
                fg.podcast.itunes_category(item)
            elif isinstance(item, (list, tuple)) and item:
                fg.podcast.itunes_category(item[0], item[1] if len(item) > 1 else None)
    elif isinstance(categories, str):
        fg.podcast.itunes_category(categories)
    # Добавить <itunes:image> и <image> для фида (cover art)
    if show_cfg.get("image"):
        fg.podcast.itunes_image(show_cfg["image"])
        fg.image(show_cfg["image"])
    fg.podcast.itunes_explicit(show_cfg.get("explicit", "no"))
    fg.podcast.itunes_type(show_cfg.get("type", "episodic"))
    if show_cfg.get("summary"):
        fg.podcast.itunes_summary(show_cfg["summary"])

    fg.link(href=rss_cfg["feed_url"], rel="self")
    fg.ttl(rss_cfg.get("ttl", 60))

    for ep in episodes:
        fe = fg.add_entry()
        fe.id(ep.get("guid") or generate_guid())
        fe.title(ep["title"])
        fe.description(ep["description"])
        fe.pubDate(datetime.fromisoformat(ep["pubDate"]))
        fe.enclosure(ep["media_url"], str(ep["file_size"]), ep["enclosure_type"])
        fe.podcast.itunes_duration(ep.get("duration"))
        cover_rel = ep.get("episode_image")
        if cover_rel:
            fe.podcast.itunes_image(f"{rss_cfg['feed_url'].rsplit('/', 1)[0]}/episodes/{ep['id']}/{cover_rel}")
        if ep.get("duration"):
            fe.podcast.itunes_duration(ep["duration"])
        fe.podcast.itunes_explicit("no")

    return fg.rss_str(pretty=True).decode("utf-8")


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    load_env()
    config = load_config()

    if args.check_domain:
        verify_domain(config["rss"]["feed_url"])

    episodes = scan_episodes(
        force_transcode=args.force or config["options"].get("force_transcode", False),
        feed_url_base=config["rss"]["feed_url"].rsplit("/", 1)[0],
        options=config["options"],
    )

    rss_content = generate_rss(config, episodes)

    if args.dry_run:
        print(rss_content)
        return

    feed_path = BASE_DIR / "feed.xml"
    feed_path.write_text(rss_content, encoding="utf-8")
    logger.info("RSS written to %s", feed_path)
    send_email("Podcast RSS updated", f"Feed updated with {len(episodes)} episodes at {datetime.utcnow()}")


if __name__ == "__main__":
    main()
