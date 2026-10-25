"""API real con base de datos temporal y stub gRPC simulado."""
import os
import sys
import tempfile

# Configuración del enviroment para usar la api
os.environ.setdefault("JWT_SECRET", "contraseña-super-secreta-top-secret-omg-brijidooo")
os.environ.setdefault("USUARIO_LECTURA", "lector")
os.environ.setdefault("CLAVE_LECTURA", "lector123")
os.environ.setdefault("USUARIO_ESCRITURA", "operador")
os.environ.setdefault("CLAVE_ESCRITURA", "operador123")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "solicitudes_test.db")
os.environ["RECONCILIACION_GRACIA_S"] = "0"
os.environ["RECONCILIACION_INTERVALO_S"] = "3600"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import grpc
import pytest
from fastapi.testclient import TestClient

import database
import main


class ErrorRpc(grpc.RpcError):
    """RpcError controlable para simular las respuestas de Reservas."""

    def __init__(self, codigo, detalle=""):
        super().__init__()
        self._codigo, self._detalle = codigo, detalle

    def code(self):
        return self._codigo

    def details(self):
        return self._detalle


class StubFalso:
    """Registra las llamadas y permite programar una excepción por método."""

    def __init__(self):
        self.llamadas = []
        self.errores = {}

    def _llamar(self, metodo, req):
        self.llamadas.append((metodo, req))
        error = self.errores.get(metodo)
        if error is not None:
            raise error
        return object()

    def ReservarUnidades(self, req, timeout=None):
        return self._llamar("ReservarUnidades", req)

    def LiberarUnidades(self, req, timeout=None):
        return self._llamar("LiberarUnidades", req)

    def ListarStock(self, req, timeout=None):
        return self._llamar("ListarStock", req)

    def veces(self, metodo):
        return sum(1 for m, _ in self.llamadas if m == metodo)


@pytest.fixture
def stub(monkeypatch):
    falso = StubFalso()
    monkeypatch.setattr(main, "get_grpc_stub", lambda: falso)
    return falso


@pytest.fixture
def cliente(stub):
    # Un cliente por prueba, con BD vacía
    if os.path.exists(database.DB_PATH):
        os.remove(database.DB_PATH)
    with TestClient(main.app) as c:
        yield c

#Token de usuarios
def _token(cliente, usuario, clave):
    r = cliente.post("/v1/auth/token", json={"usuario": usuario, "clave": clave})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}

#Usuario con permisos completos
@pytest.fixture
def escritor(cliente):
    return _token(cliente, "operador", "operador123")

#Usuario con permisos de lector
@pytest.fixture
def lector(cliente):
    return _token(cliente, "lector", "lector123")

#Un hospital
@pytest.fixture
def hospital(cliente, escritor):
    r = cliente.post("/v1/hospitales", json={"nombre": "H. Regional", "direccion": "Av. 1"}, headers=escritor)
    return r.json()["id"]
