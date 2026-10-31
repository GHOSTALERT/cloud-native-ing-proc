# One image for every role; the command picks api / worker / autoscaler.
# Run scripts/fetch_models.sh first: the ONNX weights are baked into the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MODEL_DIR=/app/models
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY models ./models
COPY imgframe ./imgframe
COPY api ./api
COPY worker ./worker
COPY autoscaler ./autoscaler

RUN useradd --uid 10001 --no-create-home app
USER 10001

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
