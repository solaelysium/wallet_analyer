# Wallet Analyzer

Local web application for collecting Ethereum wallet features and exploring
behavioral clusters.

## Capabilities

- Aggregate wallet addresses from CSV, XLSX, TXT, and manual input.
- Preview, validate, and deduplicate addresses before creating an analysis job.
- Collect resumable wallet features with live progress and cancellation.
- Browse, filter, copy, and export feature data.
- Run PCA or UMAP with HDBSCAN or KMeans.
- Export feature tables, clustering assignments, and cluster plots.

## Input formats

- CSV/XLSX: an index column followed by `wallet_address`.
- TXT: one Ethereum address per non-empty line.
- Manual input: one Ethereum address per line.

All selected files and manual text are merged into one preview. Invalid and
duplicate values are shown before confirmation.

## Docker start

Start the application without an environment file:

```bash
docker compose up --build
```

Open `http://localhost:8080`. Backend health is available at
`http://localhost:8000/health`. Add provider keys and change runtime settings
from the Settings page.

Application data is split across `keys.sqlite3`, `wallets.sqlite3`,
`events.sqlite3`, `tokens.sqlite3`, `analytics.sqlite3`, and `logs.sqlite3`
under `data/`. An existing `data/wallet_analyzer.sqlite3` is imported once and
left unchanged as a backup.

## Development

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

The Vite development server is available at `http://localhost:5173`.

## Tests

```bash
.venv/Scripts/python -m pytest backend/tests

cd frontend
npm test
npm run build
```
