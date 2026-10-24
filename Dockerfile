# SmartBrew prototype - reproducible container (Python 3.12, pinned libraries).
# Build:  docker build -t smartbrew .
# Run:    docker run --rm -p 5001:5001 smartbrew      then open http://127.0.0.1:5001
# Test:   docker run --rm smartbrew python -m pytest -v
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

EXPOSE 5001
# Listen on all interfaces so the browser on the host can reach the container.
CMD ["python", "run.py", "--reset", "--host", "0.0.0.0", "--port", "5001"]
