# database.py

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
import streamlit as st 
from typing import Generator

# --- 1. SECURE CONFIGURATION ---
# Reading secrets, assuming they are stored under a key like 'aurora_db'
###
DB_USER = st.secrets.get("aurora_db")["DB_USER"]
DB_PASS = st.secrets.get("aurora_db")["DB_PASS"]
DB_HOST = st.secrets.get("aurora_db")["DB_HOST"]
DB_PORT = st.secrets.get("aurora_db")["DB_PORT"]
DB_NAME = st.secrets.get("aurora_db")["DB_NAME"]


# --- 2. DATABASE URL ---
if DB_HOST and DB_USER and DB_PASS and DB_NAME:
    # Standard MySQL/PyMySQL connection string for AWS RDS/Aurora
    SQLALCHEMY_DATABASE_URL = (
        f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
else:
    # Fallback for local development/testing only (Note: requires manual SQLite setup)
    SQLALCHEMY_DATABASE_URL = "sqlite:///./sales_data_dev.db" 


# --- 3. CREATE ENGINE ---
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    echo=True
)

# --- 4. SESSION AND BASE ---

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get the database session
def get_db() -> Generator:
    """Provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
