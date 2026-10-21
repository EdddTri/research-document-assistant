FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
COPY research-backend /app/research-backend
RUN pip install --no-cache-dir -c /app/requirements.lock /app/research-backend
WORKDIR /app/research-backend
CMD ["uvicorn", "research_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
