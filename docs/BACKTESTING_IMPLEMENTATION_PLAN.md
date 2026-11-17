# Plan ejecutable: desempeño histórico por producto y almacén

Fecha: 2026-11-17. Repositorio: `pango-prediction`, Ruta B.
Estado: plan para implementación; no describe una funcionalidad ya terminada.

## 1. Encargo para Claude

Implementa un backtesting offline de la policy existente: para cada serie y corte
histórico, usa únicamente la historia disponible hasta el corte, pronostica las
cuatro semanas siguientes y compara con su demanda real observable. Exporta
detalle auditable y resúmenes de error por serie. Inspecciona el repositorio
completo para identificar integraciones y regresiones, pero limita las ediciones
a los archivos necesarios. No reestructures el proyecto entero.

Lee este documento completo y `AGENTS.md` antes de trabajar. Verifica el código
vivo: los nombres de archivos propuestos son orientativos. Implementa, prueba,
documenta y publica una PR revisable siguiendo el flujo del repositorio. No te
limites a devolver otro plan. Nunca mezcles ni despliegues por cuenta propia.

Este plan no autoriza cambiar modelos, parámetros, selección de policy, reglas
de disponibilidad, clasificación ni el freeze de Ruta A. Tampoco autoriza acceder
a datos privados que no se hayan proporcionado. Usa datos sintéticos para las
pruebas y solicita la ubicación de los datos reales solo si no están disponibles.

La lectura obligatoria incluye:

- `README.md`, `docs/PREDICTION_CONTEXT.md` y `docs/domain-rules.md`.
- `docs/PANGO_DOS_RUTAS_PLAN_IMPLEMENTACION.md` y `docs/contract-v1.md`.
- `docs/route-a/criteria.md` y `docs/route-a/baseline-2026-11-16/README.md`.
- `docs/worklog/README.md` y `.github/pull_request_template.md`.
- El servicio, dominio, adapters y pruebas mencionados en este plan.

## 2. Resultado esperado y límites

Una persona debe poder ejecutar un comando con un archivo de historia diaria,
una policy y cortes explícitos, y obtener:

1. Pronósticos originales, perfiles y estados por serie y corte.
2. Demanda real, disponibilidad y elegibilidad de cada semana de validación.
3. MAE, WAPE, sesgo y cobertura semanal por corte y por serie.
4. Cobertura del intervalo total solo para horizontes totalmente observables.
5. Contadores de evidencia y de resultados excluidos, fallidos o sin pronóstico.
6. Un manifiesto reproducible con versiones, hashes y configuración.

Se evalúa el método seleccionado, no se busca otro ganador. No se vuelve a
ejecutar ni se reemplaza la corrida única oficial de Ruta A. No se modifica el
endpoint de pronóstico ni se ejecuta el backtesting dentro de una petición HTTP.
Core, base de datos, frontend y automatizaciones de producción quedan fuera de
esta primera entrega. El reporte resultante preparará su integración posterior.

No expresar `100 - WAPE` como precisión; no inventar un porcentaje de confianza,
un semáforo de confiabilidad ni intervalos de confianza estadísticos. La cobertura
empírica de un intervalo predictivo no es una garantía para el próximo caso.

## 3. Estado comprobado que debes volver a verificar

| Elemento | Implementación actual |
|---|---|
| Flujo productivo | `application/forecast_service.py`: `ForecastService.run(ForecastCommand)` |
| Entrada de series | `domain/types.py`: `ForecastSeriesInput` con IDs, demanda y stock diarios |
| Semana completa | `domain/series.py`: lunes–domingo; descarta semana final incompleta |
| Disponibilidad | `domain/availability.py`: `build_weekly_availability`, `observed_demand` |
| Perfil | `domain/profile.py`: clasificación mensual y atributos semanales |
| Métricas | `domain/evaluation.py`: `evaluate_forecast`, `EvaluationPeriod`, `EvaluationTotal` |
| Evaluación HTTP | `adapters/inbound/api/routes.py`, `/v1/forecast-evaluations` |
| Policy entregada | `config/model_policy.json`: `ruta-a-baseline-v1-inactive` |
| Registro | `adapters/outbound/registry.py`: `default_registry` |
| Pruebas existentes | `tests/unit/domain/test_evaluation.py`, `tests/api/test_forecast_evaluation_api.py`, pruebas de aplicación y fixtures |

La policy rechaza inactivas y usa MA4 para el resto. MA4 requiere cinco semanas
observables. La insuficiencia sigue siendo MODEL_FAILED; su cambio operativo
quedó para después del 20 de noviembre. No lo adelantes como parte de este trabajo.
`evaluable` usa ocho semanas y no sustituye el mínimo específico de MA4.

Los IDs HTTP son enteros; internamente el dominio usa strings. Sigue la conversión
existente, sin inventar IDs ni deducir producto/almacén a partir de sus dígitos.

## 4. Diseño dentro de la arquitectura

Propuesta mínima; ajusta nombres si existe una convención mejor:

| Archivo | Responsabilidad |
|---|---|
| `application/backtest_service.py` | Comando, resultados y coordinación de cortes sobre ForecastService |
| `domain/evaluation.py` o módulo pequeño contiguo | Acumulación reusable de métricas si hace falta |
| `adapters/inbound/cli/backtest.py` | argparse, lectura JSON, carga de policy/registro y salida JSON/CSV |
| `tests/unit/application/test_backtest_service.py` | Cortes, filtrado temporal, estados y agregación |
| `tests/unit/domain/test_evaluation.py` | Nuevas reglas matemáticas estrictamente necesarias |
| `tests/integration/test_backtest_cli.py` | Ejecución del comando y validación de archivos |
| `tests/fixtures/backtest/` | Datos sintéticos pequeños y casos de entrada inválida |
| `docs/backtesting.md` | Cómo ejecutar, interpretar y reproducir |

Reutiliza ForecastService mediante inyección y las funciones del dominio.
Una dataclass sirve para transportar comandos/resultados; no crees fábricas,
frameworks de jobs ni nuevos ports sin una necesidad concreta. La aplicación no
importa FastAPI, argparse, archivos ni clases concretas de modelos. La CLI puede
componer dependencias como hace `main.py`. Usa solo biblioteca estándar y
dependencias existentes; no se necesita pandas, PyTorch ni otra librería ML.

## 5. Entrada y configuración

Diseña una entrada JSON versionada, documentada y validada estrictamente. Como
mínimo incluirá:

- `schema_version` y un identificador de dataset, sin credenciales.
- `data_start` y `data_end`: límites declarados de extracción completa.
- Demanda y stock diarios con la misma semántica que ForecastSeriesInput.
- Series con `series_id`, `product_id`, `warehouse_id`, metadata e history_only.
- Calendario y configuración `holds_stock` de almacenes.
- Lista de cortes explícitos, todos domingo, únicos y ordenables.
- Horizonte fijo de cuatro semanas compatible con la policy.

No presentes esta entrada como un contrato ya existente. Define el esquema
final, una fixture y un ejemplo completo ejecutable en la documentación.
Puedes reutilizar schemas/validadores existentes si no acoplas aplicación a HTTP.

Reglas de validación:

1. Fechas ISO y números finitos; demanda no negativa. Stock: conserva las reglas
   existentes, no agregues una prohibición de stock negativo sin inspeccionarlas.
2. No coercionar silenciosamente bools o floats a identificadores enteros.
3. IDs de serie únicos y relación consistente de producto/almacén en todo el dataset.
4. No inferir identidad real ni pertenencia a tenant: son responsabilidad de Core.
5. Toda observación queda dentro de la cobertura declarada. Cualquier corte y su
   horizonte completo deben quedar dentro de ella.
6. En esta versión exige separación de al menos cuatro semanas entre cortes.
   Rechaza solapamientos; no dupliques evidencia silenciosamente.
7. Exige configuración explícita de almacenes evaluados, o registra el supuesto
   existente con advertencia inequívoca. No presentes el supuesto como dato verificado.
8. Una extracción incompleta no convierte ausencia de filas en demanda cero.
   La declaración de cobertura es una precondición, no algo verificable solo con ventas.

Los datos son exportados y estáticos. Declara que las fechas de evento no prueban
cuándo se conocieron los datos: correcciones retroactivas y configuraciones sin
versionado limitan la fidelidad histórica. No afirmes backtesting point-in-time
perfecto sin snapshots o información de vigencia.

## 6. Ejecución temporal y prevención de fuga de información

Para cada corte C, domingo:

1. Filtra por fecha demanda y stock, conservando solo observaciones <= C.
2. Construye una entrada independiente; no muta el dataset original.
3. Ejecuta ForecastService con history_end=C, policy y parámetros verificados.
4. Recalcula series, stock handling, perfil e inactividad dentro del flujo existente.
5. Guarda el resultado completo antes de calcular cualquier etiqueta de validación.
6. Evalúa los lunes C+1, C+8, C+15 y C+22; la validación termina el domingo C+28.

No uses una availability/profile calculada hasta data_end para los pronósticos
anteriores. No filtres series por actividad, éxito o ventas futuras. Una serie
sin historia al corte debe tener un resultado registrado, no desaparecer.

Los cierres futuros de calendario solo son válidos como covariables si podían
conocerse al corte; si no existe esa trazabilidad, declara la limitación. No uses
configuración actual como si fuera histórica sin identificar ese supuesto.

## 7. Observabilidad de la validación: decisión explícita

Mantén separados datos usados para pronosticar y etiquetas retrospectivas.
El método propuesto para esta entrega diagnóstica es:

- Por cada corte, construir la serie y disponibilidad de etiquetado usando
  historia únicamente hasta C+28, nunca hasta el final global del dataset.
- Reutilizar `build_weekly_series` y `build_weekly_availability` y seleccionar
  de su resultado las cuatro semanas posteriores a C.
- Guardar en cada fila el estado y la máscara, además de
  `labeling_method = retrospective_domain_through_horizon_end_v1`.
- Mantener la semántica actual: CENSORED y OUT_OF_ASSORTMENT no observables;
  UNKNOWN permanece observable. Contar UNKNOWN por separado en el reporte.

Esta es una propuesta de método diagnóstico, no una nueva regla de negocio
confirmada por Ruta A. Expón su alcance antes de implementarla. Si los documentos
vigentes fijan otro método de validación, identifica y resuelve la discrepancia
antes de cambiar el comportamiento; no elijas silenciosamente.

La disponibilidad actual depende de la última venta y clasifica episodios con
su longitud; limitar el etiquetado al final del horizonte puede diferir de una
etiqueta calculada con más historia. Registra esa limitación. Usar datos futuros
para etiquetas no autoriza usarlos para entrenamiento o selección.

Cuando una serie no permita construir etiquetas válidas, registra la causa.
No inventes todas las etiquetas como true. No dupliques el algoritmo de stock
en la CLI, tests o notebooks.

## 8. Métricas y compatibilidad del evaluador

Para semanas observables, error = actual - estimate:

```text
MAE  = suma de errores absolutos / semanas evaluadas
WAPE = suma de errores absolutos / suma de demanda real
Bias = suma de errores firmados / suma de demanda real
Cobertura semanal = semanas dentro de [lower, upper] / semanas evaluadas
```

Bias positivo significa subestimación neta. WAPE y Bias son null cuando la suma
real es cero; MAE sigue definido si hay semanas evaluadas. Sin semanas observables,
las métricas de desempeño son null y los contadores explican la ausencia.

MASE es secundario y opcional para este MVP: usa solo demanda de entrenamiento
observable anterior al corte y la escala existente de cuatro semanas. No mezcles
escalas por serie/corte ni inventes un MASE agregado promediando ratios.

Cobertura del total: compara demanda de las cuatro semanas con el intervalo total
original solo cuando las cuatro son observables. No sumes límites semanales para
reemplazar el intervalo total ya calculado por el modelo.

Discrepancia conocida que debes comprobar: `evaluate_forecast` actualmente compara
el total real observable parcial con el intervalo completo; una prueba existente
documenta ese comportamiento. Para este backtesting NO cambies silenciosamente
el endpoint HTTP existente. Usa `total=None` para evaluar cortes incompletamente
observables y exporta `total_inside_interval=null` con una causa explícita.
Una corrección del contrato HTTP sería trabajo separado, con análisis de impacto.

Valida finitud y coherencia de números en las fronteras nuevas. Si endurecer el
validador compartido cambia el contrato público, documenta ese impacto y separa
el cambio cuando corresponda. No ocultes una regresión actualizando expectativas.

## 9. Estados, exclusiones y denominadores

Mantén dos dimensiones distintas:

- Resultado del pronóstico: ok / no_forecast / error, con motivo o código original.
- Resultado de evaluación: evaluado / sin semanas observables / no aplicable,
  con razón explícita y contadores.

No evalúes un no_forecast o error como si fuera un pronóstico cero. No les asignes
MAE=0 ni cobertura=100 %. Conserva conteos de inactivas, history_only, almacenes
sin stock, historias insuficientes, errores y series sin historia al corte.

Los resúmenes principales describen pronósticos ejecutados y sus semanas
observables. Incluye también la proporción de pares serie-corte pronosticados
sobre todos los solicitados, con numerador y denominador visibles. Así no ocultas
series difíciles detrás de buenas métricas de los casos exitosos.

La convención oficial de Ruta A de contar determinados casos como f=0 es distinta.
No mezcles este reporte operativo con aquellos criterios ni afirmes que reproduce
sus resultados. Cualquier resumen adicional con esa convención requiere etiqueta
y especificación separada.

## 10. Agregación, archivos y trazabilidad

Por serie y para el conjunto acumula componentes: semanas observables, suma real,
suma estimada, suma de errores absolutos, suma de errores firmados y aciertos de
intervalo. Calcula ratios desde esos componentes, no promediando WAPEs o MAEs de
cortes con denominadores diferentes. Las métricas globales son ponderadas por sus
componentes; no equivalen al desempeño de cada producto.

La cobertura de horizonte total es la fracción de cortes elegibles cuyo total
queda dentro del intervalo original; exporta su denominador separado. No sumes
intervalos de productos distintos para inventar un intervalo agregado válido.

Archivos mínimos, con esquema versionado y orden estable:

| Archivo | Contenido |
|---|---|
| `periods.csv` | IDs, corte, semana, point forecast, límites, actual, estado de disponibilidad, observable y error cuando corresponda |
| `results.json` | Perfil y estado original por serie/corte, total original, métricas, causas y componentes acumulables |
| `summary.json` | Resumen por serie y global; cortes, semanas, UNKNOWN, exclusiones, cobertura y componentes |
| `manifest.json` | Hashes de entrada, policy y archivos producidos; parámetros, cortes, método de etiquetado, versión del esquema, commit Git y estado dirty |

Preserva IDs y distingue null de cero en JSON/CSV. No redondees valores para
calcular; redondea solo al presentar. JSON debe rechazar NaN/Infinity.
No guardes tokens, variables de entorno completas ni datos personales en manifiestos.
No sobreescribas resultados sin una opción explícita: prefiere rechazar carpeta
existente no vacía. No dejes una salida parcial presentada como ejecución terminada.

Usa un directorio local ignorado por Git y agrega solo el patrón específico que
necesites. No ignores `output/` globalmente sin revisar: puede contener trabajo
del usuario. No subas historia real, resultados empresariales ni credenciales.

## 11. Comando de usuario y documentación

Interfaz objetivo, a adaptar al esquema definitivo:

```text
python -m pango_prediction.adapters.inbound.cli.backtest --input data/backtest.json --policy config/model_policy.json --output artifacts/backtests/demo
```

Los cortes pueden vivir en la entrada para que el hash capture la configuración;
evita dos fuentes de verdad. Exige policy explícita; no caigas a development_policy.
La CLI no requiere token de API ni levanta Uvicorn. Usa la aplicación directamente,
sin llamar por HTTP al propio servicio.

Documenta un ejemplo con fixture sintética ejecutable desde una instalación limpia,
la misma invocación en PowerShell y bash, esquema completo y una explicación de
cada salida. Un fallo estructural debe devolver código de salida no cero; los
fallos por serie deben quedar contabilizados en un reporte completado. Documenta
esa diferencia para automatizaciones. No añadas barras de progreso o logs por
observación que dificulten ejecutar lotes.

## 12. Pruebas obligatorias

Desarrolla primero pruebas de comportamiento. Incluye:

1. MA4 con historia suficiente y futuro conocido; fechas y valores esperados.
2. Ejemplo manual: pronósticos 3.5 × 4, actuals [2,4,6,3], límites semanales
   [0.125,5.625], total [8.2,20.4]. MAE=1.25, WAPE=1/3, Bias=1/15,
   cobertura semanal=3/4 y total dentro del intervalo=true.
3. Cambiar valores posteriores al corte no cambia períodos, perfil ni estado
   pronosticado del corte, aunque sí puede cambiar las métricas posteriores.
4. Cambiar datos posteriores a C+28 tampoco cambia etiquetas ni métricas del corte.
5. Paridad con ForecastService ejecutado directamente sobre historia recortada.
6. Corte dominical, futuro de lunes a domingo; errores por fechas duplicadas,
   cortes solapados y horizonte incompleto. Semana final parcial no se evalúa.
7. CENSORED/OUT_OF_ASSORTMENT excluidos; UNKNOWN incluido y contabilizado.
8. Cero demanda real: WAPE/Bias null, MAE correcto. Ninguna semana observable:
   métricas null y causa explícita.
9. Horizonte parcialmente observable: cobertura total null; no comparación parcial.
10. NEW, inactiva, sin ventas anteriores al corte, historia insuficiente,
    history_only y almacén no-stock; conservar estados sin inventar ceros.
11. Agregación con cortes de denominadores distintos, para detectar promedios
    incorrectos de ratios. No mezclar las identidades de dos series.
12. Finitud, identidad, cobertura declarada y calendario/configuración inválidos.
13. Repetición determinista; hashes correctos de bytes exportados; salida existente
    protegida y fallo de escritura sin manifiesto de éxito.
14. CLI desde subprocess, sin credencial ni servidor, genera esquema esperado.
15. Suite HTTP y de modelos existente sigue pasando sin cambios de comportamiento.

No escribas pruebas que solo comparen la implementación consigo misma. Conserva
el ejemplo manual y el test de fuga temporal como controles independientes.

## 13. Secuencia de ejecución y Git

1. Fetch, status y PRs abiertos. Respeta cambios ajenos; este checkout puede tener
   un `output/` de visualización que no pertenece a la implementación.
2. Inspecciona código y discrepancias; comunica decisiones de forma breve.
3. Crea `feature/historical-backtesting` desde el último origin/dev y publícala
   inmediatamente con upstream propio. No dependas de una PR sin fusionar sin
   consultar al propietario, como exige AGENTS.md.
4. Primer grupo coherente: aplicación y sus pruebas, tipos y acumuladores que
   realmente necesite. Ejecuta controles antes de commit y push.
5. Segundo grupo coherente: CLI, fixture sintética, integración y exportación.
   Ejecuta controles antes de commit y push.
6. Último grupo: documentación, ejemplo y worklog con decisiones, hashes de commits,
   pruebas, limitaciones y siguiente paso. Puede reorganizarse si mantiene commits
   revisables y que pasen las comprobaciones por sí mismos.
7. Abre PR a dev siguiendo plantilla, espera CI y arregla fallos propios. No merges.

El plan no permite saltarse las restricciones del sistema ni las aprobaciones de
herramientas. Sigue las instrucciones vigentes del entorno de ejecución.

## 14. Entorno y comprobaciones

Python 3.12, venv y pip; no uv. Si hace falta preparar el entorno, usa exactamente
la instalación de runtime + grupo dev de AGENTS.md. Activa venv en cada shell.

```text
python -m pytest
ruff check .
python -m mypy pango_prediction tests
```

Ejecuta además la CLI documentada sobre la fixture sintética e inspecciona sus
archivos. CI debe seguir construyendo y probando Docker. No alteres modelos,
tests ni CI para ocultar fallos. No agregues dependencias por conveniencia.

Si no hay dataset real disponible, termina y verifica el motor y el ejemplo
sintético; informa que la medición empresarial queda pendiente del dataset.
No presentes métricas sintéticas como precisión histórica real de PANGO.

## 15. Criterios de aceptación y reporte final

- [ ] Un comando evalúa varios cortes de varias series sin levantar HTTP.
- [ ] ForecastService y dominio existentes son la fuente de los pronósticos.
- [ ] Pruebas demuestran separación temporal y paridad con producción.
- [ ] Método retrospectivo de etiquetado y sus limitaciones son explícitos.
- [ ] Métricas, componentes, denominadores y estados son auditables.
- [ ] Totales incompletamente observables no reciben cobertura engañosa.
- [ ] No cambian policy, modelos, preprocessing ni contrato HTTP por accidente.
- [ ] Exportaciones reproducibles, manifiesto y ejemplo documentado funcionan.
- [ ] Datos privados y visualizaciones ajenas no se suben a Git.
- [ ] pytest, Ruff, mypy y CI pasan.
- [ ] PR, commits, archivos, resultados, decisiones y siguiente paso están reportados.

Al cerrar, incluye un ejemplo legible por serie: pronóstico, error histórico,
período de evaluación, cortes, semanas elegibles y exclusiones. Explica de dónde
sale cada número y qué no se puede concluir. La futura integración con Core debe
guardar esta evidencia junto a la identidad de policy, no reutilizar `error` como
campo de precisión ni sumar métricas de policies distintas.
