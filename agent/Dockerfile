FROM python:3.11-slim

# Node.js required to run Dynatrace MCP server via npx
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Pre-install the Dynatrace MCP server so npx doesn't cold-start on first request
RUN npm install -g @dynatrace-oss/dynatrace-mcp-server@latest

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["adk", "web", "--port", "8080", "--host", "0.0.0.0", "agent"]
