# Frontend

React-интерфейс Wallet Analyzer (в UI — **Wallet Lens**). Vite + TypeScript.

Страницы: **Кошельки**, **Признаки**, **Журнал**, **Настройки**.  
Кластеризация есть в коде (`src/features/clustering`), но из меню скрыта — раздел в доработке.

## Структура

| Путь | Назначение |
| --- | --- |
| `src/features/wallets` | импорт пакетов, джобы, сводка, логи |
| `src/features/features` | таблица признаков, фильтры, экспорт |
| `src/features/logs` | журнал событий |
| `src/features/settings` | ключи Etherscan / Infura / CoinGecko |
| `src/features/clustering` | кластеризация (не в навигации) |
| `src/api` | клиент FastAPI (`client.ts`, `types.ts`) |
| `src/components` | оболочка, модалки, карточки джобов |

Запросы идут на `/api`. В dev Vite проксирует на `http://localhost:8000`. Другой origin — `VITE_API_BASE_URL`. В Docker nginx проксирует `/api/` и `/health` на `backend:8000`.

Секреты ключей уходят на сервер только при создании или ротации; в списке значения маскируются.

## Запуск

```bash
cd frontend
npm install
npm run dev
```

Dev-сервер: [http://localhost:5173](http://localhost:5173). Backend должен быть на `:8000`.

```bash
npm run build
```

Docker-образ: Nginx раздаёт `dist` на `:80`.

## Тесты

```bash
npm test
npm run lint
```

Watch: `npm test:watch`.
