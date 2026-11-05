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

Сквозные проверки в браузере (`e2e/tests`, Playwright) на отдельном стенде со своей базой:

```sh
./scripts/e2e.sh        # dev-сборка
./scripts/e2e.sh prod   # сборка тестового сервера (Caddy, gunicorn, DEBUG выключен)
```

Сборка тестового сервера проверяется по HTTPS (`https://localhost`, сертификат локального центра Caddy), как на сервере: Safari не хранит cookie с флагом `Secure`, если сайт открыт по HTTP.

Каждый сценарий проходит в Chromium на компьютере (`desktop`). Сценарии для телефона (`*.mobile.spec.ts`) дополнительно проходят на Android (`android`, Pixel 7, Chromium) и на iPhone (`iphone`, iPhone 13, WebKit — движок всех браузеров на iOS).

- `base-scenario` — базовый пример от регистрации до закрытия заказа, с перезапуском базы и сервера;
- `booking` — бронь на экране и выдача по этой брони (§ 14.1, § 14.4.1);
- `cabinet` — клиент на домене кабинета (`cabinet.localhost`) принимает привязку и видит аренду;
- `permissions` — сотруднику без права выдачи её не предлагают, а сервер отказывает (403);
- `inspection.mobile` — осмотр при выдаче на телефоне: показания, фото позиции, сохранение;
- `offline.mobile` — связь пропала посреди осмотра: изменение остаётся на устройстве и уходит само, когда связь вернулась (§ 10.4.2). На сборке тестового сервера есть service worker, поэтому страница дополнительно перезагружается без связи и открывает локальные черновики (§ 10.4.3). Этот шаг проверяется только в Chromium: режим без сети Playwright в WebKit обрывает переход раньше service worker;
- `touch.mobile` — главные экраны на телефоне: страница не прокручивается вбок, все кнопки и поля не ниже 44 px.

Данные, которые проверка не проверяет, создаются через API из самой страницы (`e2e/lib/api.ts`).

## Фиксация версий

В `requirements.txt` версии заданы диапазонами. Все сборки (разработка, CI, сервер) ставят точные версии из `backend/requirements.lock` (`pip install -c requirements.lock`). CI проверяет их на известные уязвимости (`pip-audit`, пункт 9 аудита). Чтобы обновить версии, выполните команды и закоммитьте результат:

```sh
docker compose run --rm --no-deps -v "$PWD/frontend:/app" frontend npm install   # создаст frontend/package-lock.json
docker compose run --rm --no-deps backend sh -c "pip install -q -U -r requirements-dev.txt && pip freeze --exclude pip" > backend/requirements.lock
```

Если `npm install` сообщит о несовместимых версиях, поднимите или опустите диапазон в `package.json` до совместимого. Принятые решения (Django 6.1, PostgreSQL 18) при этом не меняются.
