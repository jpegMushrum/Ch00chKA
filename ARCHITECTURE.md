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
5. Research agent независимо решает, нужны ли свежие внешние сведения, и
   формирует не более двух запросов. Код разрешает только фиксированные источники:
   Wikipedia для общих сущностей, Apple iTunes Search для музыки и внутренний
   SearXNG для широкого поиска по открытым веб-источникам. Аккаунты и API-ключи
   для них не нужны; произвольные URL модель открывать не может.
6. Context Builder собирает personality, summary, недавние сообщения и, если
   исследование понадобилось, ограниченный факт-пак. Внешний текст помечается как
   недоверенные данные и не может задавать инструкции агентам.
7. Actor создаёт черновик ответа.
8. Observer принимает, блокирует или запрашивает одну правку, а также сверяет
   явные актуальные утверждения с факт-паком.
   `finish_reason` сохраняется вместе с черновиком: ответ, остановленный по
   лимиту, всегда перегенерируется короче, а другие незавершённые ответы
   блокируются и не попадают в чат.
9. Принятый ответ сохраняется и отправляется в Telegram.
10. `YtDlpMediaAdapter` исполняет media actions отдельно от AI-контура. Он
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
`AI_PARTICIPATION_MODEL`, `AI_RESEARCH_MODEL`, `AI_OBSERVER_MODEL` и
`AI_BASE_URL`.
Thinking mode управляется явно через `AI_THINKING_ENABLED`; по умолчанию он выключен.
Исследование отключается через `AI_RESEARCH_ENABLED=false`. Время ожидания,
срок кеша и размер факт-пака задаются `RESEARCH_TIMEOUT_SECONDS`,
`RESEARCH_CACHE_TTL_SECONDS` и `RESEARCH_MAX_FACTS`. Для полноценного общего
web-поиска достаточно добавить новый адаптер за интерфейсом `ResearchBackend`,
не меняя Participation, Actor или Observer.

SearXNG работает отдельным внутренним сервисом Compose без опубликованного порта.
JSON API включён только для общения с ботом; адрес задаётся контейнеру через
`RESEARCH_WEB_BASE_URL`. Секрет экземпляра хранится в `.env` как
`SEARXNG_SECRET`.
Образ зафиксирован по digest проверенной версии `2026.8.17-374939b88`, поэтому
обновление поискового сервиса выполняется осознанной заменой digest.
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
