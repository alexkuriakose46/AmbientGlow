<div align="center">
  
# 🌟 AmbientGlow
**Turn your workspace into a reactive light show.**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg?logo=python&logoColor=white)](#)
[![ESP32](https://img.shields.io/badge/ESP32-Arduino-00979D.svg?logo=arduino&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#)

</div>

<br/>

**AmbientGlow** is a lightweight, super-fast system that perfectly syncs your PC screen to your desk's LED strips. Whether you're gaming, watching a movie, or just coding late at night, AmbientGlow expands your monitor's colors onto your walls in real-time.

It's built with a beautiful Python GUI for your PC and a blazing-fast C++ firmware for your ESP32 microcontroller. 

---

## ✨ Features

- **📺 True Screen Sync:** Captures your screen edges with zero-copy processing for ultimate speed (~2ms per frame).
- **🎨 Built-in Animations:** Not feeling the screen sync? Switch to Rainbow, Fire, Breathing, or Color Wave modes to set the vibe.
- **⚡ Zero Lag:** Uses the Adalight protocol over USB serial for instant reaction times.
- **🖥️ Beautiful UI:** A sleek, dark-mode desktop app to control everything.
- **🔌 Easy Setup:** Just 3 wires (Data, 5V, Ground) and you're good to go!

---

## 🛠️ What You Need

To bring this to life, you'll need:
1. **An ESP32** (Any model: DevKit, WROOM, S3, etc.)
2. **A WS2812B LED Strip** (Any length!)
3. **A 5V Power Supply** (To power the LEDs — don't run too many LEDs directly off the ESP32!)
4. **3 Jumper Wires** 

---

## 🚀 Quick Start Guide

### 1. The Hardware (Wiring)
It's incredibly simple. Just connect these three wires:
- **Data (Green):** LED `DIN` ➡️ ESP32 `GPIO 23` (or whichever pin you choose!)
- **Ground (Black):** LED `GND` ➡️ ESP32 `GND` **AND** Power Supply `GND`
- **Power (Red):** LED `5V` ➡️ Power Supply `5V`

> ⚠️ **Important:** The Ground (GND) wire must connect the ESP32, the LED strip, and the Power Supply together. Without a shared ground, the lights will flicker wildly!

### 2. The Brain (ESP32 Firmware)
1. Open `esp32_firmware/ambient_light/ambient_light.ino` in the Arduino IDE.
2. Install the **FastLED** library from the Arduino Library Manager.
3. Change the `DATA_PIN` at the top of the code to match where you plugged in your data wire (e.g., `23`).
4. Change `NUM_LEDS` to match the number of LEDs on your strip.
5. Click **Upload**! You'll see a quick Red/Green/Blue flash on your LEDs to confirm it worked.

### 3. The App (Python)
1. Make sure you have Python installed.
2. Open your terminal in this folder and install the requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Launch the app!
   ```bash
   python app.py
   ```
4. Pick your ESP32's COM port, click **Connect**, select **Ambient Mode**, and hit **START**!

---

## 📸 Sneak Peek

The desktop app lets you tweak everything:
- Set your exact LED layout (Top, Bottom, Left, Right).
- Adjust brightness, smoothing, and target FPS.
- Preview the colors directly in the app before they even hit your LEDs!

---

## 🤝 Contributing
Got an idea for a cool new lighting effect? Found a bug? Feel free to open an issue or submit a pull request. Let's make AmbientGlow even better together!

## 📜 License
This project is open-source and licensed under the MIT License. Go wild, build cool stuff, and light up your room.
