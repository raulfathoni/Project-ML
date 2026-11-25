"""Aplikasi web Early Warning System Akademik."""
import csv
import io
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from auth import PembatasPercobaanLogin, login_wajib
from config import Config
from database import (
    STATUS_TINDAK_LANJUT,
    ambil_mahasiswa,
    daftar_riwayat,
    daftar_mahasiswa,
    hitung_mahasiswa,
    hitung_riwayat,
    id_internal_mahasiswa,
    init_db,
    pseudonim,
    ringkasan_mahasiswa,
    simpan_hasil,
    simpan_tindak_lanjut,
)
from layanan_prediksi import LayananPrediksi


csrf = CSRFProtect()

FITUR_UTAMA = (
    "Curricular units 1st sem (credited)",
    "Curricular units 1st sem (enrolled)",
    "Curricular units 1st sem (evaluations)",
    "Curricular units 1st sem (approved)",
    "Curricular units 1st sem (grade)",
    "Curricular units 1st sem (without evaluations)",
    "Tuition fees up to date",
    "Debtor",
    "Scholarship holder",
)

LABEL_FITUR = {
    "Curricular units 1st sem (credited)": "SKS semester 1 yang diakui",
    "Curricular units 1st sem (enrolled)": "Mata kuliah yang diambil pada semester 1",
    "Curricular units 1st sem (evaluations)": "Jumlah evaluasi semester 1",
    "Curricular units 1st sem (approved)": "Jumlah mata kuliah semester 1 yang lulus",
    "Curricular units 1st sem (grade)": "Nilai rata-rata semester 1",
    "Curricular units 1st sem (without evaluations)": "Mata kuliah tanpa evaluasi semester 1",
    "Tuition fees up to date": "Uang kuliah dibayar tepat waktu?",
    "Debtor": "Memiliki tunggakan?",
    "Scholarship holder": "Penerima beasiswa?",
    "Application mode": "Jalur pendaftaran (kode)",
    "Application order": "Urutan pilihan program studi",
    "Course": "Program studi (kode)",
    "Daytime/evening attendance": "Waktu perkuliahan",
    "Previous qualification": "Kualifikasi pendidikan sebelumnya (kode)",
    "Previous qualification (grade)": "Nilai kualifikasi pendidikan sebelumnya",
    "Mother's qualification": "Pendidikan ibu (kode)",
    "Father's qualification": "Pendidikan ayah (kode)",
    "Mother's occupation": "Pekerjaan ibu (kode)",
    "Father's occupation": "Pekerjaan ayah (kode)",
    "Admission grade": "Nilai saat masuk kuliah",
    "Displaced": "Berpindah tempat tinggal untuk kuliah?",
    "Educational special needs": "Memiliki kebutuhan pendidikan khusus?",
    "Age at enrollment": "Usia saat mulai kuliah",
    "Unemployment rate": "Tingkat pengangguran",
    "Inflation rate": "Tingkat inflasi",
    "GDP": "Pertumbuhan PDB",
}

OPSI_BINER = {
    "Tuition fees up to date": {1: "Ya", 0: "Tidak"},
    "Debtor": {0: "Tidak", 1: "Ya"},
    "Scholarship holder": {1: "Ya", 0: "Tidak"},
    "Daytime/evening attendance": {1: "Siang", 0: "Malam"},
    "Displaced": {1: "Ya", 0: "Tidak"},
    "Educational special needs": {1: "Ya", 0: "Tidak"},
}
BATAS_BARIS_CSV = 2000
BATAS_RIWAYAT = 20
BATAS_MAHASISWA = 20
UMUR_HASIL_SEMENTARA = 60 * 60
KUNCI_BATCH_SESI = "id_batch_unduhan"


def _format_angka(nilai):
    """Format angka ringkas untuk nilai formulir dan kode."""
    angka = float(nilai)
    return str(int(angka)) if angka.is_integer() else str(angka)


def _format_nilai(nilai):
    """Format nilai akademik dengan pemisah desimal Indonesia."""
    return f"{float(nilai):.2f}".replace(".", ",")


def _status_biner(nilai, label_ya, label_tidak):
    angka = float(nilai)
    if angka == 1:
        return label_ya
    if angka == 0:
        return label_tidak
    return f"Kode {_format_angka(angka)}"


def _sinyal_mahasiswa(fitur):
    """Bentuk ringkasan dari nilai aktual tanpa menetapkan ambang risiko baru."""
    fitur_wajib = (
        "Curricular units 1st sem (enrolled)",
        "Curricular units 1st sem (approved)",
        "Curricular units 1st sem (grade)",
        "Curricular units 1st sem (without evaluations)",
        "Tuition fees up to date",
        "Debtor",
        "Scholarship holder",
    )
    if any(nama not in fitur for nama in fitur_wajib):
        return None

    jumlah_diambil = fitur["Curricular units 1st sem (enrolled)"]
    jumlah_lulus = fitur["Curricular units 1st sem (approved)"]
    tanpa_evaluasi = fitur["Curricular units 1st sem (without evaluations)"]
    return {
        "akademik": [
            {
                "label": "Mata kuliah yang diambil",
                "nilai": f"{_format_angka(jumlah_diambil)} unit",
            },
            {
                "label": "Mata kuliah yang lulus",
                "nilai": (
                    f"{_format_angka(jumlah_lulus)} dari "
                    f"{_format_angka(jumlah_diambil)} unit"
                ),
            },
            {
                "label": "Nilai rata-rata semester 1",
                "nilai": f"{_format_nilai(fitur['Curricular units 1st sem (grade)'])} / 20",
            },
            {
                "label": "Mata kuliah tanpa evaluasi",
                "nilai": f"{_format_angka(tanpa_evaluasi)} unit",
            },
        ],
        "keuangan": [
            {
                "label": "Pembayaran kuliah",
                "nilai": _status_biner(
                    fitur["Tuition fees up to date"],
                    "Lunas",
                    "Belum lunas",
                ),
                "perhatian": float(fitur["Tuition fees up to date"]) == 0,
            },
            {
                "label": "Tunggakan",
                "nilai": _status_biner(fitur["Debtor"], "Ada", "Tidak ada"),
                "perhatian": float(fitur["Debtor"]) == 1,
            },
            {
                "label": "Status beasiswa",
                "nilai": _status_biner(
                    fitur["Scholarship holder"],
                    "Penerima beasiswa",
                    "Bukan penerima beasiswa",
                ),
                "perhatian": False,
            },
        ],
    }


def _nilai_awal(layanan, fitur):
    """Ambil modus fitur berkode atau median fitur numerik."""
    stat = layanan.stat[fitur]
    return stat["modus"] if "nilai_unik" in stat else stat["median"]


def _halaman_galat(kode, judul, pesan):
    """Render halaman galat ramah tanpa menampilkan detail internal."""
    return render_template(
        "galat.html",
        kode=kode,
        judul=judul,
        pesan=pesan,
    ), kode


def _muat_kamus_kode(lokasi):
    """Muat kamus opsional dengan bentuk {nama_fitur: {kode: label}}."""
    if not lokasi or not Path(lokasi).is_file():
        return None
    with Path(lokasi).open(encoding="utf-8") as berkas:
        kamus = json.load(berkas)
    if not isinstance(kamus, dict) or any(
        not isinstance(label_kode, dict) for label_kode in kamus.values()
    ):
        raise ValueError("Format kamus_kode.json harus berupa objek fitur dan peta kode-label.")
    return kamus


def _siapkan_bidang(layanan, fitur, kamus_kode, nilai_form, lanjutan):
    """Bangun metadata formulir dari fitur dan statistik model."""
    stat = layanan.stat[fitur]
    nilai_unik = stat.get("nilai_unik")
    nilai_awal = _nilai_awal(layanan, fitur)
    ringkasan_nilai = (
        stat.get("min"),
        stat.get("max"),
        stat.get("median"),
        stat.get("modus"),
        stat.get("q1"),
        stat.get("q3"),
    )
    pilihan = []
    bantuan = None

    if fitur in OPSI_BINER:
        jenis = "pilihan"
        pilihan = [
            {
                "nilai": _format_angka(nilai),
                "label": OPSI_BINER[fitur][int(float(nilai))],
            }
            for nilai in nilai_unik
        ]
    elif nilai_unik is not None and kamus_kode is not None:
        jenis = "pilihan"
        label_kode = kamus_kode.get(fitur, {})
        pilihan = [
            {
                "nilai": _format_angka(nilai),
                "label": str(
                    label_kode.get(
                        _format_angka(nilai),
                        label_kode.get(str(nilai), _format_angka(nilai)),
                    )
                ),
            }
            for nilai in nilai_unik
        ]
    else:
        jenis = "angka"
        if nilai_unik is not None:
            bantuan = "kode sesuai dokumentasi dataset"

    if nilai_form is not None:
        nilai = nilai_form.get(
            fitur,
            _format_angka(nilai_awal) if lanjutan else "",
        )
    else:
        nilai = _format_angka(nilai_awal) if lanjutan else ""
    return {
        "nama": fitur,
        "id": f"fitur-{layanan.fitur.index(fitur)}",
        "label": LABEL_FITUR.get(fitur, fitur),
        "jenis": jenis,
        "nilai": nilai,
        "nilai_awal": nilai_awal,
        "min": _format_angka(stat["min"]),
        "max": _format_angka(stat["max"]),
        "langkah": (
            "1"
            if all(
                nilai is None or float(nilai).is_integer()
                for nilai in ringkasan_nilai
            )
            else "any"
        ),
        "pilihan": pilihan,
        "bantuan": bantuan,
    }


def _formulir_individu(layanan, kamus_kode, nilai_form=None):
    """Siapkan sembilan input utama dan seluruh fitur lanjutan."""
    fitur_tambahan = [fitur for fitur in layanan.fitur if fitur not in FITUR_UTAMA]
    utama = [
        _siapkan_bidang(layanan, fitur, kamus_kode, nilai_form, False)
        for fitur in FITUR_UTAMA
    ]
    lanjutan = [
        _siapkan_bidang(layanan, fitur, kamus_kode, nilai_form, True)
        for fitur in fitur_tambahan
    ]
    return utama, lanjutan


def _direktori_hasil(app):
    """Kembalikan direktori private untuk hasil batch sementara."""
    return Path(app.instance_path) / "hasil_sementara"


def _bersihkan_hasil_lama(direktori, sekarang=None):
    """Hapus hanya CSV di direktori hasil yang sudah berumur lebih dari satu jam."""
    if not direktori.is_dir():
        return
    batas_waktu = (time.time() if sekarang is None else sekarang) - UMUR_HASIL_SEMENTARA
    for berkas in direktori.glob("*.csv"):
        if berkas.is_file() and berkas.stat().st_mtime < batas_waktu:
            berkas.unlink()


def _jalur_hasil(app, id_batch):
    """Validasi UUID sesi sebelum membentuk jalur berkas hasil."""
    try:
        nama_uuid = str(uuid.UUID(id_batch))
    except (AttributeError, TypeError, ValueError):
        return None
    return _direktori_hasil(app) / f"{nama_uuid}.csv"


def _hapus_hasil_sesi(app):
    """Hapus berkas hasil milik sesi aktif dan lupakan UUID-nya."""
    id_batch = session.pop(KUNCI_BATCH_SESI, None)
    jalur = _jalur_hasil(app, id_batch)
    if jalur is not None and jalur.is_file():
        jalur.unlink()


def _aman_untuk_csv(nilai):
    """Cegah nilai pengguna ditafsirkan sebagai rumus oleh aplikasi spreadsheet."""
    teks = str(nilai)
    if teks.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + teks
    return teks


def create_app(config=None):
    """Buat aplikasi, konfigurasi layanan prediksi, dan daftarkan rute."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("Variabel lingkungan SECRET_KEY wajib diatur.")

    app.config["LAYANAN"] = LayananPrediksi(
        app.config["MODEL_PATH"],
        app.config["STATISTIK_PATH"],
    )
    app.config["KAMUS_KODE"] = _muat_kamus_kode(app.config["KAMUS_KODE_PATH"])
    init_db(app.config["DATABASE_PATH"])
    direktori_hasil = _direktori_hasil(app)
    direktori_hasil.mkdir(parents=True, exist_ok=True)
    _bersihkan_hasil_lama(direktori_hasil)
    app.config["HASH_KATA_SANDI"] = {
        "dosen_wali": (
            generate_password_hash(app.config["KATA_SANDI_DOSEN"])
            if app.config.get("KATA_SANDI_DOSEN")
            else None
        ),
        "staf_akademik": (
            generate_password_hash(app.config["KATA_SANDI_STAF"])
            if app.config.get("KATA_SANDI_STAF")
            else None
        ),
    }
    pembatas_login = PembatasPercobaanLogin()

    csrf.init_app(app)

    @app.after_request
    def tambahkan_header_keamanan(respons):
        respons.headers["X-Content-Type-Options"] = "nosniff"
        respons.headers["X-Frame-Options"] = "DENY"
        respons.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return respons

    app.jinja_env.globals["versi_model"] = (
        app.config["LAYANAN"].metadata.get("versi_model", "Tidak tersedia")
    )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            alamat_ip = request.remote_addr or "unknown"
            tiket = pembatas_login.mulai(alamat_ip)
            if tiket is None:
                flash("Terlalu banyak percobaan masuk. Coba lagi setelah 10 menit.", "warning")
                return render_template("login.html"), 429

            peran = request.form.get("peran", "")
            kata_sandi = request.form.get("kata_sandi", "")
            hash_kata_sandi = app.config["HASH_KATA_SANDI"].get(peran)
            berhasil = bool(
                hash_kata_sandi and check_password_hash(hash_kata_sandi, kata_sandi)
            )
            pembatas_login.selesaikan(alamat_ip, tiket, berhasil)
            if berhasil:
                session.clear()
                session["peran"] = peran
                flash("Anda berhasil masuk.", "success")
                return redirect(url_for("beranda"))
            flash("Peran atau kata sandi tidak sesuai.", "danger")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        _hapus_hasil_sesi(app)
        session.clear()
        flash("Anda telah keluar.", "success")
        return redirect(url_for("login"))

    @app.route("/")
    @login_wajib()
    def beranda():
        if session["peran"] == "staf_akademik":
            return render_template(
                "beranda_staf.html",
                jumlah_csv=hitung_riwayat(
                    filter_sumber="csv",
                    db_path=app.config["DATABASE_PATH"],
                ),
                jumlah_csv_berisiko=hitung_riwayat(
                    "Berisiko Tinggi",
                    "csv",
                    db_path=app.config["DATABASE_PATH"],
                ),
                jumlah_csv_normal=hitung_riwayat(
                    "Normal",
                    "csv",
                    db_path=app.config["DATABASE_PATH"],
                ),
                riwayat_csv=daftar_riwayat(
                    5,
                    0,
                    filter_sumber="csv",
                    db_path=app.config["DATABASE_PATH"],
                ),
            )
        return render_template(
            "beranda.html",
            ringkasan_mahasiswa=ringkasan_mahasiswa(
                db_path=app.config["DATABASE_PATH"],
            ),
            mahasiswa_prioritas=daftar_mahasiswa(
                5,
                0,
                filter_kategori="Berisiko Tinggi",
                db_path=app.config["DATABASE_PATH"],
            ),
        )

    @app.route("/informasi-model")
    @app.route("/model")
    @login_wajib()
    def informasi_model():
        """Tampilkan metadata, evaluasi, dan kepentingan model."""
        layanan = app.config["LAYANAN"]
        metadata = layanan.metadata
        metrik = metadata["metrik"]
        return render_template(
            "informasi_model.html",
            versi_model=metadata["versi_model"],
            algoritma=metadata["algoritma"],
            tanggal_pelatihan=metadata["tanggal_pelatihan"],
            jumlah_fitur=metadata["jumlah_fitur"],
            ambang_risiko=layanan.t_tinggi,
            metrik_test=metrik["test"],
            recall_dropout=metrik["test"]["Recall"],
            precision_dropout=metrik["test"]["Precision"],
            proporsi_berisiko=metrik["test"]["Ditandai"],
            kepentingan_model=layanan.kepentingan_global,
            label_fitur=LABEL_FITUR,
        )

    @app.get("/mahasiswa")
    @login_wajib(peran="dosen_wali")
    def mahasiswa():
        filter_kategori = request.args.get("kategori", "")
        filter_status = request.args.get("status", "")
        pencarian = request.args.get("q", "").strip()
        if filter_kategori not in ("", "Berisiko Tinggi", "Normal"):
            flash("Filter kategori tidak valid.", "warning")
            filter_kategori = ""
        status_valid = ("", "aktif", *STATUS_TINDAK_LANJUT)
        if filter_status not in status_valid:
            flash("Filter tindak lanjut tidak valid.", "warning")
            filter_status = ""
        try:
            halaman = int(request.args.get("halaman", "1"))
        except ValueError:
            flash("Nomor halaman tidak valid; menampilkan halaman pertama.", "warning")
            halaman = 1
        if halaman < 1:
            flash("Nomor halaman tidak valid; menampilkan halaman pertama.", "warning")
            halaman = 1

        kategori = filter_kategori or None
        status = filter_status or None
        jumlah_mahasiswa = hitung_mahasiswa(
            kategori,
            pencarian,
            status,
            db_path=app.config["DATABASE_PATH"],
        )
        jumlah_halaman = max(
            1,
            (jumlah_mahasiswa + BATAS_MAHASISWA - 1) // BATAS_MAHASISWA,
        )
        halaman = min(halaman, jumlah_halaman)
        daftar = daftar_mahasiswa(
            BATAS_MAHASISWA,
            (halaman - 1) * BATAS_MAHASISWA,
            kategori,
            pencarian,
            status,
            db_path=app.config["DATABASE_PATH"],
        )
        return render_template(
            "mahasiswa.html",
            mahasiswa=daftar,
            jumlah_mahasiswa=jumlah_mahasiswa,
            halaman=halaman,
            jumlah_halaman=jumlah_halaman,
            filter_kategori=filter_kategori,
            filter_status=filter_status,
            pencarian=pencarian,
            status_tindak_lanjut=STATUS_TINDAK_LANJUT,
        )

    @app.route("/mahasiswa/<int:id_mahasiswa>", methods=["GET", "POST"])
    @login_wajib(peran="dosen_wali")
    def detail_mahasiswa(id_mahasiswa):
        data_mahasiswa = ambil_mahasiswa(
            id_mahasiswa,
            db_path=app.config["DATABASE_PATH"],
        )
        if data_mahasiswa is None:
            return _halaman_galat(
                404,
                "Mahasiswa tidak ditemukan",
                "Profil mahasiswa atau hasil prediksinya tidak tersedia.",
            )
        data_mahasiswa["sinyal"] = _sinyal_mahasiswa(
            json.loads(data_mahasiswa["fitur_input"])
        )

        if request.method == "POST":
            status = request.form.get("status", "")
            catatan = request.form.get("catatan", "").strip()
            if status not in STATUS_TINDAK_LANJUT:
                flash("Status tindak lanjut tidak valid.", "danger")
            elif len(catatan) > 4000:
                flash("Catatan tindak lanjut maksimal 4000 karakter.", "danger")
            else:
                simpan_tindak_lanjut(
                    id_mahasiswa,
                    status,
                    catatan,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    session["peran"],
                    db_path=app.config["DATABASE_PATH"],
                )
                flash("Tindak lanjut berhasil disimpan.", "success")
                return redirect(url_for("detail_mahasiswa", id_mahasiswa=id_mahasiswa))

        data_mahasiswa["rekomendasi"] = json.loads(
            data_mahasiswa["rekomendasi"]
        )
        return render_template(
            "detail_mahasiswa.html",
            mahasiswa=data_mahasiswa,
            status_tindak_lanjut=STATUS_TINDAK_LANJUT,
        )

    @app.get("/riwayat")
    @login_wajib()
    def riwayat():
        filter_kategori = request.args.get("kategori", "")
        filter_sumber = request.args.get("sumber", "")
        if filter_kategori not in ("", "Berisiko Tinggi", "Normal"):
            flash("Filter kategori tidak valid.", "warning")
            filter_kategori = ""
        if filter_sumber not in ("", "formulir", "csv"):
            flash("Filter sumber tidak valid.", "warning")
            filter_sumber = ""
        if session["peran"] == "staf_akademik":
            if filter_sumber == "formulir":
                flash("Riwayat staf akademik hanya menampilkan hasil CSV.", "warning")
            filter_sumber = "csv"
        elif session["peran"] == "dosen_wali":
            if filter_sumber == "csv":
                flash("Riwayat dosen wali hanya menampilkan prediksi formulir.", "warning")
            filter_sumber = "formulir"

        try:
            halaman = int(request.args.get("halaman", "1"))
        except ValueError:
            flash("Nomor halaman tidak valid; menampilkan halaman pertama.", "warning")
            halaman = 1
        if halaman < 1:
            flash("Nomor halaman tidak valid; menampilkan halaman pertama.", "warning")
            halaman = 1

        kategori = filter_kategori or None
        sumber = filter_sumber or None
        jumlah_riwayat = hitung_riwayat(
            kategori,
            sumber,
            db_path=app.config["DATABASE_PATH"],
        )
        jumlah_halaman = max(1, (jumlah_riwayat + BATAS_RIWAYAT - 1) // BATAS_RIWAYAT)
        halaman = min(halaman, jumlah_halaman)
        hasil_riwayat = daftar_riwayat(
            BATAS_RIWAYAT,
            (halaman - 1) * BATAS_RIWAYAT,
            kategori,
            sumber,
            db_path=app.config["DATABASE_PATH"],
        )
        return render_template(
            "riwayat.html",
            riwayat=hasil_riwayat,
            jumlah_riwayat=jumlah_riwayat,
            halaman=halaman,
            jumlah_halaman=jumlah_halaman,
            filter_kategori=filter_kategori,
            filter_sumber=filter_sumber,
        )

    @app.route("/individu", methods=["GET", "POST"])
    @login_wajib(peran="dosen_wali")
    def individu():
        layanan = app.config["LAYANAN"]
        kamus_kode = app.config["KAMUS_KODE"]
        nilai_form = request.form if request.method == "POST" else None
        bidang_utama, bidang_lanjutan = _formulir_individu(
            layanan,
            kamus_kode,
            nilai_form,
        )
        id_mahasiswa = (
            request.form.get("id_mahasiswa", "")
            if request.method == "POST"
            else ""
        )
        nama_mahasiswa = (
            request.form.get("nama_mahasiswa", "")
            if request.method == "POST"
            else ""
        )

        if request.method == "GET":
            return render_template(
                "individu.html",
                bidang_utama=bidang_utama,
                bidang_lanjutan=bidang_lanjutan,
                id_mahasiswa=id_mahasiswa,
                nama_mahasiswa=nama_mahasiswa,
                buka_lanjutan=False,
            )

        data = {}
        for fitur in layanan.fitur:
            if fitur in request.form:
                data[fitur] = request.form.get(fitur, "")
            elif fitur not in FITUR_UTAMA:
                data[fitur] = _format_angka(_nilai_awal(layanan, fitur))
            else:
                data[fitur] = ""
        data_frame = pd.DataFrame([data], columns=layanan.fitur)
        hasil, galat, peringatan = layanan.proses(data_frame)
        if galat:
            for pesan in galat:
                flash(pesan, "danger")
            return render_template(
                "individu.html",
                bidang_utama=bidang_utama,
                bidang_lanjutan=bidang_lanjutan,
                id_mahasiswa=id_mahasiswa,
                nama_mahasiswa=nama_mahasiswa,
                buka_lanjutan=True,
            )

        id_teks = id_mahasiswa.strip()
        nama_teks = nama_mahasiswa.strip()
        if nama_teks and not id_teks:
            flash("ID mahasiswa wajib diisi jika nama disimpan.", "danger")
            return render_template(
                "individu.html",
                bidang_utama=bidang_utama,
                bidang_lanjutan=bidang_lanjutan,
                id_mahasiswa=id_mahasiswa,
                nama_mahasiswa=nama_mahasiswa,
                buka_lanjutan=True,
            )
        if len(id_teks) > 120 or len(nama_teks) > 160:
            flash("ID maksimal 120 karakter dan nama maksimal 160 karakter.", "danger")
            return render_template(
                "individu.html",
                bidang_utama=bidang_utama,
                bidang_lanjutan=bidang_lanjutan,
                id_mahasiswa=id_mahasiswa,
                nama_mahasiswa=nama_mahasiswa,
                buka_lanjutan=True,
            )
        salt = app.config.get("SALT_ID")
        if id_teks and not salt:
            flash("SALT_ID belum dikonfigurasi; kosongkan ID mahasiswa atau hubungi administrator.", "danger")
            return render_template(
                "individu.html",
                bidang_utama=bidang_utama,
                bidang_lanjutan=bidang_lanjutan,
                id_mahasiswa=id_mahasiswa,
                nama_mahasiswa=nama_mahasiswa,
                buka_lanjutan=True,
            )

        baris = hasil.iloc[0]
        faktor = baris["faktor"]
        simpan_hasil(
            waktu=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            id_pseudonim=pseudonim(id_teks, salt) if id_teks else "",
            versi_model=layanan.metadata["versi_model"],
            nilai_prediksi=float(baris["nilai_prediksi"]),
            kategori=baris["kategori"],
            faktor_utama=faktor,
            sumber="formulir",
            id_batch=None,
            peran=session["peran"],
            db_path=app.config["DATABASE_PATH"],
            rekomendasi=baris["rekomendasi"],
            id_mahasiswa=id_teks,
            nama_mahasiswa=nama_teks,
            fitur_input={fitur: float(data[fitur]) for fitur in layanan.fitur},
        )
        id_profil = (
            id_internal_mahasiswa(id_teks, db_path=app.config["DATABASE_PATH"])
            if id_teks
            else None
        )
        jumlah_nilai_awal = sum(
            float(data[bidang["nama"]]) == float(bidang["nilai_awal"])
            for bidang in bidang_lanjutan
        )
        return render_template(
            "hasil_individu.html",
            nilai_prediksi=float(baris["nilai_prediksi"]),
            label_nilai_prediksi="Probabilitas Dropout",
            kategori=baris["kategori"],
            rekomendasi=baris["rekomendasi"],
            peringatan=peringatan,
            jumlah_nilai_awal=jumlah_nilai_awal,
            id_profil=id_profil,
        )

    @app.get("/unggah/template")
    @login_wajib(peran="staf_akademik")
    def unduh_template():
        keluaran = io.StringIO(newline="")
        csv.writer(keluaran).writerow(
            [*app.config["LAYANAN"].fitur, "id_mahasiswa", "nama_mahasiswa"]
        )
        isi = io.BytesIO(("\ufeff" + keluaran.getvalue()).encode("utf-8"))
        return send_file(
            isi,
            mimetype="text/csv",
            as_attachment=True,
            download_name="template_ews.csv",
        )

    @app.route("/unggah", methods=["GET", "POST"])
    @login_wajib(peran="staf_akademik")
    def unggah():
        _bersihkan_hasil_lama(direktori_hasil)
        if request.method == "GET":
            return render_template("unggah.html")

        berkas = request.files.get("berkas")
        if berkas is None or not berkas.filename:
            flash("Pilih berkas CSV untuk diunggah.", "danger")
            return render_template("unggah.html")
        if Path(berkas.filename).suffix.lower() != ".csv":
            flash("Format berkas harus .csv.", "danger")
            return render_template("unggah.html")

        berkas.stream.seek(0, 2)
        ukuran = berkas.stream.tell()
        berkas.stream.seek(0)
        if ukuran > app.config["MAX_CONTENT_LENGTH"]:
            flash("Ukuran berkas CSV melebihi batas 2 MB.", "danger")
            return render_template("unggah.html")

        try:
            data_frame = pd.read_csv(
                berkas.stream,
                encoding="utf-8-sig",
                nrows=BATAS_BARIS_CSV + 1,
                dtype={"id_mahasiswa": str, "nama_mahasiswa": str},
            )
        except pd.errors.EmptyDataError:
            flash("Berkas CSV kosong atau tidak memiliki header.", "danger")
            return render_template("unggah.html")
        except pd.errors.ParserError:
            flash("Format CSV tidak dapat dibaca. Periksa pemisah dan kutip data.", "danger")
            return render_template("unggah.html")
        except UnicodeDecodeError:
            flash("Berkas CSV harus menggunakan encoding UTF-8.", "danger")
            return render_template("unggah.html")

        data_frame.columns = data_frame.columns.astype(str).str.strip()
        if data_frame.empty:
            flash("Berkas CSV belum berisi baris data.", "danger")
            return render_template("unggah.html")
        if len(data_frame) > BATAS_BARIS_CSV:
            flash("Berkas CSV maksimal berisi 2000 baris data.", "danger")
            return render_template("unggah.html")

        kolom_id = "id_mahasiswa"
        kolom_nama = "nama_mahasiswa"
        id_tersedia = (
            data_frame[kolom_id]
            if kolom_id in data_frame.columns
            else pd.Series(index=data_frame.index, dtype="object")
        )
        memiliki_id = any(
            not pd.isna(nilai) and str(nilai).strip()
            for nilai in id_tersedia
        )
        nama_tersedia = (
            data_frame[kolom_nama]
            if kolom_nama in data_frame.columns
            else pd.Series(index=data_frame.index, dtype="object")
        )
        nama_tanpa_id = any(
            not pd.isna(nama) and str(nama).strip()
            and (
                indeks not in id_tersedia.index
                or pd.isna(id_tersedia.at[indeks])
                or not str(id_tersedia.at[indeks]).strip()
            )
            for indeks, nama in nama_tersedia.items()
        )
        if nama_tanpa_id:
            flash(
                "Setiap nama mahasiswa harus disertai id_mahasiswa.",
                "danger",
            )
            return render_template("unggah.html")
        if any(
            not pd.isna(nilai) and len(str(nilai).strip()) > 120
            for nilai in id_tersedia
        ) or any(
            not pd.isna(nilai) and len(str(nilai).strip()) > 160
            for nilai in nama_tersedia
        ):
            flash(
                "ID maksimal 120 karakter dan nama maksimal 160 karakter.",
                "danger",
            )
            return render_template("unggah.html")
        salt = app.config.get("SALT_ID")
        if memiliki_id and not salt:
            flash(
                "SALT_ID belum dikonfigurasi; ID mahasiswa tidak dapat dipseudonimkan.",
                "danger",
            )
            return render_template("unggah.html")

        layanan = app.config["LAYANAN"]
        hasil, galat, peringatan = layanan.proses(data_frame)
        if galat:
            for pesan in galat:
                flash(f"CSV tidak valid: {pesan}", "danger")
            return render_template("unggah.html")

        id_batch = str(uuid.uuid4())
        baris_hasil = []
        waktu = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for indeks, baris in hasil.iterrows():
            id_teks = ""
            if kolom_id in data_frame.columns:
                nilai_id = data_frame.at[indeks, kolom_id]
                if not pd.isna(nilai_id):
                    id_teks = str(nilai_id).strip()
            nama_teks = ""
            if kolom_nama in data_frame.columns:
                nilai_nama = data_frame.at[indeks, kolom_nama]
                if not pd.isna(nilai_nama):
                    nama_teks = str(nilai_nama).strip()
            nomor_baris = int(indeks) + 1
            id_tampilan = id_teks or str(nomor_baris)
            faktor = baris["faktor"]
            faktor_utama = faktor[0]["fitur"] if faktor else ""
            item = {
                "peringkat": int(baris["peringkat"]),
                "id": id_tampilan,
                "nama": nama_teks,
                "nilai_prediksi": float(baris["nilai_prediksi"]),
                "kategori": baris["kategori"],
                "faktor_utama": faktor_utama,
            }
            baris_hasil.append(item)
            simpan_hasil(
                waktu=waktu,
                id_pseudonim=pseudonim(id_teks, salt) if id_teks else "",
                versi_model=layanan.metadata["versi_model"],
                nilai_prediksi=item["nilai_prediksi"],
                kategori=item["kategori"],
                faktor_utama=faktor,
                sumber="csv",
                id_batch=id_batch,
                peran=session["peran"],
                db_path=app.config["DATABASE_PATH"],
                rekomendasi=baris["rekomendasi"],
                id_mahasiswa=id_teks,
                nama_mahasiswa=nama_teks,
                fitur_input={
                    fitur: float(data_frame.at[indeks, fitur])
                    for fitur in layanan.fitur
                },
            )

        keluaran = io.StringIO(newline="")
        penulis = csv.writer(keluaran)
        penulis.writerow(
            ["Peringkat", "Nama", "ID", "Probabilitas Dropout (%)", "Kategori", "Faktor utama"]
        )
        for item in baris_hasil:
            penulis.writerow(
                [
                    item["peringkat"],
                    _aman_untuk_csv(item["nama"]),
                    _aman_untuk_csv(item["id"]),
                    item["nilai_prediksi"],
                    item["kategori"],
                    item["faktor_utama"],
                ]
            )
        jalur_lama = _jalur_hasil(app, session.get(KUNCI_BATCH_SESI))
        if jalur_lama is not None and jalur_lama.is_file():
            jalur_lama.unlink()
        jalur_hasil = direktori_hasil / f"{id_batch}.csv"
        jalur_hasil.write_text(
            "\ufeff" + keluaran.getvalue(),
            encoding="utf-8",
            newline="",
        )
        session[KUNCI_BATCH_SESI] = id_batch
        return render_template(
            "unggah.html",
            hasil=baris_hasil,
            label_nilai_prediksi="Probabilitas Dropout (%)",
            peringatan=peringatan,
            jumlah_baris=len(baris_hasil),
            jumlah_tinggi=sum(
                item["kategori"] == "Berisiko Tinggi" for item in baris_hasil
            ),
            jumlah_normal=sum(item["kategori"] == "Normal" for item in baris_hasil),
            id_batch=id_batch,
        )

    @app.get("/unduh")
    @login_wajib()
    def unduh():
        _bersihkan_hasil_lama(direktori_hasil)
        id_batch = session.get(KUNCI_BATCH_SESI)
        jalur = _jalur_hasil(app, id_batch)
        if jalur is None or not jalur.is_file():
            session.pop(KUNCI_BATCH_SESI, None)
            flash("Berkas hasil tidak tersedia. Silakan unggah ulang data.", "warning")
            tujuan = "unggah" if session.get("peran") == "staf_akademik" else "beranda"
            return redirect(url_for(tujuan))
        return send_file(
            jalur,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"hasil-{id_batch}.csv",
        )

    @app.errorhandler(RequestEntityTooLarge)
    def ukuran_berkas_terlalu_besar(_galat):
        return _halaman_galat(
            413,
            "Berkas terlalu besar",
            "Ukuran berkas melebihi batas 2 MB. Silakan pilih berkas yang lebih kecil.",
        )

    @app.errorhandler(403)
    def akses_ditolak(_galat):
        return _halaman_galat(
            403,
            "Akses ditolak",
            "Anda tidak memiliki izin untuk membuka halaman ini.",
        )

    @app.errorhandler(404)
    def halaman_tidak_ditemukan(_galat):
        return _halaman_galat(
            404,
            "Halaman tidak ditemukan",
            "Halaman yang Anda cari tidak tersedia atau alamatnya sudah berubah.",
        )

    @app.errorhandler(500)
    def galat_server(_galat):
        return _halaman_galat(
            500,
            "Terjadi kendala",
            "Permintaan belum dapat diproses. Silakan coba lagi nanti.",
        )

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
