#!/bin/bash

echo "=========================================="
echo "🐍 PYTHON SERVICE - UNIN Academic System"
echo "=========================================="

# Criar diretório para banco de dados
mkdir -p /app/database

echo "🐍 Starting Python Flask system..."
echo "   Database: /app/database"
echo "   Port: 5000"

# CORREÇÃO: Executar o arquivo correto
python /app/super_agente_simples.py