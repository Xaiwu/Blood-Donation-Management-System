"""
Módulo del servidor gRPC para reservas-service.

Implementa la interfaz RPC definida en reservas.proto.

"""
import os
import signal
import sys
import threading
from concurrent import futures

import grpc

sys.path.append(os.path.dirname(__file__))

import db
import reservas_pb2 as pb
import reservas_pb2_grpc as pbg


class ReservaService(pbg.ReservaServiceServicer):
    """
    Servicio gRPC para la gestión de reservas de sangre.
    Esta clase proporciona métodos para consultar el stock de sangre y reservar unidades.
    """
    def ConsultarTipoSangre(self, request, context):
        """
        Consulta el stock de sangre para un tipo específico.

        Args:
            request (pb.ConsultarTipoRequest): La solicitud con el tipo de sangre a consultar.

        Returns:
            pb.ConsultarTipoResponse: La respuesta con el stock disponible para el tipo de sangre consultado.
        Raises:
            grpc.StatusCode.INVALID_ARGUMENT: Si el tipo de sangre no es válido.
            grpc.StatusCode.NOT_FOUND: Si el tipo de sangre consultado no está catalogado.
        """
        if request.tipo_sangre == pb.TipoSangre.TIPO_SANGRE_DESCONOCIDO:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Debe especificar un tipo de sangre valido")

        nombre_tipo = pb.TipoSangre.Name(request.tipo_sangre)
        stock = db.consultar_stock(nombre_tipo)

        if stock is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"Tipo de sangre {nombre_tipo} no catalogado")

        return pb.ConsultarTipoResponse(
            tipo_sangre=nombre_tipo,
            unidades_disponibles=stock
        )

    def ListarStock(self, request, context):
        """
        Listar el stock de todos los tipos de sangre.

        Returns:
            pb.ListarStockResponse: La respuesta con la lista de stock de todos los tipos de sangre.
        """
        filas = db.listar_todo()
        items = []
        for fila in filas:
            items.append(pb.StockSangre(
                tipo_sangre=fila["tipo_sangre"],
                unidades_disponibles=fila["unidades_disponibles"]
            ))

        return pb.ListarStockResponse(items=items)

    def ReservarUnidades(self, request, context):
        """
        Reserva unidades de sangre para una solicitud específica.

        Args:
            request (pb.ReservarUnidadesRequest): La solicitud con los detalles de tipo de sangre, cantidad e id de solicitud.

        Returns:
            pb.ReservarUnidadesResponse: La respuesta con los detalles de la reserva realizada.

        Raises:
            grpc.StatusCode.INVALID_ARGUMENT: Si el id_solicitud, cantidad o tipo de sangre no son válidos.
            grpc.StatusCode.FAILED_PRECONDITION: Si la cantidad a reservar es superior a las unidades disponibles.
        """

        if not request.id_solicitud.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "El id_solicitud es obligatorio")
        if request.cantidad <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "La cantidad debe ser mayor a 0")
        if request.tipo_sangre == pb.TipoSangre.TIPO_SANGRE_DESCONOCIDO:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Tipo de sangre no valido")

        nombre_tipo = ""
        try:
            nombre_tipo = pb.TipoSangre.Name(request.tipo_sangre)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Valor de enumerado no reconocido")
        exito, resultado = db.reservar_stock(request.id_solicitud, nombre_tipo, request.cantidad)

        if not exito or not isinstance(resultado, int):
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(resultado))

        assert isinstance(resultado, int)
        return pb.ReservarUnidadesResponse(
            id_solicitud=request.id_solicitud,
            tipo_sangre=nombre_tipo,
            unidades_reservadas=request.cantidad,
            unidades_restantes=resultado
        )

    def LiberarUnidades(self, request, context):
        """
        Libera unidades de sangre previamente reservadas.

        Args:
            request (pb.LiberarUnidadesRequest): La solicitud con los detalles de tipo de sangre, cantidad e id de solicitud.

        Returns:
            pb.LiberarUnidadesResponse: La respuesta con los detalles de la liberación realizada.

        Raises:
            grpc.StatusCode.INVALID_ARGUMENT: Si el id_solicitud, cantidad o tipo de sangre no son válidos.
            grpc.StatusCode.FAILED_PRECONDITION: Si la cantidad a liberar es superior a las unidades en reserva.
        """
        if not request.id_solicitud.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "El id_solicitud es obligatorio")
        if request.cantidad <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "La cantidad debe ser mayor a 0")
        if request.tipo_sangre == pb.TipoSangre.TIPO_SANGRE_DESCONOCIDO:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Tipo de sangre no valido")

        nombre_tipo = ""
        try:
            nombre_tipo = pb.TipoSangre.Name(request.tipo_sangre)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Valor de enumerado no reconocido")

        exito, resultado = db.liberar_stock(request.id_solicitud, nombre_tipo, request.cantidad)

        if not exito or not isinstance(resultado, int):
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(resultado))

        assert isinstance(resultado, int)
        return pb.LiberarUnidadesResponse(
            id_solicitud=request.id_solicitud,
            tipo_sangre=nombre_tipo,
            unidades_liberadas=request.cantidad,
            unidades_restantes=resultado
        )

def crear_server():
    """
    Inicializa y configura el servidor gRPC para la gestión de reservas de sangre.
    """
    db.inicializar_bd()
    servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pbg.add_ReservaServiceServicer_to_server(ReservaService(), servidor)
    servidor.add_insecure_port("[::]:50051")
    servidor.start()
    print("=====================================================")
    print("Servidor gRPC Reservas escuchando en [::]:50051")
    print("=====================================================")

    evento_apagado = threading.Event()

    def manejar_parada(signum, frame):
        print(f"\nSeñal {signum} recibida. Deteniendo servidor gRPC...")
        evento_apagado.set()

    signal.signal(signal.SIGTERM, manejar_parada)
    signal.signal(signal.SIGINT, manejar_parada)

    while not evento_apagado.wait(timeout=1):
        pass

    servidor.stop(grace=1).wait()
    print("Servidor detenido.")

if __name__ == "__main__":
    crear_server()
