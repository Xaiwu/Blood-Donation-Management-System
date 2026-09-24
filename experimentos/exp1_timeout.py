"""
Experimento 1: efecto del timeout de solicitudes -> reservas ante una dependencia lenta.

Uso:  experimentos/.venv/Scripts/python experimentos/exp1_timeout.py

"""
import csv
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
RESULTADOS = os.path.join(AQUI, "resultados")

PROYECTO = "bdms-exp"
BASE = "http://localhost:18080/v1"
COMPOSE = ["docker", "compose", "-p", PROYECTO,
           "-f", os.path.join(RAIZ, "docker-compose.yml"),
           "-f", os.path.join(AQUI, "docker-compose.experimento.yml")]

RETARDOS_MS = [0, 500, 1000, 2000, 4000]
# 30 s actúa como "sin timeout"
TIMEOUTS_S = [1.0, 2.0, 30.0]
REPETICIONES = 3
CONCURRENCIA = 50       # > 40 hilos del threadpool de FastAPI/AnyIO para observar saturación
PETICIONES = 150
INTERVALO_HEALTH_S = 0.2


# ---------------------------------------------------------------- utilidades
def leer_env() -> dict:
    valores = {}
    with open(os.path.join(RAIZ, ".env"), encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                valores[k.strip()] = v.strip().strip('"').strip("'")
    return valores


def compose(*args, env_extra=None):
    env = dict(os.environ, **(env_extra or {}))
    subprocess.run(COMPOSE + list(args), check=True, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def sql(servicio: str, sentencia: str):
    """Ejecuta SQL en la BD del contenedor."""
    codigo = (
        "import os,sqlite3,json;c=sqlite3.connect(os.environ['DB_PATH']);"
        f"r=c.execute({sentencia!r}).fetchall();c.commit();print(json.dumps(r))"
    )
    salida = subprocess.run(COMPOSE + ["exec", "-T", servicio, "python", "-c", codigo],
                            check=True, capture_output=True, text=True).stdout
    return json.loads(salida)


def http(metodo, ruta, token=None, cuerpo=None, timeout=120):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(BASE + ruta, data=datos, method=metodo)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            codigo, cuerpo_resp = r.status, r.read()
    except urllib.error.HTTPError as e:
        codigo, cuerpo_resp = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        codigo, cuerpo_resp = 0, b""
    return codigo, (time.perf_counter() - t0) * 1000, cuerpo_resp


def percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = min(len(ordenados) - 1, max(0, round(p / 100 * (len(ordenados) - 1))))
    return round(ordenados[k], 1)


# ---------------------------------------------------------------- escenario
def preparar(retardo_ms, timeout_s, credenciales):
    compose("up", "-d", "--wait", env_extra={
        "RESERVAS_DELAY_MS": str(retardo_ms), "RESERVAS_TIMEOUT_S": str(timeout_s)})
    codigo, _, cuerpo = http("POST", "/auth/token", cuerpo=credenciales)
    if codigo != 200:
        raise RuntimeError(f"No se pudo obtener token (HTTP {codigo})")
    token = json.loads(cuerpo)["access_token"]
    codigo, _, cuerpo = http("POST", "/hospitales", token,
                             {"nombre": "Hospital Experimento", "direccion": "Laboratorio"})
    id_hospital = json.loads(cuerpo)["id"]
    # Calentamiento: abre el canal gRPC antes de medir
    http("POST", "/solicitudes", token,
         {"id_hospital": id_hospital, "tipo_sangre": "O_POSITIVO", "cantidad": 1})
    esperar_reservas_estables(retardo_ms)
    # Estado inicial limpio y stock de sobra para que nunca haya 409 por falta de stock
    sql("reservas", "UPDATE inventario SET unidades_disponibles=1000000, unidades_reservadas=0")
    sql("reservas", "DELETE FROM log_reservas")
    sql("solicitudes", "DELETE FROM solicitudes")
    return token, id_hospital


def esperar_reservas_estables(retardo_ms):
    """Espera a que reservas termine de procesar RPCs pendientes (incluidas las abandonadas por timeout)."""
    quieto_s = retardo_ms / 1000 * 2 + 2
    anterior, desde = None, time.time()
    while True:
        actual = sql("reservas", "SELECT COUNT(*) FROM log_reservas")[0][0]
        if actual != anterior:
            anterior, desde = actual, time.time()
        elif time.time() - desde >= quieto_s:
            return
        time.sleep(1)


def ejecutar(retardo_ms, timeout_s, repeticion, credenciales, crudos):
    token, id_hospital = preparar(retardo_ms, timeout_s, credenciales)
    cuerpo = {"id_hospital": id_hospital, "tipo_sangre": "O_POSITIVO", "cantidad": 1}

    fin_carga = threading.Event()
    latencias_health = []

    def sondear_health():
        while not fin_carga.is_set():
            codigo, ms, _ = http("GET", "/health", timeout=60)
            latencias_health.append(ms)
            time.sleep(INTERVALO_HEALTH_S)

    sonda = threading.Thread(target=sondear_health, daemon=True)
    sonda.start()

    t_inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONCURRENCIA) as ex:
        resultados = list(ex.map(lambda _: http("POST", "/solicitudes", token, cuerpo)[:2],
                                 range(PETICIONES)))
    duracion_s = time.perf_counter() - t_inicio
    fin_carga.set()
    sonda.join()

    esperar_reservas_estables(retardo_ms)
    reservadas = sql("reservas", "SELECT COALESCE(SUM(unidades_reservadas),0) FROM inventario")[0][0]
    activas = sql("solicitudes", "SELECT COUNT(*) FROM solicitudes WHERE estado='ACTIVA'")[0][0]

    for codigo, ms in resultados:
        crudos.append({"retardo_ms": retardo_ms, "timeout_s": timeout_s, "repeticion": repeticion,
                       "codigo_http": codigo, "latencia_ms": round(ms, 1)})

    lat = [ms for _, ms in resultados]
    codigos = [c for c, _ in resultados]
    return {
        "retardo_ms": retardo_ms, "timeout_s": timeout_s, "repeticion": repeticion,
        "peticiones": PETICIONES, "concurrencia": CONCURRENCIA,
        "duracion_s": round(duracion_s, 2),
        "throughput_rps": round(PETICIONES / duracion_s, 2),
        "n_201": codigos.count(201), "n_503": codigos.count(503),
        "n_otros": PETICIONES - codigos.count(201) - codigos.count(503),
        "lat_p50_ms": percentil(lat, 50), "lat_p95_ms": percentil(lat, 95),
        "lat_p99_ms": percentil(lat, 99), "lat_max_ms": round(max(lat), 1),
        "health_n": len(latencias_health),
        "health_p50_ms": percentil(latencias_health, 50),
        "health_p95_ms": percentil(latencias_health, 95),
        "health_max_ms": round(max(latencias_health), 1) if latencias_health else None,
        "unidades_reservadas_en_reservas": reservadas,
        "solicitudes_activas": activas,
        "reservas_huerfanas": reservadas - activas,
    }


def escribir_csv(nombre, filas):
    with open(os.path.join(RESULTADOS, nombre), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)


def main():
    env = leer_env()
    credenciales = {"usuario": env["USUARIO_ESCRITURA"], "clave": env["CLAVE_ESCRITURA"]}
    os.makedirs(RESULTADOS, exist_ok=True)

    print("Construyendo imágenes del proyecto aislado...", flush=True)
    compose("build")

    resumen, crudos = [], []
    try:
        for rep in range(1, REPETICIONES + 1):
            for retardo in RETARDOS_MS:
                for timeout in TIMEOUTS_S:
                    fila = ejecutar(retardo, timeout, rep, credenciales, crudos)
                    resumen.append(fila)
                    print(f"rep={rep} retardo={retardo}ms timeout={timeout}s -> "
                          f"201={fila['n_201']} 503={fila['n_503']} otros={fila['n_otros']} "
                          f"p95={fila['lat_p95_ms']}ms health_p95={fila['health_p95_ms']}ms "
                          f"huerfanas={fila['reservas_huerfanas']}", flush=True)
                    # Guarda en cada paso para no perder datos si algo falla
                    escribir_csv("exp1_timeout_resumen.csv", resumen)
                    escribir_csv("exp1_timeout_crudo.csv", crudos)
    finally:
        print("Eliminando proyecto aislado y sus volúmenes...", flush=True)
        compose("down", "-v")

    agregado = agregar(resumen, crudos)
    escribir_csv("exp1_timeout_agregado.csv", agregado)
    graficar(agregado)
    print("Listo. Resultados en", RESULTADOS)


def leer_csv(nombre):
    with open(os.path.join(RESULTADOS, nombre), encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        for k, v in fila.items():
            try:
                fila[k] = float(v) if "." in v else int(v)
            except (ValueError, TypeError):
                fila[k] = None
    return filas


def solo_agregar():
    """Recalcula agregado y gráfico desde los CSV existentes, sin volver a generar carga."""
    agregado = agregar(leer_csv("exp1_timeout_resumen.csv"), leer_csv("exp1_timeout_crudo.csv"))
    escribir_csv("exp1_timeout_agregado.csv", agregado)
    graficar(agregado)
    print("Agregado regenerado en", RESULTADOS)


def agregar(resumen, crudos):
    """
    Media y desviación estándar entre repeticiones de cada combinación, más percentiles
    "conjuntos" calculados sobre todas las peticiones de todas las repeticiones: con 150
    muestras por repetición el p99 depende de 1-2 valores; con las 450 juntas es más estable.
    """
    filas = []
    for retardo in RETARDOS_MS:
        for timeout in TIMEOUTS_S:
            grupo = [r for r in resumen if r["retardo_ms"] == retardo and r["timeout_s"] == timeout]
            lat = [c["latencia_ms"] for c in crudos
                   if c["retardo_ms"] == retardo and c["timeout_s"] == timeout]
            fila = {"retardo_ms": retardo, "timeout_s": timeout, "repeticiones": len(grupo),
                    "n_peticiones_total": len(lat),
                    "lat_p50_ms_conjunto": percentil(lat, 50),
                    "lat_p95_ms_conjunto": percentil(lat, 95),
                    "lat_p99_ms_conjunto": percentil(lat, 99)}
            for col in ("lat_p50_ms", "lat_p95_ms", "lat_max_ms", "throughput_rps", "n_201", "n_503",
                        "n_otros", "health_n", "health_p95_ms", "health_max_ms", "reservas_huerfanas"):
                vals = [r[col] for r in grupo if r[col] is not None]
                fila[f"{col}_media"] = round(statistics.mean(vals), 1)
                fila[f"{col}_de"] = round(statistics.stdev(vals), 1) if len(vals) > 1 else 0.0
            filas.append(fila)
    return filas


def graficar(agregado):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def etiqueta(t):
        return "sin timeout (30 s)" if t >= 30 else f"timeout {t:g} s"

    fig, ejes = plt.subplots(2, 2, figsize=(13, 9))
    paneles = [
        (ejes[0][0], "lat_p95_ms", "Latencia p95 POST /v1/solicitudes (ms)"),
        (ejes[0][1], "health_p95_ms", "Latencia p95 GET /v1/health durante la carga (ms)"),
        (ejes[1][0], "n_201", f"Solicitudes exitosas (201) de {PETICIONES}"),
        (ejes[1][1], "reservas_huerfanas", "Reservas huérfanas (unidades sin solicitud)"),
    ]
    for ax, col, titulo in paneles:
        for t in TIMEOUTS_S:
            filas = [f for f in agregado if f["timeout_s"] == t]
            ax.errorbar([f["retardo_ms"] for f in filas], [f[f"{col}_media"] for f in filas],
                        yerr=[f[f"{col}_de"] for f in filas], marker="o", capsize=3, label=etiqueta(t))
        ax.set_title(titulo)
        ax.set_xlabel("Retardo de reservas (ms)")
        ax.grid(alpha=0.3)
        ax.legend()
    fig.suptitle(f"Exp. 1 — Timeout ante dependencia lenta "
                 f"({CONCURRENCIA} clientes concurrentes, {PETICIONES} peticiones, media ± DE de "
                 f"{REPETICIONES} repeticiones)")
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTADOS, "exp1_timeout.png"), dpi=120)


if __name__ == "__main__":
    if "--solo-agregar" in sys.argv:
        solo_agregar()
    else:
        main()
