from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# CORS liberado para testes
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # depois troque para o domínio do front
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexão Supabase (PostgreSQL)
conn = psycopg2.connect(
    host=os.getenv("host"),
    port=os.getenv("port"),
    database=os.getenv("dbname"),
    user=os.getenv("user"),
    password=os.getenv("password")
    
)

@app.get("/")
def home():
    return {"status": "API online"}

@app.post("/validar")
async def validar_query(request: Request):
    data = await request.json()
    query = data.get("query")

    try:
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        return {"valido": True, "linhas": len(rows)}
    except Exception as e:
        return {"valido": False, "erro": str(e)}
