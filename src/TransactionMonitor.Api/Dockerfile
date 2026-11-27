# ---- Build stage -----------------------------------------------------------
FROM mcr.microsoft.com/dotnet/sdk:8.0-alpine AS build
WORKDIR /src

# Restore first, isolated, so dependency layers cache independently of
# source changes.
COPY src/TransactionMonitor.Api/TransactionMonitor.Api.csproj TransactionMonitor.Api/
RUN dotnet restore TransactionMonitor.Api/TransactionMonitor.Api.csproj

COPY src/TransactionMonitor.Api/ TransactionMonitor.Api/
WORKDIR /src/TransactionMonitor.Api
RUN dotnet publish -c Release -o /app/publish \
    --no-restore \
    /p:UseAppHost=false

# ---- Runtime stage ----------------------------------------------------------
FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine AS runtime
WORKDIR /app

# Run as a non-root user.
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

COPY --from=build /app/publish .

ENV ASPNETCORE_URLS=http://+:8080 \
    ASPNETCORE_ENVIRONMENT=Production \
    DOTNET_EnableDiagnostics=0

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:8080/healthz || exit 1

ENTRYPOINT ["dotnet", "TransactionMonitor.Api.dll"]
