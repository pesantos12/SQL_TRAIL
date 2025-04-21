from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import os
import json
import random
import datetime
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# CORS liberado para permitir conexão do front (GitHub Pages) durante desenvolvimento
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # em produção, substituir pelo domínio específico do front-end
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexão com banco de dados (Supabase/PostgreSQL) usando variáveis de ambiente
DB_CONFIG = {
    "host": os.getenv("host"),
    "port": os.getenv("port"),
    "database": os.getenv("dbname"),
    "user": os.getenv("user"),
    "password": os.getenv("password")
}
conn = psycopg2.connect(**DB_CONFIG)

# Carrega as questões do arquivo JSON na memória ao iniciar
try:
    with open('questoes.json', 'r', encoding='utf-8') as f:
        questoes = json.load(f)
        # Cria dicionário auxiliar para acesso rápido por ID
        questoes_por_id = {q["id"]: q for q in questoes}
except Exception as e:
    print(f"Erro ao carregar 'questoes.json': {e}")
    questoes = []
    questoes_por_id = {}

@app.get("/")
def home():
    return {"status": "API online"}

@app.get("/questao")
def obter_questao():
    """Retorna uma questão aleatória (id e enunciado) do conjunto de questões."""
    if not questoes:
        return {"erro": "Nenhuma questão disponível."}
    questao = random.choice(questoes)
    return {"id": questao["id"], "enunciado": questao["enunciado"]}

@app.post("/validar")
async def validar_query(request: Request):
    """Valida a consulta SQL enviada pelo usuário, comparando com a resposta esperada da questão."""
    data = await request.json()
    user_query = data.get("query")
    questao_id = data.get("questao_id")
    resultado = {
        "valido": True,
        "correto": False,
        "feedback": "",
        "resultado_aluno": None,
        "resultado_esperado": None
    }

    # Verifica se a questão existe
    questao = questoes_por_id.get(questao_id)
    if not questao:
        return {"valido": False, "erro": "Questão inválida ou não encontrada."}

    # Permite apenas comandos SELECT na query do aluno
    query_text = user_query.strip()
    # Verifica todas as instruções separadas por ';'
    for stmt in query_text.split(';'):
        if stmt.strip() == "":
            continue
        if not stmt.strip().lower().startswith("select"):
            return {"valido": False, "erro": "Apenas comandos SELECT são permitidos."}

    # Query SQL esperada (resposta base) da questão
    expected_query = questao["resposta_base"]

    try:
        cur = conn.cursor()
        # Executa a query do usuário
        cur.execute(user_query)
        user_rows = None
        user_columns = None
        if cur.description:
            user_rows = cur.fetchall()
            user_columns = [desc[0] for desc in cur.description]
        # Executa a query esperada
        cur.execute(expected_query)
        expected_rows = None
        expected_columns = None
        if cur.description:
            expected_rows = cur.fetchall()
            expected_columns = [desc[0] for desc in cur.description]
    except psycopg2.Error as e:
        # Em caso de erro na execução da query do usuário, reinicia conexão e retorna erro
        try:
            conn.close()
        except:
            pass
        conn = psycopg2.connect(**DB_CONFIG)
        return {"valido": False, "erro": str(e)}

    # Prepara estrutura de resultados para retornar (convertendo valores para tipos JSON serializáveis)
    def format_value(val):
        """Converte valores para formatos seguros para JSON."""
        if val is None:
            return None
        if isinstance(val, (datetime.date, datetime.datetime)):
            return val.isoformat()
        if isinstance(val, Decimal):
            return str(val)
        return val

    # Monta resultado esperado (sempre disponível se query base executou corretamente)
    resultado_esperado = {
        "colunas": expected_columns if expected_columns else [],
        "linhas": []
    }
    if expected_rows is not None:
        for row in expected_rows:
            resultado_esperado["linhas"].append([format_value(v) for v in row])

    # Monta resultado do aluno (pode ser None se a query não retornou resultado algum, p.ex. comando DML)
    resultado_aluno = {
        "colunas": user_columns if user_columns else [],
        "linhas": []
    }
    if user_rows is not None:
        for row in user_rows:
            resultado_aluno["linhas"].append([format_value(v) for v in row])

    # Incorpora resultados no retorno
    resultado["resultado_esperado"] = resultado_esperado
    resultado["resultado_aluno"] = resultado_aluno

    # Se a query esperada não retornou colunas (caso inesperado), encerra marcando erro
    if expected_columns is None:
        resultado["valido"] = False
        resultado["erro"] = "Falha ao obter resultado esperado."
        return resultado

    # Compara resultados para determinar se a resposta está correta
    # 1. Compara número de linhas
    user_count = len(user_rows) if user_rows is not None else 0
    expected_count = len(expected_rows) if expected_rows is not None else 0
    if user_count != expected_count:
        resultado["feedback"] = f"Linhas incorretas. Esperado: {expected_count}"
        resultado["correto"] = False
        return resultado

    # 2. Compara colunas (quantidade e nomes)
    user_cols_list = user_columns if user_columns is not None else []
    expected_cols_list = expected_columns if expected_columns is not None else []
    if len(user_cols_list) != len(expected_cols_list) or user_cols_list != expected_cols_list:
        resultado["feedback"] = f"Colunas incorretas. Esperado: {', '.join(expected_cols_list)}"
        resultado["correto"] = False
        return resultado

    # 3. Compara conteúdo das tabelas (considerando ordem conforme necessidade)
    # Se a query esperada tiver ORDER BY, exige mesma ordem; caso contrário, compara como conjunto (ignora ordem)
    user_data = user_rows if user_rows is not None else []
    expected_data = expected_rows if expected_rows is not None else []
    if "order by" in expected_query.lower():
        # Compara sequencialmente quando ordem é relevante
        if user_data != expected_data:
            resultado["feedback"] = "Dados incorretos."
            resultado["correto"] = False
            return resultado
    else:
        # Compara ignorando ordem (como conjuntos multiconjunto)
        from collections import Counter
        if Counter([tuple(row) for row in user_data]) != Counter([tuple(row) for row in expected_data]):
            resultado["feedback"] = "Dados incorretos."
            resultado["correto"] = False
            return resultado

    # Se chegou aqui, a resposta é considerada correta
    resultado["correto"] = True
    resultado["feedback"] = "Resposta Correta!"
    return resultado
