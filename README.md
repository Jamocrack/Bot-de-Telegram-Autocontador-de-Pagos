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
ADMIN_USER_ID=tu_id_personal_aqui
```

---

## 🔑 Cómo obtener tus credenciales

Para que el bot funcione, necesitas llenar los valores en el archivo `.env`. Aquí te explicamos cómo conseguirlos:

### 1. Token de Telegram (`TELEGRAM_TOKEN`)
1. Habla con [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía el comando `/newbot` y sigue las instrucciones para darle un nombre.
3. Al finalizar, te dará un **API Token**. Cópialo y pégalo en el `.env`.

### 2. Tu ID de Telegram (`ADMIN_USER_ID`)
Este es tu número de identidad único en Telegram (necesario para ser el Administrador):
1. Escribe a [@userinfobot](https://t.me/userinfobot) y pulsa "Inicio".
2. Te responderá con tu **Id** (un número largo). Úsalo en `ADMIN_USER_ID`.

### 3. IDs de Grupos Autorizados (`AUTHORIZED_CHATS`)
El bot solo responderá en los chats que pongas aquí.
- **Para Chats Privados:** Es el mismo que tu ID personal.
- **Para Grupos:** Agrega a [@userinfobot](https://t.me/userinfobot) a tu grupo y te dirá el ID del grupo (suele empezar con `-100`).
- *Tip:* Puedes poner varios separados por comas.

### 4. API Key de IA (`OPENROUTER_API_KEY`)
1. Crea una cuenta en [OpenRouter.ai](https://openrouter.ai/).
2. Ve a la sección de [Keys](https://openrouter.ai/keys) y genera una nueva clave.
3. Asegúrate de tener créditos en tu cuenta para que la IA (Gemini) pueda procesar las imágenes.

---

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
