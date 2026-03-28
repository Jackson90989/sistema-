#!/bin/bash

echo "=========================================="
echo "🐍 PYTHON SERVICE - UNIN Academic System"
echo "=========================================="

# Criar diretório para banco de dados
mkdir -p /app/database

echo "🐍 Starting Python Flask system..."
echo "   Database: /app/database"
echo "   Port: 5000"

# Iniciar serviço
python sistema_escolar.py