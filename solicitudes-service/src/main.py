import logging
import os
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from functools import lru_cache
from uuid import UUID

import grpc
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

import database
from auth import autenticar, crear_token, requiere_escritura, verificar_token

# Importa los stubs
import reservas_pb2 as pb
import reservas_pb2_grpc as pbg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("solicitudes")

RESERVAS_ADDR = os.getenv("RESERVAS_ADDR", "localhost:50051")
# Mecanismo de resiliencia
RESERVAS_TIMEOUT_S = float(os.getenv("RESERVAS_TIMEOUT_S", "2.0"))
RETRY_AFTER_S = int(os.getenv("RETRY_AFTER_S", "5"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    database.inicializar_bd()
    yield

app = FastAPI(
    title="Sistema de donación de sangre (API EXTERNA)",
    version="1.0.0",
    docs_url="/v1/docs",
    openapi_url="/v1/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)
router = APIRouter(prefix="/v1")

#QUIZAS PASAR A OTRO ARCHIVO LAS ESTRUCTURAS PARA MANTENER UN CODIGO MÁS ORDENADO
class TipoSangre(str, Enum):
    A_POSITIVO = "A_POSITIVO"
    A_NEGATIVO = "A_NEGATIVO"
    B_POSITIVO = "B_POSITIVO"
    B_NEGATIVO = "B_NEGATIVO"
    AB_POSITIVO = "AB_POSITIVO"
    AB_NEGATIVO = "AB_NEGATIVO"
    O_POSITIVO = "O_POSITIVO"
    O_NEGATIVO = "O_NEGATIVO"

class HospitalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(..., min_length=1, max_length=120)
    direccion: str = Field(..., min_length=1, max_length=200)

class SolicitudInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_hospital: UUID
    tipo_sangre: TipoSangre
    cantidad: int = Field(..., ge=1, le=50)

class CredencialesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usuario: str
    clave: str

class HospitalOut(BaseModel):
    id: str
    nombre: str
    direccion: str

class SolicitudOut(BaseModel):
    id_solicitud: str
    id_hospital: str
    tipo_sangre: str
    cantidad: int
    estado: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

# PARA IDENTIFICAR ERRORES
def _codigo_por_estado(status_code: int) -> str:
    if status_code == 401:
        return "NO_AUTENTICADO"
    if status_code == 403:
        return "PROHIBIDO"
    if status_code == 404:
        return "NO_ENCONTRADO"
    if status_code >= 500:
        return "ERROR_INTERNO"
    return "VALIDACION"

@app.exception_handler(StarletteHTTPException)
async def manejar_http(request, exc: StarletteHTTPException):
    #ERROR + MENSAJE
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        cuerpo = exc.detail
    else:
        cuerpo = {"error": _codigo_por_estado(exc.status_code), "mensaje": str(exc.detail)}
    return JSONResponse(cuerpo, status_code=exc.status_code, headers=getattr(exc, "headers", None))

@app.exception_handler(RequestValidationError)
async def manejar_validacion(request, exc: RequestValidationError):
    errores = exc.errors()
    if errores and errores[0].get("type") == "json_invalid":
        mensaje = "El cuerpo no es un JSON válido"
    elif errores:
        primero = errores[0]
        campo = ".".join(str(p) for p in primero.get("loc", ()) if p != "body")
        mensaje = f"Campo '{campo}': {primero.get('msg')}" if campo else str(primero.get("msg"))
    else:
        mensaje = "Solicitud inválida"
    return JSONResponse({"error": "VALIDACION", "mensaje": mensaje}, status_code=400)

@app.exception_handler(Exception)
async def manejar_error_interno(request, exc: Exception):
    logger.exception("Error no controlado")
    return JSONResponse(
        {"error": "ERROR_INTERNO", "mensaje": "Error interno del servidor"}, status_code=500
    )

# Endpoints de autenticación
@router.post("/auth/token", response_model=TokenOut, tags=["Autenticación"])
def obtener_token(credenciales: CredencialesInput):
    rol = autenticar(credenciales.usuario, credenciales.clave)
    if rol is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "NO_AUTENTICADO", "mensaje": "Usuario o clave incorrectos"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return crear_token(credenciales.usuario, rol)

# Endpoints de hospitales, listar, crear y consultar
@router.get("/hospitales", response_model=list[HospitalOut], tags=["Hospitales"],
            dependencies=[Depends(verificar_token)])
def listar_hospitales():
    return database.listar_hospitales()

@router.post("/hospitales", status_code=status.HTTP_201_CREATED, response_model=HospitalOut,
             tags=["Hospitales"], dependencies=[Depends(requiere_escritura)])
def registrar_hospital(hospital: HospitalInput, response: Response):
    nuevo = database.crear_hospital(hospital.nombre, hospital.direccion)
    response.headers["Location"] = f"/v1/hospitales/{nuevo['id']}"
    return nuevo

@router.get("/hospitales/{id}", response_model=HospitalOut, tags=["Hospitales"],
            dependencies=[Depends(verificar_token)])
def consultar_hospital(id: str):
    hosp = database.obtener_hospital(id)
    if not hosp:
        raise HTTPException(status_code=404, detail={"error": "NO_ENCONTRADO", "mensaje": "Hospital no existe"})
    return hosp

#Endpoints hacia grpc
@lru_cache(maxsize=1)
def get_grpc_stub():
    canal = grpc.insecure_channel(RESERVAS_ADDR)
    return pbg.ReservaServiceStub(canal)

#Para transformar el error al formato de la api
def _error_desde_rpc(e: grpc.RpcError, operacion: str) -> HTTPException:
    if e.code() in (grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNAVAILABLE):
        # Si el sistema de reservas esta caido o no responde en el límite no cambia nada
        logger.warning("Reservas no disponible al %s: %s", operacion, e.code().name)
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "RESERVAS_NO_DISPONIBLE", "mensaje": "El servicio de Reservas no respondió a tiempo"},
            headers={"Retry-After": str(RETRY_AFTER_S)},
        )
    logger.error("Error gRPC inesperado al %s: %s %s", operacion, e.code(), e.details())
    return HTTPException(
        status_code=500,
        detail={"error": "ERROR_INTERNO", "mensaje": f"Error inesperado al comunicarse con Reservas ({operacion})"},
    )

#Endpoints de solicitudes
@router.get("/solicitudes", response_model=list[SolicitudOut], tags=["Solicitudes"],
            dependencies=[Depends(verificar_token)])
def listar_solicitudes():
    return database.listar_solicitudes()

@router.post("/solicitudes", status_code=status.HTTP_201_CREATED, response_model=SolicitudOut,
             tags=["Solicitudes"], dependencies=[Depends(requiere_escritura)])
def registrar_solicitud(solicitud: SolicitudInput, response: Response):
    id_hospital = str(solicitud.id_hospital)
    #Busca si el hospital está en la base de datos
    if not database.obtener_hospital(id_hospital):
        raise HTTPException(
            status_code=422,
            detail={"error": "HOSPITAL_INEXISTENTE", "mensaje": "El hospital indicado no existe"}
        )

    tipo_enum = pb.TipoSangre.Value(solicitud.tipo_sangre.value)
    id_nueva_solicitud = str(uuid.uuid4())
    stub = get_grpc_stub()

    try:
        # llamada al grpc con timeout incluido
        stub.ReservarUnidades(
            pb.ReservarUnidadesRequest(
                id_solicitud=id_nueva_solicitud,
                tipo_sangre=tipo_enum,
                cantidad=solicitud.cantidad
            ),
            timeout=RESERVAS_TIMEOUT_S
        )
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.FAILED_PRECONDITION:
            raise HTTPException(
                status_code=409,
                detail={"error": "STOCK_INSUFICIENTE",
                        "mensaje": e.details() or "No hay unidades suficientes del tipo de sangre pedido"}
            )
        raise _error_desde_rpc(e, "reservar")

    # Si grpc funciona se guarda en la base de datos del sistema de solicitudes
    try:
        nueva_solicitud = database.crear_solicitud(
            id_solicitud=id_nueva_solicitud,
            id_hospital=id_hospital,
            tipo_sangre=solicitud.tipo_sangre.value,
            cantidad=solicitud.cantidad
        )
    except Exception:
        # Si las unidades no se guardaron se liberan para cuidar y mantener la consistencia de la base de datos de reservas
        logger.exception("Falló el guardado de la solicitud %s tras reservar", id_nueva_solicitud)
        try:
            stub.LiberarUnidades(
                pb.LiberarUnidadesRequest(
                    id_solicitud=id_nueva_solicitud,
                    tipo_sangre=tipo_enum,
                    cantidad=solicitud.cantidad
                ),
                timeout=RESERVAS_TIMEOUT_S
            )
        # Peor caso se debe resolver manualmente
        except grpc.RpcError:
            logger.error("No se pudo compensar la reserva %s: requiere reconciliación manual", id_nueva_solicitud)
        raise HTTPException(
            status_code=500,
            detail={"error": "ERROR_INTERNO", "mensaje": "No se pudo registrar la solicitud"}
        )

    response.headers["Location"] = f"/v1/solicitudes/{id_nueva_solicitud}"
    return nueva_solicitud

@router.get("/solicitudes/{id}", response_model=SolicitudOut, tags=["Solicitudes"],
            dependencies=[Depends(verificar_token)])
def consultar_solicitud(id: str):
    sol = database.obtener_solicitud(id)
    if not sol:
        raise HTTPException(status_code=404, detail={"error": "NO_ENCONTRADO", "mensaje": "Solicitud no existe"})
    return sol

@router.post("/solicitudes/{id}/revertir", response_model=SolicitudOut, tags=["Solicitudes"],
             dependencies=[Depends(requiere_escritura)])
def revertir_solicitud(id: str):
    solicitud = database.obtener_solicitud(id)
    if not solicitud:
        raise HTTPException(status_code=404, detail={"error": "NO_ENCONTRADO", "mensaje": "Solicitud no encontrada"})

    ya_revertida = HTTPException(
        status_code=409,
        detail={"error": "SOLICITUD_YA_REVERTIDA", "mensaje": "La solicitud ya fue revertida"}
    )
    if solicitud["estado"] == "REVERTIDA":
        raise ya_revertida

    if not database.revertir_estado_solicitud(id):
        raise ya_revertida

    tipo_enum = pb.TipoSangre.Value(solicitud["tipo_sangre"])
    stub = get_grpc_stub()

    try:
        stub.LiberarUnidades(
            pb.LiberarUnidadesRequest(
                id_solicitud=id,
                tipo_sangre=tipo_enum,
                cantidad=solicitud["cantidad"]
            ),
            timeout=RESERVAS_TIMEOUT_S
        )
    except grpc.RpcError as e:
        #La solicitud vuelve a activa si no es posible liberarla
        database.restaurar_estado_activa(id)
        raise _error_desde_rpc(e, "liberar")

    return database.obtener_solicitud(id)


@router.get("/health", tags=["Operación"])
def health():
    return {"estado": "ok", "servicio": "solicitudes", "cache": "deshabilitada"}

app.include_router(router)