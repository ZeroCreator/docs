# Безопасный пошаговый переход Flat Parser на Temporal без big-bang cutover

Я опираюсь на текущую структуру репозитория: Celery beat в `conf/celery_app.py`, task entrypoints в `parser/tasks.py`, TrendAgent fan-out в `parser/resources/trendagent.py`, token watcher в `parser/resources/trendagent_update_token.py`, и daily-report/queue-depth логику в `parser/reports.py`.

Главная идея: сначала отдать Temporal orchestration, а не переписывать сразу все parser steps; Celery и RabbitMQ временно живут рядом, а rollback — это просто вернуть новые запуски на старый путь.

## Что

1. **Инвентаризация и фиксация текущего поведения.**
   Зафиксировать все текущие entrypoints и их семантику: `parser.parse-all`, `parser.parse-trendagent`, `parser.parse-trendagent-only-flats`, `parser.parse-installments`, `parser.parse-prodoma`, `parser.send-daily-report`, плюс TrendAgent `request_flat_info`.

   До миграции нужны characterization tests: порядок шагов, retry-поведение, что считается ошибкой, какие side effects пишутся в БД, JSON и Telegram.

2. **Ввести Temporal как новый orchestration layer.**
   Добавить отдельный Temporal worker/service и слой адаптеров.

   Маппинг такой:

   - `Workflow` = orchestration (`parse-all`, `parse-trendagent`, daily report pipeline)
   - `Activity` = I/O и side effects (HTTP, browser, DB writes, Telegram, JSON/reporting, queue stats)
   - `Schedule` = замена beat/cron задачам из `conf/celery_app.py`

   На этом этапе Celery ещё не удаляется.

3. **Мигрировать сначала один простой поток.**
   Лучший первый кандидат — `parse-installments` или `parse-prodoma`: у них один верхнеуровневый task и меньше скрытой распределённой логики, чем у TrendAgent.

   Сделать Temporal workflow end-to-end только для одного семейства задач, а старый Celery entrypoint оставить как fallback.

4. **Перенести линейные orchestration flows.**
   Затем перевести `parse-all`: это хороший кандидат для parent workflow с последовательными activities для ERZ → Наш.Дом.РФ → декларации.

   Здесь важно сохранить тот же порядок, что сейчас захардкожен в `parser/tasks.py`.

5. **Отдельно мигрировать TrendAgent.**
   Это самый сложный кусок: сейчас `FlatParser.parse()` делает fan-out через `request_flat_info.apply_async(..., queue="flat-parser-trendagent")`.

   В Temporal это лучше представить как workflow, который порождает activity batches / child workflows для квартир.

   Не надо тащить token watcher внутрь workflow. `watch_trendagent_token_updates` и Valkey auth token лучше оставить внешним сервисом на первом этапе, иначе вы смешаете orchestration с долгоживущим browser/session management.

6. **Перевести расписания и вывести Celery из orchestration.**
   После того как все верхнеуровневые pipelines стартуют через Temporal, перенести beat schedules на Temporal Schedules, manual run-команды — на Temporal client/start, и только потом убирать Celery из orchestration.

   Leaf-работы можно оставить на Celery ещё на переходный период, но у каждого workflow должен быть один владелец orchestration: либо Temporal, либо Celery, не оба.

## Где

- `conf/celery_app.py` — источник всех текущих расписаний, это будущие Temporal Schedules
- `parser/tasks.py` — граница migration seam: из этих entrypoints удобно строить первые workflows
- `parser/resources/trendagent.py` — самый рискованный участок из-за fan-out и autoretry
- `parser/resources/trendagent_update_token.py` + management command — оставить вне первого среза миграции
- `parser/reports.py` — отдельный activity/service для отчётности, не workflow-логика

## Риски

- **Double retry**: сейчас уже есть retries в `fetch()` и Celery autoretry в `request_flat_info`; если сверху добавить Temporal retries без правил, получите дубли side effects.
- **Wrong boundary**: workflow-код в Temporal должен быть deterministic; HTTP, текущее время, случайность, browser, внешние чтения — только в activities.
- **History growth**: TrendAgent fan-out легко раздует workflow history; для длинных прогонов надо заранее закладывать batching, child workflows и `continue-as-new`.
- **Split-brain**: нельзя, чтобы один и тот же parser-run одновременно вёлся и Celery, и Temporal. Cutover только по ingress.
- **Observability drift**: сейчас часть операционной правды сидит в RabbitMQ queue depth, JSON-отчётах и Telegram. Это нужно либо зеркалить, либо заменить до полного отключения Celery.

## Внедрение

Рекомендую такой порядок внедрения:

1. Phase 1: characterization tests + Temporal bootstrap
2. Phase 2: `parse-installments` или `parse-prodoma`
3. Phase 3: `parse-all`
4. Phase 4: `send-daily-report`
5. Phase 5: TrendAgent orchestration (`parse-trendagent`, потом `parse-trendagent-only-flats`)
6. Phase 6: замена beat на Temporal Schedules и постепенное выведение Celery

Rollback в каждой фазе один и тот же: новые старты снова маршрутизируются в Celery, а уже начатые Temporal runs завершаются в Temporal.

## Next

Следующий правильный шаг — сделать repo-specific design doc на 1 страницу с таблицей:

`current Celery task -> target Temporal workflow/activity -> task queue -> retry policy -> idempotency key -> rollback strategy`

Ниже — repo-specific таблица перехода для текущих задач Flat Parser.

## Таблица перехода Celery → Temporal

| Current Celery task / process | Current role | Target in Temporal | Suggested task queue | Retry policy | Idempotency key | Rollback strategy |
|---|---|---|---|---|---|---|
| `parser.parse-all` | Верхнеуровневый nightly orchestration для ERZ + Наш.Дом.РФ | **Workflow** `ParseAllWorkflow` | `parser-main` | Workflow без auto-retry; retries на уровне activities | `parse-all:{date}` | Переключить scheduler / ручной старт обратно на Celery `parser.parse-all` |
| ERZ `DevelopersParser().parse()` | Leaf parser step | **Activity** `parse_erz_developers` | `parser-main` | Ограниченные retries только на transient errors | `erz:developers:{run_id}` | Запускать этот шаг снова из старого Celery orchestration |
| ERZ `RootParser().parse()` | Leaf parser step | **Activity** `parse_erz_root_links` | `parser-main` | То же | `erz:root-links:{run_id}` | То же |
| ERZ `ComplexParser().parse()` | Leaf parser step | **Activity** `parse_erz_complexes` | `parser-main` | То же | `erz:complexes:{run_id}` | То же |
| ERZ `LiterParser().parse()` | Leaf parser step | **Activity** `parse_erz_liters` | `parser-main` | То же | `erz:liters:{run_id}` | То же |
| ERZ `FloorsParser().parse()` | Leaf parser step | **Activity** `parse_erz_floors` | `parser-main` | То же | `erz:floors:{run_id}` | То же |
| Наш.Дом.РФ `nash_dom.LiterParser().parse()` | Leaf parser step, async внутри | **Activity** `parse_nashdom_liters` | `parser-main` | Ограниченные retries; внимательно к browser/network failures | `nashdom:liters:{run_id}` | Откатить весь `parse-all` ingress на Celery |
| Наш.Дом.РФ `LiterDeclarationParser().parse()` | Leaf parser step | **Activity** `parse_project_declarations` | `parser-main` | Ограниченные retries | `nashdom:declarations:{run_id}` | То же |
| `parser.parse-trendagent` | Верхнеуровневый orchestration для ЖК / литеров + квартир | **Workflow** `ParseTrendagentWorkflow` | `trendagent-orchestrator` | Workflow без broad retry; retries только activities | `trendagent:full:{date_or_run}` | Вернуть запуск на Celery `parser.parse-trendagent` |
| `ComplexAndLiterParser().parse()` | Leaf parser step | **Activity** `parse_trendagent_complexes_and_liters` | `trendagent-orchestrator` | Ограниченные retries | `trendagent:complexes-liters:{run_id}` | Старый Celery workflow |
| `FlatParser().parse()` fan-out phase | Генерация задач по квартирам | Workflow step или child workflow launcher | `trendagent-orchestrator` | Не ретраить бездумно весь fan-out | `trendagent:flat-fanout:{run_id}` | Маршрутизировать новый запуск обратно в Celery |
| `request_flat_info` | Per-flat distributed task с DB writes и autoretry | **Activity** `sync_trendagent_flat` или Child Workflow для батча квартир | `trendagent-flats` | Явный activity retry policy вместо Celery autoretry; убрать двойной retry | `trendagent:flat:{flat_id}` | Оставить Celery `request_flat_info` активным и вернуть fan-out туда |
| `parser.parse-trendagent-only-flats` | Ручной / точечный запуск только квартир | **Workflow** `ParseTrendagentFlatsOnlyWorkflow` | `trendagent-orchestrator` | Как у TrendAgent flats | `trendagent:flats-only:{date_or_run}` | Ручной запуск снова через Celery task |
| `parser.parse-installments` | Простой single-step parser | **Workflow** `ParseInstallmentsWorkflow` или сразу activity behind workflow | `parser-main` | Activity retries на transient sheet/network errors | `installments:{date}` | Вернуть scheduler / manual call на Celery |
| `InstallmentsParser().parse()` | Leaf parser step | **Activity** `parse_installments_sheet` | `parser-main` | Ограниченные retries | `installments:sheet:{run_id}` | То же |
| `parser.parse-prodoma` | Single-step orchestration | **Workflow** `ParseProdomaWorkflow` | `parser-main` | Workflow minimal; retries на activity | `prodoma:{date}` | Вернуть на Celery |
| `ComplexReportParser().parse()` | Async I/O parser step | **Activity** `parse_prodoma_reports` | `parser-main` | Ограниченные retries, особенно network-bound | `prodoma:reports:{run_id}` | То же |
| `parser.send-daily-report` | Scheduled reporting pipeline | **Workflow** `SendDailyReportWorkflow` | `reporting` | Почти без retry на весь workflow; activity-level retries | `daily-report:{date}` | Вернуть scheduler на Celery `parser.send-daily-report` |
| `save_task_results()` | Пишет статус в JSON | **Activity** `persist_report_state` или заменить на DB / Temporal visibility | `reporting` | Retry only on fs/transient errors | `report-state:{parser_group}:{date}` | Оставить старый JSON путь |
| `send_daily_report()` Telegram send | Формирование + отправка отчёта | **Activities** `build_daily_report`, `send_telegram_report`, `get_trendagent_queue_depth` | `reporting` | Telegram / API retries ограниченно; не дублировать отправку | `telegram-report:{date}` | Откатить весь reporting ingress на Celery |
| RabbitMQ queue-depth lookup | Операционная метрика в отчёте | **Activity** `get_trendagent_backlog` | `reporting` | Короткие retries | `queue-depth:trendagent:{date}` | Оставить текущий RabbitMQ HTTP API вызов |
| `watch_trendagent_token_updates` | Долгоживущий внешний watcher | **Не мигрировать первым этапом**; оставить отдельным service/process | `trendagent-auth` если позже переносить | Не через workflow retries; это service supervision | `auth-token-watcher:singleton` | Оставить как есть в docker/ops |
| `get_auth_token()` + Valkey token path | Shared runtime dependency | **Оставить внешней зависимостью** на ранних фазах | `n/a` | `n/a` | `auth-token` | Не трогать до конца миграции |
| Celery beat schedules | Periodic start points | **Temporal Schedules** | По queue владельца workflow | Не использовать cron поверх Celery дальше | `schedule:{workflow_name}` | Для каждого schedule вернуть старый beat / cron trigger |
| README manual `celery call ...` | Manual operations | CLI / admin endpoint для start workflow | По queue workflow | `n/a` | `request_id_from_operator` | Временно держать обе команды |

## Рекомендуемый порядок миграции

1. `parse-installments`
2. `parse-prodoma`
3. `parse-all`
4. `send-daily-report`
5. `parse-trendagent-only-flats`
6. `parse-trendagent`
7. Только потом думать про watcher / token subsystem

## Ключевые правила

- Workflow only for orchestration, не для HTTP / DB / browser
- Один ingress-владелец на run: либо Celery, либо Temporal
- Idempotency обязателен для Telegram, DB upserts и per-flat sync
- Для TrendAgent лучше сразу думать batch / child workflows, иначе история workflow разрастётся

## Компактная версия таблицы для GitHub

Ниже — та же матрица перехода, но в вертикальном формате, чтобы её было проще читать без широкого горизонтального скролла.

### `parser.parse-all`

- **Current role:** Верхнеуровневый nightly orchestration для ERZ + Наш.Дом.РФ
- **Target in Temporal:** **Workflow** `ParseAllWorkflow`
- **Suggested task queue:** `parser-main`
- **Retry policy:** Workflow без auto-retry; retries на уровне activities
- **Idempotency key:** `parse-all:{date}`
- **Rollback strategy:** Переключить scheduler / ручной старт обратно на Celery `parser.parse-all`

### ERZ `DevelopersParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_erz_developers`
- **Suggested task queue:** `parser-main`
- **Retry policy:** Ограниченные retries только на transient errors
- **Idempotency key:** `erz:developers:{run_id}`
- **Rollback strategy:** Запускать этот шаг снова из старого Celery orchestration

### ERZ `RootParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_erz_root_links`
- **Suggested task queue:** `parser-main`
- **Retry policy:** То же
- **Idempotency key:** `erz:root-links:{run_id}`
- **Rollback strategy:** То же

### ERZ `ComplexParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_erz_complexes`
- **Suggested task queue:** `parser-main`
- **Retry policy:** То же
- **Idempotency key:** `erz:complexes:{run_id}`
- **Rollback strategy:** То же

### ERZ `LiterParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_erz_liters`
- **Suggested task queue:** `parser-main`
- **Retry policy:** То же
- **Idempotency key:** `erz:liters:{run_id}`
- **Rollback strategy:** То же

### ERZ `FloorsParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_erz_floors`
- **Suggested task queue:** `parser-main`
- **Retry policy:** То же
- **Idempotency key:** `erz:floors:{run_id}`
- **Rollback strategy:** То же

### Наш.Дом.РФ `nash_dom.LiterParser().parse()`

- **Current role:** Leaf parser step, async внутри
- **Target in Temporal:** **Activity** `parse_nashdom_liters`
- **Suggested task queue:** `parser-main`
- **Retry policy:** Ограниченные retries; внимательно к browser/network failures
- **Idempotency key:** `nashdom:liters:{run_id}`
- **Rollback strategy:** Откатить весь `parse-all` ingress на Celery

### Наш.Дом.РФ `LiterDeclarationParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_project_declarations`
- **Suggested task queue:** `parser-main`
- **Retry policy:** Ограниченные retries
- **Idempotency key:** `nashdom:declarations:{run_id}`
- **Rollback strategy:** То же

### `parser.parse-trendagent`

- **Current role:** Верхнеуровневый orchestration для ЖК / литеров + квартир
- **Target in Temporal:** **Workflow** `ParseTrendagentWorkflow`
- **Suggested task queue:** `trendagent-orchestrator`
- **Retry policy:** Workflow без broad retry; retries только activities
- **Idempotency key:** `trendagent:full:{date_or_run}`
- **Rollback strategy:** Вернуть запуск на Celery `parser.parse-trendagent`

### `ComplexAndLiterParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_trendagent_complexes_and_liters`
- **Suggested task queue:** `trendagent-orchestrator`
- **Retry policy:** Ограниченные retries
- **Idempotency key:** `trendagent:complexes-liters:{run_id}`
- **Rollback strategy:** Старый Celery workflow

### `FlatParser().parse()` fan-out phase

- **Current role:** Генерация задач по квартирам
- **Target in Temporal:** Workflow step или child workflow launcher
- **Suggested task queue:** `trendagent-orchestrator`
- **Retry policy:** Не ретраить бездумно весь fan-out
- **Idempotency key:** `trendagent:flat-fanout:{run_id}`
- **Rollback strategy:** Маршрутизировать новый запуск обратно в Celery

### `request_flat_info`

- **Current role:** Per-flat distributed task с DB writes и autoretry
- **Target in Temporal:** **Activity** `sync_trendagent_flat` или Child Workflow для батча квартир
- **Suggested task queue:** `trendagent-flats`
- **Retry policy:** Явный activity retry policy вместо Celery autoretry; убрать двойной retry
- **Idempotency key:** `trendagent:flat:{flat_id}`
- **Rollback strategy:** Оставить Celery `request_flat_info` активным и вернуть fan-out туда

### `parser.parse-trendagent-only-flats`

- **Current role:** Ручной / точечный запуск только квартир
- **Target in Temporal:** **Workflow** `ParseTrendagentFlatsOnlyWorkflow`
- **Suggested task queue:** `trendagent-orchestrator`
- **Retry policy:** Как у TrendAgent flats
- **Idempotency key:** `trendagent:flats-only:{date_or_run}`
- **Rollback strategy:** Ручной запуск снова через Celery task

### `parser.parse-installments`

- **Current role:** Простой single-step parser
- **Target in Temporal:** **Workflow** `ParseInstallmentsWorkflow` или сразу activity behind workflow
- **Suggested task queue:** `parser-main`
- **Retry policy:** Activity retries на transient sheet/network errors
- **Idempotency key:** `installments:{date}`
- **Rollback strategy:** Вернуть scheduler / manual call на Celery

### `InstallmentsParser().parse()`

- **Current role:** Leaf parser step
- **Target in Temporal:** **Activity** `parse_installments_sheet`
- **Suggested task queue:** `parser-main`
- **Retry policy:** Ограниченные retries
- **Idempotency key:** `installments:sheet:{run_id}`
- **Rollback strategy:** То же

### `parser.parse-prodoma`

- **Current role:** Single-step orchestration
- **Target in Temporal:** **Workflow** `ParseProdomaWorkflow`
- **Suggested task queue:** `parser-main`
- **Retry policy:** Workflow minimal; retries на activity
- **Idempotency key:** `prodoma:{date}`
- **Rollback strategy:** Вернуть на Celery

### `ComplexReportParser().parse()`

- **Current role:** Async I/O parser step
- **Target in Temporal:** **Activity** `parse_prodoma_reports`
- **Suggested task queue:** `parser-main`
- **Retry policy:** Ограниченные retries, особенно network-bound
- **Idempotency key:** `prodoma:reports:{run_id}`
- **Rollback strategy:** То же

### `parser.send-daily-report`

- **Current role:** Scheduled reporting pipeline
- **Target in Temporal:** **Workflow** `SendDailyReportWorkflow`
- **Suggested task queue:** `reporting`
- **Retry policy:** Почти без retry на весь workflow; activity-level retries
- **Idempotency key:** `daily-report:{date}`
- **Rollback strategy:** Вернуть scheduler на Celery `parser.send-daily-report`

### `save_task_results()`

- **Current role:** Пишет статус в JSON
- **Target in Temporal:** **Activity** `persist_report_state` или заменить на DB / Temporal visibility
- **Suggested task queue:** `reporting`
- **Retry policy:** Retry only on fs/transient errors
- **Idempotency key:** `report-state:{parser_group}:{date}`
- **Rollback strategy:** Оставить старый JSON путь

### `send_daily_report()` Telegram send

- **Current role:** Формирование + отправка отчёта
- **Target in Temporal:** **Activities** `build_daily_report`, `send_telegram_report`, `get_trendagent_queue_depth`
- **Suggested task queue:** `reporting`
- **Retry policy:** Telegram / API retries ограниченно; не дублировать отправку
- **Idempotency key:** `telegram-report:{date}`
- **Rollback strategy:** Откатить весь reporting ingress на Celery

### RabbitMQ queue-depth lookup

- **Current role:** Операционная метрика в отчёте
- **Target in Temporal:** **Activity** `get_trendagent_backlog`
- **Suggested task queue:** `reporting`
- **Retry policy:** Короткие retries
- **Idempotency key:** `queue-depth:trendagent:{date}`
- **Rollback strategy:** Оставить текущий RabbitMQ HTTP API вызов

### `watch_trendagent_token_updates`

- **Current role:** Долгоживущий внешний watcher
- **Target in Temporal:** **Не мигрировать первым этапом**; оставить отдельным service/process
- **Suggested task queue:** `trendagent-auth`, если позже переносить
- **Retry policy:** Не через workflow retries; это service supervision
- **Idempotency key:** `auth-token-watcher:singleton`
- **Rollback strategy:** Оставить как есть в docker/ops

### `get_auth_token()` + Valkey token path

- **Current role:** Shared runtime dependency
- **Target in Temporal:** **Оставить внешней зависимостью** на ранних фазах
- **Suggested task queue:** `n/a`
- **Retry policy:** `n/a`
- **Idempotency key:** `auth-token`
- **Rollback strategy:** Не трогать до конца миграции

### Celery beat schedules

- **Current role:** Periodic start points
- **Target in Temporal:** **Temporal Schedules**
- **Suggested task queue:** По queue владельца workflow
- **Retry policy:** Не использовать cron поверх Celery дальше
- **Idempotency key:** `schedule:{workflow_name}`
- **Rollback strategy:** Для каждого schedule вернуть старый beat / cron trigger

### README manual `celery call ...`

- **Current role:** Manual operations
- **Target in Temporal:** CLI / admin endpoint для start workflow
- **Suggested task queue:** По queue workflow
- **Retry policy:** `n/a`
- **Idempotency key:** `request_id_from_operator`
- **Rollback strategy:** Временно держать обе команды

### Терминология
### 1. Рабочий процесс — **Workflow**
Простыми словами: Это бизнес-план или оркестр, который говорит, что и когда делать. Вы пишете его на обычном языке программирования (Python, Go, Java).

Расшифровка: Это главная логика вашего приложения. В коде Workflow вы указываете последовательность шагов: "сначала сделай это, потом, если успешно, сделай то, а потом подожди 2 дня". Самое важное свойство Workflow — он детерминирован. Это значит, что при одном и том же наборе входных данных он всегда будет выполнять одни и те же шаги в одном и том же порядке . Это нужно для "волшебства" Temporal — возможности переигрывать историю выполнения после сбоя.

Что можно делать:

- Вызывать другие шаги (Activities)
- Запускать таймеры (sleep)
- Ждать сигналов от других систем

Что нельзя делать внутри Workflow:

- Генерировать случайные числа
- Обращаться напрямую к базам данных или API
- Пытаться получить текущее время системы

Для этого есть отдельная сущность — Activities.

#### 2. Действие — **Activity**
Простыми словами: Это конкретное полезное действие вашей программы. Именно то место, где происходит "настоящая" работа.

Расшифровка: Activity — это обычная функция, которая делает что-то "опасное" и "нестабильное": вызывает API, пишет в базу данных, отправляет email. В отличие от Workflow, Activity может и должен быть недетерминированным — он может зависеть от времени, случайных чисел и ответов внешних сервисов.

Главная "фишка": Temporal берет на себя всю боль работы с Activity. Если ваша функция завершилась ошибкой (сервис недоступен, упал таймаут), Temporal автоматически перезапустит её согласно вашей политике повторов. Вам не нужно писать циклы retry вручную.

#### 3. Очередь задач — Task Queue
Простыми словами: Это полка с заказами, куда Temporal кладет задания, а ваши программы забирают их, чтобы выполнить.

Расшифровка: Это канал связи между сервером Temporal и вашими воркерами (о них ниже). Когда Workflow'у нужно выполнить Activity, он кладет задачу в Task Queue. А Worker, который слушает эту очередь, забирает задачу и выполняет ее. Это позволяет легко масштабироваться: вы можете запустить много Worker'ов, слушающих одну очередь, и они сами разберут между собой задачи.

#### 4. Исполнитель — Worker
Простыми словами: Это настоящий исполнитель, который берет задачу из очереди и делает её.

Расшифровка: Worker — это обычный процесс, служба или под, который вы запускаете у себя на серверах. Он постоянно "слушает" Task Queue. Как только появляется задача, Worker запускает нужный код (Workflow или Activity), выполняет его и отправляет результат обратно на сервер Temporal. Temporal не выполняет ваш код сам — он только координирует, а всю работу делают ваши Worker'ы.

#### 5. Сервер Temporal — Temporal Server
Простыми словами: Это мозг и память всей системы.

Расшифровка: Это центральный сервис, который вы поднимаете (или берете в облаке Temporal Cloud). Он:

- Хранит всю историю выполнения Workflow'ов
- Отвечает на вопрос "что делать дальше?"
- Следит за таймерами
- Отправляет задачи в очереди
- Показывает красивые дашборды

Самый важный трюк сервера — Event Sourcing (хранение событий). Он не хранит переменные вашей программы. Вместо этого он хранит журнал событий: "старт", "запущено Activity", "Activity завершено", "установлен таймер". Если ваш Worker упал, новый Worker просто перечитывает этот журнал и воспроизводит (replays) код Workflow за микросекунды, восстанавливая состояние.

#### 6. Сигнал, Запрос, Обновление — Signal, Query, Update
Простыми словами: Это способы поговорить с Workflow'ом, пока он работает.

Расшифровка:

- Signal (Сигнал) — асинхронное "кивнуть" Workflow'у. Отправили и забыли. Например, "пользователь нажал кнопку 'Отмена'". Workflow получит этот сигнал, когда будет готов.
- Query (Запрос) — спросить у Workflow'а "как дела?" в любой момент. Например, "на каком ты сейчас проценте?" Это не меняет состояние, просто чтение.
- Update (Обновление) — то же самое, что Signal, но синхронное. Вы отправляете команду и ждете ответа, что Workflow её выполнил. Например, "добавь товар в корзину" и верни мне новое содержимое корзины.

#### Собираем всё вместе (Как это выглядит в коде?)
Вместо "Hello World", давайте представим простой заказ в интернет-магазине:

1. Клиент запускает Workflow ProcessOrder.
2. Workflow ставит в Task Queue задачу выполнить Activity checkPayment.
3. Worker (исполнитель) забирает задачу, списывает деньги и возвращает "ОК".
4. Workflow получает "ОК" и ставит новую задачу — Activity reserveStock.

Внезапно сервер с Worker'ом упал. Temporal Server видит, что задача не завершена.

Через минуту вы запускаете новый Worker. Он приходит, забирает задачу reserveStock и выполняет ее.

Workflow завершен. Всё работает, никто ничего не потерял.

### Шпаргалка по терминам
Термин	Аналогия из жизни	Главная особенность
Workflow	План стройки	Детерминирован, описывает "что делать", может ждать годами 
Activity	Бригада рабочих	Ненадежен, делает реальную работу, умеет сам перезапускаться 
Task Queue	Доска объявлений	Связывает Workflow и Workers, позволяет масштабировать исполнителей 
Worker	Рабочий	Забирает задачи из очереди и выполняет код 
Temporal Server	Прораб + Архив	Хранит историю, восстанавливает после сбоев 
Signal / Query	Позвонить / Спросить	Способ общения с процессом "на ходу" 
