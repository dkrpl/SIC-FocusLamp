// =====================================================================
//  focus_logic.h  -  Logika inti FocusLamp (state machine sesi fokus)
// ---------------------------------------------------------------------
//  File ini SENGAJA tidak memakai library Arduino sama sekali, supaya
//  logikanya bisa diuji terpisah dari hardware. sketch.ino yang membaca
//  sensor, lalu memanggil FocusCore::update() setiap putaran loop().
//
//  Alur state:
//    IDLE  --dock ON-->  READY  --START-->  FOCUS  <--pergi/kembali-->  AWAY
//    FOCUS/AWAY --> RESULT (SELESAI / END_PAKSA / DITINGGAL) --> READY/IDLE
// =====================================================================
#pragma once
#include <stdint.h>

// FocusState: kondisi alat. IDLE = HP belum di dock, READY = siap mulai, FOCUS = sesi berjalan,
//   AWAY = pengguna meninggalkan meja, RESULT = menampilkan hasil sesi selama beberapa detik.
enum FocusState : uint8_t { ST_IDLE, ST_READY, ST_FOCUS, ST_AWAY, ST_RESULT };
// SessionResult: hasil akhir sesi (SELESAI, END_PAKSA, atau DITINGGAL).
enum SessionResult : uint8_t { RES_NONE, RES_SELESAI, RES_END_PAKSA, RES_DITINGGAL };
// EndReason: alasan sesi berakhir; dikirim sebagai end_reason agar sesi yang dihentikan
//   sengaja bisa dibedakan dari sesi yang terganggu HP atau ditinggal.
enum EndReason : uint8_t { END_NONE, END_TIMEOUT, END_BUTTON, END_DOCK, END_DASHBOARD, END_AWAY };
// EventType: kejadian penting selama sesi; diteruskan sketch.ino ke Serial, MQTT, dan buzzer.
enum EventType : uint8_t {
  EV_NONE, EV_SESSION_START, EV_AWAY_START, EV_AWAY_END, EV_TIMER_PAUSE,
  EV_DOCK_LIFTED, EV_DOCK_RETURNED, EV_SESSION_END, EV_START_REJECTED
};

// ---------------- Parameter waktu (lihat PRD bagian Spesifikasi Logika) ----
// FocusParams: semua batas waktu dan ambang jarak yang dipakai state machine.
struct FocusParams {
  uint32_t sessionMs;       // durasi sesi default
  uint32_t confirmAwayMs;   // jarak jauh harus bertahan selama ini -> AWAY
  uint32_t confirmBackMs;   // jarak normal harus bertahan selama ini -> FOCUS
  uint32_t pauseAfterMs;    // pergi selama ini -> timer dijeda
  uint32_t abandonAfterMs;  // pergi selama ini -> sesi DITINGGAL
  uint32_t dockToleranceMs; // HP boleh diangkat selama ini
  uint32_t resultHoldMs;    // lama hasil akhir ditampilkan
  float awayDeltaCm;        // ambang pergi = baseline + nilai ini
};

// paramsNormal(): parameter untuk pemakaian sebenarnya (sesi 25 menit, konfirmasi pergi 30 dtk,
//   timer dijeda setelah pergi 2 menit, ditinggal setelah 5 menit, toleransi HP diangkat 10 dtk).
inline FocusParams paramsNormal() {
  FocusParams p;
  p.sessionMs = 25UL * 60UL * 1000UL;
  p.confirmAwayMs = 30000; p.confirmBackMs = 5000;
  p.pauseAfterMs = 120000; p.abandonAfterMs = 300000;
  p.dockToleranceMs = 10000; p.resultHoldMs = 5000;
  p.awayDeltaCm = 40.0f;
  return p;
}

// paramsDemo(): parameter yang dipercepat untuk pengujian dan video demo (sesi 2 menit,
//   konfirmasi pergi 5 dtk, jeda 10 dtk, ditinggal 20 dtk, toleransi HP diangkat 5 dtk).
inline FocusParams paramsDemo() {
  FocusParams p;
  p.sessionMs = 2UL * 60UL * 1000UL;
  p.confirmAwayMs = 5000; p.confirmBackMs = 2000;
  p.pauseAfterMs = 10000; p.abandonAfterMs = 20000;
  p.dockToleranceMs = 5000; p.resultHoldMs = 5000;
  p.awayDeltaCm = 40.0f;
  return p;
}

// ---------------- Input yang dibaca sketch.ino setiap loop -----------------
// FocusInputs: ringkasan semua input dalam satu putaran loop (sensor, tombol, perintah dashboard).
struct FocusInputs {
  bool dock = false;          // true = HP terpasang di dock
  float distanceCm = 400.0f;  // jarak (sudah di-median)
  int lightRaw = 0;           // ADC LDR 0..4095
  bool startPressed = false;  // tombol START ditekan (sekali)
  bool endPressed = false;    // tombol END ditekan (sekali)
  bool cmdStart = false;      // perintah start dari dashboard
  uint32_t cmdDurationMs = 0; // durasi dari dashboard (0 = default)
  bool cmdStop = false;       // perintah stop dari dashboard
};

// ---------------- Rekap satu sesi (menjadi satu baris dataset) -------------
// SessionSummary: data yang dikumpulkan selama satu sesi dan dikirim ke topic session.
struct SessionSummary {
  uint32_t startMs = 0, endMs = 0;
  uint32_t targetMs = 0, focusMs = 0;
  uint32_t awayCount = 0, awayTotalMs = 0;
  int32_t firstAwayMs = -1;   // -1 = tidak pernah pergi
  uint32_t dockLiftCount = 0;
  float baselineCm = 0;
  uint64_t lightSum = 0;
  uint32_t lightSamples = 0;
  SessionResult result = RES_NONE;
  EndReason endReason = END_NONE;
  // avgLight(): rata-rata nilai ADC cahaya ruangan selama sesi.
  uint32_t avgLight() const { return lightSamples ? (uint32_t)(lightSum / lightSamples) : 0; }
};

// ---------------- Kecerahan lampu dari cahaya ruangan (TC-11) --------------
// brightIsLow = true  -> ruangan terang menghasilkan ADC rendah (modul Wokwi)
// Ruangan gelap -> lampu lebih terang. Hasil 60..200 (dari 255, dibatasi).
// roomDarkness(): mengubah nilai ADC LDR menjadi tingkat gelap ruangan (0 = terang, 4095 = gelap).
inline int roomDarkness(int lightRaw, bool brightIsLow) {
  if (lightRaw < 0) lightRaw = 0;
  if (lightRaw > 4095) lightRaw = 4095;
  return brightIsLow ? lightRaw : 4095 - lightRaw;   // 0 = terang, 4095 = gelap
}
// lightPercent(): persentase cahaya ruangan untuk OLED dan dashboard (100 = sangat terang).
inline int lightPercent(int lightRaw, bool brightIsLow) {
  return 100 - (roomDarkness(lightRaw, brightIsLow) * 100L) / 4095;
}
// brightnessFromLight(): kecerahan NeoPixel dari cahaya ruangan; makin gelap ruangan, makin terang lampu (60-200).
inline int brightnessFromLight(int lightRaw, bool brightIsLow, int minB = 60, int maxB = 200) {
  long d = roomDarkness(lightRaw, brightIsLow);
  return (int)(minB + (d * (maxB - minB)) / 4095);
}

// ===========================================================================
// FocusCore: state machine sesi fokus. sketch.ino memanggil update() setiap loop dengan input
//   terbaru, lalu membaca state, sisa waktu, dan event untuk menjalankan lampu, OLED, buzzer, MQTT.
class FocusCore {
 public:
  FocusParams p = paramsNormal();
  FocusState state = ST_IDLE;
  SessionSummary s;          // sesi yang sedang berjalan
  SessionSummary last;       // sesi terakhir yang sudah selesai
  bool paused = false;       // timer dijeda saat AWAY terlalu lama
  bool dockLifted = false;   // HP sedang diangkat dari dock
  uint32_t dockLiftAt = 0;
  uint32_t awayAt = 0;       // kapan pengguna mulai pergi
  uint32_t resultAt = 0;
  uint32_t rejectAt = 0;     // kapan START ditolak (untuk pesan OLED)
  bool rejectedRecently = false;
  uint32_t sessionSeq = 0;
  float lastDistance = 400.0f;

  // begin(): menyiapkan state machine (dipanggil sekali di setup) dengan parameter demo/normal.
  void begin(const FocusParams& params, uint32_t now) {
    p = params; state = ST_IDLE; lastTick = now; evHead = evTail = 0;
  }

  // inSession(): true bila sesi sedang berjalan (FOCUS atau AWAY).
  bool inSession() const { return state == ST_FOCUS || state == ST_AWAY; }

  // awayThreshold(): ambang jarak pergi = baseline (jarak duduk saat START) + 40 cm.
  float awayThreshold() const {
    return (inSession() ? s.baselineCm : 60.0f) + p.awayDeltaCm;
  }
  // present(): true bila jarak terakhir masih di bawah ambang pergi (pengguna dianggap hadir).
  bool present() const { return lastDistance <= awayThreshold(); }

  // remainingMs(): sisa waktu sesi dalam milidetik (untuk countdown OLED dan dashboard).
  uint32_t remainingMs() const {
    if (!inSession()) return state == ST_RESULT ? 0 : p.sessionMs;
    return s.focusMs >= s.targetMs ? 0 : s.targetMs - s.focusMs;
  }
  // dockLeftMs(): sisa toleransi saat HP diangkat sebelum sesi dihentikan (ditampilkan di OLED).
  uint32_t dockLeftMs(uint32_t now) const {
    if (!dockLifted) return 0;
    uint32_t used = now - dockLiftAt;
    return used >= p.dockToleranceMs ? 0 : p.dockToleranceMs - used;
  }

  // stateName(): nama state sebagai teks (IDLE, READY, FOCUS, AWAY, atau hasil sesi).
  const char* stateName() const {
    switch (state) {
      case ST_IDLE: return "IDLE";
      case ST_READY: return "READY";
      case ST_FOCUS: return "FOCUS";
      case ST_AWAY: return "AWAY";
      default: return resultName(last.result);
    }
  }
  // resultName(): teks hasil sesi untuk payload MQTT.
  static const char* resultName(SessionResult r) {
    switch (r) {
      case RES_SELESAI: return "SELESAI";
      case RES_END_PAKSA: return "END_PAKSA";
      case RES_DITINGGAL: return "DITINGGAL";
      default: return "NONE";
    }
  }
  // endReasonName(): teks alasan sesi berakhir untuk payload MQTT (end_reason).
  static const char* endReasonName(EndReason r) {
    switch (r) {
      case END_TIMEOUT: return "waktu_habis";
      case END_BUTTON: return "tombol_end";
      case END_DOCK: return "hp_diangkat";
      case END_DASHBOARD: return "dashboard";
      case END_AWAY: return "ditinggal";
      default: return "none";
    }
  }
  // eventName(): teks nama event untuk Serial Monitor dan topic event.
  static const char* eventName(EventType e) {
    switch (e) {
      case EV_SESSION_START: return "SESSION_START";
      case EV_AWAY_START: return "AWAY_START";
      case EV_AWAY_END: return "AWAY_END";
      case EV_TIMER_PAUSE: return "TIMER_PAUSE";
      case EV_DOCK_LIFTED: return "DOCK_LIFTED";
      case EV_DOCK_RETURNED: return "DOCK_RETURNED";
      case EV_SESSION_END: return "SESSION_END";
      case EV_START_REJECTED: return "START_REJECTED";
      default: return "NONE";
    }
  }

  // popEvent(): mengambil event berikutnya dari antrian (untuk dikirim ke Serial dan MQTT).
  //   Mengembalikan false bila antrian kosong.
  bool popEvent(EventType& e) {
    if (evHead == evTail) return false;
    e = evQ[evTail]; evTail = (evTail + 1) % EVQ; return true;
  }

  // =========================== inti logika ==============================
  // update(): inti logika, dipanggil setiap putaran loop.
  //   IDLE: START ditolak; pindah ke READY bila HP di dock.
  //   READY: kembali IDLE bila HP diangkat; mulai sesi bila START atau perintah dashboard.
  //   FOCUS/AWAY: dijalankan oleh runSession(). RESULT: setelah 5 dtk kembali ke READY/IDLE.
  void update(uint32_t now, const FocusInputs& in) {
    uint32_t dt = now - lastTick;
    lastTick = now;
    lastDistance = in.distanceCm;
    if (rejectedRecently && now - rejectAt > 2000) rejectedRecently = false;

    switch (state) {
      case ST_IDLE:
        if (in.startPressed || in.cmdStart) {           // TC-01: tanpa HP ditolak
          push(EV_START_REJECTED); rejectAt = now; rejectedRecently = true;
        }
        if (in.dock) { state = ST_READY; rejectedRecently = false; }
        break;

      case ST_READY:
        if (!in.dock) { state = ST_IDLE; break; }
        if (in.startPressed || in.cmdStart) {
          startSession(now, in.cmdStart && in.cmdDurationMs ? in.cmdDurationMs : p.sessionMs,
                       in.distanceCm);
        }
        break;

      case ST_FOCUS:
      case ST_AWAY:
        runSession(now, dt, in);
        break;

      case ST_RESULT:
        if (now - resultAt >= p.resultHoldMs) state = in.dock ? ST_READY : ST_IDLE;
        break;
    }
  }

 private:
  static const int EVQ = 16;
  EventType evQ[EVQ];
  int evHead = 0, evTail = 0;
  uint32_t lastTick = 0;
  bool farFlag = false, nearFlag = false;
  uint32_t farSince = 0, nearSince = 0;

  // push(): menambahkan event ke antrian melingkar (maksimal 16); bila penuh event dibuang.
  void push(EventType e) {
    int next = (evHead + 1) % EVQ;
    if (next == evTail) return;   // antrian penuh, event dibuang
    evQ[evHead] = e; evHead = next;
  }

  // startSession(): memulai sesi baru: mengosongkan rekap, mencatat durasi dan baseline jarak,
  //   lalu pindah ke FOCUS dan mengirim event SESSION_START.
  void startSession(uint32_t now, uint32_t targetMs, float distanceCm) {
    s = SessionSummary();
    s.startMs = now;
    s.targetMs = targetMs;
    // baseline = jarak duduk normal; dibatasi supaya bacaan aneh tidak merusak ambang
    float b = distanceCm;
    if (b < 20) b = 20;
    if (b > 150) b = 150;
    s.baselineCm = b;
    paused = false; dockLifted = false;
    farFlag = nearFlag = false;
    sessionSeq++;
    state = ST_FOCUS;
    push(EV_SESSION_START);
  }

  // endSession(): mengakhiri sesi dengan hasil dan alasan tertentu, menyimpan rekap ke `last`,
  //   pindah ke RESULT, dan mengirim event SESSION_END.
  void endSession(uint32_t now, SessionResult r, EndReason why) {
    if (state == ST_AWAY) s.awayTotalMs += now - awayAt;
    s.result = r; s.endReason = why; s.endMs = now;
    last = s;
    state = ST_RESULT; resultAt = now;
    dockLifted = false; paused = false;
    push(EV_SESSION_END);
  }

  // runSession(): aturan selama sesi berjalan, diperiksa berurutan: hitung waktu fokus,
  //   penghentian manual, HP diangkat, waktu habis, lalu deteksi pergi/kembali.
  void runSession(uint32_t now, uint32_t dt, const FocusInputs& in) {
    // 1) hitung waktu fokus (berhenti saat HP diangkat atau saat AWAY dijeda)
    bool counting = !dockLifted && (state == ST_FOCUS || !paused);
    if (counting) s.focusMs += dt;
    s.lightSum += (uint32_t)in.lightRaw; s.lightSamples++;

    // 2) penghentian manual
    if (in.endPressed) { endSession(now, RES_END_PAKSA, END_BUTTON); return; }
    if (in.cmdStop)    { endSession(now, RES_END_PAKSA, END_DASHBOARD); return; }

    // 3) HP diangkat dari dock (TC-07, TC-08)
    if (!in.dock) {
      if (!dockLifted) {
        dockLifted = true; dockLiftAt = now; s.dockLiftCount++;
        push(EV_DOCK_LIFTED);
      } else if (now - dockLiftAt >= p.dockToleranceMs) {
        endSession(now, RES_END_PAKSA, END_DOCK); return;
      }
    } else if (dockLifted) {
      dockLifted = false;
      push(EV_DOCK_RETURNED);
    }

    // 4) waktu habis (TC-10)
    if (s.focusMs >= s.targetMs) { s.focusMs = s.targetMs; endSession(now, RES_SELESAI, END_TIMEOUT); return; }

    // 5) deteksi kehadiran dengan waktu konfirmasi (TC-04, TC-05, TC-06)
    bool far = in.distanceCm > s.baselineCm + p.awayDeltaCm;
    if (state == ST_FOCUS) {
      nearFlag = false;
      if (far) {
        if (!farFlag) { farFlag = true; farSince = now; }
        if (now - farSince >= p.confirmAwayMs) {
          state = ST_AWAY; awayAt = farSince; paused = false; farFlag = false;
          s.awayCount++;
          if (s.firstAwayMs < 0) s.firstAwayMs = (int32_t)s.focusMs;
          push(EV_AWAY_START);
        }
      } else {
        farFlag = false;
      }
    } else {  // ST_AWAY
      if (now - awayAt >= p.abandonAfterMs) { endSession(now, RES_DITINGGAL, END_AWAY); return; }
      if (!paused && now - awayAt >= p.pauseAfterMs) { paused = true; push(EV_TIMER_PAUSE); }
      if (!far) {
        if (!nearFlag) { nearFlag = true; nearSince = now; }
        if (now - nearSince >= p.confirmBackMs) {
          s.awayTotalMs += nearSince - awayAt;
          state = ST_FOCUS; paused = false; nearFlag = false;
          push(EV_AWAY_END);
        }
      } else {
        nearFlag = false;
      }
    }
  }
};
