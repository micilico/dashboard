FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY prowlarr_panel ./prowlarr_panel

USER nobody

CMD ["python", "-m", "prowlarr_panel"]
