# Контейнеры Flat Parser — детальное описание

---

## Быстрая шпаргалка — все контейнеры одной таблицей

Docker Compose и именует контейнеры по шаблону: `flat-parser-<имя-сервиса>-1`.

| Сервис в compose.yml | Имя контейнера | Работает всегда? | Как проверить статус | Как смотреть логи |
|----------------------|----------------|------------------|---------------------|-------------------|
| `browser` | `flat-parser-browser-1` | Да | `docker ps \| grep browser` | `docker logs flat-parser-browser-1` |
| `migrations` | `flat-parser-migrations-1` | Нет (одноразовый) | `docker ps -a \| grep migrations` | `docker logs flat-parser-migrations-1` |
| `beat` | `flat-parser-beat-1` | Да | `docker ps \| grep beat` | `docker logs flat-parser-beat-1` |
| `worker` | `flat-parser-worker-1` | Да | `docker ps \| grep worker-1` | `docker logs flat-parser-worker-1` |
| `trendagent-worker` | `flat-parser-trendagent-worker-1` | Да | `docker ps \| grep trendagent-worker` | `docker logs flat-parser-trendagent-worker-1` |
| `watch-trendagent-token-updates` | `flat-parser-watch-trendagent-token-updates-1` | Да | `docker ps \| grep watch-trendagent` | `docker logs flat-parser-watch-trendagent-token-updates-1` |
| `rabbitmq` | `flat-parser-rabbitmq-1` | Да | `docker ps \| grep rabbitmq` | `docker logs flat-parser-rabbitmq-1` |
| `valkey` | `flat-parser-valkey-1` | Да | `docker ps \| grep valkey` | `docker logs flat-parser-valkey-1` |

### Полезные команды

```bash
# Все контейнеры flat-parser — статус
docker ps -a --filter "name=flat-parser" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Все работающие контейнеры
docker ps --filter "name=flat-parser"

# Логи конкретного контейнера (последние 50 строк)
docker logs --tail 50 flat-parser-<имя>-1

# Логи в реальном времени (follow)
docker logs -f flat-parser-<имя>-1

# Перезапуск одного контейнера
docker compose restart <имя-сервиса>

# Пересборка и перезапуск
docker compose up -d --build <имя-сервиса>

# Проверить что все запущено
docker compose ps
```

---

## 1. `flat-parser-browser-1` — Headless-браузер

> **Логи:** `docker logs flat-parser-browser-1`

**Образ:** собирается из `browser/Dockerfile` (Debian Bookworm + Chromium)
**Порты:** `9222:9222`
**Restart:** always
**Tmpfs:** `/tmp`, `/root`

### За что отвечает

Предоставляет удалённый headless Chromium, к которому подключаются парсеры (через библиотеку Nodriver) для рендеринга JavaScript-сайтов, авторизации и взаимодействия со страницами. Используется тремя парсерами: TrendAgent, Наш.Дом.РФ и Продома.Дом.РФ.

### Что внутри

**Dockerfile устанавливает:**
- `chromium` — браузер
- `xvfb` — виртуальный фреймбуфер (эмулирует дисплей без монитора)
- `xauth` — авторизация X11
- `nginx` — прокси для DevTools-порта

**`docker-entrypoint.sh` запускает два процесса:**

1. **nginx** — проксирует входящие соединения с порта 9222 на внутренний порт 9223 (`default.conf`). Это нужно потому, что Chromium внутри контейнера слушает 9223, а наружу отдаётся 9222.

2. **Chromium через `xvfb-run`** — запускает браузер с флагами:
   - `--remote-debugging-port=9223` — открывает DevTools-порт для внешнего управления (Nodriver подключается именно сюда)
   - `--no-sandbox` — отключает песочницу (работаем в Docker, безопасно)
   - `--disable-gpu` — без GPU-ускорения (сервер без видеокарты)
   - `--disable-component-update`, `--disable-background-networking` — отключает автообновления и фоновую активность
   - `--disk-cache-size=0` — отключает дисковый кеш (всё в памяти)
   - `--window-size=1920,1080` — эмулирует десктопное разрешение
   - `--user-data-dir=$(mktemp -d)` — каждый запуск с чистым профилем
   - `--process-per-site` — один процесс на сайт (упрощает управление)

**Для чего используется:**
- **TrendAgent** — авторизация по телефону/паролю, получение auth_token из cookies
- **Наш.Дом.РФ** — обход капчи-слайдера, парсинг карточек объектов
- **Продома.Дом.РФ** — авторизация, получение accessToken из localStorage

---

## 2. flat-parser — Образ приложения (НЕ контейнер)

> Это **базовый образ** из `Dockerfile`. Сам по себе не запускается. На его основе работают следующие контейнеры:
> - `flat-parser-beat-1`
> - `flat-parser-worker-1`
> - `flat-parser-trendagent-worker-1`
> - `flat-parser-watch-trendagent-token-updates-1`
> - `flat-parser-migrations-1`
> 
> Если меняешь код — нужно пересобрать образ: `docker compose up -d --build`

---

## 3. `flat-parser-migrations-1` — Миграции БД

> **Важно:** Одноразовый контейнер. После завершения — останавливается. Это **нормально**.

**Образ:** `flat-parser`
**Команда:** `uv run python manage.py migrate && uv run python manage.py create_sql_view`
**Сеть:** из `.env`
**Logging:** syslog, тег `flat_parser_migrations`

### За что отвечает

Одноразовый контейнер, который:
1. Применяет все незавершенные Django-миграгии к базе данных MySQL
2. Создает (или обновляет) SQL VIEW `ДанныеПарсинга` — объединяющее представление из 14 таблиц

### Что происходит

1. **`manage.py migrate`** — стандартная Django-миграция:
   - Создает таблицы всех моделей (`Застройщик`, `ЖК`, `ErzЖК`, `TrendAgentЖК`, `Квартира`, `АкцияВКвартире` и т.д.)
   - Применяет встроенные миграции Django (`auth`, `sessions`, `contenttypes`)

2. **`manage.py create_sql_view`** — кастомная команда, выполняющая:
   ```sql
   CREATE OR REPLACE VIEW ДанныеПарсинга AS
   SELECT ... FROM Застройщик
     LEFT JOIN ЖК ON ...
     LEFT JOIN ErzЖК ON ...
     LEFT JOIN TrendAgentЖК ON ...
     LEFT JOIN ErzЛитер ON ...
     LEFT JOIN TrendAgentЛитер ON ...
     LEFT JOIN Квартира ON ...
     LEFT JOIN НашДомЛитер ON ...
     LEFT JOIN ПроектнаяДекларация ON ...
     LEFT JOIN АкцияВКвартире ON ...
     LEFT JOIN РассрочкаКвартиры ON ...
   ```

После завершения контейнер останавливается и больше не запускается (нет `restart: always`).

---

## 4. `flat-parser-beat-1` — Планировщик задач (Celery Beat)

> **Логи:** `docker logs flat-parser-beat-1`

**Образ:** `flat-parser`
**Команда:** `uv run celery -A conf.celery_app beat -l info`
**Сеть:** host
**Restart:** always
**Env:** `C_FORCE_ROOT=true`

### За что отвечает

Планировщик, который по расписанию отправляет сообщения в очередь RabbitMQ, запуская периодические задачи парсинга.

### Что происходит

Celery Beat читает `conf/celery_app.py` и по cron-расписанию отправляет задачи:

| Время (МСК) | Задача | Очередь | Описание |
|-------------|--------|---------|----------|
| **Ежедневно 20:00** | `parser.parse-all` | flat-parser | Полный цикл: парсинг ERZ → Наш.Дом.РФ → Проектные декларации |
| **Пятница 09:00** | `parser.parse-trendagent` | flat-parser-trendagent | Парсинг ЖК, литер и квартир TrendAgent |
| **Ежедневно 20:00** | `parser.parse-installments` | flat-parser | Рассрочки из Google Sheets |
| **Ежедневно 20:00** | `parser.parse-prodoma` | flat-parser | Отчеты из Продома.Дом.РФ |

**`C_FORCE_ROOT=true`** — разрешает запуск Celery от root (Docker-контейнер работает под root).

Beat **не выполняет** задачи сам — только кладет сообщения в RabbitMQ. Исполняют их worker'ы.

---

## 5. `flat-parser-worker-1` — Основной обработчик задач

> **Логи:** `docker logs flat-parser-worker-1`

**Образ:** `flat-parser`
**Команда:** `xvfb-run -a uv run celery -A conf.celery_app worker -c "${CONCURRENCY:-1}" -l info -Q flat-parser`
**Сеть:** host
**IPC:** host
**Restart:** always

### За что отвечает

Выполняет задачи Celery из основной очереди `flat-parser`. Это «рабочая лошадка» системы — именно здесь происходит парсинг.

### Что происходит

1. **`xvfb-run -a`** — запускает Celery worker внутри виртуального фреймбуфера. Это нужно потому, что некоторые задачи используют Nodriver для работы с headless-браузером, а браузеру нужен X11-дисплей.

2. **`-c "${CONCURRENCY:-1}"`** — количество одновременых процессов (по умолчанию 1). При 1 процессе задачи выполняются строго последовательно.

3. **`-Q flat-parser`** — слушает только очередь `flat-parser`.

4. **`CELERY_WORKER_MAX_TASKS_PER_CHILD = 1`** (из settings) — после выполнения каждой задачи процесс worker'а перезапускается. Это предотвращает утечки памяти и «залипание» соединений с БД.

### Обрабатываемые задачи:

- **`parser.parse-all`** — последовательно вызывает:
  - `erz.DevelopersParser().parse()` → парсит 33 застройщиков из ERZ
  - `root.RootParser().parse()` → создает связи Developer ↔ Complex
  - `erz.ComplexParser().parse()` → парсит 60+ ЖК из ERZ
  - `erz.LiterParser().parse()` → парсит литеры (корпуса) из ERZ
  - `erz.FloorsParser().parse()` → агрегирует этажность
  - `nash_dom.LiterParser().parse()` → парсит Наш.Дом.РФ (с обходом капчи)
  - `project_declaration.LiterDeclarationParser().parse()` → парсит PDF деклараций

- **`parser.parse-installments`** → `trendagent_installments.InstallmentsParser().parse()` — скачивает 9 Excel-файлов из Google Sheets, парсит через Polars

- **`parser.parse-prodoma`** → `prodoma_dom_rf.ComplexReportParser().parse()` — авторизуется на Продома.Дом.РФ, собирает отчеты по ЖК

- **`request_flat_info`** (подзадача) — детальная информация по одной квартире + акции + отделки (отправляется из `FlatParser`)

---

## 6. `flat-parser-trendagent-worker-1` — Worker для TrendAgent

> **Логи:** `docker logs flat-parser-trendagent-worker-1`
> **Тот самый контейнер, который упал с JSONDecodeError**

**Образ:** `flat-parser`
**Команда:** `xvfb-run -a uv run celery -A conf.celery_app worker -c "${CONCURRENCY:-1}" -l info -Q flat-parser-trendagent`
**Сеть:** host
**IPC:** host
**Restart:** always

### За что отвечает

Выделенный worker исключительно для задач парсинга TrendAgent. Изолирован от основной очереди.

### Что происходит

Аналогичен основному worker'у, но слушает только очередь `flat-parser-trendagent`.

### Обрабатываемые задачи:

- **`parser.parse-trendagent`** — последовательно:
  - `trendagent.ComplexAndLiterParser().parse()` → для каждого из 7 городов:
    - Запрашивает список всех ЖК через API search
    - Для каждого ЖК получает детальную информацию (планировки, характеристики)
    - Создает/обновляет `TrendAgentComplex` и `TrendAgentLiter`
  - `trendagent.FlatParser().parse()` → для каждого ЖК:
    - Запрашивает все квартиры через API
    - Для каждой квартиры отправляет `request_flat_info` как отдельную Celery-задачу (с retry × 3)

- **`request_flat_info`** — для одной квартиры:
  - Получает детали квартиры через API unified
  - Создает/обновляет `Flat`
  - Получает и сохраняет акции (`FlatAction`)
  - Получает и сохраняет варианты отделки (`FlatFinishing`)

**Зачем отдельный worker:** TrendAgent — самый объемный источник (сотни квартир на каждый ЖК). Отдельная очередь и worker предотвращают блокировку остальных задач.

---

## 7. `flat-parser-watch-trendagent-token-updates-1` — Мониторинг токена TrendAgent

> **Логи:** `docker logs flat-parser-watch-trendagent-token-updates-1`
> **Критичный!** Если он упал — токен протухнет, и весь TrendAgent перестанет работать

**Образ:** `flat-parser`
**Команда:** `xvfb-run -a uv run python manage.py watch_trendagent_token_updates`
**Сеть:** host
**IPC:** host
**Restart:** always

### За что отвечает

Постоянно работающий процесс, который поддерживает актуальный auth_token TrendAgent в Valkey. Без этого токена все запросы к API TrendAgent будут неуспешными.

### Что происходит

1. **Запускает headless Chromium** через Nodriver с прокси
2. **Открывает** `https://sso.trendagent.ru/login`
3. **Авторизуется** — вводит телефон и пароль, кликает кнопку входа
4. **Цикл мониторинга** (бесконечный):
   - Каждые 20 секунд перезагружает страницу
   - Читает все cookies браузера
   - Ищет cookie `auth_token`
   - Сравнивает с текущим значением в Valkey
   - Если отличается — записывает новый токен в Valkey и логирует изменение
   - Таймаут 60 секунд → выход из цикла → перезапуск браузера (защита от утечек)

**Почему это важно:** TrendAgent периодически инвалидирует сессии. Этот процесс автоматически переавторизуется и обновляет токен для всех остальных парсеров.

---

## 8. `flat-parser-rabbitmq-1` — Брокер сообщений

> **Логи:** `docker logs flat-parser-rabbitmq-1`
> **Web UI:** `http://<host>:15672` (логин guest / пароль guest)

**Образ:** `rabbitmq:4.0.7-management-alpine`
**Порты:** `5672` (AMQP), `15672` (Web UI), `15692` (Prometheus)
**Restart:** always

### За что отвечает

Центральная очередь сообщений. Beat кладет задачи, worker'ы их забирают.

### Что внутри

**Включенные плагины** (`config/rabbitmq_enabled_plugins`):**
- `rabbitmq_management` — веб-интерфейс управления (порт 15672)
- `rabbitmq_management_agent` — агент управления
- `rabbitmq_prometheus` — метрики для мониторинга (порт 15692)
- `rabbitmq_web_dispatch` — HTTP-диспетчер для UI

**Очереди:**
- `flat-parser` — основная очередь (ERZ, Наш.Дом.РФ, Продома.Дом.РФ, рассрочки)
- `flat-parser-trendagent` — очередь TrendAgent (ЖК, квартиры, акции)

**Данные:** хранятся в памяти контейнера (без volume — при перезапуске очереди очищаются).

---

## 9. `flat-parser-valkey-1` — Кеш и хранилище токенов

> **Логи:** `docker logs flat-parser-valkey-1`
> **Проверить токен:** `docker exec -it flat-parser-valkey-1 valkey-cli GET auth_token`

**Образ:** `valkey/valkey:8.0.2-alpine3.21`
**Порты:** `6379:6379`
**Restart:** always

### За что отвечает

In-memory хранилище на базе Valkey (Redis-совместимый форк). Используется для:

1. **auth_token TrendAgent** — ключ `auth_token` в Valkey хранит актуальный токен авторизации
   - Записывается процессом `watch-trendagent-token-updates`
   - Читается всеми запросами к TrendAgent через `utils.get_auth_token()`

2. **Кеширование** — потенциально может использоваться для кеширования любых данных

### Что внутри

Valkey — это форк Redis, созданный после смены лицензии Redis Labs. Полностью совместим с Redis-клиентами. В данном проекте используется библиотека `valkey>=6.1.0` (Python-клиент).

**Команды:**
- `valkey_client.set("auth_token", token)` — записать токен
- `valkey_client.get("auth_token")` — прочитать токен

---

## 10. `flat-parser-mariadb-1` — База данных (только dev)

> **Логи:** `docker logs flat-parser-mariadb-1`
> **Только для разработки!** В production — внешний MySQL по `.env`

**Образ:** `mariadb:11.4.2-ubi`
**Сеть:** host
**Пароль root:** `root`
**Volume:** `/srv/_flat_parser_db:/var/lib/mysql`

### За что отвечает

Relational СУБД для хранения всех распарсенных данных. В production используется внешний MySQL (адрес из `.env`).

### Что внутри

- **14 таблиц** моделей + встроенные таблицы Django (`auth_user`, `django_session`, `django_migrations` и т.д.)
- **SQL VIEW** `ДанныеПарсинга` — объединяющее представление
- **Данные** хранятся в `/srv/_flat_parser_db` — сохраняются между перезапусками

**Доступ:** `root:root@localhost:3306`

---

## Схема взаимодействия контейнеров

```
                                   ┌─────────────────┐
                                   │    Beat (cron)  │
                                   └────────┬────────┘
                                            │ задачи
                                   ┌────────▼─────────┐
                                   │    RabbitMQ      │
                                   └──┬───────────┬───┘
                                      │           │
                        ┌─────────────▼─┐   ┌─────▼──────────┐
                        │    worker     │   │ trendagent-    │
                        │ (flat-parser) │   │ worker         │
                        └──┬──────┬─────┘   └──┬──────┬──────┘
                           │      │            │      │
                  ┌────────▼┐     │       ┌────▼──┐   │
                  │  browser│     │       │MySQL  │   │
                  │  :9222  │     │       │:3306  │   │
                  └─────────┘     │       └───────┘   │
                                  │                   │
                    ┌─────────────┘                   │
                    │                                 │
           ┌────────▼────────────┐                    │
           │ watch-trendagent-   │                    │
           │ token-updates       │                    │
           └────────┬────────────┘                    │
                    │ записывает auth_token           │
           ┌────────▼────────────┐                    │
           │      Valkey         │◄───────────────────┘
           │      :6379          │  (читают auth_token)
           └─────────────────────┘
```

---

## Сводная таблица

| Контейнер | Процесс | Потребление ресурсов | Критичность |
|-----------|---------|---------------------|-------------|
| browser | Chromium + nginx | ~200-500 МБ RAM | Высокая (без него нет парсинга с JS) |
| migrations | Django migrate (одноразово) | ~100 МБ RAM | Средняя (только при деплое) |
| beat | Celery Beat | ~50 МБ RAM | Высокая (без него нет расписания) |
| worker | Celery worker (1+ процесс) | ~200-400 МБ RAM | Высокая (основной парсинг) |
| trendagent-worker | Celery worker (1+ процесс) | ~200-400 МБ RAM | Высокая (парсинг TrendAgent) |
| watch-trendagent-token-updates | Nodriver + Chromium | ~300-500 МБ RAM | Высокая (без токена TrendAgent не работает) |
| rabbitmq | Erlang + RabbitMQ | ~100-200 МБ RAM | Высокая (центральная очередь) |
| valkey | Valkey | ~10-50 МБ RAM | Высокая (хранение токенов) |
| mariadb (dev) | MariaDB | ~200-500 МБ RAM | Высокая (хранение данных) |

---

## Диагностика — что проверить когда что-то сломалось

### Шаг 1: Какие контейнеры вообще работают?

```bash
docker compose ps
```

Вывод покажет все сервисы. Смотри колонку **State**:
- `Up` — работает
- `Exit 0` — нормально завершился (это ок для `migrations`)
- `Exit 1` / `Restarting` — **проблема**, смотри логи

### Шаг 2: Читаем логи

**Главный порядок при ошибке TrendAgent (JSONDecodeError):**

```bash
# 1. Есть ли токен в Valkey?
docker exec -it flat-parser-valkey-1 valkey-cli GET auth_token

# Если nil — токен не записан. Смотри контейнер обновления токена:
docker logs flat-parser-watch-trendagent-token-updates-1

# 2. Работает ли браузер?
docker logs flat-parser-browser-1

# 3. Логи worker'а с ошибкой:
docker logs --tail 100 flat-parser-trendagent-worker-1
```

### Шаг 3: Перезапуск после исправления кода

```bash
# Пересобрать образ и перезапустить worker'ов:
docker compose up -d --build trendagent-worker worker

# Перезапустить только один контейнер (без пересборки):
docker compose restart trendagent-worker
```

### Частые проблемы

| Симптом | Причина | Что делать |
|---------|---------|------------|
| `JSONDecodeError` в trendagent-worker | Токен протух / отсутствует | Проверить `watch-trendagent-token-updates`, перезапустить |
| `auth_token` = nil в Valkey | Процесс обновления не запустился | `docker logs flat-parser-watch-trendagent-token-updates-1` |
| Контейнер `Restarting` | Крашится при старте | `docker logs flat-parser-<имя>-1` |
| `migrations` в статусе `Exit 0` | Это **норма**, он одноразовый | Не трогать |
| Браузер не отвечает | Chromium упал | `docker compose restart browser` |
| Worker завис | Задача долго висит | `docker compose restart worker` или `trendagent-worker` |
