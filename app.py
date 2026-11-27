"""AgentGuard web app: the onboarding questions first, then the live dashboard.  Run from the repo root:

    streamlit run app.py --server.port 8000

"Approve and save" on the questions page opens the dashboard; the dashboard's
"Back to the questions" button returns to the questions with the previous answers filled in.
"""
import streamlit as st

QUESTIONS_PAGE = st.Page("onboarding/app.py", title="Set up rules", icon=":material/shield:",
                         url_path="questions", default=True)
DASHBOARD_PAGE = st.Page("dashboard/app.py", title="Dashboard", icon=":material/monitoring:",
                         url_path="dashboard")

st.navigation([QUESTIONS_PAGE, DASHBOARD_PAGE], position="hidden").run()
