"""Konfigurasi aplikasi dari environment dan lokasi aset model."""
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SALT_ID = os.getenv("SALT_ID")
    KATA_SANDI_DOSEN = os.getenv("KATA_SANDI_DOSEN")
    KATA_SANDI_STAF = os.getenv("KATA_SANDI_STAF")
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    DATABASE_PATH = BASE_DIR / "instance" / "ews.sqlite3"
    KAMUS_KODE_PATH = BASE_DIR / "kamus_kode.json"
    MODEL_PATH = BASE_DIR / "model" / "ews_rf_v1.0.joblib"
    STATISTIK_PATH = BASE_DIR / "model" / "statistik_fitur_v1.0.json"
