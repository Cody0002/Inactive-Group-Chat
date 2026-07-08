# ---- Base image: slim Python to keep it small ----
FROM python:3.11-slim

# Don't write .pyc files, flush logs immediately
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory inside the container
WORKDIR /app

# Install dependencies first (Docker caches this layer if requirements don't change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Create the logs directory
RUN mkdir -p /app/logs

# The bot listens on port 8000
EXPOSE 8000

# Start the bot
CMD ["python", "main.py"]
