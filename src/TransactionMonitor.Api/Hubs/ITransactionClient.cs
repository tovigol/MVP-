using TransactionMonitor.Api.Models;

namespace TransactionMonitor.Api.Hubs;

/// <summary>
/// The set of calls the server can push to a connected client. Using a
/// strongly-typed Hub&lt;T&gt; instead of raw SendAsync("MethodName", ...)
/// means a typo in the method name is a compile error, not a silent
/// runtime miss that the frontend never receives.
/// </summary>
public interface ITransactionClient
{
    Task TransactionReceived(Transaction transaction);
}
