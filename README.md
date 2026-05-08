# 🏦 BCV Static API

[![Update BCV Data](https://github.com/chitty400/chitty-bcv-api/actions/workflows/update-bcv.yml/badge.svg)](https://github.com/chitty400/chitty-bcv-api/actions/workflows/update-bcv.yml)

API estática gratuita con datos oficiales del **Banco Central de Venezuela (BCV)**.  
Actualizada automáticamente con GitHub Actions. Consumible desde cualquier app sin backend entrega Tasa BCV del dia y INPC mensual.

---

## 📡 Endpoints

| Archivo | URL | Descripción |
|---------|-----|-------------|
| `latest.json` | `https://chitty400.github.io/chitty-bcv-api/latest.json` | Dato más reciente (tasa + IPC) |
| `history.json` | `https://chitty400.github.io/chitty-bcv-api/history.json` | Serie histórica completa del INPC (sin tasa BCV) |

---

## 📋 Schema

### `latest.json`
```json
{
  "updated_at": "2026-05-08T19:30:00Z",
  "tasa_bcv": 100.26,
  "ipc": {
    "fecha": "2026-03",
    "variacion_mensual": 13.1,
    "variacion_anual": null
  }
}
```

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

---

## ⚙️ Actualización

| Dato | Frecuencia | Fuente BCV |
|------|-----------|------------|
| Tasa USD/VES | Diaria (lun–vie, ~1AM Hora local) | `bcv.org.ve` (scraping HTML) |
| IPC mensual | Diaria (lun–vie, ~2AM Hora local) | XLS en `bcv.org.ve/estadisticas/consumidor` |

Para forzar una actualización del IPC: **Actions → Update BCV Data → Run workflow → mode: ipc**

---

## 🔍 ¿Cómo saber si los datos son frescos?

Verifica el campo `updated_at` en `latest.json`. Si tiene más de 3 días hábiles,  
el scraper puede estar caído — el badge de arriba también lo indica.

---

## 📱 Uso en Flutter

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Future<Map<String, dynamic>> fetchBCV() async {
  final response = await http.get(
    Uri.parse('https://chitty400.github.io/bcv-api/latest.json'),
  );
  return jsonDecode(response.body);
}
```

---

## 🛠️ Setup local

```bash
git clone https://github.com/chitty400/chitty-bcv-api
cd bcv-api
pip install -r scripts/requirements.txt

# Actualizar tasa
python scripts/scraper.py tasa

# Actualizar IPC
python scripts/scraper.py ipc

# Actualizar todo
python scripts/scraper.py all
```

---

## 📂 Estructura

```
bcv-api/
├── .github/
│   └── workflows/
│       └── update-bcv.yml      # Cron diario + disparo manual
├── scripts/
│   ├── scraper.py              # Lógica de scraping
│   └── requirements.txt
├── docs/                       # ← GitHub Pages sirve desde aquí
│   ├── latest.json
│   └── history.json
└── README.md
```

---

## ⚠️ Aviso legal

Los datos provienen del sitio oficial del BCV (`bcv.org.ve`).  
Este proyecto no está afiliado al BCV. Uso bajo tu propia responsabilidad.

---

## 🤝 Contribuciones

¿El scraper se rompió por un cambio en el BCV? Abre un Issue o PR.  
Proyecto abierto para la comunidad de devs venezolanos 🇻🇪
