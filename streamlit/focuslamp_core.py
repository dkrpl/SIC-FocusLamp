"""FocusLamp core: koneksi MQTT, riwayat sesi, grafik, analitik, dan rekomendasi AI.

Dipakai oleh app.py (tampilan Streamlit). File ini tidak memakai Streamlit sama sekali,
jadi logikanya sama persis dengan notebook Colab dan bisa diuji terpisah.
"""
import json
import math
import os
import random
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# =====================================================================
# 1. Konstanta
# =====================================================================
WIB = timezone(timedelta(hours=7))
MAX_ROWS = 3000
DURATION_PRESETS = [1, 2, 3, 5, 10, 15, 20, 25, 30, 45, 60]

STATE_COLOR = {"IDLE": "#bdbdbd", "READY": "#90caf9", "FOCUS": "#66bb6a", "AWAY": "#ffca28",
               "SELESAI": "#2e7d32", "END_PAKSA": "#e53935", "DITINGGAL": "#8d6e63"}
STATE_INFO = {"IDLE": "HP belum di dock", "READY": "HP di dock, siap mulai", "FOCUS": "Sesi fokus berjalan",
              "AWAY": "Pengguna meninggalkan meja", "SELESAI": "Sesi selesai", "END_PAKSA": "Sesi dihentikan",
              "DITINGGAL": "Sesi ditinggal"}
RESULT_COLOR = {"SELESAI": "#2e7d32", "END_PAKSA": "#e53935", "DITINGGAL": "#fb8c00"}
REASON_LABEL = {"waktu_habis": "Waktu habis", "tombol_end": "Tombol END", "hp_diangkat": "HP diangkat",
                "ditinggal": "Ditinggal", "dashboard": "Stop dashboard"}
REASON_TIP = {
    "hp_diangkat": "HP paling sering membuat sesi gagal. Letakkan HP di dock sebelum mulai dan aktifkan mode Jangan Ganggu agar tidak tergoda membuka notifikasi.",
    "ditinggal": "Kamu sering meninggalkan meja terlalu lama. Siapkan minum, buku, dan alat tulis sebelum menekan START.",
    "tombol_end": "Kamu sering menghentikan sesi sendiri. Durasinya mungkin terasa terlalu panjang; naikkan durasi bertahap setelah konsisten.",
    "dashboard": "Beberapa sesi dihentikan dari dashboard. Hentikan dari luar hanya bila memang perlu.",
}
SESSION_COLS = ["device", "session_id", "start_ts", "end_ts", "target_s", "focus_s", "completion_ratio", "result",
                "end_reason", "away_count", "away_total_s", "first_away_min", "dock_lift_count", "avg_light_raw",
                "baseline_cm", "demo"]


# =====================================================================
# 2. Hub: koneksi MQTT + penyimpanan data
# =====================================================================
class FocusLampHub:
    """Satu objek untuk semua data FocusLamp: menerima pesan MQTT, menyimpan riwayat, dan mengirim perintah."""

    def __init__(self, broker, port=8883, username="", password="", device_id="fl-01",
                 history_path="riwayat_sesi.csv", use_tls=None, connect=True):
        """Simpan konfigurasi, siapkan penyimpanan data, muat riwayat dari file, lalu sambungkan ke broker MQTT."""
        self.broker, self.port = broker, int(port)
        self.username, self.password = username, password
        self.device_id = device_id
        self.topic_base = f"sic8/focuslamp/{device_id}"
        self.use_tls = (self.port != 1883) if use_tls is None else use_tls
        self.history_path = history_path
        self.lock = threading.Lock()
        self.store = {"status": None, "telemetry": [], "event": [], "session": []}
        self.conn = {"connected": False, "last_error": None, "since": None}
        self.persist = True            # False saat mode simulasi agar data contoh tidak masuk riwayat
        self.mode_simulasi = None
        self.client = None
        os.makedirs(os.path.dirname(history_path) or ".", exist_ok=True)
        self.load_history()
        if connect:
            self.connect()

    # ---------- koneksi ----------
    def configured(self):
        """True bila BROKER dan USERNAME sudah diisi (bukan contoh 'ISI_...')."""
        return bool(self.broker) and not self.broker.startswith("ISI_") and bool(self.username)

    def connect(self):
        """Buat klien MQTT (TLS untuk port 8883) dan mulai menyambung di background; menyambung ulang otomatis bila putus."""
        if not self.configured():
            self.conn["last_error"] = "BROKER / USERNAME belum diisi"
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id=f"streamlit-{self.device_id}-{int(time.time())}")
        c.username_pw_set(self.username, self.password)
        if self.use_tls:
            c.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
        c.on_connect, c.on_disconnect, c.on_message = self._on_connect, self._on_disconnect, self._on_message
        c.reconnect_delay_set(min_delay=1, max_delay=30)
        c.connect_async(self.broker, self.port, keepalive=60)   # tidak memblokir; loop menyambung ulang otomatis
        c.loop_start()
        self.client = c

    def disconnect(self):
        """Hentikan loop MQTT dan putuskan koneksi."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.conn["connected"] = False

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """Dipanggil paho saat koneksi berhasil/gagal; bila berhasil, subscribe ke semua topic perangkat (sic8/focuslamp/<id>/#)."""
        self.conn["connected"] = not reason_code.is_failure
        if reason_code.is_failure:
            self.conn["last_error"] = f"Gagal tersambung: {reason_code} (cek BROKER, USERNAME, PASSWORD)"
        else:
            self.conn["last_error"] = None
            self.conn["since"] = datetime.now(WIB)
            client.subscribe(f"{self.topic_base}/#", qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        """Dipanggil paho saat koneksi terputus; tandai status MQTT terputus."""
        self.conn["connected"] = False
        if reason_code.is_failure:
            self.conn["last_error"] = f"Terputus: {reason_code}"

    def _on_message(self, client, userdata, msg):
        """Dipanggil paho untuk setiap pesan masuk; teruskan ke handle_message()."""
        self.handle_message(msg.topic, msg.payload, live=True)

    # ---------- data masuk ----------
    def handle_message(self, topic, payload, recv_time=None, live=False):
        """Simpan satu pesan MQTT ke memori sesuai jenis topic (status/telemetry/event/session). Rekap sesi juga ditulis ke file riwayat; bila jam ESP32 belum sinkron, waktu mulai/selesai diisi dari waktu terima. Saat mode data contoh, pesan langsung dari alat tidak ditampilkan, tetapi rekap sesinya tetap disimpan."""
        kind = topic.rsplit("/", 1)[-1]
        try:
            data = json.loads(payload)
        except Exception:
            self.conn["last_error"] = f"payload bukan JSON di {topic}"
            return
        data["_recv"] = recv_time or datetime.now(WIB)
        if kind == "session":
            if not data.get("start_ts") or data["start_ts"] < 1_000_000_000:   # jam ESP32 belum tersinkron NTP
                end = int(data["_recv"].timestamp())
                data["end_ts"] = end
                data["start_ts"] = end - int(data.get("focus_s") or 0) - int(data.get("away_total_s") or 0)
            if self.persist or live:      # sesi asli tetap disimpan walaupun sedang melihat data contoh
                try:
                    self.save_session(data)
                except Exception as e:
                    self.conn["last_error"] = f"gagal menyimpan riwayat: {e}"
        if live and self.mode_simulasi:   # saat mode contoh, data asli tidak dicampur ke tampilan
            return
        with self.lock:
            if kind == "status":
                self.store["status"] = data
            elif kind in ("telemetry", "event", "session"):
                self.store[kind].append(data)
                if len(self.store[kind]) > MAX_ROWS:
                    self.store[kind] = self.store[kind][-MAX_ROWS:]

    # ---------- riwayat permanen ----------
    def save_session(self, data):
        """Tambahkan satu rekap sesi sebagai satu baris baru di file riwayat CSV."""
        row = {c: data.get(c) for c in SESSION_COLS}
        new_file = not os.path.exists(self.history_path)
        pd.DataFrame([row], columns=SESSION_COLS).to_csv(self.history_path, mode="a", header=new_file, index=False)

    def load_history(self):
        """Baca file riwayat CSV ke memori (tanpa duplikat session_id); kembalikan jumlah sesi yang dimuat."""
        if not os.path.exists(self.history_path):
            return 0
        h = pd.read_csv(self.history_path).drop_duplicates("session_id", keep="last")
        rows = []
        for r in h.to_dict("records"):
            r = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}
            r["demo"] = str(r.get("demo")).lower() in ("true", "1")
            r["_recv"] = datetime.fromtimestamp(int(r["end_ts"]), WIB) if r.get("end_ts") else datetime.now(WIB)
            rows.append(r)
        with self.lock:
            known = {r["session_id"] for r in rows}
            self.store["session"] = rows + [s for s in self.store["session"] if s.get("session_id") not in known]
        return len(rows)

    def history_count(self):
        """Jumlah sesi unik yang sudah tersimpan di file riwayat."""
        if not os.path.exists(self.history_path):
            return 0
        try:
            return int(pd.read_csv(self.history_path)["session_id"].nunique())
        except Exception:
            return 0

    # ---------- membaca data ----------
    def frames(self):
        """Salin data di memori menjadi DataFrame telemetry, event, session, dan status terakhir (aman dipanggil saat pesan baru masuk)."""
        with self.lock:
            tel = pd.DataFrame(self.store["telemetry"])
            ev = pd.DataFrame(self.store["event"])
            ses = pd.DataFrame(self.store["session"])
            st = dict(self.store["status"]) if self.store["status"] else None
        return tel, ev, ses, st

    def sessions_df(self):
        """Dataset satu baris per sesi, ditambah kolom waktu mulai (WIB), jam mulai, dan nama hari."""
        _, _, ses, _ = self.frames()
        if ses.empty:
            return ses
        ses = ses.drop(columns=["_recv"], errors="ignore").drop_duplicates("session_id", keep="last")
        start = pd.to_datetime(ses["start_ts"].where(ses["start_ts"] > 0), unit="s", utc=True).dt.tz_convert("Asia/Jakarta")
        ses["start_time_wib"] = start.dt.strftime("%Y-%m-%d %H:%M:%S")
        ses["start_hour"] = start.dt.hour
        ses["day_of_week"] = start.dt.day_name()
        return ses.reset_index(drop=True)

    def status_now(self):
        """Ringkasan kondisi alat saat ini untuk kartu status: state, sisa waktu, durasi, hadir, dock, cahaya, mode, waktu update."""
        tel, _, _, st = self.frames()
        last = tel.iloc[-1].to_dict() if not tel.empty else {}
        state = (st or {}).get("state") or last.get("state") or "-"
        status_lebih_baru = st is not None and (not last or st["_recv"] >= last["_recv"])
        remaining = (st.get("remaining_s") if status_lebih_baru else last.get("remaining_s"))
        demo = (st or {}).get("demo", last.get("demo"))
        return {
            "state": state,
            "remaining_s": remaining,
            "target_s": (st or {}).get("target_s"),
            "present": last.get("present"),
            "dock": last.get("dock"),
            "light_pct": last.get("light_pct"),
            "demo": demo,
            "paused": last.get("paused"),
            "updated": max([t for t in (last.get("_recv"), (st or {}).get("_recv")) if t is not None], default=None),
        }

    def countdown(self):
        """Data hitung mundur untuk browser: sisa detik terakhir dari alat, kapan diterima (epoch detik),
        kecepatan timer alat dibanding jam dinding, dan apakah timer sedang berjalan.

        Kecepatan diukur dari telemetry terakhir: di Wokwi simulasi sering berjalan lebih lambat dari waktu
        nyata (misalnya 70%), sehingga 1 detik di alat bisa memakan lebih dari 1 detik jam dinding."""
        tel, _, _, st = self.frames()
        s = self.status_now()
        rem, at = s["remaining_s"], time.time()
        rate = 1.0
        if not tel.empty:
            last = tel.iloc[-1]
            rem, at = last.get("remaining_s"), pd.Timestamp(last["_recv"]).timestamp()
            if st is not None and pd.Timestamp(st["_recv"]).timestamp() > at:
                rem, at = st.get("remaining_s"), pd.Timestamp(st["_recv"]).timestamp()
            t = tel.tail(15)
            t = t[t["state"].isin(["FOCUS", "AWAY"])]
            if len(t) >= 2:
                waktu = t["_recv"].map(lambda x: pd.Timestamp(x).timestamp()).to_numpy()
                sisa = pd.to_numeric(t["remaining_s"], errors="coerce").to_numpy(dtype=float)
                d_sisa, d_waktu = -np.diff(sisa), np.diff(waktu)
                ok = (d_sisa > 0) & (d_waktu > 0.3) & (d_sisa < 30)      # hanya pasangan saat timer berjalan
                if ok.sum() >= 1 and d_waktu[ok].sum() > 1.5:
                    rate = float(np.clip(d_sisa[ok].sum() / d_waktu[ok].sum(), 0.2, 1.5))
        segar = (time.time() - at) < 15
        berjalan = (s["state"] in ("FOCUS", "AWAY") and not s["paused"] and s["dock"] is not False
                    and segar and rem is not None and rem > 0)
        return {"rem": float(rem) if rem is not None else None, "at": at, "rate": round(rate, 2),
                "running": bool(berjalan), "target": s["target_s"], "state": s["state"]}

    # ---------- perintah ke ESP32 ----------
    def send_cmd(self, payload):
        """Kirim satu perintah JSON ke topic cmd; ditolak bila MQTT terputus atau mode simulasi aktif. Mengembalikan (berhasil, pesan)."""
        if self.mode_simulasi:
            return False, "Mode simulasi aktif: perintah tidak dikirim. Klik 'Kembali ke data asli' dulu."
        if not self.client or not self.conn["connected"]:
            return False, "MQTT belum tersambung, perintah tidak terkirim."
        info = self.client.publish(f"{self.topic_base}/cmd", json.dumps(payload), qos=1)
        if info.rc == 0:
            return True, f"Terkirim: {json.dumps(payload)}"
        return False, f"Gagal kirim (rc={info.rc})"

    def set_duration(self, minutes):
        """Perintah ke ESP32: atur durasi sesi berikutnya (menit)."""
        return self.send_cmd({"action": "set_duration", "duration_min": minutes})

    def start_session(self, minutes=None):
        """Perintah ke ESP32: mulai sesi; durasi opsional (menit). Hanya berhasil bila alat READY."""
        return self.send_cmd({"action": "start", **({"duration_min": minutes} if minutes else {})})

    def stop_session(self):
        """Perintah ke ESP32: hentikan sesi (tercatat END_PAKSA dengan alasan dashboard)."""
        return self.send_cmd({"action": "stop"})

    def set_demo(self, on=True):
        """Perintah ke ESP32: ganti mode waktu demo (True) atau normal (False)."""
        return self.send_cmd({"action": "set_mode", "demo": bool(on)})

    # ---------- mode simulasi ----------
    def clear_data(self):
        """Kosongkan data di memori (file riwayat tidak terhapus)."""
        with self.lock:
            self.store.update({"status": None, "telemetry": [], "event": [], "session": []})

    def back_to_real(self):
        """Keluar dari mode simulasi: kosongkan data contoh lalu muat ulang riwayat asli dari file."""
        self.mode_simulasi = None
        self.persist = True
        self.clear_data()
        return self.load_history()

    def simulate_demo(self):
        """Tiga sesi contoh mode demo (SELESAI, END_PAKSA, DITINGGAL) untuk mencoba tampilan."""
        self.clear_data()
        self.persist, self.mode_simulasi = False, "demo"
        base = self.topic_base
        t = datetime.now(WIB) - timedelta(minutes=12)
        for k, (result, aways) in enumerate([("SELESAI", 0), ("END_PAKSA", 1), ("DITINGGAL", 1)]):
            sid = f"{self.device_id}-sim{k + 1}"
            self.handle_message(f"{base}/event", json.dumps({"type": "SESSION_START", "session_id": sid,
                                                              "distance_cm": 50, "elapsed_s": 0}), t)
            focus, light = 0, random.randint(30, 80)
            for step in range(60):
                state, dist = "FOCUS", 48 + random.random() * 6
                if result != "SELESAI" and 20 <= step < (60 if result == "DITINGGAL" else 26):
                    state, dist = "AWAY", 150 + random.random() * 10
                if state == "FOCUS":
                    focus += 2
                self.handle_message(f"{base}/telemetry", json.dumps({
                    "state": state, "distance_cm": round(dist, 1), "threshold_cm": 90, "present": state == "FOCUS",
                    "dock": True, "light_raw": 2000, "light_pct": light, "brightness": int(60 + (100 - light) * 1.4),
                    "elapsed_s": focus, "remaining_s": max(0, 120 - focus), "paused": False, "demo": True}), t)
                if step == 20 and aways:
                    self.handle_message(f"{base}/event", json.dumps({"type": "AWAY_START", "session_id": sid,
                                                                      "distance_cm": round(dist, 1), "elapsed_s": focus}), t)
                t += timedelta(seconds=2)
                if result == "END_PAKSA" and step == 40:
                    break
            self.handle_message(f"{base}/event", json.dumps({"type": "SESSION_END", "session_id": sid,
                                                              "distance_cm": 50, "elapsed_s": focus}), t)
            self.handle_message(f"{base}/session", json.dumps({
                "device": self.device_id, "session_id": sid, "start_ts": int(t.timestamp()) - 120,
                "end_ts": int(t.timestamp()), "target_s": 120, "focus_s": min(focus, 120),
                "completion_ratio": round(min(focus, 120) / 120, 3), "result": result,
                "end_reason": {"SELESAI": "waktu_habis", "END_PAKSA": "tombol_end", "DITINGGAL": "ditinggal"}[result],
                "away_count": aways, "away_total_s": 20 if aways else 0, "first_away_min": 0.67 if aways else None,
                "dock_lift_count": 0, "avg_light_raw": 2000, "baseline_cm": 50.0, "demo": True}), t)
            t += timedelta(seconds=10)
        self.handle_message(f"{base}/status", json.dumps({"state": "READY", "remaining_s": 120, "target_s": 120,
                                                          "demo": True}), t)

    def simulate_history(self, seed=7):
        """Data contoh 2 minggu: minggu 1 timer 20 menit selalu selesai, minggu 2 sering gagal."""
        rng = random.Random(seed)
        self.clear_data()
        self.persist, self.mode_simulasi = False, "2 minggu"
        hari0 = (datetime.now(WIB) - timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0)
        minggu2 = ["SELESAI", "hp_diangkat", "ditinggal", "hp_diangkat", "SELESAI", "tombol_end", "hp_diangkat"]
        for hari in range(14):
            target = 20 * 60
            if hari < 7:
                hasil, jam, alasan = "SELESAI", rng.choice([19, 19, 20]), "waktu_habis"
            else:
                alasan = minggu2[hari - 7]
                hasil = {"SELESAI": "SELESAI", "ditinggal": "DITINGGAL"}.get(alasan, "END_PAKSA")
                alasan = "waktu_habis" if alasan == "SELESAI" else alasan
                jam = rng.choice([19, 20]) if hasil == "SELESAI" else rng.choice([21, 21, 22])
            start = hari0 + timedelta(days=hari, hours=jam, minutes=rng.randint(0, 40))
            fokus = target if hasil == "SELESAI" else rng.randint(11 * 60, 16 * 60)
            pergi = rng.randint(0, 1) if hari < 7 else rng.randint(1, 3)
            away_s = pergi * rng.randint(20, 60)
            end = start + timedelta(seconds=fokus + away_s)
            self.handle_message(f"{self.topic_base}/session", json.dumps({
                "device": self.device_id, "session_id": f"{self.device_id}-hist{hari + 1:02d}",
                "start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()), "target_s": target,
                "focus_s": fokus, "completion_ratio": round(fokus / target, 3), "result": hasil, "end_reason": alasan,
                "away_count": pergi, "away_total_s": away_s,
                "first_away_min": round(rng.uniform(9, 14), 1) if pergi else None,
                "dock_lift_count": int(alasan == "hp_diangkat") + (rng.randint(0, 1) if hari >= 7 else 0),
                "avg_light_raw": rng.randint(1500, 2600), "baseline_cm": 50.0, "demo": False}), end)


# =====================================================================
# 3. Grafik monitoring
# =====================================================================
def fmt_mmss(sec):
    """Ubah detik menjadi teks mm:ss; '-' bila nilai kosong."""
    if sec is None or (isinstance(sec, float) and math.isnan(sec)):
        return "-"
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _empty_fig(text, height=300):
    """Grafik kosong berisi pesan, dipakai saat data belum ada."""
    fig = go.Figure()
    fig.add_annotation(text=text, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                       font=dict(size=14, color="#757575"))
    fig.update_layout(height=height, template="plotly_white", xaxis=dict(visible=False), yaxis=dict(visible=False),
                      margin=dict(l=20, r=20, t=40, b=20))
    return fig


def fig_distance(tel):
    """Grafik jarak pengguna terhadap waktu, garis ambang pergi, dan warna latar sesuai state."""
    if tel.empty:
        return _empty_fig("Belum ada telemetry. Jalankan simulasi Wokwi.", 340)
    x = tel["_recv"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=tel["distance_cm"], name="Jarak", mode="lines", line=dict(color="#1f4e79", width=2)))
    if "threshold_cm" in tel:
        fig.add_trace(go.Scatter(x=x, y=tel["threshold_cm"], name="Ambang pergi", mode="lines",
                                 line=dict(color="#e53935", width=1.5, dash="dash")))
    seg_start, seg_state = x.iloc[0], tel["state"].iloc[0]
    for i in range(1, len(tel) + 1):
        if i == len(tel) or tel["state"].iloc[i] != seg_state:
            end = x.iloc[i] if i < len(tel) else x.iloc[-1]
            fig.add_vrect(x0=seg_start, x1=end, fillcolor=STATE_COLOR.get(seg_state, "#eeeeee"), opacity=0.18, line_width=0)
            if i < len(tel):
                seg_start, seg_state = x.iloc[i], tel["state"].iloc[i]
    fig.update_layout(title="Jarak pengguna dan ambang pergi (cm)", height=340, template="plotly_white",
                      legend=dict(orientation="h", y=-0.2), margin=dict(l=50, r=20, t=50, b=40))
    fig.update_yaxes(rangemode="tozero")
    return fig


def fig_light(tel):
    """Grafik cahaya ruangan (%) dan kecerahan lampu (%) terhadap waktu."""
    if tel.empty:
        return _empty_fig("Belum ada data cahaya.")
    x = tel["_recv"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=tel["light_pct"], name="Cahaya ruangan", mode="lines", line=dict(color="#f9a825", width=2)))
    fig.add_trace(go.Scatter(x=x, y=tel["brightness"] / 255 * 100, name="Kecerahan lampu", mode="lines",
                             line=dict(color="#6a1b9a", width=2)))
    fig.update_layout(title="Cahaya ruangan & kecerahan lampu (%)", height=300, template="plotly_white",
                      legend=dict(orientation="h", y=-0.25), margin=dict(l=50, r=20, t=50, b=40))
    fig.update_yaxes(range=[0, 105])
    return fig


def fig_history(sdf):
    """Grafik batang waktu fokus tiap sesi (warna sesuai hasil) dengan penanda target durasi."""
    if sdf.empty:
        return _empty_fig("Belum ada sesi yang selesai.")
    labels = [f"#{i + 1}" for i in range(len(sdf))]
    fig = go.Figure()
    for res, color in RESULT_COLOR.items():
        m = sdf["result"] == res
        if m.any():
            fig.add_trace(go.Bar(x=[l for l, k in zip(labels, m) if k], y=sdf.loc[m, "focus_s"], name=res, marker_color=color))
    fig.add_trace(go.Scatter(x=labels, y=sdf["target_s"], name="Target", mode="markers",
                             marker=dict(symbol="line-ew-open", size=22, color="#424242", line=dict(width=2))))
    fig.update_layout(title="Riwayat sesi: waktu fokus vs target (detik)", height=300, template="plotly_white",
                      barmode="overlay", legend=dict(orientation="h", y=-0.25), margin=dict(l=50, r=20, t=50, b=40))
    fig.update_xaxes(categoryorder="array", categoryarray=labels)
    return fig


def events_table(ev, n=10):
    """Tabel n event terbaru (terbaru di atas) untuk tab Monitoring."""
    if ev.empty:
        return pd.DataFrame(columns=["Waktu", "Event", "Jarak (cm)", "Detik fokus"])
    tail = ev.tail(n).iloc[::-1]
    return pd.DataFrame({"Waktu": tail["_recv"].dt.strftime("%H:%M:%S"), "Event": tail["type"],
                         "Jarak (cm)": tail.get("distance_cm", pd.Series(dtype=float)).round(1),
                         "Detik fokus": tail.get("elapsed_s")}).reset_index(drop=True)


# =====================================================================
# 4. Analitik semua sesi
# =====================================================================
def analytics_summary(df):
    """Angka ringkasan semua sesi: jumlah, tingkat selesai, ketercapaian target, total menit fokus, rata-rata pergi."""
    n = len(df)
    return {"sesi": n,
            "tingkat_selesai": (df["result"] == "SELESAI").mean() * 100 if n else 0,
            "ketercapaian": df["completion_ratio"].mean() * 100 if n else 0,
            "fokus_menit": df["focus_s"].sum() / 60 if n else 0,
            "pergi_per_sesi": df["away_count"].mean() if n else 0}


def fig_analytics(df):
    """Empat grafik analitik: distribusi hasil, penyebab sesi berakhir, tren waktu fokus, tingkat selesai per jam mulai."""
    if df.empty:
        return None
    n = len(df)
    fig = make_subplots(rows=2, cols=2, vertical_spacing=0.16, horizontal_spacing=0.16,
                        subplot_titles=("Distribusi hasil sesi", "Penyebab sesi berakhir",
                                        "Tren waktu fokus per sesi (menit)", "Tingkat selesai per jam mulai (%)"))
    counts = df["result"].value_counts()
    fig.add_trace(go.Bar(x=counts.index, y=counts.values, marker_color=[RESULT_COLOR.get(r, "#999") for r in counts.index],
                         text=counts.values, textposition="outside", showlegend=False), row=1, col=1)
    reasons = df["end_reason"].map(REASON_LABEL).fillna(df["end_reason"]).value_counts().sort_values()
    fig.add_trace(go.Bar(x=reasons.values, y=reasons.index, orientation="h", marker_color="#4c78a8",
                         text=reasons.values, textposition="outside", showlegend=False), row=1, col=2)
    labels = [f"#{i + 1}" for i in range(n)]
    fig.add_trace(go.Scatter(x=labels, y=df["focus_s"] / 60, mode="lines+markers", name="Waktu fokus",
                             line=dict(color="#2e7d32", width=2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=labels, y=df["target_s"] / 60, mode="lines", name="Target",
                             line=dict(color="#757575", width=1.5, dash="dash")), row=2, col=1)
    if df["start_hour"].notna().any():
        by_hour = df.dropna(subset=["start_hour"]).groupby("start_hour")["result"].apply(lambda r: (r == "SELESAI").mean() * 100)
        fig.add_trace(go.Bar(x=[f"{int(h):02d}:00" for h in by_hour.index], y=by_hour.values, marker_color="#f9a825",
                             showlegend=False, text=[f"{v:.0f}%" for v in by_hour.values], textposition="outside"), row=2, col=2)
        fig.update_yaxes(range=[0, 115], row=2, col=2)
    fig.update_yaxes(range=[0, counts.max() * 1.25], row=1, col=1)
    fig.update_xaxes(range=[0, reasons.max() * 1.25], row=1, col=2)
    fig.update_yaxes(rangemode="tozero", row=2, col=1)
    fig.update_layout(height=760, template="plotly_white", legend=dict(orientation="h", y=-0.08),
                      margin=dict(l=60, r=40, t=50, b=40))
    return fig


# =====================================================================
# 5. Rekomendasi AI mingguan
# =====================================================================
AMBANG_BERTAHAN = 0.60     # durasi aman = peluang masih fokus >= 60%
PARUH_HARI = 7             # sesi 7 hari lalu berbobot setengah dari sesi hari ini
DURASI_MAX = 60


def _is_demo(s):
    """True untuk baris yang berasal dari mode demo (kolom demo bisa bool atau teks)."""
    return s.astype(str).str.lower().isin(["true", "1"])


def _bulat(m, step):
    """Bulatkan menit ke kelipatan step terdekat (minimal satu step)."""
    return int(max(step, step * round(m / step)))


def _fmt_jam(j):
    """Ubah jam desimal (misal 19.5) menjadi teks HH:MM."""
    return f"{int(j):02d}:{int(round((j % 1) * 60)) % 60:02d}"


def _prep(df):
    """Tambahkan kolom analisis: minggu ke-, durasi & fokus (menit), selesai/gagal, jam mulai, cahaya, dan bobot (sesi lama berbobot lebih kecil)."""
    d = df.copy()
    d["start"] = pd.to_datetime(d["start_ts"], unit="s", utc=True).dt.tz_convert("Asia/Jakarta")
    hari0 = d["start"].dt.normalize().min()
    d["minggu"] = ((d["start"].dt.normalize() - hari0).dt.days // 7 + 1).astype(int)
    d["target_min"] = d["target_s"] / 60
    d["fokus_min"] = d["focus_s"] / 60
    d["selesai"] = d["result"] == "SELESAI"
    d["gagal"] = (~d["selesai"]) & (d["end_reason"] != "dashboard")   # stop dari dashboard bukan kegagalan fokus
    d["jam"] = d["start"].dt.hour + d["start"].dt.minute / 60
    d["cahaya"] = pd.to_numeric(d["avg_light_raw"], errors="coerce")
    d["cahaya"] = d["cahaya"].fillna(d["cahaya"].median() if d["cahaya"].notna().any() else 2000)
    umur_hari = (d["start"].max() - d["start"]).dt.total_seconds() / 86400
    d["bobot"] = 0.5 ** (umur_hari / PARUH_HARI)
    return d.sort_values("start").reset_index(drop=True)


def ringkasan_mingguan(d):
    """Tabel ringkasan per minggu: jumlah sesi, durasi, tingkat selesai, ketercapaian, rata-rata fokus, pergi, HP diangkat, jam mulai."""
    return d.groupby("minggu").agg(sesi=("session_id", "count"), durasi_menit=("target_min", "median"),
                                   tingkat_selesai=("selesai", "mean"), ketercapaian=("completion_ratio", "mean"),
                                   fokus_menit=("fokus_min", "mean"), pergi_per_sesi=("away_count", "mean"),
                                   hp_diangkat=("dock_lift_count", "sum"), jam_mulai=("jam", "mean"))


def kurva_bertahan(d):
    """Kaplan-Meier berbobot: peluang masih fokus sampai menit ke-t.
    Sesi SELESAI dianggap tersensor di durasinya (tidak diketahui apakah sanggup lebih lama)."""
    t = np.where(d["gagal"], d["fokus_min"], d["target_min"]).astype(float)
    e = d["gagal"].to_numpy()
    w = d["bobot"].to_numpy()
    grid = np.round(np.arange(0, DURASI_MAX + 0.25, 0.25), 2)
    S = np.ones_like(grid)
    s = 1.0
    for ti in np.unique(t[e]):
        n_risk = w[t >= ti].sum()
        n_fail = w[(t == ti) & e].sum()
        if n_risk > 0:
            s *= 1 - n_fail / n_risk
        S[grid >= ti] = s
    return grid, S, float(t.max())


def peluang_di(grid, S, menit):
    """Ambil nilai kurva peluang bertahan fokus pada menit tertentu."""
    return float(S[min(np.searchsorted(grid, menit, side="right") - 1, len(S) - 1)])


def model_jam(d, durasi):
    """Logistic Regression: peluang sesi selesai berdasarkan jam mulai, durasi, dan cahaya."""
    y = d["selesai"].astype(int)
    if len(d) < 12 or y.nunique() < 2 or d["start"].dt.hour.nunique() < 2:
        return None

    def fitur(jam, durasi_min, cahaya):
        """Susun fitur model: jam mulai (sin/cos agar 23:00 dekat dengan 00:00), durasi, dan cahaya."""
        jam = np.asarray(jam, dtype=float)
        return pd.DataFrame({"jam_sin": np.sin(2 * np.pi * jam / 24), "jam_cos": np.cos(2 * np.pi * jam / 24),
                             "durasi": np.broadcast_to(durasi_min, jam.shape).astype(float),
                             "cahaya": np.broadcast_to(cahaya, jam.shape).astype(float)})

    X = fitur(d["jam"], d["target_min"].to_numpy(), d["cahaya"].to_numpy())
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    k = int(min(5, y.value_counts().min()))
    akurasi = (cross_val_score(model, X, y, cv=StratifiedKFold(k, shuffle=True, random_state=0)).mean()
               if k >= 2 else float("nan"))
    model.fit(X, y, logisticregression__sample_weight=d["bobot"].to_numpy())
    jam_kandidat = np.array(sorted(d["start"].dt.hour.unique()), dtype=float)
    peluang = model.predict_proba(fitur(jam_kandidat, durasi, d["cahaya"].median()))[:, 1]
    return {"jam": jam_kandidat.astype(int), "peluang": peluang, "akurasi": akurasi, "n": len(d)}


def hitung_rekomendasi(df, pakai_demo=False):
    """Hasilkan rekomendasi durasi timer + alasan. Mengembalikan dict; kunci 'ok' False bila data belum cukup."""
    if df.empty:
        return {"ok": False, "pesan": "Belum ada sesi. Jalankan beberapa sesi di Wokwi, atau coba simulasi 2 minggu di sidebar."}
    if not pakai_demo:
        if _is_demo(df["demo"]).all():
            return {"ok": False, "pesan": "Semua sesi masih mode demo. Pakai mode normal di tab Kontrol untuk data asli, "
                                          "atau nyalakan 'Ikutkan sesi mode demo' untuk uji coba."}
        df = df[~_is_demo(df["demo"])]
    if len(df) < 3:
        return {"ok": False, "pesan": f"Baru {len(df)} sesi; butuh minimal 3 sesi."}

    d = _prep(df)
    mingguan = ringkasan_mingguan(d)
    step = 5 if d["target_min"].median() >= 10 else 1           # data demo memakai langkah 1 menit
    W = int(d["minggu"].max())
    akhir = d[d["minggu"] == W]
    if len(akhir) < 3:                                          # minggu baru berjalan: pakai 7 sesi terakhir
        akhir = d.tail(7)
    T = _bulat(akhir["target_min"].mode().iloc[0], step)
    di_T = akhir[(akhir["target_min"] - T).abs() < step / 2]
    rate = di_T["selesai"].mean()
    grid, S, horizon = kurva_bertahan(d)
    durasi_aman = max([m for m in range(step, DURASI_MAX + 1, step)
                       if m <= horizon + 1e-9 and peluang_di(grid, S, m) >= AMBANG_BERTAHAN] or [step])

    poin = []
    if rate >= 0.8:
        durasi = min(T + step, DURASI_MAX)
        poin.append(f"**{rate:.0%}** sesi {T} menit di minggu ke-{W} selesai penuh. Kamu sudah konsisten, coba naikkan ke **{durasi} menit**.")
    elif rate >= 0.5:
        durasi = T
        poin.append(f"Baru **{rate:.0%}** sesi {T} menit yang selesai di minggu ke-{W}. Pertahankan **{T} menit** sampai minimal 80% sesi selesai.")
    else:
        berhenti = di_T.loc[di_T["gagal"], "fokus_min"].mean()
        durasi = max(step, min(durasi_aman, T - step))
        teks_berhenti = f" Rata-rata kamu berhenti di menit ke-**{berhenti:.0f}**." if pd.notna(berhenti) else ""
        poin.append(f"Hanya **{rate:.0%}** sesi {T} menit yang selesai di minggu ke-{W}.{teks_berhenti} "
                    f"Model memperkirakan peluang kamu bertahan fokus sampai menit ke-{durasi} sebesar "
                    f"**{peluang_di(grid, S, durasi):.0%}**, jadi turunkan dulu ke **{durasi} menit**, lalu naikkan lagi saat sudah konsisten.")

    lalu = d[(d["minggu"] == W - 1) & ((d["target_min"] - T).abs() < step / 2)]
    if len(lalu) >= 3 and W > 1:
        rate_lalu = lalu["selesai"].mean()
        arah = "turun" if rate < rate_lalu - 0.1 else "naik" if rate > rate_lalu + 0.1 else "stabil"
        teks = f"Dibanding minggu ke-{W - 1}, tingkat selesai durasi {T} menit **{arah}** dari {rate_lalu:.0%} menjadi {rate:.0%}."
        if arah == "turun":
            sebab = []
            jl, jk = lalu["jam"].mean(), di_T["jam"].mean()
            if jk - jl >= 1:
                sebab.append(f"rata-rata mulai belajar lebih malam ({_fmt_jam(jl)} → {_fmt_jam(jk)})")
            hl, hk = lalu["dock_lift_count"].mean(), di_T["dock_lift_count"].mean()
            if hk - hl >= 0.3:
                sebab.append(f"HP lebih sering diangkat ({hl:.1f} → {hk:.1f} kali per sesi)")
            pl, pk = lalu["away_count"].mean(), di_T["away_count"].mean()
            if pk - pl >= 0.5:
                sebab.append(f"lebih sering meninggalkan meja ({pl:.1f} → {pk:.1f} kali per sesi)")
            if sebab:
                teks += " Perubahan yang terlihat: " + "; ".join(sebab) + "."
        poin.append(teks)

    mj = model_jam(d, durasi)
    if mj is not None:
        i = int(np.argmax(mj["peluang"]))
        poin.append(f"Jam mulai terbaik menurut model: sekitar **{mj['jam'][i]:02d}:00** (peluang selesai "
                    f"{mj['peluang'][i]:.0%} untuk {durasi} menit; akurasi validasi silang {mj['akurasi']:.0%} dari {mj['n']} sesi).")
    else:
        per_jam = d.groupby(d["start"].dt.hour)["selesai"].agg(["mean", "count"])
        per_jam = per_jam[per_jam["count"] >= 2]
        if len(per_jam) >= 2:
            h = int(per_jam["mean"].idxmax())
            poin.append(f"Tingkat selesai tertinggi saat mulai jam **{h:02d}:00** ({per_jam.loc[h, 'mean']:.0%}). "
                        "Model klasifikasi aktif setelah ada ≥ 12 sesi.")

    gagal_akhir = akhir[akhir["gagal"]]
    if len(gagal_akhir) >= 2:
        utama = gagal_akhir["end_reason"].value_counts()
        if utama.index[0] in REASON_TIP:
            poin.append(f"Penyebab utama sesi gagal minggu ini: **{REASON_LABEL.get(utama.index[0], utama.index[0])}** "
                        f"({int(utama.iloc[0])} dari {len(gagal_akhir)} sesi). {REASON_TIP[utama.index[0]]}")
    pertama_pergi = pd.to_numeric(akhir["first_away_min"], errors="coerce").dropna()
    if len(pertama_pergi) >= 2 and pertama_pergi.median() < T:
        m = pertama_pergi.median()
        poin.append(f"Biasanya kamu mulai meninggalkan meja di menit ke-**{m:.0f}**. Siapkan semua kebutuhan sebelum mulai; "
                    f"kalau tetap sulit, pakai pola {_bulat(m, step)} menit fokus lalu jeda 5 menit.")

    tabel = mingguan.copy()
    tabel["tingkat_selesai"] = (tabel["tingkat_selesai"] * 100).round(0).astype(int).astype(str) + "%"
    tabel["ketercapaian"] = (tabel["ketercapaian"] * 100).round(0).astype(int).astype(str) + "%"
    tabel["jam_mulai"] = tabel["jam_mulai"].map(_fmt_jam)
    tabel = tabel.round(1).rename_axis("minggu ke-")

    return {"ok": True, "durasi": durasi, "durasi_sekarang": T, "minggu": W, "tingkat_selesai": rate,
            "durasi_aman": durasi_aman, "poin": poin, "tabel": tabel, "n": len(d),
            "_plot": {"mingguan": mingguan, "grid": grid, "S": S, "horizon": horizon, "mj": mj, "d": d}}


def fig_rekomendasi(res):
    """Empat grafik rekomendasi: tingkat selesai per minggu, fokus vs durasi, kurva peluang bertahan, peluang selesai per jam."""
    p = res["_plot"]
    mingguan, grid, S, horizon, mj, d = p["mingguan"], p["grid"], p["S"], p["horizon"], p["mj"], p["d"]
    durasi = res["durasi"]
    fig = make_subplots(rows=2, cols=2, vertical_spacing=0.18, horizontal_spacing=0.12,
                        subplot_titles=("Tingkat selesai per minggu (%)", "Rata-rata waktu fokus vs durasi (menit)",
                                        "Peluang bertahan fokus sampai menit ke-t",
                                        f"Peluang sesi selesai per jam mulai (timer {durasi} menit)"))
    xw = [f"Minggu {w}" for w in mingguan.index]
    fig.add_trace(go.Bar(x=xw, y=mingguan["tingkat_selesai"] * 100, marker_color=[
        "#2e7d32" if v >= 0.8 else "#f9a825" if v >= 0.5 else "#e53935" for v in mingguan["tingkat_selesai"]],
        text=[f"{v:.0%}" for v in mingguan["tingkat_selesai"]], textposition="outside", showlegend=False), row=1, col=1)
    fig.add_hline(y=80, line_dash="dot", line_color="#2e7d32", row=1, col=1)
    fig.add_trace(go.Bar(x=xw, y=mingguan["fokus_menit"], name="Rata-rata fokus", marker_color="#4c78a8",
                         text=[f"{v:.1f}" for v in mingguan["fokus_menit"]], textposition="outside"), row=1, col=2)
    fig.add_trace(go.Scatter(x=xw, y=mingguan["durasi_menit"], name="Durasi timer", mode="markers",
                             marker=dict(symbol="line-ew-open", size=40, color="#424242", line=dict(width=3))), row=1, col=2)
    batas = min(DURASI_MAX, max(horizon, durasi) + 5)
    m = grid <= batas
    fig.add_trace(go.Scatter(x=grid[m], y=S[m] * 100, mode="lines", line=dict(color="#6a1b9a", width=3, shape="hv"),
                             name="Peluang bertahan"), row=2, col=1)
    fig.add_hline(y=AMBANG_BERTAHAN * 100, line_dash="dot", line_color="#757575", row=2, col=1)
    fig.add_trace(go.Scatter(x=[durasi], y=[peluang_di(grid, S, durasi) * 100], mode="markers+text",
                             marker=dict(size=13, color="#e53935"), text=[f"rekomendasi {durasi} mnt"],
                             textposition="top right", showlegend=False), row=2, col=1)
    if mj is not None:
        fig.add_trace(go.Bar(x=[f"{h:02d}:00" for h in mj["jam"]], y=mj["peluang"] * 100, marker_color="#f9a825",
                             text=[f"{v:.0%}" for v in mj["peluang"]], textposition="outside", showlegend=False), row=2, col=2)
    else:
        per_jam = d.groupby(d["start"].dt.hour)["selesai"].mean()
        fig.add_trace(go.Bar(x=[f"{h:02d}:00" for h in per_jam.index], y=per_jam.values * 100, marker_color="#f9a825",
                             text=[f"{v:.0%}" for v in per_jam.values], textposition="outside", showlegend=False), row=2, col=2)
    fig.update_yaxes(range=[0, 115], row=1, col=1)
    fig.update_yaxes(range=[0, max(mingguan["durasi_menit"].max(), mingguan["fokus_menit"].max()) * 1.3], row=1, col=2)
    fig.update_yaxes(range=[0, 105], row=2, col=1)
    fig.update_xaxes(title_text="menit", row=2, col=1)
    fig.update_yaxes(range=[0, 115], row=2, col=2)
    fig.update_layout(height=720, template="plotly_white", barmode="overlay",
                      legend=dict(orientation="h", y=-0.1), margin=dict(l=60, r=40, t=50, b=50))
    return fig
