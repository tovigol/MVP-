namespace TransactionMonitor.Api.Models;

/// <summary>
/// The canonical transaction record. Immutable by design: once stored and
/// broadcast, a transaction is never mutated in place — an update is a new
/// record with the same TransactionId, which keeps concurrent reads safe
/// without locking.
/// </summary>
public sealed record Transaction
{
    public required Guid TransactionId { get; init; }
    public required decimal Amount { get; init; }
    public required string Currency { get; init; }
    public required TransactionStatus Status { get; init; }
    public required DateTimeOffset Timestamp { get; init; }
}
