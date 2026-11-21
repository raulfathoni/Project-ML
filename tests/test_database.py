import hashlib
import json
import sqlite3

import pytest

from database import (
    daftar_riwayat,
    hitung_riwayat,
    init_db,
    pseudonim,
    simpan_hasil,
)


def _simpan(db_path, waktu, kategori, sumber):
    return simpan_hasil(
        waktu=waktu,
        id_pseudonim="a1b2c3d4e5f6",
        versi_model="v1.0",
        nilai_prediksi=12.5,
        kategori=kategori,
        faktor_utama=[{"fitur": "Nilai semester 1", "dampak": 1.2}],
        sumber=sumber,
        id_batch=None,
        peran="dosen_wali",
        db_path=db_path,
    )


def test_init_db_membuat_tabel_riwayat(tmp_path):
    db_path = tmp_path / "riwayat.sqlite3"

    init_db(db_path)

    with sqlite3.connect(db_path) as koneksi:
        kolom = koneksi.execute("PRAGMA table_info(riwayat_prediksi)").fetchall()
    assert [item[1] for item in kolom] == [
        "id",
        "waktu",
        "id_pseudonim",
        "versi_model",
        "nilai_prediksi",
        "kategori",
        "faktor_utama",
        "rekomendasi",
        "sumber",
        "id_batch",
        "peran",
    ]


def test_init_db_menambahkan_rekomendasi_ke_riwayat_lama(tmp_path):
    db_path = tmp_path / "riwayat-lama.sqlite3"
    with sqlite3.connect(db_path) as koneksi:
        koneksi.execute(
            """
            CREATE TABLE riwayat_prediksi (
                id INTEGER PRIMARY KEY,
                waktu TEXT NOT NULL,
                id_pseudonim TEXT NOT NULL,
                versi_model TEXT NOT NULL,
                nilai_prediksi REAL NOT NULL,
                kategori TEXT NOT NULL,
                faktor_utama TEXT NOT NULL,
                sumber TEXT NOT NULL,
                id_batch TEXT,
                peran TEXT NOT NULL
            )
            """
        )
        koneksi.execute(
            """
            INSERT INTO riwayat_prediksi (
                waktu, id_pseudonim, versi_model, nilai_prediksi,
                kategori, faktor_utama, sumber, id_batch, peran
            ) VALUES ('2026-11-16T10:00:00', 'pseudo-1', 'v1.0', 12.5,
                      'Normal', '[]', 'formulir', NULL, 'dosen_wali')
            """
        )
        koneksi.execute(
            """
            CREATE TABLE mahasiswa (
                id INTEGER PRIMARY KEY,
                id_mahasiswa TEXT NOT NULL UNIQUE,
                id_pseudonim TEXT NOT NULL UNIQUE,
                nama TEXT NOT NULL DEFAULT '',
                diperbarui_pada TEXT NOT NULL
            )
            """
        )
        koneksi.execute(
            """
            INSERT INTO mahasiswa (
                id_mahasiswa, id_pseudonim, nama, diperbarui_pada
            ) VALUES ('MHS-1', 'pseudo-1', 'Mahasiswa lama', '2026-11-16T10:00:00')
            """
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as koneksi:
        baris = koneksi.execute(
            "SELECT id, rekomendasi FROM riwayat_prediksi"
        ).fetchone()
        fitur_mahasiswa = koneksi.execute(
            "SELECT nama, fitur_input FROM mahasiswa"
        ).fetchone()
    assert baris == (1, "[]")
    assert fitur_mahasiswa == ("Mahasiswa lama", "{}")


def test_simpan_daftar_filter_dan_hitung_riwayat(tmp_path):
    db_path = tmp_path / "riwayat.sqlite3"
    init_db(db_path)
    id_lama = _simpan(db_path, "2026-11-16T10:00:00", "Normal", "formulir")
    id_baru = _simpan(db_path, "2026-11-16T11:00:00", "Berisiko Tinggi", "csv")

    hasil = daftar_riwayat(10, 0, db_path=db_path)

    assert [item["id"] for item in hasil] == [id_baru, id_lama]
    assert json.loads(hasil[0]["faktor_utama"]) == [
        {"fitur": "Nilai semester 1", "dampak": 1.2}
    ]
    assert len(daftar_riwayat(10, 0, "Normal", "formulir", db_path)) == 1
    assert hitung_riwayat("Berisiko Tinggi", "csv", db_path) == 1
    assert hitung_riwayat("' OR 1=1 --", db_path=db_path) == 0


def test_pseudonim_menghasilkan_sha256_bersalt_12_karakter():
    id_teks = "MHS-123"
    salt = "salt-uji"

    hasil = pseudonim(id_teks, salt)

    assert hasil == hashlib.sha256((salt + id_teks).encode("utf-8")).hexdigest()[:12]
    assert len(hasil) == 12
    assert pseudonim(id_teks, "salt-lain") != hasil


def test_pseudonim_mewajibkan_salt():
    with pytest.raises(ValueError):
        pseudonim("MHS-123", "")


def test_paginasi_menolak_nilai_negatif(tmp_path):
    db_path = tmp_path / "riwayat.sqlite3"
    init_db(db_path)

    with pytest.raises(ValueError):
        daftar_riwayat(-1, 0, db_path=db_path)
