# Early Warning System Akademik

Aplikasi web Flask untuk membantu dosen wali dan staf akademik memperkirakan risiko
Dropout mahasiswa menggunakan model Gradient Boosting Classifier yang sudah
disediakan. Aplikasi menampilkan probabilitas risiko, kategori, faktor utama, dan
rekomendasi intervensi. Prediksi merupakan pendukung keputusan, bukan keputusan otomatis.

## Persyaratan

- Python 3.13
- `pip`
- Aset model dan statistik yang tersedia di folder `model/`

Model tidak perlu dan tidak boleh dilatih ulang untuk menjalankan aplikasi.

## Menjalankan di Windows (PowerShell)

Jalankan perintah berikut dari folder utama proyek:

```powershell
py -3.13 --version
if (-not (Test-Path .venv-py313)) {
    py -3.13 -m venv .venv-py313
}
.\.venv-py313\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Gunakan `.venv-py313` untuk proyek ini dan pastikan `python --version`
menampilkan Python 3.13. Perintah pembuatan environment hanya dijalankan jika
folder tersebut belum ada; environment lain seperti `.venv` tidak diubah.

Jika PowerShell menolak aktivasi lingkungan virtual, izinkan skrip hanya untuk
terminal yang sedang dibuka, lalu aktifkan kembali:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv-py313\Scripts\Activate.ps1
```

Sebelum aplikasi dijalankan, atur variabel lingkungan yang diperlukan. Ganti
kata sandi dengan kata sandi kuat milik Anda:

```powershell
$env:SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:SALT_ID = (python -c "import secrets; print(secrets.token_hex(32))")
$env:KATA_SANDI_DOSEN = Read-Host "Kata sandi dosen wali"
$env:KATA_SANDI_STAF = Read-Host "Kata sandi staf akademik"
```

`SECRET_KEY` dan kedua kata sandi harus tersedia saat aplikasi mulai. Simpan
`SALT_ID` dengan aman dan gunakan nilai yang sama setiap kali menjalankan
aplikasi dengan basis data yang sama. Perintah di atas mengatur nilai hanya
untuk terminal PowerShell saat ini; atur kembali saat membuka terminal baru.
Jangan masukkan nilai rahasia ke repositori.

Jalankan aplikasi:

```powershell
python app.py
```

Buka <http://127.0.0.1:5000> di peramban. Server pengembangan Flask ini
ditujukan untuk penggunaan lokal. Untuk deployment, gunakan server aplikasi
yang sesuai dan HTTPS; konfigurasi cookie sesi aplikasi menggunakan atribut
`Secure`.

Reset riwayat prediksi
```powershell
python -c "import sqlite3; db='instance/ews.sqlite3'; c=sqlite3.connect(db); c.executescript('DELETE FROM tindak_lanjut; DELETE FROM mahasiswa; DELETE FROM riwayat_prediksi;'); c.commit(); c.close(); print('Riwayat berhasil direset')"
```

## Masuk dan menggunakan aplikasi

Pada halaman masuk, pilih peran dan masukkan kata sandi yang telah diatur:

- **Dosen Wali**: halaman beranda, prediksi individu, riwayat, dan unduh hasil.
- **Staf Akademik**: halaman beranda, unggah CSV, riwayat, dan unduh hasil.

Untuk unggah data, masuk sebagai staf akademik, unduh templat melalui halaman
unggah, lalu isi kolom fitur dan unggah berkas CSV. Berkas dibatasi hingga 2 MB
dan 2000 baris data. Sebagai contoh, tersedia
[`contoh_uji/contoh_demo_mahasiswa.csv`](contoh_uji/contoh_demo_mahasiswa.csv).

Riwayat dan data aplikasi disimpan dalam basis data SQLite di
`instance/ews.sqlite3`, yang dibuat otomatis. Kolom `id_mahasiswa` dan
`nama_mahasiswa` pada templat bersifat opsional; jika diisi, aplikasi
menyimpan data profil tersebut ke basis data lokal. Gunakan data demo sampai
penyimpanan dan akses data mahasiswa nyata telah ditinjau sesuai kebijakan
privasi organisasi.

## Menjalankan tes

Dengan lingkungan virtual aktif dan dependensi terpasang:

```powershell
python -m pytest
```

## Struktur aset penting

- `app.py`: aplikasi Flask dan rute web.
- `layanan_prediksi.py`: validasi dan pemrosesan prediksi.
- `model/ews_gb_v2.0.joblib`: model Gradient Boosting Classifier.
- `model/statistik_fitur_v2.0.json`: statistik fitur yang digunakan aplikasi.
- `templates/` dan `static/`: antarmuka web.
- `instance/ews.sqlite3`: basis data lokal yang dibuat saat aplikasi mulai.
