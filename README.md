# PromoEffect · OfficeMax Analytics

Aplicación web para análisis del efecto de promociones sobre **cualquier cantidad de SKUs** de una categoría OfficeMax. Incluye conclusiones generadas con IA (Claude).

---

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python · Flask |
| Análisis | pandas · numpy · matplotlib |
| Frontend | HTML + CSS + JS |
| IA | Claude claude-sonnet-4-20250514 (Anthropic API) |
| Deploy | Railway (gunicorn) |

---

## Estructura

```
.
├── app.py               # Backend Flask
├── index.html           # Frontend OfficeMax-branded
├── logo-officemax.png   # Logo oficial
├── requirements.txt
└── README.md
```

---

## Flujo de la app

1. Usuario sube CSV de ventas → la app lista departamentos disponibles
2. Usuario elige departamento → la app lista todos los SKUs con unidades/ingresos
3. Usuario selecciona los SKUs que quiere analizar (uno, varios o todos)
4. Usuario sube XLSX de promos (opcional)
5. La app genera:
   - Tabla comparativa Promo vs No-Promo
   - Gráfica de línea de tiempo semanal (3×N)
   - Gráfica de Event Study Pre/Durante/Post (±4 sem)
   - Cards de conclusión por SKU con veredicto automático
   - Conclusión ejecutiva generada por Claude (LLM)

---

## Especificaciones de archivos

### Ventas CSV (requerido)

| Columna | Requerida |
|---------|-----------|
| `tran_date` (YYYY-MM-DD) | ✅ |
| `dept_nm` | ✅ |
| `prod_nbr` | ✅ |
| `qty` | ✅ |
| `net_sale` | ✅ |
| `prod_desc` | Opcional |

### Promociones XLSX (opcional)

| Columna | Requerida |
|---------|-----------|
| `SKU` | ✅ |
| `Fecha_Inicio` (DD/MM/YYYY) | ✅ |
| `Fecha_Fin` (DD/MM/YYYY) | ✅ |
| `Pct_Descuento` | ✅ |

---

## Deploy en Railway

### 1. GitHub
```bash
git init
git add .
git commit -m "feat: PromoEffect v2"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/promoeffect.git
git push -u origin main
```

### 2. Railway
1. [railway.com](https://railway.com) → **New Project** → **Deploy from GitHub repo**
2. Selecciona el repositorio
3. **Variables de entorno** → añade:
   ```
   ANTHROPIC_API_KEY = sk-ant-...
   ```
4. **Settings → Networking → Generate Domain** → esa es tu URL pública

### 3. Correr local
```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... python app.py
# → http://localhost:5000
```

---

## Notas metodológicas

- **Costo estimado**: 60% del precio modal (margen ~40%)
- **Detección de promo**: oficial (archivo) → proxy precio ≥20% → spike unidades p90
- **Event Study**: ventana ±4 semanas, normalizado a Pre=100
- **Validación de margen**: bloquea SKUs con >30% de transacciones bajo costo
- **Conclusión IA**: generada por Claude claude-sonnet-4-20250514 con los datos reales del análisis
