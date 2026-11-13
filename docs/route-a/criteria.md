# Ruta A · Criterios de selección y de aceptación (v1)

| Campo | Valor |
| --- | --- |
| Versión | 1 |
| Autor | AndresMonserrat (Ruta A) |
| Fecha y hora | 13-nov-2026 00:57:07 -05:00 |
| Fuente | *Ruta A – Registro de decisiones* v5 (11-nov-2026): "Criterios (pendiente 12)", A16 |
| Base del código | `pango-prediction`, rama `dev` en `2ad60c6` |
| Estado | Pre-registrado. Escrito antes de comparar cualquier candidato. Inmutable desde su merge (DEC-8) |

Este documento fija dos criterios distintos, como pide el registro:

1. **De selección:** con los bloques 1–7, decide qué modelo usa cada segmento de la policy v1 y cuál es el mejor baseline.
2. **De aceptación:** en la corrida única de los bloques 8–13, decide si la policy v1 aprueba frente al mejor baseline.

Si este documento contradice al registro, gana el registro, salvo en las desviaciones declaradas del anexo B.

---

## 1. Definiciones comunes

### 1.1 Bloques y datos

- Son 13 bloques de 4 semanas, con cortes por fecha y ventana expansiva (registro, "Bloques de evaluación por fecha").
  - Los bloques 1–4 sirven para ajustar.
  - Los bloques 5–7 sirven para seleccionar.
  - Los bloques 8–13 son de reporte y se corren una sola vez (DEC-6).
- Todo lo que se fija con datos se fija con datos ≤ 12-abr-2026 (DEC-7).
- En cada corte, el perfil (actividad, manejo de stock, clase de historia, clase de demanda) y la máscara de entrenamiento se recalculan con datos ≤ corte.

### 1.2 Grano

- **Selección:** a grano producto (D1), en los bloques 1–7.
- **Aceptación:** la decisión se toma a grano producto (protocolo de T4, punto 1). La medición por bodega de stock es obligatoria y entra como la condición C5.

### 1.3 Semanas evaluables y serie-bloque

- Una semana es evaluable si su estado es `AVAILABLE` o `UNKNOWN`. Las semanas `CENSORED` y `OUT_OF_ASSORTMENT` nunca entran en el error (S31).
- Una **serie-bloque** es una serie en un bloque.
  - Con menos de 2 semanas evaluables, se descarta.
  - El % de series-bloque descartadas se reporta por segmento.
- Para cada serie-bloque se calculan:
  - y = Σ de la venta real sobre las semanas evaluables;
  - f = Σ del pronóstico puntual semanal sobre esas mismas semanas;
  - el error, e = f − y.

### 1.4 Cómo se obtiene el pronóstico

- Cada candidato es un `model_policy.json` que se carga con el cargador de Ruta B. Cada modelo sale del registro de modelos del commit del tag.
- El pronóstico de una serie es exactamente el que produciría el servicio con ese corte: lo evaluado es lo desplegado.

### 1.5 Series sin pronóstico (DEC-1)

En la evaluación, se toman como f = 0:

- una serie a la que la policy le asigna sin pronóstico (`reason`);
- una serie en la que el modelo lanza `InsufficientHistoryError`.

Estas series entran en todas las métricas con f = 0, y además se reportan aparte como costo de exclusión.

### 1.6 Segmentos

- El segmento de una serie es la primera regla de la policy (sección 2.2) que coincide con su perfil en ese corte.
- `NEW` es la clase de historia de las series con 13 semanas o menos desde su primera venta.

### 1.7 Métricas

| Métrica | Definición |
| --- | --- |
| Sesgo de un conjunto | (Σf − Σy) / Σy sobre todas sus series-bloque. Si Σy = 0, no está definido (sección 2.5, regla 7) |
| WAPE | Σ\|f − y\| / Σy sobre todas las series-bloque. Siempre se reporta con el sesgo |
| Escala de una serie en un corte | S_t = suma de 4 semanas observables consecutivas de la serie contraída (S31), con historia ≤ corte; s² = media de (S_t − S_{t−4})². Hacen falta al menos 8 semanas observables. Si la escala es 0 o no existe, la serie queda fuera del RMSSE y se cuenta, por segmento |
| RMSSE de una serie | √( media sobre sus bloques evaluados de e_b² / s_b² ). La escala s_b es la del corte de cada bloque |
| RMSSE agregado | **Media simple** del RMSSE por serie (S4). Sensibilidad: la media ponderada por las unidades de las 26 semanas anteriores al corte. Si las dos medias eligen distinto, se reporta y decide la simple |
| Métrica de `NEW` | √( media de e² sobre todas las series-bloque `NEW` ), sin escala por serie (S3; anexo B) |
| Cobertura del total | % de series-bloque con la venta real del bloque dentro de [`lower`, `upper`] del total de 4 semanas que devuelve el modelo. Solo se miden las series-bloque con las 4 semanas evaluables, y se reporta el % excluido por segmento. Se separa entre totales calculados desde sumas reales y totales del respaldo ×4 (DEC-4) |
| Solo se reportan | Trampa del cero (% de series-bloque con f = 0 e y > 0), pinball en q0,10, q0,50 y q0,90, cobertura semanal |

### 1.8 Comparación estadística

- **Bootstrap pareado por producto:**
  - Se remuestrean productos con reemplazo. Cada producto entra con todas sus series y todos sus bloques; por bodega, con todas sus bodegas.
  - Se hacen 10.000 remuestreos, con la semilla 20261117, que va en `freeze.json`.
  - Los dos modelos que se comparan usan exactamente la misma muestra.
  - En cada remuestreo se recalcula la métrica agregada de cada modelo y su diferencia (candidato − referencia).
  - El IC 95 % es el de percentiles 2,5 y 97,5.
- **"Mejora con un IC 95 % que excluye 0":** la diferencia observada es menor que 0 y el límite superior del IC también.

### 1.9 Otras reglas

- **Condiciones que no se pueden calcular:** una condición que no se puede calcular cuenta como **no cumplida**. La única excepción es la contingencia de C3 (sección 3.2).
- **"En verde":** el harness está en verde cuando pasan sus tests y la corrida de los bloques 1–7 termina sin error.

---

## 2. Criterio de selección

### 2.1 Candidatos

Compiten solo los modelos del plan B que están registrados en el código del tag:

| Modelo | Parámetros | Papel |
| --- | --- | --- |
| `naive` | — | Candidato |
| `moving_average` | `{"window": w}`, con w ∈ W = {2, 3, 4} (S1) | Candidato |
| `sba` | `{"alpha": 0.1}`, fijo (DEC-3) | Candidato solo en `INTERMITTENT` y `LUMPY`, y solo si está fusionado en el tag (A10). Si no está, se declara no evaluado |
| `zero` | — | **No es candidato en ningún segmento.** Se reporta solo como referencia |

Los modelos registrados en `dev` que están fuera del plan B (`croston`, `tsb`, `adida`, `imapa`, `ets`, `auto_arima`, `deepar`) no compiten (DEC-9; anexo B).

### 2.2 Orden de las reglas de la policy v1

```text
1. inactive = true               → reason: inactive
2. history_class = NEW           → modelo elegido para NEW
3. demand_class = INSUFFICIENT   → modelo elegido para INSUFFICIENT
4. demand_class = INTERMITTENT   → modelo elegido para INTERMITTENT
5. demand_class = LUMPY          → modelo elegido para LUMPY
6. demand_class = SMOOTH         → modelo elegido para SMOOTH/ERRATIC
7. demand_class = ERRATIC        → modelo elegido para SMOOTH/ERRATIC
default                          → modelo del mejor baseline (incluye NOT_CLASSIFIABLE)
```

No hay regla `sold_to_order` (DEC-9).

### 2.3 Paso 1 · Ventanas (bloques 1–4)

- **Ventana global** w_g: la de W con el menor RMSSE agregado sobre todas las series. Si hay empate, gana la más corta.
- **Ventana del segmento** w_s: la misma regla, solo dentro de cada segmento de la sección 2.5. `SMOOTH` y `ERRATIC` se ajustan juntos. En `NEW` se usa la métrica de `NEW` (S3).
- **Si el harness no está en verde el viernes 13:** w = 4 fija en todas partes, y rige la regla 3 del plan B.

### 2.4 Paso 2 · Mejor baseline (bloques 5–7, todas las series)

1. El defecto es `naive`. El retador es `moving_average` con la ventana w_g.
2. El retador desplaza al defecto solo si cumple las dos condiciones:
   - pasa el filtro de sesgo, |sesgo| ≤ 15 %;
   - mejora el RMSSE agregado con un IC 95 % que excluye 0.
3. Si `naive` no pasa el filtro y el retador sí, gana el retador sin exigir IC. Si ninguno lo pasa, gana el de menor |sesgo|.
4. El baseline se escribe como policy JSON: `{"when": {}}` con el mismo modelo y los mismos parámetros en `default` (A12).

### 2.5 Paso 3 · Modelo por segmento (bloques 5–7)

**Candidatos por segmento:**

| Segmento | Defecto | Retadores | Métrica |
| --- | --- | --- | --- |
| `NEW` | `naive` | `moving_average(w_NEW)` | Métrica de `NEW` (S3) |
| `INSUFFICIENT` | Modelo del baseline | Los de {`naive`, `moving_average(w_s)`} distintos del defecto | RMSSE agregado |
| `INTERMITTENT` | Modelo del baseline | Los de {`naive`, `moving_average(w_s)`, `sba`} distintos del defecto | RMSSE agregado |
| `LUMPY` | Modelo del baseline | Los de {`naive`, `moving_average(w_s)`, `sba`} distintos del defecto | RMSSE agregado |
| `SMOOTH` + `ERRATIC` | Modelo del baseline | Los de {`naive`, `moving_average(w_s)`} distintos del defecto | RMSSE agregado |

En todos los segmentos salvo `NEW`, el defecto es el modelo del baseline con sus mismos parámetros (es decir, con la ventana global).

**Reglas:**

1. **Filtro de sesgo (S5):** solo compite un candidato con |sesgo| ≤ 15 % en el segmento.
2. **Desplazamiento (S6):** un retador que pase el filtro desplaza al defecto solo si mejora la métrica del segmento con un IC 95 % que excluye 0. Si lo desplazan varios, gana el de mejor métrica.
3. **Defecto descalificado:** si el defecto no pasa el filtro, gana el mejor candidato que sí lo pasa, sin exigir IC.
4. **Nadie pasa el filtro:** gana el candidato con menor |sesgo|.
5. **Desempate por simplicidad:** solo desempata entre candidatos indistinguibles. El orden es `naive` < `moving_average` con la ventana más corta < `sba`.
6. **Segmento sin series evaluables** en los bloques 5–7: se queda el defecto.
7. **Segmento con Σy = 0:** el filtro de sesgo no aplica y todos los candidatos pasan.

### 2.6 Qué se reporta (no decide)

- Por segmento: métrica, sesgo, WAPE, trampa del cero, % de series fuera del RMSSE y % de series-bloque descartadas.
- Los segmentos donde el defecto fue descalificado por sesgo, con el modelo que lo reemplazó y la diferencia de métrica con su IC.
- La sensibilidad con la media ponderada (S4).
- En `NEW`:
  - cuántos productos concentran la mitad del error;
  - el WAPE con sesgo;
  - el desempeño por bodega en los bloques 5–7, con la advertencia de que esas series empiezan en el cierre de fin de año (30-dic al 6-ene).
- Las métricas separadas antes y después del 31-dic-2025 (bloques 1–4 frente a 5–7).

---

## 3. Criterio de aceptación (bloques 8–13, corrida única)

### 3.1 Candidatos

Se comparan los dos JSON congelados en `freeze.json`: la policy v1 y el mejor baseline. Se miden sobre el mismo conjunto de series, con la convención de la sección 1.5.

### 3.2 Condiciones

La v1 aprueba **solo si cumple las cinco**:

| # | Condición | Umbral |
| --- | --- | --- |
| C1 | RMSSE agregado (grano producto) | La v1 lo mejora y el IC 95 % de (v1 − baseline) queda entero por debajo de 0 (registro) |
| C2 | Sesgo agregado de la v1 | \|sesgo\| ≤ 15 % (A1) |
| C3 | Cobertura del total de 4 semanas de la v1, solo con los totales calculados desde sumas reales | Entre 70 % y 90 % (80 % nominal ± 10 puntos) (A2 + A3). Los totales del respaldo ×4 se reportan aparte y no deciden |
| C4 | Clase A | Deterioro del RMSSE agregado de la v1 frente al baseline ≤ 5 % (A4) |
| C5 | Cada bodega de stock (series producto × bodega) | Deterioro del RMSSE agregado de la v1 frente al baseline ≤ 5 % (A5, veto suave) |

- **Deterioro:** (RMSSE_v1 − RMSSE_baseline) / RMSSE_baseline.
- **Clase A (S36):** se calcula con el costo de lo vendido del año que termina el 12-abr-2026, sin las filas con `costo_invalido`.
  - A: los productos hasta el 80 % acumulado. El producto que cruza el 80 % entra en A.
  - B: el siguiente 15 %.
- **Contingencia de C3:** si la corrección del total (A11) no está en el tag:
  - C3 se evalúa sobre los totales tal como se despliegan (respaldo ×4), con el mismo rango de 70 % a 90 %;
  - se declara como desviación de D4;
  - la regla de "no se puede calcular → no cumplida" no aplica a este caso.

### 3.3 Resultado

- **Si cumple C1 a C5,** se despliega la v1.
- **Si falla cualquiera,** se despliega el baseline.

Las dos salidas son válidas (plan B, regla 2). Después, la misma corrida mide por bodega y, si entró el reparto, decide T4 con su protocolo.

---

## 4. Declaraciones

| # | Declaración |
| --- | --- |
| DEC-1 | Contar como cero las series sin pronóstico, incluidas las que lanzan `InsufficientHistoryError`, es **solo una convención de evaluación** (A13). En producción, esas series siguen saliendo como no pronóstico |
| DEC-2 | La huella de `DomainParameters` no cubre k = 1,18 ni la ventana de 8 semanas del reparto, que van en `freeze.json` (A14). Tampoco cubre `MIN_HORIZON_RESIDUALS = 8`, que es una constante de plataforma |
| DEC-3 | El α de `sba` queda fijo en 0,1, sin ajuste |
| DEC-4 | La cobertura del total se separa entre totales desde sumas reales y totales del respaldo ×4. Solo la de sumas decide, salvo la contingencia de C3 |
| DEC-5 | Toda comparación anterior al merge de este documento es exploratoria (A16), incluidos los benchmarks de `model/`. Si el merge se atrasa, la selección **espera el merge** |
| DEC-6 | Los bloques 8–13 se corren una sola vez, con `freeze.json` y un archivo centinela. La semilla del bootstrap va en `freeze.json` |
| DEC-7 | Fecha de corte: nada se fija con datos posteriores al 12-abr-2026 |
| DEC-8 | Este documento está fechado y es inmutable desde su merge. Su SHA-256 va en `freeze.json`. Si se encuentra un error, se declara en el reporte sin cambiar el criterio |
| DEC-9 | Limitaciones de la policy v1: no hay regla `sold_to_order`; los 70 productos de temporada quedan como `inactive`; no hay `pooled_rate`; el baseline no incluye `zero` ni `sba`; se evaluó solo en EquipMaster; los modelos registrados en `dev` fuera del plan B no se evalúan |

---

## 5. Control de cambios

| Versión | Fecha | Cambio |
| --- | --- | --- |
| 1 | 13-nov-2026 00:57:07 -05:00 | Primera versión, pre-registrada |

---

## Anexo A · Decisiones que fija este documento

| # | Decisión |
| --- | --- |
| S1 | W = {2, 3, 4}, ajustada por segmento en los bloques 1–4. Ventana fija 4 si el harness no está en verde el viernes 13 |
| S2 | `NEW` se elige a grano producto en los bloques 5–7. Compiten `naive` (defecto) y `moving_average` con la ventana ajustada para `NEW`. `sba` queda excluido. El desempeño por bodega se reporta sin decidir |
| S3 | Métrica de `NEW`: raíz de la media de e² sin escala por serie. El bootstrap usa esta misma cifra |
| S4 | RMSSE agregado = media simple; la ponderada es solo sensibilidad. En la selección no hay protección de clase A |
| S5 | Filtro de \|sesgo\| ≤ 15 %. Si nadie pasa, gana el de menor \|sesgo\|. La trampa del cero solo se reporta. `zero` no es candidato |
| S6 | Primero se elige el baseline. El defecto de cada segmento es el modelo del baseline (`naive` en `NEW`). Un retador desplaza al defecto solo con un IC 95 % que excluye 0. La simplicidad solo desempata |
| A1–A5 | C2 a C5 de la sección 3.2. C1 la fija el registro |
| T1–T15 | Detalles técnicos de las secciones 1 y 2: f = 0 por historia insuficiente; escala del RMSSE; bootstrap; empate de ventanas hacia la más corta; condiciones no calculables cuentan como no cumplidas; definición de "en verde"; segmento sin series; cobertura solo con 4 semanas evaluables; Σy = 0 |

## Anexo B · Desviaciones y discrepancias frente al registro v5

| Tema | Registro v5 | Este documento | Motivo |
| --- | --- | --- | --- |
| `zero` en `INSUFFICIENT` | Candidato ("cuál (`zero`, `naive` o `moving_average` corta)") | No es candidato en ningún segmento | Su sesgo es −100 % por construcción; con el filtro de sesgo nunca podría ganar |
| Métrica de `NEW` | RMSSE | Error cuadrático sin escala por serie | Casi ninguna serie `NEW` tiene las 8 semanas observables que exige la escala |
| Bootstrap | "Pareado sobre series" | Pareado por producto | Igual que el protocolo de T4 (punto 4). Evita tratar como independientes series del mismo producto |
| Ranking Nemenyi o MCB | Mencionado | No decide; puede reportarse | La decisión usa el bootstrap pareado |
| Plazo de A16 | PR el jueves 12 antes de las 10:00 | PR abierto después (ver la fecha del encabezado) | Retraso de Ruta A. En la sesión que escribió este documento no se comparó ningún candidato |
| Modelos de StatsForecast en `dev` | Plan B: ETS, TSB, ADIDA e IMAPA quedan fuera; StatsForecast como dependencia del servicio está descartado (11-nov) | No compiten | Se sigue el plan B. Su registro en `dev` (PR #8) es una discrepancia entre el código y el registro que deciden las dos rutas, no este documento |
