"""Layanan prediksi Early Warning System (tidak bergantung pada framework UI)."""
import json
import joblib
import numpy as np
import pandas as pd

# Fitur yang dijelaskan per mahasiswa (harus ada di daftar fitur model)
FITUR_PENJELAS = [
    'Curricular units 1st sem (approved)',
    'Curricular units 1st sem (grade)',
    'Curricular units 1st sem (evaluations)',
    'Curricular units 1st sem (enrolled)',
    'Curricular units 1st sem (credited)',
    'Curricular units 1st sem (without evaluations)',
    'Tuition fees up to date',
    'Debtor',
    'Scholarship holder',
    'Age at enrollment',
    'Admission grade',
]
# Aturan rekomendasi untuk faktor yang menekan prediksi
REKOMENDASI_FAKTOR = {
    'Curricular units 1st sem (approved)': 'Bimbingan akademik: evaluasi beban dan strategi mata kuliah semester 1.',
    'Curricular units 1st sem (grade)': 'Pendampingan belajar atau tutor sebaya untuk meningkatkan nilai.',
    'Curricular units 1st sem (evaluations)': 'Telusuri kehadiran dan partisipasi mahasiswa dalam ujian/evaluasi.',
    'Curricular units 1st sem (enrolled)': 'Tinjau jumlah SKS yang diambil bersama dosen wali.',
    'Curricular units 1st sem (credited)': 'Tinjau pengakuan SKS dan kesesuaian mata kuliah.',
    'Curricular units 1st sem (without evaluations)': 'Telusuri mata kuliah yang diambil tetapi tidak dievaluasi.',
    'Age at enrollment': 'Pertimbangkan pendampingan sesuai kondisi mahasiswa (mis. beban di luar kuliah).',
    'Admission grade': 'Pantau kesiapan akademik awal melalui pendampingan di awal semester.',
}
MIN_DAMPAK = 0.10   # perubahan prediksi minimum (poin nilai) agar sebuah faktor dilaporkan


class LayananPrediksi:
    def __init__(self, path_model, path_statistik):
        bundle = joblib.load(path_model)
        self.model = bundle['model']
        self.klasifikasi = hasattr(self.model, 'predict_proba')
        if self.klasifikasi:
            indeks_risiko = np.flatnonzero(np.asarray(self.model.classes_) == 1)
            if len(indeks_risiko) != 1:
                raise ValueError('Model klasifikasi harus memiliki kelas Dropout bernilai 1.')
            self.indeks_risiko = int(indeks_risiko[0])
            self.t_tinggi = float(bundle['ambang_probabilitas']) * 100
        else:
            self.model.n_jobs = 1
            self.t_tinggi = float(bundle['ambang_berisiko_tinggi'])
        self.fitur = list(bundle['fitur'])
        self.metadata = bundle['metadata']
        with open(path_statistik, encoding='utf-8') as f:
            self.stat = json.load(f)
        self.fitur_penjelas = [f for f in FITUR_PENJELAS if f in self.fitur]
        self.kepentingan_global = sorted(
            [
                {'fitur': fitur, 'nilai': float(nilai)}
                for fitur, nilai in zip(self.fitur, self.model.feature_importances_)
            ],
            key=lambda item: item['nilai'],
            reverse=True,
        )[:5]

    # ---------- validasi ----------
    def validasi(self, df):
        """Mengembalikan (df_siap, galat, peringatan). Galat menghentikan prediksi."""
        galat, peringatan = [], []
        df = df.copy()
        df.columns = df.columns.astype(str).str.strip()
        hilang = [c for c in self.fitur if c not in df.columns]
        if hilang:
            galat.append(f'Kolom wajib tidak ditemukan: {hilang}')
            return None, galat, peringatan
        x = df[self.fitur].apply(pd.to_numeric, errors='coerce')
        for c in self.fitur:
            salah = x.index[x[c].isna()].tolist()
            if salah:
                galat.append(f'Kolom "{c}" kosong atau bukan angka pada baris: {salah[:5]}'
                             + (' dst.' if len(salah) > 5 else ''))
        if galat:
            return None, galat, peringatan
        for c in self.fitur:
            lo, hi = self.stat[c]['min'], self.stat[c]['max']
            luar = int(((x[c] < lo) | (x[c] > hi)).sum())
            if luar:
                peringatan.append(f'"{c}": {luar} nilai di luar rentang data latih ({lo} sampai {hi}).')
            unik = self.stat[c].get('nilai_unik')
            if unik is not None:
                asing = int((~x[c].isin(unik)).sum())
                if asing:
                    peringatan.append(f'"{c}": {asing} kode tidak dikenal pada data latih.')
        return x, galat, peringatan

    # ---------- prediksi dan kategori ----------
    def _nilai_prediksi(self, x):
        if self.klasifikasi:
            return self.model.predict_proba(x[self.fitur])[:, self.indeks_risiko] * 100
        return self.model.predict(x[self.fitur])

    def kategori(self, nilai):
        if self.klasifikasi:
            return np.where(np.asarray(nilai) >= self.t_tinggi, 'Berisiko', 'Normal')
        return np.where(np.asarray(nilai) < self.t_tinggi, 'Berisiko', 'Normal')

    def prediksi(self, x):
        mentah = self._nilai_prediksi(x)
        hasil = pd.DataFrame(index=x.index)
        hasil['nilai_prediksi'] = mentah.round(2)
        hasil['kategori'] = self.kategori(mentah)
        hasil['peringkat'] = pd.Series(mentah, index=x.index).rank(
            method='first', ascending=not self.klasifikasi
        ).astype(int)
        return hasil

    # ---------- faktor dominan (substitusi median data latih) ----------
    def faktor_dominan(self, x, k=3):
        dasar = self._nilai_prediksi(x)
        dampak = {}
        for f in self.fitur_penjelas:
            xs = x[self.fitur].copy()
            xs[f] = self.stat[f]['median']
            pengganti = self._nilai_prediksi(xs)
            dampak[f] = dasar - pengganti if self.klasifikasi else pengganti - dasar
        dampak = pd.DataFrame(dampak, index=x.index)
        hasil = []
        for idx in x.index:
            baris = dampak.loc[idx].sort_values(ascending=False)
            hasil.append([{'fitur': f, 'dampak': round(float(v), 2)}
                          for f, v in baris.head(k).items() if v >= MIN_DAMPAK])
        return hasil

    # ---------- rekomendasi ----------
    def rekomendasi(self, baris_x, kategori, faktor):
        saran = []
        if kategori == 'Normal':
            return ['Tidak perlu intervensi khusus; lanjutkan pemantauan rutin.']
        for f in faktor:
            if f['fitur'] in REKOMENDASI_FAKTOR:
                saran.append(REKOMENDASI_FAKTOR[f['fitur']])
        # aturan langsung pada data (faktor non-akademik tidak diandalkan dari model)
        if 'Tuition fees up to date' in baris_x and baris_x['Tuition fees up to date'] == 0:
            saran.append('Kirim pengingat pembayaran uang kuliah dan informasikan skema keringanan.')
        if 'Debtor' in baris_x and baris_x['Debtor'] == 1:
            saran.append('Tindak lanjuti status tunggakan dengan bagian keuangan.')
        if 'Scholarship holder' in baris_x and baris_x['Scholarship holder'] == 0:
            saran.append('Informasikan peluang beasiswa yang tersedia.')
        if not saran:
            saran.append('Lakukan konsultasi dengan dosen wali untuk menelusuri penyebabnya.')
        return list(dict.fromkeys(saran))        # hilangkan duplikat, urutan tetap

    # ---------- alur lengkap ----------
    def proses(self, df):
        x, galat, peringatan = self.validasi(df)
        if galat:
            return None, galat, peringatan
        hasil = self.prediksi(x)
        faktor = self.faktor_dominan(x)
        hasil['faktor'] = faktor
        hasil['rekomendasi'] = [self.rekomendasi(x.loc[i], hasil.loc[i, 'kategori'], faktor[n])
                                for n, i in enumerate(x.index)]
        return hasil.sort_values(
            'nilai_prediksi', ascending=not self.klasifikasi
        ), galat, peringatan