# 🤖 Bot Telegram Autocontador

Bot de Telegram que recibe comprobantes de pago como imágenes en un grupo,
extrae los datos con IA (Google Gemini via OpenRouter) y los expone en un
dashboard web en tiempo real.

---

## 📁 Estructura del proyecto

```
bot_telegram_autocontador/
├── bot.py              # Bot de Telegram (recibe fotos, llama a la IA)
├── processor.py        # Extracción de datos con OpenRouter / Gemini Flash
├── main.py             # Servidor FastAPI (dashboard + /api/history)
├── start.sh            # Arranca bot + servidor en paralelo (usado por Docker)
├── static/
│   └── index.html      # Dashboard con TailwindCSS
├── Dockerfile          # Imagen de producción
├── .dockerignore
├── requirements.txt
├── .env.example        # Plantilla de variables de entorno
├── temp/               # Imágenes temporales (se limpian automáticamente)
└── history.json        # Generado al procesar el primer pago
```

---

## ⚙️ Configuración del archivo `.env`

Copia la plantilla y rellena los valores reales:

```bash
cp .env.example .env
```

| Variable            | Dónde obtenerla |
|---------------------|-----------------|
| `TELEGRAM_TOKEN`    | Habla con [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OPENROUTER_API_KEY`| [openrouter.ai/keys](https://openrouter.ai/keys) → Create Key |
| `GROUP_ID`          | Añade [@userinfobot](https://t.me/userinfobot) al grupo → `/start` (número negativo) |

```env
TELEGRAM_TOKEN=7123456789:AAF...tu_token_aqui
OPENROUTER_API_KEY=sk-or-v1-...tu_key_aqui
GROUP_ID=-1001234567890
```

> **Importante:** nunca subas `.env` a Git. Está incluido en `.gitignore` y `.dockerignore`.

### Configuración del bot en Telegram

1. En BotFather: `/mybots` → tu bot → **Bot Settings** → **Group Privacy** → **Turn off**
   (necesario para que el bot lea imágenes en el grupo).
2. Agrega el bot al grupo como **administrador** (o al menos con permisos para leer mensajes).

---

## 🐳 Ejecución con Docker (recomendado)

### 1. Construir la imagen

```bash
docker build -t autocontador .
```

### 2. Ejecutar el contenedor

```bash
docker run -d \
  --name autocontador \
  --env-file .env \
  -p 8000:8000 \
  -v "$(pwd)/history.json:/app/history.json" \
  --restart unless-stopped \
  autocontador
```

| Flag | Propósito |
|------|-----------|
| `--env-file .env` | Inyecta las variables de entorno |
| `-p 8000:8000` | Expone el dashboard en `http://localhost:8000` |
| `-v history.json` | Persiste el historial **fuera** del contenedor |
| `--restart` | Reinicia automáticamente si el proceso cae |

### 3. Ver logs en tiempo real

```bash
docker logs -f autocontador
```

### 4. Detener

```bash
docker stop autocontador
```

---

## 💻 Ejecución local (sin Docker)

```bash
# 1. Entorno virtual
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar .env (ver sección anterior)
cp .env.example .env

# 4a. Terminal 1 — Bot
python bot.py

# 4b. Terminal 2 — Dashboard
uvicorn main:app --reload --port 8000
```

Dashboard disponible en: **http://localhost:8000**

---

## 🔄 Flujo completo

```
Usuario envía foto al grupo de Telegram
        │
        ▼
    bot.py  descarga imagen → /temp/
        │
        ▼
 processor.py  codifica en Base64 → POST a OpenRouter (Gemini Flash 1.5)
        │
        ▼
    IA devuelve JSON:  { emisor, monto, numero_operacion, fecha }
        │
        ├─→  bot.py publica  #REGISTRO_PAGO + JSON en el grupo
        └─→  history.json  (agrega registro, sin duplicados)
                │
                ▼
        main.py  /api/history  lee history.json
                │
                ▼
        index.html  Dashboard (auto-refresh 30 s)
```

---

## 🛠️ Dependencias

| Paquete | Versión | Uso |
|---------|---------|-----|
| `python-telegram-bot` | 21.9 | Bot API v20+ |
| `python-dotenv` | 1.0.1 | Variables de entorno |
| `aiohttp` | 3.10.11 | HTTP async → OpenRouter |
| `fastapi` | 0.115.12 | Servidor web / API |
| `uvicorn` | 0.34.0 | ASGI server |
| `httpx` | 0.28.1 | HTTP async → Telegram API |
