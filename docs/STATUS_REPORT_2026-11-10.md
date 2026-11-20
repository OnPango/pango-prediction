# PANGO Prediction — Estado del repositorio

**Fecha:** 10 de noviembre de 2026  
**Contrato servido:** v1.1  
**Frecuencia:** semanal  
**Horizonte:** 4 semanas

## Resumen ejecutivo

El servicio Python recibe demanda y stock diarios por producto-bodega, construye
la serie semanal de lunes a domingo, clasifica el comportamiento, resuelve una
política versionada y devuelve un forecast semanal.

La integración HTTP está lista para que Pango Core consuma el servicio. Cada
serie exitosa devuelve cuatro períodos semanales y un total acumulado con su
propio intervalo. Las series con errores no cancelan el resto del lote.

El total acumulado se implementó para el contrato actual. Los baselines calculan
su intervalo del horizonte usando sus residuos calibrados escalados al horizonte;
no se suman los límites semanales.

## Flujo implementado

```text
Pango Core
  demanda diaria filtrada por counts_as_demand
  + stock diario
        ↓
POST /v1/forecasts
        ↓
validación HTTP
  → semanas lunes-domingo
  → disponibilidad y censura
  → perfil mensual
  → política
  → modelo baseline
        ↓
4 ForecastPeriod + total del horizonte
        ↓
Pango Core persiste y calcula alertas/puntos de reorden
```

## Capacidades verificadas

- contrato HTTP v1.1;
- autenticación Bearer;
- demanda diaria y stock diario como entrada;
- agregación semanal;
- clasificación mensual;
- política de desarrollo `development-1`;
- modelos `zero`, `naive` y `moving_average`;
- intervalos por semana;
- intervalo propio del total de cuatro semanas;
- errores por serie y respuestas parciales;
- metadatos de modelo y política;
- validación de identificadores y campos desconocidos;
- imagen Docker configurada para incluir la política.

## Validaciones ejecutadas

| Comando | Resultado |
|---|---|
| `python -m pytest` | 276 pruebas pasaron después del cambio del total |
| pruebas focalizadas del forecast | 71 pasaron |
| `python -m mypy pango_prediction tests` | correcto |
| `ruff check .` | correcto |
| `python -m pip check` | sin dependencias rotas |
| `docker build -f docker/Dockerfile.api -t pango-prediction:local .` | no ejecutado correctamente: Docker Desktop no tenía activo el daemon Linux |

## Pendientes para terminar end-to-end

### Pango Core — prioritarios

1. Implementar el cliente HTTP hacia `POST /v1/forecasts`.
2. Construir la demanda con `counts_as_demand = true`.
3. Enviar stock diario confiable por producto-bodega.
4. Crear y actualizar `ForecastRun`.
5. Persistir `Forecast` y cuatro `ForecastPeriod`.
6. Persistir `total.estimate`, `total.lower` y `total.upper`.
7. Persistir errores por serie.
8. Representar ejecuciones parciales.
9. Implementar idempotencia por `request_id`.
10. Usar el total de cuatro semanas para alertas y puntos de reorden.

### Datos y operación

- resolver la reconstrucción o persistencia del histórico diario de stock;
- definir cuándo se dispara el forecast, probablemente al cierre de cada semana;
- validar la fórmula de alertas, stock de seguridad y punto de reorden en Core.

### Route A / modelos

- producir la política estadística final;
- ejecutar backtesting y congelar métricas;
- reemplazar la política de desarrollo;
- entrenar y versionar DeepAR cuando aplique;
- hacer que cada modelo productivo entregue un intervalo calibrado del total
  del horizonte.

### Pendientes no bloqueantes del MVP

- fechas de cierre administradas por almacén;
- calendario administrativo persistido;
- artefactos de entrenamiento en almacenamiento privado;
- build y smoke test Docker con Docker Desktop activo.

## Decisiones cerradas

- El forecast es semanal, no diario.
- Una semana va de lunes a domingo.
- El horizonte es de cuatro semanas.
- La entrada conserva granularidad diaria.
- Prediction agrega temporalmente; Core conserva semántica de movimientos.
- Prediction no calcula alertas ni órdenes de compra.
- Las fechas de cierre quedan fuera del MVP y son opcionales.
