"""FocusLamp: web pendamping lampu belajar (Streamlit).

Dibuat untuk pengguna awam: bahasa sehari-hari, satu status besar di atas, dan tombol yang jelas.
Grafik sensor dan info teknis tetap ada, tetapi disimpan di bagian yang bisa dibuka.

Jalankan dari notebook FocusLamp_Streamlit_Colab.ipynb, atau di laptop:
    streamlit run app.py
Konfigurasi dibaca dari environment variable (diisi notebook) atau .streamlit/secrets.toml:
    FL_BROKER, FL_PORT, FL_USERNAME, FL_PASSWORD, FL_DEVICE_ID, FL_HISTORY_PATH
"""
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import focuslamp_core as fl

st.set_page_config(page_title="FocusLamp", page_icon=":material/lightbulb:", layout="wide", initial_sidebar_state="collapsed")

# ---------------------------------------------------------------------
# Gaya tampilan: palet "belajar malam" (tinta biru + cahaya lampu kuning)
# ---------------------------------------------------------------------
INK, LAMP, PAPER, MIST, MUTED = "#22304F", "#F2B33D", "#F7F8FB", "#E6EBF4", "#5B6478"
STYLE = """
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
.stApp, .stApp *:not([data-testid="stIconMaterial"]):not(code):not(pre) {
  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
}
.stApp { background: #F7F8FB; }
header[data-testid="stHeader"] { display: none; }
.block-container { padding-top: 1.4rem; max-width: 1100px; }
.fl-top { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:6px; }
.fl-brand { font-size:1.55rem; font-weight:800; color:#22304F; letter-spacing:-0.01em; }
.fl-brand small { display:block; font-size:0.9rem; font-weight:500; color:#5B6478; letter-spacing:0; }
.fl-pill { padding:6px 14px; border-radius:999px; font-weight:600; font-size:0.9rem; white-space:nowrap; display:inline-flex; align-items:center; gap:8px; }
.fl-pdot { width:9px; height:9px; border-radius:50%; display:inline-block; }
.fl-advice { background:#FFF7E6; border-left:6px solid #F2B33D; border-radius:18px; padding:22px 26px; margin:6px 0 14px 0; }
.fl-advice .big { font-size:1.7rem; font-weight:800; color:#22304F; line-height:1.2; }
.fl-advice .sub { color:#4A5368; margin-top:6px; font-size:1.02rem; }
.fl-legend { display:flex; align-items:center; gap:10px; margin:4px 0; }
.fl-dot { width:16px; height:16px; border-radius:50%; flex:0 0 16px; border:1px solid rgba(0,0,0,0.12); }
/* Kotak angka ringkasan: --n kolom di laptop, --m kolom di HP */
.fl-label { font-weight:700; color:#22304F; margin:14px 0 0 0; }
.fl-stats { display:grid; grid-template-columns:repeat(var(--n), minmax(0, 1fr)); gap:12px; margin:8px 0 14px 0; }
.fl-stat { background:#fff; border:1px solid #E6EBF4; border-radius:16px; padding:14px 16px; min-width:0; }
.fl-stat .k { font-size:0.86rem; color:#5B6478; }
.fl-stat .v { font-size:1.7rem; font-weight:800; color:#22304F; line-height:1.2; margin-top:4px;
              font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.fl-stat .v small { font-size:0.9rem; font-weight:600; color:#5B6478; }
/* Daftar sesi berbentuk kartu (enak dibaca di HP, tanpa geser ke samping) */
.fl-sesi { background:#fff; border:1px solid #E6EBF4; border-radius:16px; padding:12px 16px; margin-bottom:8px; }
.fl-sesi .top { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.fl-sesi .d { font-weight:700; color:#22304F; }
.fl-sesi .r { color:#5B6478; font-size:0.88rem; margin-top:4px; }
.fl-sesi .bar { height:6px; background:#E6EBF4; border-radius:99px; margin-top:8px; overflow:hidden; }
.fl-sesi .bar span { display:block; height:100%; border-radius:99px; }
.fl-badge { padding:3px 10px; border-radius:999px; font-size:0.8rem; font-weight:700; white-space:nowrap; }
/* Kartu putih (st.container(border=True)) untuk mengelompokkan isi, seperti dashboard web */
div[data-testid="stVerticalBlock"][class*="BorderWrapper"], div[data-testid="stVerticalBlockBorderWrapper"] {
  background:#fff; border-radius:20px !important; border-color:#E6EBF4 !important; }
.fl-mini { background:#FFF7E6; border-radius:16px; padding:14px 16px; margin-top:10px; }
.fl-mini .k { font-size:0.86rem; color:#5B6478; }
.fl-mini .v { font-size:1.25rem; font-weight:800; color:#22304F; margin-top:2px; }
.fl-mobile-only { display:none; }
/* ---------- Tampilan HP: seperti aplikasi (menu di bawah layar) ---------- */
@media (max-width: 640px) {
  .fl-mobile-only { display:block; }
  .fl-desktop-only { display:none; }
  .block-container { padding-left:1rem; padding-right:1rem; padding-top:0.9rem; padding-bottom:110px !important; }
  .fl-brand { font-size:1.3rem; }
  .fl-brand small { display:none; }            /* bilah atas ringkas seperti aplikasi */
  .fl-top { flex-wrap:nowrap; margin-bottom:10px; }
  .fl-pill { font-size:0.78rem; padding:5px 11px; }
  /* Menu tab dipindah menjadi navigasi bawah (bottom navigation) seperti aplikasi Android */
  .stTabs [role="tablist"] {
    position:fixed; left:0; right:0; bottom:0; z-index:999990; gap:0; margin:0;
    background:#fff; border-top:1px solid #E6EBF4; box-shadow:0 -6px 22px rgba(34,48,79,0.10);
    padding:6px 6px calc(8px + env(safe-area-inset-bottom)); overflow:visible; }
  .stTabs [role="tab"] { flex:1 1 0; justify-content:center; padding:4px 2px; min-width:0; height:auto; }
  .stTabs [role="tab"] [data-testid="stMarkdownContainer"] p {
    display:flex; flex-direction:column; align-items:center; gap:3px; font-size:0.72rem; font-weight:600;
    white-space:nowrap; color:#5B6478; margin:0; }
  .stTabs [role="tab"] p > span[role="img"] {
    display:block !important; font-size:1.45rem; line-height:1; padding:4px 16px; border-radius:999px; color:#5B6478; }
  .stTabs [aria-selected="true"] [data-testid="stMarkdownContainer"] p { color:#22304F; font-weight:800; }
  .stTabs [aria-selected="true"] p > span[role="img"] { background:#FFE7B0; color:#22304F; }
  .stTabs .react-aria-SelectionIndicator { display:none; }
  [data-testid="stToastContainer"] { bottom:88px !important; }
  .fl-stats { grid-template-columns:repeat(var(--m), minmax(0, 1fr)); gap:8px; }
  .fl-stat { padding:10px 12px; border-radius:14px; }
  .fl-stat .k { font-size:0.74rem; }
  .fl-stat .v { font-size:1.3rem; }
  .fl-advice { padding:16px 18px; }
  .fl-advice .big { font-size:1.3rem; }
  .fl-advice .sub { font-size:0.95rem; }
  /* tombol selebar layar supaya mudah ditekan jari */
  [data-testid="stElementContainer"]:has(.stButton), [data-testid="stElementContainer"]:has(.stDownloadButton) { width:100% !important; }
  .stButton, .stDownloadButton, .stButton button, .stDownloadButton button { width:100% !important; }
  .stButton button, .stDownloadButton button { min-height:48px; border-radius:14px; }
  h3 { font-size:1.25rem !important; }
}
</style>
"""
st.markdown(STYLE, unsafe_allow_html=True)

# Supaya web bisa dipasang di layar utama HP seperti aplikasi (Chrome: menu > Tambahkan ke layar utama / Instal aplikasi).
# File manifest dan ikon ada di folder static/ dan disajikan Streamlit di alamat app/static/ (server.enableStaticServing).
PWA_JS = """<script>
(function () {
  const d = document, h = d.head;
  if (d.getElementById("fl-manifest")) return;
  const add = (tag, attrs) => { const e = d.createElement(tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); h.appendChild(e); };
  add("link", {id: "fl-manifest", rel: "manifest", href: "app/static/manifest.json"});
  add("link", {rel: "apple-touch-icon", href: "app/static/icon-192.png"});
  add("meta", {name: "theme-color", content: "#22304F"});
  add("meta", {name: "mobile-web-app-capable", content: "yes"});
  add("meta", {name: "apple-mobile-web-app-capable", content: "yes"});
  add("meta", {name: "apple-mobile-web-app-title", content: "FocusLamp"});
  // Seperti aplikasi: pindah menu di navigasi bawah langsung kembali ke bagian atas halaman.
  d.addEventListener("click", (e) => {
    if (!e.target.closest('[role="tab"]')) return;
    [d.scrollingElement, d.querySelector('[data-testid="stMain"]'), d.querySelector('[data-testid="stAppViewContainer"]')]
      .forEach((el) => { if (el) el.scrollTo({top: 0}); });
  }, true);
})();
</script>"""
st.html(PWA_JS, unsafe_allow_javascript=True)


# ---------------------------------------------------------------------
# Konfigurasi + koneksi (dibuat sekali, dipakai semua pengunjung halaman)
# ---------------------------------------------------------------------
def cfg(key, default=""):
    """Ambil konfigurasi dari environment variable (diisi notebook), lalu dari Streamlit Secrets, atau nilai bawaan."""
    if os.environ.get(key):
        return os.environ[key]
    try:
        return str(st.secrets[key])
    except Exception:
        return default


@st.cache_resource
def get_hub():
    """Buat satu FocusLampHub untuk seluruh aplikasi (disimpan cache sehingga koneksi MQTT tidak dibuat ulang setiap klik)."""
    return fl.FocusLampHub(
        broker=cfg("FL_BROKER"), port=int(cfg("FL_PORT", "8883")),
        username=cfg("FL_USERNAME"), password=cfg("FL_PASSWORD"),
        device_id=cfg("FL_DEVICE_ID", "fl-01"), history_path=cfg("FL_HISTORY_PATH", "riwayat_sesi.csv"))


hub = get_hub()
PRESETS = [15, 20, 25, 30, 45, 60]          # mode normal
PRESETS_UJI = [1, 2, 3, 5, 10, 15]          # mode uji coba (demo): durasi pendek untuk pengujian
HASIL = {"SELESAI": "Tuntas", "END_PAKSA": "Dihentikan", "DITINGGAL": "Ditinggal"}
PENYEBAB = {"waktu_habis": "Waktu habis", "tombol_end": "Tombol berhenti ditekan", "hp_diangkat": "HP diangkat terlalu lama",
            "ditinggal": "Meninggalkan meja terlalu lama", "dashboard": "Dihentikan dari web"}
WARNA_HASIL = {"Tuntas": ("#E3F6EC", "#1E8E55"), "Dihentikan": ("#FDE8E8", "#C23A3E"), "Ditinggal": ("#FDF0E1", "#B86414")}
BULAN = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
PLOT_CFG = {"displayModeBar": False, "scrollZoom": False, "responsive": True}


def tgl_id(d):
    """Format tanggal singkat berbahasa Indonesia, misalnya 24 Okt."""
    return f"{d.day} {BULAN[d.month]}"


def angka(items, n, m=None):
    """Baris kotak angka ringkasan. items = [(label, nilai)], n = kolom di laptop, m = kolom di HP.

    Dipakai menggantikan st.metric karena st.metric tersusun ke bawah satu per satu di layar HP."""
    isi = "".join(f"<div class='fl-stat'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in items)
    st.markdown(f"<div class='fl-stats' style='--n:{n};--m:{m or n}'>{isi}</div>", unsafe_allow_html=True)


def tampil(fig):
    """Tampilkan grafik Plotly yang ramah layar sentuh: tanpa toolbar, tanpa zoom saat halaman digeser jari."""
    fig.update_layout(dragmode=False)
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def tell(ok, msg_ok):
    """Beri tahu hasil perintah dengan bahasa sederhana (notifikasi kecil di pojok)."""
    if ok:
        st.toast(msg_ok, icon=":material/check_circle:")
    else:
        st.toast("Perintah belum terkirim. Pastikan lampu terhubung (lihat status di kanan atas).", icon=":material/error:")


def koneksi():
    """Status koneksi dalam bahasa awam: (teks, warna latar, warna teks)."""
    s = hub.status_now()
    upd = s["updated"]
    baru = upd is not None and (datetime.now(fl.WIB) - pd.Timestamp(upd).to_pydatetime()) < timedelta(seconds=20)
    if hub.mode_simulasi:
        return "Memakai data contoh", "#EDE7F6", "#4527A0"
    if not hub.conn["connected"]:
        return "Server belum terhubung", "#FDE8E8", "#B42318"
    if not baru:
        return "Lampu belum tersambung", "#FFF4DB", "#8A5A00"
    return "Lampu terhubung", "#E3F5EC", "#1E7A4C"


def kondisi(s):
    """Terjemahkan state alat menjadi judul, kalimat, warna cincin, dan petunjuk untuk pengguna awam."""
    state, rem, tgt = s["state"], s["remaining_s"], s["target_s"]
    menit = f"{int(rem) // 60} menit" if rem and rem >= 60 else f"{int(rem or 0)} detik"
    if state in ("FOCUS", "AWAY") and s["dock"] is False:
        return ("HP diangkat", "Kembalikan HP ke dudukan dalam beberapa detik, kalau tidak sesi akan berhenti.",
                "#E5484D", "Lampu berkedip merah dan lampu kecil DOCK ikut berkedip.")
    table = {
        "IDLE": ("Letakkan HP di dudukan", "Lampu menyala setelah HP terpasang di dudukannya.", "#8A94A8",
                 "Di simulasi Wokwi: geser slide switch ke posisi dock."),
        "READY": ("Siap belajar", "Pilih lama belajar di bawah, lalu tekan Mulai fokus. Bisa juga dengan tombol hijau di lampu.",
                  "#6FA8FF", "Lampu menyala biru redup dan lampu kecil DOCK menyala hijau."),
        "FOCUS": ("Kamu sedang fokus", f"Sisa <span id='sisa'>{menit}</span>. Tetap di meja dan biarkan HP di dudukan.", LAMP,
                  "Lampu putih; kecerahannya menyesuaikan cahaya ruangan."),
        "AWAY": ("Kamu meninggalkan meja",
                 "Timer dijeda. Kembali ke meja sebelum sesi dianggap ditinggal." if s["paused"]
                 else "Kembali ke meja agar sesi berlanjut.", "#F28C28", "Lampu menyala kuning."),
        "SELESAI": ("Sesi tuntas", "Kerja bagus! Istirahat sebentar sebelum sesi berikutnya.", "#2FBF71", "Lampu hijau."),
        "END_PAKSA": ("Sesi dihentikan", "Sesi berhenti sebelum waktunya dan tetap tercatat di riwayat.", "#E5484D", "Lampu merah."),
        "DITINGGAL": ("Sesi ditinggal", "Kamu terlalu lama meninggalkan meja, jadi sesi diakhiri.", "#B5654A", "Lampu merah redup."),
    }
    return table.get(state, ("Menunggu data dari lampu", "Nyalakan simulasi Wokwi. Status akan muncul di sini dalam beberapa detik.",
                             "#8A94A8", "Jika lama tidak muncul, buka menu Bantuan."))


HERO_HTML = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>
  html, body { margin:0; background:transparent; font-family:'Plus Jakarta Sans', system-ui, sans-serif; }
  .hero { display:flex; align-items:center; gap:34px; background:#22304F; color:#fff; border-radius:28px;
          padding:28px 34px; box-sizing:border-box; min-height:262px; }
  .ring { width:200px; height:200px; flex:0 0 200px; border-radius:50%; display:grid; place-items:center;
          background: conic-gradient(var(--c) calc(var(--p) * 1%), rgba(255,255,255,0.13) 0);
          box-shadow: 0 0 46px var(--glow); }
  .in { width:158px; height:158px; border-radius:50%; background:#22304F; display:grid; place-items:center; text-align:center; }
  .t { font-size:2.6rem; font-weight:800; font-variant-numeric:tabular-nums; line-height:1; }
  .l { font-size:0.82rem; color:#C9D1E3; margin-top:6px; }
  .st { font-size:2rem; font-weight:800; line-height:1.15; margin-bottom:8px; }
  .say { font-size:1.08rem; color:#DCE2EE; line-height:1.55; max-width:52ch; }
  .hint { margin-top:14px; font-size:0.92rem; color:#AEB8CE; }
  /* Layar HP: cincin besar di tengah dan teks di bawahnya, seperti layar timer aplikasi */
  @media (max-width: 600px) {
    .hero { flex-direction:column; text-align:center; gap:16px; padding:24px 20px 22px; border-radius:24px; min-height:0; }
    .ring { width:190px; height:190px; flex-basis:auto; }
    .in { width:150px; height:150px; }
    .t { font-size:2.5rem; } .l { font-size:0.8rem; }
    .st { font-size:1.4rem; margin-bottom:6px; } .say { font-size:0.95rem; line-height:1.5; margin:0 auto; }
    .hint { font-size:0.82rem; margin-top:10px; }
  }
</style></head><body>
<div class="hero">
  <div class="ring" id="ring" style="--p:__P__; --c:__C__; --glow:__G__">
    <div class="in"><div><div class="t" id="t">__T__</div><div class="l">__L__</div></div></div>
  </div>
  <div><div class="st">__J__</div><div class="say">__K__</div><div class="hint">__H__</div></div>
</div>
<script>
  // Hitung mundur berjalan di browser setiap 0,2 detik. Kecepatan (RATE) diukur dari data alat,
  // jadi tetap sama dengan OLED Wokwi walaupun simulasi berjalan lebih lambat dari waktu nyata.
  const REM = __REM__, AT = __AT__, RATE = __RATE__, RUN = __RUN__, TGT = __TGT__, SERVER_NOW = __NOW__;
  const offset = Date.now() / 1000 - SERVER_NOW;      // selisih jam browser dan jam server
  const fmt = s => { s = Math.max(0, Math.ceil(s)); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); };
  function tick() {
    if (REM === null) return;
    const now = Date.now() / 1000 - offset;
    const r = RUN ? Math.max(0, REM - RATE * (now - AT)) : REM;
    document.getElementById("t").textContent = fmt(r);
    const m = document.getElementById("sisa");
    if (m) m.textContent = r >= 60 ? Math.ceil(r / 60) + " menit" : Math.ceil(r) + " detik";
    if (TGT > 0 && __PROGRESS__) document.getElementById("ring").style.setProperty("--p", Math.min(100, 100 * r / TGT).toFixed(2));
  }
  tick();
  if (RUN) setInterval(tick, 200);

  // Tinggi kartu mengikuti isinya: di HP kartu lebih tinggi karena cincin dan teks disusun ke bawah.
  // Bingkai iframe dan wadah elemen Streamlit di luarnya ikut diubah tingginya.
  function fit() {
    try {
      const f = window.frameElement, h = Math.ceil(document.querySelector(".hero").getBoundingClientRect().height) + 4;
      if (!f) return;
      f.style.height = h + "px"; f.setAttribute("height", h);
      const box = f.closest('[data-testid="stElementContainer"]');
      if (box) box.style.height = h + "px";
    } catch (e) {}
  }
  fit(); window.addEventListener("resize", fit);
  if (window.ResizeObserver) new ResizeObserver(fit).observe(document.querySelector(".hero"));
</script></body></html>"""


def hero():
    """Kartu utama: cincin hitung mundur (meniru cincin lampu) + kalimat status + petunjuk.

    Hitung mundurnya dijalankan di browser agar mulus. Server hanya mengirim titik acuan (sisa detik,
    waktu diterima, kecepatan timer alat). Titik acuan lama dipakai terus selama prediksinya masih cocok
    dengan data baru, supaya kartu tidak dimuat ulang setiap 3 detik."""
    s = hub.status_now()
    judul, kalimat, warna, petunjuk = kondisi(s)
    cd = hub.countdown()
    lama = st.session_state.get("_acuan")
    if lama and cd["rem"] is not None and lama["running"] == cd["running"] and lama["state"] == cd["state"] \
            and lama["target"] == cd["target"] and lama["rem"] is not None:
        prediksi = lama["rem"] - (lama["rate"] * (cd["at"] - lama["at"]) if lama["running"] else 0)
        if abs(prediksi - cd["rem"]) <= 2.5 and abs(lama["rate"] - cd["rate"]) <= 0.15:
            cd = lama                                   # data baru masih sesuai prediksi: kartu tidak perlu dimuat ulang
    if cd is not lama:
        cd = dict(cd, now=time.time())
        st.session_state["_acuan"] = cd
    rem, tgt = cd["rem"], cd["target"]
    persen = 100 if s["state"] == "SELESAI" else (0 if not tgt else max(0, min(100, 100 * (rem or 0) / tgt)))
    glow = warna + "66" if s["state"] in ("FOCUS", "READY", "SELESAI") else "transparent"
    ganti = {"__P__": f"{persen:.2f}", "__C__": warna, "__G__": glow,
             "__T__": fl.fmt_mmss(rem) if rem is not None else "--:--",
             "__L__": "sisa waktu" if s["state"] in ("FOCUS", "AWAY") else "lama sesi",
             "__J__": judul, "__K__": kalimat, "__H__": petunjuk,
             "__REM__": "null" if rem is None else f"{rem:.2f}", "__AT__": f"{cd['at']:.3f}",
             "__RATE__": f"{cd['rate']:.2f}", "__RUN__": "true" if cd["running"] else "false",
             "__TGT__": str(int(tgt or 0)), "__NOW__": f"{cd['now']:.3f}",
             "__PROGRESS__": "false" if s["state"] == "SELESAI" else "true"}
    html = HERO_HTML
    for k, v in ganti.items():
        html = html.replace(k, v)
    components.html(html, height=266)
    return s


def ringkasan_hari_ini():
    """Tiga angka sederhana untuk hari ini: jumlah sesi, menit fokus, sesi tuntas."""
    df = hub.sessions_df()
    if not df.empty:
        tgl = pd.to_datetime(df["start_ts"], unit="s", utc=True).dt.tz_convert("Asia/Jakarta").dt.date
        df = df[tgl == datetime.now(fl.WIB).date()]
    st.markdown("<div class='fl-label'>Hari ini</div>", unsafe_allow_html=True)
    angka([("Sesi", len(df)),
           ("Menit fokus", f"{(df['focus_s'].sum() / 60 if len(df) else 0):.0f}"),
           ("Sesi tuntas", int((df["result"] == "SELESAI").sum()) if len(df) else 0)], n=3)


# ---------------------------------------------------------------------
# Kepala halaman + status koneksi
# ---------------------------------------------------------------------
@st.fragment(run_every=3)
def kepala():
    """Nama aplikasi dan status koneksi lampu (diperbarui otomatis)."""
    teks, bg, fg = koneksi()
    st.markdown(f"""<div class="fl-top"><div class="fl-brand">FocusLamp<small>Teman belajar tanpa gangguan HP</small></div>
<span class="fl-pill" style="background:{bg};color:{fg}"><span class="fl-pdot" style="background:{fg}"></span>{teks}</span></div>""", unsafe_allow_html=True)


kepala()
tab_home, tab_hist, tab_ai, tab_help = st.tabs([":material/home: Beranda", ":material/calendar_month: Riwayat", ":material/lightbulb: Saran AI", ":material/help: Bantuan"])


# ---------------------------------------------------------------------
# Tab Beranda
# ---------------------------------------------------------------------
@st.fragment(run_every=3)
def beranda_status():
    """Kartu status besar dan angka hari ini; diperbarui otomatis tiap 3 detik."""
    s = hero()
    if s["state"] in ("FOCUS", "AWAY"):
        if st.button("Hentikan sesi", help="Sesi berhenti sekarang dan tercatat sebagai dihentikan."):
            tell(*hub.stop_session()[:1], "Sesi dihentikan")
    st.session_state["_state_alat"] = s["state"]


@st.fragment(run_every=10)
def hari_ini():
    """Angka hari ini dan saran AI singkat (kolom kanan di laptop, di bawah tombol mulai di HP)."""
    ringkasan_hari_ini()
    res = fl.hitung_rekomendasi(hub.sessions_df(), pakai_demo=st.session_state.get("pakai_demo", False))
    isi = f"Coba {res['durasi']} menit per sesi" if res["ok"] else "Muncul setelah beberapa sesi"
    st.markdown(f"<div class='fl-mini'><div class='k'>Saran AI minggu ini</div><div class='v'>{isi}</div>"
                f"<div class='k' style='margin-top:4px'>Alasannya ada di menu Saran AI.</div></div>", unsafe_allow_html=True)


@st.fragment(run_every=3)
def mulai_sesi():
    """Pilihan lama belajar dan tombol Mulai fokus; status tombol ikut diperbarui otomatis."""
    st.subheader("Mulai sesi belajar")
    s = hub.status_now()
    state_alat, uji = s["state"], bool(s["demo"])
    siap = state_alat == "READY" and hub.conn["connected"] and not hub.mode_simulasi
    mode = "uji" if uji else "normal"
    opsi, bawaan = (PRESETS_UJI, 2) if uji else (PRESETS, 25)
    # Pilihan cepat + "Atur sendiri" untuk durasi bebas (0,5-180 menit, batas yang diterima ESP32)
    pilihan = st.segmented_control("Mau belajar berapa lama?", options=opsi + ["atur"], default=bawaan,
                                   format_func=lambda m: "Atur sendiri" if m == "atur" else f"{m} menit",
                                   key=f"durasi_{mode}")
    if pilihan == "atur":
        durasi = st.number_input("Lama belajar (menit)", min_value=0.5 if uji else 1.0, max_value=180.0,
                                 value=4.0 if uji else 40.0, step=0.5 if uji else 1.0, format="%g",
                                 key=f"durasi_sendiri_{mode}",
                                 help=f"Ketik angka atau pakai tombol - / +. Batas {'0,5' if uji else '1'} sampai 180 menit.")
    else:
        durasi = pilihan or bawaan
    if not uji and state_alat == "READY":
        selesai = datetime.now(fl.WIB) + timedelta(minutes=float(durasi))
        st.caption(f"Kalau mulai sekarang, sesi selesai sekitar pukul {selesai:%H:%M}.")
    if st.button(f"Mulai fokus {durasi:g} menit", type="primary", disabled=not siap):
        ok1, _ = hub.set_duration(durasi)
        ok2, _ = hub.start_session()
        tell(ok1 and ok2, f"Sesi {durasi:g} menit dimulai")
    if uji:
        st.caption("Lampu sedang di mode uji coba, jadi pilihan durasinya pendek untuk pengujian dan demo.")
    if not siap:
        if hub.mode_simulasi:
            st.caption("Sedang memakai data contoh. Kembali ke data asli di tab Bantuan untuk mengontrol lampu.")
        elif not hub.conn["connected"]:
            st.caption("Tombol aktif setelah web terhubung ke server.")
        elif state_alat in ("FOCUS", "AWAY"):
            st.caption("Sesi sedang berjalan. Sesi baru bisa dimulai setelah sesi ini selesai.")
        else:
            st.caption("Letakkan HP di dudukan dulu. Tombol aktif saat status menunjukkan Siap belajar.")


with tab_home:
    beranda_status()
    # Laptop: dua kolom (mulai sesi | ringkasan). HP: Streamlit otomatis menyusunnya ke bawah.
    kiri, kanan = st.columns([3, 2], gap="large")
    with kanan:
        hari_ini()
    with kiri:
        with st.container(border=True):
            mulai_sesi()
        with st.expander("Pengaturan lanjutan"):
            demo_now = bool(hub.status_now()["demo"])
            mode = st.radio("Mode waktu", ["Normal (untuk dipakai sehari-hari)", "Uji coba (deteksi dipercepat untuk demo)"],
                            index=1 if demo_now else 0)
            if st.button("Simpan mode", disabled=not (hub.conn["connected"] and not hub.mode_simulasi)):
                tell(hub.set_demo(mode.startswith("Uji"))[0], "Mode disimpan")
            st.caption("Saran AI hanya memakai sesi mode Normal. Mode hanya bisa diganti saat tidak ada sesi berjalan.")

    with st.expander("Detail sensor (untuk teknisi dan juri)"):
        @st.fragment(run_every=3)
        def detail_sensor():
            """Grafik jarak, cahaya, riwayat sesi, dan event terbaru seperti dashboard teknis."""
            s = hub.status_now()
            yn = lambda v: "-" if v is None else ("Ya" if v else "Tidak")
            angka([("State", s["state"]), ("Pengguna hadir", yn(s["present"])), ("HP di dudukan", yn(s["dock"])),
                   ("Cahaya ruangan", "-" if s["light_pct"] is None else f"{int(s['light_pct'])}%"),
                   ("Mode", "-" if s["demo"] is None else ("Uji coba" if s["demo"] else "Normal"))], n=5, m=2)
            tel, ev, _, _ = hub.frames()
            tampil(fl.fig_distance(tel))
            a, b = st.columns(2)
            with a:
                tampil(fl.fig_light(tel))
            with b:
                tampil(fl.fig_history(hub.sessions_df()))
            st.dataframe(fl.events_table(ev), width="stretch", hide_index=True)
        detail_sensor()


# ---------------------------------------------------------------------
# Tab Riwayat
# ---------------------------------------------------------------------
def tabel_ramah(df):
    """Tabel sesi dengan kolom berbahasa sehari-hari, terbaru di atas."""
    t = pd.to_datetime(df["start_ts"], unit="s", utc=True).dt.tz_convert("Asia/Jakarta")
    out = pd.DataFrame({
        "Tanggal": t.dt.strftime("%d %b %Y"), "Mulai": t.dt.strftime("%H:%M"),
        "Rencana (menit)": (df["target_s"] / 60).round(1), "Fokus (menit)": (df["focus_s"] / 60).round(1),
        "Hasil": df["result"].map(HASIL).fillna(df["result"]),
        "Penyebab berhenti": df["end_reason"].map(PENYEBAB).fillna(df["end_reason"]),
        "Pergi dari meja": df["away_count"].astype(int), "HP diangkat": df["dock_lift_count"].astype(int)})
    return out.iloc[::-1].reset_index(drop=True)


def grafik_harian(df, hari=14):
    """Grafik batang menit fokus per hari selama `hari` terakhir."""
    t = pd.to_datetime(df["start_ts"], unit="s", utc=True).dt.tz_convert("Asia/Jakarta").dt.date
    per = (df.assign(tgl=t).groupby("tgl")["focus_s"].sum() / 60)
    akhir = datetime.now(fl.WIB).date()
    idx = [akhir - timedelta(days=i) for i in range(hari - 1, -1, -1)]
    per = per.reindex(idx, fill_value=0)
    label = [tgl_id(d) for d in per.index]
    fig = go.Figure(go.Bar(x=label, y=per.values, marker_color=LAMP,
                           hovertemplate="%{x}: %{y:.0f} menit fokus<extra></extra>"))
    fig.update_layout(height=250, template="plotly_white", margin=dict(l=4, r=4, t=10, b=4),
                      yaxis_title="menit", font=dict(family="Plus Jakarta Sans, sans-serif"), bargap=0.25)
    # label tanggal ditulis tiap 2 hari dan tidak dimiringkan, supaya tetap terbaca di layar HP
    fig.update_xaxes(tickangle=0, tickmode="array", tickvals=label[1::2], ticktext=label[1::2])
    return fig


def daftar_sesi(df, n=8):
    """Sesi terbaru sebagai kartu: tanggal, hasil (berwarna), menit fokus dari rencana, penyebab berhenti.

    Kartu dipakai alih-alih tabel lebar karena tabel harus digeser ke samping di layar HP."""
    t = pd.to_datetime(df["start_ts"], unit="s", utc=True).dt.tz_convert("Asia/Jakarta")
    out = []
    for (_, r), w in list(zip(df.iterrows(), t))[::-1][:n]:
        hasil = HASIL.get(r["result"], r["result"])
        bg, fg = WARNA_HASIL.get(hasil, ("#E6EBF4", "#22304F"))
        fokus, rencana = r["focus_s"] / 60, r["target_s"] / 60
        persen = 0 if not rencana else max(0, min(100, 100 * fokus / rencana))
        alasan = PENYEBAB.get(r["end_reason"], r["end_reason"])
        out.append(f"""<div class='fl-sesi'><div class='top'><div class='d'>{tgl_id(w)} · {w:%H:%M}</div>
<span class='fl-badge' style='background:{bg};color:{fg}'>{hasil}</span></div>
<div class='r'>Fokus {fokus:.0f} dari {rencana:g} menit · {alasan}</div>
<div class='bar'><span style='width:{persen:.0f}%;background:{fg}'></span></div></div>""")
    st.markdown("".join(out), unsafe_allow_html=True)


with tab_hist:
    df_all = hub.sessions_df()
    if df_all.empty:
        st.info("Belum ada sesi. Setelah kamu menyelesaikan sesi pertama, riwayatnya muncul di sini.")
    else:
        t = pd.to_datetime(df_all["start_ts"], unit="s", utc=True).dt.tz_convert("Asia/Jakarta")
        minggu = df_all[t >= pd.Timestamp(datetime.now(fl.WIB) - timedelta(days=7))]
        st.subheader("7 hari terakhir")
        jam_terbaik = "-"
        if len(minggu):
            jam = pd.to_datetime(minggu["start_ts"], unit="s", utc=True).dt.tz_convert("Asia/Jakarta").dt.hour
            terbaik = minggu.assign(jam=jam).groupby("jam")["result"].apply(lambda r: (r == "SELESAI").mean()).idxmax()
            jam_terbaik = f"{int(terbaik):02d}:00"
        angka([("Sesi", len(minggu)),
               ("Total fokus", f"{minggu['focus_s'].sum() / 60:.0f} <small>menit</small>"),
               ("Sesi tuntas", f"{(minggu['result'] == 'SELESAI').mean() * 100:.0f}%" if len(minggu) else "-"),
               ("Jam paling fokus", jam_terbaik)], n=4, m=2)
        # Laptop: grafik di kiri, daftar sesi di kanan. HP: tersusun ke bawah.
        kiri, kanan = st.columns([3, 2], gap="large")
        with kiri:
            with st.container(border=True):
                st.markdown("**Menit fokus per hari**")
                tampil(grafik_harian(df_all))
            with st.expander("Grafik lengkap (analitik)"):
                tampil(fl.fig_analytics(df_all))
        with kanan:
            st.markdown("**Sesi terakhir**")
            daftar_sesi(df_all, n=5)
        with st.expander("Tabel lengkap"):
            st.dataframe(tabel_ramah(df_all), width="stretch", hide_index=True)
        st.download_button("Unduh riwayat (CSV)", df_all.to_csv(index=False).encode("utf-8"),
                           file_name="sessions.csv", mime="text/csv")


# ---------------------------------------------------------------------
# Tab Saran AI
# ---------------------------------------------------------------------
with tab_ai:
    pakai_demo = st.session_state.get("pakai_demo", False)
    res = fl.hitung_rekomendasi(hub.sessions_df(), pakai_demo=pakai_demo)
    if not res["ok"]:
        st.markdown(f"""<div class="fl-advice"><div class="big">Saran akan muncul setelah beberapa sesi</div>
<div class="sub">{res['pesan']} Saran dihitung dari kebiasaanmu sendiri, jadi makin sering dipakai makin tepat.</div></div>""",
                    unsafe_allow_html=True)
    else:
        d, T = res["durasi"], res["durasi_sekarang"]
        if d > T:
            sub = f"Kamu konsisten menuntaskan sesi {T} menit. Saatnya naik sedikit."
        elif d < T:
            sub = f"Sesi {T} menit terasa berat akhir-akhir ini. Turunkan dulu, nanti naik lagi saat sudah lancar."
        else:
            sub = f"Pertahankan {T} menit sampai sebagian besar sesi tuntas."
        st.markdown(f"""<div class="fl-advice"><div class="big">Coba belajar {d} menit per sesi</div>
<div class="sub">{sub}</div></div>""", unsafe_allow_html=True)
        if st.button(f"Pakai {d} menit untuk sesi berikutnya", type="primary",
                     disabled=not (hub.conn["connected"] and not hub.mode_simulasi)):
            tell(hub.set_duration(d)[0], f"Lampu diatur ke {d} menit")
        st.markdown("**Kenapa saran ini?**")
        st.markdown("\n".join(f"- {p}" for p in res["poin"]))
        with st.expander("Cara AI menghitung (detail)"):
            st.markdown("Saran membandingkan minggu ini dengan minggu lalu, menghitung peluang kamu bertahan fokus "
                        "sampai menit tertentu (Kaplan–Meier berbobot waktu), dan mencari jam belajar terbaik "
                        "(Logistic Regression). Sesi terbaru diberi bobot lebih besar.")
            st.dataframe(res["tabel"], width="stretch")
            tampil(fl.fig_rekomendasi(res))
    st.toggle("Ikutkan sesi mode uji coba (untuk mencoba saja)", key="pakai_demo")


# ---------------------------------------------------------------------
# Tab Bantuan
# ---------------------------------------------------------------------
with tab_help:
    st.subheader("Cara pakai")
    st.markdown("""
1. **Letakkan HP di dudukan.** Lampu menyala biru redup, artinya siap.
2. **Pilih lama belajar** di Beranda (pilihan cepat, atau **Atur sendiri** untuk durasi bebas), lalu tekan **Mulai fokus**. Bisa juga dengan tombol hijau di lampu.
3. **Belajar sampai waktu habis.** Kalau kamu pergi atau mengangkat HP, lampu memberi tanda dan sesi bisa berhenti.
4. **Lihat Riwayat dan Saran AI** setiap minggu untuk tahu durasi belajar yang paling cocok.
""")
    st.subheader("Arti warna lampu")
    for warna, arti in [("#FFFFFF", "Putih: sedang fokus (makin gelap ruangan, makin terang lampunya)"),
                        ("#6FA8FF", "Biru redup: HP sudah di dudukan, siap mulai"),
                        ("#F28C28", "Kuning: kamu meninggalkan meja"),
                        ("#E5484D", "Merah berkedip: HP diangkat, atau sesi dihentikan"),
                        ("#2FBF71", "Hijau: sesi tuntas"),
                        ("#C9CED8", "Mati: HP belum di dudukan")]:
        st.markdown(f'<div class="fl-legend"><span class="fl-dot" style="background:{warna}"></span>{arti}</div>',
                    unsafe_allow_html=True)
    st.markdown("**Lampu kecil hijau (DOCK)** menyala saat HP terpasang di dudukan, dan berkedip bila HP diangkat ketika sesi berjalan.")
    st.markdown("**Lampu kecil kuning (NET)** menyala terus saat lampu tersambung ke server. Berkedip pelan berarti WiFi putus, "
                "berkedip cepat berarti WiFi ada tetapi server belum tersambung.")
    st.info("Kalau internet putus, lampu tetap bisa dipakai belajar seperti biasa. Hasil sesi disimpan di lampu dan "
            "otomatis masuk ke Riwayat setelah lampu tersambung lagi.", icon=":material/wifi_off:")

    st.subheader("Pasang di HP seperti aplikasi")
    st.markdown("""
Web ini bisa dibuka di laptop maupun HP. Di HP, tampilannya berubah seperti aplikasi dengan menu di bagian bawah.
1. Buka link web di **Chrome** (Android) atau **Safari** (iPhone).
2. Ketuk menu titik tiga lalu pilih **Tambahkan ke layar utama** atau **Instal aplikasi** (Safari: tombol Bagikan > *Add to Home Screen*).
3. Ikon **FocusLamp** muncul di layar utama dan terbuka layar penuh tanpa bilah alamat.

Catatan: link berganti setiap notebook Colab dijalankan ulang, jadi ikon perlu dipasang ulang setelah link baru dibuat.
""")

    st.subheader("Coba tanpa alat")
    st.caption("Isi web dengan data contoh untuk melihat tampilannya. Data contoh tidak tersimpan ke riwayat.")
    a, b, c = st.columns(3)
    if a.button("Contoh 3 sesi", width="stretch"):
        hub.simulate_demo(); st.rerun()
    if b.button("Contoh 2 minggu (untuk Saran AI)", width="stretch"):
        hub.simulate_history(); st.rerun()
    if c.button("Kembali ke data asli", width="stretch", disabled=not hub.mode_simulasi):
        n = hub.back_to_real(); st.toast(f"{n} sesi dimuat dari riwayat"); st.rerun()

    with st.expander("Info teknis"):
        st.markdown(f"""
- Perangkat: `{hub.device_id}`, topic `{hub.topic_base}/#`
- Server MQTT: `{hub.broker or '-'}` ({'tersambung' if hub.conn['connected'] else 'belum tersambung'})
- Pesan terakhir: {hub.conn['last_error'] or '-'}
- File riwayat: `{hub.history_path}` ({hub.history_count()} sesi tersimpan)
- Perintah yang dikirim web sama dengan aplikasi Android nanti: `set_duration`, `start`, `stop`, `set_mode`.
""")
