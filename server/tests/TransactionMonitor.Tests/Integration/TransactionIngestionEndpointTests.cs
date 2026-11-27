using System.Net;
using System.Net.Http.Json;
using FluentAssertions;
using Microsoft.AspNetCore.Mvc.Testing;
using TransactionMonitor.Api.Models;
using Xunit;

namespace TransactionMonitor.Tests.Integration;

public class TransactionIngestionEndpointTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public TransactionIngestionEndpointTests(WebApplicationFactory<Program> factory)
    {
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task Posting_a_valid_transaction_returns_201_with_the_stored_record()
    {
        var request = new CreateTransactionRequest
        {
            Amount = 1500.50m,
            Currency = "usd", // lowercase on purpose: server should normalize to uppercase
            Status = TransactionStatus.Pending
        };

        var response = await _client.PostAsJsonAsync("/api/transactions", request);

        response.StatusCode.Should().Be(HttpStatusCode.Created);
        var body = await response.Content.ReadFromJsonAsync<Transaction>();
        body.Should().NotBeNull();
        body!.Currency.Should().Be("USD");
        body.TransactionId.Should().NotBe(Guid.Empty);
    }

    [Fact]
    public async Task Posting_an_invalid_transaction_returns_a_validation_problem()
    {
        var request = new CreateTransactionRequest
        {
            Amount = -10,
            Currency = "usd",
            Status = TransactionStatus.Pending
        };

        var response = await _client.PostAsJsonAsync("/api/transactions", request);

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task A_posted_transaction_can_be_retrieved_by_id()
    {
        var request = new CreateTransactionRequest
        {
            Amount = 75,
            Currency = "EUR",
            Status = TransactionStatus.Completed
        };

        var postResponse = await _client.PostAsJsonAsync("/api/transactions", request);
        var created = await postResponse.Content.ReadFromJsonAsync<Transaction>();

        var getResponse = await _client.GetAsync($"/api/transactions/{created!.TransactionId}");

        getResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        var fetched = await getResponse.Content.ReadFromJsonAsync<Transaction>();
        fetched!.TransactionId.Should().Be(created.TransactionId);
    }

    [Fact]
    public async Task Fetching_an_unknown_transaction_id_returns_404()
    {
        var response = await _client.GetAsync($"/api/transactions/{Guid.NewGuid()}");

        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }
}
