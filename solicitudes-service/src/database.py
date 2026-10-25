import os
import sqlite3
import uuid


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
        # Liberaciones pendientes en Reservas (reservas huérfanas por resultado ambiguo)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compensaciones_pendientes (
                id_solicitud TEXT PRIMARY KEY,
                tipo_sangre TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                intentos INTEGER NOT NULL DEFAULT 0,
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

# Lógica de compensación, para evitar reservas huerfanas y existan problemas de conectividad

# Registra una compensación pendiente para una solicitud revertida
def registrar_compensacion(id_solicitud: str, tipo_sangre: str, cantidad: int) -> None:
    conn = obtener_conexion()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO compensaciones_pendientes (id_solicitud, tipo_sangre, cantidad) VALUES (?, ?, ?)",
            (id_solicitud, tipo_sangre, cantidad)
        )
        conn.commit()
    finally:
        conn.close()

def listar_compensaciones_vencidas(gracia_s: int) -> list[dict]:
    # Solo las que superaron el timeout 
    conn = obtener_conexion()
    try:
        cursor = conn.execute(
            """SELECT id_solicitud, tipo_sangre, cantidad, intentos FROM compensaciones_pendientes
               WHERE fecha_creacion <= datetime('now', ?) ORDER BY fecha_creacion, rowid""",
            (f"-{int(gracia_s)} seconds",)
        )
        return [dict(fila) for fila in cursor.fetchall()]
    finally:
        conn.close()
# Elimina si se pudo compensar la reserva o pasó el timeout y no se pudo compensar
def eliminar_compensacion(id_solicitud: str) -> None:
    conn = obtener_conexion()
    try:
        conn.execute("DELETE FROM compensaciones_pendientes WHERE id_solicitud = ?", (id_solicitud,))
        conn.commit()
    finally:
        conn.close()
# Incrementa el contador de intentos de compensación para una solicitud revertida
def incrementar_intentos_compensacion(id_solicitud: str) -> None:
    conn = obtener_conexion()
    try:
        conn.execute(
            "UPDATE compensaciones_pendientes SET intentos = intentos + 1 WHERE id_solicitud = ?",
            (id_solicitud,)
        )
        conn.commit()
    finally:
        conn.close()
# Devuelve la cantidad de compensaciones pendientes
def contar_compensaciones_pendientes() -> int:
    conn = obtener_conexion()
    try:
        return conn.execute("SELECT COUNT(*) FROM compensaciones_pendientes").fetchone()[0]
    finally:
        conn.close()
# Lista las solicitudes revertidas
def listar_solicitudes_revertidas() -> list[dict]:
    conn = obtener_conexion()
    try:
        cursor = conn.execute(
            f"SELECT {_COLUMNAS_SOLICITUD} FROM solicitudes WHERE estado = 'REVERTIDA' ORDER BY fecha_creacion, rowid"
        )
        return [dict(fila) for fila in cursor.fetchall()]
    finally:
        conn.close()
