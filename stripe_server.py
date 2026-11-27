# stripe_server.py
import os
import stripe
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("stripe")


def _init_stripe() -> None:
    api_key = os.getenv("STRIPE_API_KEY")
    if not api_key:
        raise RuntimeError("STRIPE_API_KEY is missing in .env")
    stripe.api_key = api_key


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def create_payment(
    amount_cents: int,
    recipient: str,
    description: str = "",
) -> dict:
    """Create and confirm a USD payment in Stripe Sandbox.

    Args:
        amount_cents: Payment amount in US cents (e.g., 8000 for $80.00).
        recipient: Name of the vendor or recipient being paid.
        description: Optional payment description or invoice note.
    """
    _init_stripe()
    if amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")

    intent = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="usd",
        description=description or f"Payment to {recipient}",
        metadata={"recipient": recipient},
        payment_method="pm_card_visa",
        confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
    )
    return {
        "payment_id": intent.id,
        "status": intent.status,
        "amount_cents": intent.amount,
        "amount_usd": round(intent.amount / 100, 2),
        "currency": intent.currency,
        "recipient": recipient,
        "description": intent.description,
    }


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def list_customers(limit: int = 10) -> list[dict]:
    """List customers from Stripe Sandbox.

    Args:
        limit: Maximum number of customers to return (1-100).
    """
    _init_stripe()
    customers = stripe.Customer.list(limit=min(max(limit, 1), 100))
    return [
        {
            "id": c.id,
            "name": c.name,
            "email": c.email,
        }
        for c in customers.data
    ]


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def list_payments(limit: int = 10) -> list[dict]:
    """List recent PaymentIntents from Stripe Sandbox.

    Args:
        limit: Maximum number of payments to return (1-100).
    """
    _init_stripe()
    intents = stripe.PaymentIntent.list(limit=min(max(limit, 1), 100))
    return [
        {
            "payment_id": pi.id,
            "status": pi.status,
            "amount_cents": pi.amount,
            "amount_usd": round(pi.amount / 100, 2),
            "recipient": (pi.metadata or {}).get("recipient", ""),
            "description": pi.description or "",
        }
        for pi in intents.data
    ]


if __name__ == "__main__":
    mcp.run()