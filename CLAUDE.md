# CLAUDE.md

給 Claude Code 在此 repo 工作時的說明。使用者文件以 README.md 為準。使用者是居督／主管，不是工程師：
回覆用繁體中文、白話，少用術語。

## 專案概要

CareFlow：居家長照（居服）機構的排班系統。讀取機構 Excel（範例 `居服機構資料_三工作表_v9.xlsx`：員工資料、個案資料、
週服務計畫、Service_code、案家姓名對照、居服員姓名對照），展開排班期間的每一次服務，在硬性條件（證照、性別、
不適合配對、請假、工時、連續上班、休息、轉場）下自動排班，再由居督確認、覆寫、處理臨時異動。
介面為 Streamlit（`app.py`，約 5,800 行），分 01～05 五個模組；另有給居服員用手機打卡的 Flask 小網站（`checkin/`）。

## 主要檔案

| 檔案 | 用途 |
|---|---|
| `app.py` | Streamlit 介面（01 匯入設定、02 排班、03 衝突與異動、04 結果覆核、05 分析）、登入關卡、側欄「復原上一步」與錯誤紀錄 |
| `caregiver_engine.py` | 核心：資料載入、`PipelineConfig`、硬性條件 `_check_hard_constraints`、改派檢查 `_evaluate_reassignment`、交通 `calc_travel_minutes`、路線與工資 |
| `monthly_scheduler.py`、`team_scheduler.py`、`fast_calendar.py` | 期間排班（週序列＋CP-SAT 聯合選人＋延展） |
| `agency_rules.py` | 月加班、連續上班天數、薪資表等機構規則（AI 排班硬擋、人工操作只提醒，共用同一判斷） |
| `substitute_rank.py` | 代班／臨時支援的共用排序 |
| `dispatch_workflow.py` | 派案狀態（建議→督導確認→待回覆→已接單→已通知案家）、請假、本機資料庫 |
| `plan_versions.py` | 帶生效日期的週計畫異動（停用／新增／改時間，寫回 Excel 的 到期日／生效日） |
| `worker_panel.py` | 以居服員為主軸的時段總覽（03「個人週班」下方） |
| `billing.py` | CSMS 申報格式匯出＋自付額試算（額度、部分負擔比例、喘息／自費）；欄位對照集中在 `CLAIM_COLUMNS` |
| `undo_stack.py` | 「復原上一步」：最多 10 步，快照 session 狀態與排班結果 |
| `change_log.py` | 04「異動紀錄」：把改派、臨時異動、整案調整、週計畫異動、資格追蹤、稽核合成一張表，依類型篩選 |
| `error_log.py` | `log_error(where, exc)`：寫入 `output_results/logs/careflow_errors.log` 並回傳錯誤代碼，側欄可看本次錯誤 |
| `auth.py` | 內建帳號（主管／居督）、PBKDF2 密碼、登入失敗鎖定；`CAREFLOW_AUTH=off` 可關閉 |
| `checkin/`（store、server、runner） | 居服員打卡：員編登入＋綁定手機、定位打卡、04 看板、QR、Cloudflare 臨時外網網址、示範工具 |
| `issue_center.py` | 衝突與異常分類 |
| `dashboard_metrics.py`、`dashboard_view.py`、`kpi_metrics.py` | 05 模組儀表板與試辦 KPI（定義見 PILOT_KPI.md） |
| `audit_log.py`、`data_retention.py` | 只能附加的稽核紀錄（雜湊鏈，記錄登入帳號）、保存期限刪除 |
| `workbook_edit.py` | 直接改 Excel XML（保留原格式）的寫回工具 |
| `tools/benchmark.py` | 基準測試（沙盒或 OSRM 交通） |
| `archive/` | 已套用的一次性修補腳本，不要執行 |
| `_to_delete/` | 清理時搬出的舊檔，確認沒用後由使用者手動刪除 |

## 資料存放（`output_results/`，不進 git）

`checkin.sqlite3`（打卡、裝置綁定）、`accounts.sqlite3`（帳號）、`dispatch_workflow.sqlite3`、稽核紀錄、`logs/`、`bin/`（cloudflared）。
測試用環境變數改路徑：`CAREFLOW_CHECKIN_DB`、`CAREFLOW_ACCOUNTS_DB`、`CAREFLOW_LOG_DIR`；`CAREFLOW_CHECKIN_AUTOSTART=0` 不自動開打卡主機。

## 文件

README.md（使用說明）、checkin/README.md（打卡）、VALIDATION.md、PILOT_KPI.md、PILOT_PLAN.md、DECISION_POLICY.md、DATA_GOVERNANCE.md。

## 執行

```bash
pip install -r requirements.txt
streamlit run app.py
python tools/benchmark.py 居服機構資料_三工作表_v9.xlsx --start 2026-11-01 --end 2026-11-30   # 沙盒交通，無網路可跑
python tools/benchmark.py --travel osrm   # 真實機車路網
python -m pytest -q tests/test_plan_versions.py tests/test_billing.py tests/test_undo.py tests/test_auth.py
```

所有測試都在 `tests/`（含舊的 test_v14～v18）。約 119 個舊測試早已失敗（test_agency_import、test_agency_rules、
test_caregiver_engine、test_team_scheduler、test_recurring_schedule、test_source_fields 等，用到已移除的參數或舊資料格式），
修改時以「不新增失敗」為準。Streamlit 介面測試用 `streamlit.testing.v1.AppTest`，須先設 `CAREFLOW_AUTH=off`。
效能修改後，用 benchmark 比對指標必須完全相同（派出 1,936／未派 9／總轉場 25,795.9 分，以 v9 範例 11 月為準）。

git：目前分支 main；只在使用者要求時才 commit。

## 修改時的規則

- 新的政策參數先加進 `PipelineConfig`，再接到介面；不要把常數寫死在 `app.py`。
- 交通時間一律經過 `calc_travel_minutes`；停車、門禁等到達時間由 `buffer_mins` 涵蓋。
- 硬性條件集中在 `_check_hard_constraints`；新增的不可派原因也要加到 `eligibility.CATEGORY_RULES` 與 `monthly_scheduler.failure_kind`。
- 系統只產生「建議」；已確認、待回覆、已接單、已通知案家的服務不可被自動換人。
- 介面分「居督模式」（預設）與「🛠️ 進階模式」（側欄開關 `ui_advanced`，變數 `_advanced`）。給工程師／主管的設定、明細、示範按鈕，一律只在進階模式顯示。
- 會改排班狀態的新操作，要讓 `undo_stack.STATE_KEYS`／`RES_KEYS` 涵蓋到，才能復原。
- 不要用裸的 `except Exception: pass`；吞掉錯誤時至少呼叫 `error_log.log_error`。
- 多人同時使用：每位使用者的狀態放 `st.session_state` 或 ContextVar，不要用模組層級可變全域變數。
- 個資：排班核心只用 ID；姓名只在顯示層遮罩後使用；稽核紀錄不可寫入姓名、地址、健康資料。
- Excel 欄位為繁體中文，保持原名；用 pandas 讀取，不要用終端機 cat。
