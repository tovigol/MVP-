using TransactionMonitor.Api.Models;

namespace TransactionMonitor.Api.Storage;

public interface ITransactionStore
{
    /// <summary>Inserts or replaces a transaction by TransactionId (idempotent upsert).</summary>
    Transaction Add(Transaction transaction);

    Transaction? GetById(Guid id);

    /// <summary>Most recent transactions first, capped at <paramref name="count"/>.</summary>
    IReadOnlyList<Transaction> GetLatest(int count);

    int Count { get; }
}
