# Ruta A · Despliegue del baseline con la regla `inactive` (si gana el baseline)

| Campo | Valor |
| --- | --- |
| Fecha | Lunes 16-nov-2026, antes de la corrida única |
| Decide | Andres Monserrat (Ruta A), a partir de la revisión de Ruta B del 16-nov |
| Estado | Pre-registrada. Se escribe sin haber visto ningún resultado de los bloques 8–13. Se le manda su SHA-256 a Ruta B antes de correr |
| Relación con lo congelado | **No modifica** `freeze.json`, el harness, los criterios ni los dos JSON congelados. Solo decide qué artefacto se despliega si gana el baseline |

## 1. El problema

El baseline congelado (`baseline.json`, SHA-256 `cc196082…1aee`) es, por A12, un solo modelo para todas las series: `moving_average(window=4)`, sin la regla `inactive`. Si gana y se despliega tal cual, las series inactivas dejarían de salir como "sin pronóstico" y saldrían con pronóstico 0 y un intervalo. Eso contradice la regla de negocio acordada para las inactivas, que sí está en la policy v1.

## 2. Por qué agregar la regla no cambia nada de lo evaluado

**Por construcción**, en el código del tag `v0.2.0`:

1. Una serie es inactiva si lleva **26 semanas o más de rejilla después de su última venta** (`domain/profile.py`: `inactive = weeks_since_last_sale >= inactivity_weeks`, con `inactivity_weeks = 26`).
2. Toda semana posterior a la última venta es **UNKNOWN** (`domain/availability.py`: "after the last sale the zeros are inactivity, not censoring"), y UNKNOWN **no se enmascara**: la máscara excluye solo CENSORED y OUT_OF_ASSORTMENT.
3. Entonces las últimas 26 semanas observables de una serie inactiva son ceros, y `moving_average(4)`, que promedia las 4 últimas, da **exactamente 0**. Además tiene al menos 27 semanas observables, así que nunca le falta historia (necesita 5): no hay `MODEL_FAILED`.

**Comprobación empírica:** en el simulacro de los bloques 5–7 (`salidas/corrida_unica_simulacro/2026-11-15_191347_6a7f43c25ba78541/`), el baseline da pronóstico puntual 0 en las 4 semanas en **7.756 de 7.756** series-bloque inactivas (100 %).

**Consecuencia:** agregar la regla `inactive` solo cambia el **estado** de esas series ("sin pronóstico" en lugar de "pronóstico 0") y su **intervalo**. Nada de lo que decide cambia:

- C1, C2, C4, C5 y T4 usan el pronóstico puntual, y "sin pronóstico" cuenta como 0 (A13; criterios 1.5);
- C3 se mide solo con la v1, así que los intervalos del baseline no deciden nada.

## 3. Regla

1. **Si gana la v1**, este documento no se usa.
2. **Si gana el baseline**, se despliega el baseline con la regla `{"when": {"inactive": true}, "reason": "inactive"}` añadida **al principio**, como en la v1, **solo si** la tabla de la corrida única (`series_bloque_producto.parquet`) confirma que el baseline da pronóstico puntual 0 en las 4 semanas en el **100 %** de las series-bloque del segmento `inactive`.
3. **Si alguna no da 0**, se despliega el baseline congelado tal cual, y se reporta por qué.
4. El JSON desplegado se deriva del congelado agregando solo esa regla. Se reporta con su propio SHA-256, junto al del congelado y al resultado de la comprobación del punto 2.

## 4. Lo que esta regla no resuelve (se declara)

Si gana el baseline, una serie con **menos de 5 semanas observables** sale con `MODEL_FAILED` en producción: `moving_average(4)` no tiene respaldo, y por bodega aparecen series nuevas todo el tiempo. **Esto no se puede corregir en la policy.** Mandar esas series a otro modelo cambiaría pronósticos que se evaluaron como f = 0, y agregar NEW → `naive` convertiría el baseline en la v1.

Lo coherente con lo evaluado es que el servicio informe la historia insuficiente con un **estado operativo propio**, y que Core la trate como "sin pronóstico". Es lo que midió la evaluación (f = 0) y no cambia el método. Se le pide a Ruta B y se declara en el reporte.
