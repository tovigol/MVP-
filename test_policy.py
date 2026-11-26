"""Unit tests for the policy engine. No network, no LLM. Run: python -m pytest -q"""
import copy

import pytest

from shared.policy import evaluate, normalize, log_fields

POLICY = {
    "business": "yossi-bakery",
    "company_domains": ["yossibakery.com"],
    "default_by_risk": {"read": "allow", "write": "ask", "destructive": "ask"},
    "rules": [
        {"id": "pay-small", "category": "payment", "effect": "allow",
         "when": {"amount_usd_lte": 100, "daily_total_usd_lte": 300},
         "explain": "Payments up to $100, and up to $300 per day"},
        {"id": "pay-medium", "category": "payment", "effect": "ask",
         "when": {"amount_usd_lte": 1000},
         "explain": "Payments up to $1,000 need approval"},
        {"id": "pay-large", "category": "payment", "effect": "deny", "when": {},
         "explain": "Payments over $1,000 are blocked"},
        {"id": "email-internal", "category": "email_send", "effect": "allow",
         "when": {"all_recipients_in": "company_domains"}},
        {"id": "email-allowlist", "category": "email_send", "effect": "allow",
         "when": {"all_recipients_in": ["flour-supplier.com"]}},
        {"id": "email-external", "category": "email_send", "effect": "ask", "when": {}},
        {"id": "read-all", "category": "read", "effect": "allow", "when": {}},
        {"id": "no-export", "category": "bulk_export", "effect": "deny", "when": {}},
    ],
}

REGISTRY = {
    "stripe_create_payment_link": {"category": "payment", "risk": "write",
                                   "amount_field": "amount", "amount_unit": "cents", "source": "pack"},
    "stripe_list_customers": {"category": "read", "risk": "read", "source": "claude"},
    "email_send_email": {"category": "email_send", "risk": "write",
                         "recipient_fields": ["to", "cc", "bcc"], "source": "claude"},
    "crm_get_customer": {"category": "read", "risk": "read", "source": "claude"},
    "crm_export_all": {"category": "bulk_export", "risk": "write", "source": "reviewed"},
    "crm_delete_customer": {"category": "crm_write", "risk": "destructive", "source": "reviewed"},
}

PAY = "stripe_create_payment_link"
EMAIL = "email_send_email"


def run(tool, args, total=0.0, policy=POLICY):
    return evaluate(tool, args, policy, REGISTRY, total)


# --- payments -----------------------------------------------------------------

@pytest.mark.parametrize("amount, total, effect, rule", [
    (8000, 0, "allow", "pay-small"),          # $80
    (10000, 0, "allow", "pay-small"),         # exactly $100 (boundary)
    (10001, 0, "ask", "pay-medium"),          # $100.01
    (50000, 0, "ask", "pay-medium"),          # $500
    (100000, 0, "ask", "pay-medium"),         # exactly $1,000 (boundary)
    (500000, 0, "deny", "pay-large"),         # $5,000
    (9900, 198, "allow", "pay-small"),        # 3rd $99 of the day -> $297
    (9900, 297, "ask", "pay-medium"),         # 4th $99 of the day -> $396 > $300
    (5000, 300, "ask", "pay-medium"),         # daily limit already used up
])
def test_payment_amounts(amount, total, effect, rule):
    d = run(PAY, {"amount": amount}, total)
    assert (d.effect, d.rule_id) == (effect, rule)


@pytest.mark.parametrize("amount", ["abc", True, False, -5, None, "8000", float("nan"), float("inf"), [8000]])
def test_invalid_amount_denies(amount):
    assert run(PAY, {"amount": amount}).effect == "deny"


def test_missing_amount_denies():
    assert run(PAY, {"description": "flour"}).effect == "deny"


def test_four_payments_of_99_same_day():
    total, effects = 0.0, []
    for _ in range(4):
        d = run(PAY, {"amount": 9900}, total)
        effects.append(d.effect)
        if d.effect == "allow":
            total += 99
    assert effects == ["allow", "allow", "allow", "ask"]


# --- email --------------------------------------------------------------------

@pytest.mark.parametrize("args, effect, rule", [
    ({"to": "dana@yossibakery.com"}, "allow", "email-internal"),
    ({"to": "DANA@YossiBakery.COM"}, "allow", "email-internal"),
    ({"to": "Dana <dana@yossibakery.com>"}, "allow", "email-internal"),
    ({"to": ["a@yossibakery.com", "b@yossibakery.com"]}, "allow", "email-internal"),
    ({"to": "x@flour-supplier.com"}, "allow", "email-allowlist"),
    ({"to": "dana@yossibakery.com", "bcc": "x@gmail.com"}, "ask", "email-external"),
    ({"to": "a@yossibakery.com, b@gmail.com"}, "ask", "email-external"),
    ({"to": "a@yossibakery.com.evil.io"}, "ask", "email-external"),
    ({"to": "a@sub.yossibakery.com"}, "ask", "email-external"),
    ({"to": "dana@yossibakery.com", "cc": "x@flour-supplier.com"}, "ask", "email-external"),
    ({"to": '"dana@yossibakery.com" <bob@evil.io>'}, "ask", "email-external"),  # address in display name
    ({}, "ask", "email-external"),                                     # no recipients at all
    ({"to": [], "cc": "", "bcc": None}, "ask", "email-external"),      # empty recipient fields
])
def test_email_recipients(args, effect, rule):
    d = run(EMAIL, {"subject": "hi", "body": "...", **args})
    assert (d.effect, d.rule_id) == (effect, rule)


@pytest.mark.parametrize("to", [
    "not-an-email",
    "alice@yossibakery.com)<bob@evil.io>",   # parser-confusion trick
    "dana@yossibakery.com@evil.io",
    ["dana@yossibakery.com", 42],
    123,
])
def test_malformed_recipients_deny(to):
    assert run(EMAIL, {"to": to}).effect == "deny"


def test_any_recipient_in():
    policy = copy.deepcopy(POLICY)
    policy["rules"].insert(0, {"id": "no-evil", "category": "email_send", "effect": "deny",
                               "when": {"any_recipient_in": ["evil.io"]}})
    assert run(EMAIL, {"to": "dana@yossibakery.com", "cc": "x@evil.io"}, policy=policy).rule_id == "no-evil"
    assert run(EMAIL, {"to": "dana@yossibakery.com"}, policy=policy).rule_id == "email-internal"


# --- other tools and defaults -------------------------------------------------

def test_unregistered_tool_asks():
    d = run("stripe_delete_customer", {"id": "cus_1"})
    assert (d.effect, d.rule_id) == ("ask", None)


def test_registered_read_tool_allows():
    assert run("crm_get_customer", {"id": 1}).rule_id == "read-all"


def test_bulk_export_denied():
    d = run("crm_export_all", {})
    assert (d.effect, d.rule_id) == ("deny", "no-export")


def test_destructive_flagged():
    d = run("crm_delete_customer", {"id": 1})
    assert d.effect == "ask" and d.reason.startswith("[DESTRUCTIVE]")


# --- fail-closed --------------------------------------------------------------

def test_unknown_operator_denies():
    policy = copy.deepcopy(POLICY)
    policy["rules"].insert(0, {"id": "typo", "category": "payment", "effect": "allow",
                               "when": {"amount_usd_lt": 100}})
    d = run(PAY, {"amount": 8000}, policy=policy)
    assert d.effect == "deny" and d.reason_for_agent.startswith("Policy check failed")


def test_unknown_operator_after_false_condition_still_denies():
    policy = copy.deepcopy(POLICY)
    policy["rules"].insert(0, {"id": "typo", "category": "payment", "effect": "allow",
                               "when": {"amount_usd_lte": 1, "amout_usd_lte": 100}})
    assert run(PAY, {"amount": 8000}, policy=policy).effect == "deny"


@pytest.mark.parametrize("policy", [
    {},                                                                   # empty file
    {**POLICY, "rules": [{"id": "x", "category": "payment", "effect": "yes", "when": {}}]},  # bad effect
    {**POLICY, "rules": [{"id": "x", "category": "payment", "effect": "allow",
                          "when": {"amount_usd_lte": "100"}}]},           # non-numeric limit
])
def test_broken_policy_denies(policy):
    assert run(PAY, {"amount": 8000}, policy=policy).effect == "deny"


def test_non_dict_args_denies():
    assert run(PAY, ["amount", 8000]).effect == "deny"


# --- helpers used by the Gateway ----------------------------------------------

def test_normalize_facts():
    f = normalize(EMAIL, {"to": "DANA@YossiBakery.com", "bcc": ["x@gmail.com"]}, REGISTRY)
    assert f["category"] == "email_send" and f["recipients"] == ["dana@yossibakery.com", "x@gmail.com"]
    assert normalize(PAY, {"amount": 8000}, REGISTRY)["amount_usd"] == 80.0


def test_log_fields_never_raises():
    assert log_fields(PAY, {"amount": 8000}, REGISTRY) == ("payment", 80.0)
    assert log_fields(PAY, {"amount": "abc"}, REGISTRY) == ("payment", None)
    assert log_fields("nope", None, REGISTRY) == ("unknown", None)
