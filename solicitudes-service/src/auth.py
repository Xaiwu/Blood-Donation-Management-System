import hmac
import logging
import os
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("solicitudes.auth")

JWT_SECRET = os.getenv("JWT_SECRET")

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET no está configurado")

JWT_ALGORITMO = "HS256"
JWT_EXPIRA_SEGUNDOS = int(os.getenv("JWT_EXPIRA_SEGUNDOS", "3600"))

ROL_LECTURA = "lectura"
ROL_ESCRITURA = "escritura"
ROLES_VALIDOS = (ROL_LECTURA, ROL_ESCRITURA)

USUARIO_LECTURA = os.getenv("USUARIO_LECTURA")
CLAVE_LECTURA = os.getenv("CLAVE_LECTURA")

USUARIO_ESCRITURA = os.getenv("USUARIO_ESCRITURA")
CLAVE_ESCRITURA = os.getenv("CLAVE_ESCRITURA")

if not all([USUARIO_LECTURA, CLAVE_LECTURA, USUARIO_ESCRITURA, CLAVE_ESCRITURA]):
    raise RuntimeError("Las credenciales de usuario no están configuradas")

USUARIOS = {
    USUARIO_LECTURA: (CLAVE_LECTURA, ROL_LECTURA),
    USUARIO_ESCRITURA: (CLAVE_ESCRITURA, ROL_ESCRITURA),
}

_esquema_bearer = HTTPBearer(auto_error=False)

# ERROR 401
def _no_autenticado(mensaje: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": "NO_AUTENTICADO", "mensaje": mensaje},
        headers={"WWW-Authenticate": "Bearer"},
    )

def autenticar(usuario: str, clave: str) -> str | None:
    registro = USUARIOS.get(usuario)
    clave_esperada, rol = registro if registro else ("", None)
    # Buena practica de compáracion para evitar ataques
    clave_ok = hmac.compare_digest(clave.encode("utf-8"), clave_esperada.encode("utf-8"))
    # Devuelve el rol si las credenciales estan bien
    return rol if (registro is not None and clave_ok) else None

def crear_token(usuario: str, rol: str) -> dict:
    ahora = int(time.time())
    payload = {"sub": usuario, "rol": rol, "iat": ahora, "exp": ahora + JWT_EXPIRA_SEGUNDOS}
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITMO)
    return {"access_token": token, "token_type": "Bearer", "expires_in": JWT_EXPIRA_SEGUNDOS}

#Se usa en los endpoints para checkear al usuario
def verificar_token(
    credenciales: HTTPAuthorizationCredentials | None = Depends(_esquema_bearer),
) -> dict:
    if credenciales is None:
        raise _no_autenticado("Token faltante o inválido")
    try:
        payload = jwt.decode(
            credenciales.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITMO],
            options={"require": ["exp", "sub", "rol"]},
        )
    except jwt.ExpiredSignatureError:
        raise _no_autenticado("Token expirado")
    except jwt.InvalidTokenError:
        raise _no_autenticado("Token faltante o inválido")

    if payload.get("rol") not in ROLES_VALIDOS:
        raise _no_autenticado("Token faltante o inválido")
    return payload

# Revisa si el usuairo tiene permiso para hacer los post
def requiere_escritura(payload: dict = Depends(verificar_token)) -> dict:
    if payload["rol"] != ROL_ESCRITURA:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "PROHIBIDO",
                "mensaje": f"El rol '{payload['rol']}' no puede realizar esta operación",
            },
        )
    return payload