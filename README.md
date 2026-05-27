# SQL Traceability Rewriter (BigQuery-first)

Aplicacion web en Python para automatizar trazabilidad SQL sin IA.

La app toma una query base y una query/proceso de referencia, aplica reglas deterministicas de reescritura y devuelve:

- Query resultante.
- Reporte de cambios aplicados.
- Registro opcional en base SQLite para documentar trazas.

## Estado actual del proyecto

Implementado y funcional en MVP:

- Backend API con FastAPI.
- Motor de trazabilidad SQL basado en AST con SQLGlot.
- Interfaz web para vista previa y guardado.
- Persistencia en SQLite con SQLAlchemy.
- Ejecucion local o con Docker Compose.

No implementado todavia:

- Reglas de trazabilidad por JOIN.
- Seguimiento automatico completo de cadena de temporales (grafo de transformaciones).
- Traduccion BigQuery -> Oracle.

## Flujo funcional de la app (end-to-end)

1. Usuario abre la UI en navegador.
2. Carga:
   - Query base (la que quiere mantener y ajustar).
   - SQL de referencia (puede ser multi-step).
3. Puede elegir:
   - Vista previa: recalcula y muestra salida sin persistir.
   - Guardar traza: recalcula y guarda inputs + output + reporte.
4. Backend ejecuta motor de reescritura.
5. UI muestra query reescrita y reporte JSON.
6. Si se guardo, la corrida aparece en historial de trazas recientes.

## Como funciona la automatizacion de trazabilidad (criterios)

Esta seccion es la mas importante: describe exactamente en base a que reglas se modifica la query.

### 1) Parseo estructural (no reemplazo por texto)

Se parsean base y referencia a AST SQL. Esto evita errores comunes de find/replace de strings.

### 2) Seleccion del SELECT de referencia en proceso multi-step

Si la referencia contiene varios statements, el motor busca de atras hacia adelante y toma el primer SELECT util que encuentre.

Casos soportados como "SELECT util":

- SELECT directo.
- CREATE ... AS SELECT.
- INSERT ... SELECT.

Objetivo: usar el ultimo paso relevante del proceso como fuente de verdad para expresar calculos y filtros.

### 3) Criterio de matching de columnas

Se compara proyeccion por proyeccion entre query base y referencia usando una key logica:

- Primero alias de salida (alias_or_name), en lowercase.
- Si una proyeccion no tiene alias, se usa su SQL normalizado.

En la practica, hoy el comportamiento mas estable es por alias de salida.

### 4) Reemplazo de expresiones de columnas

Para cada columna de la query base:

- Si su key existe en referencia, compara expresion actual vs nueva (normalizadas).
- Si son distintas, reemplaza la expresion en la base por la de referencia.
- Si son iguales, no toca nada.

Regla de compatibilidad de salida:

- Si la columna base ya tenia alias, lo conserva.
- Si no tenia alias y la nueva expresion es compleja, agrega alias para mantener compatibilidad de nombre de salida.

### 5) Politica WHERE = AND (con deduplicacion)

La query base nunca pierde sus condiciones.

Proceso:

- Se descompone WHERE base y referencia en lista de condiciones por AND.
- Se normalizan para detectar duplicados.
- Se agregan solo las condiciones de referencia que no esten ya en base.
- Se reconstruye WHERE final con AND.

Resultado: se conservan filtros originales y se anexan filtros nuevos/relevantes de referencia.

### 6) Reporte de trazabilidad

El motor entrega un reporte estructurado con:

- dialect: dialecto usado (actualmente bigquery).
- columns_replaced: lista de columnas reemplazadas (from/to).
- where_conditions_added: condiciones WHERE agregadas.

Esto permite auditar exactamente que cambio y por que.

## Ejemplo conceptual

Base:

SELECT
  id,
  importe AS total
FROM ventas
WHERE estado = 'OK'

Referencia:

CREATE TEMP TABLE t1 AS
SELECT
  id,
  importe * tc AS total
FROM ventas_stg
WHERE pais = 'AR';

Salida esperada del motor:

- Reemplaza total: de importe a importe * tc.
- Mantiene estado = 'OK'.
- Agrega pais = 'AR'.
- WHERE final: estado = 'OK' AND pais = 'AR'.

## Limites conocidos del MVP

- No resuelve linaje semantico profundo entre tablas intermedias (todavia no sigue automaticamente todo el camino de temporales).
- No hace analisis de contradiccion logica entre filtros; solo agrega por AND evitando duplicados exactos.
- No aplica aun reglas especificas sobre JOIN, GROUP BY o HAVING para reconciliacion avanzada.
- No traduce sintaxis entre motores (BigQuery -> Oracle).

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
- app/lineage.py: motor de trazabilidad (reglas de reescritura).
- app/db.py: configuracion DB.
- app/models.py: modelo de persistencia de corridas.
- app/templates/index.html: UI principal.

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

## Proximo paso recomendado

Validar el motor con una query real de tu proceso (base + referencia multi-step), revisar reporte generado y ajustar reglas de matching si aparecen casos ambiguos.
