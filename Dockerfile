FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY main.py ./
COPY scripts/ ./scripts/

USER 10001:10001
CMD ["python", "main.py"]
