#!/usr/bin/env python3
"""
🏫 SISTEMA ESCOLAR COMPLETO COM WHATSAPP E GEMINI AI
- Cadastro profissional via WhatsApp
- IA com Gemini para respostas naturais
- Fallback inteligente quando Gemini offline
- Interface web para alunos e secretaria
- LGPD compliance
- ✅ Otimizado para deploy no Render
"""

# ==================== IMPORTS ====================
import queue
import threading
import sqlite3
import os
import sys
import hashlib
import secrets
import requests
import json
import re
import time
import atexit
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from dotenv import load_dotenv

# Carregar variáveis de ambiente do arquivo .env
load_dotenv()

# ==================== IMPORTS DO GEMINI ====================
try:
    from google import genai
    GEMINI_DISPONIVEL = True
    print("✅ Google GenAI package loaded successfully")
except ImportError:
    GEMINI_DISPONIVEL = False
    print("⚠️ google-genai não instalado. Execute: pip install google-genai")

# ==================== CONFIGURAÇÕES ====================
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "database" / "escola.db"

# Configurações do WhatsApp (usando .env)
WHATSAPP_API_URL = os.getenv('WHATSAPP_API_URL', 'http://localhost:3000')
WHATSAPP_API_KEY = os.getenv('WHATSAPP_API_KEY', '')
WHATSAPP_API_TIMEOUT = int(os.getenv('WHATSAPP_API_TIMEOUT', '15'))

# Configurações do Flask
SECRET_KEY = os.getenv('SECRET_KEY', secrets.token_hex(32))

# Configurações do Gemini - VEM DO .ENV!
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
GEMINI_TIMEOUT = int(os.getenv('GEMINI_TIMEOUT', '60'))

# Verificação da chave
if not GEMINI_API_KEY:
    print("⚠️ ATENÇÃO: GEMINI_API_KEY não encontrada no arquivo .env!")
    print("   Adicione no arquivo .env: GEMINI_API_KEY=sua_chave_aqui")
    print("   Continuando sem Gemini (usando fallback)...")
else:
    print(f"✅ Gemini API Key carregada do .env: {GEMINI_API_KEY[:15]}...")

# Branding da instituicao
INSTITUICAO_NOME = "UNIN"
INSTITUICAO_QUALIDADE = "uma faculdade muito boa"

# Fila para mensagens em lote
fila_mensagens = queue.Queue()

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(hours=1)

# Cache para evitar processamento duplicado
ultimas_mensagens = {}

# Cliente Gemini global
gemini_client = None

# ==================== CLASSE GEMINI INTEGRATION ====================

class GeminiClient:
    """
    Cliente para integração com a API do Gemini
    """
    
    def __init__(self, api_key=None, model="gemini-2.5-flash"):
        """
        Inicializa o cliente Gemini
        """
        self.api_key = api_key or GEMINI_API_KEY
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não configurada!")
        
        self.model = model
        self.client = None
        self._inicializar_cliente()
        
    def _inicializar_cliente(self):
        """Inicializa o cliente Google GenAI"""
        try:
            if not GEMINI_DISPONIVEL:
                raise ImportError("google-genai não instalado")
            
            self.client = genai.Client(api_key=self.api_key)
            print(f"✅ Gemini client initialized with model: {self.model}")
        except Exception as e:
            print(f"❌ Error initializing Gemini: {e}")
            raise
    
    def gerar_resposta(self, pergunta, system_prompt=None, contexto=None, 
                       historico=None, temperatura=0.7, max_tokens=500):
        """
        Gera resposta usando o Gemini
        """
        try:
            # Construir o prompt completo
            prompt_final = self._construir_prompt(
                pergunta, system_prompt, contexto, historico
            )
            
            # Configurar parâmetros de geração
            config = {
                "temperature": temperatura,
                "max_output_tokens": max_tokens,
                "top_p": 0.95,
                "top_k": 40
            }
            
            # Chamar a API
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt_final,
                config=config
            )
            
            return response.text
            
        except Exception as e:
            print(f"❌ Gemini API error: {e}")
            return self._resposta_fallback(pergunta)
    
    def _construir_prompt(self, pergunta, system_prompt, contexto, historico):
        """Constrói o prompt completo"""
        partes = []
        
        if system_prompt:
            partes.append(f"System: {system_prompt}\n")
        
        if contexto:
            if isinstance(contexto, dict):
                contexto_str = json.dumps(contexto, ensure_ascii=False, indent=2)[:1000]
                partes.append(f"Context: {contexto_str}\n")
            elif isinstance(contexto, str):
                partes.append(f"Context: {contexto}\n")
        
        if historico and len(historico) > 0:
            partes.append("Previous conversation:")
            for msg in historico[-5:]:
                role = "User" if msg.get('role') == 'user' else "Assistant"
                content = msg.get('mensagem', msg.get('content', ''))
                partes.append(f"{role}: {content}")
            partes.append("")
        
        partes.append(f"User: {pergunta}")
        partes.append("Assistant:")
        
        return "\n".join(partes)
    
    def _resposta_fallback(self, pergunta):
        """Resposta de fallback"""
        return "⚠️ Desculpe, estou com dificuldades técnicas. Por favor, tente novamente."
    
    def verificar_status(self):
        """Verifica se a API está funcionando"""
        try:
            if not self.client:
                return False
            response = self.client.models.generate_content(
                model=self.model,
                contents="Teste",
                config={"max_output_tokens": 10}
            )
            return True
        except Exception as e:
            print(f"Status check error: {e}")
            return False


# ==================== FUNÇÕES GEMINI ====================

def verificar_gemini():
    """Verifica se o Gemini está configurado"""
    global gemini_client
    try:
        if gemini_client is None:
            if not GEMINI_API_KEY:
                return False
            gemini_client = GeminiClient(api_key=GEMINI_API_KEY, model=GEMINI_MODEL)
        return gemini_client.verificar_status()
    except Exception as e:
        print(f"Error checking Gemini: {e}")
        return False


def perguntar_gemini(pergunta, contexto, tipo_usuario, dados_usuario=None, 
                     numero_whatsapp=None):
    """Envia pergunta para o Gemini"""
    global gemini_client
    
    if not gemini_client:
        if not verificar_gemini():
            return resposta_fallback(pergunta, tipo_usuario, dados_usuario)
    
    # Construir system prompt baseado no tipo de usuário
    if tipo_usuario == 'secretaria':
        system_prompt = f"""Você é a SECRETARIA da faculdade {INSTITUICAO_NOME}. 
Nome: {dados_usuario.get('secretaria_nome', 'Secretaria') if dados_usuario else 'Secretaria'}

ACESSO TOTAL - Você vê TODOS os alunos:
Total de alunos: {contexto.get('total_alunos', 0) if contexto else 0}

FUNÇÕES:
- Listar alunos, ver dados completos
- Gerenciar cadastros
- Responder perguntas administrativas
- Ser profissional e prestativo

Responda em português do Brasil."""

    elif tipo_usuario == 'aluno':
        nome_aluno = dados_usuario.get('aluno_nome', 'Aluno') if dados_usuario else 'Aluno'
        system_prompt = f"""Você é um assistente acadêmico da {INSTITUICAO_NOME} para o ALUNO {nome_aluno}

DADOS DO ALUNO:
{json.dumps(contexto, indent=2, ensure_ascii=False)[:800] if contexto else 'Não disponível'}

FUNÇÕES:
- Mostrar dados do aluno quando solicitado
- Ajudar com requerimentos acadêmicos
- Responder dúvidas sobre o curso
- Ser amigável e prestativo

Responda em português do Brasil."""

    else:  # público
        system_prompt = f"""Você é um assistente da {INSTITUICAO_NOME} para ATENDIMENTO AO PÚBLICO.

{INSTITUICAO_NOME} é {INSTITUICAO_QUALIDADE}.

FUNÇÕES:
- Tirar dúvidas sobre a faculdade (cursos, valores, documentos, localização)
- Orientar sobre cadastro
- Explicar cursos e documentos
- Ser acolhedor e prestativo

REGRAS:
- Se o usuário demonstrar interesse em se cadastrar, direcione para "quero me cadastrar"
- Use emojis para tornar a conversa mais amigável
- Responda em português do Brasil"""

    try:
        resposta = gemini_client.gerar_resposta(
            pergunta=pergunta,
            system_prompt=system_prompt,
            contexto=contexto,
            historico=[],
            temperatura=0.7,
            max_tokens=500
        )
        
        print(f"Gemini reply: {resposta[:100]}...")
        return resposta
        
    except Exception as e:
        print(f"Error in Gemini request: {e}")
        return resposta_fallback(pergunta, tipo_usuario, dados_usuario)


# ==================== BANCO DE DADOS ====================
def get_db():
    os.makedirs(BASE_DIR / "database", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Inicializa o banco com todas as tabelas"""
    os.makedirs(BASE_DIR / "database", exist_ok=True)
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Criar tabela de usuários (secretaria)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            telefone TEXT,
            tipo TEXT DEFAULT 'secretaria',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Criar tabela alunos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alunos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            cpf TEXT UNIQUE,
            telefone TEXT,
            whatsapp TEXT,
            data_nascimento DATE,
            endereco TEXT,
            cidade TEXT,
            estado TEXT,
            curso_interesse TEXT,
            senha TEXT NOT NULL,
            status TEXT DEFAULT 'Pré-cadastro',
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            consentimento_dados BOOLEAN DEFAULT 0,
            consentimento_comunicacao BOOLEAN DEFAULT 0,
            data_consentimento TIMESTAMP,
            ip_consentimento TEXT
        )
    """)
    
    # Tabela de conversas de cadastro
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversas_cadastro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            etapa TEXT DEFAULT 'inicio',
            dados TEXT,
            ip TEXT,
            user_agent TEXT,
            criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Criar secretaria padrão
    cursor.execute("SELECT COUNT(*) as total FROM usuarios")
    row = cursor.fetchone()
    total_usuarios = row['total'] if row else 0
    
    if total_usuarios == 0:
        senha_admin = hashlib.sha256("admin123".encode()).hexdigest()
        cursor.execute("""
            INSERT INTO usuarios (nome, email, senha, tipo)
            VALUES (?, ?, ?, ?)
        """, ("Secretaria Geral", "secretaria@unin.edu", senha_admin, "secretaria"))
        print("Admin user created: secretaria@unin.edu / admin123")
    
    # Criar alunos de exemplo
    cursor.execute("SELECT COUNT(*) as total FROM alunos")
    row = cursor.fetchone()
    total_alunos = row['total'] if row else 0
    
    if total_alunos == 0:
        print("Creating sample students...")
        
        alunos_exemplo = [
            ('João Silva', 'joao.silva@email.com', '12345678901', '(11) 99999-1111', '1995-05-10', hashlib.sha256("joao123".encode()).hexdigest()),
            ('Maria Souza', 'maria.souza@email.com', '98765432101', '(11) 98888-2222', '1998-08-15', hashlib.sha256("maria123".encode()).hexdigest()),
            ('Pedro Santos', 'pedro.santos@email.com', '45678912301', '(11) 97777-3333', '1997-03-20', hashlib.sha256("pedro123".encode()).hexdigest()),
        ]
        
        for aluno in alunos_exemplo:
            try:
                cursor.execute("""
                    INSERT INTO alunos (nome, email, cpf, telefone, data_nascimento, senha, consentimento_dados, status)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 'Ativo')
                """, aluno)
                print(f"   Student created: {aluno[1]}")
            except Exception as e:
                print(f"   Warning: failed to create student {aluno[1]}: {e}")
    
    conn.commit()
    conn.close()
    print("\nDatabase initialized successfully!")


# ==================== FUNÇÕES AUXILIARES ====================
def limpar_numero_whatsapp(numero):
    """Limpa o número do WhatsApp para formato padronizado"""
    if not numero:
        return ""
    numero_limpo = re.sub(r'[^0-9]', '', str(numero))
    if numero_limpo.startswith('55') and len(numero_limpo) >= 12:
        return numero_limpo
    if len(numero_limpo) == 10 or len(numero_limpo) == 11:
        return '55' + numero_limpo
    return numero_limpo


def formatar_numero_whatsapp(numero):
    """Formata número para envio no WhatsApp"""
    numero_limpo = limpar_numero_whatsapp(numero)
    return f"{numero_limpo}@c.us"

import requests

def enviar_whatsapp(chat_id, mensagem):
    headers = {
        "Content-Type": "application/json"
    }

    if WHATSAPP_API_KEY:
        headers["x-api-key"] = WHATSAPP_API_KEY

    payload = {
        "chatId": chat_id,   # 👈 EXATAMENTE COMO VEIO
        "text": mensagem
    }

    response = requests.post(
        f"{WHATSAPP_API_URL}/api/sendText",
        json=payload,
        headers=headers,
        timeout=15
    )

    print("WAHA:", response.status_code, response.text)
    return response.status_code == 200



# ==================== GERENCIADOR DE CADASTRO WHATSAPP ====================

class GerenciadorCadastroWhatsApp:
    """Gerenciador de cadastro via WhatsApp com fluxo completo"""
    
    CURSOS_VALIDOS = [
        'administração', 'engenharia civil', 'engenharia da computação',
        'direito', 'medicina', 'psicologia', 'arquitetura', 'pedagogia'
    ]
    
    ESTADOS_VALIDOS = {
        'AC': 'Acre', 'AL': 'Alagoas', 'AP': 'Amapá', 'AM': 'Amazonas',
        'BA': 'Bahia', 'CE': 'Ceará', 'DF': 'Distrito Federal', 'ES': 'Espírito Santo',
        'GO': 'Goiás', 'MA': 'Maranhão', 'MT': 'Mato Grosso', 'MS': 'Mato Grosso do Sul',
        'MG': 'Minas Gerais', 'PA': 'Pará', 'PB': 'Paraíba', 'PR': 'Paraná',
        'PE': 'Pernambuco', 'PI': 'Piauí', 'RJ': 'Rio de Janeiro', 'RN': 'Rio Grande do Norte',
        'RS': 'Rio Grande do Sul', 'RO': 'Rondônia', 'RR': 'Roraima', 'SC': 'Santa Catarina',
        'SP': 'São Paulo', 'SE': 'Sergipe', 'TO': 'Tocantins'
    }
    
    def __init__(self, numero):
        self.numero = limpar_numero_whatsapp(numero)
        self.session_id = f"whatsapp_{self.numero}"
        self.carregar_estado()
    
    def carregar_estado(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM conversas_cadastro 
            WHERE session_id = ? ORDER BY atualizada_em DESC LIMIT 1
        """, (self.session_id,))
        conversa = cursor.fetchone()
        conn.close()
        
        if conversa:
            self.etapa = conversa['etapa']
            self.dados = json.loads(conversa['dados']) if conversa['dados'] else {}
        else:
            self.etapa = 'inicio'
            self.dados = {'whatsapp': self.numero}
            self._salvar()
    
    def _salvar(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM conversas_cadastro 
            WHERE session_id = ? ORDER BY atualizada_em DESC LIMIT 1
        """, (self.session_id,))
        existe = cursor.fetchone()
        
        if existe:
            cursor.execute("""
                UPDATE conversas_cadastro 
                SET etapa = ?, dados = ?, atualizada_em = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (self.etapa, json.dumps(self.dados), existe['id']))
        else:
            cursor.execute("""
                INSERT INTO conversas_cadastro (session_id, etapa, dados, ip, user_agent)
                VALUES (?, ?, ?, ?, ?)
            """, (self.session_id, self.etapa, json.dumps(self.dados), "whatsapp", "WhatsApp"))
        
        conn.commit()
        conn.close()
    
    def processar_etapa_atual(self, mensagem):
        mensagem = mensagem.strip()
        mensagem_lower = mensagem.lower()
        
        # Cancelar
        if any(p in mensagem_lower for p in ['cancelar', 'sair', 'parar']):
            self.etapa = 'inicio'
            self.dados = {'whatsapp': self.numero}
            self._salvar()
            return "✅ Cadastro cancelado! Diga 'quero me cadastrar' quando quiser continuar."
        
        # Voltar
        if any(p in mensagem_lower for p in ['voltar', 'anterior']):
            return self.voltar_etapa()
        
        # Fluxo por etapa
        if self.etapa == 'nome':
            return self.processar_nome(mensagem)
        elif self.etapa == 'email':
            return self.processar_email(mensagem)
        elif self.etapa == 'cpf':
            return self.processar_cpf(mensagem)
        elif self.etapa == 'telefone':
            return self.processar_telefone(mensagem)
        elif self.etapa == 'nascimento':
            return self.processar_nascimento(mensagem)
        elif self.etapa == 'endereco':
            return self.processar_endereco(mensagem)
        elif self.etapa == 'cidade':
            return self.processar_cidade(mensagem)
        elif self.etapa == 'estado':
            return self.processar_estado(mensagem)
        elif self.etapa == 'curso':
            return self.processar_curso(mensagem)
        elif self.etapa == 'senha':
            return self.processar_senha(mensagem)
        elif self.etapa == 'confirmar_senha':
            return self.processar_confirmar_senha(mensagem)
        elif self.etapa == 'consentimento':
            return self.processar_consentimento(mensagem)
        elif self.etapa == 'comunicacao':
            return self.processar_comunicacao(mensagem)
        
        return "⚠️ Erro interno. Digite 'cancelar' para sair."
    
    def voltar_etapa(self):
        mapa_voltar = {
            'email': 'nome', 'cpf': 'email', 'telefone': 'cpf', 'nascimento': 'telefone',
            'endereco': 'nascimento', 'cidade': 'endereco', 'estado': 'cidade',
            'curso': 'estado', 'senha': 'curso', 'confirmar_senha': 'senha',
            'consentimento': 'confirmar_senha', 'comunicacao': 'consentimento'
        }
        if self.etapa in mapa_voltar:
            self.etapa = mapa_voltar[self.etapa]
            self._salvar()
            return f"✅ Voltando.\n\n{self.get_mensagem_etapa(self.etapa)}"
        return "Não é possível voltar."
    
    def processar_nome(self, mensagem):
        if len(mensagem.split()) >= 2:
            self.dados['nome'] = mensagem
            self.etapa = 'email'
            self._salvar()
            return self.get_mensagem_etapa('email')
        return "❌ Digite seu nome completo:"
    
    def processar_email(self, mensagem):
        if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', mensagem.lower()):
            self.dados['email'] = mensagem.lower()
            self.etapa = 'cpf'
            self._salvar()
            return self.get_mensagem_etapa('cpf')
        return "❌ Email inválido! Digite um email válido:"
    
    def processar_cpf(self, mensagem):
        cpf = re.sub(r'[^0-9]', '', mensagem)
        if len(cpf) == 11:
            self.dados['cpf'] = cpf
            self.etapa = 'telefone'
            self._salvar()
            return self.get_mensagem_etapa('telefone')
        return "❌ CPF inválido! Digite os 11 números:"
    
    def processar_telefone(self, mensagem):
        telefone = re.sub(r'[^0-9]', '', mensagem)
        if len(telefone) >= 10:
            self.dados['telefone'] = telefone
            self.etapa = 'nascimento'
            self._salvar()
            return self.get_mensagem_etapa('nascimento')
        return "❌ Telefone inválido! Digite com DDD:"
    
    def processar_nascimento(self, mensagem):
        data = self.parse_data(mensagem)
        if data:
            self.dados['data_nascimento'] = data
            self.etapa = 'endereco'
            self._salvar()
            return self.get_mensagem_etapa('endereco')
        return "❌ Data inválida! Use dia/mês/ano:"
    
    def processar_endereco(self, mensagem):
        if len(mensagem) >= 5:
            self.dados['endereco'] = mensagem
            self.etapa = 'cidade'
            self._salvar()
            return self.get_mensagem_etapa('cidade')
        return "❌ Endereço muito curto:"
    
    def processar_cidade(self, mensagem):
        if len(mensagem.strip()) >= 3:
            self.dados['cidade'] = mensagem.strip()
            self.etapa = 'estado'
            self._salvar()
            return self.get_mensagem_etapa('estado')
        return "❌ Cidade inválida:"
    
    def processar_estado(self, mensagem):
        estado = mensagem.upper().strip()
        if estado in self.ESTADOS_VALIDOS:
            self.dados['estado'] = estado
            self.etapa = 'curso'
            self._salvar()
            return self.get_mensagem_etapa('curso')
        return f"❌ Estado inválido! Use sigla (SP, RJ, MG...)"
    
    def processar_curso(self, mensagem):
        curso = mensagem.lower().strip()
        for c in self.CURSOS_VALIDOS:
            if c in curso:
                self.dados['curso_interesse'] = c
                self.etapa = 'senha'
                self._salvar()
                return self.get_mensagem_etapa('senha')
        return f"❌ Curso não encontrado. Cursos: Administração, Engenharia, Direito, Medicina..."
    
    def processar_senha(self, mensagem):
        if len(mensagem) >= 6:
            self.dados['senha'] = mensagem
            self.etapa = 'confirmar_senha'
            self._salvar()
            return self.get_mensagem_etapa('confirmar_senha')
        return "❌ Senha deve ter no mínimo 6 caracteres:"
    
    def processar_confirmar_senha(self, mensagem):
        if mensagem == self.dados.get('senha'):
            self.etapa = 'consentimento'
            self._salvar()
            return self.get_mensagem_etapa('consentimento')
        return "❌ Senhas não conferem! Digite novamente:"
    
    def processar_consentimento(self, mensagem):
        if 'sim' in mensagem.lower():
            self.dados['consentimento_dados'] = 1
            self.etapa = 'comunicacao'
            self._salvar()
            return self.get_mensagem_etapa('comunicacao')
        return "❌ Precisa aceitar para continuar. Digite SIM:"
    
    def processar_comunicacao(self, mensagem):
        self.dados['consentimento_comunicacao'] = 1 if 'sim' in mensagem.lower() else 0
        return self._finalizar_cadastro()
    
    def get_mensagem_etapa(self, etapa):
        mensagens = {
            'inicio_cadastro': "📝 Vamos cadastrar! Qual seu nome completo?",
            'nome': "Ótimo! Qual seu email?",
            'email': "Agora seu CPF (apenas números):",
            'cpf': "Seu telefone com DDD:",
            'telefone': "Sua data de nascimento (dd/mm/aaaa):",
            'nascimento': "Seu endereço completo:",
            'endereco': "Sua cidade:",
            'cidade': "Seu estado (sigla - SP, RJ, MG):",
            'estado': "Qual curso você quer?",
            'curso': "Crie uma senha (mínimo 6 caracteres):",
            'confirmar_senha': "Confirme sua senha:",
            'consentimento': "Aceita armazenar seus dados? (sim/não):",
            'comunicacao': "Aceita receber comunicados? (sim/não):"
        }
        return mensagens.get(etapa, "Continuando...")
    
    def parse_data(self, texto):
        numeros = re.sub(r'[^0-9]', '', texto)
        if len(numeros) == 8:
            try:
                dia = int(numeros[:2])
                mes = int(numeros[2:4])
                ano = int(numeros[4:])
                return datetime(ano, mes, dia).strftime('%Y-%m-%d')
            except:
                pass
        return None
    
    def _finalizar_cadastro(self):
        try:
            senha_hash = hashlib.sha256(self.dados['senha'].encode()).hexdigest()
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alunos (nome, email, cpf, telefone, whatsapp, data_nascimento,
                    endereco, cidade, estado, curso_interesse, senha,
                    consentimento_dados, consentimento_comunicacao, data_consentimento, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.dados['nome'], self.dados.get('email', ''), self.dados.get('cpf', ''),
                self.dados.get('telefone', ''), self.numero, self.dados.get('data_nascimento', ''),
                self.dados.get('endereco', ''), self.dados.get('cidade', ''), self.dados.get('estado', ''),
                self.dados.get('curso_interesse', ''), senha_hash,
                self.dados.get('consentimento_dados', 0), self.dados.get('consentimento_comunicacao', 0),
                datetime.now().isoformat(), 'Pré-cadastro'
            ))
            aluno_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self.etapa = 'concluido'
            self._salvar()
            
            return f"""✅ CADASTRO REALIZADO!

Olá {self.dados['nome']}, cadastro concluído!
Protocolo: #{aluno_id:06d}

A secretaria entrará em contato em breve!"""
        except Exception as e:
            return f"❌ Erro: {e}"
    
    def formatar_telefone(self, numero):
        if len(numero) == 13:
            return f"+{numero[:2]} ({numero[2:4]}) {numero[4:9]}-{numero[9:]}"
        elif len(numero) == 11:
            return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}"
        return numero


# ==================== FUNÇÕES DE IDENTIFICAÇÃO ====================

def identificar_usuario_por_whatsapp(numero):
    conn = get_db()
    cursor = conn.cursor()
    numero_limpo = limpar_numero_whatsapp(numero)
    
    cursor.execute("SELECT id, nome, email, 'aluno' as tipo FROM alunos WHERE whatsapp = ? LIMIT 1", (numero_limpo,))
    aluno = cursor.fetchone()
    if aluno:
        conn.close()
        return dict(aluno)
    
    cursor.execute("SELECT id, nome, email, 'aluno' as tipo FROM alunos WHERE replace(telefone, '-', '') = ? LIMIT 1", (numero_limpo,))
    aluno = cursor.fetchone()
    if aluno:
        conn.close()
        return dict(aluno)
    
    conn.close()
    return None


def processar_mensagem_aluno_whatsapp(mensagem, usuario):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alunos WHERE id = ?", (usuario['id'],))
    aluno = cursor.fetchone()
    conn.close()
    
    if not aluno:
        return "❌ Não encontrei seus dados."
    
    dados_aluno = dict(aluno)
    msg_lower = mensagem.lower()
    
    if any(p in msg_lower for p in ['trancar', 'trancamento']):
        return f"🏛️ **TRANCAMENTO**\n\nOlá {dados_aluno['nome']}. Responda CONFIRMAR para abrir solicitação."
    
    if 'confirmar trancamento' in msg_lower:
        return f"✅ Solicitação registrada! Protocolo: TR-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    if any(p in msg_lower for p in ['boleto', '2 via']):
        return f"💳 **2ª VIA DE BOLETO**\n\nSolicitação recebida. Prazo: 1 dia útil."
    
    if any(p in msg_lower for p in ['declaração', 'declaracao']):
        return f"📄 **DECLARAÇÃO**\n\nSolicitação recebida. Envio em até 1 dia útil."
    
    if any(p in msg_lower for p in ['meus dados']):
        return f"""📋 SEUS DADOS
Nome: {dados_aluno['nome']}
Email: {dados_aluno['email']}
Telefone: {dados_aluno.get('telefone', 'N/A')}
Curso: {dados_aluno.get('curso_interesse', 'N/A')}
Status: {dados_aluno.get('status', 'Ativo')}"""
    
    if any(p in msg_lower for p in ['ajuda', 'menu']):
        return """📱 MENU DO ALUNO
• meus dados
• declaração
• boleto
• trancamento"""
    
    return perguntar_gemini(mensagem, dados_aluno, 'aluno', {'aluno_nome': dados_aluno['nome']})


def processar_mensagem_publico_whatsapp(mensagem, numero):
    print(f"\n   processar_mensagem_publico_whatsapp: {mensagem[:50]}")
    
    try:
        msg_key = f"{numero}:{mensagem}"
        if msg_key in ultimas_mensagens and time.time() - ultimas_mensagens[msg_key] < 5:
            return None
        ultimas_mensagens[msg_key] = time.time()
        
        gerenciador = GerenciadorCadastroWhatsApp(numero)
        
        if gerenciador.etapa not in ['inicio', 'concluido']:
            return gerenciador.processar_etapa_atual(mensagem)
        
        if any(p in mensagem.lower() for p in ['cadastrar', 'matricular', 'quero me cadastrar']):
            gerenciador.etapa = 'nome'
            gerenciador.dados = {'whatsapp': numero}
            gerenciador._salvar()
            return gerenciador.get_mensagem_etapa('inicio_cadastro')
        
        return perguntar_gemini(mensagem, {}, 'publico', numero_whatsapp=numero)
        
    except Exception as e:
        print(f"Error: {e}")
        return "❌ Desculpe, ocorreu um erro. Tente novamente."


# ==================== RESPOSTAS FALLBACK ====================

def resposta_fallback(pergunta, tipo_usuario, dados_usuario=None):
    if tipo_usuario == 'secretaria':
        return """👋 Assistente da secretaria.

Comandos: "Quantos alunos?", "Lista de alunos" """
    
    elif tipo_usuario == 'aluno':
        return f"👋 Olá {dados_usuario.get('aluno_nome', 'Aluno')}! Digite 'meus dados' ou 'ajuda'."
    
    return responder_pergunta_fallback(pergunta)


def responder_pergunta_fallback(pergunta):
    pergunta_lower = pergunta.lower()
    
    if any(p in pergunta_lower for p in ['oi', 'olá', 'bom dia']):
        return "👋 Olá! Como posso ajudar?"
    
    if any(p in pergunta_lower for p in ['curso', 'cursos']):
        return f"""📚 Cursos: Administração, Engenharia, Direito, Medicina, Psicologia, Arquitetura, Pedagogia"""
    
    if any(p in pergunta_lower for p in ['valor', 'mensalidade']):
        return f"""💰 Mensalidades: R$800 a R$2.500. Bolsas disponíveis!"""
    
    if any(p in pergunta_lower for p in ['documento']):
        return f"""📋 Documentos: RG, CPF, comprovante de residência, histórico escolar"""
    
    if any(p in pergunta_lower for p in ['localização', 'endereço']):
        return f"""📍 Rua da Faculdade, 123 - Centro, Campinas/SP"""
    
    return """👋 Olá! Pergunte sobre cursos, valores, documentos ou digite "quero me cadastrar"."""


def get_dados_gerais():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM alunos")
    total = cursor.fetchone()['total']
    cursor.execute("SELECT id, nome, email FROM alunos ORDER BY data_cadastro DESC LIMIT 10")
    alunos = cursor.fetchall()
    conn.close()
    return {'total_alunos': total, 'alunos': [dict(a) for a in alunos]}


# ==================== ROTAS ====================

@app.route('/health')
def health():
    """Health check para o Render"""
    whatsapp_status = False
    try:
        response = requests.get(f"{WHATSAPP_API_URL}/health", timeout=2)
        if response.status_code == 200:
            whatsapp_status = True
    except:
        pass
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'python': 'running',
            'gemini': gemini_client is not None,
            'whatsapp': whatsapp_status
        }
    })

@app.route('/whatsapp-status')
def whatsapp_status():
    """Verificar status do WhatsApp"""
    try:
        response = requests.get(f"{WHATSAPP_API_URL}/api/server/status", timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'status': 'offline', 'error': str(e)})

@app.route('/api/whatsapp-webhook', methods=['POST'])
@app.route('/api/whatsapp-webhook', methods=['POST'])
def whatsapp_webhook():
    try:
        data = request.get_json(force=True)

        print("📩 Webhook recebido:")
        print(data)

        payload = data.get('payload', {})

        # ✅ Ignora mensagens do próprio bot
        if payload.get('fromMe'):
            return jsonify({"status": "ignorado"}), 200

        chat_id = payload.get('from')   # 👈 USO CORRETO
        mensagem = payload.get('body', '').strip()

        # ✅ Validação leve
        if not chat_id or not mensagem:
            return jsonify({"status": "sem dados"}), 200

        print(f"💬 Mensagem: {mensagem}")
        print(f"📱 Chat ID: {chat_id}")

        resposta = "👋 Olá! Recebi sua mensagem 😊"

        # ✅ ENVIO CORRETO
        enviar_whatsapp(chat_id, resposta)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"❌ Erro no webhook: {e}")
        return jsonify({"status": "erro"}), 200

@app.route('/api/whatsapp/status')
def api_whatsapp_status():
    try:
        response = requests.get(f"{WHATSAPP_API_URL}/status", timeout=3)
        return jsonify(response.json())
    except:
        return jsonify({'status': 'offline'})


# ==================== TEMPLATES HTML ====================
# (Mantenha todos os templates HTML aqui - HOME_TEMPLATE, LOGIN_TEMPLATE, etc.)
# Para economizar espaço, eles já estão no seu código original

# ==================== ROTAS WEB ====================

@app.route('/')
def index():
    return render_template_string(HOME_TEMPLATE)

@app.route('/chat_publico')
def chat_publico():
    if 'session_id' not in session:
        session['session_id'] = secrets.token_hex(16)
    return render_template_string(CHAT_PUBLICO_TEMPLATE)

@app.route('/api/chat_publico', methods=['POST'])
def api_chat_publico():
    data = request.json
    mensagem = data.get('mensagem', '')
    resposta = perguntar_gemini(mensagem, {}, 'publico')
    return jsonify({'resposta': resposta})

@app.route('/login')
def login_page():
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/login/aluno', methods=['POST'])
def login_aluno():
    email = request.form.get('email')
    senha = request.form.get('senha')
    senha_hash = hashlib.sha256(senha.encode()).hexdigest()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, email FROM alunos WHERE email = ? AND senha = ?", (email, senha_hash))
    aluno = cursor.fetchone()
    conn.close()
    
    if aluno:
        session['aluno_id'] = aluno['id']
        session['aluno_nome'] = aluno['nome']
        session['tipo'] = 'aluno'
        return redirect('/chat_aluno')
    return render_template_string(LOGIN_TEMPLATE, erro="Email ou senha inválidos")

@app.route('/login/secretaria', methods=['POST'])
def login_secretaria():
    email = request.form.get('email')
    senha = request.form.get('senha')
    senha_hash = hashlib.sha256(senha.encode()).hexdigest()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, email FROM usuarios WHERE email = ? AND senha = ?", (email, senha_hash))
    secretaria = cursor.fetchone()
    conn.close()
    
    if secretaria:
        session['secretaria_id'] = secretaria['id']
        session['secretaria_nome'] = secretaria['nome']
        session['tipo'] = 'secretaria'
        return redirect('/chat_secretaria')
    return render_template_string(LOGIN_TEMPLATE, erro="Email ou senha inválidos")

@app.route('/chat_aluno')
def chat_aluno():
    if 'aluno_id' not in session:
        return redirect('/login')
    return render_template_string(CHAT_ALUNO_TEMPLATE, aluno=session)

@app.route('/api/chat_aluno', methods=['POST'])
def api_chat_aluno():
    if 'aluno_id' not in session:
        return jsonify({'resposta': '❌ Faça login'})
    
    data = request.json
    pergunta = data.get('mensagem', '')
    resposta = processar_mensagem_aluno_whatsapp(pergunta, {'id': session['aluno_id'], 'nome': session['aluno_nome']})
    enviar_whatsapp(numero, resposta)

    return jsonify({
        'status': 'ok'
    })

@app.route('/chat_secretaria')
def chat_secretaria():
    if 'secretaria_id' not in session:
        return redirect('/login')
    return render_template_string(CHAT_SECRETARIA_TEMPLATE, secretaria=session)

@app.route('/api/chat_secretaria', methods=['POST'])
def api_chat_secretaria():
    if 'secretaria_id' not in session:
        return jsonify({'resposta': '❌ Faça login'})
    
    data = request.json
    pergunta = data.get('mensagem', '')
    dados = get_dados_gerais()
    resposta = perguntar_gemini(pergunta, dados, 'secretaria', session)
    return jsonify({'resposta': resposta})

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ==================== PROCESSADOR DE FILA ====================
def processar_fila_mensagens():
    while True:
        try:
            item = fila_mensagens.get(timeout=5)
            resultado = enviar_whatsapp(item['numero'], item['mensagem'])
            if not resultado['sucesso'] and item.get('tentativas', 0) < 3:
                item['tentativas'] = item.get('tentativas', 0) + 1
                fila_mensagens.put(item)
            fila_mensagens.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"Queue error: {e}")
            time.sleep(1)

thread_fila = threading.Thread(target=processar_fila_mensagens, daemon=True)
thread_fila.start()

# ==================== MAIN ====================
if __name__ == '__main__':
    print("\n" + "="*80)
    print(f" {INSTITUICAO_NOME} - ACADEMIC SYSTEM WITH GEMINI ".center(80, "="))
    print("="*80)
    
    # Inicializar banco de dados
    init_database()
    
    # Verificar configurações
    print("\n🔧 VERIFICANDO CONFIGURAÇÕES:")
    print(f"   • Arquivo .env: {'✅ Carregado' if os.getenv('GEMINI_API_KEY') else '⚠️ Não encontrado ou sem GEMINI_API_KEY'}")
    print(f"   • WhatsApp API: {WHATSAPP_API_URL}")
    print(f"   • Database: {DB_PATH}")
    
    # Inicializar Gemini
    print("\n🔧 Configurando Gemini API...")
    print(f"   • Modelo: {GEMINI_MODEL}")
    
    gemini_status = False
    if GEMINI_API_KEY:
        try:
            gemini_client = GeminiClient(api_key=GEMINI_API_KEY, model=GEMINI_MODEL)
            gemini_status = gemini_client.verificar_status()
            if gemini_status:
                print("✅ Gemini API configurada com sucesso!")
            else:
                print("❌ Erro ao conectar com Gemini API")
        except Exception as e:
            print(f"❌ Erro ao inicializar Gemini: {e}")
            gemini_status = False
    else:
        print("⚠️ Gemini não configurado (API key não encontrada no .env)")
    
    # Verificar WhatsApp
    print("\n🔧 Verificando WhatsApp API...")
    try:
        response = requests.get(f"{WHATSAPP_API_URL}/status", timeout=2)
        if response.status_code == 200 and response.json().get('status') == 'online':
            print("✅ WhatsApp: Online")
        else:
            print("⚠️ WhatsApp: Offline (client not ready)")
    except requests.exceptions.ConnectionError:
        print("⚠️ WhatsApp: Offline (serviço não está rodando)")
        print(f"   Execute: node whatsapp-api.js")
    except Exception as e:
        print(f"⚠️ WhatsApp: Erro ao verificar status - {e}")
    
    print("\n📋 TEST USERS:")
    print(f"   • Secretaria: secretaria@unin.edu / admin123")
    print(f"   • Aluno: joao.silva@email.com / joao123")
    print(f"   • Aluno: maria.souza@email.com / maria123")
    
    print("\n🔗 ACCESS URLS:")
    print(f"   • Home: http://localhost:5000/")
    print(f"   • Público: http://localhost:5000/chat_publico")
    print(f"   • Login: http://localhost:5000/login")
    print(f"   • WhatsApp Webhook: http://localhost:5000/api/whatsapp-webhook")
    print(f"   • Health Check: http://localhost:5000/health")
    
    if gemini_status:
        print(f"   • Gemini: Online ({GEMINI_MODEL})")
    else:
        print(f"   • Gemini: Offline (usando respostas de fallback)")
    
    print("\n🚀 Servidor iniciado!")
    print("="*80 + "\n")
    
    # Render usa a variável PORT
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)