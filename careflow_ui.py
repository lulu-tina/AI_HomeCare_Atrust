"""Presentation-only helpers. No scheduling or external requests."""
CAREFLOW_BUILD = '2026.10.18-v19'
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

/* ---- v19 功能模組 ---- */
[data-testid="stSidebar"] [role="radiogroup"] { gap:4px; }
[data-testid="stSidebar"] [role="radiogroup"] label { width:100%; padding:9px 12px; border-radius:10px; border-left:3px solid transparent; margin:0; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:#dcece4; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background:#fff; border-left-color:var(--cf-teal); box-shadow:var(--cf-shadow); }
[data-testid="stSidebar"] [role="radiogroup"] label p { font-size:16.5px; font-weight:600; color:#28574e; line-height:1.45; }
[data-testid="stSidebar"] [role="radiogroup"] label { padding:11px 12px; }
.cf-group { font-size:13.5px; font-weight:700; letter-spacing:.12em; color:#4f6f68; margin:14px 0 6px 2px; }
/* 側邊欄字體放大（v19.1） */
section[data-testid="stSidebar"] { min-width:320px; }
[data-testid="stSidebar"] h3 { font-size:19px !important; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"], [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { font-size:14.5px; line-height:1.65; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary p { font-size:15.5px; }
[data-testid="stSidebar"] [data-testid="stExpander"] p, [data-testid="stSidebar"] [data-testid="stExpander"] label p { font-size:14.5px; }
.cf-module { display:flex; align-items:flex-start; gap:16px; background:#fff; border:1px solid var(--cf-line); border-radius:18px; padding:18px 22px; margin:4px 0 18px; box-shadow:var(--cf-shadow); }
.cf-module .cf-number { font-size:18px; min-width:48px; padding:11px 0; }
.cf-module h2 { font-size:24px; margin:0 0 4px; padding:0; }
.cf-module p { margin:0; color:var(--cf-muted); font-size:14px; line-height:1.7; }
.cf-module .cf-role { display:inline-block; margin-left:10px; vertical-align:middle; font-size:12px; font-weight:700; padding:2px 10px; border-radius:999px; background:var(--cf-mint); color:var(--cf-teal-dark); }
.cf-module .cf-role.mgr { background:#fdf0dc; color:#8a5a12; }
.cf-status { background:#fff; border:1px solid var(--cf-line); border-radius:12px; padding:12px 14px; font-size:14.5px; color:#4f6661; line-height:1.85; }
.cf-status b { color:var(--cf-ink); }
.cf-subhead { font-size:17px; font-weight:700; color:var(--cf-ink); margin:22px 0 6px; padding-left:10px; border-left:4px solid var(--cf-teal); }
/* 分頁切換按鈕（03 的處理項目、臨時異動的事件類型）放大（v19.2） */
button[kind="segmented_control"], button[kind="segmented_controlActive"],
[data-testid="stBaseButton-segmented_control"], [data-testid="stBaseButton-segmented_controlActive"] {
  min-height:52px !important; padding:0 28px !important; border-radius:12px; }
button[kind="segmented_control"] p, button[kind="segmented_controlActive"] p,
[data-testid="stBaseButton-segmented_control"] p, [data-testid="stBaseButton-segmented_controlActive"] p {
  font-size:17.5px !important; font-weight:700 !important; letter-spacing:.02em; }
button[kind="segmented_controlActive"], [data-testid="stBaseButton-segmented_controlActive"] {
  background:var(--cf-teal) !important; color:#fff !important; border-color:var(--cf-teal) !important; }
button[kind="segmented_controlActive"] p, [data-testid="stBaseButton-segmented_controlActive"] p { color:#fff !important; }
[data-testid="stButtonGroup"] { margin:10px 0 6px; }
/* 大型分頁按鈕（v19.3） */
[class*="st-key-cftabs_"] { margin:14px 0 10px; }
[class*="st-key-cftabs_"] .stButton button { min-height:62px !important; border-radius:14px !important; border-width:2px !important; }
[class*="st-key-cftabs_"] .stButton button p { font-size:20px !important; font-weight:700 !important; letter-spacing:.03em; }
[class*="st-key-cftabs_medium"] .stButton button { min-height:52px !important; }
[class*="st-key-cftabs_medium"] .stButton button p { font-size:17.5px !important; }
[class*="st-key-cftabs_"] .stButton button[kind="secondary"] { background:#fff !important; border-color:#bcd9cf !important; color:#28574e !important; box-shadow:var(--cf-shadow); }
[class*="st-key-cftabs_"] .stButton button[kind="secondary"]:hover { border-color:var(--cf-teal) !important; background:#f2faf7 !important; }
[class*="st-key-cftabs_"] .stButton button[kind="primary"] { background:var(--cf-teal) !important; border-color:var(--cf-teal) !important; color:#fff !important; }

/* ---- v19.4 色彩與層次：每個模組一個主色，狀態用顏色區分 ---- */
:root { --cf-acc:#147d73; --cf-acc-soft:#e4f2ec; --cf-acc-ink:#0e4f48;
  --cf-blue:#2f6fb3; --cf-coral:#d0603a; --cf-violet:#6b58b8; --cf-amber:#b7791f;
  --cf-ok:#1f8a5b; --cf-ok-soft:#e5f5ec; --cf-warn:#c2410c; --cf-warn-soft:#fff1e6; --cf-bad:#c03a3a; --cf-bad-soft:#fdecec; }
.stApp { background:radial-gradient(1200px 500px at 100% -10%, var(--cf-acc-soft) 0%, transparent 60%), var(--cf-bg); }
.cf-hero { background:linear-gradient(125deg,#0f5e57 0%,#147d73 45%,#2f6fb3 100%); }
.cf-hero .cf-steps span:nth-child(1) b { background:var(--cf-blue); } .cf-hero .cf-steps span:nth-child(2) b { background:#18a08f; }
.cf-hero .cf-steps span:nth-child(3) b { background:var(--cf-coral); } .cf-hero .cf-steps span:nth-child(4) b { background:var(--cf-violet); }
.cf-hero .cf-steps span:nth-child(5) b { background:var(--cf-amber); }
.cf-bar { border-top:4px solid var(--cf-acc); }
/* 模組頁首：主色色塊＋淡色底 */
.cf-module { border:1px solid color-mix(in srgb, var(--cf-acc) 22%, white); background:linear-gradient(100deg, var(--cf-acc-soft) 0%, #fff 55%); border-left:6px solid var(--cf-acc); }
.cf-module .cf-number { background:var(--cf-acc) !important; color:#fff !important; border-radius:14px; box-shadow:0 6px 14px color-mix(in srgb, var(--cf-acc) 30%, transparent); }
.cf-module h2 { color:var(--cf-acc-ink) !important; }
.cf-module .cf-role { background:#fff; color:var(--cf-acc-ink); border:1px solid color-mix(in srgb, var(--cf-acc) 35%, white); }
.cf-subhead { border-left-color:var(--cf-acc); background:linear-gradient(90deg, var(--cf-acc-soft), transparent 70%); padding:6px 12px; border-radius:0 10px 10px 0; }
/* 側邊欄選單：每個模組的編號顏色 */
[data-testid="stSidebar"] [role="radiogroup"] label:nth-child(1) { --m:var(--cf-blue); }
[data-testid="stSidebar"] [role="radiogroup"] label:nth-child(2) { --m:#147d73; }
[data-testid="stSidebar"] [role="radiogroup"] label:nth-child(3) { --m:var(--cf-coral); }
[data-testid="stSidebar"] [role="radiogroup"] label:nth-child(4) { --m:var(--cf-violet); }
[data-testid="stSidebar"] [role="radiogroup"] label:nth-child(5) { --m:var(--cf-amber); }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { border-left:5px solid var(--m, var(--cf-teal)); background:#fff; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p { color:var(--m, var(--cf-teal)) !important; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:color-mix(in srgb, var(--m, #147d73) 10%, white); }
/* 指標卡：上緣色條輪替，數字用同色 */
[data-testid="stColumn"]:nth-child(5n+1) [data-testid="stMetric"] { --k:#147d73; }
[data-testid="stColumn"]:nth-child(5n+2) [data-testid="stMetric"] { --k:var(--cf-blue); }
[data-testid="stColumn"]:nth-child(5n+3) [data-testid="stMetric"] { --k:var(--cf-coral); }
[data-testid="stColumn"]:nth-child(5n+4) [data-testid="stMetric"] { --k:var(--cf-violet); }
[data-testid="stColumn"]:nth-child(5n+5) [data-testid="stMetric"] { --k:var(--cf-amber); }
[data-testid="stMetric"] { background-image:linear-gradient(var(--k,#147d73),var(--k,#147d73)); background-size:100% 4px; }
[data-testid="stMetricValue"] { color:var(--k,#147d73); }
/* 提示框：左側色條＋淡底，一眼分出成功／注意／錯誤／說明 */
[data-testid="stAlert"] { border:0; border-left:5px solid #9bb5ae; }
[data-testid="stAlertContentSuccess"], [data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) { border-left-color:var(--cf-ok); }
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) { border-left-color:#e08a1e; }
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) { border-left-color:var(--cf-bad); }
[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) { border-left-color:var(--cf-blue); }
/* 表格外框用模組主色淡線 */
[data-testid="stDataFrame"] { border-color:color-mix(in srgb, var(--cf-acc) 25%, white); }
/* 大型分頁按鈕：選中用模組主色 */
[class*="st-key-cftabs_"] .stButton button[kind="primary"] { background:var(--cf-acc) !important; border-color:var(--cf-acc) !important;
  box-shadow:0 6px 16px color-mix(in srgb, var(--cf-acc) 30%, transparent) !important; }
[class*="st-key-cftabs_"] .stButton button[kind="secondary"]:hover { border-color:var(--cf-acc) !important; background:var(--cf-acc-soft) !important; }
[data-testid="stTabs"] [aria-selected="true"] { color:var(--cf-acc-ink); background:var(--cf-acc-soft); }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background:var(--cf-acc); }
/* 引導卡（02 → 03 等下一步提示） */
.cf-guide-card { border-radius:14px; padding:14px 18px; margin:6px 0 10px; background:#fff; border:1px solid var(--cf-line); border-left:6px solid var(--cf-teal); box-shadow:var(--cf-shadow); }
.cf-guide-card.cf-guide-warn { background:linear-gradient(100deg,#fff1e6,#fff 70%); border-left-color:var(--cf-coral); }
.cf-guide-card.cf-guide-ok { background:linear-gradient(100deg,var(--cf-ok-soft),#fff 70%); border-left-color:var(--cf-ok); }
.cf-guide-title { font-size:17px; font-weight:700; color:var(--cf-ink); margin-bottom:4px; }
.cf-guide-body { font-size:14.5px; color:#4f6661; line-height:1.75; }
/* LINE 派案訊息原文（示範） */
.cf-line { background:#8cabd9; background:linear-gradient(180deg,#9bb6dd,#8aa9d6); border-radius:16px; padding:14px 16px 18px; margin:6px 0 10px; }
.cf-line-head { color:#fff; font-size:13px; font-weight:700; letter-spacing:.04em; margin-bottom:10px; opacity:.95; }
.cf-line-bubble { position:relative; display:inline-block; max-width:min(560px,100%); background:#fff; color:#1f2b2a; border-radius:18px; padding:12px 16px;
  font-size:15px; line-height:1.75; white-space:pre-wrap; box-shadow:0 2px 6px rgba(0,0,0,.12); margin-left:10px; }
.cf-line-bubble:before { content:""; position:absolute; left:-7px; top:12px; border-width:7px 10px 7px 0; border-style:solid; border-color:transparent #fff transparent transparent; }
/* 主畫面單選選項與欄位標題放大（v19.6）：只針對單選與輸入欄標題，不動指標數字、按鈕與一般文字 */
[data-testid="stMain"] [role="radiogroup"] label [data-testid="stMarkdownContainer"] p { font-size:17px !important; line-height:1.6; }
[data-testid="stMain"] [role="radiogroup"] { gap:6px; }
[data-testid="stMain"] [data-testid="stNumberInput"] [data-testid="stWidgetLabel"] p,
[data-testid="stMain"] [data-testid="stDateInput"] [data-testid="stWidgetLabel"] p,
[data-testid="stMain"] [data-testid="stSelectbox"] [data-testid="stWidgetLabel"] p { font-size:16px !important; font-weight:600; }
</style>
"""

def render_brand(build=None, compact=False):
    st.markdown(CSS, unsafe_allow_html=True)
    tag = f'<i>{build}</i>' if build else ''
    if compact or st.session_state.get('last_result') is not None:
        # 已有班表時只留窄版標題，把畫面讓給班表與指標
        st.markdown(f'<div class="cf-bar"><div><b>🌿 CareFlow</b>　<span>居服排班工作台</span></div><div>{tag}</div></div>', unsafe_allow_html=True)
        return
    st.markdown(f'''<div class="cf-hero"><div class="cf-eyebrow">CAREFLOW · 居服排班工作台</div><h1>把排班變得更簡單。</h1><p>整合服務資料，產生排班草案，讓每一次照顧安排更清楚。</p><div class="cf-steps"><span><b>1</b>資料匯入與設定</span><span><b>2</b>智慧排班</span><span><b>3</b>衝突與異常</span><span><b>4</b>結果與覆核</span><span><b>5</b>人力與效益</span></div></div>''', unsafe_allow_html=True)
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


# ---------- 功能模組 ----------
# (代碼, 編號, 名稱, 本頁任務, 主要使用者)
MODULES = [
    ('import', '01', '資料匯入與排班設定', 'Excel 匯入、排班期間、交通方式、排班規則及限制條件；確認資料無誤後再進入排班。', '居督'),
    ('schedule', '02', '智慧排班作業', 'AI 自動排班、個案與居服員媒合、候選人推薦、人工調整及日曆班表。', '居督'),
    ('issues', '03', '衝突與異常處理', '集中處理班次重疊、轉場不足、未成功派班、條件不符、臨時異動與替代方案。', '居督'),
    ('review', '04', '排班結果與覆核', '排班完成率、已排與未排班次、警示案件、人工覆寫紀錄、最終班表及匯出。', '居督'),
    ('analytics', '05', '人力與效益分析', '居服員工時、負荷分布、轉場時間、人力配置、服務產出與營運效益。', '主管'),
]
MODULE_KEYS = [m[0] for m in MODULES]
_MODULE_INFO = {m[0]: m for m in MODULES}


def module_label(key, badge=None):
    """側邊欄選單文字，例如「03　衝突與異常處理 · 2」。"""
    _, number, title, _, role = _MODULE_INFO[key]
    text = f'{number}　{title}' + ('（主管）' if role == '主管' else '')
    return text + (f'　· {badge}' if badge else '')


# 每個模組的主色（主色, 淡底, 深字）：頁首、小節標題、分頁按鈕、表格外框都跟著換色。
MODULE_COLORS = {
    'import': ('#2f6fb3', '#e9f1fb', '#1d4a7a'),
    'schedule': ('#147d73', '#e4f2ec', '#0e4f48'),
    'issues': ('#d0603a', '#fcede6', '#8a3519'),
    'review': ('#6b58b8', '#efecf9', '#43358a'),
    'analytics': ('#b7791f', '#fbf1df', '#7a4f10'),
}


def module_header(key):
    """模組頁首：編號、名稱、本頁任務與主要使用者。"""
    from html import escape
    _, number, title, purpose, role = _MODULE_INFO[key]
    acc, soft, ink = MODULE_COLORS.get(key, MODULE_COLORS['schedule'])
    st.markdown(f'<style>:root{{--cf-acc:{acc};--cf-acc-soft:{soft};--cf-acc-ink:{ink};}}</style>', unsafe_allow_html=True)
    role_html = f'<span class="cf-role{" mgr" if role == "主管" else ""}">{escape(role)}</span>'
    st.markdown(f'<div id="module-{escape(key)}" class="cf-module"><span class="cf-number">{escape(number)}</span>'
                f'<div><h2>{escape(title)}{role_html}</h2><p>{escape(purpose)}</p></div></div>', unsafe_allow_html=True)


def subhead(text):
    """模組內的小節標題。"""
    from html import escape
    st.markdown(f'<div class="cf-subhead">{escape(text)}</div>', unsafe_allow_html=True)


def go_to_module(key):
    """按鈕回呼：切換功能模組（回呼在畫面重跑前執行，可直接改選單狀態）。"""
    st.session_state['cf_module'] = key


def module_nav_buttons(current, prefix='nav'):
    """頁尾的上一步／下一步，依排班流程前往相鄰模組。"""
    index = MODULE_KEYS.index(current)
    left, _, right = st.columns([1, 2, 1])
    if index > 0:
        prev = MODULE_KEYS[index - 1]
        left.button('← ' + module_label(prev), key=f'{prefix}_prev_{current}', on_click=go_to_module, args=(prev,), width='stretch')
    if index < len(MODULE_KEYS) - 1:
        nxt = MODULE_KEYS[index + 1]
        right.button(module_label(nxt) + ' →', key=f'{prefix}_next_{current}', on_click=go_to_module, args=(nxt,), width='stretch')


def empty_module(key, message, target='schedule', button='前往「02 智慧排班作業」'):
    """尚無資料或尚未排班時的空白狀態。"""
    from html import escape
    st.markdown(f'<div class="cf-empty"><span>🗂️</span><h3>{escape(_MODULE_INFO[key][2])}</h3><p>{escape(message)}</p></div>',
                unsafe_allow_html=True)
    if target:
        st.button(button, key=f'empty_go_{key}', on_click=go_to_module, args=(target,), type='primary', width='stretch')


def _pick_tab(key, value):
    st.session_state[key] = value


def big_tabs(options, key, icons=None, size='large'):
    """大型分頁按鈕：比 segmented control 明顯，選中的一顆為深綠底白字。回傳目前選項。"""
    options = list(options)
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]
    current = st.session_state[key]
    with st.container(key=f'cftabs_{size}_{key}'):
        cols = st.columns(len(options))
        for i, (col, option) in enumerate(zip(cols, options)):
            label = (f'{icons[i]}  ' if icons and i < len(icons) else '') + option
            col.button(label, key=f'{key}__tab{i}', type='primary' if option == current else 'secondary',
                       on_click=_pick_tab, args=(key, option), width='stretch')
    return current


# ---------- 修改班表原因（處長指定：選單＋其他需補充說明，方便統計） ----------
CHANGE_REASONS = ['交通／時間銜接不足', '案家要求改時間', '案家要求換人', '居服員請假或臨時不可',
                  '資格證照不符', '工時或加班超限', '其他（需補充說明）']
OTHER_REASON = CHANGE_REASONS[-1]


def compose_reason(category, note=''):
    """選單類別＋補充說明 → 寫入紀錄的原因文字；選「其他」卻沒寫說明時回傳 None。"""
    note = str(note or '').strip()
    if not category:
        return None
    if category == OTHER_REASON:
        return f'其他：{note}' if note else None
    return f'{category}｜{note}' if note else category


def reason_category(text):
    """從原因文字取回統計用類別；舊紀錄（自由輸入）歸為「其他」。"""
    text = str(text or '').strip()
    for category in CHANGE_REASONS[:-1]:
        if text.startswith(category):
            return category
    if text.startswith('車程／交通因素'):
        return '交通／時間銜接不足'
    return '其他'


def reason_picker(key, label='修改原因（必選）', default=None, container=None):
    """原因選單＋補充說明。回傳組好的原因文字，未完成時回傳 None。"""
    box = container or st
    index = CHANGE_REASONS.index(default) if default in CHANGE_REASONS else None
    category = box.selectbox(label, CHANGE_REASONS, index=index, placeholder='請選擇原因', key=key + '_cat')
    other = category == OTHER_REASON
    note = box.text_input('補充說明（必填）' if other else '補充說明（選填）', key=key + '_note',
                          placeholder='請寫明原因' if other else '可補充細節，例如：案家要求改到下午')
    return compose_reason(category, note)


REASON_HINT = '請選擇修改原因；選「其他」時要填寫補充說明。'
