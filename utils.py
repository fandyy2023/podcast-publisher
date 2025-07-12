"""Utility functions for Podcast Publisher."""
from __future__ import annotations

import os
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional
from PIL import Image

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def load_env(env_path: Optional[Path] = None) -> None:
    """Load environment variables from a .env file if present."""
    env_file = env_path or Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)
        logger.debug("Loaded environment variables from %s", env_file)
    else:
        logger.debug("No .env file found at %s", env_file)


def generate_guid() -> str:
    """Generate a unique GUID for an episode item."""
    return str(uuid.uuid4())


MIN_PODCAST_BITRATE = 160  # kbps – minimum recommended for podcast platforms


def resize_cover_image(image_path: Path, min_size: int = 1400, max_size: int = 3000, background_color: tuple[int, int, int] = (0, 0, 0)) -> Path:
    """Resize an image in-place so it complies with Apple/Spotify podcast cover requirements.

    The function ensures:
    1. The image is square (1:1). If not, the shorter side is padded with
       *background_color* so that the result is square.
    2. The resulting side length is between *min_size* and *max_size* pixels.
       If the image is larger than *max_size* it is down-scaled; if smaller than
       *min_size* it is up-scaled (though platforms discourage up-scaling, it is
       sometimes unavoidable for legacy artwork).

    The image is saved back to *image_path* with high-quality settings. The file
    extension dictates the output format (PNG for .png, JPEG otherwise).
    Returns the same *image_path* for convenience.
    """

    try:
        with Image.open(image_path) as img:
            # Convert to RGB to drop any alpha for JPEG; keep transparency only for PNG.
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")

            width, height = img.size

            # Step 1: pad to square if needed
            if width != height:
                side = max(width, height)
                square = Image.new("RGB", (side, side), background_color)
                paste_x = (side - width) // 2
                paste_y = (side - height) // 2
                square.paste(img, (paste_x, paste_y))
                img = square
                width = height = side

            # Step 2: scale to fit min/max bounds
            target_side = width  # currently equals height
            if target_side > max_size:
                target_side = max_size
            elif target_side < min_size:
                target_side = min_size

            if target_side != width:
                img = img.resize((target_side, target_side), Image.LANCZOS)

            # Step 3: primary save (PNG keeps PNG, others → JPEG)
            ext = image_path.suffix.lower()
            # Prefer JPEG for podcast artwork to keep size low
            target_ext = ".jpg" if ext in (".png", ".webp") else ext
            save_path = image_path if target_ext == ext else image_path.with_suffix(".jpg")

            # Initial save
            if target_ext == ".jpg":
                img.save(save_path, format="JPEG", quality=95, optimize=True, progressive=True)
            else:
                img.save(save_path, format="PNG", optimize=True)

            # If file is still larger than 512 KB, iteratively lower JPEG quality
            if save_path.stat().st_size > 512_000:
                if target_ext != ".jpg":
                    # Convert PNG → JPEG first (quality 90) to drastically reduce size
                    save_path = image_path.with_suffix(".jpg")
                    img.save(save_path, format="JPEG", quality=90, optimize=True, progressive=True)
                # Now ensure ≤512 KB by reducing quality in 5-step decrements
                quality = 90
                while save_path.stat().st_size > 512_000 and quality >= 65:
                    quality -= 5
                    img.save(save_path, format="JPEG", quality=quality, optimize=True, progressive=True)

            # Remove original file if we changed extension
            if save_path != image_path:
                try:
                    image_path.unlink(missing_ok=True)  # py>=3.8
                except Exception:
                    pass
                image_path = save_path

        logger.info("Resized cover image %s → %dx%d px, size %d KB", image_path.name, target_side, target_side, image_path.stat().st_size // 1024)
    except Exception as exc:
        logger.error("Failed to resize cover %s: %s", image_path, exc)
        raise

    return image_path

def select_mp3_bitrate(source_bitrate_kbps: int) -> str:
    """Return an MP3 CBR bitrate string (e.g. "192k") that is not lower than
    the source bitrate and does not exceed 320 kbps.

    The bitrate is rounded up to the next standard LAME step so that we never
    downgrade quality.  Falls back to the default "192k" if the source bitrate
    is unknown or unreasonable.
    """
    # Standard CBR presets supported by LAME/ffmpeg (kbps)
    standard_rates = [32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]

    # Basic validation
    try:
        src = int(source_bitrate_kbps)
    except (ValueError, TypeError):
        return "192k"

    if src <= 0:
        return f"{MIN_PODCAST_BITRATE}k"  # Fallback to minimum recommended bitrate

    # Ensure we never choose below the platform minimum
    effective_src = max(src, MIN_PODCAST_BITRATE)

    # Pick the first standard rate that is >= effective source bitrate
    for rate in standard_rates:
        if effective_src <= rate:
            return f"{rate}k"

    # If higher than the max supported value, cap at 320 kbps
    return "320k"


def transcode_audio_to_mp3(
    source: Path,
    bitrate: str = "192k",
    *,
    metadata: Optional[dict[str, str]] = None,
    cover_path: Optional[Path] = None,
) -> Path:
    """Convert an audio *source* file to MP3 (CBR) using **ffmpeg** and embed full
ID3v2.3 metadata.

Parameters
----------
source : Path
    Input audio file (any container supported by ffmpeg).
bitrate : str, default "192k"
    Target CBR bitrate string recognised by ffmpeg (e.g. "192k").
metadata : dict[str, str] | None
    Optional mapping of ID3 keys → values (e.g. {"title": "…", "artist": "…"}).
    Arbitrary keys are accepted because ffmpeg writes unknown keys either as
    standard frames (if recognised) or as TXXX frames.
cover_path : Path | None
    Optional image to embed as *front cover* (`APIC`) – must be PNG/JPEG.

Returns
-------
Path
    Path to the generated MP3 file; *source* is removed on success.
"""
    import subprocess

    target = source.with_suffix(".mp3")
    ffmpeg_path = "/opt/homebrew/bin/ffmpeg"

    logger.info("--- TRANSCODE START ---")
    logger.info("Source: %s", source)
    logger.info("Target: %s", target)
    logger.info("Bitrate: %s", bitrate)

    command = [
        ffmpeg_path,
        '-v', 'quiet',  # Work quietly – we log ourselves
        '-i', str(source),  # Audio input
        # Optional 2-nd input (cover art)
]
    if cover_path is not None and cover_path.exists():
        command += ['-i', str(cover_path)]

    # Audio encoding options
    command += [
        '-ac', '2',  # Stereo
        '-ar', '44100',  # 44.1 kHz (CD-quality)
        '-c:a', 'libmp3lame',
        '-b:a', bitrate,
        '-compression_level', '0',  # Fastest CBR
        '-abr', 'false',
    ]

    # If we provided a cover we need correct mapping
    if cover_path is not None and cover_path.exists():
        command += ['-map', '0:a', '-map', '1:v', '-c:v', 'copy']

    # Embed metadata frames
    if metadata:
        for k, v in metadata.items():
            if v is None:
                continue
            command += ['-metadata', f'{k}={v}']

    # Tagging options – force ID3v2.3 + keep legacy ID3v1
    command += ['-id3v2_version', '3', '-write_id3v1', '1']

    # Overwrite output if exists and specify destination
    command += ['-y', str(target)]

    try:
        # Запускаем ffmpeg
        result = subprocess.run(
            command, 
            check=True, 
            capture_output=True, 
            text=True,
            timeout=300 # Таймаут 5 минут
        )
        logger.debug("ffmpeg stdout: %s", result.stdout)
        logger.debug("ffmpeg stderr: %s", result.stderr)
        
        # Если команда успешна, удаляем исходный файл
        os.remove(source)
        logger.info("Successfully transcoded and removed original file: %s", source.name)
        return target

    except FileNotFoundError:
        logger.error("ffmpeg not found at path: %s", ffmpeg_path)
        raise RuntimeError(f"ffmpeg не найден по пути: {ffmpeg_path}")
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg failed! Return code: %s", e.returncode)
        logger.error("ffmpeg stdout: %s", e.stdout)
        logger.error("ffmpeg stderr: %s", e.stderr)
        raise RuntimeError(f"Ошибка конвертации файла: {e.stderr}")
    except Exception as e:
        logger.error("An unexpected error occurred during transcoding: %s", e, exc_info=True)
        raise RuntimeError(f"Неожиданная ошибка во время конвертации: {e}")


def has_id3v2_tags(file_path: Path) -> bool:
    """Check whether a file starts with an ID3v2 header (first 3 bytes == 'ID3')."""
    try:
        with open(file_path, "rb") as f:
            return f.read(3) == b"ID3"
    except Exception as exc:
        logger.warning("Failed to read file for ID3 check: %s", exc)
        return False


def embed_id3_metadata_mp3(
    source: Path,
    *,
    metadata: Optional[dict[str, str]] = None,
    cover_path: Optional[Path] = None,
) -> Path:
    """Embed ID3v2.3 metadata and optional cover art into an existing MP3 without re-encoding.

    The function performs a *stream copy* so the audio content is preserved bit-for-bit.
    A temporary file is created next to *source* and atomically replaces it upon
    success.
    """
    if source.suffix.lower() != ".mp3":
        raise ValueError("embed_id3_metadata_mp3 expects an MP3 file")

    ffmpeg_path = "/opt/homebrew/bin/ffmpeg"
    tmp_target = source.with_name(source.stem + "_id3tmp.mp3")

    command = [ffmpeg_path, "-v", "quiet", "-i", str(source)]

    if cover_path and cover_path.exists():
        command += ["-i", str(cover_path), "-map", "0:a", "-map", "1:v", "-c:v", "copy"]
    else:
        command += ["-map", "0:a"]

    command += ["-c:a", "copy"]  # Stream-copy audio without re-encoding

    # Embed metadata frames
    if metadata:
        for k, v in metadata.items():
            if v is None:
                continue
            command += ["-metadata", f"{k}={v}"]

    command += ["-id3v2_version", "3", "-write_id3v1", "1", "-y", str(tmp_target)]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        # Replace original atomically
        os.replace(tmp_target, source)
        logger.info("Embedded ID3 metadata into %s", source.name)
        return source
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg failed while embedding tags: %s", e.stderr)
        if tmp_target.exists():
            tmp_target.unlink(missing_ok=True)
        raise RuntimeError("Не удалось добавить ID3-теги в файл MP3") from e
    except Exception as exc:
        logger.error("Unexpected error while embedding ID3: %s", exc, exc_info=True)
        if tmp_target.exists():
            tmp_target.unlink(missing_ok=True)
        raise


import json
import math

def get_audio_info(file_path: Path) -> dict:
    """
    Get audio file metadata using ffprobe.
    Returns a dictionary with filename, bitrate, size, duration, and format.
    """
    if not file_path.exists():
        logger.error("Audio file not found for info: %s", file_path)
        return {}

    ffprobe_path = "/opt/homebrew/bin/ffprobe"

    command = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=60
        )
        data = json.loads(result.stdout)

        def format_size(size_bytes):
            if size_bytes == 0:
                return "0 B"
            size_name = ("B", "KB", "MB", "GB", "TB")
            i = int(math.floor(math.log(size_bytes, 1024)))
            p = math.pow(1024, i)
            s = round(size_bytes / p, 2)
            return f"{s} {size_name[i]}"

        def format_duration(seconds_str):
            try:
                seconds = float(seconds_str)
            except (ValueError, TypeError):
                return "00:00"
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            seconds = int(seconds % 60)
            if hours > 0:
                return f"{hours:02}:{minutes:02}:{seconds:02}"
            return f"{minutes:02}:{seconds:02}"

        format_info = data.get("format", {})
        audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

        bit_rate_kbps = "N/A"
        if 'bit_rate' in format_info:
            bit_rate_kbps = f"{int(int(format_info['bit_rate']) / 1000)} kbps"
        elif audio_stream and 'bit_rate' in audio_stream:
            bit_rate_kbps = f"{int(int(audio_stream['bit_rate']) / 1000)} kbps"

        size_in_bytes = int(format_info.get("size", 0))
        # Extract additional technical metadata
        sample_rate_hz = None
        channel_count = None
        if audio_stream:
            # sample_rate and channels may be strings, convert safely
            sr_raw = audio_stream.get("sample_rate")
            ch_raw = audio_stream.get("channels")
            try:
                sample_rate_hz = int(sr_raw) if sr_raw is not None else None
            except (ValueError, TypeError):
                sample_rate_hz = None
            try:
                channel_count = int(ch_raw) if ch_raw is not None else None
            except (ValueError, TypeError):
                channel_count = None

        # Raw duration in seconds (as float) for easier maths on frontend
        try:
            duration_seconds = float(format_info.get("duration", "0"))
        except (ValueError, TypeError):
            duration_seconds = None

        info = {
            "filename": file_path.name,
            "bitrate": bit_rate_kbps,
            "size": format_size(size_in_bytes),
            "size_bytes": size_in_bytes,
            "duration": format_duration(format_info.get("duration", "0")),
            "duration_seconds": duration_seconds,
            "samplerate": sample_rate_hz,
            "channels": channel_count,
            "format": format_info.get("format_name", "N/A"),
        }
        return info

    except FileNotFoundError:
        logger.error("ffprobe not found at path: %s", ffprobe_path)
        return {}
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        logger.error("ffprobe failed for file %s: %s", file_path, e)
        return {}
    except Exception as e:
        logger.error("An unexpected error occurred during get_audio_info for %s: %s", file_path, e, exc_info=True)
        return {}


import bleach

import re

def plain_text_to_html(text: str) -> str:
    """
    Преобразует обычный текст с переносами строк и списками в HTML с сохранением структуры:
    - Нумерованные списки (1., 2., 3., ...) → <ol><li>...
    - Маркированные списки (•, -, *) → <ul><li>...
    - Абзацы → <p>...
    """
    import re
    
    # Предварительный анализ структуры текста
    lines = text.splitlines()
    result = []
    
    # Стек для отслеживания текущих открытых тегов
    tag_stack = []
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Пропускаем пустые строки
        if not line:
            # Закрываем все открытые теги списков
            while tag_stack and tag_stack[-1] in ('ul', 'ol', 'li'):
                result.append(f'</{tag_stack.pop()}>')
            i += 1
            continue
            
        # Обработка нумерованного списка (1., 2., ...)
        if re.match(r'^\s*\d+\.\s+', line):
            num_match = re.match(r'^\s*(\d+)\.\s+(.*)', line)
            num, content = num_match.groups()
            
            # Закрываем предыдущий пункт списка, если был
            if tag_stack and tag_stack[-1] == 'li':
                result.append('</li>')
                tag_stack.pop()
            
            # Если не в нумерованном списке, открываем его
            if not (tag_stack and tag_stack[-1] == 'ol'):
                # Закрываем предыдущий список, если был
                if tag_stack and tag_stack[-1] in ('ul', 'ol'):
                    result.append(f'</{tag_stack.pop()}>')
                
                result.append('<ol>')
                tag_stack.append('ol')
            
            # Добавляем пункт списка
            result.append(f'<li>{content}')
            tag_stack.append('li')
            
            # Анализируем следующие строки на наличие вложенных списков
            nested_i = i + 1
            has_nested_bullet = False
            
            while nested_i < len(lines):
                nested_line = lines[nested_i].strip()
                
                if not nested_line:
                    break
                
                # Проверяем на маркированный список
                bullet_match = re.match(r'^\s*([\u2022\-\*]|•)\s+(.*)', nested_line)
                next_num_match = re.match(r'^\s*\d+\.(\s+)(.*)', nested_line)
                
                if bullet_match:
                    if not has_nested_bullet:
                        result.append('<ul>')
                        tag_stack.append('ul')
                        has_nested_bullet = True
                    
                    _, bullet_content = bullet_match.groups()
                    result.append(f'<li>{bullet_content}</li>')
                    nested_i += 1
                elif next_num_match:
                    # Если другой нумерованный пункт, выходим
                    break
                else:
                    # Обычный текст внутри пункта
                    result.append(f'<br>{nested_line}')
                    nested_i += 1
            
            if has_nested_bullet:
                result.append('</ul>')
                tag_stack.pop()  # Убираем 'ul'
            
            i = nested_i
            continue
            
        # Обработка маркированных списков верхнего уровня
        elif re.match(r'^\s*([\u2022\-\*]|•)\s+', line):
            bullet_match = re.match(r'^\s*([\u2022\-\*]|•)\s+(.*)', line)
            _, content = bullet_match.groups()
            
            # Закрываем предыдущий пункт, если был
            if tag_stack and tag_stack[-1] == 'li':
                result.append('</li>')
                tag_stack.pop()
            
            # Если не в маркированном списке, открываем его
            if not (tag_stack and tag_stack[-1] == 'ul'):
                # Закрываем предыдущий список, если был
                if tag_stack and tag_stack[-1] in ('ul', 'ol'):
                    result.append(f'</{tag_stack.pop()}>')
                
                result.append('<ul>')
                tag_stack.append('ul')
            
            # Добавляем пункт списка
            result.append(f'<li>{content}</li>')
        else:
            # Обычный текст - абзац
            while tag_stack and tag_stack[-1] in ('ul', 'ol', 'li'):
                result.append(f'</{tag_stack.pop()}>')
            
            result.append(f'<p>{line}</p>')
        
        i += 1
    
    # Закрываем все оставшиеся открытые теги
    while tag_stack:
        result.append(f'</{tag_stack.pop()}>')
    
    return '\n'.join(result)

def html_to_plain_text(html: str) -> str:
    """
    Преобразует HTML с <p>, <ul>, <ol>, <li>, <br> обратно в plain text с переносами строк и списками.
    Используется для обратного преобразования description при открытии редактора.
    """
    import re
    from html import unescape
    # Удаляем все теги кроме p, ul, ol, li, br
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</p>|</li>|</ul>|</ol>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<li>', '\n• ', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    text = unescape(html)
    # Удаляем лишние пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def sanitize_html_for_rss(html: str) -> str:
    """Return a UTF-8/validator-friendly HTML snippet for RSS descriptions.

    • Keeps only a safe subset of tags (`<p>`, `<ul>`, `<ol>`, `<li>`, `<a href>`).
    • Removes style / class / id attributes and any disallowed markup via **bleach**.
    • Strips ASCII control characters (0x00-0x1F except \t,\n,\r) that some
      validators flag as *non-UTF-8* even though they are technically valid bytes.
    • Normalises Unicode to NFC and converts problematic whitespace (non-breaking
      space, narrow nbsp, zero-width space) to ordinary spaces or nothing.
    • Replaces common typographic quotes with plain counterparts so feeds render
      identically across podcast apps.
    """
    if not html:
        return ""

    import unicodedata
    import re

    # 1. Unicode normalisation – combine composed characters consistently
    html = unicodedata.normalize("NFC", str(html))

    # 2. Replace problematic invisible / whitespace code-points *before* bleaching
    # NBSPs → space, zero-width → removed, line/paragraph separators → newline
    transl_map = {
        "\u00A0": " ",   # NO-BREAK SPACE
        "\u202F": " ",   # NARROW NBSP
        "\u200B": "",    # ZERO WIDTH SPACE
        "\u2028": "\n", # LINE SEPARATOR
        "\u2029": "\n", # PARAGRAPH SEPARATOR
        "\uFEFF": "",    # ZERO WIDTH NO-BREAK SPACE / BOM
        "\u2018": "'", "\u2019": "'",  # left/right single quotes
        "\u201C": '"', "\u201D": '"',  # left/right double quotes
        "\u201A": ',',  "\u201E": '"',  # single low-9 quote etc.
    }
    for bad, good in transl_map.items():
        html = html.replace(bad, good)

    # 3. Strip ASCII control chars except the allowed whitespace (TAB/CR/LF)
    html = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", html)

    # 4. Clean with bleach – allow only safe tags/attrs
    allowed_tags = ["p", "ul", "ol", "li", "a"]
    allowed_attrs = {"a": ["href"]}
    cleaned = bleach.clean(
        html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        strip=True,
    )

    # 5. Post-processing tweaks – decouple leftover HTML entities, smart quotes
    cleaned = (
        cleaned.replace("&nbsp;", " ")
               .replace("&rsquo;", "'")
               .replace("&lsquo;", "'")
               .replace("&ldquo;", '"')
               .replace("&rdquo;", '"')
    )

    # 6. Final normalise and strip extraneous whitespace
    cleaned = unicodedata.normalize("NFC", cleaned).strip()
    return cleaned

def send_email(subject: str, body: str) -> None:
    """Send a notification email using SMTP credentials from environment variables."""
    import smtplib
    from email.mime.text import MIMEText

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("EMAIL_TO")

    if not all([smtp_host, smtp_user, smtp_password, email_to]):
        logger.warning("SMTP credentials incomplete; email not sent")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = email_to

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [email_to], msg.as_string())
            logger.info("Email sent to %s", email_to)
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
