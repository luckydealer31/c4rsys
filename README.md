# c4rsys — приложение

Код системы c4rsys. Документы проекта лежат рядом, в `../docs`, и в этот репозиторий не входят.

Сейчас здесь каркас задачи T00: пустое приложение и проверка связи с базой данных. Бизнес-функций ещё нет.

| Часть | Технологии |
| --- | --- |
| backend | Python 3.13, Django 6.1, PostgreSQL 18 |
| frontend | React 19, TypeScript, Vite, react-i18next (en, ru, th) |
| Локальный запуск | Docker Compose |

## Запуск

Нужен Docker Desktop.

```sh
cp .env.example .env        # один раз; файл .env не попадает в Git
docker compose up --build
```

После запуска:

- http://localhost:5173 — интерфейс; страница показывает, доступны ли сервер и база.
- http://localhost:8000/api/health — проверка сервера и базы.

Одноразовые коды входа (появятся в T02) в разработке выводятся в журнал контейнера `backend`. Настоящая почта для разработки не нужна.

## Проверки

```sh
docker compose up -d db
./scripts/check.sh
```

## Фиксация версий

Каркас написан без доступа к реестрам пакетов, поэтому версии зависимостей заданы диапазонами. При первой успешной установке зафиксируйте точные версии и закоммитьте результат:

```sh
docker compose run --rm --no-deps -v "$PWD/frontend:/app" frontend npm install   # создаст frontend/package-lock.json
docker compose run --rm backend pip freeze > backend/requirements.lock
```

Если `npm install` сообщит о несовместимых версиях, поднимите или опустите диапазон в `package.json` до совместимого. Принятые решения (Django 6.1, PostgreSQL 18) при этом не меняются.
