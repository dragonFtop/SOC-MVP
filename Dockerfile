# AI-SOC Python 服务镜像
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖清单并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 8000

# 默认启动：AI-SOC 完整分析流程
# 也可以覆盖此 CMD 单独启动服务：
#   docker run ... python -m uvicorn MVP.server.query_gateway:app --host 0.0.0.0 --port 8000
#   docker run ... streamlit run MVP/server/dashboard.py --server.port 8501
CMD ["python", "MVP/main.py"]