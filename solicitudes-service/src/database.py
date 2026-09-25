import os
import sqlite3
import uuid
import json

DB_PATH = os.getenv("DB_PATH",os.path.join(os.path.dirname(os.path.abspath(__file__)), "solicitudes.db"),)

# Estructura declarada en el YAML
_COLUMNAS_HOSPITAL = "id, nombre, direccion"
_COLUMNAS_SOLICITUD = "id_solicitud, id_hospital, tipo_sangre, cantidad, estado"


def obtener_conexion() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    #conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def inicializar_bd() -> None:
    carpeta = os.path.dirname(DB_PATH)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    conn = obtener_conexion()

    # Crea las tablas de los hospitales y solicitudes si no existen
    try:
        cursor = conn.cursor()
        # Tabla de hospitales solicitantes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hospitales (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                direccion TEXT NOT NULL,
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Tabla de solicitudes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS solicitudes (
                id_solicitud TEXT PRIMARY KEY,
                id_hospital TEXT NOT NULL,
                tipo_sangre TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                estado TEXT NOT NULL CHECK(estado IN ('ACTIVA', 'REVERTIDA')),
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(id_hospital) REFERENCES hospitales(id)
            );
        """)
        # Tabla de idempotencia
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS idempotencia_log (
                clave TEXT PRIMARY KEY,
                respuesta_json TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.commit()
    finally:
        conn.close()

# CREAR HOSPITAL, LISTAR HOSPITAL, OBTENER HOSPITAL
def crear_hospital(nombre: str, direccion: str) -> dict:
    conn = obtener_conexion()
    try:
        # genera un UUID para el hospital (ejemplo: f353ca91-4fc5-49f2-9b9e-304f83d11914 )
        hospital_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO hospitales (id, nombre, direccion) VALUES (?, ?, ?)",
            (hospital_id, nombre, direccion)
        )
        conn.commit()
        return {"id": hospital_id, "nombre": nombre, "direccion": direccion}
    finally:
        conn.close()

def obtener_hospital(hospital_id: str) -> dict | None:
    conn = obtener_conexion()
    # Busca el hospital y devuelve sus propiedades, por ahora nombre y dirección
    try:
        cursor = conn.execute(
            f"SELECT {_COLUMNAS_HOSPITAL} FROM hospitales WHERE id = ?", (hospital_id,)
        )
        fila = cursor.fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()

def listar_hospitales() -> list[dict]:
    conn = obtener_conexion()
    # Devuelve todos los hospitales, en orden de creación
    try:
        cursor = conn.execute(
            f"SELECT {_COLUMNAS_HOSPITAL} FROM hospitales ORDER BY fecha_creacion, rowid"
        )
        return [dict(fila) for fila in cursor.fetchall()]
    finally:
        conn.close()

# CREAR SOLICITUD, OBTENER SOLICITUD, LISTAR SOLICITUDES, REVERTIR / RESTAURAR ESTADO
def crear_solicitud(id_solicitud: str, id_hospital: str, tipo_sangre: str, cantidad: int) -> dict:
    conn = obtener_conexion()
    try:
        # Inserta una solicitud con un estado activo
        conn.execute("""
            INSERT INTO solicitudes (id_solicitud, id_hospital, tipo_sangre, cantidad, estado)
            VALUES (?, ?, ?, ?, 'ACTIVA')
        """, (id_solicitud, id_hospital, tipo_sangre, cantidad))
        conn.commit()
        cursor = conn.execute(
            f"SELECT {_COLUMNAS_SOLICITUD} FROM solicitudes WHERE id_solicitud = ?",
            (id_solicitud,)
        )
        return dict(cursor.fetchone())
    finally:
        conn.close()

def obtener_solicitud(id_solicitud: str) -> dict | None:
    conn = obtener_conexion()
    try:
        cursor = conn.execute(
            f"SELECT {_COLUMNAS_SOLICITUD} FROM solicitudes WHERE id_solicitud = ?",
            (id_solicitud,)
        )
        fila = cursor.fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()

def listar_solicitudes() -> list[dict]:
    conn = obtener_conexion()
    try:
        cursor = conn.execute(
            f"SELECT {_COLUMNAS_SOLICITUD} FROM solicitudes ORDER BY fecha_creacion, rowid"
        )
        return [dict(fila) for fila in cursor.fetchall()]
    finally:
        conn.close()


def revertir_estado_solicitud(id_solicitud: str) -> bool:
    #Revierte el estado
    conn = obtener_conexion()
    try:
        cursor = conn.execute(
            "UPDATE solicitudes SET estado = 'REVERTIDA' WHERE id_solicitud = ? AND estado = 'ACTIVA'",
            (id_solicitud,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

# Se usa cuando no se pudo revertir la solicitud por motivos de conexion servidor etc
def restaurar_estado_activa(id_solicitud: str) -> bool:
    conn = obtener_conexion()
    try:
        cursor = conn.execute(
            "UPDATE solicitudes SET estado = 'ACTIVA' WHERE id_solicitud = ? AND estado = 'REVERTIDA'",
            (id_solicitud,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def obtener_idempotencia(clave: str) -> dict | None:
    #Busca si ya pasó una llave de idempotencia
    conn = obtener_conexion()
    try:
        cursor = conn.execute(
            "SELECT respuesta_json, status_code FROM idempotencia_log WHERE clave = ?", 
            (clave,)
        )
        fila = cursor.fetchone()
        if fila:
            return {
                "respuesta": json.loads(fila["respuesta_json"]),
                "status_code": fila["status_code"]
            }
        return None
    finally:
        conn.close()

def guardar_idempotencia(clave: str, respuesta: dict, status_code: int) -> None:
    #Cuando hay una operación de solicitud exitosa guarda la llave para revisarla 
    conn = obtener_conexion()
    try:
        conn.execute(
            "INSERT INTO idempotencia_log (clave, respuesta_json, status_code) VALUES (?, ?, ?)",
            (clave, json.dumps(respuesta), status_code)
        )
        conn.commit()
    finally:
        conn.close()