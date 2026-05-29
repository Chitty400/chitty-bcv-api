"""
BCV Scraper - Obtiene tasa de cambio USD, EUR e IPC del Banco Central de Venezuela
Diseñado para ejecutarse en GitHub Actions y publicar JSON estático en GitHub Pages
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional  # Compatible con Python < 3.10

# ── Dependencias opcionales (se instalan en el workflow) ──────────────────────
try:
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: Instala dependencias: pip install beautifulsoup4 requests lxml")
    sys.exit(1)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ── Constantes ────────────────────────────────────────────────────────────────
BCV_HOME_URL    = "https://www.bcv.org.ve/"
BCV_IPC_XLS_URL = "https://www.bcv.org.ve/sites/default/files/precios_consumidor/4_5_7.xls"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BCV-Static-API/1.0; "
        "+https://github.com/TU_USUARIO/bcv-api)"
    ),
    "Accept-Language": "es-VE,es;q=0.9,en;q=0.8",
}

OUTPUT_DIR   = Path(__file__).parent.parent / "docs"
LATEST_FILE  = OUTPUT_DIR / "latest.json"
HISTORY_FILE = OUTPUT_DIR / "history.json"


# ── Helpers HTTP ──────────────────────────────────────────────────────────────

def http_get_text(url: str) -> str:
    if REQUESTS_AVAILABLE:
        r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        return r.text
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_get_bytes(url: str) -> bytes:
    if REQUESTS_AVAILABLE:
        r = requests.get(url, headers=HEADERS, timeout=60, verify=False)
        r.raise_for_status()
        return r.content
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


# ── Helper de extracción de tasa desde un tag HTML ───────────────────────────

def _extraer_tasa_de_tag(tag) -> Optional[float]:
    """
    Extrae el primer número con formato de tipo de cambio (ej: 535,38530000)
    del texto de un tag BeautifulSoup. Retorna None si no lo encuentra.
    """
    if tag is None:
        return None
    # Busca el <strong> hijo con clase strong-tb si existe, si no usa el tag directo
    strong = tag.select_one("strong.strong-tb") or tag.find("strong") or tag
    text = strong.get_text(strip=True).replace("\xa0", "").replace(",", ".")
    # Acepta cualquier número con decimales (ej: 547.12345, 4.99, 1234.5)
    m = re.search(r"\d+\.\d{2,}", text)
    if m:
        return float(m.group())
    return None


# ── Scraper de Tasas de Cambio ────────────────────────────────────────────────

def scrape_tasas_bcv() -> dict:
    """
    Obtiene la tasa USD y EUR desde la página principal del BCV.
    El HTML contiene elementos con id='dolar' e id='euro', cada uno con un
    <strong class="strong-tb"> que lleva el valor numérico.
    Retorna un dict con claves 'usd' y 'eur' (ambas como float).
    """
    html = http_get_text(BCV_HOME_URL)
    soup = BeautifulSoup(html, "lxml")

    resultado = {}

    for moneda, elem_id in (("usd", "dolar"), ("eur", "euro")):
        tag = soup.find(id=elem_id)
        tasa = _extraer_tasa_de_tag(tag)

        # Fallback 1: el BCV a veces usa clases como "euro" o "dolar" además del id
        if tasa is None:
            tag = soup.select_one(f"[class~='{elem_id}']")
            tasa = _extraer_tasa_de_tag(tag)

        # Fallback 2: buscar cualquier div/section que contenga el id en su texto de clase
        if tasa is None:
            tag = soup.find(lambda t: t.get("id", "").lower() == elem_id
                            or elem_id in " ".join(t.get("class", [])).lower())
            tasa = _extraer_tasa_de_tag(tag)

        if tasa is not None:
            resultado[moneda] = tasa
            print(f"  ✓ Tasa {moneda.upper()}/VES: {tasa}")
        else:
            # Debug: mostrar el HTML del elemento para diagnosticar
            raw_tag = soup.find(id=elem_id)
            print(f"  ✗ HTML recibido para '#{elem_id}': {str(raw_tag)[:300] if raw_tag else 'NO ENCONTRADO'}")
            raise ValueError(
                f"No se pudo extraer la tasa {moneda.upper()} del BCV "
                f"(elemento '#{elem_id}' no encontrado o sin valor numérico)"
            )

    return resultado


# ── Scraper legado de solo USD (mantiene compatibilidad) ─────────────────────

def scrape_tasa_bcv() -> float:
    """Alias de compatibilidad: retorna solo la tasa USD."""
    return scrape_tasas_bcv()["usd"]


# ── Scraper de IPC ────────────────────────────────────────────────────────────
#
# El XLS del BCV tiene esta estructura (ordenado de más reciente a más viejo):
#   fila: ['2021(*)', '', '']      <- año con sufijo opcional
#   fila: ['Marzo', 746784015747.9, 16.1]
#   fila: ['Febrero', 643008821970.1, 33.8]
#   ...
#   fila: [2020.0, '', '']         <- año como número float
#   fila: ['Diciembre', 327767509170.0, 77.5]
#
# Como está ordenado de más reciente a más viejo, el primer mes válido
# que encontremos ES el más reciente — retornamos ahí mismo.
#
# Usamos xlrd directamente en lugar de LibreOffice+CSV porque:
#   - LibreOffice perdía el año "2021(*)" al convertir (el * rompía el regex)
#   - xlrd lee las celdas nativas sin conversión intermedia

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _parse_xls_bytes(content: bytes) -> dict:
    try:
        import xlrd
    except ImportError:
        raise ImportError("xlrd no instalado — agrega 'xlrd' al workflow: pip install xlrd")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        wb = xlrd.open_workbook(tmp_path)
        ws = wb.sheet_by_index(0)
    finally:
        os.unlink(tmp_path)

    current_year = None

    for i in range(ws.nrows):
        col0 = ws.cell_value(i, 0)
        col1 = ws.cell_value(i, 1)
        col2 = ws.cell_value(i, 2)

        # Detectar fila de año (string "2021(*)" o float 2020.0, col1 vacía)
        anio = None
        if isinstance(col0, str):
            m = re.match(r"^(\d{4})", col0.strip())
            if m and col1 == "":
                anio = int(m.group(1))
        elif isinstance(col0, float) and col1 == "":
            anio = int(col0)

        if anio is not None:
            current_year = anio
            continue

        # Detectar fila de mes
        if not isinstance(col0, str) or current_year is None:
            continue
        mes_lower = col0.strip().lower()
        if mes_lower not in MESES_ES:
            continue
        if not isinstance(col1, (int, float)) or not isinstance(col2, (int, float)):
            continue

        indice = float(col1)
        var_m  = float(col2)
        if indice <= 0:
            continue

        # XLS ordenado de más reciente a más viejo: primer match = más reciente
        resultado = {
            "fecha": f"{current_year}-{MESES_ES[mes_lower]:02d}",
            "indice": indice,
            "variacion_mensual": round(var_m, 2),
            "variacion_anual": None,
        }
        print(f"  IPC mas reciente encontrado: {resultado['fecha']}")
        return resultado

    raise ValueError("No se encontraron datos IPC validos en el XLS")



def scrape_ipc_bcv() -> dict:
    print(f"  Descargando XLS IPC desde: {BCV_IPC_XLS_URL}")
    content = http_get_bytes(BCV_IPC_XLS_URL)
    print(f"  XLS descargado: {len(content):,} bytes")
    return _parse_xls_bytes(content)


# ── Construcción del JSON ─────────────────────────────────────────────────────

def build_latest(tasas: dict, ipc: dict) -> dict:
    """
    Arma el objeto que se guarda en latest.json.
    'tasas' debe ser un dict con al menos 'usd', opcionalmente 'eur'.
    Se mantiene 'tasa_bcv' como alias de 'usd' para compatibilidad con
    consumidores existentes del JSON.
    """
    now = datetime.now(timezone.utc)
    return {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tasa_bcv":   tasas["usd"],   # alias de compatibilidad
        "tasas": {
            "usd": tasas["usd"],
            "eur": tasas.get("eur"),
        },
        "ipc": ipc,
    }


# ── Variación anual ───────────────────────────────────────────────────────────

def calcular_variacion_anual(
    ipc: dict, history_data: Optional[list]
) -> dict:
    """
    Busca en el historial el índice del mismo mes del año anterior
    y calcula: (indice_actual / indice_anterior - 1) * 100
    """
    if not history_data or "indice" not in ipc:
        return ipc

    fecha_actual = ipc["fecha"]  # "2026-04"
    try:
        anio, mes = fecha_actual.split("-")
        fecha_anterior = f"{int(anio) - 1}-{mes}"  # "2025-04"
    except ValueError:
        return ipc

    indice_anterior = None
    for entry in history_data:
        ipc_entry = entry.get("ipc", {})
        if ipc_entry.get("fecha") == fecha_anterior and ipc_entry.get("indice"):
            indice_anterior = ipc_entry["indice"]
            break

    if indice_anterior and indice_anterior != 0:
        var_anual = ((ipc["indice"] / indice_anterior) - 1) * 100
        ipc = {**ipc, "variacion_anual": round(var_anual, 2)}
        print(f"  Variación anual calculada: {ipc['variacion_anual']}% "
              f"(índice {ipc['indice']:.2f} vs {indice_anterior:.2f} en {fecha_anterior})")
    else:
        print(f"  Sin datos del año anterior ({fecha_anterior}) para calcular variación anual")

    return ipc


# ── Gestión de archivos JSON ──────────────────────────────────────────────────

def load_json(path: Path) -> Optional[object]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(path: Path, data, indent: int = 2):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    print(f"  Guardado: {path}")


# ── Historial ─────────────────────────────────────────────────────────────────

def update_history(history_data: Optional[list], latest: dict) -> list:
    """
    Añade o actualiza la entrada del mes actual en el historial.
    Clave de deduplicación: ipc['fecha'] ("YYYY-MM"), ya que el IPC
    es mensual y el workflow puede correr múltiples veces en el mes.
    """
    if history_data is None:
        history_data = []

    ipc_fecha = latest["ipc"]["fecha"]  # "2026-04"

    for i, entry in enumerate(history_data):
        if entry.get("ipc", {}).get("fecha") == ipc_fecha:
            history_data[i] = latest
            print(f"  Historial: actualizada entrada del mes {ipc_fecha}")
            return history_data

    history_data.append(latest)
    print(f"  Historial: añadida nueva entrada para {ipc_fecha}")
    return history_data


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("BCV Static API - Scraper")
    print("=" * 60)

    mode = os.environ.get("SCRAPER_MODE", "tasa").lower()
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()

    print(f"Modo: {mode}")

    latest_prev  = load_json(LATEST_FILE) or {}
    history_data = load_json(HISTORY_FILE)

    # Leer tasas previas: soporta tanto el formato nuevo (tasas.usd/eur)
    # como el formato legado (tasa_bcv) para retrocompatibilidad
    tasas_prev = latest_prev.get("tasas", {})
    if not tasas_prev and "tasa_bcv" in latest_prev:
        tasas_prev = {"usd": latest_prev["tasa_bcv"], "eur": None}

    ipc = latest_prev.get("ipc", {})

    # ── Obtener Tasas ─────────────────────────────────────────────────────
    if mode in ("tasa", "all", "both"):
        print("\n[1/2] Scrapeando tasas de cambio BCV (USD y EUR)...")
        try:
            tasas_nuevas = scrape_tasas_bcv()
            # Actualizar solo las tasas que se hayan obtenido correctamente
            tasas_prev.update(tasas_nuevas)
        except Exception as e:
            print(f"  ✗ ERROR obteniendo tasas: {e}")
            if not tasas_prev.get("usd"):
                print("  No hay tasa USD previa disponible, abortando.")
                sys.exit(1)
            print(f"  Usando tasas previas: {tasas_prev}")

    # ── Obtener IPC ───────────────────────────────────────────────────────
    if mode in ("ipc", "all", "both"):
        print("\n[2/2] Descargando IPC del BCV...")
        try:
            ipc_nuevo = scrape_ipc_bcv()
            print(f"  ✓ IPC: {ipc_nuevo}")
        except Exception as e:
            print(f"  ✗ ERROR obteniendo IPC: {e}")
            if not ipc:
                print("  No hay IPC previo disponible, abortando.")
                sys.exit(1)
            print(f"  Usando IPC previo: {ipc}")
            ipc_nuevo = None

        if ipc_nuevo:
            # Sin cambio de mes: no hay nada nuevo que guardar
            if ipc_nuevo.get("fecha") == ipc.get("fecha"):
                print(f"\n  IPC sin cambios ({ipc_nuevo['fecha']}) — el BCV no ha actualizado aún.")
                if mode == "ipc":
                    print("  Nada que guardar.")
                    sys.exit(0)
            else:
                ipc = calcular_variacion_anual(ipc_nuevo, history_data)

    # ── Validar datos mínimos ─────────────────────────────────────────────
    if not tasas_prev.get("usd") or not ipc:
        print("\nERROR: Datos incompletos (se requiere al menos USD e IPC), no se puede generar JSON.")
        sys.exit(1)

    # ── Construir, actualizar historial y guardar ─────────────────────────
    latest = build_latest(tasas_prev, ipc)
    history_data = update_history(history_data, latest)

    print("\nGuardando archivos...")
    save_json(LATEST_FILE, latest)
    save_json(HISTORY_FILE, history_data)

    print("\n✓ Completado exitosamente")
    print(f"  USD:  {latest['tasas']['usd']}")
    print(f"  EUR:  {latest['tasas']['eur']}")
    print(f"  IPC:  {latest['ipc']}")


if __name__ == "__main__":
    main()