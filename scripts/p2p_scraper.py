"""
P2P Scraper - Obtiene tasa de cambio USDT/VES y USDT/COP desde Binance P2P.
Diseñado para ejecutarse en GitHub Actions 3 veces al día y actualizar
docs/p2p_history.json con un registro diario de tasas P2P.

Lógica replicada exactamente desde api_service.dart:
  - VES: mediana + (desviacion_std * 1.5), filtro dinámico = tasa_bcv * 30
  - COP: mediana pura,                      filtro fijo    = 1_000_000
  - rows: 20 anuncios (igual que la app)

Estructura de p2p_history.json:
{
  "2026-05-27": {
    "tasa_bcv": 544.57,
    "ves": {
      "medianas_del_dia": [71.2, 71.8, 72.1],
      "tasa_final_promedio": 71.7
    },
    "cop": {
      "medianas_del_dia": [42.1, 42.5, 42.8],
      "tasa_final_promedio": 42.46
    }
  }
}
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

FILTRO_VES_FALLBACK = 3_000.0   # fallback si BCV no está disponible
FILTRO_COP          = 1_000_000.0
P2P_ROWS            = 20        # igual que la app Flutter

OUTPUT_DIR      = Path(__file__).parent.parent / "docs"
P2P_HISTORY_FILE = OUTPUT_DIR / "p2p_history.json"


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
    Solo se llama si no hay tasa guardada para hoy en p2p_history.json.
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
      - Excluye promocionales (isPromotion == true)
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
        if adv.get("isPromotion") is True:
            continue
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
    """VES: mediana + (desviacion_std * 1.5) — replica api_service.dart líneas 528-534."""
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


# ── Scraping P2P ──────────────────────────────────────────────────────────────

def scrape_p2p(tasa_bcv: Optional[float]) -> dict:
    filtro_ves = (tasa_bcv * 30) if tasa_bcv and tasa_bcv > 0 else FILTRO_VES_FALLBACK
    print(f"\n  [VES] filtro={filtro_ves:.0f} Bs (tasa_bcv={tasa_bcv})")

    precios_ves = fetch_binance_p2p("VES", filtro_ves)
    if precios_ves:
        resultado_ves = calcular_resultado_ves(precios_ves)
        print(f"  ✓ VES ({len(precios_ves)} anuncios): resultado={resultado_ves:.2f}")
    else:
        resultado_ves = 0.0
        print("  ✗ VES: sin precios válidos")

    print(f"\n  [COP] filtro={FILTRO_COP:.0f}")
    precios_cop = fetch_binance_p2p("COP", FILTRO_COP)
    if precios_cop:
        resultado_cop = calcular_resultado_cop(precios_cop)
        print(f"  ✓ COP ({len(precios_cop)} anuncios): resultado={resultado_cop:.2f}")
    else:
        resultado_cop = 0.0
        print("  ✗ COP: sin precios válidos")

    return {
        "ves": round(resultado_ves, 2),
        "cop": round(resultado_cop, 2),
    }


# ── Gestión de p2p_history.json ───────────────────────────────────────────────

def load_p2p_history() -> dict:
    if P2P_HISTORY_FILE.exists():
        with open(P2P_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    return {}


def save_p2p_history(history: dict):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(P2P_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"\n  Guardado: {P2P_HISTORY_FILE}")


def update_p2p_history(
    history: dict,
    fecha_hoy: str,
    tasa_bcv: Optional[float],
    snapshot: dict,
) -> dict:
    """
    Inserta o actualiza la entrada del día con el nuevo snapshot.
    Acumula los resultados de cada run en medianas_del_dia
    y recalcula tasa_final_promedio.
    """
    entrada = history.get(fecha_hoy, {
        "tasa_bcv": tasa_bcv,
        "ves": {"medianas_del_dia": [], "tasa_final_promedio": 0.0},
        "cop": {"medianas_del_dia": [], "tasa_final_promedio": 0.0},
    })

    # Poblar tasa_bcv si el primer run la trajo
    if tasa_bcv and not entrada.get("tasa_bcv"):
        entrada["tasa_bcv"] = tasa_bcv

    # Acumular resultado del run actual
    for moneda in ("ves", "cop"):
        resultado = snapshot[moneda]
        if resultado > 0:
            entrada[moneda]["medianas_del_dia"].append(resultado)

    # Recalcular promedio del día
    for moneda in ("ves", "cop"):
        medianas = entrada[moneda]["medianas_del_dia"]
        if medianas:
            entrada[moneda]["tasa_final_promedio"] = round(
                sum(medianas) / len(medianas), 2
            )

    history[fecha_hoy] = entrada
    return history


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Chitty BCV — P2P Scraper")
    print("=" * 60)

    fecha_hoy = date.today().isoformat()
    print(f"Fecha: {fecha_hoy}")

    # ── Cargar historial ──────────────────────────────────────────────────────
    history = load_p2p_history()

    # ── Tasa BCV ──────────────────────────────────────────────────────────────
    # Reutilizar la del día si ya existe (runs 2 y 3), si no llamar a chitty
    tasa_bcv: Optional[float] = None

    entrada_hoy = history.get(fecha_hoy, {})
    tasa_guardada = entrada_hoy.get("tasa_bcv")
    if tasa_guardada and float(tasa_guardada) > 0:
        tasa_bcv = float(tasa_guardada)
        print(f"\n[BCV] Tasa del día ya registrada: {tasa_bcv} (sin llamada extra)")
    else:
        print("\n[BCV] Obteniendo tasa desde chitty latest.json...")
        tasa_bcv = fetch_tasa_bcv()
        if tasa_bcv is None:
            print(f"  ✗ BCV no disponible, usando fallback filtro={FILTRO_VES_FALLBACK}")

    # ── Scraping P2P ──────────────────────────────────────────────────────────
    print("\n[P2P] Scrapeando Binance P2P...")
    snapshot = scrape_p2p(tasa_bcv)

    if snapshot["ves"] == 0.0 and snapshot["cop"] == 0.0:
        print("\n✗ Sin datos P2P válidos — abortando sin guardar.")
        sys.exit(1)

    # ── Actualizar y guardar ──────────────────────────────────────────────────
    print("\n[HISTORY] Actualizando p2p_history.json...")
    history = update_p2p_history(history, fecha_hoy, tasa_bcv, snapshot)
    save_p2p_history(history)

    # ── Resumen ───────────────────────────────────────────────────────────────
    entrada = history[fecha_hoy]
    print("\n✓ Completado")
    print(f"  VES tasa_final_promedio: {entrada['ves']['tasa_final_promedio']}")
    print(f"  COP tasa_final_promedio: {entrada['cop']['tasa_final_promedio']}")
    runs = len(entrada['ves']['medianas_del_dia'])
    print(f"  Runs del día: {runs}/3")


if __name__ == "__main__":
    main()