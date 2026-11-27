using System.Collections.Concurrent;
using TransactionMonitor.Api.Models;

namespace TransactionMonitor.Api.Storage;

/// <summary>
/// In-memory store for the single pod it runs in. Thread safety comes from
/// <see cref="ConcurrentDictionary{TKey,TValue}"/> rather than explicit locks:
/// - Add/upsert is a single atomic dictionary operation, so two concurrent
///   writers for different (or even the same) TransactionId never corrupt state.
/// - Reads (<see cref="GetLatest"/>) iterate a lock-free snapshot of
///   .Values, so a writer racing a reader can't produce a torn read.
///
/// This intentionally does NOT solve cross-pod consistency — that's the job
/// of the SignalR Redis backplane on the broadcast side (see README / ADR).
/// Each pod's store holds only the transactions ingested through *that* pod,
/// which is sufficient for the live-broadcast use case this MVP targets.
/// </summary>
public sealed class InMemoryTransactionStore : ITransactionStore
{
    private readonly ConcurrentDictionary<Guid, Transaction> _transactions = new();

    public Transaction Add(Transaction transaction)
    {
        _transactions[transaction.TransactionId] = transaction;
        return transaction;
    }

    public Transaction? GetById(Guid id) =>
        _transactions.TryGetValue(id, out var transaction) ? transaction : null;

    public IReadOnlyList<Transaction> GetLatest(int count)
    {
        if (count <= 0) return Array.Empty<Transaction>();

        return _transactions.Values
            .OrderByDescending(t => t.Timestamp)
            .Take(count)
            .ToList();
    }

    public int Count => _transactions.Count;
}
