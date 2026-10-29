# ProcesarCitasIRIS

Importador independiente de reportes **Citas por estado** de IRIS a MySQL.

## Flujo
1. Copia los Excel (.xlsx o .xls) a `pendientes/`.
2. Ejecuta `python importar_citas_iris.py`.
3. El programa detecta la fila de encabezados por `ID CITA`, ignora las 10 columnas personales configuradas y utiliza `id_cita` como clave primaria.
4. Las citas existentes se actualizan con `ON DUPLICATE KEY UPDATE`.
5. **Tras confirmar la transacción (COMMIT)**, se mueve el Excel a `procesados/`. Si hay error, permanece en `pendientes/` y el detalle se registra en `logs/importador.log`.
6. Evita sobrescribir archivos con nombres repetidos.

## Instalación
Python 3.10+:
```powershell
pip install -r requirements.txt
```
Configura las variables de entorno **MYSQL_HOST**, **MYSQL_DATABASE**, **MYSQL_USER**, **MYSQL_PASSWORD** y opcionalmente **MYSQL_PORT**. Nunca guardes estas contraseñas en GitHub.

## Uso
```powershell
python importar_citas_iris.py --dry-run
python importar_citas_iris.py
```
`--dry-run` valida la lectura sin conectar a MySQL ni mover archivos.

## Seguridad
No subir Excel de pacientes al repositorio. Aunque se excluyen las diez columnas personales solicitadas, el resto del informe podría incluir datos sensibles como fechas de nacimiento y observaciones: protege el acceso a la BD. El `.gitignore` excluye los Excel de las carpetas, registros de ejecución y archivos de entorno.

## Notas
- El esquema guarda los campos del reporte como texto para evitar pérdida por valores heterogéneos de IRIS, salvo `id_cita` (BIGINT); puedes convertir fechas y horas en consultas para Grafana.
- No se eliminan registros si una cita deja de aparecer en un informe.
- El primer uso crea la tabla `citas_estado_iris` y futuras columnas nuevas.
- Si MySQL confirma datos pero falla el movimiento del archivo, en el siguiente intento se actualizarán las mismas citas, sin duplicarlas.
