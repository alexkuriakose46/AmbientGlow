/*
 * ═══════════════════════════════════════════════════════════
 *  AmbientGlow — ESP32 LED Controller Firmware
 * ═══════════════════════════════════════════════════════════
 *
 *  Receives LED color data from the PC app via USB serial
 *  using the Adalight protocol and drives a WS2812B LED strip.
 *
 *  Hardware:
 *    - ESP32 (any variant: DevKit, WROOM, S3, etc.)
 *    - WS2812B LED strip connected to DATA_PIN
 *    - 5V power supply for LEDs (do NOT power many LEDs from USB)
 *    - Common GND between ESP32 and LED power supply
 *
 *  Wiring:
 *    ESP32 GPIO 13  →  WS2812B Data In
 *    ESP32 GND      →  WS2812B GND
 *    5V PSU +       →  WS2812B 5V
 *    5V PSU GND     →  WS2812B GND  &  ESP32 GND
 *
 *  Libraries needed:
 *    - FastLED (install via Arduino Library Manager)
 *
 *  Upload:
 *    Board: "ESP32 Dev Module" (or your specific ESP32 variant)
 *    Upload Speed: 921600
 *    Flash Frequency: 80MHz
 */

#include <FastLED.h>

// ── Configuration ─────────────────────────────────────────
#define DATA_PIN       13       // GPIO pin for WS2812B data
#define MAX_LEDS       300      // Maximum supported LEDs
#define SERIAL_BAUD    115200   // Must match PC app
#define SERIAL_TIMEOUT 3000     // ms before fallback effect
#define LED_TYPE       WS2812B
#define COLOR_ORDER    GRB      // Most WS2812B strips use GRB

// ── Adalight Protocol Constants ───────────────────────────
#define MAGIC_0  'A'
#define MAGIC_1  'd'
#define MAGIC_2  'a'

// ── Globals ───────────────────────────────────────────────
CRGB leds[MAX_LEDS];
int  numLeds = 0;
unsigned long lastDataTime = 0;
bool dataReceived = false;

// ── State machine for header parsing ──────────────────────
enum ParseState {
    WAIT_MAGIC_0,
    WAIT_MAGIC_1,
    WAIT_MAGIC_2,
    WAIT_HI,
    WAIT_LO,
    WAIT_CHECKSUM,
    READ_DATA
};

ParseState state = WAIT_MAGIC_0;
uint8_t hiCount, loCount, checksum;
int expectedLeds;
int bytesRead;
int totalBytes;

// ── Fallback rainbow effect (when no PC data) ────────────
uint8_t fallbackHue = 0;

void fallbackRainbow() {
    if (numLeds == 0) {
        // Show a default pattern on first 60 LEDs
        numLeds = 60;
    }
    fill_rainbow(leds, numLeds, fallbackHue, 7);
    FastLED.show();
    fallbackHue++;
    delay(20);
}

// ── Startup animation ────────────────────────────────────
void startupAnimation() {
    // Quick color wipe to confirm LEDs work
    int testCount = min(numLeds > 0 ? numLeds : 60, MAX_LEDS);

    // Red wipe
    for (int i = 0; i < testCount; i++) {
        leds[i] = CRGB::Red;
        if (i > 0) leds[i - 1] = CRGB::Black;
        FastLED.show();
        delay(5);
    }
    // Green wipe
    for (int i = 0; i < testCount; i++) {
        leds[i] = CRGB::Green;
        if (i > 0) leds[i - 1] = CRGB::Black;
        FastLED.show();
        delay(5);
    }
    // Blue wipe
    for (int i = 0; i < testCount; i++) {
        leds[i] = CRGB::Blue;
        if (i > 0) leds[i - 1] = CRGB::Black;
        FastLED.show();
        delay(5);
    }
    // All off
    fill_solid(leds, testCount, CRGB::Black);
    FastLED.show();
}

// ══════════════════════════════════════════════════════════
//  Setup
// ══════════════════════════════════════════════════════════
void setup() {
    Serial.begin(SERIAL_BAUD);

    // Initialize FastLED
    FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, MAX_LEDS)
        .setCorrection(TypicalLEDStrip)
        .setDither(BINARY_DITHER);

    // Set initial brightness
    FastLED.setBrightness(255);

    // Clear all LEDs
    fill_solid(leds, MAX_LEDS, CRGB::Black);
    FastLED.show();

    // Play startup animation
    startupAnimation();

    lastDataTime = millis();

    // Send ready signal
    Serial.println("AmbientGlow Ready");
}

// ══════════════════════════════════════════════════════════
//  Main Loop
// ══════════════════════════════════════════════════════════
void loop() {
    // Process incoming serial data
    while (Serial.available() > 0) {
        uint8_t b = Serial.read();

        switch (state) {
            case WAIT_MAGIC_0:
                if (b == MAGIC_0) state = WAIT_MAGIC_1;
                break;

            case WAIT_MAGIC_1:
                if (b == MAGIC_1) state = WAIT_MAGIC_2;
                else state = WAIT_MAGIC_0;
                break;

            case WAIT_MAGIC_2:
                if (b == MAGIC_2) state = WAIT_HI;
                else state = WAIT_MAGIC_0;
                break;

            case WAIT_HI:
                hiCount = b;
                state = WAIT_LO;
                break;

            case WAIT_LO:
                loCount = b;
                state = WAIT_CHECKSUM;
                break;

            case WAIT_CHECKSUM:
                checksum = b;
                // Verify checksum
                if (checksum == (hiCount ^ loCount ^ 0x55)) {
                    expectedLeds = ((int)hiCount << 8 | (int)loCount) + 1;
                    if (expectedLeds > MAX_LEDS) {
                        expectedLeds = MAX_LEDS;
                    }
                    numLeds = expectedLeds;
                    bytesRead = 0;
                    totalBytes = expectedLeds * 3;
                    state = READ_DATA;
                } else {
                    // Bad checksum, restart
                    state = WAIT_MAGIC_0;
                }
                break;

            case READ_DATA:
                {
                    int ledIndex = bytesRead / 3;
                    int channel  = bytesRead % 3;

                    if (ledIndex < MAX_LEDS) {
                        switch (channel) {
                            case 0: leds[ledIndex].r = b; break;
                            case 1: leds[ledIndex].g = b; break;
                            case 2: leds[ledIndex].b = b; break;
                        }
                    }

                    bytesRead++;

                    if (bytesRead >= totalBytes) {
                        // Full frame received — show it!
                        FastLED.show();
                        lastDataTime = millis();
                        dataReceived = true;
                        state = WAIT_MAGIC_0;
                    }
                }
                break;
        }
    }

    // If no data received for SERIAL_TIMEOUT ms, show fallback
    if (millis() - lastDataTime > SERIAL_TIMEOUT) {
        fallbackRainbow();
    }
}
