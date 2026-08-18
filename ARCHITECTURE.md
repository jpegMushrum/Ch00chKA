# Архитектура Ch00chKA

Проект устроен как модульный монолит. Telegram, AI, SQLite и медиазагрузчики
подключаются через явные границы, но разворачиваются одним приложением.

## Обработка сообщения

1. Telegram adapter преобразует `Message` в `NormalizedMessage`. Базовые
   обращения состоят из имени, username и `BOT_ALIASES`. Администратор каждого
   чата может задать исходные обращения через `/generate_aliases`; AI создаёт
   производные, которые сохраняются в SQLite и проверяются до participation agent.
2. `UrlActionPlanner` создаёт независимые media actions без знания о загрузчиках.
3. `MessageProcessor` получает `ActionPlan` и запускает AI-контур отдельно.
4. Participation agent решает только, нужен ли текстовый ответ.
5. Context Builder собирает personality, summary и недавние сообщения.
6. Actor создаёт черновик ответа.
7. Observer принимает, блокирует или запрашивает одну правку.
   `finish_reason` сохраняется вместе с черновиком: ответ, остановленный по
   лимиту, всегда перегенерируется короче, а другие незавершённые ответы
   блокируются и не попадают в чат.
8. Принятый ответ сохраняется и отправляется в Telegram.
9. `YtDlpMediaAdapter` исполняет media actions отдельно от AI-контура. Он
   принимает только allowlist доменов YouTube, TikTok и Instagram, скачивает
   публичное видео через `yt-dlp`, приводит контейнер к MP4 через `ffmpeg` и
   удаляет временные файлы после отправки.

Observer не изменяет personality и не создаёт циклов саморедактирования.
Версия системного контракта хранится в `ch00chka/ai/prompts.py`.

## Каталоги

- `ch00chka/domain` — модели без внешних зависимостей.
- `ch00chka/application` — orchestration и интерфейсы компонентов.
- `ch00chka/ai` — Participation, Actor, Observer, prompts и LLM gateway.
- `ch00chka/infrastructure` — SQLite repository.
- `ch00chka/presentation` — Telegram adapter.
- `ch00chka/integrations` — загрузчики медиа и другие внешние адаптеры.

## Тестирование агентов

Без Telegram:

```powershell
python -m scripts.agent_smoke_test "Стоит ли боту отвечать на это сообщение?"
python -m scripts.agent_smoke_test "Привет, бот" --force-reply
```

Автоматические тесты orchestration:

```powershell
python -m unittest discover -s tests -v
```

Модели и endpoint меняются переменными `AI_MODEL`,
`AI_PARTICIPATION_MODEL`, `AI_OBSERVER_MODEL` и `AI_BASE_URL`.
Thinking mode управляется явно через `AI_THINKING_ENABLED`; по умолчанию он выключен.
Глобальные резервные обращения перечисляются через запятую в `BOT_ALIASES`.
Алиасы конкретного чата настраиваются командами `/generate_aliases` и
`/get_aliases`; менять их может только администратор.
При старте накопившиеся Telegram updates удаляются, чтобы после простоя бот не
отвечал на старую переписку. Это отключается через
`TELEGRAM_DROP_PENDING_UPDATES=false`.

Медиа по умолчанию ограничено 10 минутами и 48 МБ. Параметры меняются через
`MEDIA_MAX_DURATION_SECONDS`, `MEDIA_MAX_BYTES` и `MEDIA_TEMP_DIR`. Cookies и
аккаунты для первой версии не используются. Число одновременно работающих
загрузчиков задаётся через `MEDIA_MAX_CONCURRENT_DOWNLOADS` (по умолчанию 2).
