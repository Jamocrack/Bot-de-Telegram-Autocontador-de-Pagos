#!/bin/bash
# start.sh — Arranca el bot de Telegram y el servidor FastAPI en paralelo.
# Docker ejecuta este script como PID 1; manejamos SIGTERM correctamente.

set -e

# ── Función de limpieza ───────────────────────────────────────────────────────
cleanup() {
    echo "🛑  Señal recibida. Cerrando procesos (bot PID=$BOT_PID, web PID=$WEB_PID)..."
    kill "$BOT_PID" "$WEB_PID" 2>/dev/null || true
    wait "$BOT_PID" "$WEB_PID" 2>/dev/null || true
    echo "✅  Apagado limpio."
    exit 0
}

trap cleanup SIGTERM SIGINT

# ── Iniciar Bot de Telegram ───────────────────────────────────────────────────
echo "🤖  Iniciando Bot de Telegram (bot.py)..."
python bot.py &
BOT_PID=$!

# ── Iniciar servidor FastAPI ──────────────────────────────────────────────────
echo "🌐  Iniciando FastAPI en 0.0.0.0:8000..."
uvicorn main:app --host 0.0.0.0 --port 8000 &
WEB_PID=$!

echo "🚀  Ambos procesos activos. Bot PID=$BOT_PID | Web PID=$WEB_PID"

# ── Monitorear: si cualquiera termina inesperadamente, cerrar todo ────────────
# wait -n requiere bash 4.3+ (Debian bookworm incluye bash 5.x) ✓
wait -n "$BOT_PID" "$WEB_PID"
EXIT_CODE=$?

echo "⚠️   Un proceso terminó con código $EXIT_CODE. Iniciando apagado..."
cleanup
exit $EXIT_CODE
