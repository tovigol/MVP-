"""AgentGuard onboarding screen (Hebrew, RTL).  Run from the repo root:

    streamlit run onboarding/app.py --server.port 8501

Step 1: eleven yes/no questions (checkboxes) -> the LLM (Azure OpenAI, see llm.py) turns the answers into rules.
Step 2: checklist in plain Hebrew -> "approve and save" writes shared/policy.json.
TODO (README section 9): editable numbers in the checklist; the "Activate" screen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `shared` and `onboarding` import

import streamlit as st

from onboarding.baseline import ils_per_usd
from onboarding.llm import active_provider, credentials_problems, provider_label
from onboarding.policy_schema import CATEGORIES
from onboarding.questions import QUESTIONS, SECTIONS, clean_answers
from onboarding.translate import save_policy, translate_answers

CATEGORY_HE = {"payment": "💳 תשלומים", "email_send": "✉️ שליחת מייל",
               "read": "👁️ קריאת מידע", "bulk_export": "📦 ייצוא מידע מלא"}
EFFECT_HE = {"allow": ("✅", "אוטומטי"), "ask": ("✋", "דורש אישור"), "deny": ("⛔", "חסום")}
# {label} is the provider's name (Azure OpenAI / Claude), see llm.provider_label.
SOURCE_HE = {"llm": "החוקים נוצרו על ידי {label} ועברו בדיקה מול התשובות שלך.",
             "llm_retry": "החוקים נוצרו על ידי {label}. הניסיון הראשון תוקן אוטומטית ועבר בדיקה מול התשובות שלך."}
FALLBACK_HE = {
    "no_credentials": "{label} לא עיבד את התשובות: הגדרות החיבור בקובץ .env חסרות או לא תקינות. "
                      "החוקים נבנו ישירות מהתשובות. הפרטים בחלונית הטכנית למטה.",
    "auth_failed": "{label} דחה את ההתחברות (מפתח או כתובת שגויים). "
                   "החוקים נבנו ישירות מהתשובות. הפרטים בחלונית הטכנית למטה.",
    "api_error": "{label} לא היה זמין (שגיאת API או רשת). החוקים נבנו ישירות מהתשובות. "
                 "הפרטים בחלונית הטכנית למטה.",
    "invalid_output": "{label} החזיר חוקים שלא תאמו את התשובות שלך, גם בניסיון חוזר. "
                      "החוקים נבנו ישירות מהתשובות. הפרטים בחלונית הטכנית למטה.",
}

RTL_CSS = """
<style>
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
[data-testid="stHeader"], [data-testid="stSidebar"] { direction: rtl; }
h1, h2, h3, h4, p, li, label, .stMarkdown, [data-testid="stWidgetLabel"],
[data-testid="stCaptionContainer"], [data-testid="stAlert"] { text-align: right; }
/* Inputs follow the language of what is typed: Hebrew -> RTL, a domain like office.co.il -> LTR. */
input, textarea { unicode-bidi: plaintext; text-align: start; }
/* JSON and code stay left-to-right. */
pre, code, [data-testid="stJson"], [data-testid="stCode"] { direction: ltr; text-align: left; }
</style>
"""


def md(text: str) -> str:
    """Streamlit markdown treats $...$ as math. Escape dollar signs so '$100 ... $300' stays text."""
    return text.replace("$", "\\$")


def questions_step() -> None:
    st.title("🛡️ הגדרת חוקים לסוכן שלך")
    provider = active_provider()
    label = provider_label(provider)
    st.write(f"סמני את מה שמתאים. {label} יהפוך את התשובות לחוקים שה־Guard יאכוף על כל פעולה של הסוכן. "
             "מה שלא סימנת נשאר כמו ברירת המחדל, שהיא תמיד האפשרות הזהירה.")
    problems = credentials_problems(provider)
    if not problems:
        st.caption(f"🟢 {label} מוגדר: התשובות יעובדו על ידי המודל.")
    else:
        st.warning(f"הגדרות החיבור ל־{label} בקובץ .env חסרות או לא תקינות, ולכן המודל לא יעבד את התשובות. "
                   "החוקים ייבנו ישירות מהתשובות. לתקן ב־.env ולהגיש שוב.")
        st.code("\n".join(problems), language="text")

    defaults = st.session_state.get("raw_answers", {})
    raw: dict = {}
    for section_key, section_title in SECTIONS.items():
        st.subheader(section_title)
        for q in (q for q in QUESTIONS if q.section == section_key):
            raw[q.id] = st.checkbox(f"{q.number}. {q.text}", value=defaults.get(q.id, q.default), key=q.id)
            st.caption(f"אם לא עונים: {q.default_note}")
            if q.follow_up and raw[q.id]:
                f = q.follow_up
                raw[f.id] = st.text_input(f.label, value=defaults.get(f.id, ""), placeholder=f.placeholder, key=f.id)

    if not st.button("צור את החוקים שלי", type="primary"):
        return
    st.session_state.raw_answers = raw
    answers, problems = clean_answers(raw)
    if problems:
        for problem in problems:
            st.error(problem)
        return
    with st.spinner(f"{label} מתרגם את התשובות שלך לחוקים..."):
        st.session_state.result = translate_answers(answers)
    st.session_state.step = "review"
    st.rerun()


def review_step() -> None:
    result = st.session_state.result
    policy = result.policy
    st.title("✔️ אישור החוקים")
    label = provider_label(result.provider)
    if result.source == "fallback":
        st.warning(FALLBACK_HE.get(result.fallback_reason, FALLBACK_HE["api_error"]).format(label=label))
    else:
        st.success(SOURCE_HE[result.source].format(label=label))
    for note in result.unsupported:
        st.warning(md(note))

    for category in CATEGORIES:
        rules = [r for r in policy["rules"] if r["category"] == category]
        if not rules:
            continue
        st.subheader(CATEGORY_HE[category])
        for rule in rules:
            icon, effect = EFFECT_HE[rule["effect"]]
            st.markdown(md(f"{icon} **{effect}** · {rule['explain']}"))

    st.subheader("🗑️ מחיקה וכלים חדשים")
    icon, effect = EFFECT_HE[policy["default_by_risk"]["destructive"]]
    st.markdown(f"{icon} **{effect}** · מחיקת חשבוניות או מסמכים")
    icon, effect = EFFECT_HE[policy["default_by_risk"]["write"]]
    st.markdown(f"{icon} **{effect}** · כלי חדש שלא מוכר ל־Guard (כדי שהסוכן לא יאבד גישה לכלים חדשים, אבל לא יפעל בלי ידיעתך)")
    st.caption(md(f"הסכומים בדולרים הומרו מהשקלים לפי {ils_per_usd():g} ₪ לדולר. זו הערכה ולא שער חי "
                  "(אפשר לשנות ב־AGENTGUARD_ILS_PER_USD)."))

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
