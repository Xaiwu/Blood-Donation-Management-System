# Blood Donation Management System

Sistema de gestión de solicitudes de sangre basado en dos servicios independientes. Permite gestionar hospitales y solicitudes de unidades de sangre, coordinando mediante gRPC las operaciones de consulta, reserva y liberación del stock disponible.

## INTEGRANTES:

Francisco Cifuentes, Jesús Guevara, Rodrigo Bascuñan

## Arquitectura del Sistema

El sistema está compuesto por dos servicios independientes y contenedorizados, diseñados para mantener responsabilidades y datos separados. Solicitudes expone la API REST pública para gestionar hospitales y solicitudes de sangre, mientras que Reservas administra el stock y expone sus operaciones internamente mediante gRPC..

## Tabla de URLs del Sistema

| Microservicio | Protocolo / Interfaz | URL Local | Descripción |
| :--- | :--- | :--- | :--- |
| **Solicitudes Service** | HTTP / REST | `http://localhost:8080/v1` | Gestión de hospitales y solicitudes de unidades de sangre. |
| **Documentación API** | Swagger UI | `http://localhost:8080/v1/docs` | Interfaz interactiva de los endpoints REST (OpenAPI). |
| **Reservas Service** | gRPC | `reservas:50051` | Gestión del stock y operaciones de consulta, reserva y liberación de unidades. |

---

### Servicios Principales

1. **Solicitudes Service (`/solicitudes-service`)**
   - **Descripción:** Gestiona hospitales y solicitudes de unidades de sangre. Expone la API REST pública, implementa autenticación mediante JWT y coordina con Reservas las operaciones que requieren consultar o modificar el stock.
   - **Componentes clave:** Autenticación (`auth.py`), Base de datos (`database.py`), y lógica principal (`main.py`).

2. **Reservas Service (`/reservas-service`)**
   - **Descripción:** Administra el inventario de unidades de sangre y las operaciones de consulta, reserva y liberación de stock. Expone estas operaciones internamente mediante gRPC.
   - **Componentes clave:** Servidor (`server.py`), conexión a BD (`db.py`), y pruebas (`test_reservas.py`).

### Contratos y APIs (`/contracts`)

La comunicación entre servicios y con clientes externos se define mediante:
- **OpenAPI (`openapi.yaml`):** Especificación de la API REST pública de Solicitudes.
- **Protocol Buffers (`reservas.proto`):** Definición de los mensajes y operaciones del servicio gRPC de Reservas.

## Tecnologías Utilizadas

- **Lenguaje:** Python
- **Infraestructura:** Docker & Docker Compose
- **Comunicación:** REST (OpenAPI) / gRPC (Protobuf)
- **Pruebas y Experimentos:** Scripts de experimentación de rendimiento (Timeouts, Tamaño de mensajes).

## Requisitos Previos

Asegúrate de tener instalado lo siguiente en tu máquina local:
- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

Para ejecutar los experimentos ABET 6 también se requiere:

- Python 3.x

## Instalación y Configuración

1. **Clonar el repositorio:**
   ```bash
   git clone <url-del-repositorio>
   cd Blood-Donation-Management-System
   ```

2. **Configuración de variables de entorno**

Antes de iniciar el sistema, se deben configurar las variables de entorno utilizadas para la autenticación de la API REST.

Desde la raíz del proyecto, copie el archivo `.env-example` y renómbrelo como `.env`:

```bash
cp .env-example .env
```

En Windows PowerShell también puede utilizar:

```powershell
Copy-Item .env-example .env
```

Luego, complete las variables del archivo `.env`:

```env
JWT_SECRET=
USUARIO_LECTURA=lector
CLAVE_LECTURA=lector123
USUARIO_ESCRITURA=operador
CLAVE_ESCRITURA=operador123
```

Las credenciales anteriores pueden utilizarse para fines de demostración:

- `lector / lector123`: usuario con permisos de solo lectura.
- `operador / operador123`: usuario con permisos de lectura y escritura.

> **Importante:** `USUARIO_LECTURA`, `CLAVE_LECTURA`, `USUARIO_ESCRITURA` y `CLAVE_ESCRITURA` deben estar configuradas para que el servicio de Solicitudes pueda iniciar correctamente.

El archivo `.env` está excluido del control de versiones mediante `.gitignore`, por lo que las credenciales configuradas localmente no se almacenan en el repositorio.


3. **Levantar los servicios con Docker:**
   El proyecto incluye un archivo `docker-compose.yml` preconfigurado para orquestar todos los microservicios.
   ```bash
   docker compose up --build
   ```

## Ejecutar el servicio gRPC por separado

El servicio de Reservas puede ejecutarse de manera independiente mediante Docker.

Desde la raíz del repositorio, construya la imagen:

```bash
docker build -f reservas-service/Dockerfile -t reservas-service .
```

Luego ejecute el contenedor exponiendo el puerto gRPC:

```bash
docker run --rm -p 50051:50051 reservas-service
```

El servicio gRPC quedará disponible localmente en:

```text
localhost:50051
```

El contrato y las operaciones disponibles se encuentran definidos en:

```text
contracts/reservas.proto
```

## Probar los endpoints

La forma más sencilla de probar la API REST es utilizar la interfaz Swagger disponible en:

`http://localhost:8080/v1/docs`

Primero se debe obtener un token mediante `POST /v1/auth/token`. Luego, el token puede utilizarse para autorizar las operaciones protegidas desde la misma interfaz de Swagger.

Para pruebas locales pueden utilizarse las credenciales configuradas previamente en el archivo `.env`.

## Ejecución de experimentos — ABET 6

El repositorio incluye dos experimentos reproducibles utilizados para evaluar el comportamiento y rendimiento del sistema.

Los scripts y sus dependencias se encuentran en el directorio:

```text
experimentos/
```

### Preparación del entorno

Para ejecutar los experimentos se requiere:

- Docker y Docker Compose.
- Python 3.
- Un archivo `.env` correctamente configurado en la raíz del proyecto.

Se recomienda utilizar un entorno virtual de Python. Desde la raíz del proyecto:

```bash
python -m venv experimentos/.venv-exp
```

En Windows PowerShell:

```powershell
.\experimentos\.venv-exp\Scripts\Activate.ps1
```

En Linux/macOS:

```bash
source experimentos/.venv-exp/bin/activate
```

Instale las dependencias de los experimentos:

```bash
pip install -r experimentos/requirements.txt
```

---

### Experimento 1 — Timeout ante una dependencia lenta

Este experimento evalúa el comportamiento de la API REST cuando el servicio de Reservas presenta distintos niveles de latencia.

Se prueban automáticamente las combinaciones de:

- Retardos artificiales en Reservas: `0`, `500`, `1000`, `2000` y `4000 ms`.
- Timeouts de Solicitudes: `1`, `2` y `30 s`.
- 3 repeticiones por combinación.
- 50 clientes concurrentes.
- 150 solicitudes por escenario.

El retardo se introduce mediante la variable `RESERVAS_DELAY_MS`, mientras que el tiempo máximo de espera de la llamada gRPC se configura mediante `RESERVAS_TIMEOUT_S`.

Durante cada escenario también se consulta `/v1/health` para observar si la lentitud del servicio de Reservas afecta al resto de la API.

Para ejecutar el experimento, desde la raíz del repositorio:

```bash
python experimentos/exp1_timeout.py
```

El experimento utiliza `docker-compose.yml` junto con:

```text
experimentos/docker-compose.experimento.yml
```

La API utilizada durante el experimento se expone en:

```text
http://localhost:18080/v1
```

Esto evita conflictos con el puerto `8080` utilizado por la ejecución normal del sistema.

Las principales métricas registradas son:

- Latencia de las solicitudes.
- Percentiles p50, p95 y p99.
- Cantidad de respuestas exitosas (`201`).
- Cantidad de respuestas por indisponibilidad (`503`).
- Solicitudes procesadas por segundo.
- Reservas huérfanas.
- Latencia de `/v1/health` durante la carga.

Los resultados se almacenan en:

```text
experimentos/resultados/exp1_timeout_crudo.csv
experimentos/resultados/exp1_timeout_agregado.csv
experimentos/resultados/exp1_timeout_resumen.csv
experimentos/resultados/exp1_timeout.png
```

Si los datos ya fueron generados y solo se desea volver a realizar la agregación de los resultados, se puede ejecutar:

```bash
python experimentos/exp1_timeout.py --solo-agregar
```

---

### Experimento 2 — Comparación entre Protobuf y JSON

Este experimento compara el tamaño y el tiempo de serialización/deserialización de mensajes equivalentes utilizando **Protocol Buffers** y **JSON**.

Para mantener la comparación alineada con el sistema implementado, el experimento utiliza los mensajes definidos en:

```text
contracts/reservas.proto
```

Se evalúan los siguientes casos:

- `ReservarUnidadesRequest`.
- `ReservarUnidadesResponse`.
- `ListarStockResponse` con 8 elementos.
- `ListarStockResponse` con 100 elementos.
- `ListarStockResponse` con 1.000 elementos.
- `ListarStockResponse` con 10.000 elementos.

Para cada caso se comparan:

- Tamaño del mensaje Protobuf.
- Tamaño del mensaje JSON.
- Tamaño utilizando compresión gzip.
- Tiempo de serialización.
- Tiempo de deserialización.

Para ejecutar el experimento, desde la raíz del repositorio:

```bash
python experimentos/exp2_tamano_mensajes.py
```

Los resultados se almacenan en:

```text
experimentos/resultados/exp2_tamano_mensajes.csv
experimentos/resultados/exp2_tamano_mensajes.png
```

---

### Resultados

Todos los archivos generados por los experimentos se encuentran en:

```text
experimentos/resultados/
```

Los archivos `.csv` contienen los datos obtenidos durante las mediciones, mientras que los archivos `.png` corresponden a las visualizaciones utilizadas para analizar los resultados.

Estos experimentos permiten evaluar de manera reproducible el comportamiento del sistema ante una dependencia gRPC lenta y comparar las características de los mensajes utilizados en la comunicación interna.



## Estructura del Proyecto

```text
Blood-Donation-Management-System/
├── contracts/               # Definiciones de API (OpenAPI) y gRPC (Protobuf)
├── experimentos/            # Scripts de pruebas de rendimiento y resultados
├── reservas-service/        # Microservicio de reservas (Python)
├── solicitudes-service/     # Microservicio de solicitudes y auth (Python)
├── docker-compose.yml       # Orquestación principal de contenedores
├── .env-example             # Plantilla de variables de entorno
└── README.md                # Este archivo
```

## Uso de Inteligencia Artificial

Durante el desarrollo del proyecto se utilizaron herramientas de Inteligencia Artificial como apoyo en tareas de revisión, documentación y desarrollo.

En particular, se utilizó IA para:

- Apoyar la revisión y mejora de la redacción de la documentación y los ADRs.
- Resolver dudas relacionadas con la implementación y configuración de REST, gRPC y Docker.
- Apoyar la revisión y depuración de código durante el desarrollo.
- Apoyar el diseño y análisis de los experimentos realizados.
- Revisar la coherencia entre la implementación, las decisiones de arquitectura y la documentación.

Las decisiones de arquitectura, la implementación final y los resultados experimentales fueron revisados y validados por los integrantes del grupo.
