using FluentAssertions;
using TransactionMonitor.Api.Models;
using TransactionMonitor.Api.Storage;
using Xunit;

namespace TransactionMonitor.Tests.Storage;

public class InMemoryTransactionStoreTests
{
    private static Transaction MakeTransaction(Guid? id = null, DateTimeOffset? timestamp = null, decimal amount = 100m) => new()
    {
        TransactionId = id ?? Guid.NewGuid(),
        Amount = amount,
        Currency = "USD",
        Status = TransactionStatus.Pending,
        Timestamp = timestamp ?? DateTimeOffset.UtcNow
    };

    [Fact]
    public void Add_then_GetById_returns_the_same_transaction()
    {
        var store = new InMemoryTransactionStore();
        var transaction = MakeTransaction();

        store.Add(transaction);

        store.GetById(transaction.TransactionId).Should().Be(transaction);
    }

    [Fact]
    public void GetById_for_unknown_id_returns_null()
    {
        var store = new InMemoryTransactionStore();

        store.GetById(Guid.NewGuid()).Should().BeNull();
    }

    [Fact]
    public void Add_with_an_existing_TransactionId_upserts_rather_than_duplicating()
    {
        var store = new InMemoryTransactionStore();
        var id = Guid.NewGuid();
        var original = MakeTransaction(id, amount: 100m);
        var updated = MakeTransaction(id, amount: 250m);

        store.Add(original);
        store.Add(updated);

        store.Count.Should().Be(1);
        store.GetById(id)!.Amount.Should().Be(250m);
    }

    [Fact]
    public void GetLatest_returns_transactions_ordered_newest_first()
    {
        var store = new InMemoryTransactionStore();
        var now = DateTimeOffset.UtcNow;
        var oldest = MakeTransaction(timestamp: now.AddMinutes(-10));
        var middle = MakeTransaction(timestamp: now.AddMinutes(-5));
        var newest = MakeTransaction(timestamp: now);

        // Insert out of order to prove ordering comes from the query, not insertion order.
        store.Add(middle);
        store.Add(oldest);
        store.Add(newest);

        var result = store.GetLatest(10);

        result.Select(t => t.TransactionId)
            .Should().ContainInOrder(newest.TransactionId, middle.TransactionId, oldest.TransactionId);
    }

    [Fact]
    public void GetLatest_respects_the_requested_count()
    {
        var store = new InMemoryTransactionStore();
        for (var i = 0; i < 20; i++)
        {
            store.Add(MakeTransaction(timestamp: DateTimeOffset.UtcNow.AddSeconds(i)));
        }

        store.GetLatest(5).Should().HaveCount(5);
    }

    [Fact]
    public void GetLatest_with_zero_or_negative_count_returns_empty()
    {
        var store = new InMemoryTransactionStore();
        store.Add(MakeTransaction());

        store.GetLatest(0).Should().BeEmpty();
        store.GetLatest(-5).Should().BeEmpty();
    }
}
