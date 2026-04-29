## Локальное тестирование

Для локального тестирования парсинга нужны локальные сервисы. 
По коду и конфигу проекта минимальный набор такой:
- MariaDB/MySQL — основная БД Django
- RabbitMQ — брокер Celery
- Valkey — хранение auth_token TrendAgent
- Прокси — практически обязателен для внешних парсеров, особенно TrendAgent
- Watcher токена TrendAgent — нужен, если хотите гонять именно TrendAgent API через основной парсер

**Что поднимать обязательно**

Из compose-dev.yml:

services:
  mariadb
  rabbitmq
  valkey

То есть локально вам надо поднять:

```bash
docker compose -f compose-dev.yml up -d
```

Что прописать в .env

```dotenv
SECRET_KEY=dev-secret
MYSQL_NAME=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root
RABBITMQ_HOST=127.0.0.1
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest
VALKEY_HOST=127.0.0.1
VALKEY_PORT=6379
TRENDAGENT_PHONE=...
TRENDAGENT_PASSWORD=...
TRENDAGENT_CITY=MSK
PRODOMA_DOM_RF_USERNAME=dummy
PRODOMA_DOM_RF_PASSWORD=dummy
PROXY_ADDRESS=http://127.0.0.1:3128
```

**Важно:**

TRENDAGENT_*, PRODOMA_*, SECRET_KEY в settings.py читаются через os.environ[...], то есть без них Django вообще не стартует.

**Прокси**

Для локальной работы README ожидает SSH-туннель:

```bash
ssh -N -L 3128:localhost:3128 [you_name]:youparse@team-73.ru -p 2222
```

Если прокси не поднят, TrendAgent и другие внешние парсеры, скорее всего, будут падать на запросах.
---

Минимальный сценарий для локального теста

1. Поднять инфраструктуру
docker compose -f compose-dev.yml up -d
2. Подготовить БД
uv run python manage.py migrate
uv run python manage.py create_sql_view
3. Запустить watcher токена TrendAgent
В отдельном терминале:
uv run python manage.py watch_trendagent_token_updates
Он должен положить auth_token в Valkey.
4. Запустить Celery worker
Ещё в отдельном терминале:
uv run celery -A conf.celery_app worker -l info -Q flat-parser,flat-parser-trendagent
Это важно: TrendAgent-задачи кладутся в очередь flat-parser-trendagent.
5. Запустить сам парсинг TrendAgent
Ещё в одном терминале:
uv run celery -A conf.celery_app call parser.parse-trendagent --queue flat-parser-trendagent
---
Если хотите проверить только Django/БД без Celery
Можно просто убедиться, что приложение вообще стартует:
uv run python manage.py runserver