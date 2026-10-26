// =====================================================================
//  test_logic.cpp - Uji otomatis logika focus_logic.h di komputer (tanpa ESP32/Wokwi)
//  Compile & jalankan: g++ -std=c++11 -o test_logic test_logic.cpp && ./test_logic
//  Nomor TC mengikuti tabel pengujian di laporan (bagian 11). TC-02 (tombol END mengubah
//  timer) dan TC-13 (MQTT terputus) ada di sketch.ino sehingga diuji langsung di Wokwi.
// =====================================================================
#include "../wokwi/focus_logic.h"
#include <cstdio>
#include <vector>
#include <string>

// Penghitung hasil uji. CHECK(kondisi, pesan) menambah passes bila benar, atau mencetak FAIL.
static int fails = 0, passes = 0;
#define CHECK(cond, msg) do { if (cond) { passes++; } else { fails++; printf("   FAIL: %s  (line %d)\n", msg, __LINE__); } } while (0)

// Sim: simulasi alat. Menyimpan state machine, waktu tiruan (ms), input sensor, dan daftar event.
//   tick()  = maju satu langkah waktu (default 100 ms) lalu panggil update()
//   run()   = maju beberapa milidetik sekaligus
//   has()/count() = cek apakah / berapa kali event tertentu muncul
//   dockAndStart() = pasang HP di dock lalu tekan START
struct Sim {
  FocusCore c; uint32_t t = 0; FocusInputs in; std::vector<std::string> ev;
  Sim() { c.begin(paramsDemo(), 0); in.distanceCm = 50; in.lightRaw = 2000; }
  void tick(uint32_t ms = 100) {
    t += ms; c.update(t, in);
    in.startPressed = in.endPressed = in.cmdStart = in.cmdStop = false; in.cmdDurationMs = 0;
    EventType e; while (c.popEvent(e)) ev.push_back(FocusCore::eventName(e));
  }
  void run(uint32_t ms) { for (uint32_t x = 0; x < ms; x += 100) tick(); }
  bool has(const char* name) { for (auto& s : ev) if (s == name) return true; return false; }
  int count(const char* name) { int n = 0; for (auto& s : ev) if (s == name) n++; return n; }
  void dockAndStart() { in.dock = true; tick(); in.startPressed = true; tick(); }
};

// main(): menjalankan semua skenario uji berurutan dan mencetak jumlah pengecekan yang lulus.
int main() {
  printf("TC-01 mulai tanpa HP di dock\n");
  { Sim s; s.in.dock = false; s.tick(); s.in.startPressed = true; s.tick();
    CHECK(s.c.state == ST_IDLE, "tetap IDLE"); CHECK(s.has("START_REJECTED"), "event START_REJECTED");
    CHECK(!s.has("SESSION_START"), "sesi tidak mulai"); CHECK(s.c.rejectedRecently, "pesan OLED aktif"); }

  printf("TC-03 mulai sesi normal\n");
  { Sim s; s.dockAndStart();
    CHECK(s.c.state == ST_FOCUS, "masuk FOCUS"); CHECK(s.has("SESSION_START"), "event SESSION_START");
    CHECK(s.c.s.baselineCm == 50, "baseline = 50 cm"); s.run(1000);
    CHECK(s.c.remainingMs() <= 119000 && s.c.remainingMs() >= 118000, "countdown berjalan"); }

  printf("TC-04 pergi lalu kembali\n");
  { Sim s; s.dockAndStart(); s.run(3000);
    s.in.distanceCm = 150; s.run(4800); CHECK(s.c.state == ST_FOCUS, "belum AWAY sebelum 5 dtk");
    s.run(400); CHECK(s.c.state == ST_AWAY, "AWAY setelah >= 5 dtk"); CHECK(s.has("AWAY_START"), "event AWAY_START");
    s.in.distanceCm = 50; s.run(1800); CHECK(s.c.state == ST_AWAY, "belum kembali sebelum 2 dtk");
    s.run(400); CHECK(s.c.state == ST_FOCUS, "FOCUS lagi"); CHECK(s.has("AWAY_END"), "event AWAY_END");
    CHECK(s.c.s.awayCount == 1, "away_count = 1");
    CHECK(s.c.s.awayTotalMs >= 5000 && s.c.s.awayTotalMs <= 5400, "away_total ~5,2 dtk (sampai terdeteksi kembali)");
    CHECK(s.c.s.firstAwayMs >= 7000 && s.c.s.firstAwayMs <= 9000, "first_away tercatat"); }

  printf("TC-05 noise jarak 2 dtk\n");
  { Sim s; s.dockAndStart(); s.run(2000); s.in.distanceCm = 150; s.run(2000); s.in.distanceCm = 50; s.run(6000);
    CHECK(s.c.state == ST_FOCUS, "tetap FOCUS"); CHECK(!s.has("AWAY_START"), "tidak ada AWAY_START");
    CHECK(s.c.s.awayCount == 0, "away_count = 0"); }

  printf("TC-06 sesi ditinggal\n");
  { Sim s; s.dockAndStart(); s.run(2000); s.in.distanceCm = 150;
    s.run(5100); CHECK(s.c.state == ST_AWAY, "AWAY");
    s.run(5000); CHECK(s.c.paused, "timer dijeda setelah 10 dtk sejak pergi"); CHECK(s.has("TIMER_PAUSE"), "event TIMER_PAUSE");
    uint32_t f1 = s.c.s.focusMs; s.run(3000); CHECK(s.c.s.focusMs == f1, "waktu fokus berhenti saat dijeda");
    s.run(8000); CHECK(s.c.state == ST_RESULT, "berakhir"); CHECK(s.c.last.result == RES_DITINGGAL, "hasil DITINGGAL");
    CHECK(s.c.last.awayTotalMs >= 19500 && s.c.last.awayTotalMs <= 20500, "away_total ~20 dtk"); }

  printf("TC-07 HP diangkat sebentar\n");
  { Sim s; s.dockAndStart(); s.run(2000); s.in.dock = false; s.tick();
    CHECK(s.c.dockLifted, "status HP diangkat"); CHECK(s.has("DOCK_LIFTED"), "event DOCK_LIFTED");
    uint32_t f1 = s.c.s.focusMs; s.run(3000); CHECK(s.c.s.focusMs == f1, "timer berhenti saat HP diangkat");
    s.in.dock = true; s.tick(); CHECK(s.c.state == ST_FOCUS, "lanjut FOCUS"); CHECK(s.has("DOCK_RETURNED"), "event DOCK_RETURNED");
    CHECK(s.c.s.dockLiftCount == 1, "dock_lift_count = 1"); }

  printf("TC-08 HP diangkat terlalu lama\n");
  { Sim s; s.dockAndStart(); s.run(2000); s.in.dock = false; s.run(5200);
    CHECK(s.c.state == ST_RESULT, "berakhir"); CHECK(s.c.last.result == RES_END_PAKSA, "hasil END_PAKSA");
    CHECK(s.c.last.endReason == END_DOCK, "alasan hp_diangkat"); }

  printf("TC-09 end paksa via tombol\n");
  { Sim s; s.dockAndStart(); s.run(10000); s.in.endPressed = true; s.tick();
    CHECK(s.c.last.result == RES_END_PAKSA, "hasil END_PAKSA"); CHECK(s.c.last.endReason == END_BUTTON, "alasan tombol_end");
    CHECK(s.c.last.focusMs < s.c.last.targetMs, "focus_s < target_s");
    s.run(5100); CHECK(s.c.state == ST_READY, "kembali READY setelah 5 dtk"); }

  printf("TC-10 sesi selesai\n");
  { Sim s; s.dockAndStart(); s.run(120500);
    CHECK(s.c.last.result == RES_SELESAI, "hasil SELESAI"); CHECK(s.c.last.focusMs == s.c.last.targetMs, "completion_ratio = 1");
    CHECK(s.count("SESSION_END") == 1, "tepat satu SESSION_END"); }

  printf("TC-11 cahaya ruangan -> kecerahan\n");
  { int terang = brightnessFromLight(300, true), gelap = brightnessFromLight(3900, true);
    CHECK(gelap > terang, "ruangan gelap -> lampu lebih terang");
    CHECK(terang >= 60 && gelap <= 200, "dalam batas 60..200");
    CHECK(lightPercent(300, true) > lightPercent(3900, true), "persen cahaya konsisten"); }

  printf("TC-12 perintah start dari dashboard (3 menit)\n");
  { Sim s; s.in.dock = true; s.tick(); s.in.cmdStart = true; s.in.cmdDurationMs = 180000; s.tick();
    CHECK(s.c.state == ST_FOCUS, "sesi mulai"); CHECK(s.c.s.targetMs == 180000, "durasi 3 menit");
    s.in.cmdStop = true; s.tick(); CHECK(s.c.last.endReason == END_DASHBOARD, "stop dari dashboard tercatat"); }

  printf("Ekstra: lepas dock saat READY -> IDLE; END saat READY diabaikan\n");
  { Sim s; s.in.dock = true; s.tick(); s.in.endPressed = true; s.tick(); CHECK(s.c.state == ST_READY, "END diabaikan");
    s.in.dock = false; s.tick(); CHECK(s.c.state == ST_IDLE, "kembali IDLE"); }

  printf("Ekstra: pergi 2x dalam satu sesi\n");
  { Sim s; s.dockAndStart();
    for (int k = 0; k < 2; k++) { s.in.distanceCm = 150; s.run(6000); s.in.distanceCm = 50; s.run(3000); }
    CHECK(s.c.s.awayCount == 2, "away_count = 2"); CHECK(s.c.state == ST_FOCUS, "masih FOCUS"); }

  printf("\n%d checks passed, %d failed\n", passes, fails);
  return fails ? 1 : 0;
}
