# PANGO Prediction

Servicio HTTP de pronóstico de demanda por **producto × almacén**
(`series_id = product_warehouse_id`), con semanas de lunes a domingo y horizonte
de **4 semanas**.

**Ruta A evalúa y selecciona la policy; Ruta B (este repositorio) la ejecuta.**
Pango Core envía demanda, stock y calendario, y persiste los resultados.
El servicio no consulta la base de datos ni calcula alertas de inventario.

## Instalar

Requiere **Python 3.12**. Ejecuta los comandos desde la raíz del repositorio.

```text
python -m venv .venv
```

Activa el entorno en cada terminal:

```powershell
# Windows / PowerShell
.\.venv\Scripts\Activate.ps1
```

Si PowerShell bloquea la activación, habilítala solo para esa terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS (bash)
source .venv/bin/activate
```

Instala las dependencias y herramientas de desarrollo desde `pyproject.toml`:

```text
python -c "import subprocess, sys, tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); subprocess.check_call([sys.executable, '-m', 'pip', 'install', *p['project']['dependencies'], *p['dependency-groups']['dev']])"
```

## Ejecutar

Configura el token compartido con Core y la policy de Ruta A:

```powershell
# Windows / PowerShell
$env:PANGO_PREDICTION_SERVICE_TOKEN = Read-Host 'Token del servicio'
$env:PANGO_PREDICTION_POLICY_PATH = 'config/model_policy.json'
python -m uvicorn pango_prediction.main:create_app --factory --host 127.0.0.1 --port 8000
```

```bash
# Linux / macOS (bash)
read -r -s -p 'Token del servicio: ' PANGO_PREDICTION_SERVICE_TOKEN
export PANGO_PREDICTION_SERVICE_TOKEN
export PANGO_PREDICTION_POLICY_PATH=config/model_policy.json
python -m uvicorn pango_prediction.main:create_app --factory --host 127.0.0.1 --port 8000
```

Detén el servicio con `Ctrl+C`. Para recarga automática durante desarrollo,
añade `--reload` al comando de Uvicorn.

| Variable | Comportamiento |
|---|---|
| `PANGO_PREDICTION_SERVICE_TOKEN` | Obligatoria. Credencial de servicio compartida con Core. |
| `PANGO_PREDICTION_POLICY_PATH` | Selecciona la policy. Sin ella se carga `config/development_policy.json`. |
| `PANGO_PREDICTION_MAX_SERIES_PER_REQUEST` | Límite por petición; predeterminado: `500`. |
| `PANGO_PREDICTION_LOG_LEVEL` | Nivel de logs; predeterminado: `INFO`. |

La policy entregada es **`ruta-a-baseline-v1-inactive`**: inactivas sin pronóstico;
el resto usa `moving_average(window=4)`. Una ruta de policy explícita inválida
impide el arranque. Las variables se leen del entorno; el servicio no carga
un archivo `.env` automáticamente.

## Comprobar la API

Con el servicio activo, abre:

- Salud: <http://127.0.0.1:8000/health> (sin autenticación).
- Swagger: <http://127.0.0.1:8000/docs>.

En otra terminal PowerShell, introduce el mismo token y envía el ejemplo:

```powershell
$env:PANGO_PREDICTION_SERVICE_TOKEN = Read-Host 'El mismo token del servicio'
Invoke-RestMethod http://127.0.0.1:8000/health
$payload = Get-Content tests/fixtures/forecast_request_minimal.json -Raw | ConvertFrom-Json
$payload.contract_version = '1.2'
$body = $payload | ConvertTo-Json -Depth 100
$response = Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/forecasts -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $env:PANGO_PREDICTION_SERVICE_TOKEN" } -Body $body
$response | ConvertTo-Json -Depth 100
```

Comprueba que `policy_version` sea `ruta-a-baseline-v1-inactive`. Los endpoints
`POST /v1/forecasts` y `POST /v1/forecast-evaluations` requieren
`Authorization: Bearer <token>`.

Core debe declarar contrato **`1.2`** para recibir `no_forecast` y enviar
`warehouses[]` con `warehouse_id` y `holds_stock`: un almacén omitido se trata
como de stock. MA4 con menos de cinco semanas observables todavía devuelve
`MODEL_FAILED`. Consulta el [contrato HTTP](docs/contract-v1.md) para los detalles.

## Docker

Requiere Docker activo. Con el token ya exportado en la terminal:

```text
docker build -f docker/Dockerfile.api -t pango-prediction .
docker run --rm -p 127.0.0.1:8000:8000 -e PANGO_PREDICTION_SERVICE_TOKEN -e PANGO_PREDICTION_POLICY_PATH=/app/config/model_policy.json pango-prediction
```

La imagen CPU incluye la policy y StatsForecast; no instala el runtime DeepAR.

## Controles de desarrollo

Con el entorno virtual activo:

```text
python -m pytest
ruff check .
python -m mypy pango_prediction tests
```

CI ejecuta estos controles y construye y prueba la imagen Docker. El despliegue
no es automático.

## Documentación

- [Arquitectura hexagonal](docs/architecture.md): dominio, aplicación, puertos y adaptadores.
- [Reglas del dominio](docs/domain-rules.md).
- [Policy de Ruta A y trazabilidad](docs/route-a/baseline-2026-11-16/README.md).
- [Contexto del proyecto](docs/PREDICTION_CONTEXT.md) y [plan de las dos rutas](docs/PANGO_DOS_RUTAS_PLAN_IMPLEMENTACION.md).
- [Bitácora de cambios](docs/worklog/README.md).
