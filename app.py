"""Flask server to serve RSS feed and media files for the podcast.

Run with:
    python app.py

By default listens on port 5000.
"""
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, send_file, abort, session
import logging
import json
import math
import shutil
import threading
import subprocess
import hashlib
import time
import traceback
from utils import (
    transcode_audio_to_mp3,
    get_audio_info,
    sanitize_html_for_rss,
    plain_text_to_html,
    html_to_plain_text,
    select_mp3_bitrate,
    MIN_PODCAST_BITRATE,
    resize_cover_image,
    has_id3v2_tags,
    embed_id3_metadata_mp3,
)

# Initialize Flask app
app = Flask(__name__, static_folder="static", template_folder="templates")

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
import logging
from logging.handlers import RotatingFileHandler
import os

# Создаем папку для логов, если она не существует
if not os.path.exists('logs'):
    os.mkdir('logs')

# Настраиваем файловый обработчик
file_handler = RotatingFileHandler('logs/podcast_app.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
file_handler.setLevel(logging.INFO)

# Добавляем обработчик к логгеру Flask
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('--- Podcast Publisher App Started ---')
# --- КОНЕЦ НАСТРОЙКИ ЛОГИРОВАНИЯ ---
app.secret_key = 'changeme'  # для flash-сообщений

app.logger.setLevel(logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
SHOWS_DIR = BASE_DIR / "shows"
ASSETS_DIR = BASE_DIR / "assets"
# Settings file path
SETTINGS_FILE = BASE_DIR / "settings.json"

# Directory to store temporary large-file uploads received in chunks
UPLOADS_DIR = BASE_DIR / "tmp_uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# Chunk size - 10MB is below Cloudflare limit (100MB)
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB in bytes

# How long to keep temp uploads (in seconds)
TEMP_UPLOAD_TTL = 24 * 60 * 60  # 24 hours

@app.route("/feed.xml")
def feed():
    """Serve the generated RSS feed."""
    feed_path = BASE_DIR / "feed.xml"
    if not feed_path.exists():
        abort(404, "feed.xml not found. Run publisher.py first.")
    return send_from_directory(str(BASE_DIR), "feed.xml", mimetype="application/rss+xml")


@app.route("/shows/<show_id>/episodes/<ep_id>/<path:filename>")
def episode_file(show_id: str, ep_id: str, filename: str):
    """Serve episode media files with correct mime type."""
    ep_dir = SHOWS_DIR / show_id / "episodes" / ep_id
    # Определяем mime-type по расширению
    mimetype = None
    if filename.lower().endswith('.mp3'):
        mimetype = 'audio/mpeg'
    response = send_from_directory(ep_dir, filename, mimetype=mimetype, conditional=True)
    response.headers.setdefault("Accept-Ranges", "bytes")
    return response


@app.route("/shows/<show_id>/episodes/<ep_id>/browse")
def browse_episode_files(show_id: str, ep_id: str):
    """Simple directory listing for episode files."""
    ep_dir = SHOWS_DIR / show_id / "episodes" / ep_id
    if not ep_dir.exists():
        abort(404, "Episode directory not found")

    file_links = []
    for file in sorted(ep_dir.iterdir()):
        if file.is_file():
            link = url_for('episode_file', show_id=show_id, ep_id=ep_id, filename=file.name)
            file_links.append((file.name, link))

    # Build minimal HTML response
    html_parts = [
        f"<h2>Files for episode {ep_id}</h2>",
        "<ul style='font-family:Segoe UI,Arial,sans-serif;'>"
    ]
    for name, link in file_links:
        html_parts.append(f"<li><a href='{link}' target='_blank' rel='noopener noreferrer'>{name}</a></li>")
    html_parts.append("</ul>")
    return "\n".join(html_parts)


@app.route("/assets/<path:filename>")
def assets(filename: str):
    """Serve static assets such as show cover."""
    return send_from_directory(ASSETS_DIR, filename)

# Settings routes are defined at the end of the file

@app.route("/")
def index():
    shows = []
    languages = set()
    for show_dir in sorted((SHOWS_DIR).iterdir()):
        if not show_dir.is_dir():
            continue
        config_path = show_dir / "config.json"
        if not config_path.exists():
            continue
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Найти первую картинку любого поддерживаемого формата с любым именем
        cover = None
        image_exts = ("png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "ico")
        for file in show_dir.iterdir():
            if file.is_file() and not file.name.startswith('.') and file.suffix.lower().lstrip('.') in image_exts:
                cover = f"/shows/{show_dir.name}/{file.name}"
                break
        if not cover:
            cover = "/assets/default_cover.png"
        lang = cfg.get("language", "")
        if lang:
            languages.add(lang)
        shows.append({
            "id": show_dir.name,
            "title": cfg.get("title", show_dir.name),
            "description": cfg.get("description", ""),
            "image": cover,
            "language": lang
        })
    return render_template("show_list.html", shows=shows, languages=sorted(languages))

@app.route("/shows/new", methods=["GET", "POST"])
def new_show():
    msg = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        desc = request.form.get("description", "").strip()
        lang = request.form.get("language", "en-US").strip()
        cat = request.form.get("category", "").strip()
        image = request.files.get("image")
        show_id = title.lower().replace(" ", "_")
        show_dir = SHOWS_DIR / show_id
        if show_dir.exists():
            msg = "Шоу с таким названием уже существует."
        else:
            show_dir.mkdir(parents=True)
            (show_dir / "episodes").mkdir()
            config = {
                "title": title,
                "description": desc,
                "language": lang or "en-US",
                "category": cat,
                "category_main": request.form.get("category_main", ""),
                "category_sub": request.form.get("category_sub", ""),
                "summary": request.form.get("summary", ""),
                "subtitle": request.form.get("subtitle", ""),
                "copyright": request.form.get("copyright", " 2025 Algar Pool") or " 2025 Algar Pool",
                "ttl": int(request.form.get("ttl", 60) or 60),
                "author": request.form.get("author", "Algar Pool") or "Algar Pool",
                "owner_name": request.form.get("owner_name", "Algar Pool") or "Algar Pool",
                "owner_email": request.form.get("owner_email", ""),
                "explicit": request.form.get("explicit", "no") or "no",
                "type": request.form.get("type", "episodic") or "episodic",
                "image": "cover.png" if image else "",
            }
            with (show_dir / "config.json").open("w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            if image:
                ext = image.filename.split('.')[-1].lower()
                image.save(str(show_dir / f"cover.{ext}"))
            flash("Шоу успешно создано! Теперь добавьте эпизоды.", "success")
            return redirect(url_for("show_page", show_id=show_id))
    # Подстраховка для шаблона new_show.html (чтобы всегда были поля)
    show_stub = {"category_main": "", "category_sub": ""}
    return render_template("new_show.html", msg=msg, show=show_stub)

@app.route("/shows/<show_id>/inline-edit", methods=["PATCH"])
def inline_edit_show(show_id):
    show_dir = SHOWS_DIR / show_id
    config_path = show_dir / "config.json"
    if not config_path.exists():
        return jsonify({"error": "Show not found"}), 404
    data = request.get_json(force=True)
    allowed = {"title", "description"}
    updated = None
    with config_path.open("r+", encoding="utf-8") as f:
        cfg = json.load(f)
        for field in allowed:
            if field in data:
                value = str(data[field]).strip()
                if field == "title" and len(value) > 75:
                    return jsonify({"error": "Title too long (максимум 75 символов)"}), 400
                if field == "description" and len(value) > 600:
                    return jsonify({"error": "Description too long (максимум 600 символов)"}), 400
                if field == "description":
                    value = sanitize_html_for_rss(plain_text_to_html(value.strip()))
                cfg[field] = value
                updated = field
        if not updated:
            return jsonify({"error": "No valid field"}), 400
        f.seek(0)
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.truncate()
    return jsonify({updated: cfg[updated]})

@app.route("/shows/<show_id>/episodes/<ep_id>/inline-edit", methods=["PATCH"])
def inline_edit_episode(show_id, ep_id):
    ep_dir = SHOWS_DIR / show_id / "episodes" / ep_id
    meta_path = ep_dir / "metadata.json"
    if not meta_path.exists():
        return jsonify({"error": "Episode not found"}), 404
    data = request.get_json(force=True)
    allowed = {"title", "description"}
    updated = None
    with meta_path.open("r+", encoding="utf-8") as f:
        meta = json.load(f)
        for field in allowed:
            if field in data:
                value = str(data[field]).strip()
                if field == "title" and len(value) > 120:
                    return jsonify({"error": "Title too long (максимум 120 символов)"}), 400
                if field == "description" and len(value) > 4000:
                    return jsonify({"error": "Description too long (максимум 4000 символов)"}), 400
                if field == "description":
                    value = sanitize_html_for_rss(plain_text_to_html(value.strip()))
                meta[field] = value
                updated = field
        if not updated:
            return jsonify({"error": "No valid field"}), 400
        f.seek(0)
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.truncate()
    return jsonify({updated: meta[updated]})

@app.route("/shows/<show_id>/", methods=["GET", "POST"])
def show_page(show_id):
    show_dir = SHOWS_DIR / show_id
    config_path = show_dir / "config.json"
    if not config_path.exists():
        abort(404)

    if request.method == "POST":
        # This part handles the form submission for updating show metadata
        fields = ["title", "author", "owner_email", "owner_name", "explicit", "summary", "description", "category", "category_main", "category_sub", "type", "copyright", "subtitle", "ttl"]
        with config_path.open("r+", encoding="utf-8") as f:
            cfg = json.load(f)
            for field in fields:
                if field in request.form:
                    val = request.form.get(field, "")
                    if field in ("description", "summary"):
                        from utils import sanitize_html_for_rss, plain_text_to_html
                        val = sanitize_html_for_rss(plain_text_to_html(val))
                    cfg[field] = val
            if "language" in request.form:
                cfg["language"] = request.form.get("language", "en-US").strip()
            if "ttl" in request.form and request.form.get("ttl"):
                try:
                    cfg["ttl"] = int(request.form.get("ttl"))
                except (ValueError, TypeError):
                    cfg["ttl"] = 60 # Default value
            f.seek(0)
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.truncate()
        flash("Метаданные RSS успешно сохранены!", "success")
        return redirect(url_for("show_page", show_id=show_id))

    # This part handles displaying the page
    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Сначала найдем обложку шоу, она может понадобиться для эпизодов
    cover_image_url = None
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico")
    for file in show_dir.iterdir():
        if file.is_file() and file.suffix.lower() in image_exts:
            mtime = int(file.stat().st_mtime)
            cover_image_url = url_for('show_file', show_id=show_id, filename=file.name, file_type='cover') + f'?v={mtime}'
            break
    if not cover_image_url:
        cover_image_url = '/assets/default_cover.png'

    episodes_dir = show_dir / "episodes"
    episodes = []
    if episodes_dir.exists():
        for ep_dir in sorted(episodes_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not (ep_dir.is_dir() and (ep_dir / "metadata.json").exists()):
                continue

            with (ep_dir / "metadata.json").open("r", encoding="utf-8") as f:
                meta = json.load(f)

            # Найти картинку эпизода
            episode_image = None
            for file in ep_dir.iterdir():
                if file.is_file() and file.suffix.lower() in image_exts:
                    mtime = int(file.stat().st_mtime)
                    episode_image = f"/shows/{show_id}/episodes/{ep_dir.name}/{file.name}?v={mtime}"
                    break
            
            # Если у эпизода нет своей картинки, используем обложку шоу
            if not episode_image:
                episode_image = cover_image_url if cover_image_url else '/assets/default_cover.png'
            # Если обложки всё ещё нет, используем заглушку (но cover_image_url уже подстрахован выше)

            episodes.append({
                'id': ep_dir.name,
                'title': meta.get('title', 'Без названия'),
                'description': meta.get('description', ''),
                'image': episode_image
            })

    shows_list = []
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico")
    for sdir in sorted(SHOWS_DIR.iterdir()):
        if sdir.is_dir() and (sdir / "config.json").exists():
            with (sdir / "config.json").open("r", encoding="utf-8") as f:
                scfg = json.load(f)
            show_image = None
            for file in sdir.iterdir():
                if file.is_file() and file.suffix.lower() in image_exts:
                    show_image = url_for('show_file', show_id=sdir.name, filename=file.name, file_type='cover')
                    break
            if not show_image:
                show_image = '/assets/default_cover.png'
            shows_list.append({"id": sdir.name, "title": scfg.get("title", sdir.name), "image": show_image})

    # Подстраховка для legacy-шоу: всегда передавать category_main и category_sub
    if "category_main" not in cfg:
        cfg["category_main"] = ""
    if "category_sub" not in cfg:
        cfg["category_sub"] = ""
    return render_template("show.html", show=cfg, episodes=episodes, cover=cover_image_url, show_id=show_id, shows_list=shows_list)


@app.route("/api/episode_info/<show_id>/<episode_id>")
def get_episode_info_api(show_id, episode_id):
    # (Переписано) Быстро отдает готовые данные из metadata.json
    episode_dir = SHOWS_DIR / show_id / 'episodes' / episode_id
    metadata_path = episode_dir / "metadata.json"

    if not metadata_path.exists():
        return jsonify({"error": "Metadata not found"}), 404

    with metadata_path.open("r", encoding="utf-8") as f:
        meta = json.load(f)

    # Мы просто возвращаем все метаданные. Фронтенд сам решит, что показывать.
    # Убедимся, что обязательные поля для плеера есть, даже если пустые.
    response_data = {
        'title': meta.get('title', 'Без названия'),
        'audio_url': meta.get('audio'),
        'conversion_status': meta.get('conversion_status', 'unknown'),
        'conversion_error': meta.get('conversion_error'),
        'bitrate': meta.get('bitrate'),
        'channels': meta.get('channels'),
        'duration_seconds': meta.get('duration_seconds'),
        'samplerate': meta.get('samplerate'),
        'filename': Path(meta.get('audio', '')).name,
        'size': meta.get('size'),
        'size_bytes': meta.get('size_bytes')
    }

    return jsonify(response_data)




@app.route("/shows/<show_id>/edit", methods=["GET", "POST"])
def edit_show(show_id):
    show_dir = SHOWS_DIR / show_id
    config_path = show_dir / "config.json"
    if not config_path.exists():
        abort(404)

    if request.method == "POST":
        with config_path.open("r+", encoding="utf-8") as f:
            cfg = json.load(f)
            # Обновляем поля из формы
            fields = ["title", "author", "owner_email", "owner_name", "explicit", "summary", "description", "category", "category_main", "category_sub", "type", "copyright", "subtitle", "ttl"]
            for field in fields:
                if field in request.form:
                    val = request.form.get(field, "")
                    if field in ("description", "summary"):
                        from utils import sanitize_html_for_rss, plain_text_to_html
                        val = sanitize_html_for_rss(plain_text_to_html(val))
                    cfg[field] = val
            if "language" in request.form:
                cfg["language"] = request.form.get("language", "en-US").strip()
            if "ttl" in request.form and request.form.get("ttl"):
                try:
                    cfg["ttl"] = int(request.form.get("ttl"))
                except (ValueError, TypeError):
                    cfg["ttl"] = 60 # Default value
            # Обработка загрузки новой обложки
            image = request.files.get("image")
            if image and image.filename:
                ext = image.filename.split('.')[-1].lower()
                # Удаляем предыдущий файл обложки (если был указан в config)
                old_image_name = cfg.get("image")
                if old_image_name:
                    try:
                        (show_dir / old_image_name).unlink(missing_ok=True)
                    except Exception:
                        pass
                # Также подчистим любые файлы cover.* для порядка
                for old_file in show_dir.glob('cover.*'):
                    try:
                        old_file.unlink()
                    except Exception:
                        pass
                # Сохраняем новый файл под его оригинальным именем (без переименования)
                new_name = secure_filename(image.filename)
                image_path = show_dir / new_name
                image.save(str(image_path))
                try:
                    # Resize or convert to compliant format
                    from utils import resize_cover_image
                    processed_path = resize_cover_image(image_path)
                    if processed_path.name != new_name:
                        new_name = processed_path.name
                except Exception as exc:
                    app.logger.error("Failed to resize show cover on edit: %s", exc)
                cfg["image"] = new_name
            f.seek(0)
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.truncate()
        flash("Изменения шоу успешно сохранены!", "success")
        return redirect(url_for("show_page", show_id=show_id))

    from utils import html_to_plain_text
    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    # Преобразуем HTML обратно в plain text для textarea
    cfg["description"] = html_to_plain_text(cfg.get("description", ""))
    cfg["summary"] = html_to_plain_text(cfg.get("summary", ""))
    # Подстраховка для legacy-шоу: всегда передавать category_main и category_sub
    if "category_main" not in cfg:
        cfg["category_main"] = ""
    if "category_sub" not in cfg:
        cfg["category_sub"] = ""
    return render_template("edit_show.html", show=cfg, show_id=show_id)




@app.route("/shows/<show_id>/delete", methods=["POST"])
def delete_show(show_id):
    show_dir = SHOWS_DIR / show_id
    if not show_dir.exists():
        flash("Шоу не найдено.", "error")
        return redirect(url_for("index"))
    try:
        shutil.rmtree(show_dir)
        flash("Шоу удалено!", "success")
    except Exception as e:
        flash(f"Ошибка при удалении шоу: {e}", "error")
    return redirect(url_for("index"))

@app.route("/shows/<show_id>/feed.xml")
def show_feed_xml(show_id):
    import html
    import mimetypes
    from pathlib import Path
    import datetime
    import hashlib
    import time
    from email.utils import formatdate
    from flask import Response, request, url_for, abort

    # Force HTTPS in feed URLs because Cloudflare terminates TLS at the edge.
    # Using request.url_root could yield "http" since Cloudflare connects to the origin over HTTP.
    base_url = f"https://{request.host}"

    # Load show config
    show_dir = SHOWS_DIR / show_id
    config_path = show_dir / "config.json"
    if not config_path.exists():
        abort(404)
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    # Helper functions
    def normalize_explicit(val):
        """Return iTunes-valid explicit flag.
        Apple accepts: "explicit", "clean" or legacy "yes"/"no".
        PSP-1 prefers "explicit" / "clean". We map truthy values → "explicit", else → "clean".
        """
        v = str(val).strip().lower()
        if v in ("yes", "true", "explicit", "да", "y", "1"):
            return "true"
        return "false"

    def cdata_or_escape(text):
        if not text:
            return ''
        if any(x in text for x in ['&', '<', '>']):
            return f'<![CDATA[{text}]]>'
        return html.escape(text)

    items = []
    # Determine show-level cover image URL (used as fallback for episode images)
    show_cover_url = None
    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
    img_candidate = cfg.get('image')
    if img_candidate and (show_dir / img_candidate).exists():
        show_cover_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=img_candidate)}"
    if not show_cover_url:
        for f in show_dir.iterdir():
            if f.is_file() and f.suffix.lower() in image_exts:
                show_cover_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=f.name)}"
                break

    episodes_dir = show_dir / "episodes"
    if episodes_dir.exists():
        sorted_ep_dirs = sorted([d for d in episodes_dir.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)

        for ep_dir in sorted_ep_dirs:
            meta_path = ep_dir / "metadata.json"
            if not meta_path.exists():
                continue

            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            # Use audio file from metadata if available
            audio_filename = meta.get("filename")
            if not audio_filename:
                continue # Skip if no audio file is listed in metadata

            audio_file = ep_dir / audio_filename
            if not audio_file.exists():
                continue # Skip if audio file from metadata doesn't exist

            # Prepare item fields, prioritizing metadata
            title = meta.get("title", ep_dir.name)
            raw_description = meta.get("description", cfg.get('description', ''))
            description = sanitize_html_for_rss(raw_description)
            pubdate_str = meta.get("pubdate")
            try:
                dt_obj = datetime.datetime.fromisoformat(pubdate_str)
                pubdate = dt_obj.strftime("%a, %d %b %Y %H:%M:%S GMT")
            except (ValueError, TypeError, AttributeError):
                pubdate = datetime.datetime.fromtimestamp(ep_dir.stat().st_mtime).strftime("%a, %d %b %Y %H:%M:%S GMT")

            duration_str = meta.get('duration', '')
            enclosure_length = meta.get('size_bytes', 0)
            # Determine cache-busting version from latest modification time of relevant files
            version_ts = int(audio_file.stat().st_mtime)
            try:
                # Include metadata.json modification time
                version_ts = max(version_ts, int(meta_path.stat().st_mtime))
                # Include episode image mtime if present
                # Also include any image files in episode directory (covers may change without metadata update)
                image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
                for f in ep_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in image_exts and f.name != audio_file.name:
                        version_ts = max(version_ts, int(f.stat().st_mtime))
            except Exception:
                pass  # Fall back to audio file mtime
            audio_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=f'episodes/{ep_dir.name}/{audio_file.name}')}?v={version_ts}"
            episode_link = f"{base_url}{url_for('edit_episode', show_id=show_id, ep_id=ep_dir.name)}"
            
            ep_image_url = None
            img_path_candidate = None
            if meta.get("episode_image"):
                img_name = Path(meta["episode_image"]).name
                img_path_candidate = ep_dir / img_name
                if img_path_candidate.exists():
                    ep_image_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=f'episodes/{ep_dir.name}/{img_name}')}"
            # If metadata stale or missing, auto-discover any image file in episode dir
            if not ep_image_url:
                for f in ep_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                        ep_image_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=f'episodes/{ep_dir.name}/{f.name}')}"
                        break
            # Fallback to show-level cover if episode image still not found
            if not ep_image_url:
                ep_image_url = show_cover_url

            ep_summary = sanitize_html_for_rss(meta.get("summary", description))
            # Transcript URL (recommended PSP-1 element)
            transcript_url = meta.get("transcript")
            if not transcript_url:
                # Fallback to the public episode page if no dedicated transcript is available
                transcript_url = episode_link
            transcript_type = "text/html"
            mime, _ = mimetypes.guess_type(str(audio_file))
            guid_val = f"{show_id}_{ep_dir.name}"

            # Append item XML
            items.append(f'''
        <item>
            <title>{cdata_or_escape(title)}</title>
            <link>{episode_link}</link>
            <description>{cdata_or_escape(description)}</description>
            <enclosure url=\"{audio_url}\" type=\"{mime or 'audio/mpeg'}\" length=\"{enclosure_length or 0}\"/>
            <guid isPermaLink=\"false\">{guid_val}</guid>
            <pubDate>{pubdate}</pubDate>
            {f'<itunes:image href="{ep_image_url}" />' if ep_image_url else ''}
            {f'<itunes:summary>{cdata_or_escape(ep_summary)}</itunes:summary>' if ep_summary else ''}
            {f'<podcast:transcript url="{html.escape(transcript_url)}" type="{transcript_type}" />' if transcript_url else ''}
            {f'<itunes:duration>{duration_str}</itunes:duration>' if duration_str else ''}
            <itunes:explicit>{normalize_explicit(meta.get("explicit"))}</itunes:explicit>
        </item>''')

    # Find show cover – first look at explicit config, otherwise discover automatically
    cover_url = None
    img_name = cfg.get('image')
    image_exts = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')

    if img_name:
        img_path = show_dir / img_name
        if not img_path.exists():
            img_name = None  # fall back to auto-discovery

    # Auto-discover any image file in the show directory if not defined
    if not img_name:
        for f in show_dir.iterdir():
            if f.is_file() and f.suffix.lower() in image_exts:
                img_name = f.name
                break
        # Persist discovery so we do not have to search again next time
        if img_name:
            try:
                with (show_dir / 'config.json').open('r+', encoding='utf-8') as fc:
                    auto_cfg = json.load(fc)
                    auto_cfg['image'] = img_name
                    fc.seek(0)
                    json.dump(auto_cfg, fc, ensure_ascii=False, indent=2)
                    fc.truncate()
            except Exception:
                pass  # not critical

    if img_name:
        cover_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=img_name)}"

    # Assemble channel-level info
    channel_link = f"{base_url}{url_for('show_page', show_id=show_id)}"
    # Sanitize show-level description separately
    show_description = sanitize_html_for_rss(cfg.get('description', ''))
    atom_url = f"{base_url}{url_for('show_feed_xml', show_id=show_id)}"
    itunes_author = cfg.get('author')
    itunes_explicit = normalize_explicit(cfg.get('explicit'))
    itunes_owner_name = cfg.get('owner_name')
    itunes_owner_email = cfg.get('owner_email')
    itunes_summary = sanitize_html_for_rss(cfg.get('summary', cfg.get('description', '')))
    itunes_owner = f'<itunes:owner><itunes:name>{cdata_or_escape(itunes_owner_name)}</itunes:name><itunes:email>{itunes_owner_email}</itunes:email></itunes:owner>' if itunes_owner_name and itunes_owner_email else ''
    now_gmt = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    copyright_val = cfg.get('copyright', f" 2025 {itunes_author or cfg.get('title')}")
    itunes_subtitle = cfg.get('subtitle', '')

    # PSP-1 requires at least one element from the "podcast" namespace; we include <podcast:locked>
    podcast_locked = ''
    if itunes_owner_email:
        podcast_locked = f'<podcast:locked owner="{html.escape(itunes_owner_email)}">no</podcast:locked>'
    
    # Categories
    cat_main = cfg.get('category_main', '')
    cat_sub = cfg.get('category_sub', '')
    itunes_cat = ''
    if cat_main:
        itunes_cat = f'<itunes:category text="{html.escape(cat_main)}">'
        if cat_sub:
            itunes_cat += f'<itunes:category text="{html.escape(cat_sub)}"/>'
        itunes_cat += '</itunes:category>'
    
    # Image block
    image_block = ''
    if cover_url:
        image_block = f"<image>\n      <url>{html.escape(cover_url)}</url>\n      <title>{html.escape(cfg.get('title'))}</title>\n      <link>{html.escape(base_url + url_for('show_page', show_id=show_id))}</link>\n    </image>\n    <itunes:image href=\"{html.escape(cover_url)}\" />"

    # Recommended PSP-1 channel-level GUID
    podcast_guid_tag = f"<podcast:guid>{html.escape(cfg.get('guid', show_id))}</podcast:guid>"

    # Final RSS assembly
    rss = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:podcast="https://podcastindex.org/namespace/1.0">
<channel>
    <title>{cdata_or_escape(cfg.get('title', show_id))}</title>
    <link>{channel_link}</link>
    <atom:link href="{atom_url}" rel="self" type="application/rss+xml"/>
    <description>{cdata_or_escape(show_description)}</description>
    <language>{cfg.get('language', 'en-US')}</language>
    <copyright>{cdata_or_escape(copyright_val)}</copyright>
    <lastBuildDate>{now_gmt}</lastBuildDate>
    <itunes:author>{cdata_or_escape(itunes_author)}</itunes:author>
    <itunes:summary>{cdata_or_escape(itunes_summary)}</itunes:summary>
    {itunes_owner}
    <itunes:explicit>{itunes_explicit}</itunes:explicit>
    {itunes_cat}
    {image_block}
    {podcast_guid_tag}
    {''.join(items)}
    {podcast_locked}
</channel>
</rss>'''

    # Calculate ETag and Last-Modified headers
    last_modified_time = 0
    content_hash = hashlib.sha256()
    content_hash.update(rss.encode('utf-8'))
    
    # Find the most recent modification time among all files
    for meta_path in config_path, *[d / "metadata.json" for d in sorted_ep_dirs]:
        try:
            mtime = meta_path.stat().st_mtime
            if mtime > last_modified_time:
                last_modified_time = mtime
        except (FileNotFoundError, OSError):
            pass
    
    # Add config and audio file modification times to ETag calculation
    content_hash.update(str(last_modified_time).encode('utf-8'))
    etag = f'"{content_hash.hexdigest()[:16]}"'
    
    # Return the feed with caching headers
    response = Response(rss, content_type="application/rss+xml; charset=utf-8")
    response.headers['ETag'] = etag
    response.headers['Last-Modified'] = formatdate(last_modified_time, localtime=False, usegmt=True)
    response.headers.setdefault('Cache-Control', 'public, max-age=0')
    if request.headers.get('If-None-Match') == etag:
        return Response(status=304)
    return response

    audio_file = None
    for f in ep_dir.iterdir():
        if f.suffix.lower() in [".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"]:
            audio_file = f
            break
    audio_url = None
    if audio_file:
        audio_url = f"/shows/{show_id}/episodes/{ep_id}/{audio_file.name}"
    # Картинка эпизода
    image_url = meta.get("episode_image")
    if image_url and image_url.startswith("/"):
        image_url = f"https://podcast.ecowool.online{image_url}"
    # duration
    duration = meta.get("duration")
    if isinstance(duration, (int, float)):
        h = int(duration // 3600)
        m = int((duration % 3600) // 60)
        s = int(duration % 60)
        duration = f"{h}:{m:02}:{s:02}" if h > 0 else f"{m}:{s:02}"
    elif isinstance(duration, str):
        duration = duration
    episode = {
        "title": meta.get("title", ep_id),
        "description": meta.get("description", ""),
        "pubdate": meta.get("pubdate"),
        "audio_url": audio_url,
        "image_url": image_url,
        "duration": duration,
        "explicit": meta.get("explicit"),
    }
    return render_template("episode.html", show_id=show_id, episode_id=ep_id, episode=episode)

@app.route("/shows/<show_id>/<path:filename>")
def show_file(show_id, filename):
    """Serve any file that belongs to a show (cover image, episode assets, etc.).
    The <path:filename> may contain nested segments like ``episodes/<ep_id>/cover.png``.

    We resolve the requested path relative to the show's root directory and
    additionally guard against directory-traversal attempts.
    """
    show_dir = (SHOWS_DIR / show_id).resolve()
    target_path = (show_dir / filename).resolve()

    # Disallow path traversal outside the show directory
    if not str(target_path).startswith(str(show_dir)):
        abort(403)

    if not target_path.exists() or not target_path.is_file():
        abort(404)

    # ``send_from_directory`` requires directory & filename separately.
    # Pass conditional=True so Flask/Werkzeug handles Range requests.
    response = send_from_directory(str(target_path.parent), target_path.name, conditional=True)
    # Explicitly add Accept-Ranges header so validators that only perform a
    # HEAD request without a Range header can still detect byte-range support.
    response.headers.setdefault("Accept-Ranges", "bytes")
    return response


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(ASSETS_DIR, "favicon.ico")

from werkzeug.utils import secure_filename
import uuid
import os
from werkzeug.utils import secure_filename



def remove_old_episode_covers(ep_dir, img_name):
    """Удаляет все старые обложки эпизода кроме новой."""
    for old_img in ep_dir.iterdir():
        if old_img.is_file() and old_img.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico"] and old_img.name != img_name:
            try:
                old_img.unlink()
            except Exception:
                pass

def check_transcoding_needed(audio_path: Path) -> (bool, str):
    """Проверяет, нужно ли перекодировать аудиофайл."""
    # Получаем информацию о файле
    info = get_audio_info(audio_path)
    if not info or info.get("error"):
        return True, f"Не удалось получить информацию о файле: {info.get('error', 'неизвестная ошибка')}"

    # Если это не MP3, перекодируем
    if audio_path.suffix.lower() != '.mp3':
        return True, f"Файл не в формате MP3 ({audio_path.suffix})"

    # Проверяем битрейт (извлекаем число из строки '192 kbps')
    try:
        bitrate_kbps = int(info.get("bitrate", "0").split()[0])
        if bitrate_kbps < MIN_PODCAST_BITRATE:
            return True, f"Битрейт слишком низкий ({bitrate_kbps} kbps)"
    except (ValueError, IndexError):
        return True, "Не удалось определить битрейт"

    # Если все проверки пройдены, перекодирование не нужно
    return False, "Файл уже в нужном формате и качестве"


def process_audio_background(audio_path_str, show_id, ep_id):
    # (Переписано) Обрабатывает аудиофайл в фоновом режиме, получает метаданные и сохраняет их.
    with app.app_context():
        app.logger.info(f"--- BG PROCESS START for {audio_path_str} ---")
        audio_path = Path(audio_path_str)
        ep_dir = SHOWS_DIR / show_id / "episodes" / ep_id
        meta_path = ep_dir / "metadata.json"

        meta = {}
        final_audio_path = None

        try:
            app.logger.info(f"[BG] Loading metadata from {meta_path}")
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)

            needs_transcoding, reason = check_transcoding_needed(audio_path)
            app.logger.info(f"[BG] Checking transcoding for {audio_path.name}: needs_transcoding={needs_transcoding}, reason='{reason}'")

            # Pre-build ID3 metadata for both transcoding and direct tagging paths
            import datetime as _dt
            metadata_dict = {}
            try:
                with (SHOWS_DIR / show_id / "config.json").open("r", encoding="utf-8") as _fcfg:
                    show_cfg = json.load(_fcfg)
            except Exception:
                show_cfg = {}

            metadata_dict["title"] = meta.get("title") or ep_id
            metadata_dict["artist"] = show_cfg.get("author") or show_cfg.get("title") or show_id
            metadata_dict["album"] = show_cfg.get("title") or show_id
            metadata_dict["date"] = _dt.datetime.utcnow().strftime("%Y")
            # Add copyright information to be written into the TCOP frame
            if show_cfg.get("copyright"):
                metadata_dict["copyright"] = show_cfg["copyright"]
            explicit_flag = str(meta.get("explicit", "")).strip().lower()
            metadata_dict["ITUNESADVISORY"] = "1" if explicit_flag in ("yes", "true", "explicit", "y", "да", "1") else "0"

            # Attempt to locate a cover image for the episode
            cover_path_val = None
            if meta.get("image"):
                candidate = ep_dir / Path(meta["image"]).name
                if candidate.exists():
                    cover_path_val = candidate
            if cover_path_val is None:
                for _img in ep_dir.iterdir():
                    if _img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        cover_path_val = _img
                        break

            if not needs_transcoding:
                app.logger.info(f"[BG] No transcoding needed.")
                # Ensure ID3v2 tags are present; embed if missing
                if audio_path.suffix.lower() == '.mp3' and not has_id3v2_tags(audio_path):
                    app.logger.info(f"[BG] Embedding ID3 tags into {audio_path.name} …")
                    try:
                        embed_id3_metadata_mp3(
                            audio_path,
                            metadata=metadata_dict,
                            cover_path=cover_path_val,
                        )
                    except Exception as tag_exc:
                        app.logger.warning(f"[BG] Failed to embed ID3 tags: {tag_exc}")
                final_audio_path = audio_path
            else:
                app.logger.info(f"[BG] Transcoding required for {audio_path.name}. Determining optimal bitrate…")
                src_info = get_audio_info(audio_path)
                try:
                    src_br_kbps = int(str(src_info.get("bitrate", "0").split()[0]))
                except (ValueError, IndexError, TypeError):
                    src_br_kbps = None
                target_bitrate = select_mp3_bitrate(src_br_kbps) if src_br_kbps else "192k"
                app.logger.info(f"[BG] Selected target bitrate: {target_bitrate}")
                # Build ID3 metadata and detect cover art before transcoding
                import datetime as _dt
                metadata_dict = {}
                # Load show configuration for album/artist fields
                try:
                    with (SHOWS_DIR / show_id / "config.json").open("r", encoding="utf-8") as _fcfg:
                        show_cfg = json.load(_fcfg)
                except Exception:
                    show_cfg = {}

                metadata_dict["title"] = meta.get("title") or ep_id
                metadata_dict["artist"] = show_cfg.get("author") or show_cfg.get("title") or show_id
                metadata_dict["album"] = show_cfg.get("title") or show_id
                metadata_dict["date"] = _dt.datetime.utcnow().strftime("%Y")
            # Add copyright information to be written into the TCOP frame
            if show_cfg.get("copyright"):
                metadata_dict["copyright"] = show_cfg["copyright"]
            # Normalise explicit flag: 1 = explicit, 0 = not explicit/clean
            explicit_flag = str(meta.get("explicit", "")).strip().lower()
            metadata_dict["ITUNESADVISORY"] = "1" if explicit_flag in ("yes", "true", "explicit", "y", "да", "1") else "0"

            # Attempt to locate a cover image for the episode
            cover_path_val = None
            if meta.get("image"):
                candidate = ep_dir / Path(meta["image"]).name
                if candidate.exists():
                    cover_path_val = candidate
            if cover_path_val is None:
                for _img in ep_dir.iterdir():
                    if _img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        cover_path_val = _img
                        break

                new_path_str = transcode_audio_to_mp3(
                    audio_path,
                    bitrate=target_bitrate,
                    metadata=metadata_dict,
                    cover_path=cover_path_val,
                )
                if new_path_str:
                    final_audio_path = Path(new_path_str)
                    app.logger.info(f"[BG] Transcoding successful. New file: {final_audio_path}")
                    meta['audio'] = f"/shows/{show_id}/episodes/{ep_id}/{final_audio_path.name}"
                else:
                    raise Exception("transcode_audio_to_mp3 returned None")

            # Если есть финальный аудиофайл, получаем его метаданные
            if final_audio_path and final_audio_path.exists():
                app.logger.info(f"[BG] Getting audio info for {final_audio_path}")
                audio_info = get_audio_info(final_audio_path)
                meta.update(audio_info) # Добавляем всю инфу в метаданные
                meta['conversion_status'] = 'success'
                if 'conversion_error' in meta: del meta['conversion_error']
                app.logger.info(f"[BG] Audio info obtained and updated in metadata.")
            elif not final_audio_path:
                 raise Exception("Transcoding failed and no final audio path was set.")
            else: # final_audio_path было задано, но файла нет
                 raise Exception(f"Final audio file {final_audio_path} not found after processing.")

        except Exception as e:
            app.logger.error(f"[BG] Exception in background task for {ep_id}: {e}", exc_info=True)
            meta['conversion_status'] = 'failed'
            meta['conversion_error'] = str(e)

        finally:
            if meta:
                try:
                    with meta_path.open("w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
                    app.logger.info(f"[BG] Metadata saved for episode {ep_id} with final status: {meta.get('conversion_status')}")
                except Exception as e:
                    app.logger.error(f"[BG] CRITICAL: Could not write final metadata to {meta_path}. Error: {e}")
            app.logger.info(f"--- BG PROCESS END for {audio_path_str} ---")


@app.route("/shows/<show_id>/episodes/new", methods=["GET", "POST"])
def new_episode(show_id):
    show_dir = SHOWS_DIR / show_id
    episodes_dir = show_dir / "episodes"
    msg = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        explicit = request.form.get("explicit", "no")
        category_main = request.form.get("category_main", "")
        category_sub = request.form.get("category_sub", "")
        summary = request.form.get("summary", "").strip()
        duration = request.form.get("duration", "")
        # handle file uploads
        episode_image = request.files.get("episode_image")
        audio = request.files.get("audio")
        # language
        language = request.form.get("language", "en-US").strip()
        # create unique episode id
        ep_id = str(uuid.uuid4())[:8]
        ep_dir = episodes_dir / ep_id
        ep_dir.mkdir(parents=True, exist_ok=True)

        description_html = sanitize_html_for_rss(plain_text_to_html(description))
        summary_html = sanitize_html_for_rss(plain_text_to_html(summary))

        meta = {
            "id": ep_id,
            "title": title,
            "language": language,
            "description": description_html,
            "explicit": explicit,
            "category_main": category_main,
            "category_sub": category_sub,
            "summary": summary_html,
            "duration": duration,
            "conversion_status": "pending",
        }

        if episode_image and episode_image.filename:
            img_name = secure_filename(episode_image.filename)
            remove_old_episode_covers(ep_dir, img_name)
            image_path = ep_dir / img_name
            episode_image.save(str(image_path))
            try:
                processed_path = resize_cover_image(image_path)
                if processed_path.name != img_name:
                    img_name = processed_path.name
            except Exception as exc:
                app.logger.error("Failed to resize episode cover image for %s/%s: %s", show_id, ep_id, exc)
            meta["episode_image"] = f"/shows/{show_id}/episodes/{ep_id}/{img_name}"

        if audio and audio.filename:
            audio_name = secure_filename(audio.filename)
            audio_path = ep_dir / audio_name
            audio.save(str(audio_path))
            meta["audio"] = f"/shows/{show_id}/episodes/{ep_id}/{audio_name}"

            # Если файл не MP3 или требует перекодировки, сразу помечаем это.
            # Окончательное решение примет фоновый процесс, но UI уже будет в курсе.
            # Для любого нового аудиофайла запускаем проверку/конвертацию
            meta['conversion_status'] = 'processing'
            if 'conversion_error' in meta: del meta['conversion_error']
            
            # СНАЧАЛА сохраняем метаданные, ПОТОМ запускаем поток
            with (ep_dir / "metadata.json").open("w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            
            # Теперь запускаем фоновую обработку
            thread = threading.Thread(target=process_audio_background, args=(str(audio_path), show_id, ep_id))
            thread.start()
        elif request.form.get('audio_url'):
            # Format: /tmp_uploads/uploadid_filename.mp3
            audio_url = request.form.get('audio_url')
            if audio_url.startswith('/tmp_uploads/'):
                try:
                    # Extract the filename and move the file
                    temp_path = BASE_DIR / audio_url.lstrip('/')
                    if temp_path.exists():
                        audio_name = secure_filename(temp_path.name.split('_', 1)[1])
                        audio_path = ep_dir / audio_name
                        shutil.copy2(temp_path, audio_path)
                        # Delete the temp file after copying
                        temp_path.unlink()
                        meta["audio"] = f"/shows/{show_id}/episodes/{ep_id}/{audio_name}"

                        # Если файл не MP3 или требует перекодировки, сразу помечаем это.
                        # Окончательное решение примет фоновый процесс, но UI уже будет в курсе.
                        # Для любого нового аудиофайла запускаем проверку/конвертацию
                        meta['conversion_status'] = 'processing'
                        if 'conversion_error' in meta: del meta['conversion_error']
                        
                        # СНАЧАЛА сохраняем метаданные, ПОТОМ запускаем поток
                        with (ep_dir / "metadata.json").open("w", encoding="utf-8") as f:
                            json.dump(meta, f, ensure_ascii=False, indent=2)
                        
                        # Теперь запускаем фоновую обработку
                        thread = threading.Thread(target=process_audio_background, args=(str(audio_path), show_id, ep_id))
                        thread.start()
                    else:
                        flash("Temporary audio file not found. Please upload again.", "error")
                        shutil.rmtree(ep_dir)
                        return render_template("new_episode.html", show_id=show_id, msg="Temporary audio file not found")
                except Exception as e:
                    app.logger.error(f"Error processing chunked upload: {str(e)}")
                    flash(f"Error processing uploaded audio: {str(e)}", "error")
                    shutil.rmtree(ep_dir)
                    return render_template("new_episode.html", show_id=show_id, msg=f"Error processing upload: {str(e)}")
        else:
            flash("Аудиофайл обязателен для создания эпизода.", "error")
            shutil.rmtree(ep_dir)
            return render_template("new_episode.html", show_id=show_id, msg=msg)

        # Этот блок был перемещен выше, чтобы исправить race condition
        flash("Эпизод успешно создан!", "success")
        return redirect(url_for("show_page", show_id=show_id))
    return render_template("new_episode.html", show_id=show_id, msg=msg)

@app.route("/shows/<show_id>/episodes/<ep_id>/delete", methods=["POST"], endpoint="delete_episode")
def delete_episode(show_id, ep_id):
    show_dir = SHOWS_DIR / show_id
    ep_dir = show_dir / "episodes" / ep_id
    meta_path = ep_dir / "metadata.json"
    msg = None
    if not meta_path.exists():
        abort(404)
    if request.method == "POST":
        shutil.rmtree(ep_dir)
        flash("Эпизод удалён!", "success")
        return redirect(url_for("show_page", show_id=show_id))

@app.route("/shows/<show_id>/episodes/<ep_id>/edit", methods=["GET", "POST"])
def edit_episode(show_id, ep_id):
    show_dir = SHOWS_DIR / show_id
    ep_dir = show_dir / "episodes" / ep_id
    meta_path = ep_dir / "metadata.json"
    msg = None
    if not meta_path.exists():
        abort(404)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        explicit = request.form.get("explicit", "no")
        category_main = request.form.get("category_main", "")
        category_sub = request.form.get("category_sub", "")
        summary = request.form.get("summary", "").strip()
        duration = request.form.get("duration", "")
        episode_image = request.files.get("episode_image")
        audio = request.files.get("audio")
        with meta_path.open("r", encoding="utf-8") as f:
            meta = json.load(f)
        from utils import sanitize_html_for_rss, plain_text_to_html
        meta.update({
            "title": title,
            "language": request.form.get("language", "en-US").strip(),
            "description": sanitize_html_for_rss(plain_text_to_html(description)),
            "explicit": explicit,
            "category_main": category_main,
            "category_sub": category_sub,
            "summary": sanitize_html_for_rss(plain_text_to_html(summary)),
            "duration": duration,
        })
        if episode_image and episode_image.filename:
            img_name = secure_filename(episode_image.filename)
            remove_old_episode_covers(ep_dir, img_name)
            image_path = ep_dir / img_name
            episode_image.save(str(image_path))
            try:
                processed_path = resize_cover_image(image_path)
                if processed_path.name != img_name:
                    img_name = processed_path.name
            except Exception as exc:
                app.logger.error("Failed to resize episode cover image for %s/%s: %s", show_id, ep_id, exc)
            meta["episode_image"] = f"/shows/{show_id}/episodes/{ep_id}/{img_name}"
        if audio and audio.filename:
            audio_name = secure_filename(audio.filename)
            audio_path = ep_dir / audio_name
            audio.save(str(audio_path))
            meta["audio"] = f"/shows/{show_id}/episodes/{ep_id}/{audio_name}"

            # Если загружен новый файл, который не MP3, помечаем для конвертации.
            # Для любого нового аудиофайла запускаем проверку/конвертацию
            meta['conversion_status'] = 'processing'
            if 'conversion_error' in meta: del meta['conversion_error']

            # СНАЧАЛА сохраняем метаданные, ПОТОМ запускаем поток
            with (ep_dir / "metadata.json").open("w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            # Теперь запускаем фоновую обработку
            thread = threading.Thread(target=process_audio_background, args=(str(audio_path), show_id, ep_id))
            thread.start()
        else: # если аудиофайл не менялся, просто сохраняем метаданные
             with (ep_dir / "metadata.json").open("w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        flash("Эпизод обновлён!", "success")
        return redirect(url_for("show_page", show_id=show_id))
    with meta_path.open("r", encoding="utf-8") as f:
        meta = json.load(f)
    # Преобразуем HTML обратно в plain text для textarea
    meta["description"] = html_to_plain_text(meta.get("description", ""))
    meta["summary"] = html_to_plain_text(meta.get("summary", ""))
    return render_template("edit_episode.html", episode=meta, msg=msg)


import logging
from logging.handlers import RotatingFileHandler

# --- LOGGING SETUP ---
LOG_PATH = Path("logs/podcast_app.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(str(LOG_PATH), maxBytes=2_000_000, backupCount=2)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s'))
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)

@app.errorhandler(404)
def not_found(e):
    app.logger.error(f"404 Not Found: {request.method} {request.url} args={dict(request.args)} form={dict(request.form)}")
    return render_template("404.html", url=request.url, method=request.method), 404

@app.errorhandler(500)
def internal_error(e):
    tb = traceback.format_exc()
    app.logger.error(f"500 Internal Error: {request.method} {request.url} args={dict(request.args)} form={dict(request.form)}\nTraceback:\n{tb}")
    return render_template("500.html", url=request.url, method=request.method, tb=tb), 500

@app.errorhandler(405)
def method_not_allowed(e):
    app.logger.error(f"405 Method Not Allowed: {request.method} {request.url} args={dict(request.args)} form={dict(request.form)}")
    return render_template("405.html", url=request.url, method=request.method), 405

@app.route("/shows/<show_id>/cover-upload", methods=["POST"])
def upload_show_cover(show_id):
    show_dir = SHOWS_DIR / show_id
    if not show_dir.exists():
        return jsonify({"error": "Show not found"}), 404
    if 'cover' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['cover']
    img_name = secure_filename(file.filename)
    remove_old_episode_covers(show_dir, img_name)
    file_path = show_dir / img_name
    file.save(str(file_path))

    # Resize image to comply with podcast cover requirements
    try:
        processed_path = resize_cover_image(file_path)
        file_path = processed_path  # may differ if extension converted
        img_name = processed_path.name
    except Exception as exc:
        app.logger.error("Failed to resize cover for show %s: %s", show_id, exc)
        return jsonify({"error": "Failed to process image"}), 500

    url = f"/shows/{show_id}/{img_name}?v={int(file_path.stat().st_mtime)}"
    return jsonify({"image_url": url})

@app.route("/shows/<show_id>/episodes/<ep_id>/cover-upload", methods=["POST"])
def upload_episode_cover(show_id, ep_id):
    ep_dir = SHOWS_DIR / show_id / "episodes" / ep_id
    if not ep_dir.exists():
        return jsonify({"error": "Episode not found"}), 404
    if 'cover' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['cover']
    img_name = secure_filename(file.filename)
    remove_old_episode_covers(ep_dir, img_name)
    file_path = ep_dir / img_name
    file.save(str(file_path))

    # Resize image to comply with podcast cover requirements
    try:
        processed_path = resize_cover_image(file_path)
        file_path = processed_path
        img_name = processed_path.name
    except Exception as exc:
        app.logger.error("Failed to resize episode cover for %s/%s: %s", show_id, ep_id, exc)
        return jsonify({"error": "Failed to process image"}), 500

    # Обновляем config.json эпизода, чтобы поле "image" содержало имя файла
    config_path = ep_dir / "config.json"
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as cf:
                cfg = json.load(cf)
        except Exception:
            cfg = {}
    else:
        cfg = {}

    cfg["image"] = img_name
    try:
        with config_path.open("w", encoding="utf-8") as cf:
            json.dump(cfg, cf, ensure_ascii=False, indent=2)
    except Exception as exc:
        app.logger.error("Failed to update episode config %s: %s", config_path, exc)

    url = f"/shows/{show_id}/episodes/{ep_id}/{img_name}?v={int(file_path.stat().st_mtime)}"
    return jsonify({"image_url": url})

@app.route("/shows/<show_id>/episodes/<ep_id>/audio-upload", methods=["POST"])
def upload_episode_audio(show_id, ep_id):
    """Upload/replace episode MP3 file"""
    ep_dir = SHOWS_DIR / show_id / "episodes" / ep_id
    if not ep_dir.exists():
        return jsonify({"error": "Episode not found"}), 404

    # Support two modes:
    # 1) Traditional multipart upload with key 'audio' in request.files
    # 2) JSON payload {"tempFile": "relative/path/from/uploads", "filename": "desired_name.mp3"}
    if request.is_json:
        data = request.get_json(silent=True) or {}
        temp_path_str = data.get('tempFile')
        new_filename = secure_filename(data.get('filename', 'uploaded.mp3'))
        if temp_path_str:
            temp_path = BASE_DIR / temp_path_str
            if not temp_path.exists():
                return jsonify({"error": "Temp file not found"}), 400
            # Remove existing mp3 files to keep directory clean
            for existing in ep_dir.glob('*.mp3'):
                try:
                    existing.unlink()
                except OSError:
                    pass
            dest_path = ep_dir / new_filename
            try:
                shutil.move(str(temp_path), str(dest_path))
            except Exception as exc:
                app.logger.error("Failed to move temp audio %s to %s: %s", temp_path, dest_path, exc)
                return jsonify({"error": "Failed to move file"}), 500
            # Обновляем config.json эпизода, чтобы поле "audio" содержало имя файла
            config_path = ep_dir / "config.json"
            if config_path.exists():
                try:
                    with config_path.open("r", encoding="utf-8") as cf:
                        cfg = json.load(cf)
                except Exception:
                    cfg = {}
            else:
                cfg = {}

            cfg["audio"] = new_filename
            try:
                with config_path.open("w", encoding="utf-8") as cf:
                    json.dump(cfg, cf, ensure_ascii=False, indent=2)
            except Exception as exc:
                app.logger.error("Failed to update episode config %s: %s", config_path, exc)

            # После успешного перемещения можно удалить временную директорию upload_id, если она пуста
            try:
                upload_dir_parent = temp_path.parent  # tmp_uploads/<upload_id>
                if upload_dir_parent.exists():
                    shutil.rmtree(upload_dir_parent, ignore_errors=True)
            except Exception as exc:
                app.logger.warning("Cannot remove temp upload dir %s: %s", upload_dir_parent, exc)

            url = f"/shows/{show_id}/episodes/{ep_id}/{new_filename}?v={int(dest_path.stat().st_mtime)}"
            # Update metadata.json and launch background processing
            meta_path = ep_dir / "metadata.json"
            meta = {}
            if meta_path.exists():
                try:
                    with meta_path.open("r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                except Exception:
                    meta = {}
            meta.update({
                "audio": url.split("?v=")[0],
                "conversion_status": "processing",
            })
            try:
                with meta_path.open("w", encoding="utf-8") as mf:
                    json.dump(meta, mf, ensure_ascii=False, indent=2)
            except Exception as exc:
                app.logger.error("Failed to update metadata for %s: %s", ep_dir, exc)
            # Kick off transcoding / ID3 tagging in background
            threading.Thread(target=process_audio_background, args=(str(dest_path), show_id, ep_id)).start()
            return jsonify({"audio_url": url})
    
    # Fallback to multipart/form-data
    if 'audio' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['audio']
    filename = secure_filename(file.filename)
    if not filename.lower().endswith('.mp3'):
        return jsonify({"error": "Only MP3 files are allowed"}), 400

    # Remove existing mp3 files to keep directory clean
    for existing in ep_dir.glob('*.mp3'):
        try:
            existing.unlink()
        except OSError:
            pass

    file_path = ep_dir / filename
    file.save(str(file_path))

    url = f"/shows/{show_id}/episodes/{ep_id}/{filename}?v={int(file_path.stat().st_mtime)}"
    # Update metadata.json and launch background processing
    meta_path = ep_dir / "metadata.json"
    meta = {}
    if meta_path.exists():
        try:
            with meta_path.open("r", encoding="utf-8") as mf:
                meta = json.load(mf)
        except Exception:
            meta = {}
    meta.update({
        "audio": url.split("?v=")[0],
        "conversion_status": "processing",
    })
    try:
        with meta_path.open("w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False, indent=2)
    except Exception as exc:
        app.logger.error("Failed to update metadata for %s: %s", ep_dir, exc)
    threading.Thread(target=process_audio_background, args=(str(file_path), show_id, ep_id)).start()

    return jsonify({"audio_url": url})


@app.route("/sanitize_html", methods=["POST"])
def sanitize_html_api():
    from utils import sanitize_html_for_rss, plain_text_to_html
    try:
        data = request.get_json(force=True)
        html = data.get("html", "")

        cleaned = sanitize_html_for_rss(plain_text_to_html(html))
        return jsonify({"cleaned": cleaned})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
@app.route('/api/upload/chunk', methods=['POST'])
def upload_chunk():
    """API endpoint for chunked file uploads"""
    try:
        if 'file' not in request.files:
            app.logger.warning("Upload chunk failed: No file part in request")
            return jsonify({'error': 'No file part', 'details': 'Request missing file data'}), 400
        
        file = request.files['file']
        if file.filename == '':
            app.logger.warning("Upload chunk failed: Empty filename")
            return jsonify({'error': 'No selected file', 'details': 'Filename is empty'}), 400
        
        # Get upload parameters
        try:
            chunk_index = int(request.form.get('chunkIndex', 0))
            total_chunks = int(request.form.get('totalChunks', 1))  
            filename = request.form.get('filename')
            upload_id = request.form.get('uploadId')
        except ValueError as e:
            app.logger.error(f"Upload chunk failed: Invalid parameters - {str(e)}")
            return jsonify({'error': 'Invalid parameters', 'details': str(e)}), 400
        
        if not all([filename, upload_id]):
            app.logger.warning("Upload chunk failed: Missing required parameters")
            return jsonify({'error': 'Missing required parameters', 'details': 'Both filename and uploadId are required'}), 400
        
        # Create upload directory for this upload
        upload_dir = UPLOADS_DIR / upload_id
        upload_dir.mkdir(exist_ok=True)
        
        try:
            # Save this chunk
            chunk_file = upload_dir / f"chunk_{chunk_index:05d}"  # Zero-padded chunk index
            file.save(str(chunk_file))
            
            # Update metadata
            meta_file = upload_dir / "metadata.json"
            meta = {}
            if meta_file.exists():
                with meta_file.open('r') as f:
                    try:
                        meta = json.load(f)
                    except json.JSONDecodeError:
                        app.logger.warning(f"Invalid metadata file for upload {upload_id}, creating new")
                        meta = {}
            
            # Track progress
            chunks_received = meta.get('chunks_received', [])
            if chunk_index not in chunks_received:
                chunks_received.append(chunk_index)
            meta['chunks_received'] = sorted(set(chunks_received))  # Deduplicate and sort
            meta['filename'] = secure_filename(filename)
            meta['total_chunks'] = total_chunks
            meta['last_update'] = time.time()
            
            # Write metadata
            with meta_file.open('w') as f:
                json.dump(meta, f)
            
            # Check if upload is complete
            is_complete = len(meta['chunks_received']) == total_chunks
            
            app.logger.info(f"Chunk {chunk_index + 1}/{total_chunks} received for upload {upload_id} ({len(meta['chunks_received'])}/{total_chunks} complete)")
            
            return jsonify({
                'success': True,
                'chunkIndex': chunk_index,
                'received': len(meta['chunks_received']),
                'total': total_chunks,
                'complete': is_complete
            })
            
        except IOError as e:
            app.logger.error(f"Upload chunk failed: IO Error - {str(e)}")
            return jsonify({'error': 'File system error', 'details': str(e)}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error in upload_chunk: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Server error', 'details': str(e)}), 500
@app.route('/api/upload/complete', methods=['POST'])
def complete_upload():
    """Complete a chunked upload - combines chunks into a single file"""
    try:
        # Получаем данные из запроса
        try:
            data = request.get_json(force=True) 
            upload_id = data.get('uploadId')
            destination = data.get('destination', 'temp')  # Where to store the file
        except ValueError as e:
            app.logger.error(f"Invalid JSON in complete_upload request: {str(e)}")
            return jsonify({'error': 'Invalid JSON data', 'details': str(e)}), 400
        
        if not upload_id:
            app.logger.warning("Complete upload failed: Missing uploadId parameter")
            return jsonify({'error': 'Missing uploadId parameter', 'details': 'The uploadId field is required'}), 400
        
        # Verify upload directory exists
        upload_dir = UPLOADS_DIR / upload_id
        if not upload_dir.exists() or not upload_dir.is_dir():
            app.logger.warning(f"Complete upload failed: Upload directory not found for ID {upload_id}")
            return jsonify({'error': 'Upload not found or expired', 'details': 'The upload may have expired or was never started'}), 404
        
        # Check metadata
        meta_file = upload_dir / "metadata.json"
        if not meta_file.exists():
            app.logger.error(f"Complete upload failed: Metadata file missing for upload {upload_id}")
            return jsonify({'error': 'Upload metadata not found', 'details': 'The upload metadata is missing or corrupted'}), 404
        
        try:
            with meta_file.open('r') as f:
                meta = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            app.logger.error(f"Complete upload failed: Could not read metadata for upload {upload_id}: {str(e)}")
            return jsonify({'error': 'Metadata read error', 'details': str(e)}), 500
        
        filename = meta.get('filename')
        total_chunks = meta.get('total_chunks', 0)
        chunks_received = meta.get('chunks_received', [])
        
        # Проверяем полноту загрузки
        if len(chunks_received) != total_chunks:
            app.logger.warning(f"Complete upload requested but incomplete: {len(chunks_received)}/{total_chunks} chunks for {upload_id}")
            return jsonify({
                'error': 'Upload incomplete', 
                'details': f'Only {len(chunks_received)} of {total_chunks} chunks received',
                'received': len(chunks_received),
                'total': total_chunks
            }), 400
        
        app.logger.info(f"Combining {total_chunks} chunks for upload {upload_id}, filename: {filename}")
        
        # Combine chunks into a single file
        output_path = UPLOADS_DIR / f"{upload_id}_{filename}"
        
        try:
            with output_path.open('wb') as output:
                total_bytes = 0
                for i in range(total_chunks):
                    chunk_file = upload_dir / f"chunk_{i:05d}"
                    if not chunk_file.exists():
                        app.logger.error(f"Complete upload failed: Chunk {i} missing for upload {upload_id}")
                        return jsonify({'error': f'Chunk {i} missing', 'details': f'Chunk file {i} not found on server'}), 400
                    
                    try:
                        with chunk_file.open('rb') as chunk:
                            chunk_data = chunk.read()
                            output.write(chunk_data)
                            total_bytes += len(chunk_data)
                    except IOError as e:
                        app.logger.error(f"Complete upload failed: Error reading chunk {i} for upload {upload_id}: {str(e)}")
                        return jsonify({'error': f'Error reading chunk {i}', 'details': str(e)}), 500
                
                app.logger.info(f"Successfully combined {total_chunks} chunks, total size: {total_bytes} bytes")
        except IOError as e:
            app.logger.error(f"Complete upload failed: Error writing output file for upload {upload_id}: {str(e)}")
            return jsonify({'error': 'Error writing output file', 'details': str(e)}), 500
        
        # Calculate hash for verification
        try:
            file_hash = calculate_file_hash(output_path)
            file_size = output_path.stat().st_size
        except Exception as e:
            app.logger.error(f"Error calculating file hash for upload {upload_id}: {str(e)}")
            file_hash = "unknown"
            file_size = -1
        
        app.logger.info(f"Upload {upload_id} completed successfully: {filename}, size: {file_size} bytes, hash: {file_hash[:8]}...")
        
        # Return path/id for further processing
        return jsonify({
            'success': True,
            'tempFile': str(output_path.relative_to(BASE_DIR)),
            'filename': filename,
            'hash': file_hash,
            'size': file_size
        })
        
    except Exception as e:
        app.logger.error(f"Unexpected error completing upload: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Server error', 'details': str(e)}), 500


@app.route('/api/upload/status', methods=['GET'])
def upload_status():
    """Check status of a chunked upload"""
    try:
        upload_id = request.args.get('uploadId')
        
        if not upload_id:
            app.logger.warning("Upload status check failed: Missing uploadId parameter")
            return jsonify({
                'error': 'Missing uploadId parameter', 
                'details': 'The uploadId parameter must be provided in the query string'
            }), 400
        
        upload_dir = UPLOADS_DIR / upload_id
        meta_file = upload_dir / "metadata.json"
        
        # Проверка существования директории и метаданных
        if not upload_dir.exists():
            app.logger.warning(f"Upload status check failed: Upload directory not found for ID {upload_id}")
            return jsonify({
                'error': 'Upload not found', 
                'details': 'The specified upload ID does not exist or has expired'
            }), 404
            
        if not meta_file.exists():
            app.logger.warning(f"Upload status check failed: Metadata file missing for upload {upload_id}")
            return jsonify({
                'error': 'Upload metadata not found', 
                'details': 'The upload exists but metadata is missing or corrupted'
            }), 404
        
        # Чтение метаданных с обработкой возможных ошибок
        try:
            with meta_file.open('r') as f:
                meta = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            app.logger.error(f"Upload status check failed: Error reading metadata for {upload_id}: {str(e)}")
            return jsonify({
                'error': 'Metadata read error', 
                'details': f'Could not read upload metadata: {str(e)}'
            }), 500
        
        # Извлечение информации о загрузке
        filename = meta.get('filename', 'unknown')
        chunks_received = meta.get('chunks_received', [])
        total_chunks = meta.get('total_chunks', 0)
        start_time = meta.get('upload_start', 0)
        last_update = meta.get('last_update', 0)
        
        # Расчет статуса и прогресса
        received_count = len(chunks_received)
        is_complete = received_count == total_chunks
        percent_complete = int((received_count / max(total_chunks, 1)) * 100)
        
        duration = 0
        if start_time > 0:
            duration = int(time.time() - start_time)
        
        # Лог успешного запроса статуса
        app.logger.info(f"Upload status for {upload_id}: {received_count}/{total_chunks} chunks ({percent_complete}%), filename: {filename}")
        
        return jsonify({
            'filename': filename,
            'received': received_count,
            'total': total_chunks,
            'percent': percent_complete,
            'complete': is_complete,
            'duration': duration,
            'last_update': last_update,
            'upload_id': upload_id
        })
    except Exception as e:
        app.logger.error(f"Unexpected error checking upload status: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Server error', 'details': str(e)}), 500


@app.route('/api/upload/cleanup', methods=['POST'])
def cleanup_old_uploads():
    """Admin endpoint to clean up old uploads"""
    try:
        now = time.time()
        cleaned = 0
        failed = 0
        skipped = 0
        results = []
        
        app.logger.info(f"Starting cleanup of old uploads (TTL: {TEMP_UPLOAD_TTL} seconds)")
        
        # Проверяем существование директории загрузок
        if not UPLOADS_DIR.exists() or not UPLOADS_DIR.is_dir():
            app.logger.error(f"Uploads directory {UPLOADS_DIR} doesn't exist or is not a directory")
            return jsonify({
                'error': 'Uploads directory not found', 
                'details': f'The path {UPLOADS_DIR} is not a valid directory'
            }), 500
        
        # Получаем список всех элементов в директории
        try:
            items = list(UPLOADS_DIR.iterdir())
            app.logger.info(f"Found {len(items)} items in uploads directory")
        except PermissionError as e:
            app.logger.error(f"Permission error accessing uploads directory: {str(e)}")
            return jsonify({
                'error': 'Permission denied', 
                'details': f'Cannot access uploads directory: {str(e)}'
            }), 500
        except Exception as e:
            app.logger.error(f"Error accessing uploads directory: {str(e)}")
            return jsonify({
                'error': 'File system error', 
                'details': f'Cannot access uploads directory: {str(e)}'
            }), 500
        
        # Обрабатываем каждый элемент
        for upload_dir in items:
            # Пропускаем не-директории
            if not upload_dir.is_dir():
                skipped += 1
                continue
            
            dir_name = upload_dir.name
            meta_file = upload_dir / "metadata.json"
            result = {'id': dir_name, 'action': 'none'}
            
            try:
                # Проверяем метаданные
                if not meta_file.exists():
                    # Нет метаданных, удаляем если старее установленного срока
                    mod_time = upload_dir.stat().st_mtime
                    age = now - mod_time
                    
                    if age > TEMP_UPLOAD_TTL:
                        try:
                            shutil.rmtree(upload_dir)
                            app.logger.info(f"Deleted old directory without metadata: {dir_name} (age: {int(age)} seconds)")
                            cleaned += 1
                            result['action'] = 'deleted'
                            result['reason'] = 'no_metadata_expired'
                            result['age'] = int(age)
                        except (PermissionError, OSError) as e:
                            app.logger.error(f"Error deleting directory {dir_name}: {str(e)}")
                            failed += 1
                            result['action'] = 'failed'
                            result['error'] = str(e)
                    else:
                        # Ещё не истёк срок
                        app.logger.debug(f"Skipping directory without metadata (not expired): {dir_name} (age: {int(age)} seconds)")
                        skipped += 1
                        result['action'] = 'skipped'
                        result['reason'] = 'no_metadata_not_expired'
                        result['age'] = int(age)
                else:
                    # Есть метаданные, проверяем дату последнего обновления
                    try:
                        with meta_file.open('r') as f:
                            meta = json.load(f)
                        
                        # Получаем время последнего обновления
                        last_update = meta.get('last_update', 0)
                        age = now - last_update
                        chunks_total = meta.get('total_chunks', 0)
                        chunks_received = len(meta.get('chunks_received', []))
                        filename = meta.get('filename', 'unknown')
                        
                        # Записываем информацию в результат
                        result['filename'] = filename
                        result['chunks_total'] = chunks_total
                        result['chunks_received'] = chunks_received
                        result['last_update'] = last_update
                        result['age'] = int(age)
                        
                        if age > TEMP_UPLOAD_TTL:
                            try:
                                shutil.rmtree(upload_dir)
                                app.logger.info(f"Deleted expired upload: {dir_name} ({filename}) (age: {int(age)} seconds)")
                                cleaned += 1
                                result['action'] = 'deleted'
                                result['reason'] = 'expired'
                            except (PermissionError, OSError) as e:
                                app.logger.error(f"Error deleting directory {dir_name}: {str(e)}")
                                failed += 1
                                result['action'] = 'failed'
                                result['error'] = str(e)
                        else:
                            # Ещё не истёк срок
                            app.logger.debug(f"Skipping active upload (not expired): {dir_name} (age: {int(age)} seconds)")
                            skipped += 1
                            result['action'] = 'skipped'
                            result['reason'] = 'not_expired'
                    except (json.JSONDecodeError, IOError) as e:
                        # Ошибка чтения метаданных, проверяем время изменения директории
                        app.logger.warning(f"Error reading metadata for {dir_name}: {str(e)}")
                        mod_time = upload_dir.stat().st_mtime
                        age = now - mod_time
                        result['error'] = f"Metadata error: {str(e)}"
                        result['age'] = int(age)
                        
                        if age > TEMP_UPLOAD_TTL:
                            try:
                                shutil.rmtree(upload_dir)
                                app.logger.info(f"Deleted directory with corrupted metadata: {dir_name} (age: {int(age)} seconds)")
                                cleaned += 1
                                result['action'] = 'deleted'
                                result['reason'] = 'corrupted_metadata_expired'
                            except (PermissionError, OSError) as e:
                                app.logger.error(f"Error deleting directory {dir_name}: {str(e)}")
                                failed += 1
                                result['action'] = 'failed'
                                result['error'] = str(e)
                        else:
                            skipped += 1
                            result['action'] = 'skipped'
                            result['reason'] = 'corrupted_metadata_not_expired'
            except Exception as e:
                # Общая обработка ошибок для данной директории
                app.logger.error(f"Unexpected error processing directory {dir_name}: {str(e)}")
                failed += 1
                result['action'] = 'error'
                result['error'] = str(e)
            
            # Добавляем результат для этой директории
            results.append(result)
        
        # Финальный лог
        app.logger.info(f"Cleanup complete: {cleaned} deleted, {skipped} skipped, {failed} failed")
        
        # Возвращаем расширенный отчёт
        return jsonify({
            'success': True,
            'cleaned': cleaned,
            'skipped': skipped,
            'failed': failed,
            'results': results
        })
    except Exception as e:
        app.logger.error(f"Unexpected error in cleanup_old_uploads: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Server error', 'details': str(e)}), 500


def calculate_file_hash(file_path):
    """Calculate SHA-256 hash of a file"""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Settings page and API for user preferences"""
    # Default settings
    default_settings = {
        "chunked_upload_default": False
    }
    
    # Load current settings if they exist
    current_settings = default_settings.copy()
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r') as f:
                stored_settings = json.load(f)
                current_settings.update(stored_settings)
        except (json.JSONDecodeError, IOError) as e:
            app.logger.error(f"Error loading settings: {e}")
    
    # Handle POST request (API)
    if request.method == "POST":
        if request.is_json:
            try:
                new_settings = request.get_json()
                
                # Update settings
                if "chunked_upload_default" in new_settings:
                    current_settings["chunked_upload_default"] = bool(new_settings["chunked_upload_default"])
                
                # Save settings
                with open(SETTINGS_FILE, 'w') as f:
                    json.dump(current_settings, f, indent=2)
                
                return jsonify({"success": True})
            except Exception as e:
                app.logger.error(f"Error saving settings: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            return jsonify({"success": False, "error": "Invalid content type"}), 400
    
    # Handle GET request (page)
    return render_template("settings.html", **current_settings)


@app.route("/api/settings", methods=["GET"])
def api_settings():
    """API endpoint to get user settings"""
    # Default settings
    default_settings = {
        "chunked_upload_default": False
    }
    
    # Load current settings if they exist
    current_settings = default_settings.copy()
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r') as f:
                stored_settings = json.load(f)
                current_settings.update(stored_settings)
        except (json.JSONDecodeError, IOError) as e:
            app.logger.error(f"Error loading settings: {e}")
    
    return jsonify(current_settings)


@app.route("/api/shows")
def shows_api():
    """API endpoint to get list of shows"""
    shows = []
    for show_dir in sorted(SHOWS_DIR.iterdir()):
        if not show_dir.is_dir():
            continue
        show_id = show_dir.name
        show_path = SHOWS_DIR / show_id
        if not show_path.is_dir():
            continue
            
        config_path = show_path / "config.json"
        if not config_path.exists():
            continue
            
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            show_data = {
                'id': show_id,
                'title': config.get('title', ''),
                'description': config.get('description', ''),
                'language': config.get('language', 'en'),
                'image': config.get('image')
            }
            shows.append(show_data)
        except Exception as e:
            app.logger.error(f"Error loading show {show_id}: {e}")
            
    return jsonify(shows)


@app.route("/api/batch_upload/metadata")
def batch_metadata_api():
    """API endpoint to get metadata from Excel file based on language"""
    import pandas as pd
    
    # Получаем язык из параметров запроса
    language = request.args.get('language', 'english').lower()
    
    # Путь к файлу метаданных в зависимости от языка
    metadata_file = f'{language.capitalize()}.xlsx'
    file_path = os.path.join(BASE_DIR, metadata_file)
    
    if not os.path.exists(file_path):
        return jsonify({
            'error': f'Metadata file not found: {metadata_file}',
            'message': f'Place the {metadata_file} file in the root directory.'
        }), 404
    
    try:
        # Чтение файла Excel
        df = pd.read_excel(file_path)
        
        # Преобразование DataFrame в список словарей
        # Заменяем NaN/None на пустые строки, чтобы JSON был валидным
        metadata = []
        for _, row in df.iterrows():
            def safe_get(col):
                value = row.get(col, '')
                return value if pd.notna(value) else ''

            item = {
                'number': int(row['Book Number']) if pd.notna(row.get('Book Number')) else None,
                'title': safe_get('Title'),
                'author': safe_get('Author'),
                'description': safe_get('Description'),
                'about': safe_get("What's it about?"),
                'genre': safe_get('Genre')
            }
            metadata.append(item)
        
        return jsonify(metadata)
    except Exception as e:
        app.logger.error(f"Error processing metadata file: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
        

@app.route("/api/batch_upload/episodes", methods=["POST"])
def batch_upload_episodes():
    """API endpoint to handle batch upload of episodes"""
    if not request.is_json:
        return jsonify({'error': 'Invalid request format'}), 400
    
    try:
        data = request.get_json()
        episodes = data.get('episodes', [])
        language = data.get('language', 'english')
        
        if not episodes:
            return jsonify({'error': 'No episodes data provided'}), 400
            
        app.logger.info(f"Received batch upload request for {len(episodes)} episodes in {language}")
        
        # Результаты обработки каждого эпизода
        results = []
        
        # Обработка каждого эпизода
        for episode in episodes:
            # Проверка необходимых полей
            if not all(k in episode for k in ['showId', 'title', 'audioFile']):
                results.append({
                    'number': episode.get('number'),
                    'success': False,
                    'error': 'Missing required fields (showId, title or audioFile)'
                })
                continue
                
            # Данные эпизода
            show_id = episode['showId']
            title = episode['title']
            number = episode.get('number', 0)
            summary = episode.get('summary', '')
            description = episode.get('description', '')
            genres = episode.get('genres', [])
            
            # Проверка существования шоу
            show_dir_path = SHOWS_DIR / show_id
            if not show_dir_path.exists():
                results.append({
                    'number': number,
                    'success': False,
                    'error': f'Show {show_id} does not exist'
                })
                continue
                
            # Создание каталога эпизода
            episode_id = create_episode_id(title)
            episodes_dir = show_dir_path / "episodes"
            episodes_dir.mkdir(exist_ok=True)
            
            # Проверка уникальности ID
            if (episodes_dir / episode_id).exists():
                # Создаем уникальный ID с номером эпизода
                episode_id = f"{episode_id}-{number}"
                
                # Если и это существует, добавляем временную метку
                if (episodes_dir / episode_id).exists():
                    timestamp = int(time.time())
                    episode_id = f"{episode_id}-{timestamp}"
            
            # Окончательный путь к каталогу эпизода
            episode_dir = episodes_dir / episode_id
            episode_dir.mkdir(exist_ok=True)
            
            # Создание конфигурации эпизода
            episode_config = {
                "title": title,
                "summary": summary,
                "description": plain_text_to_html(description) if description else "",
                "number": number,
                "genres": genres,
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "audio": None,  # Будет заполнено позже
                "image": None   # Будет заполнено позже
            }
            
            # Сохранение конфигурации
            with open(episode_dir / "config.json", "w", encoding="utf-8") as f:
                json.dump(episode_config, f, ensure_ascii=False, indent=2)
                
            # --- Обработка медиафайлов и обновление метаданных ---
            audio_temp = episode.get('audioFile') or episode.get('tempFile')
            cover_temp = episode.get('coverFile')

            # Базовая структура метаданных для metadata.json
            meta = {
                "title": title,
                "summary": summary,
                "description": plain_text_to_html(description) if description else "",
                "number": number,
                "genres": genres,
                "published_at": episode_config["published_at"],
                "conversion_status": "pending",
            }

            # === AUDIO ===
            if audio_temp:
                audio_src = (BASE_DIR / audio_temp.lstrip('/')).resolve()
                if audio_src.exists():
                    audio_filename = secure_filename(Path(audio_src).name)
                    dest_audio_path = episode_dir / audio_filename
                    try:
                        shutil.move(str(audio_src), str(dest_audio_path))
                    except Exception:
                        # fall back to copy if move fails (e.g., cross-device)
                        shutil.copy2(str(audio_src), str(dest_audio_path))
                    # clean up tmp upload directory if applicable
                    try:
                        if audio_src.parent.parent == UPLOADS_DIR:
                            shutil.rmtree(audio_src.parent, ignore_errors=True)
                    except Exception:
                        pass
                    audio_url = f"/shows/{show_id}/episodes/{episode_id}/{audio_filename}"
                    episode_config["audio"] = audio_url
                    meta["audio"] = audio_url
                    meta["conversion_status"] = "processing"
                    # стартуем фоновую обработку (транскодирование, теги и т.д.)
                    threading.Thread(target=process_audio_background, args=(str(dest_audio_path), show_id, episode_id)).start()
                else:
                    app.logger.error(f"[batch] Audio temp file not found: {audio_temp}")

            # === COVER IMAGE ===
            if cover_temp:
                cover_src = (BASE_DIR / cover_temp.lstrip('/')).resolve()
                if cover_src.exists():
                    cover_filename = secure_filename(Path(cover_src).name)
                    dest_cover_path = episode_dir / cover_filename
                    try:
                        shutil.move(str(cover_src), str(dest_cover_path))
                    except Exception:
                        shutil.copy2(str(cover_src), str(dest_cover_path))
                    try:
                        processed_path = resize_cover_image(dest_cover_path)
                        dest_cover_path = processed_path
                        cover_filename = processed_path.name
                    except Exception as exc:
                        app.logger.error(f"[batch] Failed to resize cover image for {episode_id}: {exc}")
                    cover_url = f"/shows/{show_id}/episodes/{episode_id}/{cover_filename}"
                    episode_config["image"] = cover_url
                    meta["episode_image"] = cover_url
                else:
                    app.logger.error(f"[batch] Cover temp file not found: {cover_temp}")

            # Сохраняем обновлённый config.json
            try:
                with open(episode_dir / "config.json", "w", encoding="utf-8") as f:
                    json.dump(episode_config, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                app.logger.error(f"[batch] Failed to write config.json for {episode_id}: {exc}")

            # Сохраняем metadata.json (используется процессом обработки и фронтом)
            try:
                with open(episode_dir / "metadata.json", "w", encoding="utf-8") as mf:
                    json.dump(meta, mf, ensure_ascii=False, indent=2)
            except Exception as exc:
                app.logger.error(f"[batch] Failed to write metadata.json for {episode_id}: {exc}")
            
            results.append({
                'number': number,
                'success': True,
                'episode_id': episode_id,
                'show_id': show_id,
                'message': f'Episode {title} created successfully'
            })
            
        return jsonify({
            'success': True,
            'language': language,
            'total': len(episodes),
            'created': sum(1 for r in results if r.get('success')),
            'failed': sum(1 for r in results if not r.get('success')),
            'results': results
        })
        
    except Exception as e:
        app.logger.error(f"Error in batch_upload_episodes: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e), 'success': False}), 500


# Функция для создания ID эпизода на основе названия
def create_episode_id(title):
    """Создает URL-безопасный ID эпизода из названия"""
    # Преобразуем в нижний регистр
    slug = title.lower()
    
    # Заменяем специальные символы на дефис
    import re
    slug = re.sub(r'[^\w\s-]', '', slug)
    
    # Заменяем пробелы на дефисы
    slug = re.sub(r'[\s]+', '-', slug.strip())
    
    # Ограничиваем длину
    return slug[:50]
    
    # Handle POST request (API)
    if request.method == "POST":
        if request.is_json:
            try:
                new_settings = request.get_json()
                
                # Update settings
                if "chunked_upload_default" in new_settings:
                    current_settings["chunked_upload_default"] = bool(new_settings["chunked_upload_default"])
                
                # Save settings
                with open(SETTINGS_FILE, 'w') as f:
                    json.dump(current_settings, f, indent=2)
                
                return jsonify({"success": True})
            except Exception as e:
                app.logger.error(f"Error saving settings: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            return jsonify({"success": False, "error": "Invalid content type"}), 400
    
    # Handle GET request (page)
    return render_template("settings.html", **current_settings)


# Initialize batch upload routes
try:
    from batch_upload import init_batch_upload_routes
    init_batch_upload_routes(app)
    app.logger.info("Batch upload routes initialized successfully")
except ImportError as e:
    app.logger.warning(f"Could not initialize batch upload routes: {e}")
    # If pandas is missing, provide helpful message
    if "pandas" in str(e):
        app.logger.warning("Please install pandas: pip install pandas")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
