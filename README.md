# SQL Traceability Rewriter (BigQuery-first)

Aplicacion web en Python para automatizar trazabilidad SQL sin IA, enfocada en BigQuery y en procesos multi-step.

La app toma:

- Query base (la query que hoy consume una tabla intermedia).
- SQL de referencia (el proceso o query que genera esa tabla).

Luego aplica reglas deterministicas para reescribir la query base hacia un nivel mas bajo (cercano al origen) y devuelve:

- Query resultante.
- Reporte estructurado de cambios.
- Registro opcional en SQLite para documentacion.

## Estado actual

Implementado:

- Backend API con FastAPI.
- Motor de trazabilidad basado en AST con SQLGlot.
- UI web para vista previa, guardado y consulta de trazas.
- Persistencia en SQLite con SQLAlchemy.
- Ejecucion local o con Docker Compose.

En progreso / futuro:

- Trazabilidad con JOINs en la query base.
- Linaje automatico multi-step (grafo de temporales).
- Traduccion BigQuery -> Oracle.

## Flujo general de la app

1. El usuario abre la UI.
2. Ingresa query base y query/proceso de referencia.
3. El backend ejecuta el motor de trazabilidad.
4. La UI muestra la query reescrita y el reporte.
5. Si se guarda, queda en el historial.

## Proceso de trazabilidad (reglas exactas)

El motor aplica reglas en este orden:

### 1) Parseo estructural (AST)

Base y referencia se parsean a AST con SQLGlot. No se hace find/replace de texto plano.

### 2) Seleccion del SELECT de referencia

Si el SQL de referencia tiene multiples statements, se elige el ultimo SELECT util:

- SELECT directo.
- CREATE ... AS SELECT.
- INSERT ... SELECT.

Esto permite que la referencia sea un proceso multi-step y aun asi se tome la salida final como verdad.

### 3) Reemplazo del FROM

La query resultante toma el FROM (y JOINs) de la referencia. Esto baja la traza al origen real.

### 4) Limpieza de proyecto en FROM (caso BigQuery)

Si el FROM trae un project_id con prefijo `arcor` (ej `arcor-bd-produccion.dataset.tabla`), se elimina el prefijo y se conserva solo `dataset.tabla`.

Ejemplo:

- Entrada: `FROM arcor-bd-produccion.tablas-bd.tabla1`
- Salida: `FROM tablas-bd.tabla1`

Respeta alias:

- `FROM tablas-bd.tabla1 t1`
- `FROM tablas-bd.tabla1 AS t1`

### 5) Matching de columnas por alias de salida

Para reemplazar expresiones del SELECT se usa la key logica:

- alias de salida (alias_or_name), en lowercase.
- si no hay alias, se usa el SQL normalizado.

### 6) Reemplazo de expresiones en SELECT

Si una columna de la base tiene el mismo alias que una de la referencia, se reemplaza su expresion.

Regla de compatibilidad:

- Si la base ya tiene alias, se conserva.
- Si no, se agrega alias cuando la nueva expresion es compleja.

### 7) Inline selectivo en WHERE (trazabilidad inversa)

Para el caso de **query base con una sola tabla**, se reescriben columnas del WHERE usando la referencia:

- Si la referencia define la columna como **columna simple**, se reemplaza.
- Si la referencia define la columna como **expresion compleja o CASE**, no se reemplaza (se evita inyectar CASE en WHERE).

Esto evita inconsistencias logicas y mantiene el WHERE estable.

### 8) Politica WHERE = AND

Luego, se combinan condiciones de base y referencia con AND, deduplicando condiciones iguales.

Resultado: no se pierden filtros originales y se agregan los filtros de la referencia.

### 9) Normalizacion de alias

Se reescriben calificadores de tabla para usar consistentemente el alias final del FROM.

## Reporte de trazabilidad

El motor devuelve un JSON con:

- `dialect`: dialecto usado (`bigquery`).
- `columns_replaced`: expresiones reemplazadas en SELECT.
- `columns_inlined`: columnas de WHERE reemplazadas con columnas simples.
- `where_inline_skipped`: columnas evitadas en WHERE por expresiones complejas.
- `unmapped_columns`: columnas de WHERE sin mapeo en la referencia.
- `where_conditions_added`: condiciones agregadas desde la referencia.
- `from_replaced`: indica si se reemplazo el FROM.
- `from_sql`: FROM final de la referencia.
- `from_project_stripped`: lista de cambios de project_id eliminados.

## Tecnologias y para que se usan

- FastAPI: API web y servidor HTTP.
- Jinja2: render de HTML para la UI.
- SQLGlot: parseo y manipulacion de SQL como AST (reescritura segura).
- SQLAlchemy: persistencia en SQLite.
- SQLite: almacenamiento local de trazas.
- Docker: ejecucion contenida y reproducible.

## Limitaciones actuales

- La trazabilidad inversa completa solo esta optimizada para base con una sola tabla.
- No hay reglas avanzadas para JOIN, GROUP BY o HAVING.
- No hay traduccion BigQuery -> Oracle.

## API disponible

- POST /api/rewrite
  - Entrada: title, base_sql, reference_sql.
  - Salida: output_sql, report.
  - Uso: vista previa.

- POST /api/trace-runs
  - Entrada: title, base_sql, reference_sql.
  - Salida: output_sql, report + metadata persistida.
  - Uso: guardar corrida/documentacion.

- GET /api/trace-runs
  - Salida: historial de corridas.

## Estructura principal

- app/main.py: API y rutas web.
- app/lineage.py: motor de trazabilidad.
- app/db.py: configuracion DB.
- app/models.py: modelo de trazas.
- app/templates/: UI.

## Ejecutar local

1. Crear entorno virtual e instalar dependencias:

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

2. Iniciar app:

```bash
uvicorn app.main:app --reload
```

3. Abrir:

- http://127.0.0.1:8000

## Ejecutar con Docker

1. Build y arranque:

```bash
docker compose up --build
```

2. Abrir:

- http://127.0.0.1:8000

3. Persistencia:

- SQLite queda en ./data/traces.db (volumen host).
- Si borras contenedor, el archivo local conserva historial.
