FROM python:3.11-slim

WORKDIR /app

COPY deploy/requirements.txt .
COPY deploy/wheels /tmp/wheels
RUN if find /tmp/wheels -name "*.whl" | grep -q .; then \
        pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

COPY qq_feishu_bridge.py .
COPY oss_helpers.py .
COPY services ./services
COPY utils ./utils

RUN mkdir -p /app/data /app/logs

CMD ["python", "-u", "qq_feishu_bridge.py"]
