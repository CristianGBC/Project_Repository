# Despliegue físico de YachayRepo · Fase 8.3

| Equipo y usuario | Arquitectura | Dirección | Servicios y puertos |
|---|---|---|---|
| Angela-Dell-G15-5515 · angela | Ubuntu AMD64 | 172.23.210.142 | Frontend 5173, API 8000, monitor, reparación, reconciliación |
| IdeaPad-5 · cristian | Ubuntu AMD64 | 172.23.212.21 | PostgreSQL existente 5432, Agroindustria 8006 |
| bio-phy · bio-phy | Raspberry Pi 4 ARM64 | 172.23.195.111 | node-bio 8001 y node-phy 8002, raspberry-1 |
| math · math | Raspberry Pi 4 ARM64 | 172.23.193.80 | node-math 8003, raspberry-2 |
| chem-earth · chem-earth | Raspberry Pi 4 ARM64 | 172.23.203.107 | node-chem 8004 y node-earth 8005, raspberry-3 |

Agroindustria conserva raspberry-4 como identificador de anticolocación. El catálogo físico registra IdeaPad-5 como equipo Ubuntu. Cada nodo nuevo tiene su propio volumen. En las Pi verifica con `uname -m` que el sistema sea AArch64 y dispone de Docker, Compose v2, espacio suficiente y alimentación estable. Construye de forma nativa. Para tarjetas microSD conviene usar SSD para los datos Docker. Los logs de contenedores rotan con tres archivos de hasta 5 MB.

## Preparación en cada equipo

Ejecuta desde la raíz del mismo checkout. Selecciona solo el perfil físico de ese dispositivo mediante su carpeta.

```bash
# Angela-Dell: host=coordinator
# IdeaPad: host=agro
# Raspberry bio-phy: host=bio-phy
# Raspberry math: host=math
# Raspberry chem-earth: host=chem-earth
host=coordinator
dir="infrastructure/deployment/$host"
umask 077
cp -n "$dir/.env.example" "$dir/.env"
editor "$dir/.env"
dc() { docker compose --env-file "$dir/.env" -f "$dir/compose.yaml" "$@"; }
dc config --quiet
dc config --services
```

En todos los hosts ajusta `BIND_IP` a la interfaz local y configura `STORAGE_INTERNAL_TOKEN` y `REPAIR_INTERNAL_TOKEN` con valores distintos entre sí y coincidentes con los del coordinador físico. Ajusta `MAX_UPLOAD_SIZE_MB` en todos si cambias el límite. Los nodos no reciben credenciales SQL ni JWT.

En Angela-Dell configura `DATABASE_URL` con el driver `postgresql+psycopg`, los datos autorizados de la base y 172.23.212.21:5432. Codifica caracteres especiales de las credenciales en la URL. Genera un `AUTH_JWT_SECRET` independiente de al menos 32 caracteres. Opcionalmente configura los dominios permitidos mediante `AUTH_ALLOWED_EMAIL_DOMAINS`. Conserva RF=3 mediante `REPLICATION_FACTOR`. La limpieza automática de réplicas excedentes está deshabilitada inicialmente. Configura `VITE_API_BASE_URL` con el origen de la API y `CORS_ALLOWED_ORIGINS` con los orígenes exactos desde donde se abre el frontend. Nginx sirve el build con fallback para las rutas de la SPA. El visor PDF y las transferencias pasan por FastAPI. Usa un `DEPLOY_TAG` distinto por versión si necesitas conservar imágenes de rollback.

Comprueba IP e interferencias entre rutas Docker y 172.23.192.0/18 en cada host.

```bash
ip -br -4 addr
ip -4 route
docker network ls -q | xargs -r docker network inspect --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
```

Si hay solapamiento, acuerda un rango libre con el equipo y configura IPAM para el proyecto físico correspondiente antes de iniciarlo. Conserva las redes existentes. Repite las comprobaciones si DHCP cambia IPs. Actualiza BIND_IP, URL del frontend, CORS, DATABASE_URL si corresponde, reglas de acceso y las NODE_*_URL propuestas en el .env de Angela-Dell. Reconstruye el frontend si cambia la URL de la API.

## Orden de arranque y catálogo

1. Mantén PostgreSQL en IdeaPad-5. Desde Angela-Dell, prueba TCP con `nc -vz 172.23.212.21 5432`. Con Compose de coordinador, construye `dc build coordinator frontend` y compara revisiones usando:

```bash
dc run --rm --no-deps --entrypoint python coordinator -m alembic -c backend/alembic.ini current
dc run --rm --no-deps --entrypoint python coordinator -m alembic -c backend/alembic.ini heads
```

Si hay migraciones pendientes, acuerda ventana y respalda y verifica una restauración de prueba antes de ejecutar el mismo comando con `upgrade head`. En una base limpia autorizada, ejecuta después `dc run --rm --no-deps --entrypoint python coordinator -m backend.app.seed`. [Respaldo, restauración y pruebas aisladas](postgresql.md).

2. Inicia los nodos en sus carpetas con `dc up -d --build`. Agro usa **8106 para pruebas paralelas**. Conserva intacto el servicio anterior que ocupa 8006. Para probar el nodo nuevo, usa 8106 también en NODE_AGRO_URL y solo en el catálogo aislado. Para el despliegue definitivo acuerda reutilizar el servicio existente en 8006 tras comprobar sus archivos y tokens, o liberar ese puerto en una ventana autorizada y configurar AGRO_PORT=8006 para el nodo nuevo.

3. Comprueba desde Angela-Dell `curl -fsS http://IP:PUERTO/health` para cada nodo de la tabla. En el coordinador, revisa el plan antes de aplicarlo.

```bash
dc run --rm --no-deps --entrypoint python coordinator -m backend.app.physical_catalog preview
dc run --rm --no-deps --entrypoint python coordinator -m backend.app.physical_catalog apply
```

El comando compara la distribución lógica y cambia direcciones, puertos y la descripción de raspberry-4. Conserva IDs, asociaciones y estados. Si hay referencias y cambia la dirección o el puerto, rechaza la operación. Usa `apply --verified-replicas` únicamente tras verificar la disponibilidad e integridad de las réplicas en el destino y detener las escrituras y workers durante el corte. Los cuatro procesos cargan los destinos del catálogo al arrancar. Las NODE_*_URL del archivo local son la propuesta del comando de administración. Usa `dc restart coordinator health-monitor replica-repair upload-reconciler` después de cambiar el catálogo con la pila activa.

4. En Angela-Dell ejecuta `dc up -d --no-build coordinator frontend`. Revisa `curl -fsS http://172.23.210.142:8000/health`, Swagger en `/docs` y el frontend en `http://172.23.210.142:5173`. Comprueba el listado de nodos y arranca `dc up -d --no-build health-monitor replica-repair upload-reconciler`. No ejecutes duplicados de los workers contra el mismo catálogo. Prueba login, subida, visor, descarga y SHA-256 con datos de ensayo.

## Datos, operación y rollback

- **Despliegue limpio.** Usa una base aislada autorizada y volúmenes con otro nombre de proyecto, por ejemplo mediante `dc -p yachayrepo-physical-lab` en todos los comandos. Usa puertos libres si hay servicios paralelos. Aplica migraciones, seed y catálogo únicamente sobre la base de ensayo. Prepara al menos tres hosts de almacenamiento para validar RF=3.
- **Migración de datos existentes.** Inventaría los archivos y metadatos por nodo y versión. Respalda SQL y volúmenes, detén escrituras y workers en la ventana acordada, copia los datos completos y verifica hashes, permisos y disponibilidad de cada réplica antes de cambiar endpoints. Guarda las direcciones anteriores y el resultado del preview. Un volumen nuevo empieza vacío.
- **Estado y logs.** Ejecuta `dc ps`, `dc logs --tail=100` o `dc logs --tail=100 SERVICIO`. Usa `dc stop` para detener el proyecto seleccionado preservando contenedores y volúmenes. Para actualizar, conserva las imágenes y configuración anteriores, construye con el nuevo checkout y usa `dc up -d` en nodos o `dc build coordinator frontend` seguido de `dc up -d --no-build` en Angela-Dell.
- **Rollback.** Detén escrituras y workers. Revierte los endpoints con un plan revisado hacia los volúmenes previos verificados. Restaura el checkout, variables y las imágenes compatibles. Si el esquema también cambió, usa el procedimiento de restauración acordado con respaldo verificado y conserva los datos escritos desde el corte. Comprueba salud, lectura e integridad antes de reanudar.
- **Fallos frecuentes.** Un puerto ocupado en IdeaPad indica que debes conservar 8106 para el ensayo. Un rechazo SQL requiere verificar escucha, `pg_hba.conf` y la IP de origen observada por PostgreSQL. Un 401 interno requiere comprobar ambos tokens en sus extremos. Un fallo CORS requiere el origen exacto del navegador. Un nodo saludable pero sin archivos requiere revisar su volumen y los datos copiados antes de habilitar escrituras.
- **Seguridad.** Permite PostgreSQL y puertos de almacenamiento únicamente desde los consumidores autorizados. En Linux verifica el filtrado de puertos publicados por Docker mediante las reglas de forwarding compatibles con su backend de firewall. Restringe 5173 y 8000 a los clientes autorizados. HTTP expone credenciales, JWT, tokens internos y documentos al tráfico que pueda capturarlos. Usa una red de laboratorio privada, VPN o TLS con un proxy de confianza. Antes de un despliegue físico verifica desde un equipo no autorizado que el acceso quede bloqueado.

## Validación física pendiente

En IdeaPad-5 las rutas Docker observadas no se solaparon con
`172.23.192.0/18`. Comprueba las rutas en los otros equipos. La imagen
`python:3.14-slim` publica manifiestos AMD64 y ARM64. El build de almacenamiento
AMD64 se completó. El intento de build cruzado ARM64 terminó con `exec format
error` al iniciar el intérprete de comandos de la imagen, antes de instalar
dependencias. Queda pendiente validar las dependencias y arrancar los nodos
con builds nativos ARM64.

Construye y prueba cada nodo en su Pi ARM64. En 8.4 valida inventario, reglas de red, persistencia, catálogo e integridad en un ensayo. En 8.5 verifica RF=3 en tres IDs físicos, fallo completo de una Pi con dos nodos, descarga y hash, reparación, reconciliación y recuperación tras reinicio antes del corte definitivo. Las fases 7.5 y 7.6 permanecen aplazadas.
