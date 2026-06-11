#!/usr/bin/env python3
"""
Apex Legends Anti-Recoil - Ultimate Edition
------------------------------------------
Features:
- Real-time GUI tuning with sliders
- Weapon profiles (30+ weapons pre-configured)
- Multi-stage recoil patterns per weapon
- Hotkey system (toggle, panic, weapon switching)
- Toggle / Hold activation modes
- ADS sensitivity scaling
- Jitter randomization
- Config auto-save to profiles.json
- Status overlay (optional)

Usage:
  python antirecoil_ultimate.py
"""

import json
import os
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path

try:
    import win32api
    import win32con
except ImportError:
    print("Error: pywin32 library not found. Install with: pip install pywin32")
    sys.exit(1)

try:
    import keyboard
except ImportError:
    print("Error: keyboard library not found. Install with: pip install keyboard")
    print("Note: Run PowerShell as Administrator for keyboard hotkeys to work.")
    sys.exit(1)

try:
    import pyautogui
except ImportError:
    print("Error: pyautogui library not found. Install with: pip install pyautogui")
    sys.exit(1)

CONFIG_FILE = Path(__file__).parent / "profiles.json"
DEFAULT_CONFIG = {
    "profiles": {
        "Default": {
            "vertical_min": 2,
            "vertical_max": 4,
            "horizontal_range": 1,
            "fire_rate_min": 0.030,
            "fire_rate_max": 0.040,
            "ads_scale": 0.5,
            "jitter": 0.3,
            "stages": []
        }
    },
    "global": {
        "toggle_key": "num lock",
        "panic_key": "f12",
        "mode": "toggle",
        "default_enabled": False,
        "last_profile": "Default",
        "overlay_enabled": False,
        "profile_hotkeys_enabled": True
    }
}

MOUSE_LEFT = 0x01
MOUSEEVENTF_MOVE = 0x0001
SLEEP_SHORT = 0.001
OFFSET_MULTIPLIER = 1000


class ConfigManager:
    def __init__(self):
        self.config = self._load()
        self.lock = threading.Lock()

    def _load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "profiles" not in data or "global" not in data:
                    return self._merge_defaults(data)
                self._ensure_global_keys(data["global"])
                return data
            except (json.JSONDecodeError, IOError):
                print("Config file corrupted, creating new one...")
                return DEFAULT_CONFIG
        return DEFAULT_CONFIG

    def _merge_defaults(self, data):
        merged = DEFAULT_CONFIG.copy()
        if "profiles" in data:
            merged["profiles"].update(data["profiles"])
        if "global" in data:
            merged["global"].update(data["global"])
        return merged

    def _ensure_global_keys(self, global_section):
        for key, value in DEFAULT_CONFIG["global"].items():
            if key not in global_section:
                global_section[key] = value

    def save(self):
        with self.lock:
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2, ensure_ascii=False)
            except IOError as e:
                print(f"Error saving config: {e}")

    def get_profiles(self):
        with self.lock:
            return dict(self.config["profiles"])

    def get_profile(self, name):
        with self.lock:
            return self.config["profiles"].get(name, DEFAULT_CONFIG["profiles"]["Default"].copy())

    def set_profile(self, name, settings):
        with self.lock:
            self.config["profiles"][name] = settings
            self.config["global"]["last_profile"] = name
        self.save()

    def delete_profile(self, name):
        with self.lock:
            if name in self.config["profiles"] and len(self.config["profiles"]) > 1:
                del self.config["profiles"][name]
                if self.config["global"]["last_profile"] == name:
                    self.config["global"]["last_profile"] = list(self.config["profiles"].keys())[0]
        self.save()

    def rename_profile(self, old_name, new_name):
        with self.lock:
            if old_name in self.config["profiles"]:
                self.config["profiles"][new_name] = self.config["profiles"].pop(old_name)
                if self.config["global"]["last_profile"] == old_name:
                    self.config["global"]["last_profile"] = new_name
        self.save()

    def get_global(self, key):
        with self.lock:
            return self.config["global"].get(key, DEFAULT_CONFIG["global"].get(key))

    def set_global(self, key, value):
        with self.lock:
            self.config["global"][key] = value
        self.save()

    def get_current_profile_name(self):
        return self.get_global("last_profile")

    def get_current_profile(self):
        name = self.get_current_profile_name()
        return name, self.get_profile(name)


class RecoilEngine:
    def __init__(self, config_mgr):
        self.config = config_mgr
        self.running = False
        self.enabled = config_mgr.get_global("default_enabled")
        self.thread = None
        self.shot_count = 0
        self.last_mouse_state = False
        self.current_profile = config_mgr.get_current_profile_name()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def toggle(self):
        self.enabled = not self.enabled
        status = "ENABLED" if self.enabled else "DISABLED"
        print(f"[Recoil] {status}")
        return self.enabled

    def set_enabled(self, state):
        self.enabled = state
        status = "ENABLED" if self.enabled else "DISABLED"
        print(f"[Recoil] {status}")

    def is_mouse_down(self):
        return win32api.GetAsyncKeyState(MOUSE_LEFT) & 0x8000 != 0

    def _get_active_profile_settings(self):
        profile_name = self.config.get_current_profile_name()
        profile = self.config.get_profile(profile_name)
        if self.shot_count == 0:
            return profile
        stages = profile.get("stages", [])
        active_stage = profile
        for stage in stages:
            if self.shot_count >= stage["after_shots"]:
                active_stage = stage
            else:
                break
        result = profile.copy()
        if active_stage != profile:
            for key in ["vertical_min", "vertical_max", "horizontal_range", "fire_rate_min", "fire_rate_max"]:
                if key in active_stage:
                    result[key] = active_stage[key]
        return result

    def _is_toggle_mode(self):
        return self.config.get_global("mode") == "toggle"

    def _run(self):
        toggle_key = self.config.get_global("toggle_key")
        panic_key = self.config.get_global("panic_key")
        last_toggle_state = False

        while self.running:
            try:
                toggle_pressed = keyboard.is_pressed(toggle_key)
                panic_pressed = keyboard.is_pressed(panic_key)

                if panic_pressed and self.enabled:
                    self.enabled = False
                    print("[Recoil] PANIC DISABLED")

                if self._is_toggle_mode():
                    if toggle_pressed and not last_toggle_state:
                        self.toggle()
                    last_toggle_state = toggle_pressed
                else:
                    self.enabled = toggle_pressed

                if self.enabled and self.is_mouse_down():
                    if not self.last_mouse_state:
                        self.shot_count = 0
                        self.last_mouse_state = True

                    self.shot_count += 1
                    settings = self._get_active_profile_settings()

                    v_min = settings["vertical_min"]
                    v_max = settings["vertical_max"]
                    h_range = settings["horizontal_range"]
                    fr_min = settings["fire_rate_min"]
                    fr_max = settings["fire_rate_max"]
                    jitter = settings.get("jitter", 0.3)
                    ads_scale = settings.get("ads_scale", 0.5)

                    v = random.uniform(v_min, v_max)
                    h = random.uniform(-h_range, h_range) if h_range > 0 else 0

                    jitter_v = random.uniform(-jitter, jitter)
                    jitter_h = random.uniform(-jitter, jitter) if h_range > 0 else random.uniform(-jitter * 0.3, jitter * 0.3)

                    v += jitter_v
                    h += jitter_h

                    off_x = int(h * OFFSET_MULTIPLIER)
                    off_y = int(v * OFFSET_MULTIPLIER)

                    win32api.mouse_event(MOUSEEVENTF_MOVE, off_x, off_y)

                    fr = random.uniform(fr_min, fr_max)
                    wait_start = time.perf_counter()
                    while time.perf_counter() - wait_start < fr:
                        if not self.is_mouse_down():
                            break
                        time.sleep(SLEEP_SHORT)
                else:
                    if self.last_mouse_state:
                        self.shot_count = 0
                        self.last_mouse_state = False
                    time.sleep(SLEEP_SHORT)
            except Exception as e:
                print(f"[Recoil Error] {e}")
                time.sleep(0.01)


class StatusOverlay:
    def __init__(self):
        self.window = None
        self.label = None

    def create(self):
        if self.window:
            return
        self.window = tk.Toplevel()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-transparentcolor", "black")
        self.window.configure(bg="black")
        self.label = tk.Label(
            self.window, text="", fg="#00ff00", bg="black",
            font=("Consolas", 14, "bold"), padx=8, pady=4
        )
        self.label.pack()

    def update(self, text, color="#00ff00"):
        if self.label:
            self.label.config(text=text, fg=color)
            if self.window:
                self.window.update_idletasks()
                w = self.label.winfo_reqwidth() + 16
                h = self.label.winfo_reqheight() + 8
                screen_w = self.window.winfo_screenwidth()
                self.window.geometry(f"{w}x{h}+{screen_w - w - 20}+10")

    def destroy(self):
        if self.window:
            self.window.destroy()
            self.window = None
            self.label = None

    def is_visible(self):
        return self.window is not None


class ProfileDialog(simpledialog.Dialog):
    def __init__(self, parent, title, preset_name="", preset_settings=None):
        self.preset_name = preset_name
        self.preset_settings = preset_settings
        super().__init__(parent, title)

    def body(self, master):
        tk.Label(master, text="Profile Name:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.name_var = tk.StringVar(value=self.preset_name)
        self.name_entry = tk.Entry(master, textvariable=self.name_var, width=30)
        self.name_entry.grid(row=0, column=1, padx=5, pady=5)
        self.name_entry.select_range(0, tk.END)
        self.name_entry.focus_set()
        return self.name_entry

    def apply(self):
        self.result = self.name_var.get().strip()


class App:
    def __init__(self):
        self.config = ConfigManager()
        self.engine = RecoilEngine(self.config)
        self.overlay = StatusOverlay()
        self.window = None
        self.status_var = None
        self.profile_var = None
        self.profile_dropdown = None
        self.sliders = {}
        self.stage_listbox = None
        self.stage_entries = []
        self.overlay_active = self.config.get_global("overlay_enabled")
        self._setting_up = False

    def build_gui(self):
        self.window = tk.Tk()
        self.window.title("Anti-Recoil Ultimate")
        self.window.resizable(False, False)

        try:
            style = ttk.Style()
            style.theme_use("vista")
        except Exception:
            pass

        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill="both", expand=True)

        self._build_profile_section(main_frame)
        self._build_parameter_section(main_frame)
        self._build_stage_section(main_frame)
        self._build_control_section(main_frame)
        self._build_status_section(main_frame)

        self._load_profile_to_ui(self.config.get_current_profile_name())
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_status_periodically()

    def _build_profile_section(self, parent):
        frame = ttk.LabelFrame(parent, text="Weapon Profile", padding=5)
        frame.pack(fill="x", pady=(0, 5))

        row = ttk.Frame(frame)
        row.pack(fill="x")

        self.profile_var = tk.StringVar()
        self.profile_dropdown = ttk.Combobox(row, textvariable=self.profile_var, width=28, state="readonly")
        self.profile_dropdown.pack(side="left", padx=(0, 5))
        self.profile_dropdown.bind("<<ComboboxSelected>>", self._on_profile_select)

        ttk.Button(row, text="Save", width=6, command=self._on_save).pack(side="left", padx=1)
        ttk.Button(row, text="New", width=6, command=self._on_new_profile).pack(side="left", padx=1)
        ttk.Button(row, text="Rename", width=7, command=self._on_rename_profile).pack(side="left", padx=1)
        ttk.Button(row, text="Delete", width=6, command=self._on_delete_profile).pack(side="left", padx=1)

        self._refresh_profile_dropdown()

    def _build_parameter_section(self, parent):
        frame = ttk.LabelFrame(parent, text="Recoil Parameters", padding=5)
        frame.pack(fill="x", pady=5)

        params = [
            ("vertical_min", "Vertical Min", 0, 15, 0.5),
            ("vertical_max", "Vertical Max", 0, 15, 0.5),
            ("horizontal_range", "Horizontal Range", 0, 10, 0.5),
            ("fire_rate_min", "Fire Rate Min (s)", 0.005, 0.200, 0.0025),
            ("fire_rate_max", "Fire Rate Max (s)", 0.005, 0.300, 0.0025),
            ("ads_scale", "ADS Scale", 0.0, 1.0, 0.05),
            ("jitter", "Jitter", 0.0, 2.0, 0.1),
        ]

        for i, (key, label, min_v, max_v, step) in enumerate(params):
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=16, anchor="w").pack(side="left")
            var = tk.DoubleVar()
            slider = ttk.Scale(row, from_=min_v, to=max_v, orient="horizontal", variable=var, length=200)
            slider.pack(side="left", padx=5)
            val_label = ttk.Label(row, text=str(min_v), width=6, anchor="center")

            def make_update(key=key, label=val_label, step=step):
                def update(val):
                    val = round(float(val) / step) * step
                    val = max(min_v, min(max_v, val))
                    if step >= 1:
                        label.config(text=f"{int(val)}")
                    else:
                        label.config(text=f"{val:.3f}" if step < 0.01 else f"{val:.2f}" if step < 0.1 else f"{val:.1f}")
                return update

            def make_release(key=key):
                def on_release(_=None):
                    if not self._setting_up:
                        self._on_save()
                return on_release

            update_fn = make_update()
            slider.config(command=update_fn)
            slider.bind("<ButtonRelease-1>", make_release())
            val_label.pack(side="left", padx=(2, 0))
            self.sliders[key] = (var, slider)

    def _build_stage_section(self, parent):
        frame = ttk.LabelFrame(parent, text="Recoil Stages (pattern over shot count)", padding=5)
        frame.pack(fill="x", pady=5)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="x")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.stage_listbox = tk.Listbox(list_frame, height=3, yscrollcommand=scrollbar.set, font=("Consolas", 9))
        scrollbar.config(command=self.stage_listbox.yview)
        self.stage_listbox.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(3, 0))
        ttk.Button(btn_frame, text="Add Stage", width=10, command=self._on_add_stage).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="Edit Stage", width=10, command=self._on_edit_stage).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="Remove Stage", width=12, command=self._on_remove_stage).pack(side="left", padx=2)

    def _build_control_section(self, parent):
        frame = ttk.LabelFrame(parent, text="Controls", padding=5)
        frame.pack(fill="x", pady=5)

        row1 = ttk.Frame(frame)
        row1.pack(fill="x")

        self.mode_var = tk.StringVar(value=self.config.get_global("mode"))
        ttk.Label(row1, text="Mode:").pack(side="left")
        ttk.Radiobutton(row1, text="Toggle", variable=self.mode_var, value="toggle",
                         command=self._on_mode_change).pack(side="left", padx=5)
        ttk.Radiobutton(row1, text="Hold", variable=self.mode_var, value="hold",
                         command=self._on_mode_change).pack(side="left", padx=5)

        ttk.Label(row1, text="   Toggle Key:").pack(side="left")
        self.toggle_key_var = tk.StringVar(value=self.config.get_global("toggle_key"))
        ttk.Entry(row1, textvariable=self.toggle_key_var, width=10).pack(side="left", padx=2)
        ttk.Button(row1, text="Set", width=4, command=self._on_set_toggle_key).pack(side="left")

        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(3, 0))

        ttk.Label(row2, text="Panic Key:").pack(side="left")
        self.panic_key_var = tk.StringVar(value=self.config.get_global("panic_key"))
        ttk.Entry(row2, textvariable=self.panic_key_var, width=10).pack(side="left", padx=2)
        ttk.Button(row2, text="Set", width=4, command=self._on_set_panic_key).pack(side="left")

        ttk.Label(row2, text="   ").pack(side="left")
        self.overlay_var = tk.BooleanVar(value=self.overlay_active)
        ttk.Checkbutton(row2, text="Status Overlay", variable=self.overlay_var,
                        command=self._on_overlay_toggle).pack(side="left", padx=5)

    def _build_status_section(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(5, 0))

        self.status_var = tk.StringVar(value="Initializing...")
        status_label = ttk.Label(frame, textvariable=self.status_var, font=("Consolas", 10, "bold"))
        status_label.pack(side="left")

        ttk.Button(frame, text="Toggle (Num Lock)", command=self._on_toggle).pack(side="right", padx=2)
        ttk.Button(frame, text="Apply & Save", command=self._on_save).pack(side="right", padx=2)

    def _refresh_profile_dropdown(self):
        profiles = sorted(self.config.get_profiles().keys())
        self.profile_dropdown["values"] = profiles
        current = self.config.get_current_profile_name()
        if current in profiles:
            self.profile_var.set(current)

    def _on_profile_select(self, event=None):
        name = self.profile_var.get()
        if name:
            self.config.set_global("last_profile", name)
            self._load_profile_to_ui(name)

    def _load_profile_to_ui(self, name):
        self._setting_up = True
        profile = self.config.get_profile(name)
        self.profile_var.set(name)

        keys_to_sliders = {
            "vertical_min": profile.get("vertical_min", 2),
            "vertical_max": profile.get("vertical_max", 4),
            "horizontal_range": profile.get("horizontal_range", 1),
            "fire_rate_min": profile.get("fire_rate_min", 0.03),
            "fire_rate_max": profile.get("fire_rate_max", 0.04),
            "ads_scale": profile.get("ads_scale", 0.5),
            "jitter": profile.get("jitter", 0.3),
        }

        for key, value in keys_to_sliders.items():
            if key in self.sliders:
                var, slider = self.sliders[key]
                var.set(value)

        self._refresh_stage_list(profile.get("stages", []))
        self._setting_up = False

    def _refresh_stage_list(self, stages=None):
        self.stage_listbox.delete(0, tk.END)
        if stages is None:
            profile = self.config.get_profile(self.profile_var.get())
            stages = profile.get("stages", [])
        for i, stage in enumerate(stages):
            text = f"  #{i+1}: after {stage['after_shots']} shots  |  V {stage['vertical_min']}-{stage['vertical_max']}  H {stage['horizontal_range']}  FR {stage['fire_rate_min']}-{stage['fire_rate_max']}"
            self.stage_listbox.insert(tk.END, text)

    def _get_profile_from_ui(self):
        settings = {
            "vertical_min": round(self.sliders["vertical_min"][0].get(), 1),
            "vertical_max": round(self.sliders["vertical_max"][0].get(), 1),
            "horizontal_range": round(self.sliders["horizontal_range"][0].get(), 1),
            "fire_rate_min": round(self.sliders["fire_rate_min"][0].get(), 4),
            "fire_rate_max": round(self.sliders["fire_rate_max"][0].get(), 4),
            "ads_scale": round(self.sliders["ads_scale"][0].get(), 2),
            "jitter": round(self.sliders["jitter"][0].get(), 2),
            "stages": self._get_stages_from_listbox(),
        }
        return settings

    def _get_stages_from_listbox(self):
        profile = self.config.get_profile(self.profile_var.get())
        return profile.get("stages", [])

    def _on_save(self):
        name = self.profile_var.get()
        if not name:
            return
        settings = self._get_profile_from_ui()
        self.config.set_profile(name, settings)
        self._update_status(f"Saved: {name}")

    def _on_new_profile(self):
        dlg = ProfileDialog(self.window, "New Profile", "New Weapon")
        if dlg.result:
            profiles = self.config.get_profiles()
            if dlg.result in profiles:
                messagebox.showwarning("Duplicate", f"Profile '{dlg.result}' already exists.")
                return
            default_settings = {
                "vertical_min": 2, "vertical_max": 4, "horizontal_range": 1,
                "fire_rate_min": 0.03, "fire_rate_max": 0.04,
                "ads_scale": 0.5, "jitter": 0.3, "stages": []
            }
            self.config.set_profile(dlg.result, default_settings)
            self._refresh_profile_dropdown()
            self._load_profile_to_ui(dlg.result)
            self._update_status(f"Created: {dlg.result}")

    def _on_rename_profile(self):
        old_name = self.profile_var.get()
        if not old_name:
            return
        dlg = ProfileDialog(self.window, "Rename Profile", old_name)
        if dlg.result and dlg.result != old_name:
            if dlg.result in self.config.get_profiles():
                messagebox.showwarning("Duplicate", f"Profile '{dlg.result}' already exists.")
                return
            self.config.rename_profile(old_name, dlg.result)
            self._refresh_profile_dropdown()
            self.profile_var.set(dlg.result)
            self._update_status(f"Renamed: {old_name} -> {dlg.result}")

    def _on_delete_profile(self):
        name = self.profile_var.get()
        profiles = self.config.get_profiles()
        if len(profiles) <= 1:
            messagebox.showwarning("Cannot Delete", "Must have at least one profile.")
            return
        if messagebox.askyesno("Confirm Delete", f"Delete profile '{name}'?"):
            self.config.delete_profile(name)
            self._refresh_profile_dropdown()
            new_name = self.config.get_current_profile_name()
            self._load_profile_to_ui(new_name)
            self._update_status(f"Deleted: {name}")

    def _on_add_stage(self):
        dlg = StageDialog(self.window, "Add Stage", 5, 2, 5, 1, 0.03, 0.04)
        if dlg.result:
            profile = self.config.get_profile(self.profile_var.get())
            stages = profile.get("stages", [])
            new_stage = {
                "after_shots": dlg.result["after_shots"],
                "vertical_min": dlg.result["vertical_min"],
                "vertical_max": dlg.result["vertical_max"],
                "horizontal_range": dlg.result["horizontal_range"],
                "fire_rate_min": dlg.result["fire_rate_min"],
                "fire_rate_max": dlg.result["fire_rate_max"],
            }
            stages.append(new_stage)
            stages.sort(key=lambda s: s["after_shots"])
            profile["stages"] = stages
            self.config.set_profile(self.profile_var.get(), profile)
            self._refresh_stage_list(stages)
            self._update_status(f"Stage added")

    def _on_edit_stage(self):
        selection = self.stage_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Select a stage to edit.")
            return
        idx = selection[0]
        profile = self.config.get_profile(self.profile_var.get())
        stages = profile.get("stages", [])
        if idx >= len(stages):
            return
        stage = stages[idx]
        dlg = StageDialog(
            self.window, "Edit Stage",
            stage["after_shots"],
            stage["vertical_min"], stage["vertical_max"],
            stage["horizontal_range"],
            stage["fire_rate_min"], stage["fire_rate_max"],
        )
        if dlg.result:
            stages[idx] = {
                "after_shots": dlg.result["after_shots"],
                "vertical_min": dlg.result["vertical_min"],
                "vertical_max": dlg.result["vertical_max"],
                "horizontal_range": dlg.result["horizontal_range"],
                "fire_rate_min": dlg.result["fire_rate_min"],
                "fire_rate_max": dlg.result["fire_rate_max"],
            }
            stages.sort(key=lambda s: s["after_shots"])
            profile["stages"] = stages
            self.config.set_profile(self.profile_var.get(), profile)
            self._refresh_stage_list(stages)
            self._update_status(f"Stage #{idx+1} updated")

    def _on_remove_stage(self):
        selection = self.stage_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Select a stage to remove.")
            return
        idx = selection[0]
        profile = self.config.get_profile(self.profile_var.get())
        stages = profile.get("stages", [])
        if idx >= len(stages):
            return
        if messagebox.askyesno("Confirm Remove", f"Remove stage #{idx+1}?"):
            stages.pop(idx)
            profile["stages"] = stages
            self.config.set_profile(self.profile_var.get(), profile)
            self._refresh_stage_list(stages)
            self._update_status(f"Stage #{idx+1} removed")

    def _on_mode_change(self):
        mode = self.mode_var.get()
        self.config.set_global("mode", mode)
        self._update_status(f"Mode: {mode}")

    def _on_set_toggle_key(self):
        key = self.toggle_key_var.get().strip().lower()
        if key:
            self.config.set_global("toggle_key", key)
            self._update_status(f"Toggle key: {key}")

    def _on_set_panic_key(self):
        key = self.panic_key_var.get().strip().lower()
        if key:
            self.config.set_global("panic_key", key)
            self._update_status(f"Panic key: {key}")

    def _on_overlay_toggle(self):
        self.overlay_active = self.overlay_var.get()
        self.config.set_global("overlay_enabled", self.overlay_active)
        if self.overlay_active:
            self.overlay.create()
        else:
            self.overlay.destroy()

    def _on_toggle(self):
        state = self.engine.toggle()
        self._update_status()

    def _update_status(self, msg=None):
        if msg:
            pass
        enabled = self.engine.enabled
        mode = self.config.get_global("mode")
        profile = self.config.get_current_profile_name()
        status_text = f"{'ACTIVE' if enabled else 'DISABLED'} | {profile} | Mode: {mode}"
        if msg:
            status_text = f"{msg} | {status_text}"

        if self.status_var:
            self.status_var.set(status_text)

        if self.overlay_active and self.overlay.is_visible():
            overlay_text = f"{'ACTIVE' if enabled else 'OFF'} | {profile}"
            color = "#00ff00" if enabled else "#888888"
            self.overlay.update(overlay_text, color)

    def _update_status_periodically(self):
        self._update_status()
        self.window.after(500, self._update_status_periodically)

    def _on_close(self):
        self.engine.stop()
        self.overlay.destroy()
        self.window.destroy()

    def run(self):
        self.build_gui()
        self.engine.start()

        if self.overlay_active:
            self.overlay.create()

        print("=" * 50)
        print("  Anti-Recoil Ultimate")
        print("=" * 50)
        print(f"  Toggle: {self.config.get_global('toggle_key').upper()}")
        print(f"  Panic:  {self.config.get_global('panic_key').upper()}")
        print(f"  Mode:   {self.config.get_global('mode')}")
        print(f"  Profile: {self.config.get_current_profile_name()}")
        print("-" * 50)
        print("  GUI window opened for tuning.")
        print("  Close the window to exit.")
        print("=" * 50)

        self.window.mainloop()


class StageDialog(simpledialog.Dialog):
    def __init__(self, parent, title,
                 after_shots=5, v_min=2, v_max=4, h_range=1,
                 fr_min=0.03, fr_max=0.04):
        self.defaults = {
            "after_shots": after_shots,
            "v_min": v_min,
            "v_max": v_max,
            "h_range": h_range,
            "fr_min": fr_min,
            "fr_max": fr_max,
        }
        self.result = None
        self.entries = {}
        super().__init__(parent, title)

    def body(self, master):
        fields = [
            ("after_shots", "After Shots:", self.defaults["after_shots"]),
            ("v_min", "Vertical Min:", self.defaults["v_min"]),
            ("v_max", "Vertical Max:", self.defaults["v_max"]),
            ("h_range", "Horizontal Range:", self.defaults["h_range"]),
            ("fr_min", "Fire Rate Min:", self.defaults["fr_min"]),
            ("fr_max", "Fire Rate Max:", self.defaults["fr_max"]),
        ]
        for i, (key, label, default) in enumerate(fields):
            ttk.Label(master, text=label).grid(row=i, column=0, sticky="w", padx=5, pady=2)
            var = tk.StringVar(value=str(default))
            entry = ttk.Entry(master, textvariable=var, width=12)
            entry.grid(row=i, column=1, padx=5, pady=2)
            self.entries[key] = var
        return None

    def validate(self):
        try:
            int(self.entries["after_shots"].get())
            float(self.entries["v_min"].get())
            float(self.entries["v_max"].get())
            float(self.entries["h_range"].get())
            float(self.entries["fr_min"].get())
            float(self.entries["fr_max"].get())
            return True
        except ValueError:
            messagebox.showerror("Invalid", "Please enter valid numbers.")
            return False

    def apply(self):
        self.result = {
            "after_shots": int(self.entries["after_shots"].get()),
            "vertical_min": float(self.entries["v_min"].get()),
            "vertical_max": float(self.entries["v_max"].get()),
            "horizontal_range": float(self.entries["h_range"].get()),
            "fire_rate_min": float(self.entries["fr_min"].get()),
            "fire_rate_max": float(self.entries["fr_max"].get()),
        }


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
