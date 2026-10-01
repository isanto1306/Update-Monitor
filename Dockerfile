FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apk add --no-cache docker-cli docker-cli-buildx docker-cli-compose ca-certificates

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/main.py /app/main.py
COPY static/ /app/static/
# Keep the large production HTML untouched in Git. The card display-scaling
# controller is appended to the already loaded frontend bridge at image build.
RUN printf '\n' >> /app/static/update-channel.js \
    && cat /app/static/layout-options.js >> /app/static/update-channel.js \
    && python -c 'from pathlib import Path; p=Path("/app/static/index.html"); s=p.read_text(encoding="utf-8"); p.write_text(s.replace("/update-channel.js?v=0.3.382", "/update-channel.js?v=0.3.382-spinner1", 1), encoding="utf-8")'
RUN python -m py_compile /app/main.py
RUN mkdir -p /app/cache /app/backups

EXPOSE 9001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9001/api/auth/status', timeout=3).read()" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9001", "--no-server-header", "--no-access-log"]