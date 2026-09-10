FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用层缓存）
COPY pyproject.toml ./
COPY mini_llm_gateway ./mini_llm_gateway
RUN pip install --no-cache-dir .

# 运行时资产
COPY config ./config
COPY prompts ./prompts

# 非 root 运行
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

# 用 --factory：模块级不构造 app，避免导入期读取配置/建连接
CMD ["uvicorn", "mini_llm_gateway.app:build_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
