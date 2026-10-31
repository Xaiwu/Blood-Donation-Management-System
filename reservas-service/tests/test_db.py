"""
Pruebas unitarias de la capa de persistencia de reservas (db.py) sobre una BD temporal.

No requieren el servidor gRPC levantado ni modifican el stock real.
"""

import os
import sqlite3
import sys
import uuid

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import db


@pytest.fixture
def bd(tmp_path, monkeypatch):
    """BD aislada y sembrada con el stock inicial."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "reservas.db"))
    db.inicializar_bd()
    return db


def _estado(tipo):
    conn = db.obtener_conexion()
    try:
        fila = conn.execute(
            "SELECT unidades_disponibles, unidades_reservadas FROM inventario WHERE tipo_sangre = ?", (tipo,)
        ).fetchone()
        return fila["unidades_disponibles"], fila["unidades_reservadas"]
    finally:
        conn.close()


def test_liberar_es_idempotente_por_id(bd):
    """Repetir la liberación de un mismo id no devuelve stock dos veces."""
    assert bd.reservar_stock("A", "O_NEGATIVO", 2) == (True, 4)
    assert bd.liberar_stock("A", "O_NEGATIVO", 2) == (True, 6)
    assert bd.liberar_stock("A", "O_NEGATIVO", 2) == (True, 6)
    assert _estado("O_NEGATIVO") == (6, 0)


def test_liberar_reintento_no_toca_reservas_ajenas(bd):
    """El reintento de liberar A no consume las unidades reservadas por B."""
    bd.reservar_stock("A", "O_NEGATIVO", 2)
    bd.reservar_stock("B", "O_NEGATIVO", 3)
    bd.liberar_stock("A", "O_NEGATIVO", 2)
    bd.liberar_stock("A", "O_NEGATIVO", 2)
    assert _estado("O_NEGATIVO") == (3, 3)


def test_liberar_id_sin_reserva_se_rechaza(bd):
    """No se puede liberar con un id que nunca reservó, aunque haya unidades reservadas por otros."""
    bd.reservar_stock("B", "O_NEGATIVO", 3)
    exito, mensaje = bd.liberar_stock("X", "O_NEGATIVO", 1)
    assert exito is False and "reserva" in mensaje
    assert _estado("O_NEGATIVO") == (3, 3)


def test_liberar_con_parametros_distintos_se_rechaza(bd):
    """Tipo o cantidad distintos a la reserva original se rechazan."""
    bd.reservar_stock("A", "O_NEGATIVO", 2)
    assert bd.liberar_stock("A", "O_NEGATIVO", 1)[0] is False
    assert bd.liberar_stock("A", "A_POSITIVO", 2)[0] is False
    assert _estado("O_NEGATIVO") == (4, 2)


def test_reservar_mismo_id_con_otros_parametros_se_rechaza(bd):
    """Reusar un id con otra cantidad no es un reintento: se rechaza y no descuenta."""
    bd.reservar_stock("A", "O_NEGATIVO", 2)
    exito, _ = bd.reservar_stock("A", "O_NEGATIVO", 3)
    assert exito is False
    assert _estado("O_NEGATIVO") == (4, 2)


def test_reservar_tras_liberar_con_mismo_id_se_rechaza(bd):
    """Un id ya liberado no vuelve a descontar stock por un reintento tardío."""
    bd.reservar_stock("A", "O_NEGATIVO", 2)
    bd.liberar_stock("A", "O_NEGATIVO", 2)
    exito, _ = bd.reservar_stock("A", "O_NEGATIVO", 2)
    assert exito is False
    assert _estado("O_NEGATIVO") == (6, 0)


def test_reserva_idempotente_sigue_funcionando(bd):
    """El reintento exacto de una reserva activa responde éxito sin descontar otra vez."""
    assert bd.reservar_stock("A", "O_NEGATIVO", 2) == (True, 4)
    assert bd.reservar_stock("A", "O_NEGATIVO", 2) == (True, 4)
    assert _estado("O_NEGATIVO") == (4, 2)


def test_error_de_bd_se_levanta_como_excepcion_no_como_rechazo_de_negocio(bd, monkeypatch):
    """Una falla del motor no debe devolverse como (False, texto), que se leería como falta de stock."""
    bloqueo = db.obtener_conexion()
    bloqueo.execute("BEGIN IMMEDIATE")  # otro escritor retiene el bloqueo de escritura

    def conexion_sin_espera():
        conn = sqlite3.connect(db.DB_PATH, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 0;")
        return conn

    monkeypatch.setattr(db, "obtener_conexion", conexion_sin_espera)
    try:
        with pytest.raises(db.ErrorBaseDatos) as exc:
            db.reservar_stock(str(uuid.uuid4()), "O_NEGATIVO", 1)
        assert exc.value.transitorio is True
    finally:
        bloqueo.rollback()
        bloqueo.close()
