"""Owner CLI. `python dev/approve.py` lists pending approvals; `<id> yes|no` decides one.

Set GUARD_DB to point at the gateway's database.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import store  # noqa: E402

USAGE = "usage: python dev/approve.py [<id> yes|no]"


def main(argv: list[str]) -> int:
    if not argv:
        rows = store.pending_approvals()
        if not rows:
            print("No pending approvals.")
        for r in rows:
            print(f"{r['approval_id']}  {r['tool']}  amount={r['amount_usd']}  {r['reason']}")
        return 0
    if len(argv) != 2 or argv[1] not in ("yes", "no"):
        print(USAGE)
        return 2
    status = "approved" if argv[1] == "yes" else "rejected"
    if store.decide_approval(argv[0], status, decided_by="cli"):
        print(f"{argv[0]}: {status}")
        return 0
    print(f"{argv[0]}: not pending (already decided, timed out, or unknown id)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
