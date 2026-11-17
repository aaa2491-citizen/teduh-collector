# TEDUH: Runbook bagian Input (Collection & Raw Storage)

Pemilik: Anne + Daniel. Serah terima ke Keshia + Mita: data mentah di Atlas.

## Keputusan yang sudah disepakati
| Sumber | Frekuensi | Dipakai untuk |
|---|---|---|
| Satu Data Jakarta (API) | 1x sekarang, 1x lagi sebelum demo | Training: target penumpang harian (Jan 2024 - Agu 2026) |
| NASA POWER | 1x sekarang, opsional 1x ±5 Des | Training (cuaca aktual) + cek akurasi forecast BMKG |
| Open-Meteo Previous Runs | 1x sekarang | Training: forecast H-1 (bukan hindsight) |
| BMKG Jakarta (267 kel.) | Harian, otomatis | Input prediksi di Flask + visualisasi + arsip |
| BMKG nasional | Harian, ~18rb desa/hari (Opsi A), simpan 14 hari | Bukti skala + dataset >= 1 GB |

Semua jalan di **Atlas Flex** (credit $50). Jadwal harian di **GitHub Actions** (repo public).
Cadangan kalau GitHub diblokir BMKG: `run_daily.bat` + Task Scheduler di laptop Daniel.

## A. Atlas (pemilik credit, ±15 menit)
1. Project baru `TEDUH` di organisasi yang punya credit.
2. Create cluster -> **Flex**, region AWS **us-east-1**.
3. Database Access -> 1 user per anggota (Read and write to any database).
4. Network Access -> `0.0.0.0/0` (IP GitHub Actions berubah-ubah).
5. Connect -> Drivers -> Python -> copy connection string.
6. Access Manager -> invite semua anggota.
7. Billing -> Alerts -> $30.

## B. Setup lokal (siapa saja, ±20 menit)
```
# Windows
py -m venv .venv
.venv\Scripts\activate
# Mac
python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt
```
Copy `.env.example` -> `.env`, isi `MONGO_URI` dan `CONTACT`.

## C. Isi data awal (sekali, ±40 menit)
| # | Perintah | Cek |
|---|---|---|
| 1 | `python 00_smoke_test.py` | status 200; kirim output ke Anne (cek struktur `data -> cuaca -> slot`, ada `tp` atau tidak) |
| 2 | download `db/archive/wilayah_2023.sql` dari github.com/cahyadsn/wilayah | (kode wilayah 2022, sesuai BMKG) |
| 3 | `python 01_build_village_list.py wilayah_2023.sql` | ~83rb desa, 267 Jakarta; catat angka pastinya untuk slide |
| 4 | `python 02_mongo_setup.py` | index tercetak |
| 5 | `python bmkg_collector.py --jakarta-once` | ~5 menit |
| 6 | `python 04_checks.py` | pass Jakarta, `new` ~267 |
| 7 | `python 03_load_historical.py ridership-api` | ~7.061 baris, sampai 2026-08-31, laporan kualitas tercetak |
| 8 | `python 03_load_historical.py nasa` | tidak banyak hari kosong selain beberapa hari terakhir |
| 9 | `python 03_load_historical.py openmeteo` | kalau "mostly empty": ulangi dengan `--model ecmwf_ifs` |

## D. GitHub Actions (pemilik repo, ±15 menit)
1. Repo **public** baru, upload semua isi folder termasuk `.github/workflows/collect.yml`. **Jangan** upload `.env` atau slide kuliah.
2. Settings -> Secrets and variables -> Actions: `MONGO_URI`, `CONTACT`.
3. Actions -> TEDUH daily collection -> Run workflow, `national_minutes` = `10`.
4. Jakarta sukses (~267 `new`) -> selesai, jalan otomatis tiap hari 19:30 WIB.
   Banyak error ke BMKG -> pakai cadangan (bagian F).

## E. Cek rutin (1 menit sehari)
- Tab Actions: job kemarin centang hijau?
- `python 04_checks.py`: TOTAL MB stabil ~2-3 GB (warning kalau > 80% dari 5 GB), pass Jakarta harian ada, `done` nasional naik.
- Backup mingguan: `python 04_checks.py --export jakarta_YYYYMMDD.csv` -> Google Drive.

## F. Cadangan: laptop Daniel (Windows)
Task Scheduler -> Create Task:
- Triggers: Daily, jam laptop biasa nyala.
- Actions: `run_daily.bat`, Start in = folder script.
- Settings: centang "Run task as soon as possible after a scheduled start is missed".
- Conditions: centang "Wake the computer to run this task".
Laptop cukup nyala ~4 jam/hari. Kalau terputus, besok lanjut otomatis.

## G. Serah terima ke Process (akhir minggu ini)
Keshia + Mita baca langsung dari Atlas `teduh_raw`:
- `bmkg_forecasts` (payload BMKG utuh; `04_checks.py --export` untuk versi rata per slot)
- `villages` (adm4, desa, kecamatan, kotkab, lat, lon) -> tabel `locations`
- `raw_ridership` (payload API utuh), `raw_nasa_power`, `raw_open_meteo`
Catatan untuk mereka: lihat laporan kualitas ridership (Mikrotrans 2026, nol Des 2024, KRL dobel Des 2025, Lebaran/pemilu/kerusuhan).

## H. Sebelum demo (±8 Des)
- `python 04_checks.py --explain 32.73.01.1001` -> screenshot index vs tanpa index
- `python 04_checks.py --unwind 31.71.01.1001` -> screenshot `$unwind`
- Refresh sekali: `ridership-api` dan `nasa`
- Setelah 12 Des: hapus cluster Flex + matikan workflow.

Atribusi wajib di Flask: "Sumber data cuaca: BMKG".
