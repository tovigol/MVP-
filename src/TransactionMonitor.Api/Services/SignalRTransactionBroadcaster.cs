using Microsoft.AspNetCore.SignalR;
using TransactionMonitor.Api.Hubs;
using TransactionMonitor.Api.Models;

namespace TransactionMonitor.Api.Services;

public sealed class SignalRTransactionBroadcaster : ITransactionBroadcaster
{
    private readonly IHubContext<TransactionHub, ITransactionClient> _hubContext;

    public SignalRTransactionBroadcaster(IHubContext<TransactionHub, ITransactionClient> hubContext)
    {
        _hubContext = hubContext;
    }

    public Task BroadcastAsync(Transaction transaction, CancellationToken cancellationToken = default) =>
        _hubContext.Clients.All.TransactionReceived(transaction);
}
