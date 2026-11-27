"""Onboarding tests. No network and no API key: Claude is replaced by a fake client.

Run from the repo root:  python -m pytest onboarding -q
"""
import copy
import json
from types import SimpleNamespace

import pytest

from onboarding.baseline import baseline_rules, build_policy
from onboarding.policy_schema import (AMOUNT_OPERATORS, RECIPIENT_OPERATORS, normalize_rules,
                                      validate_policy)
from onboarding.questions import QUESTIONS, clean_answers, parse_domains
from onboarding.translate import save_policy, translate_answers
from shared.policy import OPERATORS, evaluate

RAW = {"business_name": "המאפייה של יוסי", "company_domains": "yossibakery.com",
       "auto_pay_limit_usd": 100, "daily_pay_limit_usd": 300, "max_pay_limit_usd": 1000,
       "trusted_domains": "flour-supplier.com", "forbidden": ["bulk_export"], "free_text": ""}

REGISTRY = {
    "stripe_create_payment_link": {"category": "payment", "risk": "write", "amount_field": "amount",
                                   "amount_unit": "cents", "source": "pack"},
    "email_send_email": {"category": "email_send", "risk": "write",
                         "recipient_fields": ["to", "cc", "bcc"], "source": "claude"},
    "crm_get_customer": {"category": "read", "risk": "read", "source": "claude"},
    "crm_export_all": {"category": "bulk_export", "risk": "write", "source": "reviewed"},
}


@pytest.fixture
def answers():
    cleaned, problems = clean_answers(RAW)
    assert not problems
    return cleaned


@pytest.fixture
def policy(answers):
    return build_policy(answers, baseline_rules(answers))


# --- the contract with the real engine (Person 1) -------------------------------------------
# evaluate(tool, args, policy, registry, daily_total_usd): today's payment total is passed in as a number.

@pytest.mark.parametrize("tool, args, total, expected", [
    ("stripe_create_payment_link", {"amount": 8000}, 0, "allow"),      # $80
    ("stripe_create_payment_link", {"amount": 50000}, 0, "ask"),       # $500
    ("stripe_create_payment_link", {"amount": 500000}, 0, "deny"),     # $5,000
    ("stripe_create_payment_link", {"amount": 9900}, 297, "ask"),      # 4th $99 payment of the day
    ("email_send_email", {"to": "dana@yossibakery.com"}, 0, "allow"),
    ("email_send_email", {"to": "DANA@YossiBakery.COM"}, 0, "allow"),
    ("email_send_email", {"to": "buyer@flour-supplier.com"}, 0, "allow"),
    ("email_send_email", {"to": "dana@yossibakery.com", "bcc": "x@gmail.com"}, 0, "ask"),
    ("email_send_email", {"to": "a@yossibakery.com.evil.io"}, 0, "ask"),
    ("email_send_email", {"to": "new-customer@gmail.com"}, 0, "ask"),
    ("crm_get_customer", {"id": 1}, 0, "allow"),
    ("crm_export_all", {}, 0, "deny"),
])
def test_baseline_policy_through_real_engine(policy, tool, args, total, expected):
    assert evaluate(tool, args, policy, REGISTRY, total).effect == expected


def test_operator_sets_match_the_engine():
    assert AMOUNT_OPERATORS | RECIPIENT_OPERATORS == set(OPERATORS)  # OPERATORS is a name -> handler dict


def test_forbidding_everything_denies(answers):
    answers["forbidden"] = ["bulk_export", "external_email", "all_payments"]
    policy = build_policy(answers, baseline_rules(answers))
    assert validate_policy(policy) == []
    assert evaluate("stripe_create_payment_link", {"amount": 100}, policy, REGISTRY, 0).effect == "deny"
    assert evaluate("email_send_email", {"to": "x@gmail.com"}, policy, REGISTRY, 0).effect == "deny"


# --- validation ------------------------------------------------------------------------------

def _with_rules(policy, *rules):
    bad = copy.deepcopy(policy)
    bad["rules"] = list(rules)
    return bad


def test_baseline_policy_is_valid(policy):
    assert validate_policy(policy) == []


@pytest.mark.parametrize("rule, fragment", [
    ({"id": "r", "category": "payment", "effect": "allow", "when": {"amount_usd_gte": 5}, "explain": "x"},
     "unsupported operator"),
    ({"id": "r", "category": "email_send", "effect": "ask", "when": {"amount_usd_lte": 5}, "explain": "x"},
     "never matches category"),
    ({"id": "r", "category": "read", "effect": "allow", "when": {"all_recipients_in": ["a.com"]}, "explain": "x"},
     "never matches category"),
    ({"id": "r", "category": "payment", "effect": "allow", "when": {}, "explain": "x"},
     "must have amount_usd_lte"),
    ({"id": "r", "category": "email_send", "effect": "allow", "when": {"any_recipient_in": ["a.com"]}, "explain": "x"},
     "must have all_recipients_in"),
    ({"id": "r", "category": "bulk_export", "effect": "allow", "when": {}, "explain": "x"},
     "never be allowed"),
    ({"id": "r", "category": "payment", "effect": "maybe", "when": {}, "explain": "x"}, "effect must be"),
    ({"id": "r", "category": "wire", "effect": "ask", "when": {}, "explain": "x"}, "category must be"),
    ({"id": "R_1", "category": "payment", "effect": "ask", "when": {}, "explain": "x"}, "kebab-case"),
    ({"id": "r", "category": "payment", "effect": "ask", "when": {"amount_usd_lte": "100"}, "explain": "x"},
     "positive number"),
    ({"id": "r", "category": "payment", "effect": "ask", "when": {"amount_usd_lte": True}, "explain": "x"},
     "positive number"),
    ({"id": "r", "category": "payment", "effect": "ask", "when": {}, "explain": "  "}, "explain"),
    ({"id": "r", "category": "email_send", "effect": "ask", "when": {"all_recipients_in": ["not a domain"]},
      "explain": "x"}, "list of lowercase domains"),
])
def test_invalid_rules_are_rejected(policy, rule, fragment):
    problems = validate_policy(_with_rules(policy, rule))
    assert any(fragment in p for p in problems), problems


def test_duplicate_ids_are_rejected(policy):
    rule = {"id": "r", "category": "read", "effect": "allow", "when": {}, "explain": "x"}
    assert any("duplicate id" in p for p in validate_policy(_with_rules(policy, rule, rule)))


def test_catch_all_before_specific_rule_is_rejected(policy):
    catch_all = {"id": "pay-all", "category": "payment", "effect": "ask", "when": {}, "explain": "x"}
    specific = {"id": "pay-small", "category": "payment", "effect": "allow",
                "when": {"amount_usd_lte": 100}, "explain": "x"}
    problems = validate_policy(_with_rules(policy, catch_all, specific))
    assert any("'pay-small' can never match" in p for p in problems)


def test_broad_amount_before_narrow_amount_is_rejected(policy):
    broad = {"id": "pay-medium", "category": "payment", "effect": "ask",
             "when": {"amount_usd_lte": 1000}, "explain": "x"}
    narrow = {"id": "pay-small", "category": "payment", "effect": "allow",
              "when": {"amount_usd_lte": 100, "daily_total_usd_lte": 300}, "explain": "x"}
    assert any("'pay-small' can never match" in p for p in validate_policy(_with_rules(policy, broad, narrow)))


def test_same_conditions_in_different_categories_do_not_shadow(policy):
    rules = [{"id": "a", "category": "read", "effect": "allow", "when": {}, "explain": "x"},
             {"id": "b", "category": "bulk_export", "effect": "deny", "when": {}, "explain": "x"}]
    assert validate_policy(_with_rules(policy, *rules)) == []


def test_company_domains_keyword_needs_company_domains(policy):
    policy = copy.deepcopy(policy)
    policy["company_domains"] = []
    assert any("no company_domains" in p for p in validate_policy(policy))


def test_default_by_risk_must_be_complete(policy):
    policy = copy.deepcopy(policy)
    del policy["default_by_risk"]["destructive"]
    assert any("default_by_risk" in p for p in validate_policy(policy))


def test_normalize_rules_turns_single_keyword_list_into_keyword():
    rules = normalize_rules([{"id": "a", "category": "email_send", "effect": "allow", "explain": "x",
                              "when": {"all_recipients_in": ["company_domains"], "any_recipient_in": ["A.com "]}}])
    assert rules[0]["when"] == {"all_recipients_in": "company_domains", "any_recipient_in": ["a.com"]}


# --- answers ---------------------------------------------------------------------------------

def test_parse_domains_accepts_common_input_shapes():
    valid, invalid = parse_domains("Yossi.com, @bakery.co.il\nhttps://www.flour.com/ shop@mail.example.org nodots")
    # The engine compares domains exactly, so a subdomain must stay a subdomain.
    assert valid == ["yossi.com", "bakery.co.il", "flour.com", "mail.example.org"]
    assert invalid == ["nodots"]


def test_clean_answers_reports_problems_in_hebrew():
    raw = {**RAW, "business_name": " ", "company_domains": "", "daily_pay_limit_usd": 50, "max_pay_limit_usd": "abc"}
    _, problems = clean_answers(raw)
    assert len(problems) == 4
    assert all(any("֐" <= ch <= "׿" for ch in p) for p in problems)


def test_default_answers_are_a_valid_form_state():
    defaults = {q.id: q.default for q in QUESTIONS}
    _, problems = clean_answers({**defaults, "business_name": "x", "company_domains": "x.com"})
    assert problems == []


# --- translate_answers with a fake Claude ----------------------------------------------------

class FakeClient:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        stop_reason, text = reply if isinstance(reply, tuple) else ("end_turn", reply)
        return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


def _reply(answers, unsupported=()):
    rules = copy.deepcopy(baseline_rules(answers))
    for rule in rules:  # Claude is told to write ["company_domains"], so imitate that
        if rule["when"].get("all_recipients_in") == "company_domains":
            rule["when"]["all_recipients_in"] = ["company_domains"]
    return json.dumps({"rules": rules, "unsupported": list(unsupported)}, ensure_ascii=False)


def _bad_reply():
    return json.dumps({"rules": [{"id": "pay-all", "category": "payment", "effect": "allow", "when": {},
                                  "explain": "הכול מותר"}], "unsupported": []}, ensure_ascii=False)


def test_valid_first_answer_is_used(answers):
    client = FakeClient(_reply(answers, unsupported=["אי אפשר להגביל לפי ספק"]))
    result = translate_answers(answers, client=client)
    assert result.source == "claude"
    assert result.unsupported == ["אי אפשר להגביל לפי ספק"]
    assert validate_policy(result.policy) == []
    assert result.policy["default_by_risk"] == {"read": "allow", "write": "ask", "destructive": "ask"}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "fallbacks" in call or call.get("betas") is None  # server-side fallback is opt-out, not forgotten


def test_invalid_first_answer_is_retried_with_the_errors(answers):
    client = FakeClient(_bad_reply(), _reply(answers))
    result = translate_answers(answers, client=client)
    assert result.source == "claude_retry"
    assert len(client.calls) == 2
    feedback = client.calls[1]["messages"][-1]["content"]
    assert "must have amount_usd_lte" in feedback
    assert any("attempt 1" in e for e in result.errors)


def test_two_invalid_answers_fall_back_to_baseline(answers):
    result = translate_answers(answers, client=FakeClient(_bad_reply(), "not json at all"))
    assert result.source == "fallback"
    assert validate_policy(result.policy) == []
    assert [r["id"] for r in result.policy["rules"]] == [r["id"] for r in baseline_rules(answers)]


def test_missing_credentials_fall_back_without_retry(answers):
    client = FakeClient(TypeError("Could not resolve authentication method. Expected one of api_key"))
    result = translate_answers(answers, client=client)
    assert result.source == "fallback"
    assert len(client.calls) == 1
    assert "ANTHROPIC_API_KEY" in result.errors[0]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_and_truncation_fall_back(answers, stop_reason):
    result = translate_answers(answers, client=FakeClient((stop_reason, "{}")))
    assert result.source == "fallback"


def test_fallback_tells_the_owner_the_free_text_was_ignored(answers):
    answers["free_text"] = "אל תשלם ביום שישי"
    result = translate_answers(answers, client=FakeClient(TypeError("authentication")))
    assert result.source == "fallback" and result.unsupported


def test_save_policy_writes_utf8_and_refuses_invalid(tmp_path, policy):
    path = save_policy(policy, tmp_path / "policy.json")
    assert json.loads(path.read_text(encoding="utf-8")) == policy
    assert "המאפייה" in path.read_text(encoding="utf-8")  # not \u-escaped
    assert not (tmp_path / "policy.json.tmp").exists()
    with pytest.raises(ValueError):
        save_policy(_with_rules(policy), tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()
