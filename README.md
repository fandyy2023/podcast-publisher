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
* Опциональное транскодирование WAV → MP3 (192 kbps) при флаге `--force`
* HTTP-раздача RSS, аудио и картинок через Flask (или FastAPI)
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

## Лицензия
MIT
