# Wallet Analyzer

Локальное веб-приложение для сбора транзакций Ethereum-кошельков и расчёта поведенческих признаков.

Интерфейс: **Кошельки → Признаки → Журнал → Настройки**. Кластеризация пока в процессе доработки и скрыта из UI.

## Возможности

- Импорт адресов из CSV, XLSX, TXT и ручного ввода с предпросмотром, валидацией и дедупликацией.
- Сбор данных через Etherscan, Infura и CoinGecko: обычные/внутренние транзакции и ERC-20 переводы.
- Двухфазный пайплайн: сначала коллекция, затем расчёт признаков (`wallet_features.v4`).
- Пропуск кошельков с более чем 25 000 событий.
- Таблица признаков: фильтры, сортировка, закреплённый столбец адреса, экспорт CSV/XLSX.
- Кластеризация (в процессе): PCA или UMAP, затем HDBSCAN или KMeans; пока недоступна в интерфейсе.
- Ключи провайдеров, лимиты и воркеры настраиваются в UI, без `.env`.

## Форматы входа

| Источник | Формат |
| --- | --- |
| CSV / XLSX | только столбцы `index` и `wallet_address` |
| TXT / ручной ввод | один Ethereum-адрес на непустую строку |

Все источники сливаются в один предпросмотр. Некорректные строки, дубликаты и уже проверенные адреса видны до запуска пакета.

## Стек

- Backend: FastAPI, SQLAlchemy, SQLite (несколько файлов через `ATTACH`)
- Frontend: React, Vite, TanStack Table / Query
- ML: scikit-learn, UMAP, HDBSCAN

## Запуск

```bash
docker compose up --build
```

- UI: [http://localhost:8080](http://localhost:8080)
- API health: [http://localhost:8000/health](http://localhost:8000/health)

После старта добавьте ключи Etherscan, Infura и CoinGecko на странице **Настройки**.

Данные лежат в `data/`:

| Файл | Содержимое |
| --- | --- |
| `keys.sqlite3` | API-ключи и runtime-настройки |
| `wallets.sqlite3` | кошельки и пакеты импорта |
| `events.sqlite3` | транзакции и переводы |
| `tokens.sqlite3` | токены и цены |
| `analytics.sqlite3` | признаки и кластеры |
| `logs.sqlite3` | журнал |

Существующий `data/wallet_analyzer.sqlite3` импортируется один раз и остаётся как бэкап.

## Разработка

Backend:

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt
.venv/Scripts/python -m uvicorn backend.app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Vite: [http://localhost:5173](http://localhost:5173).

Подробнее: [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md).

## Тесты

```bash
.venv/Scripts/python -m pytest backend/tests

cd frontend
npm test
npm run build
```
