"""AgentGuard onboarding screen. The first page of the web app; run from the repo root:

    streamlit run app.py --server.port 8000

Step 1: a checklist of eleven yes/no questions, each with its own icon -> the LLM (Azure OpenAI,
        see llm.py) turns the answers into rules.
Step 2: checklist in plain English -> "approve and save" writes shared/policy.json and opens the dashboard.
Same look as the dashboard (shared/theme.py): white cards, greeting header, status pills, KPI cards.
TODO (README section 9): editable numbers in the checklist; the "Activate" screen.
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `shared` and `onboarding` import

import streamlit as st

from dashboard.data import CRITICAL, GOOD, WARNING, tint
from onboarding.baseline import ils_per_usd
from onboarding.llm import active_provider, credentials_problems, provider_label
from onboarding.policy_schema import CATEGORIES
from onboarding.questions import QUESTIONS, SECTIONS, clean_answers
from onboarding.translate import save_policy, translate_answers
from shared import theme
from shared.theme import BRAND, mi

# Icons are Material Symbols names (https://fonts.google.com/icons).
CATEGORY_LABELS = {"payment": ("payments", "Payments"), "email_send": ("mail", "Sending email"),
                   "read": ("visibility", "Reading information"), "bulk_export": ("inventory_2", "Full data export")}
# effect -> icon, word, color, badge text color, what it means (the colors match the dashboard).
EFFECTS = {"allow": ("check_circle", "Automatic", GOOD, "#067a18", "The Guard allows it without stopping the agent"),
           "ask": ("front_hand", "Needs approval", WARNING, "#b54708", "The agent waits for your answer in Slack"),
           "deny": ("block", "Blocked", CRITICAL, "#b42318", "Always stopped, before anything happens")}
# {label} is the provider's name (Azure OpenAI / Claude), see llm.provider_label.
SOURCE_TEXT = {"llm": "{label} wrote these rules, and they were checked against your answers.",
               "llm_retry": "{label} wrote these rules. Its first try was fixed automatically and then checked "
                            "against your answers."}
FALLBACK_TEXT = {
    "no_credentials": "{label} didn't process your answers: the connection settings in .env are missing or invalid. "
                      "The rules were built directly from your answers. Details are in the technical panel below.",
    "auth_failed": "{label} rejected the login (wrong key or address). "
                   "The rules were built directly from your answers. Details are in the technical panel below.",
    "api_error": "{label} wasn't available (API or network error). The rules were built directly from your answers. "
                 "Details are in the technical panel below.",
    "invalid_output": "{label} returned rules that didn't match your answers, even on a second try. "
                      "The rules were built directly from your answers. Details are in the technical panel below.",
}
STEPS = ["Questions about your business", "Review the rules", "Dashboard"]
ICON_MD = re.compile(r"^:material/(\w+):\s*")

PAGE_CSS = f"""
<style>
{theme.BASE_CSS}
.block-container {{ padding-top: 2.2rem; max-width: 1180px; }}
.ag-badge {{ margin-left: 0; display: inline-flex; align-items: center; gap: 4px; }}
.ag-kpis-3 {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
.ag-rule {{ font-size: 14px; color: #344054; margin-top: 6px; line-height: 1.55; }}

/* Steps in the sidebar. */
.ag-step {{ display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: 10px;
            font-size: 14px; color: #475467; margin-bottom: 4px; }}
.ag-step.on {{ background: {tint(BRAND, .10)}; color: #101828; font-weight: 650; }}
.ag-step-num {{ width: 24px; height: 24px; border-radius: 50%; display: inline-flex; align-items: center;
                justify-content: center; font-size: 12px; font-weight: 700; background: #f2f4f7; color: #475467; }}
.ag-step.on .ag-step-num {{ background: {BRAND}; color: #fff; }}
.ag-step.done .ag-step-num {{ background: {tint(GOOD, .18)}; color: #067a18; }}
.ag-step-num .ag-mi {{ font-size: 15px; vertical-align: 0; }}
</style>
"""


def md(text: str) -> str:
    """Streamlit markdown treats $...$ as math. Escape dollar signs so '$100 ... $300' stays text."""
    return text.replace("$", "\\$")


def h(text) -> str:
    """Escape text for the raw-HTML cards; '&#36;' keeps a '$' from starting markdown math."""
    return theme.esc(text).replace("$", "&#36;")


def split_icon(title_md: str) -> tuple[str, str]:
    """':material/payments: Part 1' -> ('payments', 'Part 1')."""
    m = ICON_MD.match(title_md)
    return (m.group(1), title_md[m.end():]) if m else ("help", title_md)


def pill(icon: str, color: str, text: str, tone: str | None = None) -> str:
    style = f' style="background:{tint(tone, .12)};border-color:{tint(tone, .35)}"' if tone else ""
    return f'<span class="ag-pill"{style}>{mi(icon, color)} {h(text)}</span>'


def header(icon: str, title: str, sub: str, pills: list[str]) -> None:
    st.markdown(f'<div class="ag-top"><div><div class="ag-hello">{mi(icon, BRAND)} {h(title)}</div>'
                f'<div class="ag-hello-sub">{h(sub)}</div></div>'
                f'<div class="ag-pills">{"".join(pills)}</div></div>', unsafe_allow_html=True)


def sidebar(step: int) -> None:
    rows = []
    for number, label in enumerate(STEPS, 1):
        state = "on" if number == step else "done" if number < step else ""
        badge = mi("check") if number < step else str(number)
        rows.append(f'<div class="ag-step {state}"><span class="ag-step-num">{badge}</span>{h(label)}</div>')
    with st.sidebar:
        st.markdown(theme.logo_html("Your AI assistant's safety guard"), unsafe_allow_html=True)
        st.markdown(f'<div class="ag-card-sub">Steps</div>{"".join(rows)}', unsafe_allow_html=True)
        st.divider()
        st.page_link("dashboard/app.py", label="Go to the dashboard", icon=":material/monitoring:")
        st.caption("The rules are saved in shared/policy.json, and the Gateway checks every agent action against them.")


def questions_step() -> None:
    sidebar(1)
    provider = active_provider()
    label = provider_label(provider)
    problems = credentials_problems(provider)
    llm_pill = (pill("check_circle", GOOD, f"{label} connected") if not problems
                else pill("warning", "#b54708", f"{label} not set up", WARNING))
    header("shield", "Set up your agent's rules",
           f"Tick what fits your business. {label} turns your answers into rules the Guard enforces on every "
           "action of your agent. Anything you leave as is keeps its default, which is always the cautious choice.",
           [llm_pill, pill("checklist", BRAND, f"{len(QUESTIONS)} questions")])
    if problems:
        st.warning(f"The {label} connection settings in .env are missing or invalid, so the model won't process "
                   "your answers. The rules will be built directly from your answers. Fix .env and submit again.")
        st.code("\n".join(problems), language="text")

    defaults = st.session_state.get("raw_answers", {})
    raw: dict = {}
    for column, (section_key, section_title) in zip(st.columns(len(SECTIONS), gap="medium"), SECTIONS.items()):
        icon, title = split_icon(section_title)
        section_questions = [q for q in QUESTIONS if q.section == section_key]
        with column, st.container(key=f"card_{section_key}"):
            st.markdown(f'<div class="ag-card-title">{mi(icon, BRAND)}{h(title)}</div>'
                        f'<div class="ag-card-sub">{len(section_questions)} questions</div>', unsafe_allow_html=True)
            for q in section_questions:
                raw[q.id] = st.checkbox(f":orange[:material/{q.icon}:] {q.number}. {q.text}",
                                        value=defaults.get(q.id, q.default), key=q.id)
                st.caption(f"If you don't answer: {q.default_note}")
                if q.follow_up and raw[q.id]:
                    f = q.follow_up
                    raw[f.id] = st.text_input(f.label, value=defaults.get(f.id, ""), placeholder=f.placeholder,
                                              key=f.id)

    if not st.button("Create my rules", type="primary", icon=":material/auto_awesome:"):
        return
    st.session_state.raw_answers = raw
    answers, problems = clean_answers(raw)
    if problems:
        for problem in problems:
            st.error(problem)
        return
    with st.spinner(f"{label} is turning your answers into rules..."):
        st.session_state.result = translate_answers(answers)
    st.session_state.step = "review"
    st.rerun()


def kpi_html(effect: str, count: int) -> str:
    icon, word, color, _, meaning = EFFECTS[effect]
    return (f'<div class="ag-card"><div class="ag-kpi-head"><span class="ag-icon" '
            f'style="background:{tint(color, .14)}">{mi(icon, color)}</span>{h(word)}</div>'
            f'<div class="ag-kpi-body"><div><div class="ag-kpi-value">{count}</div>'
            f'<div class="ag-kpi-sub">{h(meaning)}</div></div></div></div>')


def rules_card_html(icon: str, title: str, items: list[tuple[str, str]]) -> str:
    rows = []
    for effect, text in items:
        effect_icon, word, color, text_color, _ = EFFECTS[effect]
        rows.append(f'<div class="ag-item"><span class="ag-badge" style="background:{tint(color, .16)};'
                    f'color:{text_color}">{mi(effect_icon)}{h(word)}</span>'
                    f'<div class="ag-rule">{h(text)}</div></div>')
    count = f"{len(items)} rule" + ("" if len(items) == 1 else "s")
    return (f'<div class="ag-card"><div class="ag-card-title">{mi(icon, BRAND)}{h(title)}</div>'
            f'<div class="ag-card-sub">{count}</div>{"".join(rows)}</div>')


def review_step() -> None:
    sidebar(2)
    result = st.session_state.result
    policy = result.policy
    label = provider_label(result.provider)
    if result.source == "fallback":
        source_pill = pill("construction", "#b54708", "Built directly from your answers", WARNING)
        sub = "This is how the Guard will handle every action of your agent. If something is off, go back and edit."
    else:
        source_pill = pill("auto_awesome", BRAND, f"Written by {label}")
        sub = SOURCE_TEXT[result.source].format(label=label) + " If something is off, go back and edit."
    header("task_alt", "Review your rules", sub, [source_pill])
    if result.source == "fallback":
        st.warning(FALLBACK_TEXT.get(result.fallback_reason, FALLBACK_TEXT["api_error"]).format(label=label))
    for note in result.unsupported:
        st.warning(md(note))

    cards = []
    for category in CATEGORIES:
        rules = [r for r in policy["rules"] if r["category"] == category]
        if rules:
            icon, title = CATEGORY_LABELS[category]
            cards.append((icon, title, [(r["effect"], r["explain"]) for r in rules]))
    cards.append(("delete", "Deleting and new tools", [
        (policy["default_by_risk"]["destructive"], "Deleting invoices or documents"),
        (policy["default_by_risk"]["write"], "A new tool the Guard doesn't know yet (so the agent doesn't lose access "
                                             "to new tools, but never acts without you knowing)"),
    ]))

    counts = Counter(effect for _, _, items in cards for effect, _ in items)
    st.markdown(f'<div class="ag-kpis ag-kpis-3">{"".join(kpi_html(e, counts[e]) for e in EFFECTS)}</div>',
                unsafe_allow_html=True)
    columns = st.columns(2, gap="medium")
    for i, card in enumerate(cards):
        columns[i % 2].markdown(rules_card_html(*card), unsafe_allow_html=True)
    st.caption(md(f"Dollar amounts were converted from shekels at ₪{ils_per_usd():g} per dollar. This is an "
                  "estimate, not a live rate (change it with AGENTGUARD_ILS_PER_USD)."))

    if result.errors:
        with st.expander("Technical details (for developers)"):
            st.code("\n".join(result.errors), language="text")
    with st.expander("The JSON that will be saved (for developers)"):
        st.json(policy)

    back, save = st.columns(2)
    if back.button("Back to editing", icon=":material/edit:"):
        st.session_state.step = "questions"
        st.rerun()
    if save.button("Approve and save", type="primary", icon=":material/save:"):
        save_policy(policy)
        st.session_state.just_saved = True  # the dashboard shows a "saved" toast
        st.switch_page("dashboard/app.py")


st.set_page_config(page_title="AgentGuard · Set up rules", page_icon=":material/shield:", layout="wide")
st.markdown(PAGE_CSS, unsafe_allow_html=True)
if st.session_state.get("step", "questions") == "review":
    review_step()
else:
    questions_step()
