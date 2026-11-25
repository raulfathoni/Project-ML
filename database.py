"""Penyimpanan riwayat prediksi menggunakan sqlite3."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


DATABASE_PATH = Path(__file__).resolve().parent / "instance" / "ews.sqlite3"


def _siapkan_jalur(db_path):
    """Pastikan folder basis data tersedia dan kembalikan jalurnya."""
    jalur = Path(db_path or DATABASE_PATH)
    jalur.parent.mkdir(parents=True, exist_ok=True)
    return jalur


def _buka_koneksi(db_path):
    """Buka koneksi SQLite dengan hasil berbentuk baris bernama."""
    koneksi = sqlite3.connect(_siapkan_jalur(db_path))
    koneksi.row_factory = sqlite3.Row
    return koneksi


def init_db(db_path=None):
    """Buat tabel riwayat, profil mahasiswa, dan tindak lanjut."""
    with closing(_buka_koneksi(db_path)) as koneksi:
        with koneksi:
            koneksi.execute(
                """
                CREATE TABLE IF NOT EXISTS riwayat_prediksi (
                    id INTEGER PRIMARY KEY,
                    waktu TEXT NOT NULL,
                    id_pseudonim TEXT NOT NULL,
                    versi_model TEXT NOT NULL,
                    nilai_prediksi REAL NOT NULL,
                    kategori TEXT NOT NULL,
                    faktor_utama TEXT NOT NULL,
                    rekomendasi TEXT NOT NULL DEFAULT '[]',
                    sumber TEXT NOT NULL CHECK (sumber IN ('formulir', 'csv')),
                    id_batch TEXT,
                    peran TEXT NOT NULL
                )
                """
            )
            kolom_riwayat = {
                baris["name"]
                for baris in koneksi.execute(
                    "PRAGMA table_info(riwayat_prediksi)"
                )
            }
            if "rekomendasi" not in kolom_riwayat:
                koneksi.execute(
                    "ALTER TABLE riwayat_prediksi "
                    "ADD COLUMN rekomendasi TEXT NOT NULL DEFAULT '[]'"
                )
            koneksi.execute(
                """
                CREATE TABLE IF NOT EXISTS mahasiswa (
                    id INTEGER PRIMARY KEY,
                    id_mahasiswa TEXT NOT NULL UNIQUE,
                    id_pseudonim TEXT NOT NULL UNIQUE,
                    nama TEXT NOT NULL DEFAULT '',
                    diperbarui_pada TEXT NOT NULL,
                    fitur_input TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            kolom_mahasiswa = {
                baris["name"]
                for baris in koneksi.execute("PRAGMA table_info(mahasiswa)")
            }
            if "fitur_input" not in kolom_mahasiswa:
                koneksi.execute(
                    "ALTER TABLE mahasiswa "
                    "ADD COLUMN fitur_input TEXT NOT NULL DEFAULT '{}'"
                )
            koneksi.execute(
                """
                CREATE TABLE IF NOT EXISTS tindak_lanjut (
                    mahasiswa_id INTEGER PRIMARY KEY
                        REFERENCES mahasiswa(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'Belum dimulai',
                    catatan TEXT NOT NULL DEFAULT '',
                    diperbarui_pada TEXT NOT NULL,
                    diperbarui_oleh TEXT NOT NULL
                )
                """
            )


def pseudonim(id_teks, salt):
    """Hash ID dengan salt di depan, lalu potong hasil SHA-256 menjadi 12 karakter."""
    if not isinstance(id_teks, str):
        raise TypeError("id_teks harus berupa teks.")
    if not isinstance(salt, str) or not salt:
        raise ValueError("Salt pseudonim harus berupa teks yang tidak kosong.")
    return hashlib.sha256((salt + id_teks).encode("utf-8")).hexdigest()[:12]


def simpan_hasil(
    waktu,
    id_pseudonim,
    versi_model,
    nilai_prediksi,
    kategori,
    faktor_utama,
    sumber,
    id_batch,
    peran,
    db_path=None,
    rekomendasi=None,
    id_mahasiswa=None,
    nama_mahasiswa=None,
    fitur_input=None,
):
    """Simpan prediksi dan, bila tersedia, perbarui profil mahasiswa."""
    faktor_teks = (
        faktor_utama
        if isinstance(faktor_utama, str)
        else json.dumps(faktor_utama, ensure_ascii=False)
    )
    rekomendasi_teks = json.dumps(rekomendasi or [], ensure_ascii=False)
    fitur_input_teks = json.dumps(fitur_input or {}, ensure_ascii=False)
    with closing(_buka_koneksi(db_path)) as koneksi:
        with koneksi:
            if id_mahasiswa:
                koneksi.execute(
                    """
                    INSERT INTO mahasiswa (
                        id_mahasiswa, id_pseudonim, nama, diperbarui_pada,
                        fitur_input
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id_mahasiswa) DO UPDATE SET
                        id_pseudonim = excluded.id_pseudonim,
                        nama = CASE
                            WHEN excluded.nama != '' THEN excluded.nama
                            ELSE mahasiswa.nama
                        END,
                        diperbarui_pada = excluded.diperbarui_pada,
                        fitur_input = CASE
                            WHEN excluded.fitur_input != '{}'
                                THEN excluded.fitur_input
                            ELSE mahasiswa.fitur_input
                        END
                    """,
                    (
                        id_mahasiswa,
                        id_pseudonim,
                        nama_mahasiswa or "",
                        waktu,
                        fitur_input_teks,
                    ),
                )
            kursor = koneksi.execute(
                """
                INSERT INTO riwayat_prediksi (
                    waktu, id_pseudonim, versi_model, nilai_prediksi,
                    kategori, faktor_utama, rekomendasi, sumber, id_batch, peran
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    waktu,
                    id_pseudonim,
                    versi_model,
                    nilai_prediksi,
                    kategori,
                    faktor_teks,
                    rekomendasi_teks,
                    sumber,
                    id_batch,
                    peran,
                ),
            )
            return kursor.lastrowid


def ringkasan_mahasiswa(db_path=None):
    """Hitung kondisi berdasarkan prediksi terakhir untuk tiap profil."""
    with closing(_buka_koneksi(db_path)) as koneksi:
        baris = koneksi.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN terbaru.kategori = 'Berisiko Tinggi'
                       THEN 1 ELSE 0 END) AS berisiko_tinggi,
                   SUM(CASE WHEN terbaru.kategori = 'Normal'
                       THEN 1 ELSE 0 END) AS normal
            FROM mahasiswa m
            JOIN riwayat_prediksi terbaru
              ON terbaru.id = (
                  SELECT p.id
                  FROM riwayat_prediksi p
                  WHERE p.id_pseudonim = m.id_pseudonim
                  ORDER BY p.id DESC
                  LIMIT 1
              )
            """
        ).fetchone()
    return {
        "total": baris["total"] or 0,
        "berisiko_tinggi": baris["berisiko_tinggi"] or 0,
        "normal": baris["normal"] or 0,
    }


def daftar_mahasiswa(
    batas,
    offset,
    filter_kategori=None,
    pencarian=None,
    filter_tindak_lanjut=None,
    db_path=None,
):
    """Ambil profil dengan prediksi terakhir untuk daftar pemantauan."""
    _validasi_paginasi(batas, offset)
    pola = f"%{pencarian.strip()}%" if pencarian and pencarian.strip() else None
    with closing(_buka_koneksi(db_path)) as koneksi:
        baris = koneksi.execute(
            """
            SELECT m.id, m.id_mahasiswa, m.nama, m.id_pseudonim,
                   p.waktu, p.versi_model, p.nilai_prediksi, p.kategori, p.faktor_utama,
                   p.rekomendasi,
                   COALESCE(t.status, 'Belum dimulai') AS status_tindak_lanjut
            FROM mahasiswa m
            JOIN riwayat_prediksi p
              ON p.id = (
                  SELECT terbaru.id
                  FROM riwayat_prediksi terbaru
                  WHERE terbaru.id_pseudonim = m.id_pseudonim
                  ORDER BY terbaru.id DESC
                  LIMIT 1
              )
            LEFT JOIN tindak_lanjut t ON t.mahasiswa_id = m.id
            WHERE (? IS NULL OR p.kategori = ?)
              AND (? IS NULL OR m.nama LIKE ? OR m.id_mahasiswa LIKE ?)
              AND (
                  ? IS NULL
                  OR (? = 'aktif' AND COALESCE(t.status, 'Belum dimulai') != 'Selesai')
                  OR COALESCE(t.status, 'Belum dimulai') = ?
              )
            ORDER BY
                CASE p.kategori WHEN 'Berisiko Tinggi' THEN 0 ELSE 1 END,
                CASE WHEN p.versi_model = 'v2.0'
                     THEN -p.nilai_prediksi ELSE p.nilai_prediksi END ASC,
                m.nama COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """,
            (
                filter_kategori,
                filter_kategori,
                pola,
                pola,
                pola,
                filter_tindak_lanjut,
                filter_tindak_lanjut,
                filter_tindak_lanjut,
                batas,
                offset,
            ),
        ).fetchall()
    return [dict(item) for item in baris]


def hitung_mahasiswa(
    filter_kategori=None,
    pencarian=None,
    filter_tindak_lanjut=None,
    db_path=None,
):
    """Hitung profil yang mempunyai prediksi terakhir sesuai filter."""
    pola = f"%{pencarian.strip()}%" if pencarian and pencarian.strip() else None
    with closing(_buka_koneksi(db_path)) as koneksi:
        hasil = koneksi.execute(
            """
            SELECT COUNT(*)
            FROM mahasiswa m
            JOIN riwayat_prediksi p
              ON p.id = (
                  SELECT terbaru.id
                  FROM riwayat_prediksi terbaru
                  WHERE terbaru.id_pseudonim = m.id_pseudonim
                  ORDER BY terbaru.id DESC
                  LIMIT 1
              )
            LEFT JOIN tindak_lanjut t ON t.mahasiswa_id = m.id
            WHERE (? IS NULL OR p.kategori = ?)
              AND (? IS NULL OR m.nama LIKE ? OR m.id_mahasiswa LIKE ?)
              AND (
                  ? IS NULL
                  OR (? = 'aktif' AND COALESCE(t.status, 'Belum dimulai') != 'Selesai')
                  OR COALESCE(t.status, 'Belum dimulai') = ?
              )
            """,
            (
                filter_kategori,
                filter_kategori,
                pola,
                pola,
                pola,
                filter_tindak_lanjut,
                filter_tindak_lanjut,
                filter_tindak_lanjut,
            ),
        ).fetchone()
    return hasil[0]


def id_internal_mahasiswa(id_mahasiswa, db_path=None):
    """Ambil ID internal untuk membentuk tautan profil tanpa membuka ID di URL."""
    with closing(_buka_koneksi(db_path)) as koneksi:
        baris = koneksi.execute(
            "SELECT id FROM mahasiswa WHERE id_mahasiswa = ?",
            (id_mahasiswa,),
        ).fetchone()
    return baris["id"] if baris is not None else None


def ambil_mahasiswa(id_mahasiswa, db_path=None):
    """Ambil detail terbaru profil beserta tindak lanjutnya."""
    with closing(_buka_koneksi(db_path)) as koneksi:
        baris = koneksi.execute(
            """
            SELECT m.id, m.id_mahasiswa, m.nama, m.id_pseudonim,
                   m.fitur_input,
                   p.id AS prediksi_id, p.waktu, p.versi_model,
                   p.nilai_prediksi, p.kategori, p.faktor_utama,
                   p.rekomendasi,
                   COALESCE(t.status, 'Belum dimulai') AS status_tindak_lanjut,
                   COALESCE(t.catatan, '') AS catatan_tindak_lanjut,
                   t.diperbarui_pada, t.diperbarui_oleh
            FROM mahasiswa m
            JOIN riwayat_prediksi p
              ON p.id = (
                  SELECT terbaru.id
                  FROM riwayat_prediksi terbaru
                  WHERE terbaru.id_pseudonim = m.id_pseudonim
                  ORDER BY terbaru.id DESC
                  LIMIT 1
              )
            LEFT JOIN tindak_lanjut t ON t.mahasiswa_id = m.id
            WHERE m.id = ?
            """,
            (id_mahasiswa,),
        ).fetchone()
    return dict(baris) if baris is not None else None


STATUS_TINDAK_LANJUT = (
    "Belum dimulai",
    "Terjadwal",
    "Dalam proses",
    "Selesai",
)


def simpan_tindak_lanjut(
    id_mahasiswa,
    status,
    catatan,
    diperbarui_pada,
    diperbarui_oleh,
    db_path=None,
):
    """Simpan status dan catatan pendampingan untuk satu profil."""
    if status not in STATUS_TINDAK_LANJUT:
        raise ValueError("Status tindak lanjut tidak valid.")
    with closing(_buka_koneksi(db_path)) as koneksi:
        with koneksi:
            koneksi.execute(
                """
                INSERT INTO tindak_lanjut (
                    mahasiswa_id, status, catatan, diperbarui_pada, diperbarui_oleh
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(mahasiswa_id) DO UPDATE SET
                    status = excluded.status,
                    catatan = excluded.catatan,
                    diperbarui_pada = excluded.diperbarui_pada,
                    diperbarui_oleh = excluded.diperbarui_oleh
                """,
                (id_mahasiswa, status, catatan, diperbarui_pada, diperbarui_oleh),
            )


def _validasi_paginasi(batas, offset):
    """Tolak nilai paginasi negatif atau bukan bilangan bulat."""
    if not isinstance(batas, int) or batas < 0:
        raise ValueError("batas harus bilangan bulat nol atau lebih.")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset harus bilangan bulat nol atau lebih.")


def daftar_riwayat(
    batas,
    offset,
    filter_kategori=None,
    filter_sumber=None,
    db_path=None,
):
    """Ambil riwayat terbaru dengan filter opsional dan paginasi."""
    _validasi_paginasi(batas, offset)
    with closing(_buka_koneksi(db_path)) as koneksi:
        baris = koneksi.execute(
            """
            SELECT id, waktu, id_pseudonim, versi_model, nilai_prediksi,
                   kategori, faktor_utama, sumber, id_batch, peran
            FROM riwayat_prediksi
            WHERE (? IS NULL OR kategori = ?)
              AND (? IS NULL OR sumber = ?)
            ORDER BY waktu DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (
                filter_kategori,
                filter_kategori,
                filter_sumber,
                filter_sumber,
                batas,
                offset,
            ),
        ).fetchall()
    return [dict(item) for item in baris]


def hitung_riwayat(filter_kategori=None, filter_sumber=None, db_path=None):
    """Hitung jumlah riwayat sesuai filter opsional."""
    with closing(_buka_koneksi(db_path)) as koneksi:
        hasil = koneksi.execute(
            """
            SELECT COUNT(*) FROM riwayat_prediksi
            WHERE (? IS NULL OR kategori = ?)
              AND (? IS NULL OR sumber = ?)
            """,
            (
                filter_kategori,
                filter_kategori,
                filter_sumber,
                filter_sumber,
            ),
        ).fetchone()
    return hasil[0]