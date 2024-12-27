# 使用輕量級的 Python 基礎映像
FROM python:3.9-slim

# 設定工作目錄
WORKDIR /app

# 複製專案中的所有檔案到容器內
COPY . .

# 安裝必要的套件
RUN pip install --no-cache-dir -r requirements.txt

# 暴露 Cloud Run 預設埠
EXPOSE 8080

# 啟動 Flask 應用
CMD ["python", "app.py"]
