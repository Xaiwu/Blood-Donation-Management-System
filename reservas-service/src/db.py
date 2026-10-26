"""
Módulo de persistencia y gestión  para reservas-service.
"""
import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "reservas.db"))

def obtener_conexion() -> sqlite3.Connection:
    """
    Crea y retorna una conexión a la base de datos local SQLite.

    Se configura isolation_level=None para desactivar las transacciones
    del driver de Python, permitiendo control manual y explícito mediante
    sentencias BEGIN IMMEDIATE, COMMIT y ROLLBACK.

    Returns:
        sqlite3.Connection: Objeto de conexión con row_factory configurado a Row.
    """
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def inicializar_bd() -> None:
    """
    Crea las tablas y siembra el stock inicial de los 8 grupos si no existen.
    Aplica restricciones CHECK para garantizar stock no negativo.
    Utiliza 'INSERT OR IGNORE' para que el arranque sea completamente idempotente
    ante múltiples ejecuciones o reinicios del contenedor.
    """
    carpeta = os.path.dirname(DB_PATH)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    conn = obtener_conexion()
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventario (
                tipo_sangre TEXT PRIMARY KEY,
                unidades_disponibles INTEGER NOT NULL CHECK (unidades_disponibles >= 0),
                unidades_reservadas INTEGER NOT NULL DEFAULT 0 CHECK (unidades_reservadas >= 0)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log_reservas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_solicitud TEXT NOT NULL,
                tipo_sangre TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                accion TEXT NOT NULL,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        tipos_iniciales = [
            ("A_POSITIVO", 10, 0),
            ("A_NEGATIVO", 5, 0),
            ("B_POSITIVO", 8, 0),
            ("B_NEGATIVO", 3, 0),
            ("AB_POSITIVO", 4, 0),
            ("AB_NEGATIVO", 2, 0),
            ("O_POSITIVO", 20, 0),
            ("O_NEGATIVO", 6, 0),
        ]

        cursor.executemany("""
            INSERT OR IGNORE INTO inventario (tipo_sangre, unidades_disponibles, unidades_reservadas)
            VALUES (?, ?, ?);
        """, tipos_iniciales)
    finally:
        conn.close()


def consultar_stock(tipo_nombre: str) -> int | None:
    """
    Consulta las unidades disponibles de un tipo de sangre específico.

    Args:
        tipo_nombre: Nombre del tipo de sangre.

    Returns:
        int: Cantidad de unidades disponibles si el tipo existe en el catálogo.
        None: Si el tipo de sangre no está registrado o no existe.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT unidades_disponibles FROM inventario WHERE tipo_sangre = ?",
            (tipo_nombre,)
        )
        fila = cursor.fetchone()
        return fila["unidades_disponibles"] if fila else None
    finally:
        conn.close()


def listar_todo() -> list[sqlite3.Row]:
    """
    Obtiene el catálogo completo de tipos de sangre y sus disponibilidades.

    Returns:
        List[sqlite3.Row]: Lista de filas con columnas 'tipo_sangre' y 'unidades_disponibles'.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT tipo_sangre, unidades_disponibles FROM inventario")
        return cursor.fetchall()
    finally:
        conn.close()


def reservar_stock(
    id_solicitud: str,
    tipo_nombre: str,
    cantidad: int
) -> tuple[bool, int | str]:
    """
    Reserva unidades de sangre para una solicitud específica.

    Utiliza 'BEGIN IMMEDIATE' para adquirir un bloqueo de escritura previo al SELECT,
    mitigando condiciones de carrera y evitando sobreasignaciones concurrentes.
    Si la solicitud ya fue procesada con anterioridad, retorna el estado actual sin
    duplicar el descuento (Idempotent Receiver).

    Args:
        id_solicitud: Identificador único de la solicitud.
        tipo_nombre: Nombre del tipo de sangre solicitado.
        cantidad: Número entero de unidades a reservar.

    Returns:
        Tuple[bool, int | str]:
            - (True, int): Éxito; retorna el remanente de unidades disponibles.
            - (False, str): Falla de validación, stock insuficiente o error SQL.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")

        # Comprobación de Idempotencia
        cursor.execute(
            "SELECT id FROM log_reservas WHERE id_solicitud = ? AND accion = 'RESERVA'",
            (id_solicitud,)
        )
        if cursor.fetchone():
            # Si la solicitud ya existe, se lee el stock remanente y se responde con éxito sin descontar
            cursor.execute(
                "SELECT unidades_disponibles FROM inventario WHERE tipo_sangre = ?",
                (tipo_nombre,)
            )
            fila = cursor.fetchone()
            conn.commit()
            return True, fila["unidades_disponibles"] if fila else 0

        # Comprobar existencia y stock disponible
        cursor.execute(
            "SELECT unidades_disponibles, unidades_reservadas FROM inventario WHERE tipo_sangre = ?",
            (tipo_nombre,)
        )
        fila = cursor.fetchone()
        if not fila:
            conn.rollback()
            return False, "Tipo de sangre no encontrado"

        disponibles = fila["unidades_disponibles"]
        if disponibles < cantidad:
            conn.rollback()
            return False, f"Stock insuficiente: disponibles {disponibles}, solicitadas {cantidad}"

        # Aplicar descuento y registrar
        cursor.execute("""
            UPDATE inventario
            SET unidades_disponibles = unidades_disponibles - ?,
                unidades_reservadas = unidades_reservadas + ?
            WHERE tipo_sangre = ?
        """, (cantidad, cantidad, tipo_nombre))

        cursor.execute("""
            INSERT INTO log_reservas (id_solicitud, tipo_sangre, cantidad, accion)
            VALUES (?, ?, ?, 'RESERVA')
        """, (id_solicitud, tipo_nombre, cantidad))

        conn.commit()
        return True, disponibles - cantidad

    except sqlite3.Error as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def liberar_stock(
    id_solicitud: str,
    tipo_nombre: str,
    cantidad: int
) -> tuple[bool, int | str]:
    """
    Libera unidades previamente reservadas retornándolas al inventario disponible.
    Implementa Idempotent Receiver verificando log_reservas antes de descontar de reservadas.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        # Comprobación de Idempotencia Si ya se libero antes o no.
        cursor.execute(
            "SELECT id FROM log_reservas WHERE id_solicitud = ? AND accion = 'LIBERACION'",
            (id_solicitud,)
        )
        if cursor.fetchone():
            # Si ya fue liberada antes, devolver el stock disponible actual sin liberar dos veces
            cursor.execute(
                "SELECT unidades_disponibles FROM inventario WHERE tipo_sangre = ?",
                (tipo_nombre,)
            )
            fila = cursor.fetchone()
            conn.commit()
            return True, fila["unidades_disponibles"] if fila else 0

        # Verificar que exista la reserva original para ese id_solicitud
        cursor.execute(
            "SELECT cantidad FROM log_reservas WHERE id_solicitud = ? AND accion = 'RESERVA'",
            (id_solicitud,)
        )
        reserva_previa = cursor.fetchone()
        if not reserva_previa:
            conn.rollback()
            return False, f"No existe una reserva previa asociada a la solicitud {id_solicitud}"

        # Comprobar existencia y stock en reserva
        cursor.execute(
            "SELECT unidades_disponibles, unidades_reservadas FROM inventario WHERE tipo_sangre = ?",
            (tipo_nombre,)
        )
        fila = cursor.fetchone()
        if not fila:
            conn.rollback()
            return False, "Tipo de sangre no encontrado"

        reservadas = fila["unidades_reservadas"]
        disponibles = fila["unidades_disponibles"]

        if reservadas < cantidad:
            conn.rollback()
            return False, f"No se pueden liberar {cantidad} unidades; solo hay {reservadas} en reserva"

        # Actualizar inventario y registrar liberación
        cursor.execute("""
            UPDATE inventario
            SET unidades_disponibles = unidades_disponibles + ?,
                unidades_reservadas = unidades_reservadas - ?
            WHERE tipo_sangre = ?
        """, (cantidad, cantidad, tipo_nombre))

        cursor.execute("""
            INSERT INTO log_reservas (id_solicitud, tipo_sangre, cantidad, accion)
            VALUES (?, ?, ?, 'LIBERACION')
        """, (id_solicitud, tipo_nombre, cantidad))

        conn.commit()
        return True, disponibles + cantidad

    except sqlite3.Error as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()
