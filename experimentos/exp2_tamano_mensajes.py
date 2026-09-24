"""
Experimento 2: tamaño y costo de serialización de mensajes Protobuf (gRPC) vs JSON (REST).

Uso:  experimentos/.venv/Scripts/python experimentos/exp2_tamano_mensajes.py
"""
import csv
import gzip
import json
import os
import random
import subprocess
import sys
import timeit
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
GEN = os.path.join(AQUI, "_gen")
RESULTADOS = os.path.join(AQUI, "resultados")

# Genera los stubs desde el contrato para medir exactamente lo que viaja por gRPC
os.makedirs(GEN, exist_ok=True)
subprocess.run(
    [sys.executable, "-m", "grpc_tools.protoc", "-I", os.path.join(RAIZ, "contracts"),
     f"--python_out={GEN}", os.path.join(RAIZ, "contracts", "reservas.proto")],
    check=True,
)
sys.path.insert(0, GEN)
import reservas_pb2 as pb  # noqa: E402

TIPOS = [n for n in pb.TipoSangre.keys() if n != "TIPO_SANGRE_DESCONOCIDO"]
REPETICIONES = 20000


def json_compacto(obj) -> bytes:
    # Mismo formato que emite Starlette/FastAPI (JSONResponse)
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def casos():
    id_sol = str(uuid.uuid4())

    yield ("ReservarUnidadesRequest",
           pb.ReservarUnidadesRequest(id_solicitud=id_sol, tipo_sangre=pb.O_POSITIVO, cantidad=3),
           {"id_solicitud": id_sol, "tipo_sangre": "O_POSITIVO", "cantidad": 3})

    yield ("ReservarUnidadesResponse",
           pb.ReservarUnidadesResponse(id_solicitud=id_sol, tipo_sangre=pb.O_POSITIVO,
                                       unidades_reservadas=3, unidades_restantes=17),
           {"id_solicitud": id_sol, "tipo_sangre": "O_POSITIVO",
            "unidades_reservadas": 3, "unidades_restantes": 17})

    # Listados de N elementos: N=8 es el inventario real; el resto mide cómo escala.
    # Datos aleatorios con semilla fija: evitan que gzip se beneficie de patrones periódicos
    # y el stock >= 1 evita que Protobuf omita campos por valer 0.
    rng = random.Random(42)
    for n in (8, 100, 1000, 10000):
        items = [(rng.choice(TIPOS), rng.randint(1, 500)) for _ in range(n)]
        msg = pb.ListarStockResponse(items=[
            pb.StockSangre(tipo_sangre=pb.TipoSangre.Value(t), unidades_disponibles=u) for t, u in items
        ])
        js = {"items": [{"tipo_sangre": t, "unidades_disponibles": u} for t, u in items]}
        yield (f"ListarStockResponse (N={n})", msg, js)


def medir_us(fn, repeticiones) -> float:
    return timeit.timeit(fn, number=repeticiones) / repeticiones * 1e6


def main():
    filas = []
    for nombre, msg, obj in casos():
        b_pb = msg.SerializeToString()
        b_js = json_compacto(obj)
        # Menos repeticiones para mensajes grandes, para que el script termine rápido
        rep = max(50, REPETICIONES // max(1, len(b_js) // 100))
        clase = type(msg)
        filas.append({
            "mensaje": nombre,
            "bytes_protobuf": len(b_pb),
            "bytes_json": len(b_js),
            "ratio_json_sobre_pb": round(len(b_js) / len(b_pb), 2),
            "bytes_protobuf_gzip": len(gzip.compress(b_pb)),
            "bytes_json_gzip": len(gzip.compress(b_js)),
            "us_serializar_pb": round(medir_us(msg.SerializeToString, rep), 2),
            "us_serializar_json": round(medir_us(lambda: json_compacto(obj), rep), 2),
            "us_deserializar_pb": round(medir_us(lambda: clase.FromString(b_pb), rep), 2),
            "us_deserializar_json": round(medir_us(lambda: json.loads(b_js), rep), 2),
        })

    os.makedirs(RESULTADOS, exist_ok=True)
    ruta_csv = os.path.join(RESULTADOS, "exp2_tamano_mensajes.csv")
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    for fila in filas:
        print(fila)
    graficar(filas)
    print(f"\nResultados en {ruta_csv}")


def graficar(filas):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    etiquetas = [f["mensaje"].replace("Response", "Resp.").replace("Request", "Req.") for f in filas]
    x = range(len(filas))
    ancho = 0.2
    fig, ax = plt.subplots(figsize=(11, 5))
    series = [("bytes_protobuf", "Protobuf"), ("bytes_json", "JSON"),
              ("bytes_protobuf_gzip", "Protobuf + gzip"), ("bytes_json_gzip", "JSON + gzip")]
    for i, (col, lab) in enumerate(series):
        ax.bar([p + (i - 1.5) * ancho for p in x], [f[col] for f in filas], ancho, label=lab)
    ax.set_yscale("log")
    ax.set_ylabel("Bytes del payload (escala log)")
    ax.set_title("Exp. 2 — Tamaño de mensaje: Protobuf vs JSON (mismos datos)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(etiquetas, rotation=15, ha="right")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTADOS, "exp2_tamano_mensajes.png"), dpi=120)


if __name__ == "__main__":
    main()
