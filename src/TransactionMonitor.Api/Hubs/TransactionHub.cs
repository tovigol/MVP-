using Microsoft.AspNetCore.SignalR;

namespace TransactionMonitor.Api.Hubs;

/// <summary>
/// Clients (the /monitor dashboard) connect here purely to receive
/// TransactionReceived pushes; there's nothing for them to call. SignalR's
/// connection manager already handles concurrent connections thread-safely,
/// including under the Redis backplane where a single logical "Clients.All"
/// broadcast fans out across every pod's local connections.
/// </summary>
public sealed class TransactionHub : Hub<ITransactionClient>
{
}
