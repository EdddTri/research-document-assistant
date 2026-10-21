FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
COPY research-backend /app/research-backend
COPY research-agent /app/research-agent
RUN pip install --no-cache-dir -c /app/requirements.lock /app/research-backend /app/research-agent
WORKDIR /app/research-agent
CMD ["uvicorn", "research_agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
