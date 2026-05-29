# 🏦 BCV Static API

[![Update BCV Data](https://github.com/chitty400/chitty-bcv-api/actions/workflows/update-bcv.yml/badge.svg)](https://github.com/chitty400/chitty-bcv-api/actions/workflows/update-bcv.yml)
[![P2P Rate Scraper](https://github.com/chitty400/chitty-bcv-api/actions/workflows/p2p.yml/badge.svg)](https://github.com/chitty400/chitty-bcv-api/actions/workflows/p2p.yml)

API estática gratuita con datos oficiales del **Banco Central de Venezuela (BCV)** y tasas P2P de Binance.  
Actualizada automáticamente con GitHub Actions. Consumible desde cualquier app sin backend.

---

## 📡 Endpoints

| Archivo | URL | Descripción |
|---------|-----|-------------|
| `latest.json` | `https://chitty400.github.io/chitty-bcv-api/latest.json` | Tasa BCV (USD y EUR) + IPC más reciente |
| `history.json` | `https://chitty400.github.io/chitty-bcv-api/history.json` | Serie histórica completa del IPC mensual |
| `p2p_history.json` | `https://chitty400.github.io/chitty-bcv-api/p2p_history.json` | Tasas P2P diarias USDT/VES y USDT/COP (Binance) |

---

## 📋 Schema

### `latest.json`
```json
{
  "updated_at": "2026-05-08T05:53:15Z",
  "tasa_bcv": 499.8608,
  "tasas": {
    "usd": 499.8608,
    "eur": 562.14
  },
  "ipc": {
    "fecha": "2026-04",
    "indice": 403528566746262.0,
    "variacion_mensual": 10.6,
    "variacion_anual": 611.86
  }
}
```

> `tasa_bcv` es un alias de `tasas.usd`, mantenido por compatibilidad con consumidores anteriores.

### `history.json`
```json
[
  {
    "date": "2026-05-08",
    "updated_at": "2026-05-08T19:30:00Z",
    "tasa_bcv": 100.26,
    "ipc": { "fecha": "2026-03", "variacion_mensual": 13.1, "variacion_anual": null }
  }
]
```

### `p2p_history.json`
```json
{
  "2026-05-27": {
    "tasa_bcv": 544.57,
    "ves": {
      "medianas_del_dia": [71.2, 71.8, 72.1],
      "tasa_final_promedio": 71.7
    },
    "cop": {
      "medianas_del_dia": [4200.1, 4250.5, 4280.8],
      "tasa_final_promedio": 4243.8
    }
  }
}
```

La tasa P2P de VES se calcula como `mediana + (desviación_estándar × 1.5)` sobre los 20 mejores anuncios BUY de Binance, replicando la lógica de la app. La de COP es mediana pura.

---

## ⚙️ Actualización

| Dato | Frecuencia | Fuente |
|------|-----------|--------|
| Tasa USD/VES y EUR/VES | Lun–vie ~1 AM hora local | `bcv.org.ve` (scraping HTML) |
| IPC mensual | Lun–vie ~2 AM hora local | XLS en `bcv.org.ve/estadisticas/consumidor` |
| Tasas P2P (VES y COP) | 3 veces al día (7 AM, 1 PM, 6 PM hora Venezuela) | Binance P2P API |

Para forzar una actualización manual:
- **Tasa BCV/IPC:** Actions → Update BCV Data → Run workflow → elegir modo (`tasa` / `ipc` / `all`)
- **P2P:** Actions → P2P Rate Scraper → Run workflow

---

## 🔍 ¿Cómo saber si los datos son frescos?

Verifica el campo `updated_at` en `latest.json`. Si tiene más de 3 días hábiles,  
el scraper puede estar caído — los badges de arriba también lo indican.

---

## 📱 Uso en Flutter

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Future<Map<String, dynamic>> fetchBCV() async {
  final response = await http.get(
    Uri.parse('https://chitty400.github.io/chitty-bcv-api/latest.json'),
  );
  return jsonDecode(response.body);
}

Future<Map<String, dynamic>> fetchP2P() async {
  final response = await http.get(
    Uri.parse('https://chitty400.github.io/chitty-bcv-api/p2p_history.json'),
  );
  return jsonDecode(response.body);
}
```

---

## 🛠️ Setup local

```bash
git clone https://github.com/chitty400/chitty-bcv-api
cd chitty-bcv-api
pip install beautifulsoup4 requests lxml xlrd

# Actualizar tasa BCV (USD + EUR)
python scripts/scraper.py tasa

# Actualizar IPC
python scripts/scraper.py ipc

# Actualizar todo
python scripts/scraper.py all

# Actualizar tasas P2P
pip install requests
python scripts/p2p_scraper.py
```

---

## 📂 Estructura

```
chitty-bcv-api/
├── .github/
│   └── workflows/
│       ├── update-bcv.yml      # Cron diario (tasa + IPC) + disparo manual
│       └── p2p.yml             # Cron 3×/día (tasas P2P Binance)
├── scripts/
│   ├── scraper.py              # Scraping tasa BCV (USD/EUR) e IPC
│   └── p2p_scraper.py          # Scraping USDT/VES y USDT/COP desde Binance P2P
├── docs/                       # ← GitHub Pages sirve desde aquí
│   ├── latest.json
│   ├── history.json
│   └── p2p_history.json
└── README.md
```

---

## ⚠️ Aviso legal

Los datos de tasa e IPC provienen del sitio oficial del BCV (`bcv.org.ve`).  
Los datos P2P provienen de la API pública de Binance.  
Este proyecto no está afiliado al BCV ni a Binance. Uso bajo tu propia responsabilidad.

---

## 🤝 Contribuciones

¿El scraper se rompió por un cambio en el BCV o en Binance? Abre un Issue o PR.  
Proyecto abierto para la comunidad de devs venezolanos 🇻🇪