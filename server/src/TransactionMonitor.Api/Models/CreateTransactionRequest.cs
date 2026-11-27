namespace TransactionMonitor.Api.Models;

/// <summary>
/// What the ingestion API accepts. Kept separate from the domain
/// <see cref="Transaction"/> so client-supplied fields are optional/lenient
/// (e.g. the caller may omit TransactionId or Timestamp and let the server
/// assign them) while the stored/broadcast model stays fully populated.
/// </summary>
public sealed class CreateTransactionRequest
{
    public Guid? TransactionId { get; set; }
    public decimal Amount { get; set; }
    public string Currency { get; set; } = string.Empty;
    public TransactionStatus Status { get; set; }
    public DateTimeOffset? Timestamp { get; set; }
}
