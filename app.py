"""
AmbientGlow — Real-time Screen Ambient Lighting Controller
===========================================================
Captures edge colors from your screen and streams them to an ESP32
driving a WS2812B (NeoPixel) LED strip via USB serial.

Usage:
    pip install -r requirements.txt
    python app.py

Protocol (Adalight-compatible):
    Header : 'A' 'd' 'a'  (3 bytes)
    Hi     : (num_leds-1) >> 8
    Lo     : (num_leds-1) & 0xFF
    Chk    : Hi ^ Lo ^ 0x55
    Data   : R G B × num_leds

Author: AmbientGlow
"""

from __future__ import annotations

import colorsys
import math
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, ttk
from typing import List, Optional, Tuple

import mss
import numpy as np
import serial
import serial.tools.list_ports

# ══════════════════════════════════════════════════════════════
#  Constants & Lookup Tables
# ══════════════════════════════════════════════════════════════

ADALIGHT_MAGIC = b"Ada"
GAMMA = 2.5
GAMMA_LUT = np.array(
    [int(pow(i / 255.0, GAMMA) * 255 + 0.5) for i in range(256)],
    dtype=np.uint8,
)

# Default LED layout (clockwise from top-left)
DEFAULT_TOP = 28
DEFAULT_RIGHT = 16
DEFAULT_BOTTOM = 28
DEFAULT_LEFT = 16

# UI Colors (dark theme)
BG = "#0f0f1a"
BG_CARD = "#1a1a2e"
BG_INPUT = "#16213e"
FG = "#e0e0e0"
FG_DIM = "#888899"
ACCENT = "#00d4ff"
ACCENT2 = "#7b2ff7"
DANGER = "#ff4757"
SUCCESS = "#2ed573"

# ══════════════════════════════════════════════════════════════
#  Screen Capture Engine
# ══════════════════════════════════════════════════════════════


class ScreenCapture:
    """High-performance screen edge color sampler using mss."""

    def __init__(self) -> None:
        self.sct = mss.MSS()
        self.monitor_index: int = 1  # primary
        self.downsample: int = 4  # capture every Nth pixel
        self.edge_depth_pct: float = 0.10  # 10 % of shortest edge

    # ── public helpers ──────────────────────────────────────

    def get_monitors(self) -> list[dict]:
        """Return list of available monitors (skip the 'all' virtual one)."""
        return self.sct.monitors[1:]

    def set_monitor(self, index: int) -> None:
        self.monitor_index = max(1, min(index + 1, len(self.sct.monitors) - 1))

    # ── main sampling method ────────────────────────────────

    def capture_edge_colors(
        self,
        n_top: int,
        n_right: int,
        n_bottom: int,
        n_left: int,
    ) -> list[tuple[int, int, int]]:
        """
        Capture the screen, sample edge bands, and return averaged RGB
        colors for each LED position going clockwise from top-left.
        """
        mon = self.sct.monitors[self.monitor_index]
        raw = np.array(self.sct.grab(mon))  # BGRA uint8

        # Downsample for speed (stride-based, zero-copy)
        ds = self.downsample
        img = raw[::ds, ::ds, :3]  # drop alpha, keep BGR
        h, w, _ = img.shape
        depth = max(int(min(h, w) * self.edge_depth_pct), 2)

        colors: list[tuple[int, int, int]] = []

        # Top edge — left → right
        if n_top > 0:
            band = img[:depth, :, :]
            colors.extend(self._avg_segments(band, n_top, axis=1, bgr=True))

        # Right edge — top → bottom
        if n_right > 0:
            band = img[:, w - depth :, :]
            colors.extend(self._avg_segments(band, n_right, axis=0, bgr=True))

        # Bottom edge — right → left
        if n_bottom > 0:
            band = img[h - depth :, :, :]
            segs = self._avg_segments(band, n_bottom, axis=1, bgr=True)
            segs.reverse()
            colors.extend(segs)

        # Left edge — bottom → top
        if n_left > 0:
            band = img[:, :depth, :]
            segs = self._avg_segments(band, n_left, axis=0, bgr=True)
            segs.reverse()
            colors.extend(segs)

        return colors

    # ── internals ───────────────────────────────────────────

    @staticmethod
    def _avg_segments(
        band: np.ndarray,
        n: int,
        axis: int,
        bgr: bool = True,
    ) -> list[tuple[int, int, int]]:
        """Split *band* along *axis* into *n* segments; return mean RGB."""
        length = band.shape[axis]
        seg = length // max(n, 1)
        out: list[tuple[int, int, int]] = []
        for i in range(n):
            s, e = i * seg, (i + 1) * seg
            if axis == 1:
                region = band[:, s:e, :]
            else:
                region = band[s:e, :, :]
            avg = region.mean(axis=(0, 1))
            if bgr:
                out.append((int(avg[2]), int(avg[1]), int(avg[0])))
            else:
                out.append((int(avg[0]), int(avg[1]), int(avg[2])))
        return out


# ══════════════════════════════════════════════════════════════
#  Serial Communication Handler
# ══════════════════════════════════════════════════════════════


class SerialHandler:
    """Thread-safe serial communication with ESP32."""

    def __init__(self) -> None:
        self.port: Optional[serial.Serial] = None
        self.connected: bool = False
        self._lock = threading.Lock()

    @staticmethod
    def list_ports() -> list[str]:
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port_name: str, baud: int = 115200) -> bool:
        with self._lock:
            try:
                self.port = serial.Serial(port_name, baud, timeout=1)
                time.sleep(2)  # wait for ESP32 reset
                self.connected = True
                return True
            except Exception:
                self.connected = False
                return False

    def disconnect(self) -> None:
        with self._lock:
            if self.port and self.port.is_open:
                self.port.close()
            self.connected = False

    def send_colors(
        self,
        colors: list[tuple[int, int, int]],
        brightness: int = 255,
    ) -> bool:
        """Send LED data using Adalight protocol."""
        if not self.connected or not self.port:
            return False

        n = len(colors)
        hi = (n - 1) >> 8
        lo = (n - 1) & 0xFF
        chk = hi ^ lo ^ 0x55

        # Build packet
        buf = bytearray(6 + n * 3)
        buf[0:3] = ADALIGHT_MAGIC
        buf[3] = hi
        buf[4] = lo
        buf[5] = chk

        idx = 6
        bri = brightness / 255.0
        for r, g, b in colors:
            buf[idx] = GAMMA_LUT[min(255, max(0, int(r * bri)))]
            buf[idx + 1] = GAMMA_LUT[min(255, max(0, int(g * bri)))]
            buf[idx + 2] = GAMMA_LUT[min(255, max(0, int(b * bri)))]
            idx += 3

        with self._lock:
            try:
                self.port.write(buf)
                return True
            except Exception:
                self.connected = False
                return False


# ══════════════════════════════════════════════════════════════
#  Software LED Effects
# ══════════════════════════════════════════════════════════════


class Effects:
    """Generate color arrays for built-in LED effects."""

    def __init__(self) -> None:
        self._phase: float = 0.0

    def solid(
        self, n: int, color: tuple[int, int, int]
    ) -> list[tuple[int, int, int]]:
        return [color] * n

    def rainbow(self, n: int, speed: float = 1.0) -> list[tuple[int, int, int]]:
        self._phase += speed * 0.02
        out = []
        for i in range(n):
            hue = (self._phase + i / n) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            out.append((int(r * 255), int(g * 255), int(b * 255)))
        return out

    def breathing(
        self, n: int, color: tuple[int, int, int], speed: float = 1.0
    ) -> list[tuple[int, int, int]]:
        self._phase += speed * 0.03
        intensity = (math.sin(self._phase) + 1.0) / 2.0
        r = int(color[0] * intensity)
        g = int(color[1] * intensity)
        b = int(color[2] * intensity)
        return [(r, g, b)] * n

    def color_wave(self, n: int, speed: float = 1.0) -> list[tuple[int, int, int]]:
        self._phase += speed * 0.015
        out = []
        for i in range(n):
            t = self._phase + i * 0.15
            r = int((math.sin(t) + 1) * 127)
            g = int((math.sin(t + 2.094) + 1) * 127)
            b = int((math.sin(t + 4.189) + 1) * 127)
            out.append((r, g, b))
        return out

    def fire(self, n: int, speed: float = 1.0) -> list[tuple[int, int, int]]:
        """Warm fire/candle effect."""
        self._phase += speed * 0.05
        out = []
        for i in range(n):
            flicker = (math.sin(self._phase * 3.7 + i * 0.8) * 0.3
                       + math.sin(self._phase * 7.3 + i * 1.4) * 0.15
                       + 0.55)
            flicker = max(0.0, min(1.0, flicker))
            r = int(255 * flicker)
            g = int(100 * flicker * flicker)
            b = int(20 * flicker * flicker * flicker)
            out.append((r, g, b))
        return out


# ══════════════════════════════════════════════════════════════
#  Smoothing Helper
# ══════════════════════════════════════════════════════════════


def smooth_colors(
    current: list[tuple[int, int, int]],
    previous: Optional[list[tuple[int, int, int]]],
    factor: float,
) -> list[tuple[int, int, int]]:
    """Temporal smoothing: blend *current* toward *previous* by *factor*."""
    if previous is None or len(previous) != len(current):
        return current
    alpha = 1.0 - factor
    return [
        (
            int(c[0] * alpha + p[0] * factor),
            int(c[1] * alpha + p[1] * factor),
            int(c[2] * alpha + p[2] * factor),
        )
        for c, p in zip(current, previous)
    ]


# ══════════════════════════════════════════════════════════════
#  Main Application (tkinter GUI)
# ══════════════════════════════════════════════════════════════


class AmbientGlowApp:
    """Full-featured GUI for controlling the ambient light system."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("✦ AmbientGlow")
        self.root.geometry("520x830")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        # Try to set icon / DPI awareness
        try:
            from ctypes import windll  # type: ignore
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # Core objects
        self.capture = ScreenCapture()
        self.serial_handler = SerialHandler()
        self.effects = Effects()

        # State variables
        self.mode = tk.StringVar(value="ambient")
        self.brightness = tk.IntVar(value=200)
        self.target_fps = tk.IntVar(value=30)
        self.smoothing_val = tk.DoubleVar(value=0.35)
        self.n_top = tk.IntVar(value=DEFAULT_TOP)
        self.n_right = tk.IntVar(value=DEFAULT_RIGHT)
        self.n_bottom = tk.IntVar(value=DEFAULT_BOTTOM)
        self.n_left = tk.IntVar(value=DEFAULT_LEFT)
        self.effect_speed = tk.DoubleVar(value=1.0)
        self.solid_color: tuple[int, int, int] = (0, 180, 255)
        self.monitor_var = tk.StringVar()
        self.port_var = tk.StringVar()

        # Runtime state
        self._prev_colors: Optional[list[tuple[int, int, int]]] = None
        self._running = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._actual_fps: float = 0.0

        # Build the UI
        self._build_ui()
        self._refresh_ports()
        self._refresh_monitors()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI Construction ─────────────────────────────────────

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        # Configure dark theme styles
        style.configure("Dark.TFrame", background=BG_CARD)
        style.configure("Dark.TLabel", background=BG_CARD, foreground=FG, font=("Segoe UI", 10))
        style.configure("DarkTitle.TLabel", background=BG_CARD, foreground=ACCENT, font=("Segoe UI", 11, "bold"))
        style.configure("Header.TLabel", background=BG, foreground=FG, font=("Segoe UI", 16, "bold"))
        style.configure("FPS.TLabel", background=BG, foreground=SUCCESS, font=("Consolas", 10))
        style.configure("Dark.TRadiobutton", background=BG_CARD, foreground=FG, font=("Segoe UI", 10))
        style.configure("Dark.TButton", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

        # Map selection colors
        style.map("Dark.TRadiobutton",
                   background=[("active", BG_INPUT)],
                   foreground=[("active", ACCENT)])

        # Scrollable container
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self._scroll_frame = tk.Frame(canvas, bg=BG)

        self._scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._scroll_frame, anchor="nw", width=500)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        main = self._scroll_frame
        pad = {"padx": 12, "pady": 4}

        # ── Header ──
        hdr = tk.Frame(main, bg=BG)
        hdr.pack(fill="x", padx=10, pady=(12, 4))
        ttk.Label(hdr, text="✦  AmbientGlow", style="Header.TLabel").pack(side="left")
        self._fps_label = ttk.Label(hdr, text="⏱ -- FPS", style="FPS.TLabel")
        self._fps_label.pack(side="right")

        # ── Connection Card ──
        conn = self._card(main, "🔌  Connection")
        row = tk.Frame(conn, bg=BG_CARD)
        row.pack(fill="x", **pad)

        ttk.Label(row, text="Port:", style="Dark.TLabel").pack(side="left")
        self._port_combo = ttk.Combobox(
            row, textvariable=self.port_var, width=12, state="readonly"
        )
        self._port_combo.pack(side="left", padx=(4, 6))

        refresh_btn = ttk.Button(row, text="⟳", width=3, command=self._refresh_ports)
        refresh_btn.pack(side="left")

        self._connect_btn = ttk.Button(
            row, text="Connect", style="Accent.TButton", command=self._toggle_connection
        )
        self._connect_btn.pack(side="right")

        self._status_label = ttk.Label(conn, text="● Disconnected", style="Dark.TLabel")
        self._status_label.configure(foreground=DANGER)
        self._status_label.pack(anchor="w", **pad)

        # ── Monitor Card ──
        mon = self._card(main, "🖥  Monitor")
        row2 = tk.Frame(mon, bg=BG_CARD)
        row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Screen:", style="Dark.TLabel").pack(side="left")
        self._mon_combo = ttk.Combobox(
            row2, textvariable=self.monitor_var, width=30, state="readonly"
        )
        self._mon_combo.pack(side="left", padx=4, fill="x", expand=True)
        self._mon_combo.bind("<<ComboboxSelected>>", self._on_monitor_change)

        # ── Mode Card ──
        mode_card = self._card(main, "💡  Mode")
        modes = [
            ("Ambient", "ambient"),
            ("Solid Color", "solid"),
            ("Rainbow", "rainbow"),
            ("Breathing", "breathing"),
            ("Color Wave", "wave"),
            ("Fire", "fire"),
            ("Off", "off"),
        ]
        mode_frame = tk.Frame(mode_card, bg=BG_CARD)
        mode_frame.pack(fill="x", **pad)
        for i, (label, val) in enumerate(modes):
            rb = ttk.Radiobutton(
                mode_frame, text=label, variable=self.mode, value=val,
                style="Dark.TRadiobutton",
            )
            rb.grid(row=i // 4, column=i % 4, sticky="w", padx=8, pady=2)

        # Solid color picker
        self._color_frame = tk.Frame(mode_card, bg=BG_CARD)
        self._color_frame.pack(fill="x", **pad)
        ttk.Label(self._color_frame, text="Color:", style="Dark.TLabel").pack(side="left")
        self._color_preview = tk.Canvas(
            self._color_frame, width=40, height=22, bg=self._rgb_hex(self.solid_color),
            highlightthickness=1, highlightbackground="#444",
        )
        self._color_preview.pack(side="left", padx=6)
        ttk.Button(self._color_frame, text="Pick…", command=self._pick_color).pack(side="left")

        # ── LED Layout Card ──
        led_card = self._card(main, "💠  LED Layout")
        led_grid = tk.Frame(led_card, bg=BG_CARD)
        led_grid.pack(fill="x", **pad)

        for col_idx, (label, var) in enumerate([
            ("Top", self.n_top), ("Right", self.n_right),
            ("Bottom", self.n_bottom), ("Left", self.n_left),
        ]):
            ttk.Label(led_grid, text=label, style="Dark.TLabel").grid(
                row=0, column=col_idx, padx=8
            )
            spin = ttk.Spinbox(
                led_grid, from_=0, to=120, width=5, textvariable=var
            )
            spin.grid(row=1, column=col_idx, padx=8, pady=4)

        total_frame = tk.Frame(led_card, bg=BG_CARD)
        total_frame.pack(fill="x", **pad)
        self._total_label = ttk.Label(total_frame, text="Total: 88 LEDs", style="Dark.TLabel")
        self._total_label.pack(side="left")

        # Update total when spinboxes change
        for var in [self.n_top, self.n_right, self.n_bottom, self.n_left]:
            var.trace_add("write", self._update_total)

        # ── Settings Card ──
        settings = self._card(main, "⚙  Settings")

        self._slider(settings, "Brightness", self.brightness, 0, 255, "")
        self._slider(settings, "Target FPS", self.target_fps, 5, 60, "")
        self._slider_float(settings, "Smoothing", self.smoothing_val, 0.0, 0.9)
        self._slider_float(settings, "Effect Speed", self.effect_speed, 0.1, 5.0)

        # ── LED Preview Card ──
        preview_card = self._card(main, "🎨  Live Preview")
        self._preview_canvas = tk.Canvas(
            preview_card, width=470, height=180, bg="#0a0a12", highlightthickness=0
        )
        self._preview_canvas.pack(padx=8, pady=8)

        # ── Start / Stop ──
        btn_frame = tk.Frame(main, bg=BG)
        btn_frame.pack(fill="x", padx=12, pady=10)
        self._start_btn = tk.Button(
            btn_frame, text="▶  START", font=("Segoe UI", 13, "bold"),
            bg=ACCENT2, fg="white", activebackground=ACCENT,
            relief="flat", padx=20, pady=8, command=self._toggle_run,
        )
        self._start_btn.pack(fill="x")

    # ── UI Helpers ──────────────────────────────────────────

    def _card(self, parent: tk.Widget, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", padx=10, pady=5)
        card = tk.Frame(outer, bg=BG_CARD, highlightbackground="#2a2a4a",
                        highlightthickness=1)
        card.pack(fill="x")
        ttk.Label(card, text=title, style="DarkTitle.TLabel").pack(
            anchor="w", padx=12, pady=(8, 2)
        )
        return card

    def _slider(
        self, parent: tk.Widget, label: str, var: tk.IntVar,
        lo: int, hi: int, suffix: str,
    ) -> None:
        f = tk.Frame(parent, bg=BG_CARD)
        f.pack(fill="x", padx=12, pady=3)
        lbl = ttk.Label(f, text=f"{label}:", style="Dark.TLabel")
        lbl.pack(side="left")
        val_lbl = ttk.Label(f, text=str(var.get()), style="Dark.TLabel", width=5)
        val_lbl.pack(side="right")
        scale = ttk.Scale(f, from_=lo, to=hi, variable=var, orient="horizontal")
        scale.pack(side="right", fill="x", expand=True, padx=6)
        var.trace_add("write", lambda *_: val_lbl.configure(text=f"{var.get()}{suffix}"))

    def _slider_float(
        self, parent: tk.Widget, label: str, var: tk.DoubleVar,
        lo: float, hi: float,
    ) -> None:
        f = tk.Frame(parent, bg=BG_CARD)
        f.pack(fill="x", padx=12, pady=3)
        lbl = ttk.Label(f, text=f"{label}:", style="Dark.TLabel")
        lbl.pack(side="left")
        val_lbl = ttk.Label(f, text=f"{var.get():.2f}", style="Dark.TLabel", width=5)
        val_lbl.pack(side="right")
        scale = ttk.Scale(f, from_=lo, to=hi, variable=var, orient="horizontal")
        scale.pack(side="right", fill="x", expand=True, padx=6)
        var.trace_add("write", lambda *_: val_lbl.configure(text=f"{var.get():.2f}"))

    @staticmethod
    def _rgb_hex(c: tuple[int, int, int]) -> str:
        return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"

    def _update_total(self, *_args) -> None:
        try:
            total = self.n_top.get() + self.n_right.get() + self.n_bottom.get() + self.n_left.get()
            self._total_label.configure(text=f"Total: {total} LEDs")
        except tk.TclError:
            pass

    # ── Refresh helpers ─────────────────────────────────────

    def _refresh_ports(self) -> None:
        ports = SerialHandler.list_ports()
        self._port_combo["values"] = ports
        if ports:
            self._port_combo.current(0)

    def _refresh_monitors(self) -> None:
        mons = self.capture.get_monitors()
        labels = [
            f"Monitor {i + 1}  ({m['width']}×{m['height']})"
            for i, m in enumerate(mons)
        ]
        self._mon_combo["values"] = labels
        if labels:
            self._mon_combo.current(0)

    def _on_monitor_change(self, _event=None) -> None:
        idx = self._mon_combo.current()
        self.capture.set_monitor(idx)

    # ── Connection ──────────────────────────────────────────

    def _toggle_connection(self) -> None:
        if self.serial_handler.connected:
            self.serial_handler.disconnect()
            self._status_label.configure(text="● Disconnected", foreground=DANGER)
            self._connect_btn.configure(text="Connect")
        else:
            port = self.port_var.get()
            if not port:
                return
            ok = self.serial_handler.connect(port)
            if ok:
                self._status_label.configure(text=f"● Connected to {port}", foreground=SUCCESS)
                self._connect_btn.configure(text="Disconnect")
            else:
                self._status_label.configure(text="● Connection failed", foreground=DANGER)

    # ── Color picker ────────────────────────────────────────

    def _pick_color(self) -> None:
        result = colorchooser.askcolor(
            initialcolor=self._rgb_hex(self.solid_color), title="Choose solid color"
        )
        if result and result[0]:
            self.solid_color = (int(result[0][0]), int(result[0][1]), int(result[0][2]))
            self._color_preview.configure(bg=self._rgb_hex(self.solid_color))

    # ── Start / Stop ────────────────────────────────────────

    def _toggle_run(self) -> None:
        if self._running:
            self._stop.set()
            self._running = False
            self._start_btn.configure(text="▶  START", bg=ACCENT2)
        else:
            self._stop.clear()
            self._running = True
            self._start_btn.configure(text="■  STOP", bg=DANGER)
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            self._update_preview_loop()

    # ── Main capture loop (runs in background thread) ──────

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()

            mode = self.mode.get()
            n_t = self.n_top.get()
            n_r = self.n_right.get()
            n_b = self.n_bottom.get()
            n_l = self.n_left.get()
            total = n_t + n_r + n_b + n_l

            if total == 0:
                time.sleep(0.1)
                continue

            # Generate colors based on mode
            if mode == "ambient":
                try:
                    colors = self.capture.capture_edge_colors(n_t, n_r, n_b, n_l)
                except Exception:
                    time.sleep(0.05)
                    continue
            elif mode == "solid":
                colors = self.effects.solid(total, self.solid_color)
            elif mode == "rainbow":
                colors = self.effects.rainbow(total, self.effect_speed.get())
            elif mode == "breathing":
                colors = self.effects.breathing(total, self.solid_color, self.effect_speed.get())
            elif mode == "wave":
                colors = self.effects.color_wave(total, self.effect_speed.get())
            elif mode == "fire":
                colors = self.effects.fire(total, self.effect_speed.get())
            elif mode == "off":
                colors = [(0, 0, 0)] * total
            else:
                colors = [(0, 0, 0)] * total

            # Apply temporal smoothing
            sm = self.smoothing_val.get()
            if sm > 0:
                colors = smooth_colors(colors, self._prev_colors, sm)
            self._prev_colors = colors

            # Send to ESP32
            bri = self.brightness.get()
            self.serial_handler.send_colors(colors, bri)

            # Store for preview
            self._latest_colors = colors

            # FPS throttle
            elapsed = time.perf_counter() - t0
            target_dt = 1.0 / max(self.target_fps.get(), 1)
            sleep_time = target_dt - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

            actual_dt = time.perf_counter() - t0
            self._actual_fps = 1.0 / actual_dt if actual_dt > 0 else 0

    # ── Preview drawing (runs on main thread via after) ────

    _latest_colors: list[tuple[int, int, int]] = []

    def _update_preview_loop(self) -> None:
        if not self._running:
            return

        # Update FPS display
        self._fps_label.configure(text=f"⏱ {self._actual_fps:.0f} FPS")

        # Draw LED preview
        self._draw_preview(self._latest_colors)

        self.root.after(33, self._update_preview_loop)  # ~30 Hz preview

    def _draw_preview(self, colors: list[tuple[int, int, int]]) -> None:
        c = self._preview_canvas
        c.delete("all")
        cw, ch = 470, 180

        if not colors:
            c.create_text(cw // 2, ch // 2, text="No data", fill=FG_DIM,
                          font=("Segoe UI", 11))
            return

        n_t = self.n_top.get()
        n_r = self.n_right.get()
        n_b = self.n_bottom.get()
        n_l = self.n_left.get()

        margin = 22
        led_size = 8
        inner_x1 = margin + led_size + 4
        inner_y1 = margin + led_size + 4
        inner_x2 = cw - margin - led_size - 4
        inner_y2 = ch - margin - led_size - 4

        # Draw monitor outline
        c.create_rectangle(inner_x1, inner_y1, inner_x2, inner_y2,
                           outline="#333", width=1, dash=(4, 4))
        c.create_text((inner_x1 + inner_x2) // 2, (inner_y1 + inner_y2) // 2,
                      text="MONITOR", fill="#333", font=("Segoe UI", 9))

        idx = 0

        # Top LEDs
        if n_t > 0:
            span = inner_x2 - inner_x1
            step = span / n_t
            for i in range(n_t):
                if idx < len(colors):
                    col = self._rgb_hex(colors[idx])
                    x = inner_x1 + i * step + step / 2
                    y = margin
                    c.create_oval(x - led_size / 2, y - led_size / 2,
                                  x + led_size / 2, y + led_size / 2,
                                  fill=col, outline="")
                    idx += 1

        # Right LEDs
        if n_r > 0:
            span = inner_y2 - inner_y1
            step = span / n_r
            for i in range(n_r):
                if idx < len(colors):
                    col = self._rgb_hex(colors[idx])
                    x = cw - margin
                    y = inner_y1 + i * step + step / 2
                    c.create_oval(x - led_size / 2, y - led_size / 2,
                                  x + led_size / 2, y + led_size / 2,
                                  fill=col, outline="")
                    idx += 1

        # Bottom LEDs
        if n_b > 0:
            span = inner_x2 - inner_x1
            step = span / n_b
            for i in range(n_b):
                if idx < len(colors):
                    col = self._rgb_hex(colors[idx])
                    x = inner_x2 - i * step - step / 2
                    y = ch - margin
                    c.create_oval(x - led_size / 2, y - led_size / 2,
                                  x + led_size / 2, y + led_size / 2,
                                  fill=col, outline="")
                    idx += 1

        # Left LEDs
        if n_l > 0:
            span = inner_y2 - inner_y1
            step = span / n_l
            for i in range(n_l):
                if idx < len(colors):
                    col = self._rgb_hex(colors[idx])
                    x = margin
                    y = inner_y2 - i * step - step / 2
                    c.create_oval(x - led_size / 2, y - led_size / 2,
                                  x + led_size / 2, y + led_size / 2,
                                  fill=col, outline="")
                    idx += 1

    # ── Shutdown ────────────────────────────────────────────

    def _on_close(self) -> None:
        self._stop.set()
        self._running = False
        self.serial_handler.disconnect()
        self.root.destroy()

    # ── Entry point ─────────────────────────────────────────

    def run(self) -> None:
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = AmbientGlowApp()
    app.run()
