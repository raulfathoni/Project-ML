# Proyek: Early Warning System Akademik (Flask)

Aplikasi web untuk dosen wali dan staf akademik. Memperkirakan risiko dropout mahasiswa
dengan model Gradient Boosting Classifier yang sudah dilatih (jangan melatih ulang atau
mengubah model), lalu menampilkan kategori risiko, faktor dominan, dan rekomendasi intervensi.

## Aturan teknis
- Python + Flask, template Jinja2, Bootstrap 5 dari CDN. Tanpa framework JavaScript. JS seperlunya (vanilla).
- Database: modul `sqlite3` bawaan Python, tanpa ORM. Gunakan query berparameter (`?`), jangan menyambung string SQL.
- Teks antarmuka seluruhnya Bahasa Indonesia. Nama fungsi/variabel boleh Indonesia atau Inggris, konsisten dengan `layanan_prediksi.py`.
- JANGAN mengubah `layanan_prediksi.py`. Semua logika prediksi, validasi, kategori, faktor, dan rekomendasi
  dipanggil lewat kelas `LayananPrediksi` (metode `proses(df)` mengembalikan `(hasil, galat, peringatan)`).
- `LayananPrediksi` dibuat SEKALI saat aplikasi mulai, bukan per permintaan.
- Daftar 15 fitur model selalu diambil dari `layanan.fitur`. Rentang nilai dan nilai default dari `layanan.stat`
  (min, max, median, modus, nilai_unik). Jangan menulis angka rentang secara manual.
- Kategori hanya dua: "Berisiko" dan "Normal". Hasil terurut dari nilai prediksi terendah (paling berisiko).

## Peran pengguna
- `dosen_wali`: akses /individu, /riwayat, /, /unduh
- `staf_akademik`: akses /unggah, /riwayat, /, /unduh
Login sederhana: dua kata sandi dari variabel lingkungan (hash dengan werkzeug), tanpa tabel pengguna.
Peran disimpan di `session`. Rute yang dilindungi memakai dekorator `login_wajib(peran=...)`.

## Privasi dan keamanan
- Jangan menyimpan nama mahasiswa. `id_mahasiswa` yang diketik pengguna disimpan hanya dalam bentuk
  hash SHA-256 bersalt (salt dari variabel lingkungan), dipotong 12 karakter.
- Unggahan: hanya `.csv`, maksimal 2 MB (`MAX_CONTENT_LENGTH`), maksimal 2000 baris.
- `SECRET_KEY` dan kata sandi dari variabel lingkungan, bukan ditulis di kode. `debug=False` di produksi.
- Aktifkan CSRF (Flask-WTF `CSRFProtect`) untuk semua formulir POST.
- Semua halaman hasil menampilkan peringatan: "Hasil ini pendukung keputusan, bukan keputusan otomatis."

## Gaya kode
- Fungsi kecil, ada docstring singkat, tidak ada kode yang tidak dipakai.
- Tangani galat dengan pesan yang jelas bagi pengguna non-teknis (flash message), bukan traceback.
- Setiap fitur baru disertai tes pytest dengan klien uji Flask.

## Aturan UI (dari desain Figma)
- Tampilan mengacu pada gambar frame yang saya lampirkan dan token di static/css/tokens.css.
- JANGAN menyalin position:absolute, left/top, atau width/height tetap dari CSS Figma. Terjemahkan ke flex/grid
  yang responsif (breakpoint 390, 768, 1440).
- Semua warna, font, jarak, radius, dan bayangan harus memakai variabel di tokens.css, bukan nilai langsung.
- Gunakan HTML semantik (nav, main, form, table, button) dan label untuk setiap input.
- Bootstrap 5 hanya untuk grid dan perilaku (accordion, alert, modal). Tampilan komponen memakai class
  buatan sendiri di static/css/app.css (struktur: dasar, tata letak, komponen, halaman).
- Badge kategori jangan hanya berbeda warna: sertakan teks ("Berisiko" / "Normal") dan ikon agar terbaca
  oleh pengguna buta warna.
- Jangan mengubah logika Jinja atau rute yang sudah ada saat merapikan tampilan.