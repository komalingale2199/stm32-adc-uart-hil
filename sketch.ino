// TMP36 Temperature Sensor Simulation
// Board: ST Nucleo L031K6 | Framework: Arduino
// Reads a potentiometer on A0 as a TMP36 analog proxy

// ── Constants ────────────────────────────────────────────────
const int    SENSOR_PIN   = A0;       // Analog pin connected to potentiometer / TMP36
const int    BAUD_RATE    = 115200;   // Serial baud rate
const int    INTERVAL_MS  = 500;      // Sampling interval in milliseconds
const float  VREF_MV      = 3300.0;  // Reference voltage in millivolts (3.3 V board)
const int    ADC_MAX      = 1023;     // 10-bit ADC max value
const int    MV_LOW       = 100;      // Lower bound for "OK" status (mV)
const int    MV_HIGH      = 3200;     // Upper bound for "OK" status (mV)

// ── State ─────────────────────────────────────────────────────
bool ledState = false;                // Tracks current LED_BUILTIN state

// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(BAUD_RATE);           // Start serial at 115200 baud
  pinMode(LED_BUILTIN, OUTPUT);      // Configure onboard LED as output
  pinMode(SENSOR_PIN, INPUT);        // Configure A0 as analog input (optional, good practice)
}

// ─────────────────────────────────────────────────────────────
void loop() {
  static unsigned long lastTime = 0;         // Stores timestamp of last reading

  unsigned long now = millis();              // Current uptime in milliseconds

  // Only sample every INTERVAL_MS milliseconds (non-blocking)
  if (now - lastTime < INTERVAL_MS) return;
  lastTime = now;                            // Update last sample timestamp

  // ── 1. Read ADC ──────────────────────────────────────────
  int raw = analogRead(SENSOR_PIN);          // Raw 10-bit ADC value (0–1023)

  // ── 2. Convert to millivolts ─────────────────────────────
  int mv = (int)(raw * VREF_MV / ADC_MAX);  // Scale raw value to mV (0–3300)

  // ── 3. Convert mV to temperature (TMP36 formula) ─────────
  // TMP36: 500 mV offset at 0 °C, 10 mV/°C slope
  float temp = (mv - 500) / 10.0;           // Temperature in degrees Celsius

  // ── 4. Determine status ───────────────────────────────────
  // "OK" when voltage is within the sensor's valid output range
  const char* status = (mv >= MV_LOW && mv <= MV_HIGH) ? "OK" : "OUT_OF_RANGE";

  // ── 5. Send JSON over Serial ──────────────────────────────
  Serial.print("{\"ts\":");     // Opening brace + timestamp key
  Serial.print(now);            // millis() value
  Serial.print(",\"raw\":");    // Raw ADC key
  Serial.print(raw);            // Raw ADC value
  Serial.print(",\"mv\":");     // Millivolts key
  Serial.print(mv);             // Millivolts value
  Serial.print(",\"temp\":");   // Temperature key
  Serial.print(temp, 1);        // Temperature value, 1 decimal place
  Serial.print(",\"status\":\""); // Status key (opening quote)
  Serial.print(status);         // "OK" or "OUT_OF_RANGE"
  Serial.println("\"}");        // Closing quote, brace, and newline

  // ── 6. Toggle onboard LED ─────────────────────────────────
  ledState = !ledState;                      // Flip LED state
  digitalWrite(LED_BUILTIN, ledState);       // Apply new LED state
}
