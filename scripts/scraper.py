"""
BCV Scraper - Obtiene tasa de cambio USD e IPC del Banco Central de Venezuela
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
BCV_IPC_XLS_URL = (
    "https://www.bcv.org.ve/sites/default/files/precios_consumidor/"
    "4_5_7_indice_y_variaciones_mensuales_serie_desde_dic_2007_1.xls"
)

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


# ── Scraper de Tasa de Cambio ────────────────────────────────────────────────

def scrape_tasa_bcv() -> float:
    html = http_get_text(BCV_HOME_URL)
    soup = BeautifulSoup(html, "lxml")

    for selector in ["#dolar strong", ".dolar strong", "#dolar", ".tipo-cambio-dolar"]:
        tag = soup.select_one(selector)
        if tag:
            text = tag.get_text(strip=True).replace(",", ".")
            m = re.search(r"\d{2,6}[.,]\d{2,8}", text)
            if m:
                return float(m.group().replace(",", "."))

    text_full = soup.get_text(" ")
    candidates = re.findall(r"\b\d{2,6}[,\.]\d{5,10}\b", text_full)
    if candidates:
        return float(candidates[0].replace(",", "."))

    raise ValueError("No se pudo extraer la tasa de cambio del BCV")


# ── Scraper de IPC ────────────────────────────────────────────────────────────

def _parse_xls_bytes(content: bytes) -> dict:
    import subprocess
    import tempfile

    MESES_ES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }

    with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    csv_path = tmp_path.replace(".xls", ".csv")

    try:
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "csv",
             tmp_path, "--outdir", os.path.dirname(tmp_path)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise ValueError(f"LibreOffice falló: {result.stderr}")

        with open(csv_path, encoding="utf-8", errors="replace") as f:
            csv_text = f.read()
    finally:
        os.unlink(tmp_path)
        if os.path.exists(csv_path):
            os.unlink(csv_path)

    return _parse_ipc_csv(csv_text, MESES_ES)


def _parse_ipc_csv(text: str, meses: dict) -> dict:
    current_year = None

    for line in text.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        col0 = cols[0].strip()

        m = re.match(r"^(\d{4})", col0)
        if m:
            c1 = cols[1].strip() if len(cols) > 1 else ""
            if not c1 or not re.match(r"\d", c1):
                current_year = int(m.group(1))
                continue

        mes_lower = col0.lower()
        if mes_lower in meses and current_year and len(cols) >= 3:
            try:
                indice = float(cols[1])
                var_m  = float(cols[2])
                mes_num = meses[mes_lower]
                return {
                    "fecha": f"{current_year}-{mes_num:02d}",
                    "indice": indice,
                    "variacion_mensual": round(var_m, 2),
                    "variacion_anual": None,
                }
            except (ValueError, IndexError):
                pass

    raise ValueError("No se encontraron datos IPC válidos en el CSV")


def scrape_ipc_bcv() -> dict:
    print(f"  Descargando XLS IPC desde: {BCV_IPC_XLS_URL}")
    content = http_get_bytes(BCV_IPC_XLS_URL)
    print(f"  XLS descargado: {len(content):,} bytes")
    return _parse_xls_bytes(content)


# ── Construcción del JSON ─────────────────────────────────────────────────────

def build_latest(tasa: float, ipc: dict) -> dict:
    """Arma el objeto que se guarda en latest.json."""
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tasa_bcv": tasa,
        "ipc": ipc,
    }


# ── Variación anual ───────────────────────────────────────────────────────────

def calcular_variacion_anual(ipc: dict, history_data: list | None) -> dict:
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

def load_json(path: Path):
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

def update_history(history_data: list | None, latest: dict) -> list:
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

    tasa = latest_prev.get("tasa_bcv")
    ipc  = latest_prev.get("ipc", {})

    # ── Obtener Tasa ──────────────────────────────────────────────────────
    if mode in ("tasa", "all", "both"):
        print("\n[1/2] Scrapeando tasa de cambio BCV...")
        try:
            tasa = scrape_tasa_bcv()
            print(f"  ✓ Tasa USD/VES: {tasa}")
        except Exception as e:
            print(f"  ✗ ERROR obteniendo tasa: {e}")
            if tasa is None:
                print("  No hay tasa previa disponible, abortando.")
                sys.exit(1)
            print(f"  Usando tasa previa: {tasa}")

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
    if tasa is None or not ipc:
        print("\nERROR: Datos incompletos, no se puede generar JSON.")
        sys.exit(1)

    # ── Construir, actualizar historial y guardar ─────────────────────────
    latest = build_latest(tasa, ipc)
    history_data = update_history(history_data, latest)

    print("\nGuardando archivos...")
    save_json(LATEST_FILE, latest)
    save_json(HISTORY_FILE, history_data)

    print("\n✓ Completado exitosamente")
    print(f"  Tasa:  {latest['tasa_bcv']}")
    print(f"  IPC:   {latest['ipc']}")


if __name__ == "__main__":
    main()