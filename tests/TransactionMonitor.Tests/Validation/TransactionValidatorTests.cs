using FluentAssertions;
using TransactionMonitor.Api.Models;
using TransactionMonitor.Api.Validation;
using Xunit;

namespace TransactionMonitor.Tests.Validation;

public class TransactionValidatorTests
{
    private readonly TransactionValidator _validator = new();

    private static CreateTransactionRequest ValidRequest() => new()
    {
        Amount = 1500.50m,
        Currency = "USD",
        Status = TransactionStatus.Pending,
        Timestamp = DateTimeOffset.UtcNow
    };

    [Fact]
    public void Valid_request_passes_validation()
    {
        var result = _validator.Validate(ValidRequest());

        result.IsValid.Should().BeTrue();
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-10)]
    public void Amount_must_be_greater_than_zero(decimal amount)
    {
        var request = ValidRequest();
        request.Amount = amount;

        var result = _validator.Validate(request);

        result.IsValid.Should().BeFalse();
        result.Errors.Should().Contain(e => e.PropertyName == nameof(CreateTransactionRequest.Amount));
    }

    [Theory]
    [InlineData("")]
    [InlineData("US")]
    [InlineData("USDD")]
    [InlineData("12D")]
    public void Currency_must_be_a_three_letter_code(string currency)
    {
        var request = ValidRequest();
        request.Currency = currency;

        var result = _validator.Validate(request);

        result.IsValid.Should().BeFalse();
        result.Errors.Should().Contain(e => e.PropertyName == nameof(CreateTransactionRequest.Currency));
    }

    [Fact]
    public void Status_outside_the_enum_range_is_rejected()
    {
        var request = ValidRequest();
        request.Status = (TransactionStatus)999;

        var result = _validator.Validate(request);

        result.IsValid.Should().BeFalse();
        result.Errors.Should().Contain(e => e.PropertyName == nameof(CreateTransactionRequest.Status));
    }

    [Fact]
    public void Timestamp_far_in_the_future_is_rejected()
    {
        var request = ValidRequest();
        request.Timestamp = DateTimeOffset.UtcNow.AddHours(1);

        var result = _validator.Validate(request);

        result.IsValid.Should().BeFalse();
        result.Errors.Should().Contain(e => e.PropertyName == nameof(CreateTransactionRequest.Timestamp));
    }

    [Fact]
    public void Missing_timestamp_is_allowed_since_the_server_assigns_one()
    {
        var request = ValidRequest();
        request.Timestamp = null;

        var result = _validator.Validate(request);

        result.IsValid.Should().BeTrue();
    }
}
