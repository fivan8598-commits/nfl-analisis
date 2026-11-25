# Actualización de Niftl

GitHub Actions consulta la cartelera, la disponibilidad, los reportes de club y las actas de Niftl. Esas lecturas activan las revisiones de las fuentes en el servidor cuando corresponden. El registro muestra la hora del último corte y cuántos partidos finalizados tienen acta e individuales. Si el servidor sigue revisando, el script vuelve a consultar.

## Cadencia y uso

- Cada cinco minutos durante las ventanas habituales de partidos del domingo, incluida la noche.
- Cada diez minutos durante las ventanas nocturnas de lunes y jueves. Las ventanas especiales de la temporada 2026–27 se revisan cada diez o quince minutos. Revisa esas excepciones al publicarse el siguiente calendario.
- Cada hora de 06:13 a 23:13 de Cancún, de agosto a febrero, para revisar fuentes fuera de los partidos.

El mes con más disparos durante la temporada de referencia es enero de 2027, con 1,632 jobs programados, incluidos los solapes horarios. Los jobs suelen consumir un minuto de Linux cada uno. Los reintentos manuales, las ejecuciones largas y otros repositorios comparten el presupuesto de Actions. GitHub ejecuta el cron según la disponibilidad de sus runners y puede retrasar un job.

Las consultas usan rutas públicas del sitio. El workflow requiere solo permisos de lectura del repositorio y funciona sin secretos. Cada revisión incorpora las actas y los partes que la fuente ya publicó. Los resultados confirmados se conservan si ESPN o la NFL presentan demora.

## Ejecución

Coloca estos archivos en la rama predeterminada del repositorio. En **Actions**, selecciona **Actualizar Niftl** y **Run workflow** para verificar el primer registro. Las ejecuciones programadas usan la misma rama.

```sh
node --test refresh.test.mjs
node refresh.mjs
```
