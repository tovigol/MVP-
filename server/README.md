# Transaction Monitor — Server

.NET 8 backend for the Real-Time Financial Monitor assessment.

## Stack

| Concern | Choice |
|---|---|
| API style | Minimal APIs (`Program.cs`) — small surface area, easy to unit/integration test |
| Real-time | SignalR (`TransactionHub`), strongly-typed via `Hub<ITransactionClient>` |
| Validation | FluentValidation |
| Storage | In-memory, `ConcurrentDictionary`-backed (see ADR below for why not SQLite by default) |
| Distributed fan-out | SignalR's Redis backplane (`Microsoft.AspNetCore.SignalR.StackExchangeRedis`), opt-in via config |
| Tests | xUnit, FluentAssertions, NSubstitute, `WebApplicationFactory` for integration tests |

## Project layout

```
src/TransactionMonitor.Api/
  Program.cs                          composition root + all endpoints
  Models/                             Transaction, TransactionStatus, CreateTransactionRequest
  Validation/TransactionValidator.cs  FluentValidation rules
  Storage/                            ITransactionStore + InMemoryTransactionStore
  Hubs/                               TransactionHub, ITransactionClient
  Services/                           ITransactionBroadcaster + SignalR implementation
  Dockerfile
tests/TransactionMonitor.Tests/
  Validation/      rule-level unit tests
  Storage/         store behavior (upsert, ordering, count)
  Concurrency/      many-writer / interleaved-read stress tests
  Services/        broadcaster unit test (mocked IHubContext)
  Integration/     end-to-end HTTP tests via WebApplicationFactory
k8s/
  deployment.yaml  5 replicas, readiness/liveness probes, Redis via Secret
  service.yaml     ClusterIP + session affinity note
  redis-deployment.yaml   demo-only Redis for local clusters
```

## Running locally

```bash
cd src/TransactionMonitor.Api
dotnet run
```

Swagger UI is available at `/swagger` in Development. The React client (see
the companion `client.zip`) expects `POST /api/transactions` and the hub at
`/hubs/transactions`, proxied from `http://localhost:5173`.

## Running tests

```bash
cd tests/TransactionMonitor.Tests
dotnet test
```

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/transactions` | Ingests a transaction, validates it, stores it, broadcasts it |
| `GET` | `/api/transactions?take=100` | Latest transactions on *this* pod (see ADR) |
| `GET` | `/api/transactions/{id}` | Single transaction lookup |
| `GET` | `/healthz` | Liveness/readiness probe target |
| WS | `/hubs/transactions` | SignalR hub; clients receive `TransactionReceived(transaction)` |

---

## ADR: synchronizing state across replicas

**Context.** The assessment's stated scenario: deploy 5 pods behind a
Service. A client connected to Pod A's WebSocket won't see a transaction
POSTed to Pod B, because each pod's `InMemoryTransactionStore` and SignalR
connection table are pod-local by default.

**Decision.** Split the problem into two, because they have different
answers:

1. **Real-time fan-out (solved, implemented):** SignalR's Redis backplane.
   Every pod subscribes to the same Redis pub/sub channel. When Pod B's
   ingestion endpoint calls `Clients.All.TransactionReceived(...)`, that
   call is published to Redis; every pod (including Pod A) receives it and
   pushes it down to its own locally-connected clients. This is a few lines
   of configuration (`AddStackExchangeRedis(...)` in `Program.cs`, gated on
   whether `Redis:ConnectionString` is set) and needs no application-level
   pub/sub code — it's the standard, supported way to run SignalR behind
   multiple replicas.

2. **Historical/REST reads (`GET /api/transactions`) — not fully solved by
   the backplane:** the Redis backplane only carries the live broadcast
   message; it doesn't give every pod a shared view of *stored* history.
   Right now, `GET /api/transactions` on Pod A only returns what Pod A has
   personally ingested since it started. For this MVP that's an accepted
   trade-off — the dashboard's primary experience is the live stream, which
   *is* consistent across pods. If the REST history endpoint needs to be
   consistent too, the fix is to swap `InMemoryTransactionStore` for a
   shared backing store (Redis itself as a sorted set / list, or
   Postgres/SQLite-on-shared-volume) behind the same `ITransactionStore`
   interface — the interface was deliberately kept storage-agnostic so this
   is a single-class swap, not a rewrite.

**Why Redis over Kafka/RabbitMQ for the fan-out:** the requirement is
"broadcast the latest event to whoever's currently connected," not
"guarantee every event is durably delivered and replayable." Pub/sub is the
right shape for that; a message broker would add durability and replay
semantics this use case doesn't ask for. If a future requirement is "a
client that reconnects after 10 minutes should catch up on everything it
missed," that's the point to introduce a broker or a persisted event log —
not the broadcast layer.

**Session affinity:** an established WebSocket connection is already pinned
to one pod (it's a single TCP connection), so it needs no extra stickiness
once open. SignalR's `/negotiate` handshake — and any fallback transport
like long polling — does issue separate HTTP requests that must land on the
same pod, so `service.yaml` sets `sessionAffinity: ClientIP` as a safety
net for that handshake and for environments where WebSocket upgrades get
blocked by an intermediary.

**Why in-memory storage instead of SQLite for the MVP:** the spec allows
either. In-memory keeps the concurrency story simple and testable
(`ConcurrentDictionary` gives atomic upserts and snapshot reads with zero
lock code), and since each pod's store is pod-local either way (per the
trade-off above), SQLite-on-local-disk wouldn't actually solve cross-pod
consistency either — it would just add I/O and a migration story for a
problem it doesn't fix. `ITransactionStore` is the seam where a real
implementation (shared SQL store, or SQLite for durability across restarts
of a *single* pod) would plug in without touching the endpoints.

## What's verified vs. not

This project was written and reviewed carefully, but the sandbox used to
build it has no .NET SDK and no access to NuGet — so unlike the React
client (which was `npm install`'d, type-checked, and built end-to-end),
this backend has **not** been compiled or test-run in this environment.
Before relying on it: run `dotnet restore && dotnet build` and
`dotnet test` locally, and skim `Program.cs` for the Redis/CORS wiring
against your actual local ports.
