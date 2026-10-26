// =====================================================================
//  FocusLamp - Lampu belajar IoT untuk sesi fokus
//  Samsung Innovation Campus Batch 8 - Selection Phase 2
// ---------------------------------------------------------------------
//  Input : HC-SR04 (jarak), LDR (cahaya), slide switch (HP di dock),
//          tombol START, tombol END (END PAKSA saat sesi / ubah timer saat READY)
//  Output: NeoPixel ring 16 LED (lampu + status), OLED SSD1306, buzzer,
//          LED hijau DOCK (HP di dock), LED kuning NET (status WiFi/MQTT)
//  Komunikasi: Serial Monitor + MQTT (EMQX Serverless, TLS 8883). Saat koneksi putus alat tetap
//              jalan; rekap sesi disimpan di antrean lalu dikirim ulang setelah tersambung lagi.
//  Timer sesi: tombol END saat READY, ketik di Serial Monitor, atau perintah
//              MQTT {"action":"set_duration","duration_min":25} dari dashboard/aplikasi
//  Logika state machine ada di focus_logic.h
// =====================================================================
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_NeoPixel.h>
#include <time.h>
#include "focus_logic.h"

// ====================== 1. KONFIGURASI (UBAH DI SINI) ======================
// --- Saklar jaringan ---
//     false = mode offline: tanpa WiFi & broker MQTT, semua data hanya ke Serial Monitor
//     true  = kirim data ke EMQX Serverless (isi MQTT_HOST/USER/PASS di bawah dulu)
const bool USE_MQTT = true;

// --- WiFi Wokwi (jangan diubah saat simulasi di Wokwi) ---
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";

// --- EMQX Serverless: Address dari halaman Overview, username/password dari Access Control > Authentication ---
const char* MQTT_HOST = "aa1aa161.ala.asia-southeast1.emqxsl.com";    // Address deployment, tanpa "mqtts://"
const int   MQTT_PORT = 8883;
const char* MQTT_USER = "focuslampsic";
const char* MQTT_PASS = "";

// --- Identitas perangkat & topic: sic8/focuslamp/<DEVICE_ID>/<jenis> ---
#define DEVICE_ID "fl-01"
#define TOPIC_BASE "sic8/focuslamp/" DEVICE_ID

// --- Mode demo: semua waktu dipercepat (sesi 2 menit) untuk uji & video ---
bool demoMode = true;

// --- Pilihan durasi sesi (menit). Saat READY, tombol END berpindah ke pilihan berikutnya.
//     Durasi juga bisa diketik di Serial Monitor (misal: 25) atau dikirim dari aplikasi/dashboard.
const uint16_t DURATION_PRESETS[] = {1, 2, 5, 10, 15, 20, 25, 30, 45, 60};

// --- Arah sensor cahaya. Modul LDR Wokwi: ruangan terang = nilai ADC kecil.
//     Kalau saat kalibrasi ternyata kebalikannya, ubah menjadi false.
const bool LDR_BRIGHT_IS_LOW = true;

// ============================ 2. PIN / GPIO ================================
const int PIN_TRIG = 5;
const int PIN_ECHO = 18;
const int PIN_LDR = 34;        // ADC1, tetap bisa dibaca saat WiFi aktif
const int PIN_DOCK = 27;       // slide switch, LOW = HP di dock
const int PIN_BTN_START = 32;  // aktif LOW
const int PIN_BTN_END = 33;    // aktif LOW
const int PIN_NEOPIXEL = 4;
const int PIN_BUZZER = 25;
const int PIN_LED_DOCK = 26;   // LED hijau: HP di dock (lewat resistor 220 ohm ke GND)
const int PIN_LED_NET = 13;    // LED kuning: status WiFi/MQTT (lewat resistor 220 ohm ke GND)
                               // Kuning (Vf +-2 V) dipilih karena LED biru (Vf +-3 V) sangat redup dari pin 3,3 V
const int NUM_PIXELS = 16;
// OLED memakai I2C default ESP32: SDA = GPIO21, SCL = GPIO22, alamat 0x3C

// ============================ 3. OBJEK GLOBAL ==============================
struct Note { uint16_t freq; uint16_t ms; };   // satu nada buzzer (Hz, ms)
Adafruit_SSD1306 display(128, 64, &Wire, -1);
Adafruit_NeoPixel ring(NUM_PIXELS, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);
WiFiClientSecure tlsClient;
PubSubClient mqtt(tlsClient);
FocusCore core;

bool oledOk = false;
bool timeSynced = false;
bool wifiWasConnected = false;
bool mqttWasConnected = false;
bool netSimDown = false;        // true = koneksi diputus sengaja lewat perintah Serial "putus" (untuk pengujian)
uint32_t lastMqttAttempt = 0, lastWifiRetry = 0, lastFlush = 0;
uint32_t lastTelemetry = 0, lastDistRead = 0, lastLamp = 0, lastOled = 0;
FocusState lastState = ST_IDLE;
bool lastPaused = false, lastLifted = false;
char sessionId[32] = "-";
unsigned long sessionStartEpoch = 0;

// --- antrean rekap sesi yang belum terkirim karena MQTT putus (lihat bagian 9) ---
const int OUTBOX_MAX = 8;
char outbox[OUTBOX_MAX][560];
int outboxCount = 0;

// --- data sensor terbaru ---
float distBuf[5] = {400, 400, 400, 400, 400};
int distIdx = 0;
float distanceCm = 400;      // median 5 bacaan terakhir
int lightRaw = 0;
int brightness = 0;
bool dockOn = false;

// --- perintah dari dashboard (diisi di callback MQTT) ---
bool pendingCmdStart = false, pendingCmdStop = false;
uint32_t pendingCmdDurationMs = 0;

// ======================= 4. TOMBOL (INTERRUPT) =============================
// Interrupt dipakai supaya tekanan tombol tidak hilang walaupun loop sedang
// sibuk (misalnya saat menyambung ke broker MQTT).
volatile bool startFlag = false, endFlag = false;
volatile uint32_t startLastIsr = 0, endLastIsr = 0;
// onStartIsr(): dipanggil otomatis oleh interrupt saat tombol START ditekan.
//   Hanya menyalakan penanda startFlag (dibaca di loop); tekanan < 250 ms dari
//   tekanan sebelumnya diabaikan sebagai pantulan kontak (debounce).
void IRAM_ATTR onStartIsr() {
  uint32_t t = millis();
  if (t - startLastIsr > 250) { startFlag = true; startLastIsr = t; }
}
// onEndIsr(): sama seperti onStartIsr(), untuk tombol END (penanda endFlag).
void IRAM_ATTR onEndIsr() {
  uint32_t t = millis();
  if (t - endLastIsr > 250) { endFlag = true; endLastIsr = t; }
}

// Slide switch dock di-debounce 50 ms
bool dockRaw = false, dockStable = false;
uint32_t dockChangedAt = 0;
// readDock(): membaca slide switch (HP di dock) dan menyaring perubahan singkat.
//   Status dockOn baru berubah bila posisi switch stabil minimal 50 ms.
void readDock(uint32_t now) {
  bool r = digitalRead(PIN_DOCK) == LOW;
  if (r != dockRaw) { dockRaw = r; dockChangedAt = now; }
  if (now - dockChangedAt >= 50) dockStable = dockRaw;
  dockOn = dockStable;
}

// ============================ 5. SENSOR ====================================
// readUltrasonicCm(): mengukur jarak pengguna dengan HC-SR04.
//   Mengirim pulsa TRIG 10 us, mengukur lama pulsa ECHO, lalu mengubahnya ke cm
//   (kecepatan suara 0,0343 cm/us, dibagi 2 karena pulang-pergi). Tanpa pantulan = 400 cm.
float readUltrasonicCm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long dur = pulseIn(PIN_ECHO, HIGH, 30000UL);  // timeout ~5 m
  if (dur == 0) return 400.0f;                           // tidak ada pantulan = jauh
  return dur * 0.0343f / 2.0f;
}

// median5(): mengembalikan nilai tengah dari 5 bacaan jarak terakhir.
//   Median membuang bacaan ekstrem sesaat sehingga deteksi pergi/kembali tidak mudah salah.
float median5(const float* v) {
  float a[5];
  for (int i = 0; i < 5; i++) a[i] = v[i];
  for (int i = 1; i < 5; i++) {          // insertion sort kecil
    float k = a[i]; int j = i - 1;
    while (j >= 0 && a[j] > k) { a[j + 1] = a[j]; j--; }
    a[j + 1] = k;
  }
  return a[2];
}

// ============================ 6. BUZZER ====================================
// Pemutar nada sederhana tanpa delay(), supaya loop tetap jalan.
// (struct Note didefinisikan di bagian 3 agar dikenali prototipe otomatis Arduino)
const Note TONE_START[]  = {{1568, 100}, {0, 40}, {2093, 120}};
const Note TONE_AWAY[]   = {{1500, 250}};
const Note TONE_DOCK[]   = {{2500, 100}, {0, 80}, {2500, 100}};
const Note TONE_DONE[]   = {{1047, 150}, {1319, 150}, {1568, 350}};
const Note TONE_END[]    = {{440, 200}, {0, 60}, {330, 400}};
const Note TONE_REJECT[] = {{300, 200}};
const Note TONE_SET[]    = {{1800, 60}};
const Note* tonePlaying = nullptr;
int toneLen = 0, toneIdx = 0;
uint32_t toneNextAt = 0;

// playTone(): memulai urutan nada buzzer (dipanggil lewat makro PLAY).
//   Nada dimainkan bertahap oleh updateBuzzer() sehingga program tidak berhenti menunggu.
void playTone(const Note* notes, int len) {
  tonePlaying = notes; toneLen = len; toneIdx = 0; toneNextAt = 0;
}
// updateBuzzer(): memainkan nada berikutnya bila durasi nada sebelumnya sudah lewat.
void updateBuzzer(uint32_t now) {
  if (!tonePlaying || now < toneNextAt) return;
  if (toneIdx >= toneLen) { noTone(PIN_BUZZER); tonePlaying = nullptr; return; }
  const Note& n = tonePlaying[toneIdx++];
  if (n.freq) tone(PIN_BUZZER, n.freq); else noTone(PIN_BUZZER);
  toneNextAt = now + n.ms;
}
#define PLAY(arr) playTone(arr, sizeof(arr) / sizeof(arr[0]))

// ============================ 7. LAMPU =====================================
// fillRing(): mengisi ke-16 LED NeoPixel dengan satu warna (r, g, b)
//   pada tingkat kecerahan level (0-255), lalu menampilkannya.
void fillRing(uint8_t r, uint8_t g, uint8_t b, int level) {   // level 0..255
  uint32_t c = ring.Color((r * level) / 255, (g * level) / 255, (b * level) / 255);
  for (int i = 0; i < NUM_PIXELS; i++) ring.setPixelColor(i, c);
  ring.show();
}

// updateLamp(): menentukan warna dan kecerahan lampu sesuai kondisi sesi.
//   IDLE mati, READY biru redup, FOCUS putih (kecerahan otomatis dari LDR; kuning hangat
//   di 10% waktu terakhir), AWAY kuning, HP diangkat merah berkedip, SELESAI hijau,
//   END PAKSA merah berkedip, DITINGGAL merah redup.
void updateLamp(uint32_t now) {
  bool blinkFast = (now / 250) % 2 == 0;
  bool blinkSlow = (now / 600) % 2 == 0;
  brightness = brightnessFromLight(lightRaw, LDR_BRIGHT_IS_LOW);

  if (core.inSession() && core.dockLifted) {            // HP diangkat: merah berkedip
    fillRing(255, 0, 0, blinkFast ? 200 : 0);
    return;
  }
  switch (core.state) {
    case ST_IDLE:  fillRing(0, 0, 0, 0); break;
    case ST_READY: fillRing(0, 60, 255, 35); break;      // biru redup
    case ST_FOCUS: {
      bool lastTenPercent = core.remainingMs() * 10 <= core.s.targetMs;
      if (lastTenPercent) fillRing(255, 170, 70, brightness);   // kuning hangat
      else fillRing(255, 255, 255, brightness);                 // putih
      break;
    }
    case ST_AWAY:
      if (core.paused) fillRing(255, 160, 0, blinkSlow ? 120 : 20);
      else fillRing(255, 160, 0, 120);                   // kuning
      break;
    case ST_RESULT:
      if (core.last.result == RES_SELESAI) fillRing(0, 255, 0, 150);
      else if (core.last.result == RES_END_PAKSA) fillRing(255, 0, 0, blinkFast ? 200 : 0);
      else fillRing(255, 0, 0, 40);                      // DITINGGAL: merah redup
      break;
  }
}

// updateDockLed(): LED hijau di GPIO26 sebagai tanda HP terpasang di dock.
//   mati          = HP belum di dock
//   nyala terus   = HP sudah di dock (siap belajar / sesi berjalan normal)
//   berkedip      = sesi berjalan tetapi HP diangkat; kembalikan sebelum toleransi habis
void updateDockLed(uint32_t now) {
  bool on;
  if (core.inSession() && core.dockLifted) on = (now / 250) % 2 == 0;   // kedip 2x per detik
  else on = dockOn;
  digitalWrite(PIN_LED_DOCK, on ? HIGH : LOW);
}

// updateNetLed(): LED kuning di GPIO13 sebagai tanda koneksi ke server.
//   nyala terus   = tersambung ke broker MQTT (EMQX), data terkirim ke web
//   kedip cepat   = WiFi tersambung, tetapi MQTT belum/terputus (cek alamat, username, password)
//   kedip lambat  = WiFi putus / sedang menyambung WiFi
//   mati          = mode offline (USE_MQTT = false)
void updateNetLed(uint32_t now) {
  bool on;
  if (!USE_MQTT) on = false;
  else if (mqtt.connected()) on = true;
  else if (WiFi.status() == WL_CONNECTED) on = (now / 150) % 2;   // sekitar 3x per detik
  else on = (now / 500) % 2;                                      // 1x per detik
  digitalWrite(PIN_LED_NET, on ? HIGH : LOW);
}

// ============================ 8. OLED ======================================
// updateOled(): menggambar ulang layar OLED.
//   Baris atas: state, mode, jumlah sesi di antrean (bila ada), status koneksi
//   (M = MQTT, w = WiFi saja, - = WiFi putus / offline).
//   Tengah: sisa waktu mm:ss. Bawah: pesan sesuai kondisi serta cahaya dan kecerahan lampu.
void updateOled(uint32_t now) {
  if (!oledOk) return;
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // baris atas: state + mode + status koneksi
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print(core.stateName());
  display.setCursor(80, 0);
  display.print(demoMode ? "DEMO" : "    ");
  if (outboxCount > 0) {                 // jumlah sesi yang menunggu dikirim (antrean)
    display.setCursor(104, 0);
    display.print(outboxCount);
  }
  display.setCursor(116, 0);
  display.print(!USE_MQTT ? "-" : mqtt.connected() ? "M" : (WiFi.status() == WL_CONNECTED ? "w" : "-"));
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  // tengah: countdown besar mm:ss
  uint32_t rem = core.remainingMs() / 1000;
  char buf[40];
  snprintf(buf, sizeof(buf), "%02lu:%02lu", (unsigned long)(rem / 60), (unsigned long)(rem % 60));
  display.setTextSize(3);
  display.setCursor(19, 16);
  display.print(buf);

  // baris info sesuai kondisi
  display.setTextSize(1);
  display.setCursor(0, 44);
  if (core.rejectedRecently) {
    display.print("HP belum di dock!");
  } else if (core.inSession() && core.dockLifted) {
    snprintf(buf, sizeof(buf), "Kembalikan HP! %lus", (unsigned long)((core.dockLeftMs(now) + 999) / 1000));
    display.print(buf);
  } else {
    switch (core.state) {
      case ST_IDLE:  display.print("Pasang HP di dock"); break;
      case ST_READY:
        if (core.p.sessionMs >= 60000)
          snprintf(buf, sizeof(buf), "%lu mnt | END=ubah", (unsigned long)(core.p.sessionMs / 60000));
        else
          snprintf(buf, sizeof(buf), "%lu dtk | END=ubah", (unsigned long)(core.p.sessionMs / 1000));
        display.print(buf);
        break;
      case ST_FOCUS:
        snprintf(buf, sizeof(buf), "Jarak %dcm base %d", (int)distanceCm, (int)core.s.baselineCm);
        display.print(buf);
        break;
      case ST_AWAY:  display.print(core.paused ? "Timer dijeda" : "Kembali ke meja!"); break;
      case ST_RESULT:
        snprintf(buf, sizeof(buf), "Fokus %lus dari %lus",
                 (unsigned long)(core.last.focusMs / 1000), (unsigned long)(core.last.targetMs / 1000));
        display.print(buf);
        break;
    }
  }

  // baris bawah: cahaya & kecerahan
  display.setCursor(0, 56);
  snprintf(buf, sizeof(buf), "Cahaya %d%% Lampu %d", lightPercent(lightRaw, LDR_BRIGHT_IS_LOW), brightness);
  display.print(buf);
  display.display();
}

// ============================ 9. MQTT ======================================
// epochNow(): waktu sekarang dalam detik sejak 1970 (UTC) dari server NTP.
//   Mengembalikan 0 bila jam belum tersinkron, agar dashboard tahu waktunya belum valid.
unsigned long epochNow() {
  time_t t = time(nullptr);
  return t > 1700000000 ? (unsigned long)t : 0;   // 0 = jam belum sinkron
}

// publishMsg(): mengirim satu pesan JSON ke topic sic8/focuslamp/<DEVICE_ID>/<sub>.
//   Tidak melakukan apa pun bila MQTT belum tersambung (alat tetap jalan offline).
void publishMsg(const char* sub, const char* payload, bool retain) {
  if (!mqtt.connected()) return;
  char topic[64];
  snprintf(topic, sizeof(topic), "%s/%s", TOPIC_BASE, sub);
  mqtt.publish(topic, payload, retain);
}

// publishStatus(): mengirim status alat (state, sisa waktu, durasi, id sesi, mode)
//   ke topic status dengan retain, sehingga dashboard yang baru tersambung langsung tahu kondisi alat.
void publishStatus() {
  char p[200];
  snprintf(p, sizeof(p),
           "{\"device\":\"%s\",\"ts\":%lu,\"state\":\"%s\",\"remaining_s\":%lu,\"target_s\":%lu,"
           "\"session_id\":\"%s\",\"demo\":%s}",
           DEVICE_ID, epochNow(), core.stateName(), (unsigned long)(core.remainingMs() / 1000),
           (unsigned long)((core.inSession() ? core.s.targetMs : core.p.sessionMs) / 1000),
           sessionId, demoMode ? "true" : "false");
  publishMsg("status", p, true);
}

// publishTelemetry(): mengirim data sensor berkala ke topic telemetry
//   (jarak, ambang pergi, hadir, dock, cahaya, kecerahan, waktu fokus, sisa waktu, jeda, mode).
void publishTelemetry() {
  char p[340];
  snprintf(p, sizeof(p),
           "{\"device\":\"%s\",\"ts\":%lu,\"state\":\"%s\",\"distance_cm\":%.1f,\"threshold_cm\":%.1f,\"present\":%s,"
           "\"dock\":%s,\"light_raw\":%d,\"light_pct\":%d,\"brightness\":%d,\"elapsed_s\":%lu,"
           "\"remaining_s\":%lu,\"paused\":%s,\"demo\":%s}",
           DEVICE_ID, epochNow(), core.stateName(), distanceCm, core.awayThreshold(),
           core.present() ? "true" : "false",
           dockOn ? "true" : "false", lightRaw, lightPercent(lightRaw, LDR_BRIGHT_IS_LOW), brightness,
           (unsigned long)((core.inSession() ? core.s.focusMs : 0) / 1000),
           (unsigned long)(core.remainingMs() / 1000), core.paused ? "true" : "false",
           demoMode ? "true" : "false");
  publishMsg("telemetry", p, false);
}

// publishEvent(): mengirim satu kejadian (SESSION_START, AWAY_START, DOCK_LIFTED, dll.)
//   ke topic event, lengkap dengan id sesi, jarak, dan detik fokus saat kejadian.
void publishEvent(EventType e) {
  char p[220];
  snprintf(p, sizeof(p),
           "{\"device\":\"%s\",\"ts\":%lu,\"session_id\":\"%s\",\"type\":\"%s\",\"distance_cm\":%.1f,"
           "\"elapsed_s\":%lu}",
           DEVICE_ID, epochNow(), sessionId, FocusCore::eventName(e), distanceCm,
           (unsigned long)(core.s.focusMs / 1000));
  publishMsg("event", p, false);
}

// ---------------- Antrean sesi (outbox) ----------------
// Rekap sesi adalah data terpenting (dipakai riwayat dan Saran AI), jadi tidak boleh hilang
// saat WiFi/MQTT putus. Sesi yang gagal dikirim disimpan di RAM (maks. 8 sesi) lalu dikirim
// ulang otomatis, urut dari yang paling lama, segera setelah MQTT tersambung kembali.
// queueSession(): menyimpan satu rekap sesi ke antrean. Bila antrean penuh, sesi tertua dibuang.
//   Penanda "queued":true ditambahkan agar dashboard tahu sesi ini dikirim terlambat.
void queueSession(const char* payload) {
  if (outboxCount == OUTBOX_MAX) {
    for (int i = 1; i < OUTBOX_MAX; i++) strcpy(outbox[i - 1], outbox[i]);
    outboxCount--;
    Serial.println("[ANTREAN] penuh, sesi tertua dibuang");
  }
  char* slot = outbox[outboxCount];
  strncpy(slot, payload, sizeof(outbox[0]) - 16);
  slot[sizeof(outbox[0]) - 16] = '\0';
  size_t n = strlen(slot);
  if (n > 0 && slot[n - 1] == '}') strcpy(slot + n - 1, ",\"queued\":true}");
  outboxCount++;
  Serial.printf("[ANTREAN] MQTT putus, sesi disimpan (%d menunggu dikirim)\n", outboxCount);
}

// flushOutbox(): mengirim semua sesi di antrean selama MQTT tersambung. Berhenti bila
//   pengiriman gagal (sisa antrean dicoba lagi 5 detik kemudian).
void flushOutbox() {
  while (outboxCount > 0 && mqtt.connected()) {
    if (!mqtt.publish(TOPIC_BASE "/session", outbox[0], false)) break;
    for (int i = 1; i < outboxCount; i++) strcpy(outbox[i - 1], outbox[i]);
    outboxCount--;
    Serial.printf("[ANTREAN] 1 sesi tertunda terkirim, sisa %d\n", outboxCount);
  }
}

// publishSession(): mengirim rekap satu sesi yang baru berakhir ke topic session.
//   Satu pesan = satu baris dataset (durasi, waktu fokus, hasil, alasan berakhir,
//   jumlah pergi, HP diangkat, rata-rata cahaya, dll.). Juga dicetak ke Serial Monitor.
//   Bila MQTT sedang putus, rekap disimpan di antrean dan dikirim setelah tersambung lagi.
void publishSession() {
  const SessionSummary& s = core.last;
  char firstAway[16];
  if (s.firstAwayMs < 0) snprintf(firstAway, sizeof(firstAway), "null");
  else snprintf(firstAway, sizeof(firstAway), "%.2f", s.firstAwayMs / 60000.0f);
  char p[520];
  snprintf(p, sizeof(p),
           "{\"device\":\"%s\",\"session_id\":\"%s\",\"start_ts\":%lu,\"end_ts\":%lu,\"target_s\":%lu,"
           "\"focus_s\":%lu,\"completion_ratio\":%.3f,\"result\":\"%s\",\"end_reason\":\"%s\","
           "\"away_count\":%lu,\"away_total_s\":%lu,\"first_away_min\":%s,\"dock_lift_count\":%lu,"
           "\"avg_light_raw\":%lu,\"baseline_cm\":%.1f,\"demo\":%s}",
           DEVICE_ID, sessionId, sessionStartEpoch, epochNow(), (unsigned long)(s.targetMs / 1000),
           (unsigned long)(s.focusMs / 1000), s.targetMs ? (float)s.focusMs / s.targetMs : 0.0f,
           FocusCore::resultName(s.result), FocusCore::endReasonName(s.endReason),
           (unsigned long)s.awayCount, (unsigned long)(s.awayTotalMs / 1000), firstAway,
           (unsigned long)s.dockLiftCount, (unsigned long)s.avgLight(), s.baselineCm,
           demoMode ? "true" : "false");
  // Kirim langsung bila tersambung dan antrean kosong (agar urutan sesi tetap); selain itu masuk antrean.
  bool sent = false;
  if (mqtt.connected() && outboxCount == 0) sent = mqtt.publish(TOPIC_BASE "/session", p, false);
  if (!sent) queueSession(p);
  Serial.print("[SESSION] ");
  Serial.println(p);
}

// --- baca nilai sederhana dari JSON perintah, misal {"action":"start","duration_min":3}
// jsonFind(): mengambil nilai sebuah kunci dari teks JSON sederhana tanpa library tambahan.
//   Contoh: jsonFind("{\"action\":\"start\"}", "action", out) -> out = "start".
//   Mengembalikan false bila kunci tidak ada.
bool jsonFind(const String& js, const char* key, String& out) {
  String k = String("\"") + key + "\"";
  int i = js.indexOf(k);
  if (i < 0) return false;
  i = js.indexOf(':', i + k.length());
  if (i < 0) return false;
  i++;
  while (i < (int)js.length() && (js[i] == ' ' || js[i] == '"')) i++;
  int j = i;
  while (j < (int)js.length() && js[j] != ',' && js[j] != '}' && js[j] != '"') j++;
  out = js.substring(i, j);
  out.trim();
  return true;
}

// applyMode(): mengganti mode waktu. demo = true memakai parameter cepat untuk uji/video,
//   false memakai parameter normal (sesi 25 menit, konfirmasi pergi 30 detik, dst.).
void applyMode(bool demo) {
  demoMode = demo;
  core.p = demo ? paramsDemo() : paramsNormal();
  Serial.printf("[MODE] %s, durasi sesi %lu menit\n", demo ? "DEMO (dipercepat)" : "NORMAL",
                (unsigned long)(core.p.sessionMs / 60000));
}

// --- Atur durasi sesi (timer). Sumber: tombol END, Serial Monitor, MQTT/aplikasi.
// setDurationMin(): mengatur durasi sesi berikutnya (0,5-180 menit).
//   Dipakai oleh tiga jalur: tombol END, Serial Monitor, dan perintah MQTT dari dashboard/aplikasi.
//   Ditolak saat sesi sedang berjalan. Setelah berhasil: bunyi singkat dan kirim status baru.
void setDurationMin(float minutes, const char* source) {
  if (core.inSession()) { Serial.println("[TIMER] tidak bisa diubah saat sesi berjalan"); return; }
  if (minutes < 0.5f) minutes = 0.5f;
  if (minutes > 180.0f) minutes = 180.0f;
  core.p.sessionMs = (uint32_t)(minutes * 60000.0f);
  Serial.printf("[TIMER] durasi sesi = %.1f menit (dari %s)\n", minutes, source);
  PLAY(TONE_SET);
  publishStatus();
}

// cycleDuration(): dipanggil saat tombol END ditekan dalam kondisi READY.
//   Memilih durasi berikutnya dari DURATION_PRESETS (1, 2, 5, ... 60 menit), lalu kembali ke awal.
void cycleDuration() {   // pilihan berikutnya, kembali ke awal setelah yang terakhir
  uint32_t curMin = core.p.sessionMs / 60000;
  const int n = sizeof(DURATION_PRESETS) / sizeof(DURATION_PRESETS[0]);
  uint16_t next = DURATION_PRESETS[0];
  for (int i = 0; i < n; i++) {
    if (DURATION_PRESETS[i] > curMin) { next = DURATION_PRESETS[i]; break; }
  }
  setDurationMin(next, "tombol END");
}

// --- Satu pintu untuk semua perintah JSON (dari MQTT maupun Serial Monitor)
//     {"action":"start","duration_min":25}   {"action":"stop"}
//     {"action":"set_duration","duration_min":25}   {"action":"set_mode","demo":true}
// handleCommand(): menjalankan perintah JSON, baik dari MQTT maupun Serial Monitor.
//   start -> mulai sesi (opsional durasi), stop -> hentikan sesi, set_duration -> atur timer,
//   set_mode -> ganti demo/normal. Start/stop hanya ditandai di sini dan dieksekusi di loop().
void handleCommand(const String& js, const char* source) {
  String action, val;
  if (!jsonFind(js, "action", action)) { Serial.println("[CMD] format salah, butuh \"action\""); return; }
  if (action == "start") {
    pendingCmdStart = true;
    pendingCmdDurationMs = jsonFind(js, "duration_min", val) ? (uint32_t)(val.toFloat() * 60000.0f) : 0;
  } else if (action == "stop") {
    pendingCmdStop = true;
  } else if (action == "set_duration") {
    if (jsonFind(js, "duration_min", val)) setDurationMin(val.toFloat(), source);
  } else if (action == "set_mode") {
    if (core.inSession()) { Serial.println("[CMD] set_mode ditolak: sesi sedang berjalan"); return; }
    if (jsonFind(js, "demo", val)) applyMode(val == "true");
    publishStatus();
  } else {
    Serial.printf("[CMD] action tidak dikenal: %s\n", action.c_str());
  }
}

// onMqttMessage(): dipanggil PubSubClient saat ada pesan masuk di topic cmd.
//   Mengubah payload menjadi teks lalu meneruskannya ke handleCommand().
void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  String js;
  for (unsigned int i = 0; i < len; i++) js += (char)payload[i];
  Serial.printf("[CMD] %s %s\n", topic, js.c_str());
  handleCommand(js, "MQTT");
}

// --- Perintah lewat Serial Monitor (ketik lalu Enter) - pengganti aplikasi saat offline
// printHelp(): menampilkan daftar perintah yang bisa diketik di Serial Monitor.
void printHelp() {
  Serial.println("[HELP] Perintah Serial Monitor:");
  Serial.println("  25          -> atur timer 25 menit (angka 0.5 - 180)");
  Serial.println("  start       -> mulai sesi (HP harus di dock)  | start 10 -> mulai 10 menit");
  Serial.println("  stop        -> hentikan sesi (END_PAKSA)");
  Serial.println("  demo/normal -> ganti mode waktu");
  Serial.println("  putus       -> simulasi WiFi putus (uji LED NET & antrean sesi) | sambung -> sambung lagi");
  Serial.println("  {\"action\":\"set_duration\",\"duration_min\":25} -> format JSON seperti aplikasi");
}

// setNetSimDown(): memutus / menyambung WiFi secara sengaja dari Serial Monitor, untuk menguji
//   LED NET dan antrean sesi di Wokwi (di Wokwi WiFi tidak bisa dicabut seperti router sungguhan).
void setNetSimDown(bool down) {
  if (!USE_MQTT) { Serial.println("[NET] mode offline, perintah diabaikan"); return; }
  netSimDown = down;
  if (down) {
    mqtt.disconnect();
    WiFi.disconnect();
    Serial.println("[NET] simulasi: WiFi diputus. Alat tetap berjalan, sesi disimpan di antrean");
  } else {
    WiFi.begin(WIFI_SSID, WIFI_PASS, 6);
    lastWifiRetry = millis();
    Serial.println("[NET] simulasi: menyambung WiFi lagi...");
  }
}

String serialLine;
uint32_t serialLastChar = 0;

// processSerialLine(): menerjemahkan satu baris ketikan Serial Monitor menjadi perintah.
//   Contoh: "25" -> timer 25 menit, "start 10" -> mulai 10 menit, "stop", "demo", "normal",
//   atau teks JSON yang sama dengan perintah aplikasi.
void processSerialLine() {
  String line = serialLine;
  serialLine = "";
  line.trim();
  if (line.length() == 0) return;
  Serial.printf("[SERIAL] > %s\n", line.c_str());
  if (line[0] == '{') { handleCommand(line, "Serial"); return; }
  String low = line;
  low.toLowerCase();
  if (low == "help" || low == "?") printHelp();
  else if (low == "start") handleCommand("{\"action\":\"start\"}", "Serial");
  else if (low.startsWith("start ")) {
    String js = String("{\"action\":\"start\",\"duration_min\":") + low.substring(6) + "}";
    handleCommand(js, "Serial");
  }
  else if (low == "stop") handleCommand("{\"action\":\"stop\"}", "Serial");
  else if (low == "demo") handleCommand("{\"action\":\"set_mode\",\"demo\":true}", "Serial");
  else if (low == "normal") handleCommand("{\"action\":\"set_mode\",\"demo\":false}", "Serial");
  else if (low == "putus") setNetSimDown(true);
  else if (low == "sambung") setNetSimDown(false);
  else if (line.toFloat() > 0) setDurationMin(line.toFloat(), "Serial");
  else Serial.println("[SERIAL] perintah tidak dikenal, ketik help");
}

// readSerialCommands(): mengumpulkan karakter dari Serial Monitor sampai Enter,
//   lalu memprosesnya. Bila tidak ada Enter, baris diproses setelah 300 ms tanpa ketikan.
void readSerialCommands(uint32_t now) {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    serialLastChar = now;
    if (c == '\n' || c == '\r') processSerialLine();
    else if (serialLine.length() < 160) serialLine += c;
  }
  // jaga-jaga bila Serial Monitor tidak mengirim Enter
  if (serialLine.length() > 0 && now - serialLastChar > 300) processSerialLine();
}

// mqttConfigured(): memastikan MQTT_HOST dan MQTT_USER sudah diganti dari contoh "ISI_...".
bool mqttConfigured() {   // kredensial broker MQTT sudah diisi?
  return strstr(MQTT_HOST, "ISI_") == nullptr && strstr(MQTT_USER, "ISI_") == nullptr;
}

// maintainNetwork(): menjaga koneksi WiFi dan MQTT setiap putaran loop.
//   Saat WiFi baru tersambung: sinkronkan jam lewat NTP. Bila MQTT terputus: coba sambung
//   ulang tiap 5 detik, lalu subscribe topic cmd dan kirim status. Bila tersambung: proses
//   pesan masuk (mqtt.loop) dan kirim sesi yang tertunda di antrean.
//   Bila WiFi putus: alat tetap jalan, WiFi dicoba sambung ulang tiap 10 detik.
//   Tidak dijalankan sama sekali di mode offline.
void maintainNetwork(uint32_t now) {
  if (!USE_MQTT) return;                 // mode offline: lewati WiFi & MQTT
  bool wifiUp = !netSimDown && WiFi.status() == WL_CONNECTED;
  if (wifiUp && !wifiWasConnected) {
    Serial.printf("[WIFI] tersambung, IP %s\n", WiFi.localIP().toString().c_str());
    configTime(0, 0, "pool.ntp.org", "time.google.com");   // epoch UTC untuk timestamp
  }
  if (!wifiUp && wifiWasConnected) Serial.println("[WIFI] terputus. Alat tetap berjalan, menyambung ulang...");
  wifiWasConnected = wifiUp;
  if (!timeSynced && epochNow() > 0) { timeSynced = true; Serial.println("[TIME] jam tersinkron"); }
  if (mqttWasConnected && !mqtt.connected()) {
    Serial.println("[MQTT] terputus. Data sesi akan disimpan di antrean sampai tersambung lagi");
    mqttWasConnected = false;
  }
  if (!wifiUp) {
    if (!netSimDown && now - lastWifiRetry >= 10000) { lastWifiRetry = now; WiFi.reconnect(); }
    return;
  }

  if (!mqtt.connected()) {
    if (now - lastMqttAttempt < 5000 && lastMqttAttempt != 0) return;   // coba ulang tiap 5 detik
    lastMqttAttempt = now;
    if (!mqttConfigured()) {
      Serial.println("[MQTT] MQTT_HOST / MQTT_USER / MQTT_PASS belum diisi (bagian 1. KONFIGURASI)");
      return;
    }
    char clientId[32];
    snprintf(clientId, sizeof(clientId), "focuslamp-%s-%04X", DEVICE_ID, (unsigned)random(0x10000));
    Serial.printf("[MQTT] menyambung ke %s:%d ...\n", MQTT_HOST, MQTT_PORT);
    if (mqtt.connect(clientId, MQTT_USER, MQTT_PASS)) {
      Serial.println("[MQTT] tersambung");
      mqttWasConnected = true;
      mqtt.subscribe(TOPIC_BASE "/cmd", 1);
      publishStatus();
      flushOutbox();                       // kirim sesi yang tertunda selama putus
    } else {
      Serial.printf("[MQTT] gagal, kode %d. Alat tetap jalan offline, coba lagi 5 dtk\n", mqtt.state());
    }
  } else {
    mqtt.loop();
    if (outboxCount > 0 && now - lastFlush >= 5000) { lastFlush = now; flushOutbox(); }
  }
}

// ledSelfTest(): tes lampu indikator saat alat menyala. LED DOCK dan LED NET menyala bersamaan
//   selama 0,6 detik, sehingga kabel kedua LED bisa dicek tanpa menunggu WiFi/MQTT.
//   Bila salah satu tidak ikut menyala, periksa jalur pin -> resistor -> anoda, katoda -> GND.
void ledSelfTest() {
  digitalWrite(PIN_LED_DOCK, HIGH);
  digitalWrite(PIN_LED_NET, HIGH);
  Serial.println("[LED] tes indikator: LED DOCK dan LED NET menyala 0,6 detik");
  delay(600);
  digitalWrite(PIN_LED_DOCK, LOW);
  digitalWrite(PIN_LED_NET, LOW);
}

// ============================ 10. SETUP ====================================
// setup(): dijalankan sekali saat ESP32 menyala.
//   Menyiapkan Serial, mode pin, interrupt tombol, NeoPixel, OLED, state machine,
//   lalu WiFi dan MQTT over TLS (bila USE_MQTT = true).
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== FocusLamp start ===");

  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_DOCK, INPUT_PULLUP);
  pinMode(PIN_BTN_START, INPUT_PULLUP);
  pinMode(PIN_BTN_END, INPUT_PULLUP);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_LED_DOCK, OUTPUT);
  digitalWrite(PIN_LED_DOCK, LOW);
  pinMode(PIN_LED_NET, OUTPUT);
  digitalWrite(PIN_LED_NET, LOW);
  ledSelfTest();
  analogReadResolution(12);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_START), onStartIsr, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_END), onEndIsr, FALLING);

  ring.begin();
  ring.clear();
  ring.show();

  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (!oledOk) Serial.println("[OLED] tidak ditemukan, cek wiring SDA/SCL");
  else { display.clearDisplay(); display.setTextColor(SSD1306_WHITE); display.setTextSize(2);
         display.setCursor(4, 24); display.print("FocusLamp"); display.display(); }

  core.begin(demoMode ? paramsDemo() : paramsNormal(), millis());
  Serial.printf("[MODE] %s, durasi sesi %lu menit\n", demoMode ? "DEMO (dipercepat)" : "NORMAL",
                (unsigned long)(core.p.sessionMs / 60000));
  Serial.println("[INFO] Atur timer: tombol END saat READY, atau ketik angka menit di Serial Monitor (ketik help)");

  if (USE_MQTT) {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);           // ESP32 menyambung ulang WiFi sendiri bila sinyal hilang
    WiFi.begin(WIFI_SSID, WIFI_PASS, 6);   // channel 6 mempercepat koneksi di Wokwi
    tlsClient.setInsecure();               // tetap terenkripsi TLS; sertifikat tidak divalidasi (simplifikasi)
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setCallback(onMqttMessage);
    mqtt.setBufferSize(768);
    mqtt.setKeepAlive(30);
  } else {
    Serial.println("[NET] Mode OFFLINE (USE_MQTT = false): data hanya ke Serial Monitor");
  }
}

// ============================ 11. LOOP =====================================
// loop(): dijalankan berulang sekitar setiap 5 ms.
//   Urutan: jaga koneksi -> perintah Serial -> baca sensor -> kumpulkan input
//   -> state machine (core.update) -> tangani event (Serial, MQTT, buzzer) -> kirim status bila
//   berubah -> kirim telemetri berkala -> perbarui lampu, LED dock, LED net, OLED, dan buzzer.
void loop() {
  uint32_t now = millis();
  maintainNetwork(now);
  now = millis();   // jaringan bisa makan waktu, ambil ulang

  // --- perintah dari Serial Monitor ---
  readSerialCommands(now);

  // --- baca sensor ---
  readDock(now);
  lightRaw = analogRead(PIN_LDR);
  brightness = brightnessFromLight(lightRaw, LDR_BRIGHT_IS_LOW);
  if (now - lastDistRead >= 200) {
    lastDistRead = now;
    distBuf[distIdx] = readUltrasonicCm();
    distIdx = (distIdx + 1) % 5;
    distanceCm = median5(distBuf);
  }

  // --- kumpulkan input untuk logika ---
  FocusInputs in;
  in.dock = dockOn;
  in.distanceCm = distanceCm;
  in.lightRaw = lightRaw;
  noInterrupts();
  in.startPressed = startFlag; startFlag = false;
  in.endPressed = endFlag; endFlag = false;
  interrupts();
  in.cmdStart = pendingCmdStart; in.cmdDurationMs = pendingCmdDurationMs;
  in.cmdStop = pendingCmdStop;
  pendingCmdStart = pendingCmdStop = false; pendingCmdDurationMs = 0;

  // tombol END saat READY = ganti durasi timer (saat sesi berjalan tetap = END PAKSA)
  if (in.endPressed && core.state == ST_READY) { in.endPressed = false; cycleDuration(); }

  core.update(now, in);

  // --- tangani event: Serial, MQTT, suara ---
  EventType e;
  while (core.popEvent(e)) {
    if (e == EV_SESSION_START) {
      sessionStartEpoch = epochNow();
      snprintf(sessionId, sizeof(sessionId), "%s-%lu", DEVICE_ID,
               sessionStartEpoch ? sessionStartEpoch : (unsigned long)(now / 1000));
    }
    Serial.printf("[%8lu] [EVENT] %s dist=%.1fcm\n", (unsigned long)now, FocusCore::eventName(e), distanceCm);
    publishEvent(e);
    switch (e) {
      case EV_SESSION_START: PLAY(TONE_START); break;
      case EV_AWAY_START: PLAY(TONE_AWAY); break;
      case EV_DOCK_LIFTED: PLAY(TONE_DOCK); break;
      case EV_START_REJECTED: PLAY(TONE_REJECT); break;
      case EV_SESSION_END:
        if (core.last.result == RES_SELESAI) PLAY(TONE_DONE); else PLAY(TONE_END);
        publishSession();
        break;
      default: break;
    }
  }

  // --- status berubah? kirim status (retain) ---
  if (core.state != lastState || core.paused != lastPaused || core.dockLifted != lastLifted) {
    lastState = core.state; lastPaused = core.paused; lastLifted = core.dockLifted;
    publishStatus();
  }

  // --- telemetri berkala + log Serial ---
  uint32_t interval = demoMode ? 2000 : 5000;
  if (now - lastTelemetry >= interval) {
    lastTelemetry = now;
    publishTelemetry();
    Serial.printf("[%8lu] %s dist=%.1f hadir=%d dock=%d light=%d bright=%d sisa=%lus\n",
                  (unsigned long)now, core.stateName(), distanceCm, core.present(), dockOn, lightRaw,
                  brightness, (unsigned long)(core.remainingMs() / 1000));
  }

  // --- output ---
  if (now - lastLamp >= 50) { lastLamp = now; updateLamp(now); }
  updateDockLed(now);
  updateNetLed(now);
  if (now - lastOled >= 250) { lastOled = now; updateOled(now); }
  updateBuzzer(now);
  delay(5);
}
