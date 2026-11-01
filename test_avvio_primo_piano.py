"""Collaudo mirato: countdown all'avvio sul monitor principale e primo piano costante.

Uso: python test_avvio_primo_piano.py <percorso sleep_countdown.pyw>
Impostazioni in una cartella temporanea, nessuna azione reale.
"""
import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from types import SimpleNamespace

loader = importlib.machinery.SourceFileLoader("sc", sys.argv[1])
spec = importlib.util.spec_from_loader("sc", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

# --- Isolamento: impostazioni temporanee, nessuna azione, nessuna notifica ---
tmp = tempfile.mkdtemp(prefix="sonno_test_cima_")
m.CARTELLA_DATI = tmp
m.FILE_IMPOSTAZIONI = os.path.join(tmp, "impostazioni.json")
m.esegui_azione = lambda azione, gentile: None
m.imposta_anti_sospensione = lambda attiva: None
m.SonnoApp._notifica = lambda self, titolo, testo: None
m.SonnoApp._crea_icona = lambda self: None

u32 = ctypes.WinDLL("user32")
u32.GetTopWindow.argtypes = [wintypes.HWND]
u32.GetTopWindow.restype = wintypes.HWND
u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetWindow.restype = wintypes.HWND
u32.GetForegroundWindow.restype = wintypes.HWND
u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetAncestor.restype = wintypes.HWND
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wintypes.HWND]

esiti = []


def verifica(cond, nome):
    esiti.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + nome, flush=True)


def radice(finestra):
    return u32.GetAncestor(finestra.winfo_id(), 2)


def ordine_z():
    """HWND visibili dall'alto verso il basso."""
    lista, h = [], u32.GetTopWindow(None)
    while h:
        if u32.IsWindowVisible(h):
            lista.append(h)
        h = u32.GetWindow(h, 2)   # GW_HWNDNEXT
    return lista


def classe(h):
    buf = ctypes.create_unicode_buffer(64)
    u32.GetClassNameW(h, buf, 64)
    return buf.value


# --- Monitor: principale e uno secondario (se c'e') ---
monitor = []
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)


def _raccogli(hmon, _hdc, _rect, _lp):
    info = m._MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    u32.GetMonitorInfoW(hmon, ctypes.byref(info))
    r = info.rcWork
    monitor.append((bool(info.dwFlags & 1), (r.left, r.top, r.right, r.bottom)))
    return True


u32.EnumDisplayMonitors(None, None, PROC(_raccogli), 0)
primario = next(a for p, a in monitor if p)
secondario = next((a for p, a in monitor if not p), None)
print("Monitor:", monitor, flush=True)

# Posizione salvata sul monitor secondario: all'avvio deve vincere il principale
imp = m.impostazioni_predefinite()
imp["spegnimento"]["prova"] = True
if secondario:
    imp["countdown_pos"] = [secondario[0] + 40, secondario[1] + 40]
m.salva_impostazioni(imp)

root = tk.Tk()
app = m.SonnoApp(root, nascosta=True, istanza=None)
root.after(60000, root.destroy)   # sicurezza


def dentro(area, x, y):
    return area[0] <= x < area[2] and area[1] <= y < area[3]


def passo1():
    cd = app.countdown
    verifica(cd.visibile, "avvio nascosto: il countdown compare subito")
    w = cd.win
    cx, cy = w.winfo_rootx() + w.winfo_width() // 2, w.winfo_rooty() + w.winfo_height() // 2
    verifica(dentro(primario, cx, cy), f"avvio: countdown sul monitor principale ({cx},{cy})")
    verifica(not root.winfo_viewable(), "avvio nascosto: finestra principale nascosta")
    # Regola della posizione: salvata sul principale = rispettata, altrove = angolo del principale
    w_, h_ = w.winfo_reqwidth(), w.winfo_reqheight()
    app.imp["countdown_pos"] = [primario[0] + 5, primario[1] + 3]
    verifica(cd._posizione(True) == (primario[0] + 5, primario[1] + 3),
             "posizione salvata sul principale: rispettata all'avvio")
    if secondario:
        pos2 = [secondario[0] + 40, secondario[1] + 40]
        app.imp["countdown_pos"] = pos2
        verifica(cd._posizione(True) == m._angolo(primario, w_, h_),
                 "posizione salvata sul secondario: all'avvio angolo del principale")
        verifica(cd._posizione(False) == tuple(pos2),
                 "durante la sessione la posizione sul secondario resta valida")
    verifica(app._cd_al_primario is False, "la regola del principale vale solo al primo aggiornamento")
    # Ferma il tick per un controllo deterministico dell'ordine delle finestre
    app._vivo = False
    root.after(1500, passo2)


altra = None


def passo2():
    global altra
    cd = app.countdown
    # Un'altra finestra 'sempre in primo piano' sopra il countdown
    altra = tk.Toplevel(root)
    altra.overrideredirect(True)
    altra.attributes("-topmost", True)
    altra.geometry(f"200x60+{cd.win.winfo_rootx()}+{cd.win.winfo_rooty()}")
    altra.configure(bg="#444444")
    altra.update()
    altra.lift()
    altra.update()
    z = ordine_z()
    h_cd, h_altra = radice(cd.win), radice(altra)
    prima = z.index(h_altra) < z.index(h_cd)
    print("Prima del riordino: l'altra finestra sta sopra =", prima, flush=True)
    fg = u32.GetForegroundWindow()
    cd.mantieni_in_cima()
    root.update()
    z = ordine_z()
    verifica(z.index(h_cd) < z.index(h_altra), "dopo il riordino il countdown sta sopra l'altra finestra in primo piano")
    verifica(u32.GetForegroundWindow() == fg, "il riordino non sposta il focus")
    root.after(300, passo3)


def passo3():
    cd = app.countdown
    rilievi = {}

    def a_menu_aperto():
        rilievi["flag"] = cd._menu_aperto is not None
        cd.mantieni_in_cima()   # deve astenersi
        z = ordine_z()
        menu = [h for h in z if classe(h) == "#32768"]
        h_cd = radice(cd.win)
        rilievi["menu_visto"] = bool(menu)
        rilievi["menu_sopra"] = bool(menu) and z.index(menu[0]) < z.index(h_cd)
        u32.EndMenu()

    def scorta():
        # Se i timer non girano a menu aperto, chiudi il menu col tasto Esc
        time.sleep(6)
        if "flag" not in rilievi:
            u32.keybd_event(0x1B, 0, 0, 0)
            u32.keybd_event(0x1B, 0, 2, 0)

    threading.Thread(target=scorta, daemon=True).start()
    root.after(1200, a_menu_aperto)
    x, y = cd.win.winfo_rootx() + 10, cd.win.winfo_rooty() + 10
    t0 = time.monotonic()
    cd._menu_contestuale(SimpleNamespace(x_root=x, y_root=y))
    durata = time.monotonic() - t0
    print(f"Menu rimasto aperto {durata:.1f} s; rilievi {rilievi}", flush=True)
    verifica(durata >= 1.0, "il menu del tasto destro blocca fino alla chiusura")
    verifica(rilievi.get("flag"), "a menu aperto il riordino e' sospeso")
    verifica(rilievi.get("menu_sopra"), "a menu aperto il menu resta sopra il countdown")
    verifica(cd._menu_aperto is None, "a menu chiuso il riordino riprende")
    root.after(200, root.destroy)


root.after(1800, passo1)
root.mainloop()
print(f"\n{sum(esiti)}/{len(esiti)} verifiche superate", flush=True)
