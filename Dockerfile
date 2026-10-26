FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=7860

# Install system dependencies: FFmpeg with libass support, OpenCV dependencies, fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    fonts-noto-core \
    fonts-noto-cjk \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (UID 1000 is standard for Hugging Face Spaces)
RUN useradd -m -u 1000 user

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Give full read/write permissions to user for app directories and cache
RUN mkdir -p work outputs fonts .cache && \
    chown -R user:user /app /home/user && \
    chmod -R 777 /app /home/user

# Switch to non-root user
USER user
ENV HOME=/home/user

EXPOSE 7860

# Launch FastAPI app
CMD ["sh", "-c", "python -m uvicorn app.api.server:app --host 0.0.0.0 --port ${PORT:-7860}"]
