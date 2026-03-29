# Imagem Python otimizada
FROM python:3.11-slim

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Definir diretório de trabalho
WORKDIR /app

# Copiar requirements primeiro (cache)
COPY requirements.txt .

# Instalar dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fonte
COPY super_agente_simples.py .
COPY start.sh .

# Criar diretório para banco de dados
RUN mkdir -p /app/database

# Configurar variáveis
ENV PYTHONUNBUFFERED=1
ENV FLASK_PORT=5000

# Tornar script executável
RUN chmod +x /app/start.sh

# Expor porta
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# Comando de inicialização
CMD ["/app/start.sh"]