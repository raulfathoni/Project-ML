import csv
import io
import os
import re
import sqlite3
import time
from pathlib import Path

import pandas as pd
import pytest

from app import LABEL_FITUR, create_app
from auth import PembatasPercobaanLogin, login_wajib
from config import Config
from database import (
    ambil_mahasiswa,
    daftar_riwayat,
    id_internal_mahasiswa,
    pseudonim,
)


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "kunci-uji",
            "SALT_ID": "salt-uji",
            "KATA_SANDI_DOSEN": "sandi-dosen-uji",
            "KATA_SANDI_STAF": "sandi-staf-uji",
            "SESSION_COOKIE_SECURE": False,
            "DATABASE_PATH": tmp_path / "riwayat.sqlite3",
        }
    )


def _data_individu(app, **perubahan):
    layanan = app.config["LAYANAN"]
    data = {}
    for fitur in layanan.fitur:
        stat = layanan.stat[fitur]
        data[fitur] = stat["modus"] if "nilai_unik" in stat else stat["median"]
    data.update(perubahan)
    return {fitur: str(nilai) for fitur, nilai in data.items()}


def _masuk_staf(client):
    return client.post(
        "/login",
        data={"peran": "staf_akademik", "kata_sandi": "sandi-staf-uji"},
    )


def _isi_riwayat(app, jumlah):
    with sqlite3.connect(app.config["DATABASE_PATH"]) as koneksi:
        koneksi.executemany(
            """
            INSERT INTO riwayat_prediksi (
                waktu, id_pseudonim, versi_model, nilai_prediksi,
                kategori, faktor_utama, sumber, id_batch, peran
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"2026-11-16T17:00:{indeks:02d}+00:00",
                    f"pseudo-{indeks:02d}",
                    "v1.0",
                    float(indeks),
                    "Normal" if indeks % 2 else "Berisiko Tinggi",
                    '[{"fitur":"RAHASIA_INPUT","dampak":1.0}]',
                    "csv" if indeks % 3 else "formulir",
                    None,
                    "dosen_wali",
                )
                for indeks in range(jumlah)
            ],
        )


def test_login_berhasil_dan_beranda_dapat_diakses(app):
    client = app.test_client()
    respons_login = client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
        follow_redirects=True,
    )

    assert respons_login.status_code == 200
    respons_beranda = client.get("/")
    assert respons_beranda.status_code == 200
    assert b"Beranda" in respons_beranda.data
    html = respons_beranda.get_data(as_text=True)
    assert html.index("bootstrap.min.css") < html.index("css/tokens.css") < html.index("css/app.css")
    assert client.get("/static/css/app.css").status_code == 200


def test_beranda_tidak_menampilkan_informasi_model(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    respons = client.get("/")
    html = respons.get_data(as_text=True)

    assert respons.status_code == 200
    assert "Informasi model" in html
    assert "Faktor dominan model" not in html
    assert "Keterbatasan" not in html


def test_halaman_informasi_model_menampilkan_metadata_metrik_dan_keterbatasan(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    respons = client.get("/informasi-model")
    html = respons.get_data(as_text=True)
    layanan = app.config["LAYANAN"]
    metadata = layanan.metadata
    metrik = metadata["metrik"]

    assert respons.status_code == 200
    assert metadata["algoritma"] == "GradientBoostingClassifier"
    for nilai in (
        metadata["versi_model"],
        metadata["tanggal_pelatihan"],
        str(metadata["jumlah_fitur"]),
        f"{layanan.t_tinggi:.1f}%",
        f'{metrik["test"]["AUC"]:.2f}',
        f'{metrik["test"]["PR-AUC"]:.2f}',
        f'{metrik["test"]["Recall"] * 100:.1f}%',
        f'{metrik["test"]["Precision"] * 100:.1f}%',
        f'{metrik["test"]["Ditandai"] * 100:.1f}%',
    ):
        assert nilai in html

    assert "Keterbatasan" in html
    assert f'Sekitar {metrik["test"]["Ditandai"] * 100:.0f}% mahasiswa ditandai berisiko' in html
    assert f'{(1 - metrik["test"]["Precision"]) * 100:.0f}% dari yang ditandai bukan Dropout' in html
    assert f'mendeteksi sekitar {metrik["test"]["Recall"] * 100:.0f}%' in html
    assert "bukan hubungan sebab-akibat" in html
    assert "data politeknik di Portugal" in html
    assert "belum divalidasi pada data kampus ini" in html
    assert "Hasil ini pendukung keputusan, bukan keputusan otomatis." in html
    assert f'Semua {len(layanan.fitur)} faktor model' in html
    assert "bukan pengaruh khusus untuk seorang mahasiswa" in html
    assert len(layanan.fitur) == 26
    for fitur in layanan.fitur:
        assert LABEL_FITUR.get(fitur, fitur) in html


def test_model_gradient_boosting_menghasilkan_probabilitas_dropout(app):
    layanan = app.config["LAYANAN"]
    data = _data_individu(
        app,
        **{"Curricular units 1st sem (approved)": 0},
    )
    frame = pd.DataFrame([data])

    hasil, galat, _ = layanan.proses(frame)

    probabilitas = (
        layanan.model.predict_proba(frame[layanan.fitur])[0, layanan.indeks_risiko]
        * 100
    )
    assert not galat
    assert hasil.iloc[0]["nilai_prediksi"] == pytest.approx(round(probabilitas, 2))
    assert hasil.iloc[0]["kategori"] == (
        "Berisiko Tinggi"
        if probabilitas >= layanan.t_tinggi
        else "Normal"
    )


def test_login_salah_tidak_membuat_sesi(app):
    client = app.test_client()

    respons = client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "salah"},
    )

    assert respons.status_code == 200
    html = respons.get_data(as_text=True)
    assert "Peran atau kata sandi tidak sesuai." in html
    assert "app-flash-list login-flash" in html
    with client.session_transaction() as sesi:
        assert "peran" not in sesi


def test_halaman_login_menampilkan_layout_terpisah_dan_toggle_kata_sandi(app):
    respons = app.test_client().get("/login")
    html = respons.get_data(as_text=True)

    assert respons.status_code == 200
    assert "login-layout" in html
    assert "Early Warning System" in html
    assert "Selamat datang" in html
    assert "login-password__toggle" in html
    assert 'aria-pressed="false"' in html
    assert "app-navbar" not in html
    assert "app-footer" not in html


def test_tanpa_login_diarahkan_ke_halaman_login(app):
    respons = app.test_client().get("/")

    assert respons.status_code == 302
    assert respons.headers["Location"].endswith("/login")


def test_peran_yang_salah_mendapat_403(app):
    @app.get("/uji/khusus-staf")
    @login_wajib(peran="staf_akademik")
    def halaman_staf():
        return "Khusus staf"

    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )

    respons = client.get("/uji/khusus-staf")

    assert respons.status_code == 403
    assert "Akses ditolak" in respons.get_data(as_text=True)
    assert "css/app.css" in respons.get_data(as_text=True)


def test_galat_404_413_dan_500_memakai_halaman_ramah(app):
    client = app.test_client()
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/uji/galat-server")
    def galat_server_uji():
        raise RuntimeError("detail internal yang tidak boleh ditampilkan")

    tidak_ditemukan = client.get("/alamat-tidak-ada")
    app.config["MAX_CONTENT_LENGTH"] = 1
    terlalu_besar = client.post("/login", data={"badan": "ukuran"})
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    galat_server = client.get("/uji/galat-server")

    for respons, kode in (
        (tidak_ditemukan, "404"),
        (terlalu_besar, "413"),
        (galat_server, "500"),
    ):
        html = respons.get_data(as_text=True)
        assert kode in html
        assert "css/app.css" in html
        assert "Traceback" not in html
        assert "detail internal yang tidak boleh ditampilkan" not in html

    assert tidak_ditemukan.status_code == 404
    assert terlalu_besar.status_code == 413
    assert galat_server.status_code == 500


def test_header_keamanan_dan_cookie_sesi_diterapkan(app):
    app.config["SESSION_COOKIE_SECURE"] = True
    client = app.test_client()

    respons = client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    cookie = respons.headers["Set-Cookie"]

    assert Config.SESSION_COOKIE_SECURE is True
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert respons.headers["X-Content-Type-Options"] == "nosniff"
    assert respons.headers["X-Frame-Options"] == "DENY"
    assert respons.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_csrf_melindungi_formulir_post(tmp_path):
    aplikasi = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "kunci-csrf-uji",
            "KATA_SANDI_DOSEN": "sandi-dosen-uji",
            "KATA_SANDI_STAF": "sandi-staf-uji",
            "DATABASE_PATH": tmp_path / "csrf.sqlite3",
        }
    )

    respons = aplikasi.test_client().post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )

    assert respons.status_code == 400
    assert "CSRF" in respons.get_data(as_text=True)


def test_lima_login_gagal_dibatasi_per_alamat_ip(app):
    client = app.test_client()
    alamat_ip = "192.0.2.10"

    for _ in range(5):
        respons = client.post(
            "/login",
            data={"peran": "dosen_wali", "kata_sandi": "salah"},
            environ_overrides={"REMOTE_ADDR": alamat_ip},
        )
        assert respons.status_code == 200

    dibatasi = client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
        environ_overrides={"REMOTE_ADDR": alamat_ip},
    )
    ip_lain = client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
        environ_overrides={"REMOTE_ADDR": "198.51.100.4"},
    )

    assert dibatasi.status_code == 429
    assert ip_lain.status_code == 302


def test_percobaan_gagal_kedaluwarsa_setelah_sepuluh_menit():
    waktu = [100.0]
    pembatas = PembatasPercobaanLogin(jam=lambda: waktu[0])

    for _ in range(5):
        tiket = pembatas.mulai("192.0.2.20")
        pembatas.selesaikan("192.0.2.20", tiket, berhasil=False)

    assert pembatas.mulai("192.0.2.20") is None
    waktu[0] += 600
    assert pembatas.mulai("192.0.2.20") is not None


def test_logout_menghapus_sesi(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )

    respons_keluar = client.post("/logout")

    assert respons_keluar.status_code == 302
    assert client.get("/").status_code == 302


def test_form_individu_menghasilkan_prediksi_berisiko_dan_menyimpan_pseudonim(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    data = _data_individu(
        app,
        **{
            "Curricular units 1st sem (approved)": 0,
            "id_mahasiswa": "MHS-123",
        },
    )

    respons = client.post("/individu", data=data)
    html = respons.get_data(as_text=True)
    riwayat = daftar_riwayat(10, 0, db_path=app.config["DATABASE_PATH"])

    assert respons.status_code == 200
    assert "Berisiko Tinggi" in html
    assert "Faktor yang paling memengaruhi prediksi mahasiswa ini" not in html
    assert "Rekomendasi" in html
    assert "17 field lanjutan memakai nilai awal." in html
    assert len(riwayat) == 1
    assert riwayat[0]["id_pseudonim"] == pseudonim("MHS-123", "salt-uji")
    assert riwayat[0]["id_pseudonim"] != "MHS-123"


def test_profil_tindak_lanjut_dan_dashboard_dosen_menggunakan_prediksi_tersimpan(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    data = _data_individu(
        app,
        **{
            "Curricular units 1st sem (enrolled)": 6,
            "Curricular units 1st sem (approved)": 2,
            "Curricular units 1st sem (grade)": 8.2,
            "Curricular units 1st sem (without evaluations)": 1,
            "Tuition fees up to date": 0,
            "Debtor": 1,
            "Scholarship holder": 0,
            "id_mahasiswa": "MHS-DETAIL-01",
            "nama_mahasiswa": "Ayu Pratama",
        },
    )

    respons_prediksi = client.post("/individu", data=data)
    id_profil = id_internal_mahasiswa(
        "MHS-DETAIL-01",
        db_path=app.config["DATABASE_PATH"],
    )
    dashboard = client.get("/")
    daftar = client.get("/mahasiswa")

    assert respons_prediksi.status_code == 200
    assert f"/mahasiswa/{id_profil}" in respons_prediksi.get_data(as_text=True)
    assert "Ayu Pratama" in dashboard.get_data(as_text=True)
    assert "MHS-DETAIL-01" in daftar.get_data(as_text=True)

    detail = client.post(
        f"/mahasiswa/{id_profil}",
        data={"status": "Terjadwal", "catatan": "Jadwalkan konsultasi belajar."},
        follow_redirects=True,
    )
    data_detail = ambil_mahasiswa(
        id_profil,
        db_path=app.config["DATABASE_PATH"],
    )
    html_detail = detail.get_data(as_text=True)

    assert detail.status_code == 200
    assert "Tindak lanjut berhasil disimpan." in html_detail
    assert "Faktor yang paling memengaruhi prediksi mahasiswa ini" not in html_detail
    assert "Kondisi yang perlu ditinjau" in html_detail
    assert "2 dari 6 unit" in html_detail
    assert "8,20 / 20" in html_detail
    assert "Belum lunas" in html_detail
    assert "Ada" in html_detail
    assert "Rekomendasi pendampingan" in html_detail
    assert "Jadwalkan konsultasi belajar." in html_detail
    assert data_detail["status_tindak_lanjut"] == "Terjadwal"
    assert data_detail["catatan_tindak_lanjut"] == "Jadwalkan konsultasi belajar."
    assert "Ayu Pratama" in client.get(
        "/mahasiswa?status=Terjadwal"
    ).get_data(as_text=True)
    assert "Ayu Pratama" not in client.get(
        "/mahasiswa?status=Selesai"
    ).get_data(as_text=True)


def test_dashboard_staf_berbeda_dan_rute_mahasiswa_dibatasi(app):
    client = app.test_client()
    _masuk_staf(client)

    dashboard = client.get("/")
    daftar_mahasiswa = client.get("/mahasiswa")
    html_dashboard = dashboard.get_data(as_text=True)

    assert dashboard.status_code == 200
    assert "Pemantauan data akademik" in dashboard.get_data(as_text=True)
    assert "Faktor dominan model" not in dashboard.get_data(as_text=True)
    assert "Dashboard pemantauan" not in dashboard.get_data(as_text=True)
    assert "Unggah data CSV" in html_dashboard
    assert "Riwayat CSV" in html_dashboard
    assert 'href="/mahasiswa"' not in html_dashboard
    assert 'href="/individu"' not in html_dashboard
    assert "Tindak lanjut" not in html_dashboard
    assert daftar_mahasiswa.status_code == 403


def test_form_individu_mengisi_data_lanjutan_yang_tidak_dikirim_dengan_nilai_awal(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    semua_data = _data_individu(
        app,
        **{"Curricular units 1st sem (approved)": 0},
    )
    fitur_utama = {
        "Curricular units 1st sem (credited)",
        "Curricular units 1st sem (enrolled)",
        "Curricular units 1st sem (evaluations)",
        "Curricular units 1st sem (approved)",
        "Curricular units 1st sem (grade)",
        "Curricular units 1st sem (without evaluations)",
        "Tuition fees up to date",
        "Debtor",
        "Scholarship holder",
    }
    data = {fitur: nilai for fitur, nilai in semua_data.items() if fitur in fitur_utama}

    respons = client.post("/individu", data=data)

    assert respons.status_code == 200
    assert "Berisiko Tinggi" in respons.get_data(as_text=True)
    assert "17 field lanjutan memakai nilai awal." in respons.get_data(as_text=True)


def test_form_individu_menampilkan_peringatan_di_luar_rentang(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    data = _data_individu(
        app,
        **{"Curricular units 1st sem (approved)": 27},
    )

    respons = client.post("/individu", data=data)

    assert respons.status_code == 200
    assert "di luar rentang data latih" in respons.get_data(as_text=True)


def test_galat_validasi_memunculkan_flash_dan_mempertahankan_isian(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    data = _data_individu(
        app,
        **{
            "Curricular units 1st sem (approved)": "",
            "id_mahasiswa": "MHS-VALIDASI",
        },
    )

    respons = client.post("/individu", data=data)
    html = respons.get_data(as_text=True)

    assert respons.status_code == 200
    assert "kosong atau bukan angka" in html
    assert "MHS-VALIDASI" in html
    assert 'name="Curricular units 1st sem (approved)"' in html
    assert 'value="MHS-VALIDASI"' in html


def test_individu_ditolak_untuk_staf_akademik(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "staf_akademik", "kata_sandi": "sandi-staf-uji"},
    )

    assert client.get("/individu").status_code == 403


def test_formulir_menampilkan_bantuan_kode_dan_data_lanjutan_tertutup(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )

    respons = client.get("/individu")
    html = respons.get_data(as_text=True)

    assert respons.status_code == 200
    assert "kode sesuai dokumentasi dataset" in html
    assert "<details class=\"kartu data-lanjutan\"" in html
    assert "Application mode" in html


def test_kamus_kode_mengubah_kolom_nominal_menjadi_dropdown(app, tmp_path):
    kamus_path = tmp_path / "kamus_kode.json"
    kamus_path.write_text(
        '{"Application mode": {"1": "Aplikasi umum"}}',
        encoding="utf-8",
    )
    app_dengan_kamus = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "kunci-uji-kamus",
            "KATA_SANDI_DOSEN": "sandi-dosen-uji",
            "KATA_SANDI_STAF": "sandi-staf-uji",
            "DATABASE_PATH": tmp_path / "riwayat-kamus.sqlite3",
            "KAMUS_KODE_PATH": kamus_path,
        }
    )
    client = app_dengan_kamus.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )

    respons = client.get("/individu")

    assert respons.status_code == 200
    assert 'name="Application mode"' in respons.get_data(as_text=True)
    assert "Aplikasi umum" in respons.get_data(as_text=True)


def test_unggah_csv_valid_mempertahankan_urutan_dan_menyimpan_batch(app):
    client = app.test_client()
    _masuk_staf(client)
    jalur_csv = (
        Path(__file__).resolve().parents[1]
        / "contoh_uji"
        / "contoh_unggah_valid.csv"
    )

    with jalur_csv.open("rb") as berkas:
        respons = client.post(
            "/unggah",
            data={"berkas": (berkas, jalur_csv.name)},
            content_type="multipart/form-data",
        )

    html = respons.get_data(as_text=True)
    peringkat = [int(nilai) for nilai in re.findall(r"<td>(\d+)</td>", html)]
    assert respons.status_code == 200
    assert "60 baris diproses." in html
    assert html.count('data-kategori="') == 60
    assert "Berisiko Tinggi" in html
    assert peringkat == list(range(1, 61))

    with client.session_transaction() as sesi:
        id_batch = sesi["id_batch_unduhan"]
    riwayat = daftar_riwayat(
        100,
        0,
        filter_sumber="csv",
        db_path=app.config["DATABASE_PATH"],
    )
    assert len(riwayat) == 60
    assert {item["id_batch"] for item in riwayat} == {id_batch}
    assert all(item["id_pseudonim"] for item in riwayat)

    unduhan = client.get("/unduh")
    assert unduhan.status_code == 200
    assert len(unduhan.get_data().decode("utf-8-sig").splitlines()) == 61
    unduhan.close()
    jalur_hasil = Path(app.instance_path) / "hasil_sementara" / f"{id_batch}.csv"

    assert jalur_hasil.is_file()
    assert client.post("/logout").status_code == 302
    assert not jalur_hasil.exists()


def test_template_csv_memuat_seluruh_fitur_dan_id_opsional(app):
    client = app.test_client()
    _masuk_staf(client)

    respons = client.get("/unggah/template")
    header = next(csv.reader(io.StringIO(respons.get_data(as_text=True).lstrip("\ufeff"))))

    assert respons.status_code == 200
    assert header == [
        *app.config["LAYANAN"].fitur,
        "id_mahasiswa",
        "nama_mahasiswa",
    ]


def test_unggah_csv_menyimpan_nama_id_dan_hasil_ke_daftar_mahasiswa(app):
    client = app.test_client()
    _masuk_staf(client)
    layanan = app.config["LAYANAN"]
    data = _data_individu(app, **{"Curricular units 1st sem (approved)": 0})
    keluaran = io.StringIO(newline="")
    penulis = csv.writer(keluaran)
    penulis.writerow([*layanan.fitur, "id_mahasiswa", "nama_mahasiswa"])
    penulis.writerow(
        [
            *[data[fitur] for fitur in layanan.fitur],
            "MHS-CSV-01",
            "Bima Santoso",
        ]
    )

    respons_unggah = client.post(
        "/unggah",
        data={
            "berkas": (
                io.BytesIO(keluaran.getvalue().encode("utf-8")),
                "mahasiswa.csv",
            )
        },
        content_type="multipart/form-data",
    )
    client.post("/logout")
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    daftar = client.get("/mahasiswa")

    assert respons_unggah.status_code == 200
    assert "Bima Santoso" in respons_unggah.get_data(as_text=True)
    assert "Bima Santoso" in daftar.get_data(as_text=True)
    assert "MHS-CSV-01" in daftar.get_data(as_text=True)


def test_contoh_demo_csv_konsisten_dan_menghasilkan_prediksi(app):
    jalur_csv = (
        Path(__file__).resolve().parents[1]
        / "contoh_uji"
        / "contoh_demo_mahasiswa.csv"
    )
    with jalur_csv.open(encoding="utf-8-sig", newline="") as berkas:
        baris = list(csv.DictReader(berkas))

    assert len(baris) == 6
    for mahasiswa in baris:
        jumlah_diambil = int(mahasiswa["Curricular units 1st sem (enrolled)"])
        jumlah_dinilai = int(mahasiswa["Curricular units 1st sem (evaluations)"])
        jumlah_lulus = int(mahasiswa["Curricular units 1st sem (approved)"])
        jumlah_tanpa_evaluasi = int(
            mahasiswa["Curricular units 1st sem (without evaluations)"]
        )
        assert jumlah_lulus <= jumlah_diambil
        assert jumlah_lulus <= jumlah_dinilai
        assert jumlah_tanpa_evaluasi <= jumlah_diambil

    client = app.test_client()
    _masuk_staf(client)
    with jalur_csv.open("rb") as berkas:
        respons = client.post(
            "/unggah",
            data={"berkas": (berkas, jalur_csv.name)},
            content_type="multipart/form-data",
        )

    html = respons.get_data(as_text=True)
    assert respons.status_code == 200
    assert "6 baris diproses." in html
    assert "Mahasiswa Demo 01" in html
    assert "Normal" in html
    assert "Berisiko Tinggi" in html


def test_unggah_menolak_ekstensi_dan_jumlah_baris_tidak_valid(app):
    client = app.test_client()
    _masuk_staf(client)

    ekstensi_salah = client.post(
        "/unggah",
        data={"berkas": (io.BytesIO(b"data"), "data.txt")},
        content_type="multipart/form-data",
    )
    terlalu_banyak_baris = client.post(
        "/unggah",
        data={"berkas": (io.BytesIO(b"kolom\n" + b"x\n" * 2001), "data.csv")},
        content_type="multipart/form-data",
    )

    assert "Format berkas harus .csv." in ekstensi_salah.get_data(as_text=True)
    assert "maksimal berisi 2000 baris" in terlalu_banyak_baris.get_data(as_text=True)


def test_unggah_membersihkan_berkas_hasil_lebih_dari_satu_jam(app):
    client = app.test_client()
    _masuk_staf(client)
    jalur_hasil_lama = (
        Path(app.instance_path)
        / "hasil_sementara"
        / "00000000-0000-0000-0000-000000000001.csv"
    )
    jalur_hasil_lama.write_text("hasil", encoding="utf-8")
    waktu_lama = time.time() - 3601
    os.utime(jalur_hasil_lama, (waktu_lama, waktu_lama))

    respons = client.get("/unggah")

    assert respons.status_code == 200
    assert not jalur_hasil_lama.exists()


def test_unggah_csv_dengan_kolom_course_hilang_menampilkan_galat(app):
    client = app.test_client()
    _masuk_staf(client)
    jalur_csv = (
        Path(__file__).resolve().parents[1]
        / "contoh_uji"
        / "contoh_unggah_rusak.csv"
    )

    with jalur_csv.open("rb") as berkas:
        respons = client.post(
            "/unggah",
            data={"berkas": (berkas, jalur_csv.name)},
            content_type="multipart/form-data",
        )

    html = respons.get_data(as_text=True)
    assert respons.status_code == 200
    assert "Kolom wajib tidak ditemukan" in html
    assert "Course" in html
    assert "Traceback" not in html


def test_rute_unggah_hanya_dapat_diakses_staf(app):
    client = app.test_client()
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )

    assert client.get("/unggah").status_code == 403


def test_riwayat_mendukung_kedua_peran_paginasi_urut_dan_filter(app):
    _isi_riwayat(app, 45)
    client_dosen = app.test_client()
    client_dosen.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )
    riwayat_dosen = client_dosen.get("/riwayat")
    html_dosen = riwayat_dosen.get_data(as_text=True)
    badan_dosen = html_dosen.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    id_dosen = re.findall(r"<td>(pseudo-\d{2})</td>", badan_dosen)

    assert riwayat_dosen.status_code == 200
    assert len(id_dosen) == 15
    assert id_dosen[0] == "pseudo-42"
    assert "RAHASIA_INPUT" not in html_dosen
    assert all(int(item[-2:]) % 3 == 0 for item in id_dosen)

    client_staf = app.test_client()
    client_staf.post(
        "/login",
        data={"peran": "staf_akademik", "kata_sandi": "sandi-staf-uji"},
    )
    riwayat_staf = client_staf.get("/riwayat")
    html_staf = riwayat_staf.get_data(as_text=True)
    badan_staf = html_staf.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    id_staf = re.findall(r"<td>(pseudo-\d{2})</td>", badan_staf)

    assert riwayat_staf.status_code == 200
    assert len(id_staf) == 20
    assert id_staf[0] == "pseudo-44"
    assert id_staf[-1] == "pseudo-16"
    assert all(int(item[-2:]) % 3 != 0 for item in id_staf)
    assert "Riwayat CSV" in html_staf


def test_riwayat_menampilkan_keadaan_kosong_dan_memerlukan_login(app):
    client = app.test_client()

    assert client.get("/riwayat").status_code == 302
    client.post(
        "/login",
        data={"peran": "dosen_wali", "kata_sandi": "sandi-dosen-uji"},
    )

    respons = client.get("/riwayat")

    assert respons.status_code == 200
    assert "Belum ada riwayat prediksi" in respons.get_data(as_text=True)
