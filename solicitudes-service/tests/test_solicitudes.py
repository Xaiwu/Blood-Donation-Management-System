"""
Pruebas de solicitudes-service: autenticación, flujo de solicitudes y consistencia
frente a fallas de Reservas (reservas huérfanas, compensaciones y reversión).
"""

import grpc
import pytest

import database
import main
from conftest import ErrorRpc

# Cuando se pasa el timeout, o no esta disponible el servicio de reservas
ESTADOS_AMBIGUOS = [grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNAVAILABLE]

# Define una solicitud de sangre
def _pedir(cliente, escritor, hospital, cantidad=2, tipo="O_POSITIVO"):
    return cliente.post(
        "/v1/solicitudes",
        json={"id_hospital": hospital, "tipo_sangre": tipo, "cantidad": cantidad},
        headers=escritor,
    )


def _fallar_guardado_local(monkeypatch):
    def _falla(**kwargs):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(database, "crear_solicitud", _falla)


# Tests de autenticación

def test_sin_token_401(cliente):
    r = cliente.get("/v1/solicitudes")
    assert r.status_code == 401 and r.json()["error"] == "NO_AUTENTICADO"


def test_lector_no_puede_escribir_403(cliente, lector, hospital):
    r = cliente.post("/v1/solicitudes", json={"id_hospital": hospital, "tipo_sangre": "O_POSITIVO", "cantidad": 1},
                     headers=lector)
    assert r.status_code == 403 and r.json()["error"] == "PROHIBIDO"


# flujo bueno y errores "normales"

def test_crear_solicitud_201(cliente, escritor, hospital, stub):
    r = _pedir(cliente, escritor, hospital)
    assert r.status_code == 201 and r.json()["estado"] == "ACTIVA"
    assert stub.veces("ReservarUnidades") == 1


def test_hospital_inexistente_422_sin_llamar_a_reservas(cliente, escritor, stub):
    r = _pedir(cliente, escritor, "7c9e6679-7425-40de-944b-e07fc1f90ae7")
    assert r.status_code == 422 and r.json()["error"] == "HOSPITAL_INEXISTENTE"
    assert stub.veces("ReservarUnidades") == 0


def test_stock_insuficiente_409(cliente, escritor, hospital, stub):
    stub.errores["ReservarUnidades"] = ErrorRpc(grpc.StatusCode.FAILED_PRECONDITION, "Stock insuficiente")
    r = _pedir(cliente, escritor, hospital)
    assert r.status_code == 409 and r.json()["error"] == "STOCK_INSUFICIENTE"
    assert database.contar_compensaciones_pendientes() == 0


def test_validacion_400(cliente, escritor, hospital):
    r = _pedir(cliente, escritor, hospital, cantidad=0)
    assert r.status_code == 400 and r.json()["error"] == "VALIDACION"


# Reservas huerfanas
# Estas pruebas cubren el caso en que Reservas acepta la petición de reserva pero
# responde con un fallo ambiguo (timeout o indisponibilidad). La lógica del servicio
# debe evitar dejar el stock bloqueado o una solicitud parcialmente creada y, en
# cambio, registrar una compensación para poder reintentar la liberación más tarde.

@pytest.mark.parametrize("codigo", ESTADOS_AMBIGUOS)
def test_timeout_al_reservar_deja_compensacion_pendiente(cliente, escritor, hospital, stub, codigo):
    #Si Reservas no confirma la solicitud queda rechazada y se deja una compensación pendiente
    stub.errores["ReservarUnidades"] = ErrorRpc(codigo)
    r = _pedir(cliente, escritor, hospital)
    assert r.status_code == 503 and r.json()["error"] == "RESERVAS_NO_DISPONIBLE"
    assert r.headers["Retry-After"] == "5"
    assert database.contar_compensaciones_pendientes() == 1
    assert database.listar_solicitudes() == []


def test_reconciliador_libera_la_reserva_huerfana(cliente, escritor, hospital, stub):
    #El reconciliador debe liberar la reserva huérfana cuando ya existe una compensación vencida.
    stub.errores["ReservarUnidades"] = ErrorRpc(grpc.StatusCode.DEADLINE_EXCEEDED)
    _pedir(cliente, escritor, hospital, cantidad=3, tipo="A_NEGATIVO")
    comp = database.listar_compensaciones_vencidas(0)[0]

    assert main.reconciliar_compensaciones() == 1

    metodo, req = stub.llamadas[-1]
    assert metodo == "LiberarUnidades"
    assert req.id_solicitud == comp["id_solicitud"] and req.cantidad == 3
    assert database.contar_compensaciones_pendientes() == 0


def test_reconciliador_descarta_si_reservas_no_tiene_la_reserva(cliente, escritor, hospital, stub):
    #Si la reserva nunca se confirmó el liberar en Reservas responde FAILED_PRECONDITION y no hay nada que liberar
    stub.errores["ReservarUnidades"] = ErrorRpc(grpc.StatusCode.UNAVAILABLE)
    _pedir(cliente, escritor, hospital)
    stub.errores["LiberarUnidades"] = ErrorRpc(grpc.StatusCode.FAILED_PRECONDITION, "No existe una reserva")
    assert main.reconciliar_compensaciones() == 1
    assert database.contar_compensaciones_pendientes() == 0


def test_reconciliador_conserva_la_compensacion_si_reservas_sigue_caido(cliente, escritor, hospital, stub):
    #Si Reservas sigue caído al intentar liberar la compensación debe mantenerse pendiente para reintentar más tarde
    stub.errores["ReservarUnidades"] = ErrorRpc(grpc.StatusCode.UNAVAILABLE)
    _pedir(cliente, escritor, hospital)
    stub.errores["LiberarUnidades"] = ErrorRpc(grpc.StatusCode.UNAVAILABLE)
    assert main.reconciliar_compensaciones() == 0
    comp = database.listar_compensaciones_vencidas(0)[0]
    assert comp["intentos"] == 1


def test_reconciliador_respeta_el_periodo_de_gracia(cliente, escritor, hospital, stub, monkeypatch):
    #El reconciliador respeta el tiempo de reintentar
    stub.errores["ReservarUnidades"] = ErrorRpc(grpc.StatusCode.DEADLINE_EXCEEDED)
    _pedir(cliente, escritor, hospital)
    monkeypatch.setattr(main, "RECONCILIACION_GRACIA_S", 3600)
    assert main.reconciliar_compensaciones() == 0
    assert stub.veces("LiberarUnidades") == 0
    assert database.contar_compensaciones_pendientes() == 1


# Compensación si falla el guardado local 
# Estas pruebas validan que el servicio mantiene la consistencia cuando la operación de
# persistencia local falla justo después de reservar en Reservas. En ese caso, se debe
# compensar haciendo la liberación correspondiente para no dejar unidades retenidas.

def test_fallo_local_compensa_liberando(cliente, escritor, hospital, stub, monkeypatch):
    #Si el guardado local falla la operación debe responder 500 y liberar la reserva para compensar el error
    _fallar_guardado_local(monkeypatch)
    r = _pedir(cliente, escritor, hospital)
    assert r.status_code == 500
    assert stub.veces("LiberarUnidades") == 1
    assert database.contar_compensaciones_pendientes() == 0


def test_fallo_local_y_compensacion_fallida_queda_pendiente(cliente, escritor, hospital, stub, monkeypatch):
    #Si la compensación falla la operación queda pendiente para reintentarse 
    _fallar_guardado_local(monkeypatch)
    stub.errores["LiberarUnidades"] = ErrorRpc(grpc.StatusCode.UNAVAILABLE)
    r = _pedir(cliente, escritor, hospital)
    assert r.status_code == 500
    assert database.contar_compensaciones_pendientes() == 1


# Reversión

def test_revertir_ok(cliente, escritor, hospital, stub):
    #Una reversión exitosa cambia el estado de la solicitud y libera la reserva tomada
    id_sol = _pedir(cliente, escritor, hospital).json()["id_solicitud"]
    r = cliente.post(f"/v1/solicitudes/{id_sol}/revertir", headers=escritor)
    assert r.status_code == 200 and r.json()["estado"] == "REVERTIDA"
    assert stub.veces("LiberarUnidades") == 1


def test_revertir_dos_veces_409_sin_liberar_otra_vez(cliente, escritor, hospital, stub):
    #Reintentar la reversión sobre una solicitud ya revertida debe ser rechazado con 409
    id_sol = _pedir(cliente, escritor, hospital).json()["id_solicitud"]
    cliente.post(f"/v1/solicitudes/{id_sol}/revertir", headers=escritor)
    r = cliente.post(f"/v1/solicitudes/{id_sol}/revertir", headers=escritor)
    assert r.status_code == 409 and r.json()["error"] == "SOLICITUD_YA_REVERTIDA"
    assert stub.veces("LiberarUnidades") == 1


def test_revertir_con_reservas_caido_restaura_activa_y_el_reintento_converge(cliente, escritor, hospital, stub):
    #Si la liberación falla temporalmente la solicitud vuelve a ACTIVA y el siguiente intento corrige
    id_sol = _pedir(cliente, escritor, hospital).json()["id_solicitud"]
    stub.errores["LiberarUnidades"] = ErrorRpc(grpc.StatusCode.DEADLINE_EXCEEDED)
    r = cliente.post(f"/v1/solicitudes/{id_sol}/revertir", headers=escritor)
    assert r.status_code == 503
    assert database.obtener_solicitud(id_sol)["estado"] == "ACTIVA"

    del stub.errores["LiberarUnidades"]
    r = cliente.post(f"/v1/solicitudes/{id_sol}/revertir", headers=escritor)
    assert r.status_code == 200 and r.json()["estado"] == "REVERTIDA"


def test_arranque_completa_reversiones_a_medias(cliente, escritor, hospital, stub):
    #Una solicitud revertida donde la liberación no se confirma se reenvía al iniciar
    id_sol = _pedir(cliente, escritor, hospital).json()["id_solicitud"]
    database.revertir_estado_solicitud(id_sol)  # simula la caída 
    main.reconciliar_revertidas()
    metodo, req = stub.llamadas[-1]
    assert metodo == "LiberarUnidades" and req.id_solicitud == id_sol


# Tests de health

def test_health_ok(cliente, stub):
    r = cliente.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok" and r.json()["reservas"] == "disponible"


def test_health_degradado_si_reservas_cae(cliente, stub):
    #Con Reservas caído ya no se declara "ok"
    stub.errores["ListarStock"] = ErrorRpc(grpc.StatusCode.UNAVAILABLE)
    r = cliente.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "degradado" and r.json()["reservas"] == "no_disponible"


def test_health_live_no_consulta_reservas(cliente, stub):
    #Liveness: responde aunque Reservas esté caído y sin llamarlo
    stub.errores["ListarStock"] = ErrorRpc(grpc.StatusCode.UNAVAILABLE)
    r = cliente.get("/v1/health/live")
    assert r.status_code == 200 and r.json()["estado"] == "vivo"
    assert stub.veces("ListarStock") == 0


# Revisa si el servicio de reservas esta disponible
def test_health_ready_ok(cliente, stub):
    r = cliente.get("/v1/health/ready")
    assert r.status_code == 200 and r.json()["reservas"] == "disponible"


def test_health_si_reservas_cae(cliente, stub):
    #Si Reservas falla al verificar stock responde 503
    stub.errores["ListarStock"] = ErrorRpc(grpc.StatusCode.UNAVAILABLE)
    r = cliente.get("/v1/health/ready")
    assert r.status_code == 503 and r.json()["error"] == "RESERVAS_NO_DISPONIBLE"
    assert "Retry-After" in r.headers


def test_health_ready_error_inesperado_es_503(cliente, stub):
    #Un error distinto de timeout/caída también deja al servicio no disponible
    stub.errores["ListarStock"] = ErrorRpc(grpc.StatusCode.INTERNAL)
    r = cliente.get("/v1/health/ready")
    assert r.status_code == 503 and r.json()["error"] == "RESERVAS_NO_DISPONIBLE"
