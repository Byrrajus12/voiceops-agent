FROM python:3.11-slim

WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["adk", "web", "--port", "8080", "--host", "0.0.0.0", "agent"]
