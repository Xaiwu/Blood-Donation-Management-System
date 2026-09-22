"""
Pruebas con pytest para simular un cliente y probar casos válidos y límite del sistema de reservas.
"""

import os
import sys
import uuid

import grpc
import pytest

# Asegurar importacion desde src/
ruta = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
sys.path.append(ruta)


import reservas_pb2 as pb
import reservas_pb2_grpc as pbg

@pytest.fixture(scope="module")
def stub():
    """Canal y stub gRPC reutilizable para las pruebas."""
    canal = grpc.insecure_channel("localhost:50051")
    return pbg.ReservaServiceStub(canal)


### PRUEBAS DE CONSULTA Y LISTADO


def test_consultar_tipo_valido(stub):
    """Verifica consulta exitosa de stock de un tipo valido."""
    req = pb.ConsultarTipoRequest(tipo_sangre=pb.TipoSangre.A_POSITIVO)
    resp = stub.ConsultarTipoSangre(req)
    assert resp.tipo_sangre == pb.TipoSangre.A_POSITIVO
    assert resp.unidades_disponibles >= 0

def test_consultar_tipo_desconocido_falla(stub):
    """Verifica que el enum 0 dispare INVALID_ARGUMENT."""
    req = pb.ConsultarTipoRequest(tipo_sangre=pb.TipoSangre.TIPO_SANGRE_DESCONOCIDO)
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ConsultarTipoSangre(req)
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    details = exc_info.value.details()
    assert details is not None
    assert "valido" in details.lower()

def test_listar_stock_completo(stub):
    """Verifica que ListarStock devuelva los 8 tipos de sangre del inventario."""
    req = pb.ListarStockRequest()
    resp = stub.ListarStock(req)
    assert len(resp.items) == 8


### PRUEBAS DE RESERVA Y VALIDACIONES


def test_reservar_unidades_exito(stub):
    """Valida camino exitoso de una reserva con descuento de stock usando ID único."""
    id_solicitud = f"TEST-REQ-{uuid.uuid4().hex[:8]}"
    inicial = stub.ConsultarTipoSangre(pb.ConsultarTipoRequest(tipo_sangre=pb.TipoSangre.B_POSITIVO)).unidades_disponibles

    req = pb.ReservarUnidadesRequest(
        id_solicitud=id_solicitud,
        tipo_sangre=pb.TipoSangre.B_POSITIVO,
        cantidad=1
    )
    resp = stub.ReservarUnidades(req)
    assert resp.id_solicitud == id_solicitud
    assert resp.unidades_reservadas == 1
    assert resp.unidades_restantes == inicial - 1

def test_reserva_idempotente_no_duplica_descuento(stub):
    """Verifica el patron Idempotent Receiver: reenviar el mismo id_solicitud no descuenta dos veces."""
    id_solicitud = f"TEST-IDEM-{uuid.uuid4().hex[:8]}"
    req = pb.ReservarUnidadesRequest(
        id_solicitud=id_solicitud,
        tipo_sangre=pb.TipoSangre.O_POSITIVO,
        cantidad=2
    )
    # Primera llamada: descuenta 2 unidades
    resp1 = stub.ReservarUnidades(req)

    # Segunda llamada reitento con mismo id_solicitud: debe devolver el mismo remanente sin descontar otra vez
    resp2 = stub.ReservarUnidades(req)
    assert resp2.unidades_restantes == resp1.unidades_restantes

def test_reservar_cantidad_invalida(stub):
    """Valida rechazo con INVALID_ARGUMENT ante cantidad menor o igual a cero."""
    req = pb.ReservarUnidadesRequest(
        id_solicitud=f"TEST-INV-{uuid.uuid4().hex[:8]}",
        tipo_sangre=pb.TipoSangre.O_POSITIVO,
        cantidad=0
    )
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ReservarUnidades(req)
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

def test_reservar_sin_id_solicitud(stub):
    """Valida rechazo cuando id_solicitud viaja en blanco."""
    req = pb.ReservarUnidadesRequest(
        id_solicitud="   ",
        tipo_sangre=pb.TipoSangre.O_POSITIVO,
        cantidad=1
    )
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ReservarUnidades(req)
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

def test_reservar_stock_insuficiente(stub):
    """Valida rechazo con FAILED_PRECONDITION si no hay stock suficiente."""
    req = pb.ReservarUnidadesRequest(
        id_solicitud=f"TEST-OVER-{uuid.uuid4().hex[:8]}",
        tipo_sangre=pb.TipoSangre.AB_NEGATIVO,
        cantidad=9999
    )
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ReservarUnidades(req)
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    details = exc_info.value.details()
    assert details is not None
    assert "insuficiente" in details.lower()


### PRUEBAS DE LIBERACION

def test_liberar_unidades_exito(stub):
    """Valida liberacion de unidades previamente reservadas en la misma prueba."""
    id_solicitud = f"TEST-LIB-{uuid.uuid4().hex[:8]}"

    # Reservar primero
    req_reserva = pb.ReservarUnidadesRequest(
        id_solicitud=id_solicitud,
        tipo_sangre=pb.TipoSangre.AB_POSITIVO,
        cantidad=1
    )
    stub.ReservarUnidades(req_reserva)

    # Liberar lo reservado
    req_liberar = pb.LiberarUnidadesRequest(
        id_solicitud=id_solicitud,
        tipo_sangre=pb.TipoSangre.AB_POSITIVO,
        cantidad=1
    )
    resp = stub.LiberarUnidades(req_liberar)
    assert resp.unidades_liberadas == 1

def test_liberar_mas_de_lo_reservado_falla(stub):
    """Valida que no se pueda liberar stock que no estaba reservado."""
    req = pb.LiberarUnidadesRequest(
        id_solicitud=f"TEST-EXC-{uuid.uuid4().hex[:8]}",
        tipo_sangre=pb.TipoSangre.O_NEGATIVO,
        cantidad=9999
    )
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.LiberarUnidades(req)
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
