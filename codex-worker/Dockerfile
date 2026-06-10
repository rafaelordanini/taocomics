FROM node:22-slim

RUN apt-get update && apt-get install -y python3 python3-pip curl && rm -rf /var/lib/apt/lists/*

# Install Codex CLI
RUN npm install -g @openai/codex

# Install Python worker dependencies
RUN pip3 install --no-cache-dir --break-system-packages fastapi uvicorn

WORKDIR /worker
COPY server.py .

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
