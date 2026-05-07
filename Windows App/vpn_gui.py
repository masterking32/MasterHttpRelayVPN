import customtkinter as ctk
import tkinter as tk
import threading
import asyncio
import json
import os
import sys
import winreg
import ctypes
import webbrowser
from proxy_server import ProxyServer
from cert_installer import install_ca, is_ca_trusted
from mitm import CA_CERT_FILE, MITMCertManager

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
CONFIG_FILE = "config.json"

def set_system_proxy(enable: bool, server: str = ""):
    try:
        internet_settings = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_ALL_ACCESS
        )
        if enable:
            winreg.SetValueEx(internet_settings, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(internet_settings, "ProxyServer", 0, winreg.REG_SZ, server)
        else:
            winreg.SetValueEx(internet_settings, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(internet_settings)

        INTERNET_OPTION_REFRESH = 37
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        internet_set_option = ctypes.windll.wininet.InternetSetOptionW
        internet_set_option(0, INTERNET_OPTION_REFRESH, 0, 0)
        internet_set_option(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
    except Exception:
        pass

class VPNApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MasterVPN")
        self.geometry("450x550")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.proxy_loop = None
        self.proxy_task = None
        self.is_running = False
        self.load_config()
        self.setup_ui()

    def load_config(self):
        self.config = {
            "mode": "apps_script",
            "google_ip": "216.239.38.120",
            "front_domain": "www.google.com",
            "script_id": "",
            "auth_key": "",
            "listen_host": "127.0.0.1",
            "listen_port": 8085,
            "log_level": "INFO",
            "verify_ssl": True
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config.update(json.load(f))
            except:
                pass

    def save_config(self):
        self.config["script_id"] = self.script_id_entry.get().strip()
        self.config["auth_key"] = self.auth_key_entry.get().strip()
        if "script_ids" in self.config:
            self.config["script_ids"] = [self.config["script_id"]]
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    def _copy_text(self, widget, event=None):
        try:
            text = widget.selection_get()
        except tk.TclError:
            text = widget.get()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        return "break"

    def _paste_text(self, widget, event=None):
        widget.focus_set()
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"
        
        try:
            if widget.selection_present():
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
            
        widget.insert(tk.INSERT, text)
        return "break"

    def _cut_text(self, widget, event=None):
        try:
            text = widget.selection_get()
        except tk.TclError:
            text = widget.get()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            try:
                widget.delete("sel.first", "sel.last")
            except tk.TclError:
                widget.delete(0, tk.END)
            self.update_idletasks()
        return "break"

    def apply_context_menu(self, entry_widget):
        menu = tk.Menu(self, tearoff=0, bg="#2b2b2b", fg="white", activebackground="#1f538d")
        menu.add_command(label="Copy", command=lambda w=entry_widget: self._copy_text(w))
        menu.add_command(label="Paste", command=lambda w=entry_widget: self._paste_text(w))
        menu.add_command(label="Cut", command=lambda w=entry_widget: self._cut_text(w))

        def show_menu(event):
            entry_widget.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
            menu.grab_release()
            return "break"

        entry_widget.bind("<Button-3>", show_menu)
        entry_widget.bind("<Control-c>", lambda e, w=entry_widget: self._copy_text(w))
        entry_widget.bind("<Control-v>", lambda e, w=entry_widget: self._paste_text(w))
        entry_widget.bind("<Control-x>", lambda e, w=entry_widget: self._cut_text(w))

    def setup_ui(self):
        self.logo_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.logo_frame.pack(fill="x", pady=(30, 10))

        self.logo_label = ctk.CTkLabel(
            self.logo_frame,
            text="🛡️",
            font=("Segoe UI Emoji", 72),
            anchor="center",
            justify="center"
        )
        self.logo_label.pack(anchor="center")

        self.title_label = ctk.CTkLabel(self, text="Master VPN", font=("Segoe UI", 28, "bold"))
        self.title_label.pack(pady=(0, 25))

        self.script_id_entry = ctk.CTkEntry(self, placeholder_text="Google Script ID", width=360, height=45)
        self.script_id_entry.pack(pady=10)
        self.script_id_entry.insert(0, self.config.get("script_id", ""))
        self.apply_context_menu(self.script_id_entry)

        self.auth_key_entry = ctk.CTkEntry(self, placeholder_text="Auth Key (Secret)", show="*", width=360, height=45)
        self.auth_key_entry.pack(pady=10)
        self.auth_key_entry.insert(0, self.config.get("auth_key", ""))
        self.apply_context_menu(self.auth_key_entry)

        self.connect_btn = ctk.CTkButton(
            self,
            text="CONNECT",
            font=("Segoe UI", 18, "bold"),
            width=220,
            height=55,
            corner_radius=27,
            fg_color="#28a745",
            hover_color="#218838",
            command=self.toggle_connection
        )
        self.connect_btn.pack(pady=20)

        self.links_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.links_frame.pack(pady=5)

        self.github_link = ctk.CTkLabel(
            self.links_frame,
            text="GitHub",
            font=("Segoe UI", 14, "bold"),
            text_color="#00a8ff",
            cursor="hand2"
        )
        self.github_link.pack(side="left", padx=8)
        self.github_link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/AriPath/MasterVPN"))

        self.separator = ctk.CTkLabel(
            self.links_frame,
            text="|",
            font=("Segoe UI", 14, "bold"),
            text_color="white"
        )
        self.separator.pack(side="left")

        self.telegram_link = ctk.CTkLabel(
            self.links_frame,
            text="Telegram",
            font=("Segoe UI", 14, "bold"),
            text_color="#00a8ff",
            cursor="hand2"
        )
        self.telegram_link.pack(side="left", padx=8)
        self.telegram_link.bind("<Button-1>", lambda e: webbrowser.open("https://t.me/AriPath"))

        self.proxy_label = ctk.CTkLabel(self, text="Status: Disconnected", font=("Segoe UI", 14))
        self.proxy_label.pack(pady=15)

    def toggle_connection(self):
        if not self.is_running:
            self.start_vpn()
        else:
            self.stop_vpn()

    def start_vpn(self):
        if not self.script_id_entry.get() or not self.auth_key_entry.get():
            self.proxy_label.configure(text="Error: Missing Inputs!", text_color="red")
            return
        self.save_config()
        self.connect_btn.configure(text="CONNECTING...", state="disabled", fg_color="#ffc107")
        self.proxy_label.configure(text="Setting up system proxy...", text_color="yellow")
        self.update()
        threading.Thread(target=self._init_and_run).start()

    def _init_and_run(self):
        try:
            if not os.path.exists(CA_CERT_FILE):
                MITMCertManager()
            if not is_ca_trusted(CA_CERT_FILE):
                install_ca(CA_CERT_FILE)

            proxy_addr = f"{self.config.get('listen_host', '127.0.0.1')}:{self.config.get('listen_port', 8085)}"
            set_system_proxy(True, proxy_addr)

            self.proxy_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.proxy_loop)
            proxy = ProxyServer(self.config)

            self.is_running = True
            self.update_ui_state(True)

            self.proxy_task = self.proxy_loop.create_task(proxy.start())
            self.proxy_loop.run_until_complete(self.proxy_task)
        except asyncio.CancelledError:
            pass
        except Exception:
            self.is_running = False
            self.update_ui_state(False)

    def stop_vpn(self):
        self.is_running = False
        self.connect_btn.configure(text="DISCONNECTING...", state="disabled")
        set_system_proxy(False)
        if self.proxy_loop and self.proxy_task:
            self.proxy_loop.call_soon_threadsafe(self.proxy_task.cancel)
        self.update_ui_state(False)

    def update_ui_state(self, running):
        if running:
            self.connect_btn.configure(text="DISCONNECT", fg_color="#dc3545", hover_color="#c82333", state="normal")
            self.proxy_label.configure(text="Status: Connected (Auto Proxy Set)", text_color="#28a745")
        else:
            self.connect_btn.configure(text="CONNECT", fg_color="#28a745", hover_color="#218838", state="normal")
            self.proxy_label.configure(text="Status: Disconnected", text_color="white")

    def on_closing(self):
        if self.is_running:
            self.stop_vpn()
        set_system_proxy(False)
        self.destroy()
        os._exit(0)

if __name__ == "__main__":
    app = VPNApp()
    app.mainloop()
