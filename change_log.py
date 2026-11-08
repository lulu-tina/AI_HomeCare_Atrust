"""異動紀錄：把散在各處的紀錄合成一張表，用「類型」篩選。

來源：人工覆寫稽覈日誌、臨時異動、整案調整、週計畫異動、資格追蹤、系統稽核紀錄。
只整理顯示用欄位，不改動原始紀錄；原始紀錄仍各自保存（稽核雜湊鏈不受影響）。
"""
import pandas as pd

COLUMNS = ['時間', '類型', '案家', '任務', '內容', '原因']
AUDIT_PREFIX = '系統：'

# 臨時異動紀錄裡「內容」要顯示的欄位（依序，有值才顯示）
_DYNAMIC_FIELDS = ['日期', '服務時段', '原日期', '原時段', '新日期', '新時段', '原預計結束時間', '新預計結束時間',
                   '延遲分鐘', '處理方式', '原居服員', '居服員', '最終選擇居服員', '改派筆數', '缺人筆數']


def _blank(value):
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() in ('', 'nan', 'None', 'NaT')


def _time(value):
    if _blank(value):
        return ''
    try:
        return pd.Timestamp(value).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return str(value)[:16]


def _row(when, kind, client='', task='', content='', reason=''):
    return {'時間': _time(when), '類型': kind, '案家': '' if _blank(client) else str(client),
            '任務': '' if _blank(task) else str(task), '內容': content, '原因': '' if _blank(reason) else str(reason)}


def _label(fn, value):
    if _blank(value):
        return ''
    try:
        return fn(str(value)) if fn else str(value)
    except Exception:          # 顯示名稱轉換失敗時退回原代碼，不影響整張表
        return str(value)


def build(override_log=None, dynamic_log=(), whole_log=(), plan_log=(), followups=(), audit=None,
          client_label=None, caregiver_label=None):
    """回傳合併後的 DataFrame（新的在上）。client_label／caregiver_label 為顯示名稱函式（可省略）。"""
    rows = []
    cl = lambda v: _label(client_label, v)
    cg = lambda v: _label(caregiver_label, v)
    if override_log is not None and not override_log.empty:
        for r in override_log.to_dict('records'):
            before, after = cg(r.get('AI推薦居服員ID')), cg(r.get('居督指定居服員ID'))
            content = f'{before or "未指派"} → {after or "待安排"}'
            rows.append(_row(r.get('時間戳記'), '改派', cl(r.get('案家ID')), r.get('任務ID'), content, r.get('變更原因')))
    for r in dynamic_log or ():
        kind = str(r.get('事件類型') or '臨時異動')
        if kind.startswith('週計畫異動'):
            continue          # 週計畫異動另有一筆彙總（下方 plan_log），逐筆明細不重複列
        task = r.get('任務ID') or r.get('受影響任務ID') or r.get('延遲任務ID')
        parts = []
        for key in _DYNAMIC_FIELDS:
            value = r.get(key)
            if _blank(value):
                continue
            if key in ('原居服員', '居服員', '最終選擇居服員'):
                value = '、'.join(cg(v) for v in str(value).split('、'))
            parts.append(f'{key}：{value}')
        rows.append(_row(r.get('時間戳記'), kind, cl(r.get('案家ID')), task, '；'.join(parts),
                         r.get('人工改選原因') or r.get('原因')))
    for r in whole_log or ():
        content = f"{r.get('異動', '')}：{r.get('原值', '')} → {r.get('新值', '')}"
        if not _blank(r.get('需確認')):
            content += f"（{r['需確認']}）"
        rows.append(_row(r.get('時間'), '整案調整', cl(r.get('案號')), '', content, r.get('原因')))
    for r in plan_log or ():
        content = (f"{r.get('生效日', '')} 起：{r.get('異動內容', '')}"
                   f"（移除 {r.get('移除舊班次', 0)} 個舊班次、新增 {r.get('新增班次', 0)} 個"
                   + (f"，待安排 {r.get('待安排')}" if r.get('待安排') else '') + '）')
        rows.append(_row(r.get('套用時間'), '週計畫異動', cl(r.get('案家ID')), '', content, r.get('原因')))
    for r in followups or ():
        rows.append(_row(r.get('建立時間'), '資格追蹤', r.get('案家'), '',
                         f"{r.get('需求變更', '')}・{r.get('處理方式', '')}：{r.get('待辦', '')}（{r.get('狀態', '')}）", ''))
    if audit is not None and not audit.empty:
        for r in audit.to_dict('records'):
            rows.append(_row(r.get('時間'), AUDIT_PREFIX + str(r.get('事件', '')), '', '',
                             f"{r.get('帳號') or ''}　{r.get('內容') or ''}".strip(), ''))
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if not frame.empty:
        frame = frame.sort_values('時間', ascending=False, kind='stable').reset_index(drop=True)
    return frame


def kinds(frame):
    """篩選選單用：一般類型在前、系統稽核在後。"""
    found = list(dict.fromkeys(frame['類型'])) if frame is not None and not frame.empty else []
    return sorted([k for k in found if not k.startswith(AUDIT_PREFIX)]) + sorted(k for k in found if k.startswith(AUDIT_PREFIX))


def default_kinds(frame):
    """預設只看班表異動；系統稽核（登入、匯出…）要自己勾。還沒有任何班表異動時，就全部顯示。"""
    found = kinds(frame)
    return [k for k in found if not k.startswith(AUDIT_PREFIX)] or found
