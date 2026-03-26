FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p data

# Expose the default port
EXPOSE 8000

# Environment defaults
ENV HOST=0.0.0.0
ENV PORT=8000
ENV DEMO_MODE=true

# Run the application
CMD ["python", "main.py"]
