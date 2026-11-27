"""AgentGuard onboarding screen (Hebrew, RTL).  Run from the repo root:

    streamlit run onboarding/app.py --server.port 8501

Step 1: questions -> Claude turns the answers into rules.
Step 2: checklist in plain Hebrew -> "approve and save" writes shared/policy.json.
TODO (README section 9): editable numbers in the checklist; the "Activate" screen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `shared` and `onboarding` import

import streamlit as st

from onboarding.policy_schema import CATEGORIES
from onboarding.questions import QUESTIONS, clean_answers
from onboarding.translate import save_policy, translate_answers

CATEGORY_HE = {"payment": "💳 תשלומים", "email_send": "✉️ שליחת מייל",
               "read": "👁️ קריאת מידע", "bulk_export": "📦 ייצוא מידע מלא"}
EFFECT_HE = {"allow": ("✅", "אוטומטי"), "ask": ("✋", "דורש אישור"), "deny": ("⛔", "חסום")}
SOURCE_HE = {"claude": ("success", "החוקים נוצרו על ידי Claude ועברו בדיקה."),
             "claude_retry": ("success", "החוקים נוצרו על ידי Claude. הניסיון הראשון תוקן אוטומטית ועבר בדיקה."),
             "fallback": ("warning", "Claude לא היה זמין, אז החוקים נבנו מהתשובות לשאלות בלבד.")}

RTL_CSS = """
<style>
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
[data-testid="stHeader"], [data-testid="stSidebar"] { direction: rtl; }
h1, h2, h3, h4, p, li, label, .stMarkdown, [data-testid="stWidgetLabel"],
[data-testid="stCaptionContainer"], [data-testid="stAlert"] { text-align: right; }
/* Inputs follow the language of what is typed: Hebrew -> RTL, a domain like yossibakery.com -> LTR. */
input, textarea { unicode-bidi: plaintext; text-align: start; }
/* JSON and code stay left-to-right. */
pre, code, [data-testid="stJson"], [data-testid="stCode"] { direction: ltr; text-align: left; }
</style>
"""


def md(text: str) -> str:
    """Streamlit markdown treats $...$ as math. Escape dollar signs so '$100 ... $300' stays text."""
    return text.replace("$", "\\$")


def render_question(q, defaults: dict):
    default = defaults.get(q.id, q.default)
    if q.kind == "number":
        return st.number_input(q.label, min_value=1.0, value=float(default), step=10.0, format="%g",
                               help=q.help, key=q.id)
    if q.kind == "multiselect":
        return st.multiselect(q.label, options=list(q.options), default=default,
                              format_func=q.options.get, help=q.help, key=q.id)
    if q.kind == "longtext":
        return st.text_area(q.label, value=default, placeholder=q.placeholder, help=q.help, key=q.id)
    return st.text_input(q.label, value=default, placeholder=q.placeholder, help=q.help, key=q.id)


def questions_step() -> None:
    st.title("🛡️ הגדרת חוקים לסוכן שלך")
    st.write("ענה על כמה שאלות קצרות, ו־Claude יהפוך אותן לחוקים שה־Guard יאכוף על כל פעולה של הסוכן.")
    defaults = st.session_state.get("raw_answers", {})
    with st.form("questions"):
        raw = {q.id: render_question(q, defaults) for q in QUESTIONS}
        submitted = st.form_submit_button("צור את החוקים שלי", type="primary")
    if not submitted:
        return
    st.session_state.raw_answers = raw
    answers, problems = clean_answers(raw)
    if problems:
        for problem in problems:
            st.error(problem)
        return
    with st.spinner("Claude מתרגם את התשובות שלך לחוקים..."):
        st.session_state.result = translate_answers(answers)
    st.session_state.step = "review"
    st.rerun()


def review_step() -> None:
    result = st.session_state.result
    policy = result.policy
    st.title("✔️ אישור החוקים")
    level, message = SOURCE_HE[result.source]
    getattr(st, level)(message)
    for note in result.unsupported:
        st.warning(md(note))

    st.caption(f"עסק: {policy['business']} · דומיינים של העסק: {', '.join(policy['company_domains'])}")
    for category in CATEGORIES:
        rules = [r for r in policy["rules"] if r["category"] == category]
        if not rules:
            continue
        st.subheader(CATEGORY_HE[category])
        for rule in rules:
            icon, label = EFFECT_HE[rule["effect"]]
            st.markdown(md(f"{icon} **{label}** · {rule['explain']}"))
    st.info("כלי חדש שלא מוכר ל־Guard דורש את אישורך, כדי שהסוכן לא יאבד גישה לכלים חדשים אבל לא יפעל בלי ידיעתך.")

    if result.errors:
        with st.expander("פרטים טכניים (למפתחים)"):
            st.code("\n".join(result.errors), language="text")
    with st.expander("ה־JSON שיישמר (למפתחים)"):
        st.json(policy)

    back, save = st.columns(2)
    if back.button("חזרה לעריכה"):
        st.session_state.step = "questions"
        st.rerun()
    if save.button("אשר ושמור", type="primary"):
        save_policy(policy)
        st.success("החוקים נשמרו ב־shared/policy.json. ה־Gateway יקרא אותם בקריאה הבאה של הסוכן.")


st.set_page_config(page_title="AgentGuard · הגדרת חוקים", page_icon="🛡️", layout="centered")
st.markdown(RTL_CSS, unsafe_allow_html=True)
if st.session_state.get("step", "questions") == "review":
    review_step()
else:
    questions_step()
