# Mensaje para Andrés — versión candidata de Ruta B

Hola Andrés, ya incorporé la policy y los respaldos en una rama de Ruta B.

- Commit de implementación candidato:
  `654920ae0b262fa05919d4f7eb93d8aca900f5b6`.
- Rama: `feature/route-a-baseline-policy`, propuesta hacia `dev`.
- Policy: `ruta-a-baseline-v1-inactive`, en `config/model_policy.json`, sin
  modificar los bytes recibidos. SHA-256:
  `326e0b3586477c9a00529e305b87267f1afbdedb14595981a63fbfb9c7503842`.
- Adjunto `v0.2.0-to-654920a.patch`: diff de `v0.2.0` a ese commit sobre
  `pango_prediction/domain/` y `pango_prediction/adapters/outbound/`.

En ese diff, los adaptadores de salida no tienen cambios. En dominio cambian
`policy.py` y `types.py` para cerrar y validar el vocabulario de motivos de
“sin pronóstico”; en `profile.py` solo cambia un docstring. No cambian series,
disponibilidad, calendario, parámetros, intervalos, reparto ni algoritmos.

La policy queda seleccionable al arrancar con
`PANGO_PREDICTION_POLICY_PATH=config/model_policy.json`
(`/app/config/model_policy.json` dentro del contenedor). Para recibir el estado
`no_forecast`, Core debe declarar contrato `1.2`.

Los controles locales pasan: 461 pruebas, Ruff y mypy. Las pruebas HTTP con
la policy recibida confirman MA4, inactivas sin pronóstico y conservación del
error por historia insuficiente. StatsForecast sigue disponible.

El commit es una versión candidata para tu revisión, todavía no un despliegue
ejecutado. Cuando se publique, confirmaré el hash realmente desplegado; si
difiere, enviaré el diff actualizado. El commit posterior de documentación
solo añade esta entrega y el worklog, sin cambiar el código ejecutable.

Quedan para después del 20 el tratamiento operativo de historia insuficiente
y la coordinación de ese estado con Core. El lock del servicio sigue pendiente;
la declaración del lock del harness no lo sustituye.
