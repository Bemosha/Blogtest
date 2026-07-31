# Telegram AutoPost Bot (RSS / Website)

Бот для автопостинга в Telegram из RSS-лент и/или сайта.

## Возможности

- парсинг RSS;
- базовый парсинг сайта по CSS-селекторам;
- автопост по расписанию;
- защита от дублей (через `state.json`);
- команды в Telegram: `/start`, `/check`, `/status`.
- опционально: иногда публикует AI-изображение (Nano Banana) по теме IT/логотипа канала.

---

## 1) Подготовка

1. Создайте бота через **@BotFather** и получите `TELEGRAM_BOT_TOKEN`.
2. Добавьте бота в канал/чат, куда он будет постить.
3. Для канала дайте права на публикацию.

---

## 2) Настройка

В папке `telegram_autopost_bot`:

1. Скопируйте `.env.example` в `.env` и заполните значения.
2. Скопируйте `sources.example.json` в `sources.json` и укажите свои источники.

Пример `TELEGRAM_CHAT_ID`:
- `@my_channel_name` (публичный канал)
- `-1001234567890` (ID чата/канала)

---

## 3) Установка зависимостей

```bash
pip install -r telegram_autopost_bot/requirements.txt
```

---

## 4) Запуск

```bash
python telegram_autopost_bot/main.py
```

После запуска:
- бот начнёт проверять источники каждые `CHECK_INTERVAL_MINUTES`;
- можно вручную вызвать `/check` в личке с ботом.

---

## Формат `sources.json`

### RSS

```json
{
  "type": "rss",
  "name": "Habr",
  "url": "https://habr.com/ru/rss/all/all/",
  "limit": 7
}
```

### Сайт

```json
{
  "type": "website",
  "name": "Some Site",
  "url": "https://example.com/news",
  "item_selector": "article",
  "link_selector": "a[href]",
  "title_selector": "h2",
  "summary_selector": "p",
  "limit": 5
}
```

---

## Важно

- Используйте только разрешённые источники и соблюдайте правила сайтов.
- Не публикуйте спам/нарушающий контент.

---

## AI-изображения (Nano Banana, опционально)

Бот может иногда публиковать не только текст, но и картинку по теме поста (IT + концепт логотипа канала).

Добавьте в `.env`:

```env
ENABLE_AI_IMAGE_POSTS=1
AI_IMAGE_POST_CHANCE=0.2
NANO_BANANA_API_URL=https://openrouter.ai/api/v1/images/generations
NANO_BANANA_API_KEY=your_api_key_here
NANO_BANANA_MODEL=google/gemini-2.5-flash-image-preview
AI_IMAGE_SIZE=1024x1024
AI_IMAGE_STYLE_PROMPT=clean modern IT illustration, channel logo concept, vector style, bright contrast, no watermark, no readable text
```

Пояснения:
- `ENABLE_AI_IMAGE_POSTS=1` — включает режим AI-картинок.
- `AI_IMAGE_POST_CHANCE` — вероятность генерации картинки для каждого поста (0..1).
- если генерация не удалась, бот автоматически отправит обычный текстовый пост (fallback).

---

## Понятные посты + рост подписок

Теперь бот формирует более читаемый формат:
- заголовок;
- блок «Коротко»;
- источник;
- ссылка;
- 2–3 тематических хэштега;
- CTA-блок с призывом подписаться.

Настройки в `.env`:

```env
FOLLOW_CTA_ENABLED=1
CHANNEL_HANDLE=your_channel
AUTHOR_HANDLE=your_username
SUBSCRIBE_CTA_TEXT=Подпишись, чтобы не пропускать полезные IT-разборы и быстрые новости.
```

Советы для роста подписок:
- поставьте `CHANNEL_HANDLE`, чтобы в каждом посте был понятный путь подписки;
- добавьте `AUTHOR_HANDLE`, чтобы люди знали, кто ведёт канал;
- держите `SUBSCRIBE_CTA_TEXT` коротким и конкретным (1 строка).
