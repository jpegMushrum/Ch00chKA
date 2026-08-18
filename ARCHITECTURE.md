# Архитектура Ch00chKA

Проект устроен как модульный монолит. Telegram, AI, SQLite и медиазагрузчики
подключаются через явные границы, но разворачиваются одним приложением.

## Обработка сообщения

1. Telegram adapter преобразует `Message` в `NormalizedMessage`. При запуске
   один AI-запрос генерирует безопасные варианты обращения к боту; они
   объединяются с `BOT_ALIASES` и проверяются до participation agent.
2. `UrlActionPlanner` создаёт независимые media actions без знания о загрузчиках.
3. `MessageProcessor` получает `ActionPlan` и запускает AI-контур отдельно.
4. Participation agent решает только, нужен ли текстовый ответ.
5. Context Builder собирает personality, summary и недавние сообщения.
6. Actor создаёт черновик ответа.
7. Observer принимает, блокирует или запрашивает одну правку.
8. Принятый ответ сохраняется и отправляется в Telegram.
9. Legacy media adapter исполняет media actions отдельно от AI-контура.

Observer не изменяет personality и не создаёт циклов саморедактирования.
Версия системного контракта хранится в `ch00chka/ai/prompts.py`.

## Каталоги

- `ch00chka/domain` — модели без внешних зависимостей.
- `ch00chka/application` — orchestration и интерфейсы компонентов.
- `ch00chka/ai` — Participation, Actor, Observer, prompts и LLM gateway.
- `ch00chka/infrastructure` — SQLite repository.
- `ch00chka/presentation` — Telegram adapter.
- `ch00chka/integrations` — внешние и временные legacy-адаптеры.
- `handlers/load_video.py` — существующая реализация скачивания, пока без рефакторинга.

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
Генерация вариантов имени управляется через `AI_ALIAS_GENERATION_ENABLED`, а
ручные варианты перечисляются через запятую в `BOT_ALIASES`.
