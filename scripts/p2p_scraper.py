"""
P2P Scraper - Obtiene tasa de cambio USDT/VES y USDT/COP desde Binance P2P.
Diseñado para ejecutarse en GitHub Actions 3 veces al día y actualizar
el history.json existente con datos P2P diarios sin romper retrocompatibilidad.

Lógica replicada exactamente desde api_service.dart:
  - VES: mediana + (desviacion_std * 1.5), filtro dinámico = tasa_bcv * 30
  - COP: mediana pura,                      filtro fijo    = 1_000_000
  - rows: 20 anuncios (igual que la app)
"""

import json
import math
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ── Constantes ────────────────────────────────────────────────────────────────

CHITTY_LATEST_URL = "https://chitty400.github.io/chitty-bcv-api/latest.json"
BINANCE_P2P_URL   = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

HEADERS_GET = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ChittyBCV-P2P/1.0; "
        "+https://github.com/chitty400/chitty-bcv-api)"
    ),
    "Accept": "application/json",
}

HEADERS_POST = {
    **HEADERS_GET,
    "Content-Type": "application/json",
}

# Filtro fallback si BCV no está disponible (~$45 a tasas actuales)
FILTRO_VES_FALLBACK = 3_000.0
FILTRO_COP          = 1_000_000.0
P2P_ROWS            = 20  # igual que la app Flutter

# Paths — asume que el script está en la raíz del repo,
# igual que scraper.py, y los JSON viven en docs/
OUTPUT_DIR   = Path(__file__).parent / "docs"
HISTORY_FILE = OUTPUT_DIR / "history.json"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def http_get_json(url: str) -> Optional[dict]:
    try:
        if REQUESTS_AVAILABLE:
            r = requests.get(url, headers=HEADERS_GET, timeout=15)
            r.raise_for_status()
            return r.json()
        req = urllib.request.Request(url, headers=HEADERS_GET)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  ✗ GET {url} → {e}")
        return None


def http_post_json(url: str, payload: dict) -> Optional[dict]:
    body = json.dumps(payload).encode("utf-8")
    try:
        if REQUESTS_AVAILABLE:
            r = requests.post(url, headers=HEADERS_POST, data=body, timeout=15)
            r.raise_for_status()
            return r.json()
        req = urllib.request.Request(
            url, data=body, headers=HEADERS_POST, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  ✗ POST {url} → {e}")
        return None


# ── BCV ───────────────────────────────────────────────────────────────────────

def fetch_tasa_bcv() -> Optional[float]:
    """
    Lee tasa_bcv desde chitty latest.json.
    Solo se llama en el run 1 del día; los runs 2 y 3 la leen del history.
    """
    data = http_get_json(CHITTY_LATEST_URL)
    if not data:
        return None
    tasa = data.get("tasa_bcv") or data.get("tasas", {}).get("usd")
    if tasa and float(tasa) > 0:
        print(f"  ✓ Tasa BCV: {tasa}")
        return float(tasa)
    print("  ✗ tasa_bcv no encontrada en latest.json")
    return None


# ── Binance P2P ───────────────────────────────────────────────────────────────

def fetch_binance_p2p(fiat: str, monto_filtro: float) -> list[float]:
    """
    Llama a Binance P2P y retorna la lista de precios filtrados.
    Replica exactamente los filtros de api_service.dart:
      - Excluye anuncios promocionales (isPromotion == true)
      - Excluye anuncios cuyo minSingleTransAmount > monto_filtro
      - tradeType: BUY, asset: USDT, rows: 20
    """
    payload = {
        "asset":         "USDT",
        "fiat":          fiat,
        "merchantCheck": False,
        "page":          1,
        "rows":          P2P_ROWS,
        "publisherType": None,
        "transAmount":   str(int(monto_filtro)),
        "tradeType":     "BUY",
    }

    data = http_post_json(BINANCE_P2P_URL, payload)
    if not data:
        return []

    if data.get("code") != "000000":
        print(f"  ✗ Binance P2P {fiat}: código de error {data.get('code')}")
        return []

    data_list = data.get("data") or []
    if not data_list:
        print(f"  ✗ Binance P2P {fiat}: lista vacía")
        return []

    precios = []
    for anuncio in data_list:
        adv = anuncio.get("adv", {})

        # Excluir promocionales
        if adv.get("isPromotion") is True:
            continue

        # Excluir si el mínimo de transacción supera nuestro filtro
        min_amount = float(adv.get("minSingleTransAmount") or 0)
        if min_amount > monto_filtro:
            continue

        try:
            precios.append(float(adv["price"]))
        except (KeyError, ValueError, TypeError):
            continue

    return precios


def calcular_mediana(precios: list[float]) -> float:
    precios = sorted(precios)
    n = len(precios)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return precios[n // 2]
    return (precios[n // 2 - 1] + precios[n // 2]) / 2.0


def calcular_resultado_ves(precios: list[float]) -> float:
    """
    VES: mediana + (desviacion_std * 1.5)
    Replica exactamente la fórmula de api_service.dart líneas 528-534.
    """
    if not precios:
        return 0.0
    mediana  = calcular_mediana(precios)
    media    = sum(precios) / len(precios)
    varianza = sum((p - media) ** 2 for p in precios) / len(precios)
    desv     = math.sqrt(varianza) if varianza > 0 else 0.0
    return mediana + (desv * 1.5)


def calcular_resultado_cop(precios: list[float]) -> float:
    """COP: mediana pura."""
    return calcular_mediana(precios)


# ── Lógica principal del día ──────────────────────────────────────────────────

def scrape_p2p(tasa_bcv: Optional[float]) -> dict:
    """
    Ejecuta las dos llamadas P2P y retorna el resultado del snapshot.
    """
    # ── VES ──────────────────────────────────────────────────────────────────
    filtro_ves = (tasa_bcv * 30) if tasa_bcv and tasa_bcv > 0 else FILTRO_VES_FALLBACK
    print(f"\n  [VES] filtro={filtro_ves:.0f} Bs (tasa_bcv={tasa_bcv})")

    precios_ves = fetch_binance_p2p("VES", filtro_ves)
    if precios_ves:
        resultado_ves = calcular_resultado_ves(precios_ves)
        mediana_ves   = calcular_mediana(precios_ves)
        print(f"  ✓ VES precios ({len(precios_ves)}): mediana={mediana_ves:.2f} → resultado={resultado_ves:.2f}")
    else:
        resultado_ves = 0.0
        mediana_ves   = 0.0
        print("  ✗ VES: sin precios válidos")

    # ── COP ──────────────────────────────────────────────────────────────────
    print(f"\n  [COP] filtro={FILTRO_COP:.0f}")

    precios_cop = fetch_binance_p2p("COP", FILTRO_COP)
    if precios_cop:
        resultado_cop = calcular_resultado_cop(precios_cop)
        mediana_cop   = calcular_mediana(precios_cop)
        print(f"  ✓ COP precios ({len(precios_cop)}): mediana={mediana_cop:.2f} → resultado={resultado_cop:.2f}")
    else:
        resultado_cop = 0.0
        mediana_cop   = 0.0
        print("  ✗ COP: sin precios válidos")

    return {
        "ves": {
            "resultado": round(resultado_ves, 2),
            "precios":   [round(p, 2) for p in sorted(precios_ves)],
        },
        "cop": {
            "resultado": round(resultado_cop, 2),
            "precios":   [round(p, 2) for p in sorted(precios_cop)],
        },
    }


# ── Gestión del history.json ──────────────────────────────────────────────────

def load_history() -> list:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    return []


def save_history(history: list):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"\n  Guardado: {HISTORY_FILE}")


def fecha_mes(fecha_iso: str) -> str:
    """'2026-05-27' → '2026-05'"""
    return fecha_iso[:7]


def encontrar_entrada_mes(history: list, anio_mes: str) -> Optional[int]:
    """Retorna el índice de la entrada del mes en el historial, o None."""
    for i, entry in enumerate(history):
        # Soporta tanto 'date' ("2026-05-01") como 'ipc.fecha' ("2026-05")
        date_entry = entry.get("date", "")[:7]
        ipc_fecha  = entry.get("ipc", {}).get("fecha", "")[:7]
        if date_entry == anio_mes or ipc_fecha == anio_mes:
            return i
    return None


def update_history_p2p(
    history: list,
    fecha_hoy: str,        # "2026-05-27"
    tasa_bcv: Optional[float],
    snapshot: dict,
) -> list:
    """
    Inserta o actualiza los datos P2P del día en la entrada mensual correcta.
    
    Estrategia retrocompatible:
    - Si ya existe una entrada para este mes → le agrega/actualiza el sub-dict 'p2p'
    - Si no existe → crea una entrada mínima para el mes con solo p2p y tasa_bcv
      (el scraper.py principal la completará con IPC cuando corra)
    
    Dentro de 'p2p', cada snapshot del día se acumula en 'snapshots' (lista)
    y se recalcula 'medianas_del_dia' y 'tasa_final_promedio'.
    """
    anio_mes = fecha_mes(fecha_hoy)
    idx      = encontrar_entrada_mes(history, anio_mes)

    if idx is None:
        # Mes nuevo — entrada mínima; scraper.py la enriquecerá con IPC
        nueva_entrada = {
            "date":       f"{anio_mes}-01",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tasa_bcv":   tasa_bcv,
            "ipc":        {},   # se llenará cuando corra scraper.py
            "p2p":        {},
        }
        history.append(nueva_entrada)
        idx = len(history) - 1
        print(f"  Nueva entrada de mes creada: {anio_mes}")
    else:
        print(f"  Entrada de mes encontrada en índice {idx}: {anio_mes}")

    entry = history[idx]

    # Poblar tasa_bcv si estaba null y ahora la tenemos
    if tasa_bcv and not entry.get("tasa_bcv"):
        entry["tasa_bcv"] = tasa_bcv
        print(f"  tasa_bcv actualizada: {tasa_bcv}")

    # Inicializar sub-dict p2p si no existe
    if "p2p" not in entry:
        entry["p2p"] = {}

    # Entrada del día dentro de p2p
    dia_entry = entry["p2p"].get(fecha_hoy, {
        "tasa_bcv":  tasa_bcv,
        "ves": {"medianas_del_dia": [], "tasa_final_promedio": 0.0},
        "cop": {"medianas_del_dia": [], "tasa_final_promedio": 0.0},
    })

    # Acumular snapshot del run actual
    for moneda, key_resultado in (("ves", "resultado"), ("cop", "resultado")):
        resultado = snapshot[moneda][key_resultado]
        if resultado > 0:
            dia_entry[moneda]["medianas_del_dia"].append(resultado)

    # Recalcular promedio del día para cada moneda
    for moneda in ("ves", "cop"):
        medianas = dia_entry[moneda]["medianas_del_dia"]
        if medianas:
            promedio = round(sum(medianas) / len(medianas), 2)
            dia_entry[moneda]["tasa_final_promedio"] = promedio

    entry["p2p"][fecha_hoy] = dia_entry
    history[idx] = entry

    return history


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Chitty BCV — P2P Scraper")
    print("=" * 60)

    fecha_hoy = date.today().isoformat()  # "2026-05-27"
    print(f"Fecha: {fecha_hoy}")

    # ── Cargar historial existente ────────────────────────────────────────────
    history  = load_history()
    anio_mes = fecha_mes(fecha_hoy)
    idx      = encontrar_entrada_mes(history, anio_mes)

    # ── Tasa BCV ──────────────────────────────────────────────────────────────
    # Si ya hay una entrada del mes con tasa_bcv, la reutilizamos (runs 2 y 3).
    # Si no (run 1 del día o mes nuevo), la pedimos a chitty.
    tasa_bcv: Optional[float] = None

    if idx is not None:
        tasa_bcv_guardada = history[idx].get("tasa_bcv")
        if tasa_bcv_guardada and float(tasa_bcv_guardada) > 0:
            tasa_bcv = float(tasa_bcv_guardada)
            print(f"\n[BCV] Tasa del mes ya registrada: {tasa_bcv} (sin llamada extra)")

    if tasa_bcv is None:
        print("\n[BCV] Obteniendo tasa desde chitty latest.json...")
        tasa_bcv = fetch_tasa_bcv()
        if tasa_bcv is None:
            print(f"  ✗ BCV no disponible, usando fallback VES={FILTRO_VES_FALLBACK}")

    # ── Scraping P2P ──────────────────────────────────────────────────────────
    print("\n[P2P] Scrapeando Binance P2P...")
    snapshot = scrape_p2p(tasa_bcv)

    ves_ok = snapshot["ves"]["resultado"] > 0
    cop_ok = snapshot["cop"]["resultado"] > 0

    if not ves_ok and not cop_ok:
        print("\n✗ Sin datos P2P válidos — abortando sin guardar.")
        sys.exit(1)

    # ── Actualizar historial ──────────────────────────────────────────────────
    print("\n[HISTORY] Actualizando history.json...")
    history = update_history_p2p(history, fecha_hoy, tasa_bcv, snapshot)
    save_history(history)

    # ── Resumen ───────────────────────────────────────────────────────────────
    print("\n✓ Completado")
    if ves_ok:
        print(f"  VES: {snapshot['ves']['resultado']} Bs/USDT")
    if cop_ok:
        print(f"  COP: {snapshot['cop']['resultado']} COP/USDT")


if __name__ == "__main__":
    main()