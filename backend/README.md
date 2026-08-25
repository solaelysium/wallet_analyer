# Backend

FastAPI-сервис сбора Ethereum-данных, расчёта признаков и кластеризации.

Точка входа: `app.main:app`. В Docker слушает `:8000`. Документация: `/docs`.

## Модули

| Файл | Роль |
| --- | --- |
| `app/api.py` | HTTP API |
| `app/jobs.py` | пакетный сбор и фаза признаков |
| `app/analytics.py` | признаки `wallet_features.v4` |
| `app/providers.py` | Etherscan, Infura, CoinGecko |
| `app/key_pool.py` | ротация ключей, rate limit, `last_used_at` |
| `app/imports.py` | предпросмотр и подтверждение импорта |
| `app/ml.py` | PCA/UMAP + KMeans/HDBSCAN |
| `app/database.py` | SQLite hub + `ATTACH` |

## Данные

Каталог `data/` (в Docker: `/app/data`). Runtime-настройки **не** читаются из `.env`.

| Файл | Содержимое |
| --- | --- |
| `keys.sqlite3` | API-ключи и runtime-настройки |
| `wallets.sqlite3` | сети, импорты, кошельки, джобы |
| `events.sqlite3` | normal / internal / ERC-20 |
| `tokens.sqlite3` | токены и цены |
| `analytics.sqlite3` | снимки признаков, кластеры |
| `logs.sqlite3` | журнал |

Ключи в API маскируются. CRUD: `/api/api-keys`. Лимиты и воркеры: `/api/settings` — без перезапуска.

Лимит сбора: **25 000** событий на кошелёк. Пагинация Etherscan обходит окно `page × offset ≤ 10 000` слайдом `startblock`.

Пайплайн джоба: `collection` → `features`. Сверх лимита — `skipped`.

Существующий `data/wallet_analyzer.sqlite3` импортируется один раз и не меняется.

## API

- `GET /health`
- Импорт: `/api/imports/preview`, `/api/imports/confirm`
- Джобы: `/api/jobs`, stop / resume / retry / recalculate
- Признаки: `/api/features`, `/api/features/export`
- Кластеры: `/api/clusters` (API есть, UI пока скрыт)
- Ключи и настройки: `/api/api-keys`, `/api/settings`
- Журнал: `/api/logs`

## Запуск

Из корня репозитория:

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt
.venv/Scripts/python -m uvicorn backend.app.main:app --reload
```

API: [http://127.0.0.1:8000](http://127.0.0.1:8000).

В контейнере рабочая директория `/app`, модуль `app.main:app`.

## Тесты

```bash
.venv/Scripts/python -m pytest backend/tests
```
