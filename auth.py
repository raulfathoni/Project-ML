"""Utilitas autentikasi dan pembatasan percobaan masuk."""
from functools import wraps
from threading import Lock
from time import monotonic

from flask import abort, flash, redirect, session, url_for


def login_wajib(peran=None):
    """Batasi akses untuk sesi masuk dengan peran yang sesuai."""
    def dekorator(fungsi):
        @wraps(fungsi)
        def terbungkus(*args, **kwargs):
            peran_aktif = session.get("peran")
            if peran_aktif not in ("dosen_wali", "staf_akademik"):
                flash("Silakan masuk untuk melanjutkan.", "warning")
                return redirect(url_for("login"))
            if peran is not None and peran_aktif != peran:
                abort(403)
            return fungsi(*args, **kwargs)

        return terbungkus

    return dekorator


class PembatasPercobaanLogin:
    """Batasi percobaan masuk gagal dengan jendela waktu bergulir per IP."""

    def __init__(self, maksimum=5, periode_detik=600, jam=monotonic):
        self.maksimum = maksimum
        self.periode_detik = periode_detik
        self._jam = jam
        self._percobaan = {}
        self._kunci = Lock()

    def mulai(self, alamat_ip):
        """Reservasikan slot percobaan atau kembalikan None bila sudah dibatasi."""
        sekarang = self._jam()
        batas_waktu = sekarang - self.periode_detik
        with self._kunci:
            for ip, catatan in list(self._percobaan.items()):
                catatan[:] = [
                    item for item in catatan
                    if not item[2] or item[1] > batas_waktu
                ]
                if not catatan:
                    del self._percobaan[ip]

            catatan = self._percobaan.setdefault(alamat_ip, [])
            if len(catatan) >= self.maksimum:
                return None
            tiket = object()
            catatan.append((tiket, sekarang, False))
            return tiket

    def selesaikan(self, alamat_ip, tiket, berhasil):
        """Catat kegagalan atau bersihkan kegagalan lama setelah login berhasil."""
        sekarang = self._jam()
        with self._kunci:
            catatan = self._percobaan.get(alamat_ip)
            if not catatan:
                return

            for indeks, (tiket_tersimpan, _, _) in enumerate(catatan):
                if tiket_tersimpan is not tiket:
                    continue
                if berhasil:
                    catatan[:] = [
                        item for item in catatan
                        if item[0] is not tiket and not item[2]
                    ]
                else:
                    catatan[indeks] = (tiket, sekarang, True)
                break

            if not catatan:
                self._percobaan.pop(alamat_ip, None)
