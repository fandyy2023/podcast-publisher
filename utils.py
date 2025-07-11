"""Utility functions for Podcast Publisher."""
from __future__ import annotations

import os
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

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


def transcode_audio_to_mp3(source: Path, bitrate: str = "192k") -> Path:
    """Convert any audio file to MP3 using ffmpeg directly. Deletes source, returns path to new file."""
    import subprocess

    target = source.with_suffix(".mp3")
    ffmpeg_path = "/opt/homebrew/bin/ffmpeg"

    logger.info("--- TRANSCODE START ---")
    logger.info("Source: %s", source)
    logger.info("Target: %s", target)
    logger.info("Bitrate: %s", bitrate)

    command = [
        ffmpeg_path,
        '-v', 'quiet',             # Работать в тихом режиме, чтобы избежать зависания
        '-i', str(source),          # Input file
        '-ac', '2',                # Принудительно стерео
        '-ar', '44100',            # Принудительно 44.1 кГц (CD-quality)
        '-c:a', 'libmp3lame',     # Explicitly use the LAME encoder
        '-b:a', bitrate,            # Используем вычисленный битрейт
        '-compression_level', '0', # Минимальное сжатие
        '-abr', 'false',           # Отключить ABR, только CBR
        '-y',
        str(target)                 # Output file
    ]

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
    """
    Очищает HTML-описание для RSS/подкастов, оставляя только <p>, <ul>, <li>, <a>.
    Удаляет/экранирует все остальные теги и спецсимволы.
    """
    allowed_tags = ['p', 'ul', 'ol', 'li', 'a']
    allowed_attrs = {'a': ['href']}
    # Удаляем все стили, классы, id и т.д.
    cleaned = bleach.clean(
        html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        strip=True
    )
    # bleach уже экранирует спецсимволы, но заменим типовые кавычки на стандартные
    cleaned = cleaned.replace('&rsquo;', "'").replace('&lsquo;', "'")
    cleaned = cleaned.replace('&ldquo;', '"').replace('&rdquo;', '"')
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
