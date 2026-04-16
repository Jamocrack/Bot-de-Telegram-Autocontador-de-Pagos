# 🤖 Autocontador Elite - Asistente Financiero para Telegram

Este es un bot de Telegram profesional diseñado para el seguimiento inteligente de ingresos y comprobantes de pago (Yape, Plin, PayPal, bancos internacionales, Binance, Lemon, etc.) utilizando **Inteligencia Artificial (Gemini 2.0 Flash)**. 

El sistema funciona de forma **100% local, privada y sin necesidad de servidores web**, convirtiéndose en la herramienta definitiva de contabilidad personal.

## 🌟 Características de Nivel Elite

- **🎮 Navegación Instantánea:** Dashboard y menús interactivos que funcionan editando el mismo mensaje, eliminando el desorden del chat.
- **🌍 IA Internacional:** Reconoce pagos de cualquier país y entidad (PayPal, Binance, Lemon, Bancos locales).
- **💱 Soporte Multi-moneda:** Detecta Soles, Dólares, Euros y USDT, con conversión automática a Soles en tiempo real.
- **🛡️ Seguridad por "Lista Blanca":** Tú decides en qué grupos puede trabajar el bot, protegiendo tu uso de la IA.
- **👑 Resumen General de Admin:** El dueño del bot tiene un botón especial para ver las estadísticas globales de todos los usuarios.
- **🧹 Sistema de Chat Limpio:** Borra automáticamente mensajes de error o estados temporales para mantener tus grupos impecables.
- **📊 Gestión Total:** Buscador integrado, reportes por categoría y exportación a CSV compatible con Excel.

---

## 🚀 Guía de Instalación Rápida

### 1. Preparación del Sistema
Asegúrate de tener **Python 3.10+** instalado. Descarga el repositorio y en una terminal ejecuta:
```bash
pip install -r requirements.txt
```

### 2. Configuración del Entorno (`.env`)
Crea un archivo llamado `.env` en la raíz del proyecto y rellénalo con tus credenciales:

```env
# --- Credenciales de Telegram ---
TELEGRAM_TOKEN=tu_token_aqui
# Chats autorizados para usar el bot (separados por coma)
AUTHORIZED_CHATS=-100123456789, 987654321

# --- Inteligencia Artificial ---
OPENROUTER_API_KEY=tu_key_aqui
MODEL_NAME=google/gemini-2.0-flash-001

# --- Administración ---
# Tu ID de Telegram para habilitar el Dashboard Maestro
ADMIN_USER_ID=tu_id_aqui
```

### 3. Ejecución
- **Windows:** Ejecuta el archivo `ejecutar_bot.bat`.
- **Manual:** Ejecuta `python bot.py`.

---

## 🛠️ Comandos Dinámicos

- `/commands`: Abre el **Centro de Comandos Interactivo** con botones.
- `/dashboard`: Panel de control para ver estadísticas, exportar datos o borrar registros.
- `/buscar [texto]`: Encuentra transacciones específicas por nombre o monto.
- `/consultar [persona/categoría]`: Reportes inteligentes y sumatorias instantáneas.

---

**Diseñado para ser potente, ligero y 100% privado.**
