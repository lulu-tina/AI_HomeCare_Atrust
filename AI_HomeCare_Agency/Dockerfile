# CareFlow：單一容器。注意：本資料夾需含 osrm_client.py（原有檔案，未隨本包重發）。
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# 排班工作紀錄（sqlite）放在 /app/output_results，請用 volume 保存
VOLUME ["/app/output_results"]
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
