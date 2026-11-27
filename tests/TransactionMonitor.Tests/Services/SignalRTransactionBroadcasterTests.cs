using FluentAssertions;
using Microsoft.AspNetCore.SignalR;
using NSubstitute;
using TransactionMonitor.Api.Hubs;
using TransactionMonitor.Api.Models;
using TransactionMonitor.Api.Services;
using Xunit;

namespace TransactionMonitor.Tests.Services;

public class SignalRTransactionBroadcasterTests
{
    [Fact]
    public async Task BroadcastAsync_sends_TransactionReceived_to_all_clients()
    {
        var clientProxy = Substitute.For<ITransactionClient>();
        var clients = Substitute.For<IHubClients<ITransactionClient>>();
        clients.All.Returns(clientProxy);

        var hubContext = Substitute.For<IHubContext<TransactionHub, ITransactionClient>>();
        hubContext.Clients.Returns(clients);

        var broadcaster = new SignalRTransactionBroadcaster(hubContext);
        var transaction = new Transaction
        {
            TransactionId = Guid.NewGuid(),
            Amount = 42.5m,
            Currency = "USD",
            Status = TransactionStatus.Completed,
            Timestamp = DateTimeOffset.UtcNow
        };

        await broadcaster.BroadcastAsync(transaction);

        await clientProxy.Received(1).TransactionReceived(transaction);
    }
}
