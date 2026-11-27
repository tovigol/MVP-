"""AgentGuard onboarding screen. The first page of the web app; run from the repo root:

    streamlit run app.py --server.port 8000

Step 1: eleven yes/no questions (toggles), each with its own icon -> the LLM (Azure OpenAI,
        see llm.py) turns the answers into rules.
Step 2: checklist in plain English -> "approve and save" writes shared/policy.json and opens the dashboard.
Same look as the dashboard (shared/theme.py): white cards, greeting header, status pills, KPI cards.
The review step also shows the "Connect your agent" card (the one URL to paste) before saving.
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `shared` and `onboarding` import

import streamlit as st

from dashboard.data import ACCENT, CRITICAL, GOOD, WARNING, tint
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
# A question's default note: ':green[:material/check:] Yes, blocked' -> tone, icon, text.
NOTE_MD = re.compile(r"^:(red|green)\[:material/(\w+):\]\s*(.*)$")
# Each part of the questionnaire has its own color, used for its icon tiles and its "on" rows.
SECTION_COLORS = {"payments": BRAND, "mail": ACCENT, "data": "#7a5af8"}
# A question that is turned on gets its part's color.
ON_CSS = "".join(f'.st-key-card_{key} [class*="st-key-q_"]:has(input[role="switch"]:checked) '
                 f'{{ background: {tint(color, .06)}; border-color: {tint(color, .35)}; }}\n'
                 for key, color in SECTION_COLORS.items())

PAGE_CSS = f"""
<style>
{theme.BASE_CSS}
.block-container {{ padding-top: 2.2rem; max-width: 1180px; }}
.ag-badge {{ margin-left: 0; display: inline-flex; align-items: center; gap: 4px; }}
.ag-kpis-3 {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
.ag-rule {{ font-size: 14px; color: #344054; margin-top: 6px; line-height: 1.55; }}

/* Questions: one card per part, like the dashboard's KPI cards (tinted icon tile + title). */
.ag-sec-head {{ display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }}
.ag-sec-head .ag-icon {{ width: 40px; height: 40px; border-radius: 12px; }}
.ag-sec-head .ag-card-title {{ font-size: 17px; }}
.ag-sec-head .ag-card-sub {{ margin-bottom: 0; }}

/* One question = one soft tile: icon, question, default badge, toggle on the right. */
[class*="st-key-q_"] {{ border: 1px solid #eef0f4; border-radius: 12px; padding: 12px 12px 12px 14px;
                         background: #fcfcfd; transition: background .15s, border-color .15s; }}
.ag-q {{ display: flex; gap: 12px; align-items: flex-start; }}
.ag-q .ag-icon {{ flex: none; width: 32px; height: 32px; border-radius: 9px; }}
.ag-q .ag-icon .ag-mi {{ font-size: 18px; }}
.ag-q-title {{ font-size: 14.5px; font-weight: 600; color: #101828; line-height: 1.45; }}
.ag-q-num {{ color: #98a2b3; font-weight: 600; margin-right: 2px; }}
.ag-q-def {{ font-size: 12.5px; color: #667085; margin-top: 6px; line-height: 1.5; }}
.ag-q-def .ag-tag {{ display: inline-block; padding: 1px 7px; border-radius: 6px; font-weight: 600; }}
/* Streamlit gives markdown a -16px bottom margin and the row overflow:auto; together they clipped the tag. */
[class*="st-key-q_"] [data-testid="stMarkdownContainer"]:has(.ag-q) {{ margin-bottom: 0 !important; }}
[class*="st-key-q_"] [data-testid="stHorizontalBlock"] {{ overflow: visible; }}
.ag-q-def .ag-mi {{ font-size: 14px; vertical-align: -0.15em; margin-right: 2px; }}
/* Follow-up fields line up with the question text (icon tile 32px + gap 12px). */
[class*="st-key-q_"] .stNumberInput, [class*="st-key-q_"] .stTextInput {{ padding-left: 44px; }}

/* Cards in one row share one height, like the dashboard. */
/* Column > block > layout wrapper > card: let the wrapper and the card fill the column. */
[data-testid="stColumn"] [data-testid="stLayoutWrapper"]:has(> [class*="st-key-card_"]) {{
  flex: 1 1 auto; display: flex; flex-direction: column; }}
[data-testid="stColumn"] [data-testid="stLayoutWrapper"] > [class*="st-key-card_"] {{ flex: 1 1 auto; }}
{ON_CSS}

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


def default_html(note: str) -> str:
    """'If you don't answer' + the default as a small green (yes) or red (no) tag."""
    m = NOTE_MD.match(note)
    if not m:
        return f'<div class="ag-q-def">If you don\'t answer: {h(note)}</div>'
    tone, icon, text = m.groups()
    bg, fg = (tint(GOOD, .12), "#067a18") if tone == "green" else (tint(CRITICAL, .10), "#b42318")
    return (f'<div class="ag-q-def">If you don\'t answer: <span class="ag-tag" style="background:{bg};color:{fg}">'
            f'{mi(icon)}{h(text)}</span></div>')


def section_card(section_key: str, section_title: str, defaults: dict, raw: dict) -> None:
    """One part of the questionnaire: a header like the dashboard's KPI cards, then one tile per question."""
    icon, title = split_icon(section_title)
    color = SECTION_COLORS.get(section_key, BRAND)
    questions = [q for q in QUESTIONS if q.section == section_key]
    on = sum(bool(st.session_state.get(q.id, defaults.get(q.id, q.default))) for q in questions)
    st.markdown(f'<div class="ag-sec-head"><span class="ag-icon" style="background:{tint(color, .14)}">'
                f'{mi(icon, color)}</span><div><div class="ag-card-title">{h(title)}</div>'
                f'<div class="ag-card-sub">{len(questions)} questions · {on} turned on</div></div></div>',
                unsafe_allow_html=True)
    for q in questions:
        with st.container(key=f"q_{q.id}", gap="small"):
            with st.container(horizontal=True, vertical_alignment="top", gap="small", wrap=False):
                st.markdown(f'<div class="ag-q"><span class="ag-icon" style="background:{tint(color, .12)}">'
                            f'{mi(q.icon, color)}</span><div><div class="ag-q-title"><span class="ag-q-num">'
                            f'{q.number}.</span> {h(q.text)}</div>{default_html(q.default_note)}</div></div>',
                            unsafe_allow_html=True, width="stretch")
                raw[q.id] = st.toggle(q.text, value=defaults.get(q.id, q.default), key=q.id,
                                      label_visibility="collapsed", width="content")
            if q.follow_up and raw[q.id]:
                f = q.follow_up
                if f.kind == "amount":  # questions 1 and 2: the owner chooses the amount
                    raw[f.id] = st.number_input(f.label, min_value=1, step=100, format="%d",
                                                value=int(defaults.get(f.id) or f.default), key=f.id)
                else:
                    raw[f.id] = st.text_input(f.label, value=defaults.get(f.id, ""),
                                              placeholder=f.placeholder, key=f.id)


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
        with column, st.container(key=f"card_{section_key}", gap="small"):
            section_card(section_key, section_title, defaults, raw)

    actions = st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center")
    actions.caption("Not sure? Leave the default. It is always the cautious choice, and you can change it later.")
    if not actions.button("Create my rules", type="primary", icon=":material/auto_awesome:"):
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

    # "Activate" (README section 2): show the one URL the agent connects to. Display only.
    with st.container(key="card_connect"):
        st.markdown(f'<div class="ag-card-title">{mi("link", BRAND)}Connect your agent</div>'
                    '<div class="ag-card-sub">Paste this one URL into your agent. From then on every action goes '
                    "through the Guard, and the agent never holds your Stripe, email or Slack keys.</div>",
                    unsafe_allow_html=True)
        st.code("http://localhost:8080/mcp", language="text")
        azure, claude, claude_code, n8n = st.tabs(["Azure OpenAI agent", "Claude", "Claude Code", "n8n"])
        azure.success("Your Azure OpenAI agent is already connected to this URL.", icon=":material/check_circle:")
        azure.code('GATEWAY_URL="http://localhost:8080/mcp"', language="bash")
        claude.write("Settings → Connectors → Add custom connector → paste the URL.")
        claude_code.code("claude mcp add --transport http agentguard http://localhost:8080/mcp", language="bash")
        n8n.write("Add an **MCP Client Tool** node to your AI Agent → Endpoint: paste the URL → "
                  "Transport: HTTP Streamable.")

    actions = st.container(horizontal=True, horizontal_alignment="distribute")  # Back on the left, Save on the right
    if actions.button("Back to editing", icon=":material/edit:"):
        st.session_state.step = "questions"
        st.rerun()
    if actions.button("Approve and save", type="primary", icon=":material/save:"):
        save_policy(policy)
        st.session_state.just_saved = True  # the dashboard shows a "saved" toast
        st.switch_page("dashboard/app.py")


st.set_page_config(page_title="AgentGuard · Set up rules", page_icon=":material/shield:", layout="wide")
st.markdown(PAGE_CSS, unsafe_allow_html=True)
if st.session_state.get("step", "questions") == "review":
    review_step()
else:
    questions_step()
