FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY cloud-panel/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
RUN pip install --no-cache-dir ./common

COPY cloud-panel/cloud_panel ./cloud_panel
COPY cloud-panel/build.py ./build.py
RUN python build.py
RUN mkdir -p /app/data && chown nobody:nogroup /app/data

USER nobody

CMD ["python", "-m", "cloud_panel"]
