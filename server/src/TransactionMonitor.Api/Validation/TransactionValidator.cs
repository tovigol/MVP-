using FluentValidation;
using TransactionMonitor.Api.Models;

namespace TransactionMonitor.Api.Validation;

public sealed class TransactionValidator : AbstractValidator<CreateTransactionRequest>
{
    public TransactionValidator()
    {
        RuleFor(x => x.Amount)
            .GreaterThan(0)
            .WithMessage("Amount must be greater than zero.");

        RuleFor(x => x.Currency)
            .NotEmpty()
            .WithMessage("Currency is required.")
            .Matches(@"^[A-Za-z]{3}$")
            .WithMessage("Currency must be a 3-letter ISO 4217 code (e.g. USD).");

        RuleFor(x => x.Status)
            .IsInEnum()
            .WithMessage("Status must be one of: Pending, Completed, Failed.");

        RuleFor(x => x.Timestamp)
            .Must(ts => ts is null || ts <= DateTimeOffset.UtcNow.AddMinutes(5))
            .WithMessage("Timestamp cannot be more than 5 minutes in the future.");
    }
}
