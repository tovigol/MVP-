"""The look shared by the web app's pages (dashboard and onboarding questions).

Background, white cards, the greeting header, status pills, KPI cards, list items with badges and
Material Symbols icons. Each page adds its own CSS on top (the dashboard its charts, the questions RTL).
Cards: raw HTML in <div class="ag-card">, or Streamlit widgets in st.container(key="card_<name>").
"""
from __future__ import annotations

import html

BRAND = "#f26b1d"

BASE_CSS = """
.stApp { background: #f5f6fa; }
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #eef0f4; }
header[data-testid="stHeader"] { background: transparent; }

.ag-card, [class*="st-key-card"] {
  background: #fff; border: 1px solid #eef0f4; border-radius: 16px;
  box-shadow: 0 1px 3px rgba(16,24,40,.06), 0 1px 2px rgba(16,24,40,.04);
  padding: 18px 20px; margin-bottom: 16px;
}
.ag-card-title { font-size: 16px; font-weight: 650; color: #101828; margin-bottom: 2px; }
.ag-card-sub { font-size: 13px; color: #667085; margin-bottom: 12px; }
.ag-empty { font-size: 14px; color: #98a2b3; padding: 18px 0 8px; }

.ag-top { display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap;
          gap: 12px; margin-bottom: 18px; }
.ag-hello { font-size: 28px; font-weight: 700; color: #101828; line-height: 1.2; }
.ag-hello-sub { font-size: 14px; color: #667085; margin-top: 4px; }
.ag-pills { display: flex; gap: 8px; flex-wrap: wrap; }
.ag-pill { display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; border-radius: 10px;
           font-size: 13px; font-weight: 600; background: #fff; border: 1px solid #eef0f4; color: #344054; }

.ag-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px; }
@media (max-width: 1000px) { .ag-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.ag-kpi-head { display: flex; align-items: center; gap: 10px; font-size: 14px; color: #475467; font-weight: 500; }
.ag-icon { width: 34px; height: 34px; border-radius: 10px; display: inline-flex; align-items: center;
           justify-content: center; font-size: 17px; }
.ag-kpi-body { display: flex; justify-content: space-between; align-items: flex-end; margin-top: 14px; }
.ag-kpi-value { font-size: 30px; font-weight: 700; color: #101828; line-height: 1.1; }
.ag-kpi-sub { font-size: 13px; color: #667085; margin-top: 4px; }
.ag-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }

.ag-item { padding: 12px 0; border-bottom: 1px solid #f2f4f7; }
.ag-item:last-child { border-bottom: 0; }
.ag-item-title { font-size: 14px; font-weight: 600; color: #101828; }
.ag-item-meta { font-size: 12.5px; color: #667085; margin-top: 2px; }
.ag-badge { font-size: 11.5px; font-weight: 600; padding: 2px 8px; border-radius: 999px; margin-left: 6px; }
.ag-btn { display: inline-block; margin-top: 10px; padding: 7px 14px; border-radius: 9px; background: BRAND;
          color: #fff !important; font-size: 13px; font-weight: 600; text-decoration: none !important; }

/* Material Symbols (Rounded). Streamlit ships this font, so it works offline too. */
.ag-mi { font-family: "Material Symbols Rounded"; font-weight: normal; font-style: normal; font-size: 1.2em;
         line-height: 1; letter-spacing: normal; text-transform: none; white-space: nowrap; direction: ltr;
         display: inline-block; vertical-align: -0.22em; font-feature-settings: "liga";
         -webkit-font-smoothing: antialiased; user-select: none; }
.ag-icon .ag-mi { font-size: 21px; vertical-align: 0; }
.ag-pill .ag-mi { font-size: 17px; vertical-align: 0; }
.ag-card-title .ag-mi, .ag-empty .ag-mi { margin-right: 4px; }
.ag-btn .ag-mi { font-size: 16px; margin-left: 4px; vertical-align: -0.2em; }
""".replace("BRAND", BRAND)


def esc(text) -> str:
    return html.escape(str(text))


def mi(name: str, color: str | None = None) -> str:
    """A Material Symbols icon for raw-HTML cards (':material/name:' only works in Streamlit markdown)."""
    style = f' style="color:{color}"' if color else ""
    return f'<span class="ag-mi" aria-hidden="true"{style}>{esc(name)}</span>'


def logo_html(tagline: str) -> str:
    """The AgentGuard logo for the top of the sidebar."""
    return (f'<div dir="ltr" style="font-size:22px;font-weight:700;color:#101828">{mi("shield", BRAND)} Agent'
            f'<span style="color:{BRAND}">Guard</span></div>'
            f'<div style="font-size:12px;color:#667085;margin-bottom:18px">{esc(tagline)}</div>')
