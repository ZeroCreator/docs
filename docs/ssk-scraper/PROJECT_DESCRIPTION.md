# Flat Parser — Проектная документация

## Обзор проекта

**Flat Parser** — это автоматизированная система парсинга данных о застройщиках, жилых комплексах, квартирах и связанной информации с российских порталов недвижимости. Проект построен на базе Django + Celery и использует распределенную архитектуру с браузерной автоматизацией.

**Автор:** Eduard Stromenko (estromenko@mail.ru)

**Технологический стек:**
- Python 3.12
- Django 5.0.7+
- Celery 5.4.0+
- MySQL/MariaDB 11.4
- RabbitMQ 4.0.7
- Valkey 8.0.2 (Redis-совместимый кеш)
- Docker & Docker Compose
- Nodriver (автоматизация браузера на базе Chromium)
- Polars (обработка Excel-файлов)
- Sentry (мониторинг ошибок)

---

## Архитектура системы

### Контейнеры (Production)

Система состоит из следующих сервисов в `compose.yml`:

| Сервис | Описание |
|--------|----------|
| **browser** | Контейнер с Chromium + Xvfb + nginx для headless-браузерной автоматизации (порт 9222) |
| **flat-parser** | Основной образ приложения (Django) |
| **migrations** | Одноразовый контейнер для применения миграций и создания SQL-представления |
| **beat** | Celery Beat — планировщик периодических задач |
| **worker** | Основной Celery worker (с Xvfb для headless-браузера) |
| **trendagent-worker** | Отдельный worker для задач TrendAgent (очередь `flat-parser-trendagent`) |
| **watch-trendagent-token-updates** | Долгоживущий процесс для мониторинга и обновления auth_token TrendAgent |
| **captcha-solver** | Сервис распознавания капчи (порт 4396), образ `rotate-captcha-crack` |
| **rabbitmq** | Брокер сообщений (порты 5672, 15672, 15692) |
| **valkey** | Кеш/хранилище auth_token (порт 6379) |

### Development

В `compose-dev.yml` упрощенная конфигурация:
- **mariadb** — локальная БД (сеть host, пароль root, данные в `/srv/_flat_parser_db`)
- **rabbitmq** — брокер (сеть host)
- **valkey** — кеш (порт 6379)

---

## Источники данных (парсеры)

Проект парсит данные из **5 источников**:

### 1. ERZ (erzrf.ru) — Единый ресурс застройщиков

**Модуль:** `parser/resources/erz.py`

| Класс | Что парсит |
|-------|------------|
| `DevelopersParser` | Информация о застройщиках: название, сайт, регионы, строящиеся/сданные ЖК |
| `ComplexParser` | Данные о жилых комплексах: адрес, бренд, телефоны, оценка ЕРЗ, медали, преимущества |
| `LiterParser` | Информация о литерах (корпусах): стадия строительства, срок сдачи, класс, этажность |
| `FloorsParser` | Этажность ЖК (агрегирует данные из литер) |

**API endpoints:**
- `https://erzrf.ru/erz-rest/api/v1/gk/lists` — список ЖК
- `https://erzrf.ru/erz-rest-oa/brand/{id}` — данные застройщика
- `https://erzrf.ru/erz-rest/api/v1/gk/index/{id}` — данные ЖК
- `https://erzrf.ru/erz-rest/api/v1/gk/tabs` — литеры ЖК
- `https://erzrf.ru/erz-rest/api/v1/buildinfo/{id}` — детали литерa

### 2. TrendAgent (api.trendagent.ru)

**Модуль:** `parser/resources/trendagent.py`

| Класс | Что парсит |
|-------|------------|
| `ComplexAndLiterParser` | ЖК и литеры: планировки, описания, характеристики, вознаграждения АН |
| `FlatParser` | Квартиры: площади, цены, отделка, окна, статус |
| `request_flat_info` (Celery task) | Детальная информация по каждой квартире + акции + отделки |

**Города:** MSK, SPB, KRD, NSK, RND, KZN, EKB

**API endpoints:**
- `https://api.trendagent.ru/v4_29/blocks/search/` — поиск ЖК
- `https://api.trendagent.ru/v4_29/blocks/{id}/unified/` — детали ЖК
- `https://api.trendagent.ru/v4_29/apartments/search/` — поиск квартир
- `https://api.trendagent.ru/v4_29/apartments/{id}/unified/` — детали квартиры
- `https://rewards.trendagent.ru/search` — вознаграждения АН
- `https://discounts.trendagent.ru/apartments/{id}/discounts` — акции/скидки
- `https://api.trendagent.ru/v4_29/finishings/apartment/{id}/` — варианты отделки

**Авторизация:** auth_token из cookies, обновляется через `trendagent_update_token.py` (headless-браузер → Valkey)

### 3. Наш.Дом.РФ (xn--80az8a.xn--d1aqf.xn--p1ai)

**Модуль:** `parser/resources/nash_dom.py`

| Класс | Что парсит |
|-------|------------|
| `LiterParser` | Корпуса: средняя цена за м², распроданность, количество квартир, проектная декларация |

**Особенности:**
- Использует Nodriver для headless-браузера
- Распознает капчу через локальный сервис (`http://127.0.0.1:4396`)
- Соотносит литеры с данными TrendAgent по названию/номеру

### 4. Проектные декларации (PDF)

**Модуль:** `parser/resources/project_declaration.py`

| Класс | Что парсит |
|-------|------------|
| `LiterDeclarationParser` | Данные из PDF проектных деклараций: условный номер, назначение, этаж, подъезд, площади, высота потолков |

**Механизм:** Скачивает PDF по ссылке из Наш.Дом.РФ → извлекает таблицу через pypdf → нормализует → сопоставляет с квартирами TrendAgent

### 5. Продома.Дом.РФ (xn--80ahygbdh.xn--d1aqf.xn--p1ai)

**Модуль:** `parser/resources/prodoma_dom_rf.py`

| Класс | Что парсит |
|-------|------------|
| `ComplexReportParser` | Отчеты по ЖК: продажи, площади, средневзвешенные цены, статистика |

**Особенности:**
- Авторизация через headless-браузер (Nodriver)
- Получает access_token из localStorage
- 4 API-эндпоинта для различных отчетов

### 6. Рассрочки TrendAgent (Google Sheets)

**Модуль:** `parser/resources/trendagent_installments.py`

| Класс | Что парсит |
|-------|------------|
| `InstallmentsParser` | Данные о рассрочках по городам из Google Sheets (Excel) |

**Города:** Москва, Санкт-Петербург, Краснодарский край, Ростов-на-Дону, Новосибирск, Уфа, Казань, Екатеринбург, Крым

**Механизм:** Скачивает Excel → парсит через Polars → записывает в БД

---

## Модели данных

### Основные модели (`parser/models.py`)

| Модель | Таблица | Описание |
|--------|---------|----------|
| `Developer` | `Застройщик` | Застройщики (ERZ ID, название, сайт, регионы) |
| `Complex` | `ЖК` | Связующая таблица между ERZ и внешними источниками |
| `ErzComplex` | `ErzЖК` | ЖК из ERZ (адрес, оценка, медали, преимущества) |
| `TrendAgentComplex` | `TrendAgentЖК` | ЖК из TrendAgent (характеристики, планы, вознаграждения) |
| `ErzLiter` | `ErzЛитер` | Литера (корпуса) из ERZ |
| `TrendAgentLiter` | `TrendAgentЛитер` | Литера из TrendAgent |
| `NashDomLiter` | `НашДомЛитер` | Литера из Наш.Дом.РФ |
| `Flat` | `Квартира` | Квартиры (площади, цены, характеристики) |
| `FlatAction` | `АкцияВКвартире` | Акции и скидки на квартиры |
| `FlatInstallment` | `РассрочкаКвартиры` | Рассрочки на квартиры |
| `FlatFinishing` | `ОтделкаКвартиры` | Варианты отделки квартир |
| `LiterDeclaration` | `ПроектнаяДекларация` | Данные из проектных деклараций |
| `Installment` | `Рассрочка` | Общие данные по рассрочкам (из Google Sheets) |
| `ComplexReport` | `ОтчетыПоЖК` | Отчеты по ЖК из Продома.Дом.РФ |

### SQL-представление

`create_sql_view.py` создает VIEW `ДанныеПарсинга`, объединяющую все основные таблицы через LEFT JOIN для удобного анализа данных.

---

## Расписание задач (Celery Beat)

| Задача | Расписание | Очередь | Описание |
|--------|------------|---------|----------|
| `parser.parse-all` | Ежедневно в 20:00 | flat-parser | Полный цикл: ERZ → Наш.Дом.РФ → Проектные декларации |
| `parser.parse-trendagent` | Пятница 09:00 | flat-parser-trendagent | Парсинг TrendAgent (ЖК, литеры, квартиры) |
| `parser.parse-installments` | Ежедневно в 20:00 | flat-parser | Парсинг рассрочек из Google Sheets |
| `parser.parse-prodoma` | Ежедневно в 20:00 | flat-parser | Отчеты из Продома.Дом.РФ |

---

## Конфигурация

### Переменные окружения (.env)

| Переменная | Описание |
|------------|----------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | Режим отладки (true/false) |
| `ALLOWED_HOSTS` | Разрешенные хосты |
| `MYSQL_NAME` | Имя БД (по умолчанию mysql) |
| `MYSQL_HOST` | Хост MySQL |
| `MYSQL_PORT` | Порт MySQL (3306) |
| `MYSQL_USER` | Пользователь MySQL |
| `MYSQL_PASSWORD` | Пароль MySQL |
| `TRENDAGENT_PASSWORD` | Пароль для TrendAgent |
| `TRENDAGENT_PHONE` | Телефон для TrendAgent |
| `TRENDAGENT_CITY` | Город для TrendAgent |
| `PRODOMA_DOM_RF_USERNAME` | Логин для Продома.Дом.РФ |
| `PRODOMA_DOM_RF_PASSWORD` | Пароль для Продома.Дом.РФ |
| `RABBITMQ_HOST` | Хост RabbitMQ |
| `RABBITMQ_PORT` | Порт RabbitMQ |
| `VALKEY_HOST` | Хост Valkey |
| `VALKEY_PORT` | Порт Valkey |
| `BROWSER_HOST` | Хост headless-браузера |
| `BROWSER_PORT` | Порт headless-браузера (9222) |
| `PROXY_ADDRESS` | Адрес прокси |
| `PROXIES` | Список прокси (через запятую) |
| `SENTRY_DSN` | DSN для Sentry |
| `REQUEST_TIMEOUT` | Таймаут запросов (10) |
| `REQUEST_RETRIES` | Количество повторов (5) |
| `REQUEST_RETRIES_DELAY_SECONDS` | Задержка между повторами (20) |
| `CONCURRENCY` | Количество Celery worker'ов |
| `WATCHTOWER_POLL_INTERVAL` | Интервал проверки обновлений (60) |

---

## Справочники

### Застройщики (`DEVELOPER_IDS`)

В проекте определено **33 застройщика** с маппингом ID между ERZ и TrendAgent:
- dogma, tochno, ssk, insiti, jugstrojinvest, metriks, ekaterinodarinvest-stroj, neometrija, vkb-novostrojki, ksk, evropeja, semja, romeks, kapital-invest, bauinvest, development-jug, garantija, darstroj, alfastrojinvest, strojgrad, rks-development, uk-jug, flagman, jug-inzhiniring, sarmat, tikhoreckgazstroj, m2-development, kamber, kontinent, nvm и др.

### ЖК (`COMPLEX_IDS`)

**60+ жилых комплексов** с маппингом между 4 источниками (ERZ, TrendAgent, Наш.Дом.РФ, Продома.Дом.РФ).

---

## Структура проекта

```
flat-parser/
├── conf/                     # Конфигурация Django
│   ├── settings.py           # Настройки (БД, Celery, парсеры, Sentry)
│   ├── celery_app.py         # Celery app + расписание Beat
│   ├── urls.py               # URL-маршруты (только admin)
│   └── wsgi.py
├── parser/                   # Основное приложение Django
│   ├── models.py             # 14 моделей данных
│   ├── tasks.py              # Celery-задачи (4 основные задачи)
│   ├── admin.py              # Admin-интерфейс для всех моделей
│   ├── utils.py              # Утилиты: fetch() с retry, get_auth_token()
│   ├── valkey.py             # Клиент Valkey
│   ├── resources/            # Парсеры
│   │   ├── erz.py            # Парсер ERZ
│   │   ├── trendagent.py     # Парсер TrendAgent
│   │   ├── nash_dom.py       # Парсер Наш.Дом.РФ
│   │   ├── project_declaration.py  # Парсер проектных деклараций (PDF)
│   │   ├── prodoma_dom_rf.py # Парсер Продома.Дом.РФ
│   │   ├── trendagent_installments.py  # Парсер рассрочек (Google Sheets)
│   │   ├── trendagent_update_token.py  # Обновление auth_token TrendAgent
│   │   └── root.py           # RootParser (связь Developer ↔ Complex)
│   └── management/commands/  # Пользовательские команды
│       ├── create_sql_view.py        # Создание SQL VIEW
│       └── watch_trendagent_token_updates.py  # Мониторинг токена TrendAgent
├── browser/                  # Контейнер headless-браузера
│   ├── Dockerfile            # Debian + Chromium + Xvfb + nginx
│   ├── default.conf          # Конфигурация nginx
│   └── docker-entrypoint.sh  # Скрипт запуска
├── config/
│   └── rabbitmq_enabled_plugins  # Плагины RabbitMQ
├── misc/
│   └── Шахматка.xlsx         # Пример выходного файла (шахматка)
├── compose.yml               # Production Docker Compose
├── compose-dev.yml           # Development Docker Compose
├── Dockerfile                # Образ приложения
├── pyproject.toml            # Зависимости (uv)
├── flake.nix                 # Nix flake для dev-окружения
└── manage.py                 # Django management
```

---

## Запуск

### Development

```bash
# Установка системных зависимостей
sudo apt-get install -y clang pkg-config libmysqlclient-dev mysql-client

# Запуск infrastructure
docker compose -f compose-dev.yml up -d

# Миграции и создание VIEW
uv run python manage.py migrate
uv run python manage.py create_sql_view
uv run python manage.py createsuperuser --email admin@admin.admin --username admin

# Запуск сервера и worker
uv run python manage.py runserver
uv run celery -A conf.celery_app worker -B -l info
```

### Production

```bash
docker compose up -d --build
```

---

## Особенности архитектуры

1. **Распределенная обработка:** Celery workers обрабатывают парсинг асинхронно, каждая квартира — отдельная task с retry (3 попытки).

2. **Headless-браузер:** Выделенный контейнер с Chromium (порт 9222), управляемый через Nodriver. Используется для TrendAgent, Наш.Дом.РФ и Продома.Дом.РФ.

3. **Капча:** Сервис `captcha-solver` (порт 4396) распознает капчу-слайдер для Наш.Дом.РФ.

4. **Авторизация TrendAgent:** Отдельный процесс `watch_trendagent_token_updates` постоянно мониторит сессию и обновляет auth_token в Valkey.

5. **Прокси:** Все внешние запросы проходят через прокси (настраивается через `PROXY_ADDRESS`).

6. **Маппинг источников:** Справочники `DEVELOPER_IDS` и `COMPLEX_IDS` связывают IDs между ERZ, TrendAgent, Наш.Дом.РФ и Продома.Дом.РФ.

7. **Мониторинг:** Sentry отслеживает ошибки. Логи отправляются в syslog. Watchtower автоматически обновляет контейнеры.

8. **SQL VIEW:** Представление `ДанныеПарсинга` объединяет все основные таблицы для удобных запросов.

9. **Обработка ошибок:** Каждый этап парсинга обёрнут в `try/except`. Если один источник упал (например, Наш.Дом.РФ недоступен), остальные продолжают работать. Ошибка логируется в `logger.exception` с полным traceback, а задача помечается как `SUCCESS`.
