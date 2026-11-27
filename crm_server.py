# crm_server.py
import csv
from pathlib import Path
from fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "customers.csv"

mcp = FastMCP("crm")


def _load_customers() -> list[dict]:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CRM CSV file not found: {CSV_PATH}")
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def get_customer(query: str) -> list[dict]:
    """Search for matching customer records in the bakery CRM by ID, name, email, or company.
    Returns up to 3 matching records.

    Args:
        query: Search string of at least 2 characters (e.g. C004, David, or email).
    """
    q = query.strip().lower()
    if len(q) < 2:
        raise ValueError("query too short: must be at least 2 characters")

    rows = _load_customers()
    fields = ("id", "name", "email", "company")
    matches = [
        row for row in rows
        if any(q in (row.get(field) or "").lower() for field in fields)
    ]
    return matches[:3]


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def export_all() -> list[dict]:
    """Bulk export the entire customer database (all rows, emails, balances, and notes).
    Sensitive bulk data export operation.
    """
    return _load_customers()


if __name__ == "__main__":
    mcp.run()