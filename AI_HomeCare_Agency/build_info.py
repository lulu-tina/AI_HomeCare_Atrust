"""版本標記：app.py 啟動時會檢查下列檔案是否都是同一版，避免只覆蓋部分檔案造成行為不一致。"""
CAREFLOW_BUILD = '2026.10.11-v18'
REQUIRED_MODULES = ('caregiver_engine', 'monthly_scheduler', 'agency_rules', 'schedule_output', 'calendar_view',
                    'dispatch_workflow', 'careflow_ui', 'dashboard_view', 'dashboard_metrics', 'schedule_checks',
                    'workbook_edit', 'repair_workbook', 'address_geocoder', 'dispatch_message',
                    'temp_case', 'eligibility', 'pending_clients', 'pending_advice', 'analysis_bg')
