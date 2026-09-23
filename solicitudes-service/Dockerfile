FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/solicitudes.db \
    RESERVAS_ADDR=reservas:50051

WORKDIR /app

# Requerimientos
COPY solicitudes-service/requirements.txt .
RUN pip install -r requirements.txt grpcio-tools

# Contrato
COPY contracts/reservas.proto ./contracts/
# Codigo
COPY solicitudes-service/src ./src

# Solicitudes es cliente gRPC: necesita los mismos stubs generados del contrato
RUN python -m grpc_tools.protoc -I contracts \
    --python_out=src --grpc_python_out=src contracts/reservas.proto

RUN useradd --uid 1000 app && mkdir /data && chown app:app /data
USER app

WORKDIR /app/src
EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/v1/health', timeout=2)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]