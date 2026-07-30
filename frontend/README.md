# Wallet Lens frontend

React 18, TypeScript, and Vite interface for wallet import, feature exploration, clustering,
and live provider configuration.

## Commands

```sh
npm install
npm run dev
npm run build
npm test
npm run lint
```

Development requests under `/api` are proxied to `http://localhost:8000`. Set
`VITE_API_BASE_URL` to use another API origin.

The centralized API contract is in `src/api/client.ts` and `src/api/types.ts`.

The Settings page updates runtime job/provider limits and manages API keys. Secret values are
only sent when a key is added or rotated; list and mutation responses are treated as masked data.
