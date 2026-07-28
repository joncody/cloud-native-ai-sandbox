# Docker build file for the Python AI agent microservice
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

EXPOSE 5000

CMD ["python", "agent.py"]
