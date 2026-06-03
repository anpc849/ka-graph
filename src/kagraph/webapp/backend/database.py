import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from kagraph._studio_config import DEFAULT_DB_PATH, DEFAULT_DB_URL

DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("KATRACE_DB_URL", DEFAULT_DB_URL)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
if DATABASE_URL.startswith("sqlite"):
    print(f"KaTrace SQLite database: {DATABASE_URL}", flush=True)
else:
    print("KaTrace database configured from KATRACE_DB_URL.", flush=True)
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
