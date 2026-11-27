using TransactionMonitor.Api.Models;

namespace TransactionMonitor.Api.Services;

public interface ITransactionBroadcaster
{
    Task BroadcastAsync(Transaction transaction, CancellationToken cancellationToken = default);
}
