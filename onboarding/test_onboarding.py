"""Onboarding tests. No network and no API key: the LLM client is replaced by a fake.

Run from the repo root:  python -m pytest onboarding -q
"""
import copy
import json
import os
from types import SimpleNamespace

import pytest

from onboarding import llm
from onboarding.baseline import (baseline_default_by_risk, baseline_rules, build_policy, ils_per_usd, limitation_notes,
                                 limits)
from onboarding.equivalence import REGISTRY, explain_problems, reference_policy, semantic_problems
from onboarding.policy_schema import (AMOUNT_OPERATORS, RECIPIENT_OPERATORS, RULES_JSON_SCHEMA, normalize_rules,
                                      to_strict_schema, validate_policy)
from onboarding.questions import QUESTIONS, clean_answers, parse_addresses, parse_domains
from onboarding.translate import _user_prompt, save_policy, translate_answers
from shared.policy import OPERATORS, evaluate

# A typical owner: pays up to 2,000 ₪ alone with a daily cap, trusts the office domain and two fixed addresses.
RAW = dict(pay_auto=True, pay_daily_limit=True, pay_trusted_suppliers=False, supplier_names="ספק הקמח",
           pay_new_supplier_asks=False,
           mail_internal=True, internal_domain="office.co.il",
           mail_trusted=True, trusted_addresses="accountant@cpa-firm.co.il, office@taxes.gov.il",
           mail_unknown_asks=True, mail_files=False, block_export=True, block_delete=True, read_allowed=True)
AUTO, DAILY = 540, 1891  # 2,000 ₪ and 7,000 ₪ at the default 3.7 ₪ per $, rounded down


@pytest.fixture(autouse=True)
def _stable_env(monkeypatch):
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)  # never read the real .env
    monkeypatch.delenv("AGENTGUARD_ILS_PER_USD", raising=False)


@pytest.fixture
def answers():
    cleaned, problems = clean_answers(RAW)
    assert not problems
    return cleaned


@pytest.fixture
def policy(answers):
    return build_policy(answers, baseline_rules(answers))


def _clean(**changes):
    cleaned, problems = clean_answers({**RAW, **changes})
    assert not problems, problems
    return cleaned


def _policy_for(**changes):
    cleaned = _clean(**changes)
    return build_policy(cleaned, baseline_rules(cleaned))


def _effect(policy, tool, args=None, total=0):
    return evaluate(tool, args or {}, policy, REGISTRY, total).effect


# --- the questionnaire -----------------------------------------------------------------------

def test_questionnaire_matches_the_spec():
    assert [q.number for q in QUESTIONS] == list(range(1, 12))
    assert [sum(q.section == s for q in QUESTIONS) for s in ("payments", "mail", "data")] == [4, 4, 3]
    # "if not answered" defaults: 1 no, 2 yes, 3 no, 4 yes, 5 no, 6 no, 7 yes, 8 no, 9 yes, 10 yes, 11 yes
    assert [q.default for q in QUESTIONS] == [False, True, False, True, False, False, True, False, True, True, True]
    assert "2,000 ₪" in QUESTIONS[0].text and "7,000 ₪" in QUESTIONS[1].text
    assert [q.id for q in QUESTIONS if q.follow_up] == ["pay_trusted_suppliers", "mail_internal", "mail_trusted"]


def test_an_untouched_form_gives_the_cautious_policy():
    cleaned, problems = clean_answers({})
    assert problems == []
    policy = build_policy(cleaned, baseline_rules(cleaned))
    assert validate_policy(policy) == [] and semantic_problems(cleaned, policy) == []
    assert _effect(policy, "pay", {"amount": 1}) == "ask"            # 1: nothing is paid without asking
    assert _effect(policy, "pay", {"amount": DAILY + 1}) == "deny"   # 2: the daily cap is on
    assert _effect(policy, "mail", {"to": "x@unknown-example.org"}) == "ask"   # 5, 6, 7
    assert _effect(policy, "export") == "deny"                       # 9
    assert _effect(policy, "delete") == "deny"                       # 10
    assert _effect(policy, "read") == "allow"                        # 11


@pytest.mark.parametrize("changes, fragment", [
    ({"pay_trusted_suppliers": True, "supplier_names": " "}, "שאלה 3"),
    ({"internal_domain": ""}, "שאלה 5"),
    ({"internal_domain": "gmail.com"}, "חינמי"),
    ({"internal_domain": "not a domain"}, "שאלה 5"),
    ({"trusted_addresses": ""}, "שאלה 6"),
    ({"trusted_addresses": "me@gmail.com, you@walla.co.il"}, "חינמי"),
    ({"trusted_addresses": "@nothing"}, "שאלה 6"),
])
def test_follow_ups_are_required_and_checked(changes, fragment):
    _, problems = clean_answers({**RAW, **changes})
    assert any(fragment in p for p in problems), problems


def test_free_mail_addresses_are_dropped_with_a_warning_and_never_allowed():
    cleaned, problems = clean_answers({**RAW, "trusted_addresses": "me@gmail.com, office@taxes.gov.il"})
    assert problems == []
    assert cleaned["trusted_domains"] == ["taxes.gov.il"] and cleaned["trusted_addresses"] == ["office@taxes.gov.il"]
    assert any("me@gmail.com" in w for w in cleaned["warnings"])
    policy = build_policy(cleaned, baseline_rules(cleaned))
    assert _effect(policy, "mail", {"to": "anyone@gmail.com"}) == "ask"


def test_follow_ups_of_unticked_questions_are_ignored():
    cleaned, problems = clean_answers({**RAW, "mail_trusted": False, "mail_internal": False,
                                       "internal_domain": "gmail.com", "trusted_addresses": "???"})
    assert problems == [] and cleaned["trusted_domains"] == [] and cleaned["company_domains"] == []


def test_parse_domains_and_addresses_accept_common_shapes():
    assert parse_domains("Yossi.com, @bakery.co.il\nhttps://www.flour.com/ shop@mail.example.org nodots") == (
        ["yossi.com", "bakery.co.il", "flour.com", "mail.example.org"], ["nodots"])  # subdomains stay exact
    entries, invalid = parse_addresses("Ana@CPA.co.il; taxes.gov.il ana@cpa.co.il @x.com")
    assert entries == [("ana@cpa.co.il", "cpa.co.il"), ("taxes.gov.il", "taxes.gov.il")]
    assert invalid == ["@x.com"]


# --- the policy, through the real rules engine (Person 1) ------------------------------------

@pytest.mark.parametrize("tool, args, total, expected", [
    ("pay", {"amount": 80}, 0, "allow"),
    ("pay", {"amount": AUTO}, 0, "allow"),
    ("pay", {"amount": AUTO + 1}, 0, "ask"),
    ("pay", {"amount": 100}, 1700, "allow"),          # 1,800 <= daily cap
    ("pay", {"amount": 100}, 1850, "deny"),           # 1,950 would pass the daily cap
    ("pay", {"amount": 600}, 1700, "deny"),
    ("pay", {"amount": 5000}, 0, "deny"),
    ("mail", {"to": "dana@office.co.il"}, 0, "allow"),
    ("mail", {"to": "DANA@Office.CO.IL"}, 0, "allow"),
    ("mail", {"to": "accountant@cpa-firm.co.il"}, 0, "allow"),
    ("mail", {"to": "other@taxes.gov.il"}, 0, "allow"),         # allowed by domain, not by single address
    ("mail", {"to": "new-customer@gmail.com"}, 0, "ask"),
    ("mail", {"to": "a@office.co.il.evil.io"}, 0, "ask"),
    ("mail", {"to": "dana@office.co.il", "bcc": "x@gmail.com"}, 0, "ask"),
    ("read", {}, 0, "allow"),
    ("export", {}, 0, "deny"),
    ("delete", {}, 0, "deny"),
    ("mystery_tool", {}, 0, "ask"),
])
def test_policy_decisions(policy, tool, args, total, expected):
    assert _effect(policy, tool, args, total) == expected


def test_limits_at_the_default_rate():
    lim = limits()
    assert (lim["auto_usd"], lim["daily_usd"], lim["rate"]) == (AUTO, DAILY, 3.7)


def test_exchange_rate_is_configurable_and_a_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_ILS_PER_USD", "4")
    assert (limits()["auto_usd"], limits()["daily_usd"]) == (500, 1750)
    for bad in ("abc", "0", "-3", "nan"):
        monkeypatch.setenv("AGENTGUARD_ILS_PER_USD", bad)
        assert ils_per_usd() == 3.7


@pytest.mark.parametrize("auto", [True, False])
@pytest.mark.parametrize("daily", [True, False])
def test_payment_rules_for_every_combination_of_questions_1_and_2(auto, daily):
    policy = _policy_for(pay_auto=auto, pay_daily_limit=daily)
    assert validate_policy(policy) == []
    assert _effect(policy, "pay", {"amount": 80}) == ("allow" if auto else "ask")
    assert _effect(policy, "pay", {"amount": 5000}) == ("deny" if daily else "ask")
    assert _effect(policy, "pay", {"amount": 5000}, 0) != "allow"


def test_each_remaining_question_changes_what_it_should():
    assert _effect(_policy_for(mail_unknown_asks=False), "mail", {"to": "x@unknown-example.org"}) == "deny"
    assert _effect(_policy_for(mail_internal=False), "mail", {"to": "dana@office.co.il"}) == "ask"
    assert _effect(_policy_for(mail_trusted=False), "mail", {"to": "accountant@cpa-firm.co.il"}) == "ask"
    assert _effect(_policy_for(block_export=False), "export") == "ask"       # never allow, but not always blocked
    assert _effect(_policy_for(block_delete=False), "delete") == "ask"
    assert _effect(_policy_for(read_allowed=False), "read") == "ask"
    assert baseline_default_by_risk(_clean(read_allowed=False))["read"] == "ask"


def test_no_policy_ever_allows_unknown_email_or_export_or_deletion():
    for changes in ({}, {"mail_unknown_asks": False}, {"block_export": False}, {"block_delete": False},
                    {"pay_auto": False, "pay_daily_limit": False}, {"read_allowed": False}):
        policy = _policy_for(**changes)
        assert _effect(policy, "mail", {"to": "x@unknown-example.org"}) != "allow"
        assert _effect(policy, "export") != "allow" and _effect(policy, "delete") != "allow"


# --- what the Guard cannot enforce is told to the owner --------------------------------------

def test_limitations_are_reported_for_questions_3_4_8_and_the_deviations():
    notes = " | ".join(limitation_notes(_clean(pay_trusted_suppliers=True, supplier_names="ספק הקמח, חברת החשמל",
                                                 pay_new_supplier_asks=True, mail_files=True,
                                                 mail_unknown_asks=False, block_export=False)))
    for fragment in ("שאלה 3", "ספק הקמח", "שאלה 4", "שאלה 8", "שאלה 7", "שאלה 9"):
        assert fragment in notes


def test_no_note_about_new_suppliers_when_nothing_is_paid_automatically():
    notes = " | ".join(limitation_notes(_clean(pay_auto=False, pay_new_supplier_asks=True)))
    assert "שאלה 4" not in notes
    assert limitation_notes(_clean(pay_new_supplier_asks=False)) == []


# --- validation ------------------------------------------------------------------------------

def _with_rules(policy, *rules):
    bad = copy.deepcopy(policy)
    bad["rules"] = list(rules)
    return bad


def test_baseline_policy_is_valid(policy):
    assert validate_policy(policy) == []


def test_every_combination_of_answers_gives_a_valid_policy_that_matches_its_reference():
    import itertools
    ids = [q.id for q in QUESTIONS]
    for combo in itertools.product([False, True], repeat=len(ids)):
        cleaned = _clean(**dict(zip(ids, combo)))
        built = build_policy(cleaned, baseline_rules(cleaned))
        assert validate_policy(built) == [] and semantic_problems(cleaned, built) == []
        assert explain_problems(built) == []


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


def test_normalize_rules_cleans_what_the_model_sends():
    rules = normalize_rules([{"id": "a", "category": "email_send", "effect": "allow", "explain": "x",
                              "when": {"all_recipients_in": ["company_domains"], "any_recipient_in": ["A.com "],
                                       "amount_usd_lte": None, "daily_total_usd_lte": None}}])
    assert rules[0]["when"] == {"all_recipients_in": "company_domains", "any_recipient_in": ["a.com"]}


def test_empty_lists_from_strict_mode_mean_no_condition():
    rules = normalize_rules([{"id": "email-unknown", "category": "email_send", "effect": "ask", "explain": "x",
                              "when": {"all_recipients_in": [], "any_recipient_in": [], "amount_usd_lte": None}}])
    assert rules[0]["when"] == {}


def test_operator_sets_match_the_engine():
    assert AMOUNT_OPERATORS | RECIPIENT_OPERATORS == set(OPERATORS)  # OPERATORS is a name -> handler dict


def test_strict_schema_requires_every_property_and_leaves_the_original_alone():
    original = copy.deepcopy(RULES_JSON_SCHEMA)
    strict = to_strict_schema(RULES_JSON_SCHEMA)
    assert RULES_JSON_SCHEMA == original

    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        elif node.get("type") == "array":
            walk(node["items"])
    walk(strict)
    when = strict["properties"]["rules"]["items"]["properties"]["when"]["properties"]
    assert when["amount_usd_lte"]["type"] == ["number", "null"]
    assert when["all_recipients_in"]["type"] == ["array", "null"]


# --- the model's rules are checked against the owner's answers --------------------------------

def _rule(policy, rule_id):
    return next(r for r in policy["rules"] if r["id"] == rule_id)


def test_the_reference_passes_its_own_check(answers, policy):
    assert semantic_problems(answers, reference_policy(answers)) == []
    assert semantic_problems(answers, policy) == []


def test_an_equivalent_but_differently_arranged_policy_passes(answers, policy):
    # Categories are independent: moving the export rule in front of the payment rules changes no decision.
    rearranged = copy.deepcopy(policy)
    rearranged["rules"].sort(key=lambda r: r["category"] != "bulk_export")  # stable: export first, the rest in order
    assert rearranged["rules"] != policy["rules"]
    assert validate_policy(rearranged) == [] and semantic_problems(answers, rearranged) == []


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: _rule(p, "email-unknown").update(effect="deny"), "email to someone@unknown-example.org"),
    (lambda p: _rule(p, "pay-daily-block").update(effect="ask"), "payment of"),
    (lambda p: p["rules"].remove(_rule(p, "no-export")), "an export tool"),
    (lambda p: p["rules"].remove(_rule(p, "email-trusted")), "email to person@cpa-firm.co.il"),
    (lambda p: p["default_by_risk"].update(destructive="ask"), "a delete tool"),
    (lambda p: _rule(p, "pay-auto")["when"].update(amount_usd_lte=AUTO * 10), "payment of"),
])
def test_a_policy_that_misreads_the_answers_is_caught(answers, policy, mutate, fragment):
    wrong = copy.deepcopy(policy)
    mutate(wrong)
    problems = semantic_problems(answers, wrong)
    assert any(fragment in p for p in problems), problems
    assert all("owner's answers require" in p for p in problems if "more calls" not in p)


@pytest.mark.parametrize("rule_id, text, fragment", [
    ("email-internal", "מיילים פנימיים נשלחים אוטומטית; המגבלה היומית היא 7,000 ₪", "must not mention amounts"),
    ("no-export", "ייצוא חסום, עד $5", "must not mention amounts"),
    ("pay-auto", "תשלום עד 3,500 ₪ מתבצע אוטומטית", "not one of the two limits"),
    ("pay-auto", "Payments are automatic", "must be in Hebrew"),
    ("read-all", "קריאה מותרת " + "מאוד " * 60, "one short sentence"),
])
def test_wrong_facts_in_an_explanation_are_caught(policy, rule_id, text, fragment):
    wrong = copy.deepcopy(policy)
    _rule(wrong, rule_id)["explain"] = text
    assert any(fragment in p for p in explain_problems(wrong)), explain_problems(wrong)


def test_the_two_limits_may_be_named_in_either_currency(policy):
    right = copy.deepcopy(policy)
    _rule(right, "pay-auto")["explain"] = "תשלום עד 2,000 ₪ (כ־$540) מתבצע אוטומטית, עד 7,000 ₪ ליום"
    assert explain_problems(right) == []


# --- translate_answers with a fake model ------------------------------------------------------

class FakeClient:
    """Stands in for anthropic.Anthropic."""

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


def _reply(answers, unsupported=(), mutate=None):
    rules = copy.deepcopy(baseline_rules(answers))
    if mutate:
        mutate(rules)
    for rule in rules:  # the model is told to write ["company_domains"], so imitate that
        if rule["when"].get("all_recipients_in") == "company_domains":
            rule["when"]["all_recipients_in"] = ["company_domains"]
    return json.dumps({"rules": rules, "unsupported": list(unsupported)}, ensure_ascii=False)


def _bad_reply():
    """Rejected by the validator: an unbounded allow for payments."""
    return json.dumps({"rules": [{"id": "pay-all", "category": "payment", "effect": "allow", "when": {},
                                  "explain": "הכול מותר"}], "unsupported": []}, ensure_ascii=False)


def _misread_reply(answers):
    """Valid rules that contradict the answers: unknown email is denied although the owner wants to be asked."""
    def flip(rules):
        next(r for r in rules if r["id"] == "email-unknown")["effect"] = "deny"
    return _reply(answers, mutate=flip)


def test_valid_first_answer_is_used(answers):
    client = FakeClient(_reply(answers, unsupported=["הערה של המודל"]))
    result = translate_answers(answers, provider="anthropic", client=client)
    assert result.source == "llm" and result.provider == "anthropic"
    assert "הערה של המודל" in result.unsupported
    assert validate_policy(result.policy) == [] and semantic_problems(answers, result.policy) == []
    assert result.policy["default_by_risk"] == {"read": "allow", "write": "ask", "destructive": "deny"}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "fallbacks" in call or call.get("betas") is None  # server-side fallback is opt-out, not forgotten


def test_only_structured_values_are_sent_to_the_model():
    cleaned = _clean(pay_trusted_suppliers=True, supplier_names="ספק סודי בע״מ")
    prompt = _user_prompt(cleaned)
    assert '"auto_usd": 540' in prompt and '"daily_usd": 1891' in prompt
    assert "cpa-firm.co.il" in prompt                       # the domain is needed for the rule
    assert "ספק סודי" not in prompt and "accountant@" not in prompt   # names and addresses are not


def test_invalid_first_answer_is_retried_with_the_errors(answers):
    client = FakeClient(_bad_reply(), _reply(answers))
    result = translate_answers(answers, provider="anthropic", client=client)
    assert result.source == "llm_retry"
    assert len(client.calls) == 2
    assert "must have amount_usd_lte" in client.calls[1]["messages"][-1]["content"]
    assert any("attempt 1" in e for e in result.errors)


def test_rules_that_misread_the_answers_are_retried_and_then_replaced(answers):
    client = FakeClient(_misread_reply(answers), _reply(answers))
    result = translate_answers(answers, provider="anthropic", client=client)
    assert result.source == "llm_retry"
    assert "owner's answers require 'ask'" in client.calls[1]["messages"][-1]["content"]

    result = translate_answers(answers, provider="anthropic",
                               client=FakeClient(_misread_reply(answers), _misread_reply(answers)))
    assert result.source == "fallback" and result.fallback_reason == "invalid_output"
    assert result.policy["rules"] == baseline_rules(answers)


def test_a_wrong_fact_in_an_explanation_is_retried(answers):
    def leak_limit(rules):
        next(r for r in rules if r["id"] == "read-all")["explain"] = "קריאה מותרת; המגבלה היומית היא 7,000 ₪"
    client = FakeClient(_reply(answers, mutate=leak_limit), _reply(answers))
    result = translate_answers(answers, provider="anthropic", client=client)
    assert result.source == "llm_retry"
    assert "must not mention amounts" in client.calls[1]["messages"][-1]["content"]


def test_two_unusable_answers_fall_back_to_baseline(answers):
    result = translate_answers(answers, provider="anthropic", client=FakeClient(_bad_reply(), "not json at all"))
    assert result.source == "fallback"
    assert [r["id"] for r in result.policy["rules"]] == [r["id"] for r in baseline_rules(answers)]


def test_missing_credentials_fall_back_without_retry(answers):
    client = FakeClient(TypeError("Could not resolve authentication method. Expected one of api_key"))
    result = translate_answers(answers, provider="anthropic", client=client)
    assert result.source == "fallback" and len(client.calls) == 1
    assert "ANTHROPIC_API_KEY" in result.errors[0]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_and_truncation_fall_back(answers, stop_reason):
    result = translate_answers(answers, provider="anthropic", client=FakeClient((stop_reason, "{}")))
    assert result.source == "fallback"


def test_fallback_still_tells_the_owner_what_cannot_be_enforced():
    cleaned = _clean(pay_trusted_suppliers=True, supplier_names="ספק הקמח", pay_new_supplier_asks=True)
    result = translate_answers(cleaned, provider="anthropic", client=FakeClient(TypeError("authentication")))
    assert result.source == "fallback" and any("שאלה 3" in n for n in result.unsupported)


def test_fallback_reason_tells_why_the_model_did_not_run(answers):
    no_key = translate_answers(answers, provider="anthropic",
                               client=FakeClient(TypeError("Could not resolve authentication method")))
    refused = translate_answers(answers, provider="anthropic", client=FakeClient(("refusal", "{}")))
    invalid = translate_answers(answers, provider="anthropic", client=FakeClient(_bad_reply(), "not json"))
    ok = translate_answers(answers, provider="anthropic", client=FakeClient(_reply(answers)))
    assert [no_key.fallback_reason, refused.fallback_reason, invalid.fallback_reason, ok.fallback_reason] == \
        ["no_credentials", "api_error", "invalid_output", None]


def test_blank_anthropic_credentials_count_as_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert llm.credentials_available("anthropic") is False
    assert "ANTHROPIC_API_KEY" not in os.environ  # removed, so the SDK sees "no key" and not an empty one
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert llm.credentials_available("anthropic") is True


def test_save_policy_writes_utf8_and_refuses_invalid(tmp_path, policy):
    path = save_policy(policy, tmp_path / "policy.json")
    assert json.loads(path.read_text(encoding="utf-8")) == policy
    assert "תשלום" in path.read_text(encoding="utf-8")  # not \u-escaped
    assert not (tmp_path / "policy.json.tmp").exists()
    with pytest.raises(ValueError):
        save_policy(_with_rules(policy), tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()


# --- Azure OpenAI provider -------------------------------------------------------------------

AZURE_ENV = {"AZURE_OPENAI_API_KEY": "test-key", "AZURE_OPENAI_ENDPOINT": "https://my-res.openai.azure.com/",
             "AZURE_OPENAI_DEPLOYMENT": "gpt-4o", "AZURE_OPENAI_API_VERSION": "2024-10-21"}


@pytest.fixture
def azure_env(monkeypatch):
    """A clean, valid Azure configuration."""
    for name in ("AGENTGUARD_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in AZURE_ENV.items():
        monkeypatch.setenv(name, value)


class FakeAzureClient:
    """Stands in for openai.AzureOpenAI: client.chat.completions.create(**kwargs)."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        finish, text, refusal = reply if isinstance(reply, tuple) else ("stop", reply, None)
        message = SimpleNamespace(content=text, refusal=refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def _azure_error(cls, message, status):
    import httpx2
    request = httpx2.Request("POST", "https://my-res.openai.azure.com/")
    return cls(message, response=httpx2.Response(status, request=request), body=None)


def _strict_reply(answers):
    """What strict structured outputs return: every `when` key present, unused ones null."""
    rules = copy.deepcopy(baseline_rules(answers))
    for rule in rules:
        when = rule["when"]
        if when.get("all_recipients_in") == "company_domains":
            when["all_recipients_in"] = ["company_domains"]
        for op in ("amount_usd_lte", "daily_total_usd_lte", "all_recipients_in", "any_recipient_in"):
            when.setdefault(op, None)
    return json.dumps({"rules": rules, "unsupported": []}, ensure_ascii=False)


def test_azure_answer_is_used_and_nulls_are_dropped(answers, azure_env):
    client = FakeAzureClient(_strict_reply(answers))
    result = translate_answers(answers, provider="azure", client=client)
    assert result.source == "llm" and result.provider == "azure"
    assert validate_policy(result.policy) == []
    assert result.policy["rules"] == baseline_rules(answers)  # nulls gone, ["company_domains"] normalized
    call = client.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["messages"][0]["role"] == "system" and "AgentGuard" in call["messages"][0]["content"]
    assert call["response_format"]["type"] == "json_schema" and call["response_format"]["json_schema"]["strict"]
    assert call["max_completion_tokens"] == llm.MAX_OUTPUT_TOKENS


def test_azure_invalid_answer_is_retried_with_feedback(answers, azure_env):
    client = FakeAzureClient(_bad_reply(), _strict_reply(answers))
    result = translate_answers(answers, provider="azure", client=client)
    assert result.source == "llm_retry"
    assert [m["role"] for m in client.calls[1]["messages"]] == ["system", "user", "assistant", "user"]
    assert "must have amount_usd_lte" in client.calls[1]["messages"][-1]["content"]


def test_azure_deployment_without_structured_outputs_falls_back_to_json_mode(answers, azure_env):
    import openai
    client = FakeAzureClient(_azure_error(openai.BadRequestError, "response_format json_schema is not supported", 400),
                             _strict_reply(answers))
    result = translate_answers(answers, provider="azure", client=client)
    assert result.source == "llm"
    assert client.calls[1]["response_format"] == {"type": "json_object"}


def test_azure_deployment_that_wants_max_tokens(answers, azure_env):
    import openai
    client = FakeAzureClient(_azure_error(openai.BadRequestError, "Unrecognized argument: max_completion_tokens", 400),
                             _strict_reply(answers))
    assert translate_answers(answers, provider="azure", client=client).source == "llm"
    assert "max_tokens" in client.calls[1] and "max_completion_tokens" not in client.calls[1]


def test_azure_asks_for_low_reasoning_and_drops_it_for_models_that_do_not_know_it(answers, azure_env):
    import openai
    client = FakeAzureClient(_azure_error(openai.BadRequestError, "Unrecognized request argument: reasoning_effort", 400),
                             _strict_reply(answers))
    assert translate_answers(answers, provider="azure", client=client).source == "llm"
    assert client.calls[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in client.calls[1]


def test_reasoning_effort_can_be_switched_off(answers, azure_env, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_REASONING_EFFORT", "none")
    client = FakeAzureClient(_strict_reply(answers))
    translate_answers(answers, provider="azure", client=client)
    assert "reasoning_effort" not in client.calls[0]


@pytest.mark.parametrize("reply", [
    ("length", "{}", None),
    ("content_filter", "", None),
    ("stop", None, "I cannot help"),
])
def test_azure_unusable_answers_fall_back(answers, azure_env, reply):
    result = translate_answers(answers, provider="azure", client=FakeAzureClient(reply))
    assert result.source == "fallback" and result.fallback_reason == "api_error"


def test_azure_rejected_credentials_are_reported_as_such(answers, azure_env):
    import openai
    client = FakeAzureClient(_azure_error(openai.AuthenticationError, "Access denied", 401))
    result = translate_answers(answers, provider="azure", client=client)
    assert result.source == "fallback" and result.fallback_reason == "auth_failed"
    assert len(client.calls) == 1  # no pointless retry


def test_azure_placeholder_endpoint_is_caught_before_any_request(answers, azure_env, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ' "https://YOUR-RESOURCE-NAME.openai.azure.com/" ')
    assert any("example value" in p for p in llm.credentials_problems("azure"))
    result = translate_answers(answers, provider="azure")  # no client: make_client must refuse, not call the network
    assert result.source == "fallback" and result.fallback_reason == "no_credentials"


def test_azure_settings_tolerate_common_env_mistakes(azure_env, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ' "https://my-res.openai.azure.com/openai/deployments/gpt-4o/chat" ')
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "21-10-2024")  # day-month-year -> year-month-day
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", ' "gpt-4o" ')
    settings = llm.azure_settings()
    assert settings["endpoint"] == "https://my-res.openai.azure.com"
    assert settings["api_version"] == "2024-10-21"
    assert settings["deployment"] == "gpt-4o"
    assert llm.credentials_problems("azure") == []
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://openai-resource.openai.azure.com")
    assert llm.azure_settings()["endpoint"] == "https://openai-resource.openai.azure.com"  # not cut at "//openai"
    # A Foundry project address (what the portal shows first) becomes the resource's Azure OpenAI address.
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-res.services.ai.azure.com/api/projects/my-project")
    assert llm.azure_settings()["endpoint"] == "https://my-res.openai.azure.com"
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-res.cognitiveservices.azure.com/")
    assert llm.azure_settings()["endpoint"] == "https://my-res.cognitiveservices.azure.com"
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "latest")
    assert any("AZURE_OPENAI_API_VERSION" in p for p in llm.credentials_problems("azure"))


def test_problems_never_contain_the_secret(azure_env, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    assert "test-key" not in " ".join(llm.credentials_problems("azure"))


def test_provider_selection(azure_env, monkeypatch):
    assert llm.active_provider() == "azure"
    monkeypatch.setenv("AGENTGUARD_PROVIDER", "anthropic")
    assert llm.active_provider() == "anthropic"
    monkeypatch.delenv("AGENTGUARD_PROVIDER")
    for name in AZURE_ENV:
        monkeypatch.delenv(name)
    assert llm.active_provider() == "azure"  # nothing configured: Azure is the documented default
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert llm.active_provider() == "anthropic"  # only a Claude key exists
