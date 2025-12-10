import os
import psycopg2
from dotenv import load_dotenv
from app.config import logger

load_dotenv()

def get_db_connection(db_name: str):
    """Get a connection to Azure PostgreSQL database"""
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv(db_name),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        sslmode=os.getenv("DB_SSLMODE", "require")
    )

    conn.autocommit = True
    return conn