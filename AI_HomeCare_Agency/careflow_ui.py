"""Presentation-only helpers. No scheduling or external requests."""
CAREFLOW_BUILD = '2026.10.11-v18'
import streamlit as st

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;600;700&display=swap');
:root {
  --cf-ink:#173a38; --cf-teal:#147d73; --cf-teal-dark:#0e665d; --cf-mint:#e4f2ec; --cf-sand:#f1b873;
  --cf-muted:#667c78; --cf-line:#e2eae6; --cf-card:#ffffff; --cf-bg:#f6f8f5;
  --cf-shadow:0 1px 2px rgba(23,58,56,.05),0 6px 18px rgba(23,58,56,.05);
}
html, body, .stApp, [data-testid="stAppViewContainer"], button, input, textarea, select {
  font-family:'Noto Sans TC','PingFang TC','Microsoft JhengHei','Segoe UI',sans-serif;
}
.stApp { background:var(--cf-bg); }
[data-testid="stMainBlockContainer"], [data-testid="stAppViewContainer"] .main .block-container { max-width:1440px; padding-top:1.6rem; padding-bottom:4rem; }
[data-testid="stSidebar"] { background:#eef4f0; border-right:1px solid var(--cf-line); }
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { font-size:15px; letter-spacing:.02em; margin-top:.6rem; }
h1,h2,h3 { color:var(--cf-ink); letter-spacing:-.02em; font-weight:700; }
h3 { font-size:1.25rem; }
[data-testid="stCaptionContainer"] { color:var(--cf-muted); }

/* hero */
.cf-hero { background:radial-gradient(120% 140% at 0% 0%,#1b6f64 0%,#123f3c 60%); border-radius:22px; padding:30px 36px; color:#fff; margin-bottom:16px; box-shadow:var(--cf-shadow); position:relative; overflow:hidden; }
.cf-hero:after { content:""; position:absolute; right:-60px; top:-60px; width:240px; height:240px; border-radius:50%; background:#ffffff0d; }
.cf-eyebrow { font-size:12px; font-weight:700; letter-spacing:.18em; color:#b5dcd0; }
.cf-hero h1 { color:white; font-size:32px; font-weight:700; margin:8px 0; padding:0; }
.cf-hero p { color:#d7ebe4; margin:0; font-size:15px; line-height:1.8; }
.cf-steps { display:flex; gap:10px; flex-wrap:wrap; margin-top:22px; }
.cf-steps span { background:#ffffff14; border:1px solid #ffffff2b; border-radius:999px; padding:8px 16px; font-size:13.5px; }
.cf-bar { display:flex; align-items:center; justify-content:space-between; gap:12px; background:var(--cf-card); border:1px solid var(--cf-line); border-radius:16px; padding:12px 20px; margin-bottom:12px; box-shadow:var(--cf-shadow); }
.cf-bar b { color:var(--cf-ink); font-size:16px; letter-spacing:.02em; }
.cf-bar span { color:var(--cf-muted); font-size:13px; }
.cf-bar i { font-style:normal; background:var(--cf-mint); color:var(--cf-teal-dark); border-radius:999px; padding:3px 10px; font-size:12px; font-weight:600; }

/* section */
.cf-section { display:flex; align-items:center; gap:14px; margin:34px 0 14px; scroll-margin-top:80px; padding-top:6px; border-top:1px solid var(--cf-line); }
.cf-number { background:linear-gradient(135deg,#1a8a7d,#147d73); color:#fff; border-radius:12px; min-width:42px; text-align:center; padding:9px 0; font-size:15px; font-weight:700; box-shadow:0 4px 10px #147d7328; }
.cf-section h2 { font-size:22px; padding:0; margin:0 0 3px; }
.cf-section p { font-size:14px; color:var(--cf-muted); margin:0; }
.cf-nav { display:grid; gap:4px; }
.cf-nav a { padding:10px 14px; border-radius:10px; color:#28574e !important; text-decoration:none; font-size:14px; font-weight:600; border-left:3px solid transparent; }
.cf-nav a:hover { background:#dcece4; border-left-color:var(--cf-teal); }
.cf-empty { border:1px dashed #b9d4c8; background:white; border-radius:18px; text-align:center; padding:35px 20px; margin:12px 0; }
.cf-empty span { font-size:30px; }.cf-empty h3 { font-size:21px; padding:12px 0 8px; }.cf-empty p { color:var(--cf-muted); font-size:14px; }

/* cards */
[data-testid="stMetric"] { background:var(--cf-card); border:1px solid var(--cf-line); border-radius:16px; padding:16px 20px 14px; box-shadow:var(--cf-shadow); background-image:linear-gradient(var(--cf-teal),var(--cf-teal)); background-size:100% 3px; background-repeat:no-repeat; background-position:top; }
[data-testid="stMetricLabel"] { color:var(--cf-muted); font-size:13px; font-weight:600; letter-spacing:.02em; }
[data-testid="stMetricValue"] { color:var(--cf-teal); font-weight:700; font-size:2rem; font-variant-numeric:tabular-nums; }
[data-testid="stMetricDelta"] { font-size:12.5px; }
[data-testid="stExpander"] { background:var(--cf-card); border:1px solid var(--cf-line); border-radius:14px; box-shadow:var(--cf-shadow); }
[data-testid="stExpander"] summary { padding:13px 16px; font-weight:600; }
[data-testid="stFileUploader"] { background:white; padding:18px; border:1px solid var(--cf-line); border-radius:16px; }
[data-testid="stFileUploaderDropzone"] { background:#f4f9f6; border-radius:12px; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border:1px solid var(--cf-line); border-radius:12px; overflow:hidden; box-shadow:var(--cf-shadow); }
[data-testid="stForm"] { background:var(--cf-card); border:1px solid var(--cf-line); border-radius:16px; padding:18px 20px; }
[data-testid="stAlert"] { border-radius:12px; border:1px solid var(--cf-line); }

/* inputs */
[data-baseweb="select"] > div, [data-baseweb="input"] > div, [data-baseweb="textarea"] { border-radius:10px; }
[data-baseweb="select"] > div:focus-within, [data-baseweb="input"] > div:focus-within { box-shadow:0 0 0 3px #147d7326; border-color:var(--cf-teal); }
.stButton button,.stDownloadButton button { border-radius:10px; min-height:42px; font-weight:600; transition:transform .06s ease, box-shadow .15s ease; }
.stButton button:active { transform:translateY(1px); }
.stButton button[kind="primary"] { background:var(--cf-teal); border-color:var(--cf-teal); box-shadow:0 4px 12px #147d7326; }
.stButton button[kind="primary"]:hover { background:var(--cf-teal-dark); border-color:var(--cf-teal-dark); }

/* tabs / segmented */
[data-testid="stTabs"] [role="tablist"] { gap:8px; border-bottom:1px solid var(--cf-line); }
[data-testid="stTabs"] [role="tab"] { border-radius:10px 10px 0 0; padding:10px 18px; font-weight:600; }
[data-testid="stTabs"] [aria-selected="true"] { color:var(--cf-teal); background:var(--cf-mint); }
button[kind="segmented_control"], button[kind="segmented_controlActive"] { border-radius:10px; font-weight:600; min-height:42px; padding:0 18px; }
button[kind="segmented_controlActive"] { background:var(--cf-teal) !important; color:#fff !important; border-color:var(--cf-teal) !important; }
[data-testid="stRadio"] label { font-weight:500; }

.cf-chips { display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 2px; }
.cf-chip { border-radius:999px; padding:3px 11px; font-size:12.5px; font-weight:600; border:1px solid transparent; }
.cf-mint { background:var(--cf-mint); color:var(--cf-teal-dark); border-color:#c9e4d9; }
.cf-amber { background:#fdf0dc; color:#8a5a12; border-color:#f3dbb2; }
.cf-rose { background:#fbe7e4; color:#9a3a30; border-color:#f2c9c3; }
.cf-slate { background:#eef1f0; color:#46605b; border-color:#dde4e1; }

@media(max-width:640px) { .cf-hero { padding:22px 18px; }.cf-hero h1 {font-size:26px;}.cf-steps span {font-size:12px; padding:7px 11px;}.cf-section h2 {font-size:19px;} [data-testid="stMetricValue"] {font-size:1.6rem;} }

/* ---- v14 visual refresh ---- */
.cf-hero { background:linear-gradient(135deg,#0f4c47 0%,#147d73 55%,#2aa190 100%); padding:34px 40px; }
.cf-hero h1 { font-size:34px; }
.cf-steps span b { display:inline-block; background:#ffffff26; border-radius:999px; min-width:26px; text-align:center; margin-right:8px; padding:1px 0; }

/* 匯入區：大型上傳卡 */
.cf-upload-card { background:#fff; border:1px solid var(--cf-line); border-radius:20px; padding:22px 26px 6px; margin:6px 0 14px; box-shadow:var(--cf-shadow); }
.cf-upload-card h3 { margin:0 0 4px; font-size:19px; }
.cf-upload-card p { margin:0 0 10px; color:var(--cf-muted); font-size:14px; }
.cf-flow { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:8px 0 16px; }
.cf-flow div { background:#fff; border:1px solid var(--cf-line); border-radius:16px; padding:16px 18px; box-shadow:var(--cf-shadow); }
.cf-flow div.on { border-color:var(--cf-teal); background:linear-gradient(180deg,#f2faf7,#fff); }
.cf-flow b { display:flex; align-items:center; gap:10px; color:var(--cf-ink); font-size:15px; }
.cf-flow b i { font-style:normal; background:var(--cf-teal); color:#fff; border-radius:9px; width:28px; height:28px; display:inline-flex; align-items:center; justify-content:center; font-size:13px; }
.cf-flow div:not(.on) b i { background:#cfe0d9; color:#47635c; }
.cf-flow span { display:block; margin-top:6px; color:var(--cf-muted); font-size:13px; line-height:1.6; }
[data-testid="stFileUploader"] { border:1.5px dashed #7fbfae; background:linear-gradient(180deg,#f7fcfa,#fff); border-radius:20px; padding:20px 22px; box-shadow:var(--cf-shadow); }
[data-testid="stFileUploader"] section { padding:30px 20px; border-radius:14px; background:#eef8f4; border:0; }
[data-testid="stFileUploader"] section:hover { background:#e2f3ec; }
[data-testid="stFileUploader"] button { background:var(--cf-teal); color:#fff; border:0; min-height:46px; padding:0 26px; font-size:15px; font-weight:700; border-radius:12px; box-shadow:0 6px 16px #147d7338; }
[data-testid="stFileUploader"] button:hover { background:var(--cf-teal-dark); color:#fff; }
[data-testid="stFileUploader"] small { color:var(--cf-muted); }

/* 主要按鈕放大 */
.stButton button[kind="primary"] { min-height:54px; font-size:17px; letter-spacing:.04em; border-radius:14px; background:linear-gradient(135deg,#18968a,#0e665d); box-shadow:0 8px 20px #147d7333; }
.stButton button[kind="primary"]:hover { filter:brightness(1.06); transform:translateY(-1px); }
.stButton button[kind="secondary"] { border-color:#cfe0d9; background:#fff; }
.stButton button[kind="secondary"]:hover { border-color:var(--cf-teal); color:var(--cf-teal-dark); background:#f2faf7; }

/* 狀態列、資料卡 */
.cf-file { display:flex; align-items:center; gap:14px; background:#fff; border:1px solid #bfe0d4; border-left:5px solid var(--cf-teal); border-radius:14px; padding:12px 18px; margin:6px 0 12px; box-shadow:var(--cf-shadow); }
.cf-file b { color:var(--cf-ink); }
.cf-file span { color:var(--cf-muted); font-size:13px; }
.cf-card { background:#fff; border:1px solid var(--cf-line); border-radius:16px; padding:16px 20px; margin:8px 0; box-shadow:var(--cf-shadow); }
.cf-card h4 { margin:0 0 8px; font-size:15px; color:var(--cf-ink); }
.cf-checks { width:100%; border-collapse:collapse; font-size:13.5px; }
.cf-checks th { text-align:left; color:var(--cf-muted); font-weight:600; padding:6px 10px; border-bottom:1px solid var(--cf-line); }
.cf-checks td { padding:7px 10px; border-bottom:1px solid #f0f4f2; vertical-align:top; }
.cf-ok { color:#0e665d; font-weight:700; }.cf-no { color:#9a3a30; font-weight:700; }.cf-warn { color:#8a5a12; font-weight:700; }
[data-testid="stSidebar"] { background:linear-gradient(180deg,#e9f3ee,#f4f8f5); }
.cf-section { border-top:0; margin-top:40px; }
.cf-section h2 { font-size:23px; }
@media(max-width:900px) { .cf-flow { grid-template-columns:1fr; } }
</style>
"""

def render_brand(build=None):
    st.markdown(CSS, unsafe_allow_html=True)
    tag = f'<i>{build}</i>' if build else ''
    if st.session_state.get('last_result') is not None:
        # 已有班表時只留窄版標題，把畫面讓給班表與指標
        st.markdown(f'<div class="cf-bar"><div><b>🌿 CareFlow</b>　<span>居服排班工作台</span></div><div>{tag}</div></div>', unsafe_allow_html=True)
        return
    st.markdown(f'''<div class="cf-hero"><div class="cf-eyebrow">CAREFLOW · 居服排班工作台</div><h1>把排班變得更簡單。</h1><p>整合服務資料，產生排班草案，讓每一次照顧安排更清楚。</p><div class="cf-steps"><span><b>1</b>匯入 Excel</span><span><b>2</b>確認資料</span><span><b>3</b>產生班表</span><span><b>4</b>臨時異動</span></div></div>''', unsafe_allow_html=True)
    st.caption('🔒 排班使用 ID，姓名以遮罩方式顯示；可在「個資顯示設定」調整。' + (f'　版本 {build}' if build else ''))

def chips(items, tone='mint'):
    """小標籤列（只顯示文字，不含任何個資）。tone：mint／amber／rose／slate。"""
    from html import escape
    items = [str(x) for x in (items or []) if str(x).strip()]
    if items:
        st.markdown('<div class="cf-chips">' + ''.join(f'<span class="cf-chip cf-{escape(tone)}">{escape(x)}</span>' for x in items) + '</div>', unsafe_allow_html=True)

def section_title(anchor, number, title, description):
    from html import escape
    st.markdown(f'<div id="{escape(anchor)}" class="cf-section"><span class="cf-number">{escape(number)}</span><div><h2>{escape(title)}</h2><p>{escape(description)}</p></div></div>', unsafe_allow_html=True)

def render_summary(caregivers, clients, tasks):
    cols = st.columns(3)
    for col, label, value in zip(cols, ['居服員', '服務個案', '待排服務'], [caregivers, clients, tasks]):
        col.metric(label, f'{value:,}')


def flow_cards(active=1):
    """匯入前的三步驟說明卡。active：目前所在步驟 1～3。"""
    items = [('1', '上傳 Excel', '機構資料（員工、個案、週服務計畫、Service_code）一份檔案即可。'),
             ('2', '選擇排班期間', '預設整個月；可改一年或自訂日期。'),
             ('3', '按「開始排班」', '系統先排固定週型，再延展到整個期間。')]
    html = ''.join(f'<div class="{"on" if int(n) <= active else ""}"><b><i>{n}</i>{t}</b><span>{d}</span></div>' for n, t, d in items)
    st.markdown(f'<div class="cf-flow">{html}</div>', unsafe_allow_html=True)

def file_status(name, caregivers, clients, tasks):
    from html import escape
    st.markdown(f'<div class="cf-file"><div style="font-size:24px">✅</div><div><b>{escape(str(name))}</b><br><span>已讀取 {caregivers:,} 位居服員 · {clients:,} 位個案 · {tasks:,} 筆服務</span></div></div>', unsafe_allow_html=True)
