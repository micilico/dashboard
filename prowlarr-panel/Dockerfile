FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY prowlarr-panel/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
RUN pip install --no-cache-dir ./common

COPY prowlarr-panel/prowlarr_panel ./prowlarr_panel
COPY prowlarr-panel/build.py ./build.py
RUN python build.py
RUN chown -R nobody:nogroup /app

USER nobody

CMD ["python", "-m", "prowlarr_panel"]
