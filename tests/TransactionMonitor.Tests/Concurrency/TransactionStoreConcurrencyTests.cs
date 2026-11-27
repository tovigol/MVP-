using FluentAssertions;
using TransactionMonitor.Api.Models;
using TransactionMonitor.Api.Storage;
using Xunit;

namespace TransactionMonitor.Tests.Concurrency;

public class TransactionStoreConcurrencyTests
{
    [Fact]
    public async Task Many_concurrent_writers_with_distinct_ids_all_land_without_loss()
    {
        var store = new InMemoryTransactionStore();
        const int writerCount = 2000;

        var writers = Enumerable.Range(0, writerCount).Select(i => Task.Run(() =>
        {
            store.Add(new Transaction
            {
                TransactionId = Guid.NewGuid(),
                Amount = i + 1,
                Currency = "USD",
                Status = TransactionStatus.Pending,
                Timestamp = DateTimeOffset.UtcNow
            });
        }));

        await Task.WhenAll(writers);

        // Every write should be visible and none should have clobbered another,
        // since each uses a distinct TransactionId.
        store.Count.Should().Be(writerCount);
    }

    [Fact]
    public async Task Concurrent_writers_to_the_same_id_never_throw_and_leave_exactly_one_record()
    {
        var store = new InMemoryTransactionStore();
        var sharedId = Guid.NewGuid();
        const int writerCount = 500;

        var writers = Enumerable.Range(0, writerCount).Select(i => Task.Run(() =>
        {
            store.Add(new Transaction
            {
                TransactionId = sharedId,
                Amount = i,
                Currency = "USD",
                Status = TransactionStatus.Pending,
                Timestamp = DateTimeOffset.UtcNow
            });
        }));

        var act = async () => await Task.WhenAll(writers);

        await act.Should().NotThrowAsync();
        store.Count.Should().Be(1);
        store.GetById(sharedId).Should().NotBeNull();
    }

    [Fact]
    public async Task Reads_interleaved_with_writes_never_see_a_torn_or_corrupted_snapshot()
    {
        var store = new InMemoryTransactionStore();
        using var cts = new CancellationTokenSource();

        var writer = Task.Run(async () =>
        {
            for (var i = 0; i < 5000 && !cts.IsCancellationRequested; i++)
            {
                store.Add(new Transaction
                {
                    TransactionId = Guid.NewGuid(),
                    Amount = 1,
                    Currency = "USD",
                    Status = TransactionStatus.Pending,
                    Timestamp = DateTimeOffset.UtcNow
                });
            }
        });

        var readerErrors = new List<Exception>();
        var reader = Task.Run(() =>
        {
            try
            {
                for (var i = 0; i < 200; i++)
                {
                    // Every returned item must be a fully-formed Transaction —
                    // GetLatest should never expose a half-written entry.
                    var snapshot = store.GetLatest(50);
                    foreach (var tx in snapshot)
                    {
                        tx.TransactionId.Should().NotBe(Guid.Empty);
                        tx.Currency.Should().NotBeNullOrEmpty();
                    }
                }
            }
            catch (Exception ex)
            {
                readerErrors.Add(ex);
            }
        });

        await writer;
        cts.Cancel();
        await reader;

        readerErrors.Should().BeEmpty();
    }
}
