# 使用官方 Python 映像檔
FROM python:3.10-slim

# 設定環境變數
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 1. 安裝 Chromium, Driver 與中文字型 (一次完成，減少 Layer)
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    fonts-wqy-microhei \
    fonts-wqy-zenhei \
    fontconfig \
    curl \
    --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 2. 更新字型快取
RUN fc-cache -fv

# 3. 設定環境變數與語系
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

# 設定工作目錄
WORKDIR /app

# 先安裝依賴，利用 Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製程式碼
COPY . .

CMD ["python", "main.py"]
