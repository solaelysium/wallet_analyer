# Wallet Analyzer Backend

Local FastAPI service for importing Ethereum wallets, collecting on-chain history,
calculating versioned features, and clustering wallets.

## Run

Use Python 3.11 or newer from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn backend.app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`, with OpenAPI documentation at
`/docs`. Idempotent schema initialization, SQLite WAL, foreign-key enforcement
inside each database, a busy timeout, and schema versioning are configured
automatically.

Feature snapshots intentionally retain versioned numeric values in JSON instead
of duplicating a changing feature vocabulary into nullable SQL columns. This
keeps old feature versions reproducible while list filtering, exports, and ML
feature selection remain dynamic.

Build the backend container with:

```powershell
docker build -t wallet-analyzer-backend backend
```

## Storage and settings

The service does not read runtime configuration from `.env`. It creates six
SQLite files under `data/`:

- `keys.sqlite3`: plaintext provider keys and runtime settings
- `wallets.sqlite3`: chains, imports, wallets, and collection jobs
- `events.sqlite3`: normal, internal, and token transfer events
- `tokens.sqlite3`: token metadata and prices
- `analytics.sqlite3`: feature snapshots and clustering results
- `logs.sqlite3`: application and job logs

Provider keys are masked in API responses. They can be created, updated,
enabled, disabled, and deleted through `/api/api-keys`. Runtime provider and job
settings are available through `/api/settings`. Provider key, rate, retry,
cooldown, concurrency, and timeout changes apply without a restart.

At first startup, an existing `data/wallet_analyzer.sqlite3` is imported without
modifying the source database. IDs are preserved. Legacy encrypted keys and
provider keys in a root `.env` are read only by this one-time migration and
stored in `keys.sqlite3`; later startups do not read `.env`.

## Tests

```powershell
pytest backend\tests
```
