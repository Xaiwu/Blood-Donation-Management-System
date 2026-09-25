# Blood Donation Management System

Un sistema de gestión de donación de sangre basado en una arquitectura de microservicios. Este proyecto permite gestionar solicitudes de donación y reservas, garantizando una comunicación eficiente entre los distintos servicios.

## INTEGRANTES:

Francisco Cifuentes, Jesús Guevara, Rodrigo Bascuñan

## Arquitectura del Sistema

El sistema está compuesto por múltiples servicios contenedorizados, diseñados para funcionar de manera independiente y comunicarse a través de contratos bien definidos.

## Tabla de URLs del Sistema

| Microservicio | Protocolo / Interfaz | URL Local | Descripción |
| :--- | :--- | :--- | :--- |
| **Solicitudes Service** | HTTP / REST | `http://localhost:8000` | Gestión y registro de solicitudes de donación. |
| **Documentación API** | Swagger UI | `http://localhost:8000/docs` | Interfaz interactiva de los endpoints REST (OpenAPI). |
| **Reservas Service** | gRPC | `localhost:50051` | Gestión de reservas, horarios y disponibilidad. |

---

### Servicios Principales

1. **Solicitudes Service (`/solicitudes-service`)**
   - **Descripción:** Encargado de gestionar las solicitudes de donantes y pacientes.
   - **Componentes clave:** Autenticación (`auth.py`), Base de datos (`database.py`), y lógica principal (`main.py`).

2. **Reservas Service (`/reservas-service`)**
   - **Descripción:** Administra la programación y reservas de las donaciones.
   - **Componentes clave:** Servidor (`server.py`), conexión a BD (`db.py`), y pruebas (`test_reservas.py`).

### Contratos y APIs (`/contracts`)

La comunicación entre servicios y con clientes externos se define mediante:
- **OpenAPI (`openapi.yaml`):** Especificación para las APIs REST.
- **Protocol Buffers (`reservas.proto`):** Definición de mensajes y servicios para comunicación eficiente mediante gRPC.

## Tecnologías Utilizadas

- **Lenguaje:** Python
- **Infraestructura:** Docker & Docker Compose
- **Comunicación:** REST (OpenAPI) / gRPC (Protobuf)
- **Pruebas y Experimentos:** Scripts de experimentación de rendimiento (Timeouts, Tamaño de mensajes).

## Requisitos Previos

Asegúrate de tener instalado lo siguiente en tu máquina local:
- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- Python 3.x

## Instalación y Configuración

1. **Clonar el repositorio:**
   ```bash
   git clone <url-del-repositorio>
   cd Blood-Donation-Management-System
   ```

2. **Configurar las variables de entorno:**
   Copia el archivo de ejemplo de variables de entorno y ajusta los valores según sea necesario.
   ```bash
   cp .env-example .env
   ```

3. **Levantar los servicios con Docker:**
   El proyecto incluye un archivo `docker-compose.yml` preconfigurado para orquestar todos los microservicios.
   ```bash
   docker-compose up --build
   ```

## Experimentos y Rendimiento

El directorio `/experimentos` contiene scripts y configuraciones de Docker Compose (`docker-compose.experimento.yml`) diseñados para medir el rendimiento del sistema bajo diferentes condiciones:

- `exp1_timeout.py`: Pruebas de resiliencia y manejo de tiempos de espera.
- `exp2_tamano_mensajes.py`: Evaluación del impacto del tamaño del payload en la comunicación gRPC/REST.

Los resultados de estas pruebas se almacenan en formato CSV y gráficos PNG dentro de la carpeta `experimentos/resultados/`.

## Para probar los endpoints

La forma más fácil es usar la interfaz web presente en http://localhost:8000/v1/docs

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