# Pulse — Transaction Monitor Client

React + TypeScript client for the Real-Time Financial Monitor assessment.

## Stack and why

| Concern | Choice | Reason |
|---|---|---|
| Real-time transport | `@microsoft/signalr` | Matches the backend's SignalR hub; auto-reconnect built in; pairs with a Redis backplane on the server so it's pod-agnostic behind a load balancer. |
| List virtualization | `react-window` | Directly satisfies the "100 transactions shouldn't freeze the browser" requirement — only visible rows are mounted. |
| State | `zustand` | Minimal boilerplate for a fast-moving feed (append-only stream + filter), avoids prop-drilling between the socket hook and both routes. |
| Motion | `framer-motion` | Row entry flash + connection pulse (bonus: animation on arrival / status transitions), respects `prefers-reduced-motion`. |
| Routing | `react-router-dom` | Two required routes: `/add` and `/monitor`. |

## Structure

```
src/
  api/client.ts              POST to the ingestion endpoint
  hooks/useTransactionSocket.ts   SignalR connection lifecycle -> store
  store/transactionStore.ts  live feed, connection state, filter
  types/transaction.ts       shared Transaction model + mock generator
  components/                StatusBadge, ConnectionPulse, FilterBar, TransactionRow
  pages/Monitor.tsx          /monitor — virtualized live grid
  pages/AddTransaction.tsx   /add — manual form + random/burst generator
```

## Design

Dark trading-terminal aesthetic: near-black panel background, monospace
(IBM Plex Mono) for IDs/amounts/timestamps, Inter for UI chrome. Status
color coding — amber (Pending), green (Completed), red (Failed) — carries
through the badge, the row's left border, and the filter counts. The
connection pulse (a beating dot) is the signature element: it visually
mirrors what "live" actually means here — a persisted socket, potentially
reconnecting across backend pods.

## Running

```bash
npm install
npm run dev
```

The dev server proxies `/api` and `/hubs` to `http://localhost:5000` — point
`vite.config.ts` at your backend's actual port, or set `VITE_API_BASE_URL`
/ `VITE_HUB_URL` env vars to override.

## Performance notes

- `react-window`'s `FixedSizeList` mounts only visible rows, so a burst of
  100+ transactions (see the "Fire burst of 100" button on `/add`) doesn't
  create 100 DOM nodes at once.
- The store caps the in-memory buffer at 2000 transactions to keep a
  long-running session bounded.
- Row entry animation only runs for the single most-recently-arrived
  transaction (`isNew` flag), not the whole list, to avoid re-triggering
  motion on every render.
