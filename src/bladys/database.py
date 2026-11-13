from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from bladys.config import read_secret

DATABASE_URL = read_secret("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está configurada")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_session():
    with SessionLocal() as session:
        yield session
