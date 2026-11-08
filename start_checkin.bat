@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 啟動 CareFlow 打卡網頁，這個視窗不要關。
where python 1>NUL 2>&1
if %errorlevel%==0 (
  python -m checkin.server %*
) else (
  py -m checkin.server %*
)
pause
