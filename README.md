# Podcast Publisher

Python-based toolkit to generate and host an RSS feed for a podcast show, suitable for submission to Spotify (and любой другой подкаст-агрегатор).

## Quick Start

```bash
# 1. Создать и активировать виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Отредактировать настройки шоу
cp .env.example .env              # при необходимости
cp feedgen_config.example.json feedgen_config.json

# 4. Сгенерировать RSS-ленту
python publisher.py --log-level INFO

# 5. Запустить HTTP-сервер (локально)
python app.py

# 6. Пробросить порт во внешний мир (например, через ngrok)
ngrok http 5000
```

После этого RSS будет доступен по URL вроде `https://<random>.ngrok.io/feed.xml`. Его можно указать в Spotify for Podcasters при создании шоу.

## Ключевые возможности

* Автоматическая генерация `feed.xml` на основе структуры каталогов и `metadata.json` для каждого эпизода
* Автоматическое транскодирование WAV/OGG/FLAC → MP3 320 kbps при загрузке через веб-интерфейс и опция `--force` для CLI
* HTTP-раздача RSS, аудио и картинок через Flask (или FastAPI)
* Поддержка **чанковой загрузки** файлов >100 MB с прогресс-баром и кнопкой отмены
* Интерфейс **пакетной загрузки** эпизодов из Excel + drag-and-drop аудио/обложек
* **Автосопоставление жанров** из Excel с официальными категориями Apple/Spotify через `data/category_mapping.json` + fuzzy-поиск
* Расширяемый CLI с флагами `--dry-run`, `--check-domain`, `--enable-analytics`
* Логирование и уведомления об ошибках по email
* Лёгкая миграция с Mac+ngrok → Raspberry Pi+домен

## Структура проекта
```
podcast-publisher/
├── .env.example            # переменные окружения (шаблон)
├── feedgen_config.json     # конфиг шоу + RSS (пример)
├── assets/                 # обложка шоу
│   └── show_cover.jpg
├── episodes/               # подпапки эпизодов (ep001/, ep002/ …)
├── feed.xml                # генерируется автоматически
├── app.py                  # HTTP-сервер
├── publisher.py            # CLI для генерации/обновления RSS
├── utils.py                # вспомогательные функции
└── requirements.txt        # зависимости
```

## Встраивание ID3-тегов

Начиная с версии ≥ 0.3 все MP3-файлы генерируются скриптом `utils.transcode_audio_to_mp3` с полным набором тегов ID3 v2.3.  Логика следующая:

1. Фоновой задачей `process_audio_background` собираются данные о шоу и эпизоде:  
   • `title` — название эпизода;  
   • `artist` — поле `author` из `shows/<show_id>/config.json` (или название шоу, если автора нет);  
   • `album`  — название шоу;  
   • `date`   — текущий год в UTC;  
   • `ITUNESADVISORY` — флаг контента ("1" = explicit, "0" = clean), вычисляется из `explicit` в `metadata.json`.
2. Если в папке эпизода есть обложка (`.jpg/.png/.webp`) — она подхватывается как второе входное видео-поток ffmpeg и записывается в кадр `APIC (front-cover)`.
3. Команда ffmpeg вызывается с опциями:

```bash
ffmpeg -i audio.wav [-i cover.jpg] \
       -ac 2 -ar 44100 -c:a libmp3lame -b:a 192k \
       -map 0:a [-map 1:v -c:v copy] \
       -metadata title="…" -metadata artist="…" … \
       -id3v2_version 3 -write_id3v1 1 output.mp3
```

В результате получаем валидный MP3, проходящий проверки Apple/Spotify с корректным флагом explicit и обложкой.

### Мелкие исправления
* Установлен поиск обложки в папке эпизода, если путь не задан явно.  
* Исправлено удаление старого файла WAV после успешной конверсии.

## Сопоставление жанров с категориями Apple/Spotify

Начиная с v0.4, категории подкаста задаются автоматически на основе жанров, указанных в Excel-таблицах (`English.xlsx`, `data/russian.xlsx` и т. д.)

1. Скрипт `scripts/generate_category_mapping.py` собирает все уникальные значения колонок *Genre/Жанр* во всех `.xlsx` файлах репозитория.
2. Для каждого тега выполняются пошаговые попытки сопоставления:
   1. Точное соответствие в `data/category_mapping.json` (добавляется вручную или предыдущим запуском скрипта).
   2. Fuzzy-поиск (`difflib.get_close_matches`) по официальному списку Apple Podcasts (main + subcategories).
3. Результат сохраняется обратно в `data/category_mapping.json` (предыдущая копия переименовывается в `.bak`).
4. При пакетной загрузке `app.py` использует эту карту:
   * Тег жанра нормализуется (`[\W_]+ → " "`, `lower()`).
   * Если найдено совпадение, значения `category`, `category_main`, `category_sub` сохраняются в `shows/<show>/config.json` и отображаются в редакторе эпизода.
   * Если тег новый, будет использовано значение по умолчанию *None*; при следующем прогоне скрипта тег появится в JSON — достаточно вписать категорию вручную.

Таким образом, достаточно периодически запускать:

```bash
source .venv/bin/activate
python scripts/generate_category_mapping.py
```

чтобы база жанров оставалась актуальной, а placeholder «Arts» больше не появлялся.

---

## Лицензия
MIT
