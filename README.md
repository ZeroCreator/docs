## 🚀 Быстрый старт

### 1. Клонирование репозитория

```bash
git clone https://github.com/zerocreator/docs-hub.git
cd docs-hub
```

### 2. Настройка виртуального окружения
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Установка зависимостей
```bash
pip install --upgrade pip
pip install mkdocs mkdocs-material
```

### 4. Запуск локального сервера
```bash
mkdocs serve
```

Сайт будет доступен по адресу: http://127.0.0.1:8000


💡 Если порт 8000 занят, используйте другой порт:

```bash
mkdocs serve --dev-addr=127.0.0.1:8001
```

### 5. Сборка статического сайта
```bash
mkdocs build
```

Собранные файлы появятся в папке site/
