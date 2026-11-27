using FluentValidation;
using Microsoft.AspNetCore.SignalR;
using TransactionMonitor.Api.Hubs;
using TransactionMonitor.Api.Models;
using TransactionMonitor.Api.Services;
using TransactionMonitor.Api.Storage;
using TransactionMonitor.Api.Validation;

var builder = WebApplication.CreateBuilder(args);

// ---- Services -------------------------------------------------------------

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

builder.Services.AddSingleton<ITransactionStore, InMemoryTransactionStore>();
builder.Services.AddSingleton<ITransactionBroadcaster, SignalRTransactionBroadcaster>();
builder.Services.AddSingleton<IValidator<CreateTransactionRequest>, TransactionValidator>();

var signalRBuilder = builder.Services.AddSignalR();

// Cloud-ready real-time layer: if a Redis connection string is configured,
// wire SignalR's backplane so "Clients.All.TransactionReceived(...)" fans
// out across every pod, not just the one that handled the ingestion request.
// See README.md / ADR for the full distributed-architecture rationale.
var redisConnectionString = builder.Configuration["Redis:ConnectionString"];
if (!string.IsNullOrWhiteSpace(redisConnectionString))
{
    signalRBuilder.AddStackExchangeRedis(redisConnectionString, options =>
    {
        options.Configuration.ChannelPrefix = StackExchange.Redis.RedisChannel.Literal("transaction-monitor");
    });
}

var allowedOrigins = builder.Configuration
    .GetSection("Cors:AllowedOrigins")
    .Get<string[]>() ?? new[] { "http://localhost:5173" };

builder.Services.AddCors(options =>
{
    options.AddPolicy("Frontend", policy =>
    {
        policy.WithOrigins(allowedOrigins)
              .AllowAnyHeader()
              .AllowAnyMethod()
              .AllowCredentials(); // required for SignalR's websocket negotiate
    });
});

var app = builder.Build();

// ---- Middleware -------------------------------------------------------------

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseCors("Frontend");

// ---- Endpoints -------------------------------------------------------------

app.MapGet("/healthz", () => Results.Ok(new { status = "healthy" }))
   .WithName("HealthCheck");

app.MapPost("/api/transactions", async (
        CreateTransactionRequest request,
        IValidator<CreateTransactionRequest> validator,
        ITransactionStore store,
        ITransactionBroadcaster broadcaster,
        CancellationToken cancellationToken) =>
    {
        var validationResult = await validator.ValidateAsync(request, cancellationToken);
        if (!validationResult.IsValid)
        {
            return Results.ValidationProblem(validationResult.ToDictionary());
        }

        var transaction = new Transaction
        {
            TransactionId = request.TransactionId ?? Guid.NewGuid(),
            Amount = request.Amount,
            Currency = request.Currency.ToUpperInvariant(),
            Status = request.Status,
            Timestamp = request.Timestamp ?? DateTimeOffset.UtcNow
        };

        store.Add(transaction);
        await broadcaster.BroadcastAsync(transaction, cancellationToken);

        return Results.Created($"/api/transactions/{transaction.TransactionId}", transaction);
    })
    .WithName("IngestTransaction")
    .Produces<Transaction>(StatusCodes.Status201Created)
    .ProducesValidationProblem();

app.MapGet("/api/transactions", (ITransactionStore store, int? take) =>
        Results.Ok(store.GetLatest(take ?? 100)))
    .WithName("GetLatestTransactions")
    .Produces<IReadOnlyList<Transaction>>();

app.MapGet("/api/transactions/{id:guid}", (Guid id, ITransactionStore store) =>
        store.GetById(id) is { } transaction ? Results.Ok(transaction) : Results.NotFound())
    .WithName("GetTransactionById")
    .Produces<Transaction>()
    .Produces(StatusCodes.Status404NotFound);

app.MapHub<TransactionHub>("/hubs/transactions");

app.Run();

// Exposed for WebApplicationFactory-based integration tests.
public partial class Program { }
