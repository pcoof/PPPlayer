FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PPPLAYER_USE_SYSTEM_PROXY=0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.web.txt /app/requirements.web.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.web.txt

# 只复制 Web/API 运行所需内容；不要导入 main.py（pywebview/winreg 等 Windows-only）。
COPY src /app/src
COPY static /app/static

EXPOSE 19527

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:19527/api/version', timeout=2)"

CMD ["python", "-c", "from src.server import create_app; create_app().run(host='0.0.0.0', port=19527, threaded=True, use_reloader=False)"]
