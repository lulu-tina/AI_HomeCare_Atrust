# CLAUDE.md

給 Claude Code 在此 repo 工作時的說明。使用者文件以 README.md 為準。

## 專案概要

CareFlow：居家長照（居服）機構的排班系統。讀取機構 Excel（員工資料、個案資料、週服務計畫、Service_code、兩張姓名對照），
展開排班期間的每一次服務，在硬性條件（證照、性別、不適合配對、請假、工時、休息、轉場）下自動排班，
再由居督確認、覆寫、處理臨時異動。介面為 Streamlit（`app.py`），分 01～05 五個模組。

## 主要檔案

| 檔案 | 用途 |
|---|---|
| `app.py` | Streamlit 介面（01 匯入設定、02 排班、03 衝突與異動、04 結果覆核、05 分析） |
| `caregiver_engine.py` | 核心：資料載入、`PipelineConfig`、硬性條件 `_check_hard_constraints`、交通 `calc_travel_minutes`、路線與工資 |
| `monthly_scheduler.py`、`team_scheduler.py`、`fast_calendar.py` | 期間排班（週序列＋CP-SAT 聯合選人＋延展） |
| `substitute_rank.py` | 代班／臨時支援的共用排序 |
| `dispatch_workflow.py` | 派案狀態（建議→督導確認→待回覆→已接單→已通知案家）、請假、本機資料庫 |
| `issue_center.py` | 衝突與異常分類 |
| `dashboard_metrics.py`、`dashboard_view.py` | 05 模組儀表板 |
| `kpi_metrics.py` | 試辦 KPI（分子／分母／期間／基準值），定義見 PILOT_KPI.md |
| `audit_log.py`、`data_retention.py` | 只能附加的稽核紀錄（雜湊鏈）、保存期限刪除 |
| `tools/benchmark.py` | 基準測試（沙盒或 OSRM 交通） |
| `archive/` | 已套用的一次性修補腳本，不要執行 |

## 文件

README.md（使用說明）、VALIDATION.md（驗證紀錄）、PILOT_KPI.md、PILOT_PLAN.md、DECISION_POLICY.md、DATA_GOVERNANCE.md。

## 執行

```bash
pip install -r requirements.txt
streamlit run app.py
python tools/benchmark.py                 # 沙盒交通，無網路可跑
python tools/benchmark.py --travel osrm   # 真實機車路網
python -m pytest -q tests/test_kpi_metrics.py tests/test_unsuitable_pairing.py tests/test_governance.py
```

`tests/` 中有一批舊測試仍使用已移除的參數（如 `continuity_policy`），在 v20 之前就已失敗，修改時以「不新增失敗」為準。

## 修改時的規則

- 新的政策參數先加進 `PipelineConfig`，再接到介面；不要把常數寫死在 `app.py`。
- 交通時間一律經過 `calc_travel_minutes`；停車、門禁等到達時間由 `buffer_mins` 涵蓋。
- 硬性條件集中在 `_check_hard_constraints`；新增的不可派原因也要加到 `eligibility.CATEGORY_RULES` 與 `monthly_scheduler.failure_kind`。
- 系統只產生「建議」；已確認、待回覆、已接單、已通知案家的服務不可被自動換人。
- 個資：排班核心只用 ID；姓名只在顯示層遮罩後使用；稽核紀錄不可寫入姓名、地址、健康資料。
- Excel 欄位為繁體中文，保持原名；用 pandas 讀取，不要用終端機 cat。
