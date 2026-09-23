FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/reservas.db

WORKDIR /app

# Requerimientos
COPY reservas-service/requirements.txt .
RUN pip install -r requirements.txt grpcio-tools

# Contrato
COPY contracts/reservas.proto ./contracts/
# Codigo
COPY reservas-service/src ./src

# Genera reservas_pb2.py y reservas_pb2_grpc.py desde el contrato
RUN python -m grpc_tools.protoc -I contracts \
    --python_out=src --grpc_python_out=src contracts/reservas.proto

# Usuario sin privilegios y carpeta para la base
RUN useradd --uid 1000 app && mkdir /data && chown app:app /data
USER app

WORKDIR /app/src
EXPOSE 50051

HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD python -c "import socket; socket.create_connection(('localhost', 50051), 2)"

CMD ["python", "server.py"]