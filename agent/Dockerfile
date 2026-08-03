# Container build definition for the Python AI Agent microservice
FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Copy dependency definition first to leverage Docker layer caching
COPY requirements.txt .

# Install Python packages without caching installation files
RUN pip install --no-cache-dir -r requirements.txt

# Copy microservice application source code
COPY agent.py .

# Expose microservice REST and metrics port
EXPOSE 5000

# Specify command to start Flask application
CMD ["python", "agent.py"]
