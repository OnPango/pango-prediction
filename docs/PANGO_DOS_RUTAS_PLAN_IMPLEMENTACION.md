# PANGO / CAMELLO — Dos rutas de trabajo paralelas del módulo predictivo

## Propósito

Este documento define dos rutas que pueden ejecutarse en simultáneo sin bloquearse:

- **Ruta A — Pruebas de rendimiento de modelos**
- **Ruta B — Implementación del módulo de predicción de PANGO**

No es un cronograma. Es una lista de tareas técnicas, ordenadas paso a paso.

---

# RUTA A — PRUEBAS DE RENDIMIENTO DE MODELOS

## Objetivo

Determinar qué modelos funcionan mejor para el problema real de PANGO a nivel:

```text
producto × bodega
```

La unidad de serie es:

```text
series_id = product_warehouse_id
```

El resultado final será una política productiva versionada:

```text
model_policy.json
```

## A1. Construir el dataset diario correcto

- [ ] Tomar los exports históricos de World Office.
- [ ] Identificar producto, bodega, fecha, cantidad, tipo de documento y saldo.
- [ ] Mantener la definición de demanda vigente:
  - FV cuenta como demanda.
  - DMC no se resta del target principal.
  - REM no se suma para evitar doble conteo.
  - SA y movimientos internos no cuentan como demanda.
- [ ] Identificar desde qué fecha la bodega es confiable.
- [ ] No inventar equivalencias entre bodegas históricas y actuales.
- [ ] Construir una clave por producto + bodega.
- [ ] Mapearla a `product_warehouse_id` cuando exista.
- [ ] Agregar demanda por día y por producto-bodega.
- [ ] Construir stock diario por producto-bodega.
- [ ] Conservar `group2` o categoría para reglas especiales.

### Salida

```text
product_id
warehouse_id
series_id
date
demand_quantity
closing_stock
group2
```

### Terminado cuando

Cada observación utilizable tenga producto, bodega y fecha confiables; lo no confiable quede explícitamente excluido o marcado.

---

## A2. Implementar construcción de series

Esta lógica debe vivir en código Python reutilizable, no solo en el notebook.

- [ ] Crear `series.py`.
- [ ] Definir semana lunes-domingo.
- [ ] Etiquetar cada semana por su lunes de inicio.
- [ ] Agregar demanda diaria a semanal.
- [ ] Completar semanas faltantes.
- [ ] Validar duplicados.
- [ ] Validar fechas.
- [ ] Validar cantidades negativas.
- [ ] Mantener frecuencia `weekly`.
- [ ] Mantener horizonte de 4 semanas.

### Salida

```text
unique_id
ds
y
```

con:

```text
unique_id = product_warehouse_id
```

---

## A3. Implementar disponibilidad y quiebres

- [ ] Crear `availability.py`.
- [ ] Calcular disponibilidad usando stock, no una marca ya resuelta por backend.
- [ ] Implementar:
  - semana sin venta + stock final 0 → censurada;
  - semana con venta + stock final 0 → observada;
  - producto contra pedido → tratamiento especial;
  - más de 13 semanas sin stock → sin surtido.
- [ ] Diferenciar `AVAILABLE`, `CENSORED`, `UNKNOWN`.
- [ ] Generar `available_mask`.

### Salida

```text
unique_id
ds
y
available_mask
availability_state
```

---

## A4. Implementar calendario

- [ ] Crear `calendar.py`.
- [ ] Implementar lunes-viernes = 1.
- [ ] Implementar sábado = 0.6.
- [ ] Implementar domingo = 0.
- [ ] Implementar festivos/cierres = 0.
- [ ] Recibir cierres como configuración.
- [ ] Generar `dias_habiles`.

---

## A5. Crear EDA v2 por producto-bodega

- [ ] Contar series `product_warehouse_id`.
- [ ] Contar productos.
- [ ] Contar bodegas.
- [ ] Medir rango temporal.
- [ ] Medir longitud de historia por serie.
- [ ] Medir porcentaje de ceros.
- [ ] Medir porcentaje censurado.
- [ ] Medir demanda total.
- [ ] Detectar historia corta.
- [ ] Detectar inactivos.
- [ ] Detectar sin surtido.
- [ ] Analizar distribución por bodega.
- [ ] Analizar ABC si aplica.
- [ ] Analizar atípicos sin recortarlos.

### Salida

```text
EDA_v2_product_warehouse.ipynb
```

más un reporte de calidad del dataset.

---

## A6. Implementar perfil de demanda

- [ ] Crear `profile.py`.
- [ ] Calcular ADI.
- [ ] Calcular CV².
- [ ] Agregar semanal → mensual para la clasificación.
- [ ] Usar máximo 24 meses previos al cutoff.
- [ ] Clasificar: suave, errática, intermitente, irregular.
- [ ] Calcular `observed_periods`.
- [ ] Calcular `nonzero_periods`.
- [ ] Detectar `inactive`.
- [ ] Detectar `short_history`.
- [ ] Recalcular la clase en cada cutoff.

### Salida

Perfil por:

```text
unique_id × cutoff
```

---

## A7. Definir backtesting viable

- [ ] Medir cuánta historia confiable hay por bodega.
- [ ] Verificar si caben 13 folds × 4 semanas.
- [ ] Si caben: folds 1–7 selección, folds 8–13 reporte.
- [ ] Si no caben: reducir folds sin cambiar horizonte.
- [ ] Mantener expanding origin.
- [ ] Nunca usar random split.
- [ ] Mantener selección y reporte separados.
- [ ] Documentar las fechas exactas.
- [ ] Generar `backtest_folds.csv`.

---

## A8. Implementar métricas

- [ ] WAPE sobre acumulado de 4 semanas.
- [ ] Bias sobre acumulado de 4 semanas.
- [ ] MAE sobre bloques de 4 semanas.
- [ ] MASE con escala de 4 semanas.
- [ ] Excluir semanas `available_mask=0`.
- [ ] Excluir de la suma tanto forecast como real de semanas censuradas.
- [ ] Reportar cobertura.
- [ ] Reportar por clase.
- [ ] Reportar por ABC.
- [ ] Reportar por bodega.
- [ ] Reportar por serie.
- [ ] Reportar historia corta.

---

## A9. Ejecutar baselines

- [ ] Zero.
- [ ] Naive.
- [ ] Moving Average 8.
- [ ] Seasonal Naive 52 si existe historia suficiente.

### Salida mínima

```text
model
WAPE_4w
Bias_4w
MAE_4w
MASE_4w
coverage
time
```

---

## A10. Ejecutar modelos StatsForecast

- [ ] AutoETS.
- [ ] Croston SBA.
- [ ] TSB.
- [ ] ADIDA.
- [ ] IMAPA.
- [ ] AutoARIMA.
- [ ] Usar StatsForecast directamente.
- [ ] No reimplementar algoritmos manualmente.
- [ ] Fijar versión de librería.
- [ ] Registrar parámetros y tiempos.

---

## A11. Ejecutar Prophet

- [ ] Probar sin yearly seasonality.
- [ ] Probar con yearly seasonality.
- [ ] Probar `dias_habiles` como regresor.
- [ ] Mantenerlo como candidato adicional, no obligatorio.

---

## A12. Ejecutar DeepAR

- [ ] Preparar muchas series `product_warehouse` para entrenamiento global.
- [ ] No entrenar un DeepAR por serie.
- [ ] Probar `input_size`: 13, 26 y 52 cuando sea viable.
- [ ] Probar scaler `robust`.
- [ ] Probar scaler `standard`.
- [ ] Usar 300–500 steps como punto inicial.
- [ ] Incluir `dias_habiles`.
- [ ] Probar mes solo como experimento.
- [ ] Usar Negative Binomial si la demanda es esencialmente entera.
- [ ] Usar distribución continua si existen cantidades fraccionarias relevantes.
- [ ] Registrar seed, GPU, duración, hiperparámetros, checkpoint y versión NeuralForecast.

---

## A13. Comparar y seleccionar

- [ ] Comparar solo con folds de selección.
- [ ] Identificar mejor modelo global.
- [ ] Identificar mejor modelo por clase.
- [ ] Analizar Bias.
- [ ] Analizar estabilidad entre folds.
- [ ] Analizar errores de alto volumen.
- [ ] Analizar historia corta.
- [ ] Verificar si DeepAR supera realmente los baselines.
- [ ] No usar folds de reporte para tuning.

---

## A14. Congelar candidato

- [ ] Elegir modelo/política con folds de selección.
- [ ] Congelar parámetros.
- [ ] Ejecutar una sola vez sobre folds de reporte.
- [ ] Si falla, rechazar `v1-candidate` y abrir `v2-candidate`.
- [ ] No modificar v1 usando información del reporte.

---

## A15. Generar `model_policy.json`

Debe incluir:

```text
policy_version
forecast_level = product_warehouse
frequency
horizon
class → model
parameters
model library
library version
```

Si usa DeepAR, incluir además:

```text
model_ref
checkpoint_version
training_period
artifact_sha256
```

---

## A16. Entregables finales Ruta A

- [ ] `EDA_v2_product_warehouse.ipynb`
- [ ] training bundle product-warehouse
- [ ] `backtest_folds.csv`
- [ ] benchmark reproducible
- [ ] predicciones semanales
- [ ] predicciones 4 semanas
- [ ] métricas selección
- [ ] métricas reporte
- [ ] métricas por clase
- [ ] métricas por bodega
- [ ] métricas por ABC
- [ ] errores por serie
- [ ] tiempos de modelos
- [ ] configuración experimental
- [ ] checkpoint DeepAR si aplica
- [ ] `model_policy.json`
- [ ] reporte de decisión

---

# RUTA B — IMPLEMENTACIÓN DEL MÓDULO DE PREDICCIÓN

## Objetivo

Crear un servicio Python desplegable que reciba desde Pango Core:

```text
demanda + stock + metadata
```

por `product_warehouse_id` y devuelva un forecast de 4 semanas sin acceder directamente a PostgreSQL.

---

## B1. Inicializar repositorio

- [ ] Crear `pango-forecast-service`.
- [ ] Crear `pyproject.toml`.
- [ ] Definir versión de Python.
- [ ] Crear `.gitignore`.
- [ ] Crear `.env.example`.
- [ ] Crear README inicial.
- [ ] Configurar pytest.
- [ ] Configurar formatter/linter.
- [ ] Crear estructura `src/`.

---

## B2. Crear estructura modular

```text
src/pango_forecast/
├── main.py
├── config.py
├── api/
├── application/
├── domain/
├── models/
└── artifacts/
```

Criterio: el dominio debe poder ejecutarse sin FastAPI.

---

## B3. Crear tipos de dominio

En `domain/types.py`:

- [ ] `DemandObservation`
- [ ] `StockObservation`
- [ ] `ForecastSeriesInput`
- [ ] `RegularizedSeries`
- [ ] `DemandProfile`
- [ ] `ForecastPeriod`
- [ ] `ForecastResult`
- [ ] `ModelSpec`

---

## B4. Implementar `series.py`

- [ ] Recibir demanda diaria.
- [ ] Validar fechas.
- [ ] Validar cantidades.
- [ ] Agrupar duplicados diarios.
- [ ] Crear semana lunes-domingo.
- [ ] Completar semanas faltantes.
- [ ] Construir serie regular.
- [ ] Usar `product_warehouse_id` como `series_id`.
- [ ] Crear tests para lunes, martes, domingo, huecos y duplicados.

---

## B5. Implementar `availability.py`

- [ ] Recibir stock diario.
- [ ] Calcular cierre semanal.
- [ ] Implementar AVAILABLE.
- [ ] Implementar CENSORED.
- [ ] Implementar UNKNOWN.
- [ ] Implementar venta + cierre cero.
- [ ] Implementar producto contra pedido.
- [ ] Implementar >13 semanas sin stock.
- [ ] Crear un test por regla.

---

## B6. Implementar `calendar.py`

- [ ] Recibir fechas de cierre.
- [ ] Calcular `dias_habiles`.
- [ ] Implementar sábado 0.6.
- [ ] Implementar domingo 0.
- [ ] Implementar festivos/cierres.
- [ ] Construir calendario futuro para 4 semanas.

---

## B7. Implementar `profile.py`

- [ ] Calcular observed periods.
- [ ] Calcular nonzero periods.
- [ ] Calcular ADI.
- [ ] Calcular CV².
- [ ] Clasificar demanda.
- [ ] Detectar inactive.
- [ ] Detectar short_history.
- [ ] Mantener profile separado de policy.

---

## B8. Crear interfaz de modelos

En `models/base.py` definir una interfaz común:

```python
predict(series, horizon, params)
```

Todos los modelos deben devolver la misma estructura.

---

## B9. Crear Model Registry

En `models/registry.py` registrar:

- [ ] zero
- [ ] naive
- [ ] moving_average_8
- [ ] auto_ets
- [ ] croston_sba
- [ ] tsb
- [ ] adida
- [ ] imapa
- [ ] auto_arima
- [ ] deepar

No usar cadenas extensas de `if/elif`.

---

## B10. Implementar baselines

En `models/baselines.py`:

- [ ] Zero.
- [ ] Naive.
- [ ] Moving Average 8.
- [ ] Seasonal Naive si se comparte en producción.
- [ ] Tests para cada uno.

---

## B11. Implementar wrappers StatsForecast

En `models/statsforecast.py`:

- [ ] AutoETS.
- [ ] CrostonSBA.
- [ ] TSB.
- [ ] ADIDA.
- [ ] IMAPA.
- [ ] AutoARIMA.

Regla: usar StatsForecast directamente; no copiar algoritmos.

---

## B12. Implementar Policy

En `domain/policy.py`:

- [ ] Definir schema JSON.
- [ ] Crear loader.
- [ ] Validar `policy_version`.
- [ ] Validar modelo registrado.
- [ ] Validar parámetros.
- [ ] Resolver `profile → model spec`.
- [ ] Crear temporalmente `development_policy.json`.

La policy definitiva vendrá de Ruta A.

---

## B13. Implementar Application Service

En `application/forecast_service.py`:

```text
input
↓
series
↓
availability
↓
calendar
↓
profile
↓
policy
↓
registry
↓
model
↓
result
```

- [ ] Soportar múltiples series.
- [ ] Manejar fallos parciales.
- [ ] Generar warnings.
- [ ] Incluir `model_version`.
- [ ] Incluir `policy_version`.

---

## B14. Definir contrato HTTP v1

Crear `docs/contract-v1.md`.

Request mínimo:

```text
contract_version
request_id
forecast_run_id
frequency
horizon
history_end
calendar
series[]
```

Por serie:

```text
series_id = product_warehouse_id
product_id
warehouse_id
metadata
demand[]
stock[]
```

---

## B15. Implementar FastAPI

Crear:

```text
api/schemas.py
api/routes.py
api/errors.py
main.py
```

Endpoints:

- [ ] `GET /health`
- [ ] `POST /v1/forecasts`

FastAPI solo adapta HTTP; no contiene lógica de forecasting.

---

## B16. Seguridad servicio-servicio

- [ ] Definir service token.
- [ ] No reutilizar JWT de usuarios.
- [ ] Validar `Authorization`.
- [ ] Configurar secreto por entorno.
- [ ] No guardar secretos en Git.

---

## B17. Manejar errores parciales

Ejemplo esperado:

```text
100 series
99 OK
1 INVALID_INPUT
```

- [ ] Estado por serie.
- [ ] Error code estable.
- [ ] Error message.
- [ ] El batch puede ser válido aunque una serie falle.
- [ ] HTTP error solo para fallos globales.

---

## B18. Implementar DeepAR Training

Crear `training/train_deepar.py`.

- [ ] Recibir training dataset.
- [ ] Entrenar modelo global.
- [ ] Registrar configuración.
- [ ] Registrar seed.
- [ ] Registrar periodo de entrenamiento.
- [ ] Guardar checkpoint.
- [ ] Calcular SHA-256.
- [ ] Crear metadata.
- [ ] Nunca entrenar desde `POST /v1/forecasts`.

---

## B19. Implementar DeepAR Runtime

- [ ] PyTorch.
- [ ] NeuralForecast.
- [ ] Cargar checkpoint.
- [ ] Cachear checkpoint.
- [ ] Recibir series.
- [ ] Ejecutar inferencia.
- [ ] Devolver forecast.
- [ ] Registrar tiempo.
- [ ] Registrar device.
- [ ] Registrar cache hit.

---

## B20. Definir almacenamiento de artefactos

- [ ] Crear bucket privado para modelos.
- [ ] Guardar checkpoints fuera de Git.
- [ ] Guardar metadata.
- [ ] Definir `model_ref`.
- [ ] Asociar versión.
- [ ] Definir descarga.
- [ ] Verificar checksum.

Supabase Storage es candidato natural.

---

## B21. DeepAR multi-tenant

- [ ] Un modelo de EquipMaster no se aplica automáticamente a otra empresa.
- [ ] Definir `model_ref`, por ejemplo `deepar-equipmaster-v1`.
- [ ] Pango Core mantiene asociación tenant → modelo activo.
- [ ] Forecast Service recibe una referencia opaca.

---

## B22. Crear Docker CPU

Crear `docker/Dockerfile.api` con:

- FastAPI;
- StatsForecast;
- domain;
- application;
- policy.

No necesita PyTorch si DeepAR corre separado.

---

## B23. Crear Docker GPU Runtime

Crear `docker/Dockerfile.deepar` con:

- PyTorch compatible con CUDA;
- NeuralForecast;
- runtime DeepAR;
- código de dominio compartido.

---

## B24. Crear Docker GPU Trainer

Crear `docker/Dockerfile.train` para ejecutar `training/train_deepar.py` como batch job.

---

## B25. Integrar con Pango Core

En NestJS:

- [ ] Crear módulo forecasting.
- [ ] Crear HTTP client al Forecast Service.
- [ ] Resolver tenant.
- [ ] Consultar demanda.
- [ ] Consultar stock.
- [ ] Agregar demanda por día.
- [ ] Resolver `product_warehouse_id`.
- [ ] Crear `forecast_run`.
- [ ] Enviar request.
- [ ] Procesar response.
- [ ] Persistir `forecasts`.
- [ ] Persistir `forecast_periods`.
- [ ] Manejar PARTIAL/FAILED.
- [ ] Exponer estado al frontend.

---

## B26. Flujo asíncrono hacia frontend

- [ ] Implementar `POST /forecast-runs`.
- [ ] Responder `202 + run_id`.
- [ ] Implementar `GET /forecast-runs/:id`.
- [ ] No mantener el navegador esperando una inferencia GPU.
- [ ] No introducir RabbitMQ en el MVP salvo necesidad real.

---

## B27. Observabilidad

Registrar:

- [ ] request_id;
- [ ] forecast_run_id;
- [ ] series count;
- [ ] policy version;
- [ ] model;
- [ ] model version;
- [ ] duration;
- [ ] status.

DeepAR además:

- [ ] GPU device;
- [ ] checkpoint;
- [ ] cache hit;
- [ ] checkpoint load time;
- [ ] inference time.

Usar logs JSON.

---

## B28. Testing

### Unit

- [ ] series;
- [ ] availability;
- [ ] calendar;
- [ ] profile;
- [ ] policy;
- [ ] registry;
- [ ] modelos.

### Integration

- [ ] request → forecast service → result.

### API

- [ ] health;
- [ ] forecast válido;
- [ ] serie inválida;
- [ ] partial success;
- [ ] unauthorized.

### Golden test privado

- [ ] comparar construcción de series contra EDA.

---

## B29. CI/CD

### CPU

- [ ] tests;
- [ ] lint;
- [ ] type checks;
- [ ] Docker build;
- [ ] integration test;
- [ ] deploy.

### GPU

- [ ] tests;
- [ ] Docker GPU build;
- [ ] inference smoke test;
- [ ] deploy runtime.

### Trainer

- [ ] smoke training sintético;
- [ ] no entrenamiento completo en CI.

---

## B30. Entregables finales Ruta B

- [ ] repositorio `pango-forecast-service`;
- [ ] domain compartido;
- [ ] wrappers StatsForecast;
- [ ] policy loader;
- [ ] Forecast Service;
- [ ] FastAPI;
- [ ] contrato HTTP v1;
- [ ] DeepAR trainer;
- [ ] DeepAR GPU runtime;
- [ ] Dockerfiles;
- [ ] tests;
- [ ] documentación;
- [ ] integración NestJS;
- [ ] observabilidad;
- [ ] CI/CD.

---

# PUNTOS DE INTEGRACIÓN ENTRE AMBAS RUTAS

Ambas rutas deben compartir exactamente:

```text
domain/series.py
domain/availability.py
domain/calendar.py
domain/profile.py
models/
```

Ruta A produce:

```text
model_policy.json
checkpoint DeepAR candidato
```

Ruta B los consume.

---

# TRABAJO QUE PUEDE EMPEZAR YA SIN ESPERAR

## Ruta A

- [ ] identificar historia confiable por bodega;
- [ ] construir dataset diario product-warehouse;
- [ ] medir calidad;
- [ ] crear EDA v2;
- [ ] determinar backtest viable.

## Ruta B

- [ ] inicializar repositorio;
- [ ] crear `types.py`;
- [ ] implementar `series.py`;
- [ ] implementar `availability.py`;
- [ ] implementar `calendar.py`;
- [ ] implementar `profile.py`;
- [ ] crear tests.

Ninguna ruta necesita esperar a la otra para comenzar.

---

# Regla final

```text
RUTA A
Evalúa y decide
      ↓
model_policy + checkpoint
      ↓
RUTA B
Ejecuta y sirve
```

Ambas deben cumplir:

```text
misma serie
+
misma lógica
+
mismo modelo
=
lo evaluado es lo desplegado
```
