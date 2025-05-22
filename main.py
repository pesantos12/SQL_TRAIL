# main.py
from flask import Flask, render_template, request, jsonify
import psycopg2
from dotenv import load_dotenv
import os
import requests
import json
import random

# --- Carrega variáveis de ambiente ---
load_dotenv()
DB_PARAMS = {
    'user': os.getenv('user'),
    'password': os.getenv('password'),
    'host': os.getenv('host'),
    'port': os.getenv('port'),
    'dbname': os.getenv('dbname')
}
GROQ_API_KEY = os.getenv('API_KEY')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama3-70b-8192')
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'

# --- Carrega questões ---
with open('questoes.json', encoding='utf-8') as f:
    lista = json.load(f)
QUESTOES = {q['id']: q for q in lista}
CURRENT_QUESTION_ID = None

# --- Inicializa Flask ---
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# --- Funções utilitárias ---
def is_select(query: str) -> bool:
    """Verifica se o comando inicia com SELECT"""
    return query.strip().lower().startswith('select')


def executador(query: str, fetch: bool = False, params: tuple = None):
    """
    Executa a query no banco:
      - Se fetch=False: retorna True/False (sucesso ou erro).
      - Se fetch=True: retorna lista de tuplas ou False em caso de erro.
    """
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(query, params or ())
        if fetch:
            return cur.fetchall()
        return True
    except Exception:
        if conn:
            conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_row_count(query: str) -> int:
    """
    Conta o número de linhas retornadas por uma consulta ou retorna -1 em erro.
    """
    rows = executador(query, fetch=True)
    return len(rows) if isinstance(rows, list) else -1


def fetch_query_results(sql: str, max_rows: int = 20):
    """
    Executa a SQL e retorna um dict com:
      - columns: lista de nomes de coluna
      - rows: até max_rows tuplas
      - total_rows: total de linhas
    Retorna None em caso de erro.
    """
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        cur.execute(sql)
        all_rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        total = len(all_rows)
        display = all_rows[:max_rows]
        cur.close()
        conn.close()
        return {'columns': colnames, 'rows': display, 'total_rows': total}
    except Exception:
        return None


def groq_validate(enunciado: str, base_sql: str, student_sql: str) -> bool:
    """
    Chama a API do Groq para validar equivalência semântica.
    Retorna True se equivalentes, False caso contrário.
    """
    headers = {
        'Authorization': f'Bearer {GROQ_API_KEY}',
        'Content-Type': 'application/json'
    }

    # Prompt de sistema enfatiza alias, concat e renomeações, e pede só True/False
    prompt_system = """
    Você é um avaliador de SQL de nível especialista, com profundo entendimento de semântica e requisitos de negócio. 
    Sua tarefa é verificar se a consulta do aluno atende aos requisitos do enunciado — ou seja, se os dados retornados permitem deduzir corretamente as informações solicitadas, mesmo que:
    - o aluno use aliases diferentes;
    - retorne colunas separadas em vez de concatenadas (por exemplo, first_name e last_name em vez de uma coluna “nome completo”);
    - renomeie campos ou altere a ordem das colunas;
    - utilize operadores equivalentes ou formatações diversas.

    Por exemplo, se o enunciado pede “nome completo”, aceite tanto uma única coluna concatenada quanto duas colunas separadas que, juntas, permitam compor o nome completo.  

    Responda estritamente com **True** se a consulta do aluno satisfaz os requisitos do enunciado ou **False** caso contrário, sem qualquer outra palavra, pontuação ou explicação.```

    """.strip()

    # Prompt do usuário com base e aluno
    prompt_user = f"""
    Enunciado: {enunciado}
    Consulta base: {base_sql}
    Consulta do aluno: {student_sql}
    Ambas as consultas acima retornam resultados que satisfazem corretamente o enunciado?
    Responda apenas True ou False.
    """.strip()

    payload = {
        'model': GROQ_MODEL,
        'messages': [
            {'role': 'system', 'content': prompt_system},
            {'role': 'user',   'content': prompt_user}
        ],
        'temperature': 0.0,
        'max_tokens': 4
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        text = resp.json()['choices'][0]['message']['content'].strip().lower()
        return text.startswith('true')
    except Exception:
        return False


# --- Rotas Flask ---
@app.route('/question')
def question():
    global CURRENT_QUESTION_ID
    q = random.choice(list(QUESTOES.values()))
    CURRENT_QUESTION_ID = q['id']
    return jsonify({'id': q['id'], 'enunciado': q['enunciado']})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/validate', methods=['POST'])
def validate():
    data = request.get_json() or {}

    # Verifica se questão foi carregada
    if CURRENT_QUESTION_ID is None or CURRENT_QUESTION_ID not in QUESTOES:
        return jsonify({'valid': False, 'error': 'Nenhuma questão carregada.'}), 400

    q = QUESTOES[CURRENT_QUESTION_ID]
    base_sql = q['resposta_base']
    enunciado = q['enunciado']
    student_sql = data.get('student_sql', '').strip()

    if not student_sql:
        return jsonify({'valid': False, 'error': 'Falta consulta do aluno.', 'enunciado': enunciado}), 400

    # 1) Valida SELECT
    if not is_select(student_sql):
        return jsonify({'valid': False, 'error': 'Consulta não inicia com SELECT.', 'enunciado': enunciado}), 200

    # 2) Validação de sintaxe/semântica
    if not executador(student_sql):
        return jsonify({'valid': False, 'error': 'Erro na execução da consulta (sintaxe ou semântica).', 'enunciado': enunciado}), 200

    # 3) Valida número de linhas
    cnt_base = get_row_count(base_sql)
    cnt_student = get_row_count(student_sql)
    if cnt_base < 0 or cnt_student < 0:
        return jsonify({'valid': False, 'error': 'Erro ao contar linhas.', 'enunciado': enunciado}), 200
    if cnt_base != cnt_student:
        return jsonify({'valid': False, 'error': f'Número de linhas diferente ({cnt_base} vs {cnt_student}).', 'enunciado': enunciado}), 200

    # 4) Validação semântica com Groq
    equivalent = groq_validate(enunciado, base_sql, student_sql)
    payload = {'valid': equivalent, 'enunciado': enunciado}
    if equivalent:
        payload['message'] = 'OK'
    else:
        payload['error'] = 'Consultas não são semanticamente equivalentes.'

    # Adiciona tabelas para visualização
    stu_data = fetch_query_results(student_sql)
    if stu_data:
        payload['result_table'] = stu_data
    exp_data = fetch_query_results(base_sql)
    if exp_data:
        payload['expected_table'] = exp_data

    return jsonify(payload)

if __name__ == '__main__':
    app.run(debug=True)
