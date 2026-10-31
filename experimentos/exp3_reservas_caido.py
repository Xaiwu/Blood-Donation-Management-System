"""Experimento 3: comportamiento de la API con Reservas detenido y pausado (D4/T7).

Levanta un proyecto aislado (bdms-caido, puerto 18180) sin .env, mide códigos y
latencias con Reservas arriba, detenido (docker stop) y pausado (docker pause),
el tiempo de recuperación y la resolución de compensaciones pendientes.
Al terminar elimina el proyecto y sus volúmenes.

Uso (desde la raíz del repositorio, con Docker en ejecución):
    python experimentos/exp3_reservas_caido.py
"""
import csv
import json
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERRIDE = os.path.join(RAIZ, "experimentos", "docker-compose.caido.yml")
SALIDA = os.path.join(RAIZ, "experimentos", "resultados", "exp3_caido_crudo.csv")
ENV_VACIO = os.path.join(RAIZ, "experimentos", "resultados", ".env-vacio")
BASE = "http://localhost:18180/v1"
N = 10  # peticiones por endpoint y estado


def compose(*args):
    cmd = ["docker", "compose", "-p", "bdms-caido", "--env-file", ENV_VACIO,
           "-f", "docker-compose.yml", "-f", OVERRIDE, *args]
    subprocess.run(cmd, cwd=RAIZ, check=True, capture_output=True)


def llamar(metodo, ruta, cuerpo=None, token=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(BASE + ruta, data=datos, method=metodo)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            codigo, cabeceras, texto = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        codigo, cabeceras, texto = e.code, e.headers, e.read()
    ms = (time.perf_counter() - t0) * 1000
    try:
        cuerpo_resp = json.loads(texto) if texto else None
    except ValueError:
        cuerpo_resp = None
    return codigo, ms, cabeceras.get("Retry-After"), cuerpo_resp


def medir(estado, token, cuerpo_sol, activas, filas):
    pruebas = [
        ("POST /solicitudes", lambda i: llamar("POST", "/solicitudes", cuerpo_sol, token)),
        ("POST /solicitudes/{id}/revertir",
         lambda i: llamar("POST", f"/solicitudes/{activas[i % len(activas)]}/revertir", token=token)),
        ("GET /solicitudes", lambda i: llamar("GET", "/solicitudes", token=token)),
        ("GET /hospitales", lambda i: llamar("GET", "/hospitales", token=token)),
        ("GET /health/live", lambda i: llamar("GET", "/health/live")),
        ("GET /health", lambda i: llamar("GET", "/health")),
        ("GET /health/ready", lambda i: llamar("GET", "/health/ready")),
    ]
    for nombre, prueba in pruebas:
        for i in range(N):
            codigo, ms, retry, cuerpo = prueba(i)
            detalle = None
            if isinstance(cuerpo, dict):
                detalle = cuerpo.get("error") or cuerpo.get("estado")
            filas.append([estado, nombre, i, codigo, round(ms, 1), retry, detalle])


def resumen(filas):
    print("| Estado | Endpoint | Códigos | Detalle | p50 (ms) | máx. (ms) | Retry-After |")
    print("|---|---|---|---|---|---|---|")
    for estado in dict.fromkeys(f[0] for f in filas):
        for ep in dict.fromkeys(f[1] for f in filas):
            sub = [f for f in filas if f[0] == estado and f[1] == ep]
            codigos = {}
            for f in sub:
                codigos[f[3]] = codigos.get(f[3], 0) + 1
            detalle = ", ".join(sorted({str(f[6]) for f in sub}))
            retry = ", ".join(sorted({str(f[5]) for f in sub if f[5]})) or "-"
            lat = [f[4] for f in sub]
            print(f"| {estado} | {ep} | {codigos} | {detalle} | {statistics.median(lat):.0f} "
                  f"| {max(lat):.0f} | {retry} |")


def main():
    open(ENV_VACIO, "w").close()
    compose("up", "-d", "--build", "--wait")
    try:
        token = llamar("POST", "/auth/token", {"usuario": "operador", "clave": "operador123"})[3]["access_token"]
        hospital = llamar("POST", "/hospitales", {"nombre": "Hospital Exp3", "direccion": "Calle 1"}, token)[3]["id"]
        cuerpo_sol = {"id_hospital": hospital, "tipo_sangre": "O_POSITIVO", "cantidad": 1}
        filas = []

        # Arriba: se crean N solicitudes y se revierten esas mismas
        activas = [llamar("POST", "/solicitudes", cuerpo_sol, token)[3]["id_solicitud"] for _ in range(N)]
        medir("arriba", token, cuerpo_sol, activas, filas)

        # Solicitudes ACTIVAS que se intentarán revertir con Reservas caído
        activas = [llamar("POST", "/solicitudes", cuerpo_sol, token)[3]["id_solicitud"] for _ in range(3)]

        compose("stop", "reservas")
        medir("detenido", token, cuerpo_sol, activas, filas)
        compose("start", "reservas")
        time.sleep(8)

        compose("pause", "reservas")
        medir("pausado", token, cuerpo_sol, activas, filas)
        compose("unpause", "reservas")

        # Recuperación: tiempo desde que 'start' retorna hasta el primer 201
        compose("stop", "reservas")
        compose("start", "reservas")
        t0 = time.perf_counter()
        intentos = 0
        while True:
            intentos += 1
            if llamar("POST", "/solicitudes", cuerpo_sol, token)[0] == 201:
                break
            time.sleep(0.1)
        recuperacion_ms = (time.perf_counter() - t0) * 1000

        # Compensaciones pendientes hasta que el reconciliador las resuelve
        pendientes = []
        for s in range(0, 61, 5):
            r = llamar("GET", "/health/ready")
            pendientes.append((s, (r[3] or {}).get("compensaciones_pendientes")))
            if s < 60:
                time.sleep(5)

        with open(SALIDA, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["estado", "endpoint", "i", "codigo", "ms", "retry_after", "detalle"])
            w.writerows(filas)

        resumen(filas)
        print(f"\nRecuperación: primer 201 a los {recuperacion_ms:.0f} ms tras 'start' ({intentos} intentos)")
        print("Compensaciones pendientes (s, n):", pendientes)
    finally:
        compose("down", "-v")
        os.remove(ENV_VACIO)


if __name__ == "__main__":
    main()
