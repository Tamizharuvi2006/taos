FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

ENV PYTHONPATH=/app/..
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "taos.apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
