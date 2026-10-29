#!/usr/bin/env python3
"""Carga reportes IRIS Citas por estado desde pendientes/ hacia MySQL."""
import argparse
import logging
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

EXCLUIDAS = {
    "NOMBRE PACIENTE", "APELLIDO PATERNO", "APELLIDO MATERNO",
    "TIPO IDENTIFICACION", "NUMERO TIPO IDENTIFICACION", "DIRECCION",
    "TELEFONO CONTACTO", "TELEFONO RESIDENCIAL", "TELEFONO MOVIL",
    "CORREO ELECTRONICO",
}
TABLA = "citas_estado_iris"

def canon(valor):
    texto = unicodedata.normalize("NFKD", str(valor).strip().upper())
    texto = texto.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto)

def columna(valor):
    return re.sub(r"[^a-z0-9]+", "_", canon(valor).lower()).strip("_")

def celda(valor):
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip() or None

def leer_archivo(archivo):
    matriz = pd.read_excel(archivo, header=None, dtype=object)
    fila = next((i for i in range(min(80, len(matriz)))
                 if "ID CITA" in [canon(x) for x in matriz.iloc[i].tolist()]), None)
    if fila is None:
        raise ValueError("No se encontró la fila de encabezados con ID CITA")
    headers = [canon(x) if pd.notna(x) else "" for x in matriz.iloc[fila]]
    posiciones = [(i, columna(h)) for i, h in enumerate(headers)
                  if h and h not in EXCLUIDAS and h != "NAN"]
    nombres = [h for _, h in posiciones]
    if len(set(nombres)) != len(nombres) or "id_cita" not in nombres:
        raise ValueError("ID CITA ausente o columnas repetidas")
    datos = matriz.iloc[fila + 1:, [i for i, _ in posiciones]].copy()
    datos.columns = nombres
    for nombre in nombres:
        datos[nombre] = datos[nombre].map(celda)
    def id_cita(v):
        if v is None:
            return None
        try:
            numero = int(v)
            return numero if numero > 0 else None
        except ValueError:
            return None
    datos["id_cita"] = datos["id_cita"].map(id_cita)
    datos = datos.loc[datos.id_cita.notna()]
    return datos.drop_duplicates(subset=["id_cita"], keep="last")

def conexion():
    import mysql.connector
    requeridas = ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
    faltantes = [k for k in requeridas if not os.getenv(k)]
    if faltantes:
        raise RuntimeError("Faltan variables de entorno: " + ", ".join(faltantes))
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"], database=os.environ["MYSQL_DATABASE"],
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        port=int(os.environ.get("MYSQL_PORT", "3306")), charset="utf8mb4",
        autocommit=False,
    )

def garantizar_tabla(cnx, columnas):
    cursor = cnx.cursor()
    cursor.execute(f"""CREATE TABLE IF NOT EXISTS `{TABLA}` (
        id_cita BIGINT NOT NULL PRIMARY KEY,
        archivo_origen VARCHAR(255),
        fecha_importacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        fecha_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
          ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute(f"SHOW COLUMNS FROM `{TABLA}`")
    existentes = {fila[0] for fila in cursor.fetchall()}
    for col in columnas:
        if col not in existentes:
            # TEXT conserva códigos, valores no numéricos y fechas atípicas de IRIS.
            cursor.execute(f"ALTER TABLE `{TABLA}` ADD COLUMN `{col}` TEXT NULL")
    cnx.commit()
    cursor.close()

def guardar(cnx, datos, archivo):
    if datos.empty:
        return 0, 0
    garantizar_tabla(cnx, datos.columns)
    columnas = list(datos.columns)
    cursor = cnx.cursor()
    ids = [int(x) for x in datos["id_cita"]]
    anteriores = set()
    for pos in range(0, len(ids), 500):
        lote = ids[pos:pos + 500]
        cursor.execute(
            f"SELECT id_cita FROM `{TABLA}` WHERE id_cita IN ({','.join(['%s'] * len(lote))})",
            lote,
        )
        anteriores.update(row[0] for row in cursor.fetchall())
    claves = columnas + ["archivo_origen"]
    campos = ", ".join(f"`{c}`" for c in claves)
    signos = ", ".join(["%s"] * len(claves))
    cambios = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in claves if c != "id_cita")
    sql = (f"INSERT INTO `{TABLA}` ({campos}) VALUES ({signos}) "
           f"ON DUPLICATE KEY UPDATE {cambios}")
    try:
        registros = [tuple(int(x) if c == "id_cita" else x
                           for c, x in zip(columnas, row)) + (archivo,)
                     for row in datos.itertuples(index=False, name=None)]
        for pos in range(0, len(registros), 300):
            cursor.executemany(sql, registros[pos:pos + 300])
        cnx.commit()
    except Exception:
        cnx.rollback()
        raise
    finally:
        cursor.close()
    return len(ids) - len(anteriores), len(anteriores)

def destino_sin_colision(carpeta, archivo):
    destino = carpeta / archivo.name
    if not destino.exists():
        return destino
    marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return carpeta / f"{archivo.stem}_{marca}{archivo.suffix}"

def main():
    parser = argparse.ArgumentParser(description="Importar reportes IRIS a MySQL")
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    base = args.base_dir.resolve()
    pendientes, procesados, logs = (base / x for x in ("pendientes", "procesados", "logs"))
    for carpeta in (pendientes, procesados, logs):
        carpeta.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(logs / "importador.log", encoding="utf-8"),
                  logging.StreamHandler()],
    )
    archivos = sorted(p for p in pendientes.iterdir()
                      if p.is_file() and p.suffix.lower() in {".xlsx", ".xls"}
                      and not p.name.startswith("~$"))
    if not archivos:
        logging.info("No hay Excel pendientes.")
        return
    cnx = None
    errores = 0
    try:
        if not args.dry_run:
            cnx = conexion()
        for archivo in archivos:
            try:
                datos = leer_archivo(archivo)
                logging.info("%s: %s ID CITA únicos; %s columnas",
                             archivo.name, len(datos), len(datos.columns))
                if args.dry_run:
                    continue
                nuevos, actualizados = guardar(cnx, datos, archivo.name)
                destino = destino_sin_colision(procesados, archivo)
                shutil.move(str(archivo), str(destino))
                logging.info("Importado: nuevos=%s, actualizados=%s; movido a %s",
                             nuevos, actualizados, destino.name)
            except Exception:
                errores += 1
                logging.exception("ERROR en %s (se mantiene pendiente)", archivo.name)
    finally:
        if cnx is not None:
            cnx.close()
    if errores:
        raise SystemExit(f"{errores} archivo(s) con errores; revisar logs/importador.log")

if __name__ == "__main__":
    main()
