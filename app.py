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
from utils import (
    transcode_audio_to_mp3,
    get_audio_info,
    sanitize_html_for_rss,
    plain_text_to_html,
    html_to_plain_text,
    select_mp3_bitrate,
    MIN_PODCAST_BITRATE,
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
    return send_from_directory(ep_dir, filename, mimetype=mimetype, conditional=True)


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


@app.route("/api/episode_info/<show_id>/<episode_id>")
def episode_info(show_id, episode_id):
    show_dir = SHOWS_DIR / show_id
    ep_dir = show_dir / "episodes" / episode_id
    ep_dir = show_dir / "episodes" / ep_id
    meta_path = ep_dir / "metadata.json"

    if not meta_path.exists():
        abort(404)

    with meta_path.open("r", encoding="utf-8") as f:
        episode_meta = json.load(f)

    config_path = show_dir / "config.json"
    with config_path.open("r", encoding="utf-8") as f:
        show_meta = json.load(f)

    if not episode_meta.get("audio"):
        audio_exts = ("mp3", "wav", "m4a", "ogg", "flac", "aac")
        for f in ep_dir.iterdir():
            if f.is_file() and f.suffix.lower().lstrip('.') in audio_exts:
                episode_meta["audio"] = f"/shows/{show_id}/episodes/{ep_id}/{f.name}"
                break
    
    audio_info = {}
    if episode_meta.get("audio"):
        parts = episode_meta["audio"].split('/')
        if len(parts) >= 6:
            audio_path = Path(episode_meta["audio"]).name
            audio_info = get_audio_info(ep_dir / audio_path)

    return render_template("episode.html", show=show_meta, episode=episode_meta, audio_info=audio_info, show_id=show_id, ep_id=ep_id)


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
                # Удаляем все старые cover.*
                for old_file in show_dir.glob('cover.*'):
                    try:
                        old_file.unlink()
                    except Exception:
                        pass
                # Сохраняем новый файл
                image.save(str(show_dir / f"cover.{ext}"))
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
    from flask import Response, request, url_for, abort

    base_url = request.url_root.rstrip('/')

    # Load show config
    show_dir = SHOWS_DIR / show_id
    config_path = show_dir / "config.json"
    if not config_path.exists():
        abort(404)
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    # Helper functions
    def normalize_explicit(val):
        v = str(val).strip().lower()
        if v in ("yes", "true", "explicit", "да", "y", "1"):
            return "yes"
        return "no"

    def cdata_or_escape(text):
        if not text:
            return ''
        if any(x in text for x in ['&', '<', '>']):
            return f'<![CDATA[{text}]]>'
        return html.escape(text)

    items = []

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
            description = meta.get("description", cfg.get('description', ''))
            pubdate_str = meta.get("pubdate")
            try:
                dt_obj = datetime.datetime.fromisoformat(pubdate_str)
                pubdate = dt_obj.strftime("%a, %d %b %Y %H:%M:%S GMT")
            except (ValueError, TypeError, AttributeError):
                pubdate = datetime.datetime.fromtimestamp(ep_dir.stat().st_mtime).strftime("%a, %d %b %Y %H:%M:%S GMT")

            duration_str = meta.get('duration', '')
            enclosure_length = meta.get('size_bytes', 0)
            audio_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=f'episodes/{ep_dir.name}/{audio_file.name}')}"
            episode_link = f"{base_url}{url_for('edit_episode', show_id=show_id, ep_id=ep_dir.name)}"
            
            ep_image_url = None
            if meta.get("episode_image"):
                img_name = Path(meta["episode_image"]).name
                ep_image_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=f'episodes/{ep_dir.name}/{img_name}')}"

            ep_summary = meta.get("summary", description)
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
            {f'<itunes:duration>{duration_str}</itunes:duration>' if duration_str else ''}
            <itunes:explicit>{normalize_explicit(meta.get("explicit"))}</itunes:explicit>
        </item>''')

    # Find show cover
    cover_url = None
    if cfg.get('image'):
        img_path = show_dir / cfg['image']
        if img_path.exists():
            cover_url = f"{base_url}{url_for('show_file', show_id=show_id, filename=cfg['image'])}"

    # Assemble channel-level info
    channel_link = f"{base_url}{url_for('show_page', show_id=show_id)}"
    atom_url = f"{base_url}{url_for('show_feed_xml', show_id=show_id)}"
    itunes_author = cfg.get('author')
    itunes_explicit = normalize_explicit(cfg.get('explicit'))
    itunes_owner_name = cfg.get('owner_name')
    itunes_owner_email = cfg.get('owner_email')
    itunes_summary = cfg.get('summary', cfg.get('description', ''))
    itunes_owner = f'<itunes:owner><itunes:name>{cdata_or_escape(itunes_owner_name)}</itunes:name><itunes:email>{itunes_owner_email}</itunes:email></itunes:owner>' if itunes_owner_name and itunes_owner_email else ''
    now_gmt = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    copyright_val = cfg.get('copyright', f" 2025 {itunes_author or cfg.get('title')}")
    itunes_subtitle = cfg.get('subtitle', '')
    
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
        image_block = f'''
    <image>
      <url>{cover_url}</url>
      <title>{cdata_or_escape(cfg.get('title', show_id))}</title>
      <link>{channel_link}</link>
    </image>
    <itunes:image href="{cover_url}" />'''

    # Final RSS assembly
    rss = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
    <title>{cdata_or_escape(cfg.get('title', show_id))}</title>
    <link>{channel_link}</link>
    <atom:link href="{atom_url}" rel="self" type="application/rss+xml"/>
    <description>{cdata_or_escape(cfg.get('description', ''))}</description>
    <language>{cfg.get('language', 'en-US')}</language>
    <copyright>{cdata_or_escape(copyright_val)}</copyright>
    <lastBuildDate>{now_gmt}</lastBuildDate>
    <itunes:author>{cdata_or_escape(itunes_author)}</itunes:author>
    <itunes:summary>{cdata_or_escape(itunes_summary)}</itunes:summary>
    {itunes_owner}
    <itunes:explicit>{itunes_explicit}</itunes:explicit>
    {itunes_cat}
    {image_block}
    {''.join(items)}
</channel>
</rss>'''
    return Response(rss, mimetype="application/rss+xml")

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
    # Если filename содержит '/', это файл эпизода: <episode_id>/<audio_filename>
    if '/' in filename:
        episode_id, audio_filename = filename.split('/', 1)
        episode_dir = SHOWS_DIR / show_id / 'episodes' / episode_id
        file_path = episode_dir / audio_filename
        if not file_path.exists() or not file_path.is_file():
            abort(404)
        return send_from_directory(episode_dir, audio_filename)
    else:
        # Это файл шоу (например, cover.png)
        show_dir = SHOWS_DIR / show_id
        file_path = show_dir / filename
        if not file_path.exists() or not file_path.is_file():
            abort(404)
        return send_from_directory(show_dir, filename)


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(ASSETS_DIR, "favicon.ico")

from werkzeug.utils import secure_filename
import uuid



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

            if not needs_transcoding:
                app.logger.info(f"[BG] No transcoding needed.")
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
                new_path_str = transcode_audio_to_mp3(audio_path, bitrate=target_bitrate)
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
            episode_image.save(str(ep_dir / img_name))
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
        else:
            flash("Аудиофайл обязателен для создания эпизода.", "error")
            shutil.rmtree(ep_dir)
            return render_template("new_episode.html", show_id=show_id, msg=msg)

        # Этот блок был перемещен выше, чтобы исправить race condition
        flash("Эпизод успешно создан!", "success")
        return redirect(url_for("show_page", show_id=show_id))
    return render_template("new_episode.html", msg=msg)

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
            episode_image.save(str(ep_dir / img_name))
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
import traceback

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
    file.save(str(show_dir / img_name))
    url = f"/shows/{show_id}/{img_name}?v={int(Path(show_dir / img_name).stat().st_mtime)}"
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
    file.save(str(ep_dir / img_name))
    url = f"/shows/{show_id}/episodes/{ep_id}/{img_name}?v={int(Path(ep_dir / img_name).stat().st_mtime)}"
    return jsonify({"image_url": url})

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
