# ─────────────────────────────────────────────
# Etapa única — imagen slim, capa de deps cacheada
# ─────────────────────────────────────────────
FROM python:3.11-slim

# ── Variables de entorno Python ──────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Dependencias (capa separada para cache) ──
# Se copian primero para que Docker reutilice esta
# capa mientras el código fuente no cambie.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Código fuente ────────────────────────────
COPY . .

# ── Directorios de runtime + permisos ────────
RUN mkdir -p temp logs static \
    && chmod +x start.sh \
    # Usuario sin privilegios para más seguridad
    && groupadd --system appgroup \
    && useradd --system --gid appgroup --no-create-home appuser \
    && chown -R appuser:appgroup /app

USER appuser

# ── Puerto de FastAPI ─────────────────────────
EXPOSE 8000

# ── Health check ─────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/history')" \
    || exit 1

# ── Inicio de ambos procesos ──────────────────
CMD ["./start.sh"]
