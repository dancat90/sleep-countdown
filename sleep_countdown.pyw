"""
Calcolatore ore di sonno con spegnimento automatico del PC - GUI real-time.

Calcola A RITROSO dalla sveglia l'orario a cui andare a dormire (inizio
della routine serale), dati le ore di sonno desiderate e i minuti di
lettura, scontando i tempi fissi: buffer addormentamento, passeggiata
cane, riassetto cucina + pulizia personale. Il tasto "Vado a dormire ora"
passa al calcolo IN AVANTI: dall'ora attuale ricava l'ora della sveglia.
Il modo in avanti e' solo una vista: lo spegnimento segue sempre le
sveglie della settimana. All'avvio si riparte a ritroso.

Integra un timer di spegnimento sul modello di Shutdown Timer Classic:
l'inizio della routine serale (piu' una correzione in minuti) diventa
l'orario dell'azione finale (spegni, riavvia, sospendi, iberna, blocca,
disconnetti). Countdown sempre in primo piano con i colori a soglie,
visibile da subito all'avvio sul monitor principale, icona nell'area di notifica, avvisi a 30/10/1 minuti, rinvii limitati,
annullo per la serata, timer manuale classico. Sveglia per giorno della
settimana: un giorno spento non ha sveglia e la sera prima non spegne.

Uso: pythonw sleep_countdown.pyw [--nascosta]
  --nascosta  finestra principale nascosta: restano icona e countdown (avvio all'accesso)
Una seconda esecuzione non apre un altro timer: mostra la finestra attiva.

Gemello desktop della web app PWA (stesso repo: index.html); la parte di
spegnimento esiste solo qui.
"""

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from ctypes import wintypes
from datetime import datetime, time as dtime, timedelta
from tkinter import messagebox, ttk
from typing import NamedTuple

try:
    # Icona nell'area di notifica: senza pystray l'app funziona lo stesso,
    # ma senza icona e senza notifiche
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None

# === Tempi fissi (minuti) - modificabili qui ===
BUFFER_ADDORMENTAMENTO_MIN = 15   # tempo per prendere sonno una volta a letto
PASSEGGIATA_CANE_MIN = 20         # passeggiata cane
CUCINA_IGIENE_MIN = 15            # riassetto cucina + pulizia personale
SOGLIA_GIALLO_MIN = 30            # entro quanto dall'ora di coricarsi scatta il giallo

# === Spegnimento automatico - modificabili qui ===
AZIONI = ("Spegni", "Riavvia", "Sospendi", "Iberna", "Blocca", "Disconnetti")
MAX_RINVII = 2                    # rinvii concessi per ogni serata
RINVIO_MIN = 15                   # durata di un rinvio
SOGLIE_AVVISO_MIN = (30, 10, 1)   # a queste soglie: notifica + countdown in primo piano
CORREZIONE_MAX_MIN = 120          # correzione ammessa: da -120 a +120 minuti
GAP_MAX_S = 30                    # salto oltre questo fra due tick = PC sospeso o spento
COUNTDOWN_DIM = 12                # dimensione predefinita del countdown (punti)
COUNTDOWN_DIM_MIN = 8             # limiti della regolazione con la rotellina
COUNTDOWN_DIM_MAX = 32

# === Palette (coerente con la web app) ===
BG = "#0d1117"
FG_TITLE = "#c9d1d9"
FG_MUTED = "#8b949e"
FG_DIM = "#6e7681"
FG_OFF = "#484f58"    # campo escluso dal calcolo corrente
COL_OK = "#3fb950"
COL_WARN = "#d29922"
COL_BAD = "#f85149"
COL_INFO = "#58a6ff"
CAMPO_BG = "#161b22"
BORDO = "#30363d"
BTN_BG = "#21262d"
# Colori del countdown oltre a quelli della palette (soglie di Shutdown Timer Classic)
CD_ARANCIO = "#db6d28"
CD_ROSSO = "#da3633"

# === Percorsi e nomi ===
CARTELLA_DATI = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "SonnoSpegnimento")
FILE_IMPOSTAZIONI = os.path.join(CARTELLA_DATI, "impostazioni.json")
NOME_ISTANZA = "SonnoSpegnimento"

# Giorni abbreviati in italiano (strftime('%a') dipende dal locale di sistema)
GIORNI = {0: "lun", 1: "mar", 2: "mer", 3: "gio", 4: "ven", 5: "sab", 6: "dom"}
NOMI_GIORNI = ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica")
ORE = [f"{i:02d}" for i in range(24)]
MINUTI5 = [f"{i:02d}" for i in range(0, 60, 5)]


# ======================================================================
# Logica pura (senza GUI): calcolo, impostazioni, regola di scatto
# ======================================================================

def adesso() -> datetime:
    """Orologio dell'app: punto unico, sostituibile nei test."""
    return datetime.now()


def registra_errore(testo: str) -> None:
    """Appende un errore a errori.log nella cartella dati (pythonw non ha console)."""
    try:
        os.makedirs(CARTELLA_DATI, exist_ok=True)
        with open(os.path.join(CARTELLA_DATI, "errori.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {testo.rstrip()}\n")
    except OSError:
        pass


def parse_ora(testo) -> tuple[int, int]:
    """'HH:MM' -> (h, m). ValueError se il formato non e' valido."""
    h, m = (int(x) for x in str(testo).split(":"))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(testo)
    return h, m


def impostazioni_predefinite() -> dict:
    return {
        "versione": 1,
        "sveglie": {str(g): {"attiva": True, "ora": "07:00"} for g in range(7)},
        "sonno_h": 8,
        "sonno_m": 0,
        "lettura_min": 30,
        "lettura_on": True,
        "cane_on": True,
        "cucina_on": True,
        "spegnimento": {
            "attivo": True,
            "azione": "Spegni",
            "correzione_min": 0,
            "gentile": False,
            "impedisci_sospensione": True,
            "prova": False,
        },
        "countdown_pos": None,
        "countdown_dim": COUNTDOWN_DIM,
        # Chiave = data della sveglia: {"rinvii": n, "annullata": bool, "eseguita": bool}
        "serate": {},
    }


def _unisci(base: dict, dati: dict) -> None:
    """Copia in base i valori di dati con lo stesso tipo; ignora tutto il resto."""
    for chiave, valore in base.items():
        if chiave not in dati:
            continue
        nuovo = dati[chiave]
        if chiave == "serate":
            if isinstance(nuovo, dict):
                base[chiave] = {k: v for k, v in nuovo.items() if isinstance(v, dict)}
        elif chiave == "countdown_pos":
            if (isinstance(nuovo, list) and len(nuovo) == 2
                    and all(type(x) is int for x in nuovo)):
                base[chiave] = nuovo
        elif isinstance(valore, dict):
            if isinstance(nuovo, dict):
                _unisci(valore, nuovo)
        elif type(nuovo) is type(valore):
            base[chiave] = nuovo


def carica_impostazioni(percorso: str | None = None) -> dict:
    """Impostazioni dal JSON, completate e ripulite; predefinite se il file manca o e' rotto."""
    imp = impostazioni_predefinite()
    try:
        with open(percorso or FILE_IMPOSTAZIONI, encoding="utf-8") as f:
            dati = json.load(f)
        if isinstance(dati, dict):
            _unisci(imp, dati)
    except (OSError, ValueError):
        pass
    # Valori fuori dominio riportati ai predefiniti
    for voce in imp["sveglie"].values():
        try:
            parse_ora(voce["ora"])
        except ValueError:
            voce["ora"] = "07:00"
    sp = imp["spegnimento"]
    if sp["azione"] not in AZIONI:
        sp["azione"] = "Spegni"
    sp["correzione_min"] = max(-CORREZIONE_MAX_MIN, min(CORREZIONE_MAX_MIN, sp["correzione_min"]))
    imp["countdown_dim"] = max(COUNTDOWN_DIM_MIN, min(COUNTDOWN_DIM_MAX, imp["countdown_dim"]))
    if not 0 <= imp["sonno_h"] <= 23:
        imp["sonno_h"] = 8
    if not 0 <= imp["sonno_m"] <= 59:
        imp["sonno_m"] = 0
    if imp["lettura_min"] < 0:
        imp["lettura_min"] = 30
    # Le serate piu' vecchie di 10 giorni non servono piu'
    limite = (adesso() - timedelta(days=10)).date().isoformat()
    imp["serate"] = {k: v for k, v in imp["serate"].items() if k >= limite}
    return imp


def salva_impostazioni(imp: dict, percorso: str | None = None) -> None:
    """Scrittura atomica: file temporaneo + sostituzione."""
    percorso = percorso or FILE_IMPOSTAZIONI
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    temporaneo = percorso + ".tmp"
    with open(temporaneo, "w", encoding="utf-8") as f:
        json.dump(imp, f, ensure_ascii=False, indent=2)
    os.replace(temporaneo, percorso)


def prossima_sveglia(now: datetime, sveglie: dict) -> datetime | None:
    """Prima sveglia attiva dopo now, cercata fino a 7 giorni avanti."""
    for giorni in range(8):
        data = (now + timedelta(days=giorni)).date()
        voce = sveglie.get(str(data.weekday()), {})
        if not voce.get("attiva"):
            continue
        try:
            h, m = parse_ora(voce.get("ora"))
        except ValueError:
            continue
        candidata = datetime.combine(data, dtime(h, m))
        if candidata > now:
            return candidata
    return None


def calcola_catena(sveglia, sonno_h, sonno_m, lettura, cane, cucina):
    """Catena a ritroso: sveglia -> (addormentato, a letto, inizio routine)."""
    addormentato = sveglia - timedelta(hours=sonno_h, minutes=sonno_m)
    a_letto = addormentato - timedelta(minutes=BUFFER_ADDORMENTAMENTO_MIN)
    vai_a_dormire = a_letto - timedelta(minutes=lettura + cane + cucina)
    return addormentato, a_letto, vai_a_dormire


def calcola_catena_avanti(now, sonno_h, sonno_m, lettura, cane, cucina):
    """Catena in avanti: la routine parte adesso -> (a letto, addormentato, sveglia)."""
    a_letto = now + timedelta(minutes=lettura + cane + cucina)
    addormentato = a_letto + timedelta(minutes=BUFFER_ADDORMENTAMENTO_MIN)
    sveglia = addormentato + timedelta(hours=sonno_h, minutes=sonno_m)
    return a_letto, addormentato, sveglia


def formatta_sonno(minuti: int) -> str:
    """Durata del sonno: '7h' oppure '7h30'."""
    return f"{minuti // 60}h" if minuti % 60 == 0 else f"{minuti // 60}h{minuti % 60:02d}"


def chiave_serata(sveglia: datetime) -> str:
    """Una serata = la notte prima di quella sveglia; chiave = data della sveglia."""
    return sveglia.date().isoformat()


def deve_scattare(prev_now, prev_target, now, target, gap_max_s=GAP_MAX_S) -> bool:
    """Vero solo se il tempo che scorre attraversa il bersaglio sotto osservazione.

    Niente scatto se:
    - fra i due tick c'e' un salto (PC sospeso o spento, app avviata tardi);
    - il bersaglio e' cambiato in questo tick (una modifica lo porta nel passato);
    - il bersaglio era gia' passato al tick precedente.
    """
    if prev_now is None or prev_target is None or target is None:
        return False
    if prev_target != target:
        return False
    if (now - prev_now).total_seconds() > gap_max_s:
        return False
    return prev_now < target <= now


def formatta_durata(secondi: float) -> str:
    """Durata leggibile: '1g 05h 20m', '2h 05m', '15 min', 'meno di un minuto'."""
    minuti = int(max(0.0, secondi) // 60)
    giorni, minuti = divmod(minuti, 1440)
    ore, minuti = divmod(minuti, 60)
    if giorni:
        return f"{giorni}g {ore:02d}h {minuti:02d}m"
    if ore:
        return f"{ore}h {minuti:02d}m"
    if minuti:
        return f"{minuti} min"
    return "meno di un minuto"


def formatta_orologio(secondi: float) -> str:
    """Countdown 'H:MM:SS' (con i giorni davanti oltre le 24 ore)."""
    # Arrotondamento per eccesso: lo zero coincide con lo scatto
    s = int(max(0.0, secondi) + 0.999)
    giorni, s = divmod(s, 86400)
    ore, s = divmod(s, 3600)
    minuti, s = divmod(s, 60)
    testo = f"{ore}:{minuti:02d}:{s:02d}"
    return f"{giorni}g {testo}" if giorni else testo


def colori_countdown(secondi: float, fase: bool = False) -> tuple[str, str]:
    """(sfondo, testo) in versione discreta: sfondo scuro e testo verde > 30',
    giallo > 10', arancio > 1', poi sfondo rosso lampeggiante nell'ultimo minuto."""
    if secondi > 30 * 60:
        return BG, COL_OK
    if secondi > 10 * 60:
        return BG, COL_WARN
    if secondi > 60:
        return BG, CD_ARANCIO
    return (BG, CD_ROSSO) if fase else (CD_ROSSO, "#ffffff")


class Stato(NamedTuple):
    """Fotografia dello spegnimento in un istante."""
    tipo: str | None            # "sonno", "manuale" oppure None
    target: datetime | None     # orario effettivo (correzione e rinvii inclusi)
    azione: str
    esito: str                  # armato, in_ritardo, annullata, eseguita, disattivo, nessuna_sveglia
    rinvii: int = 0
    chiave: str | None = None   # chiave della serata (solo tipo "sonno")


# ======================================================================
# Windows: azioni finali, anti-sospensione, istanza singola
# ======================================================================

_CREATE_NO_WINDOW = 0x08000000
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def ibernazione_disponibile() -> bool:
    """SYSTEM_POWER_CAPABILITIES: byte 6 = SystemS4, byte 8 = HiberFilePresent."""
    buffer = ctypes.create_string_buffer(128)
    try:
        if not ctypes.WinDLL("powrprof").GetPwrCapabilities(buffer):
            return False
    except (OSError, AttributeError):
        return False
    return bool(buffer.raw[6]) and bool(buffer.raw[8])


def imposta_anti_sospensione(attiva: bool) -> None:
    """Impedisce (o riconsente) la sospensione automatica; lo schermo puo' spegnersi."""
    k32 = ctypes.WinDLL("kernel32")
    k32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    k32.SetThreadExecutionState.restype = ctypes.c_uint32
    k32.SetThreadExecutionState(_ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if attiva else 0))


def esegui_azione(azione: str, gentile: bool) -> str | None:
    """Esegue l'azione finale. None se riuscita, altrimenti il messaggio d'errore."""
    forza = [] if gentile else ["/f"]   # /f chiude le app anche se non rispondono
    comandi = {
        "Spegni": ["shutdown", "/s", "/t", "0", *forza],
        "Riavvia": ["shutdown", "/r", "/t", "0", *forza],
        "Iberna": ["shutdown", "/h", *forza],
        "Disconnetti": ["shutdown", "/l", *forza],
    }
    try:
        if azione == "Sospendi":
            # bHibernate=False: sospensione vera (S3), non ibernazione
            ok = ctypes.WinDLL("powrprof").SetSuspendState(False, not gentile, False)
            return None if ok else "Windows ha rifiutato la sospensione"
        if azione == "Blocca":
            ok = ctypes.WinDLL("user32").LockWorkStation()
            return None if ok else "Windows ha rifiutato il blocco"
        esito = subprocess.run(
            comandi[azione], capture_output=True, text=True, errors="replace",
            creationflags=_CREATE_NO_WINDOW, timeout=30)
    except (OSError, KeyError, subprocess.SubprocessError) as e:
        return str(e)
    if esito.returncode != 0:
        return (esito.stderr or esito.stdout or f"codice {esito.returncode}").strip()
    return None


class IstanzaSingola:
    """Mutex con nome: la seconda istanza chiede alla prima di mostrarsi ed esce."""

    _ERROR_ALREADY_EXISTS = 183
    _EVENT_MODIFY_STATE = 0x0002

    def __init__(self, nome: str) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.OpenEventW.restype = wintypes.HANDLE
        k32.SetEvent.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32
        self._evento = None
        self._mutex = k32.CreateMutexW(None, False, f"Local\\{nome}")
        self.prima = ctypes.get_last_error() != self._ERROR_ALREADY_EXISTS
        nome_evento = f"Local\\{nome}_mostra"
        if self.prima:
            # Evento auto-reset: la seconda istanza lo segnala, la prima lo consuma
            self._evento = k32.CreateEventW(None, False, False, nome_evento)
        else:
            evento = k32.OpenEventW(self._EVENT_MODIFY_STATE, False, nome_evento)
            if evento:
                k32.SetEvent(evento)
                k32.CloseHandle(evento)

    def richiesta_mostra(self) -> bool:
        """Vero (una volta) se un'altra esecuzione ha chiesto di mostrare la finestra."""
        return bool(self._evento) and self._k32.WaitForSingleObject(self._evento, 0) == 0

    def chiudi(self) -> None:
        for handle in (self._evento, self._mutex):
            if handle:
                self._k32.CloseHandle(handle)
        self._evento = self._mutex = None


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _user32():
    u32 = ctypes.WinDLL("user32")
    u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    u32.MonitorFromPoint.restype = wintypes.HMONITOR
    u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
    u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u32.GetAncestor.restype = wintypes.HWND
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u32.SetWindowPos.restype = wintypes.BOOL
    return u32


# --- Primo piano: SetWindowPos(HWND_TOPMOST) senza spostare, ridimensionare o attivare ---
_HWND_TOPMOST = -1
_SWP_IN_CIMA = 0x0001 | 0x0002 | 0x0010 | 0x0200   # NOSIZE | NOMOVE | NOACTIVATE | NOOWNERZORDER
_GA_ROOT = 2
MENU_PAUSA_MAX_S = 15   # rete di sicurezza: oltre questo il menu si considera chiuso


def porta_in_cima(finestra) -> None:
    """Porta la finestra sopra tutte, comprese le altre 'sempre in primo piano', senza focus."""
    u32 = _user32()
    # winfo_id e' la finestra interna di Tk: quella da riordinare e' la radice
    hwnd = u32.GetAncestor(finestra.winfo_id(), _GA_ROOT)
    if hwnd:
        u32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_IN_CIMA)


def monitor_di(x: int, y: int, nullo_se_fuori: bool = False):
    """(handle, area di lavoro) del monitor che contiene il punto.

    Con nullo_se_fuori=True ritorna None se il punto non cade su nessun monitor
    (es. nel vuoto fra monitor di scala diversa); altrimenti usa il piu' vicino.
    """
    u32 = _user32()
    handle = u32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 0 if nullo_se_fuori else 2)
    if not handle:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not u32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return None
    r = info.rcWork
    return handle, (r.left, r.top, r.right, r.bottom)


def posizione_mouse() -> tuple[int, int]:
    punto = wintypes.POINT()
    _user32().GetCursorPos(ctypes.byref(punto))
    return punto.x, punto.y


def _angolo(area, w: int, h: int) -> tuple[int, int]:
    """Angolo in basso a destra di un'area di lavoro (sopra la barra delle applicazioni)."""
    sinistra, alto, destra, basso = area
    return max(sinistra, destra - w - 16), max(alto, basso - h - 16)


# ======================================================================
# Widget in tema scuro
# ======================================================================

def _etichetta(parent, text, size=11, fg=FG_MUTED, **kw):
    return tk.Label(parent, text=text, font=("Segoe UI", size), fg=fg, bg=BG, **kw)


def _combo(parent, var, values, width=4):
    return ttk.Combobox(parent, textvariable=var, values=values,
                        width=width, state="readonly", font=("Segoe UI", 11))


def _check(parent, var, text, bold=False):
    # Spunta in tema scuro: il testo resta leggibile anche quando non selezionata
    return tk.Checkbutton(
        parent, text=text, variable=var,
        font=("Segoe UI", 10, "bold" if bold else "normal"),
        fg=FG_TITLE if bold else FG_MUTED, bg=BG, activebackground=BG,
        activeforeground=FG_TITLE, selectcolor=CAMPO_BG,
        highlightthickness=0, bd=0, cursor="hand2",
    )


def _radio(parent, var, value, text):
    return tk.Radiobutton(
        parent, text=text, variable=var, value=value, font=("Segoe UI", 10),
        fg=FG_MUTED, bg=BG, activebackground=BG, activeforeground=FG_TITLE,
        selectcolor=CAMPO_BG, highlightthickness=0, bd=0, cursor="hand2",
    )


def _spin(parent, var, minimo, massimo, passo=1, larghezza=3):
    # Attenzione: alla creazione lo Spinbox riscrive la variabile col minimo
    return tk.Spinbox(
        parent, from_=minimo, to=massimo, increment=passo, textvariable=var,
        width=larghezza, font=("Segoe UI", 11), justify="center",
        bg=CAMPO_BG, fg=FG_TITLE, buttonbackground=BTN_BG, insertbackground=FG_TITLE,
        relief="flat", highlightthickness=1, highlightbackground=BORDO,
        highlightcolor=COL_INFO,
    )


def _bottone(parent, text, command):
    return tk.Button(
        parent, text=text, command=command, font=("Segoe UI", 9),
        fg=FG_TITLE, bg=BTN_BG, activebackground=BORDO, activeforeground=FG_TITLE,
        disabledforeground=FG_DIM, relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
    )


def _centra(win, root) -> None:
    """Dialogo al centro della finestra principale (o dello schermo se e' nascosta)."""
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    if root.winfo_viewable():
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 3
    else:
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


def disegna_icona(colore: str):
    """Falce di luna 64x64 nel colore di stato, per l'area di notifica."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    disegno = ImageDraw.Draw(img)
    disegno.ellipse((4, 4, 60, 60), fill=colore)
    disegno.ellipse((22, -4, 70, 44), fill=(0, 0, 0, 0))   # ritaglio: resta la falce
    return img


# ======================================================================
# Finestra di countdown (modello Shutdown Timer Classic)
# ======================================================================

class FinestraCountdown:
    """Countdown compatto su una riga: senza bordi, sempre in primo piano, trascinabile.

    Rotellina = dimensione (memorizzata), doppio clic = finestra principale,
    tasto destro = menu con rinvio, annullo e nascondi.
    """

    def __init__(self, app: "SonnoApp") -> None:
        self.app = app
        self.visibile = False
        self._drag = None
        self._menu_aperto = None   # istante di apertura del menu del tasto destro, None se chiuso
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)

        self.frame = tk.Frame(self.win, bg=BG, padx=8, pady=1, highlightthickness=1,
                              highlightbackground=BORDO)
        self.frame.pack(fill="both", expand=True)
        self.lbl_titolo = tk.Label(self.frame, bg=BG, fg=FG_MUTED)
        self.lbl_titolo.pack(side="left", padx=(0, 6))
        self.lbl_tempo = tk.Label(self.frame, bg=BG)
        self.lbl_tempo.pack(side="left")
        self._applica_dimensione()

        # Trascinamento col tasto sinistro, doppio clic = finestra principale,
        # destro = menu, rotellina = dimensione
        for w in (self.frame, self.lbl_titolo, self.lbl_tempo):
            w.bind("<ButtonPress-1>", self._inizio_drag)
            w.bind("<B1-Motion>", self._trascina)
            w.bind("<ButtonRelease-1>", self._fine_drag)
            w.bind("<Double-Button-1>", lambda _e: app.mostra_finestra())
            w.bind("<Button-3>", self._menu_contestuale)
            w.bind("<MouseWheel>", self._rotellina)

    def _applica_dimensione(self) -> None:
        dimensione = self.app.imp["countdown_dim"]
        self.lbl_tempo.config(font=("Segoe UI", dimensione, "bold"))
        self.lbl_titolo.config(font=("Segoe UI", max(7, round(dimensione * 0.7))))

    def _rotellina(self, evento) -> None:
        passo = 1 if evento.delta > 0 else -1
        nuova = max(COUNTDOWN_DIM_MIN,
                    min(COUNTDOWN_DIM_MAX, self.app.imp["countdown_dim"] + passo))
        if nuova != self.app.imp["countdown_dim"]:
            self.app.memorizza_dim_countdown(nuova)
            self._applica_dimensione()

    def mostra(self, solo_primario: bool = False) -> None:
        if not self.visibile:
            self.win.update_idletasks()
            x, y = self._posizione(solo_primario)
            self.win.geometry(f"+{x}+{y}")
            self.win.deiconify()
            self.visibile = True
        self.win.lift()
        self.win.attributes("-topmost", True)

    def mantieni_in_cima(self) -> None:
        """Chiamata a ogni tick: riporta il countdown sopra le altre finestre in primo piano.

        Sospesa a menu del tasto destro aperto, altrimenti il countdown lo coprirebbe.
        """
        if not self.visibile:
            return
        if self._menu_aperto is not None:
            if time.monotonic() - self._menu_aperto < MENU_PAUSA_MAX_S:
                return
            self._menu_aperto = None
        porta_in_cima(self.win)

    def nascondi(self) -> None:
        if self.visibile:
            self.win.withdraw()
            self.visibile = False

    def aggiorna(self, secondi, titolo, fase) -> None:
        sfondo, testo = colori_countdown(secondi, fase)
        # Bordo nel colore della soglia; titolo attenuato tranne nel lampeggio rosso
        self.frame.config(bg=sfondo, highlightbackground=testo)
        self.lbl_titolo.config(bg=sfondo, text=titolo, fg=FG_MUTED if sfondo == BG else testo)
        self.lbl_tempo.config(bg=sfondo, fg=testo, text=formatta_orologio(secondi))

    def porta_vicino_al_mouse(self) -> None:
        """Se il countdown sta su un monitor diverso da quello del mouse, lo porta li'."""
        self.win.update_idletasks()
        w = max(self.win.winfo_width(), self.win.winfo_reqwidth())
        h = max(self.win.winfo_height(), self.win.winfo_reqheight())
        mouse = monitor_di(*posizione_mouse())
        attuale = monitor_di(self.win.winfo_rootx() + w // 2, self.win.winfo_rooty() + h // 2)
        if mouse is None or (attuale is not None and attuale[0] == mouse[0]):
            return
        x, y = _angolo(mouse[1], w, h)
        self.win.geometry(f"+{x}+{y}")
        self.app.memorizza_pos_countdown(x, y)

    def _posizione(self, solo_primario: bool = False) -> tuple[int, int]:
        """Ultima posizione salvata, se cade su un monitor; altrimenti angolo del primario.

        Con solo_primario=True (avvio del programma) la posizione salvata vale solo
        se cade sul monitor principale.
        """
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        primario = monitor_di(0, 0)   # il monitor principale ha sempre l'origine in (0, 0)
        pos = self.app.imp.get("countdown_pos")
        if pos:
            monitor = monitor_di(pos[0] + w // 2, pos[1] + h // 2, nullo_se_fuori=True)
            if monitor and (not solo_primario
                            or (primario is not None and monitor[0] == primario[0])):
                return pos[0], pos[1]
        if primario:
            return _angolo(primario[1], w, h)
        return (self.win.winfo_screenwidth() - w - 24, self.win.winfo_screenheight() - h - 72)

    def _inizio_drag(self, evento) -> None:
        self._drag = (evento.x_root - self.win.winfo_x(), evento.y_root - self.win.winfo_y())

    def _trascina(self, evento) -> None:
        if self._drag:
            self.win.geometry(f"+{evento.x_root - self._drag[0]}+{evento.y_root - self._drag[1]}")

    def _fine_drag(self, _evento) -> None:
        if self._drag:
            self._drag = None
            self.app.memorizza_pos_countdown(self.win.winfo_x(), self.win.winfo_y())

    def _menu_contestuale(self, evento) -> None:
        menu = tk.Menu(self.win, tearoff=0)
        menu.add_command(label="Apri finestra principale", command=self.app.mostra_finestra)
        menu.add_command(label=f"Rinvia {RINVIO_MIN} min", command=self.app.rinvia,
                         state="normal" if self.app._puo_rinviare else "disabled")
        menu.add_command(label="Annulla un rinvio", command=self.app.annulla_rinvio,
                         state="normal" if self.app._puo_annullare_rinvio else "disabled")
        menu.add_command(label=self.app._etichetta_annulla_cd, command=self.app.annulla_o_ferma)
        menu.add_separator()
        menu.add_command(label="Nascondi", command=self.nascondi)
        # Su Windows tk_popup ritorna a menu chiuso: fino ad allora niente riordino in cima
        self._menu_aperto = time.monotonic()
        try:
            menu.tk_popup(evento.x_root, evento.y_root)
        finally:
            menu.grab_release()
            self._menu_aperto = None


# ======================================================================
# Dialoghi: sveglie della settimana, timer manuale
# ======================================================================

class DialogoSettimana:
    """Sveglia per ogni giorno; un giorno spento non ha sveglia (e la sera prima non spegne)."""

    def __init__(self, app: "SonnoApp") -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Sveglie della settimana")
        self.win.configure(bg=BG)
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        griglia = tk.Frame(self.win, bg=BG, padx=18, pady=14)
        griglia.pack()
        self._voci = []
        for g in range(7):
            voce = app.imp["sveglie"][str(g)]
            try:
                h, m = parse_ora(voce["ora"])
            except ValueError:
                h, m = 7, 0
            attiva = tk.BooleanVar(value=voce["attiva"])
            ore = tk.StringVar(value=f"{h:02d}")
            minuti = tk.StringVar(value=f"{m:02d}")
            _check(griglia, attiva, NOMI_GIORNI[g]).grid(row=g, column=0, sticky="w",
                                                         padx=(0, 12), pady=3)
            _combo(griglia, ore, ORE).grid(row=g, column=1)
            _etichetta(griglia, ":", size=12, fg=FG_TITLE).grid(row=g, column=2)
            _combo(griglia, minuti, MINUTI5).grid(row=g, column=3)
            for var in (attiva, ore, minuti):
                var.trace_add("write", lambda *_, g=g: self._applica(g))
            self._voci.append((attiva, ore, minuti))
        _etichetta(self.win, "Giorno spento: nessuna sveglia, e la sera prima\n"
                             "nessuno spegnimento automatico.",
                   size=9, fg=FG_DIM, justify="center").pack(padx=18)
        _bottone(self.win, "Chiudi", self.win.destroy).pack(pady=(10, 14))
        _centra(self.win, app.root)

    def _applica(self, giorno: int) -> None:
        attiva, ore, minuti = self._voci[giorno]
        self.app.imposta_sveglia(giorno, attiva.get(), f"{ore.get()}:{minuti.get()}")


class DialogoTimerManuale:
    """Timer classico, come Shutdown Timer Classic: azione + durata oppure orario."""

    def __init__(self, app: "SonnoApp") -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Timer manuale")
        self.win.configure(bg=BG)
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        self.azione = tk.StringVar(value=app.azione_var.get())
        self.modo = tk.StringVar(value="tra")
        self.ore = tk.StringVar()
        self.minuti = tk.StringVar()
        self.secondi = tk.StringVar()
        fra_un_ora = adesso() + timedelta(hours=1)
        self.alle_h = tk.StringVar(value=f"{fra_un_ora.hour:02d}")
        self.alle_m = tk.StringVar(value=f"{fra_un_ora.minute // 5 * 5:02d}")
        self.background = tk.BooleanVar(value=False)

        corpo = tk.Frame(self.win, bg=BG, padx=18, pady=14)
        corpo.pack()
        _etichetta(corpo, "Azione:", size=10).grid(row=0, column=0, sticky="e", pady=4)
        _combo(corpo, self.azione, app.azioni, width=11).grid(
            row=0, column=1, columnspan=6, sticky="w", padx=(6, 0))

        _radio(corpo, self.modo, "tra", "Tra").grid(row=1, column=0, sticky="e", pady=4)
        for col, (var, massimo, unita) in enumerate(
                ((self.ore, 99, "h"), (self.minuti, 59, "m"), (self.secondi, 59, "s"))):
            _spin(corpo, var, 0, massimo).grid(row=1, column=1 + col * 2, padx=(6, 0))
            _etichetta(corpo, unita, size=10).grid(row=1, column=2 + col * 2, sticky="w")
        # Lo Spinbox ha riscritto le variabili col minimo: valori iniziali qui
        self.ore.set("1")
        self.minuti.set("0")
        self.secondi.set("0")

        _radio(corpo, self.modo, "alle", "Alle").grid(row=2, column=0, sticky="e", pady=4)
        alle = tk.Frame(corpo, bg=BG)
        alle.grid(row=2, column=1, columnspan=6, sticky="w", padx=(6, 0))
        _combo(alle, self.alle_h, ORE).pack(side="left")
        _etichetta(alle, ":", size=12, fg=FG_TITLE).pack(side="left")
        _combo(alle, self.alle_m, MINUTI5).pack(side="left")

        _check(corpo, self.background, "Avvia in background (solo icona)").grid(
            row=3, column=0, columnspan=7, sticky="w", pady=(8, 0))
        self.errore = _etichetta(corpo, "", size=9, fg=COL_BAD)
        self.errore.grid(row=4, column=0, columnspan=7)

        pulsanti = tk.Frame(self.win, bg=BG)
        pulsanti.pack(pady=(0, 14))
        _bottone(pulsanti, "Avvia", self._avvia).pack(side="left", padx=4)
        _bottone(pulsanti, "Chiudi", self.win.destroy).pack(side="left", padx=4)
        _centra(self.win, app.root)

    def _avvia(self) -> None:
        now = adesso()
        try:
            if self.modo.get() == "tra":
                parti = [int(v.get()) for v in (self.ore, self.minuti, self.secondi)]
                durata = parti[0] * 3600 + parti[1] * 60 + parti[2]
                if min(parti) < 0 or durata <= 0:
                    raise ValueError
                target = now + timedelta(seconds=durata)
            else:
                h, m = int(self.alle_h.get()), int(self.alle_m.get())
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
        except ValueError:
            self.errore.config(text="Durata o orario non validi")
            return
        self.app.avvia_manuale(target, self.azione.get(), mostra=not self.background.get())
        self.win.destroy()


# ======================================================================
# Applicazione
# ======================================================================

class SonnoApp:
    def __init__(self, root: tk.Tk, nascosta: bool = False, istanza=None) -> None:
        self.root = root
        self.istanza = istanza
        self.imp = carica_impostazioni()
        self._pronta = False
        self._vivo = True

        self.root.title("A che ora dormire")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.minsize(460, 0)
        # La X nasconde soltanto: il timer resta attivo nell'area di notifica
        self.root.protocol("WM_DELETE_WINDOW", self.nascondi_finestra)
        self.root.report_callback_exception = self._errore_tk
        if pystray is not None:
            # Luna come icona di tutte le finestre (riferimento tenuto vivo contro il GC)
            from PIL import ImageTk
            self._icona_finestra = ImageTk.PhotoImage(disegna_icona(COL_INFO))
            self.root.iconphoto(True, self._icona_finestra)

        imp, sp = self.imp, self.imp["spegnimento"]
        # Variabili di input: la riga Sveglia mostra il giorno della prossima sveglia
        self.hour_var = tk.StringVar(value="07")
        self.minute_var = tk.StringVar(value="00")
        self.sleep_h_var = tk.StringVar(value=f"{imp['sonno_h']:02d}")
        self.sleep_m_var = tk.StringVar(value=f"{imp['sonno_m']:02d}")
        self.reading_var = tk.StringVar(value=str(imp["lettura_min"]))
        # Spunte attivita' serali: se disattivate, il tempo relativo non viene sottratto
        self.reading_on = tk.BooleanVar(value=imp["lettura_on"])
        self.cane_on = tk.BooleanVar(value=imp["cane_on"])
        self.cucina_on = tk.BooleanVar(value=imp["cucina_on"])
        # Opzioni di spegnimento (Iberna solo se Windows la rende disponibile)
        self.azioni = [a for a in AZIONI if a != "Iberna" or ibernazione_disponibile()]
        if sp["azione"] not in self.azioni:
            sp["azione"] = "Spegni"
        self.auto_on = tk.BooleanVar(value=sp["attivo"])
        self.azione_var = tk.StringVar(value=sp["azione"])
        self.correzione_var = tk.StringVar()
        self.gentile_var = tk.BooleanVar(value=sp["gentile"])
        self.anti_sosp_var = tk.BooleanVar(value=sp["impedisci_sospensione"])
        self.prova_var = tk.BooleanVar(value=sp["prova"])

        # Stato di esecuzione
        self.manuale = None           # {"target", "azione"} se il timer manuale e' attivo
        self._giorno_rif = None       # giorno della settimana mostrato nella riga Sveglia
        self._sync = False            # vero mentre l'app riscrive da sola le tendine Sveglia
        self._prev_now = None         # tick precedente, per la regola di scatto
        self._prev_target = None
        self._notificate = set()      # (bersaglio, soglia) gia' avvisati
        # All'avvio il countdown compare subito, e sul monitor principale
        self._mostra_cd = True        # richiesta di mostrare il countdown al prossimo aggiornamento
        self._cd_al_primario = True   # vale solo per il primo aggiornamento
        self._anti_sosp = None
        self._da_salvare = False
        self._fase = False            # lampeggio dell'ultimo minuto
        self._dlg_settimana = None
        self._dlg_manuale = None
        # False = a ritroso dalla sveglia, True = in avanti dall'ora attuale (non memorizzato)
        self.modo_avanti = False
        # Stato letto dal menu dell'icona (thread di pystray): solo valori semplici
        self._puo_rinviare = False
        self._puo_annullare_rinvio = False
        self._puo_annullare = False
        self._etichetta_annulla = "Annulla stasera"
        self._etichetta_annulla_cd = "Annulla stasera"
        self._firma_icona = None
        self._comandi = queue.Queue()

        self._build_ui()
        self.correzione_var.set(str(sp["correzione_min"]))
        self.countdown = FinestraCountdown(self)

        for var in (self.hour_var, self.minute_var):
            var.trace_add("write", self._sveglia_modificata)
        for var in (self.sleep_h_var, self.sleep_m_var, self.reading_var,
                    self.reading_on, self.cane_on, self.cucina_on):
            var.trace_add("write", self._input_modificato)
        for var in (self.auto_on, self.azione_var, self.correzione_var,
                    self.gentile_var, self.anti_sosp_var, self.prova_var):
            var.trace_add("write", self._opzioni_modificate)

        self.icona = self._crea_icona()
        if nascosta:
            self.root.withdraw()
            self.root.after(2000, self._notifica_avvio)
        self._pronta = True
        self._tick()
        self._poll()

    # ------------------------------------------------------------------
    # Interfaccia
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        tk.Label(
            self.root, text="\U0001F319  A che ora dormire",
            font=("Segoe UI", 16, "bold"), fg=FG_TITLE, bg=BG,
        ).pack(pady=(15, 2))
        self.subtitle_label = tk.Label(
            self.root, text="L'ora per coricarti e dormire quanto vuoi",
            font=("Segoe UI", 9), fg=FG_DIM, bg=BG,
        )
        self.subtitle_label.pack(pady=(0, 10))

        form = tk.Frame(self.root, bg=BG)
        form.pack(pady=2)

        sleep_hours = [f"{i:02d}" for i in range(4, 13)]
        minutes15 = [f"{i:02d}" for i in range(0, 60, 15)]

        # --- Riga sveglia: modifica la sveglia del giorno indicato ---
        # (si attenua nel modo in avanti, dove la sveglia e' il risultato)
        self.sveglia_label = _etichetta(form, "Sveglia:")
        self.sveglia_label.grid(row=0, column=0, padx=5, pady=4, sticky="e")
        self.sveglia_h_combo = _combo(form, self.hour_var, ORE)
        self.sveglia_h_combo.grid(row=0, column=1)
        self.sveglia_due_punti = tk.Label(form, text=":", fg=FG_TITLE, bg=BG,
                                          font=("Segoe UI", 12, "bold"))
        self.sveglia_due_punti.grid(row=0, column=2)
        self.sveglia_m_combo = _combo(form, self.minute_var, MINUTI5)
        self.sveglia_m_combo.grid(row=0, column=3, sticky="w")
        _bottone(form, "Settimana…", self.apri_settimana).grid(row=0, column=4, padx=(8, 0))

        # --- Riga sonno desiderato ---
        _etichetta(form, "Voglio dormire:").grid(row=1, column=0, padx=5, pady=4, sticky="e")
        _combo(form, self.sleep_h_var, sleep_hours).grid(row=1, column=1)
        tk.Label(form, text=":", fg=FG_TITLE, bg=BG,
                 font=("Segoe UI", 12, "bold")).grid(row=1, column=2)
        _combo(form, self.sleep_m_var, minutes15).grid(row=1, column=3, sticky="w")

        # --- Riga lettura (spunta per escluderla senza azzerare i minuti) ---
        _check(form, self.reading_on, "Lettura (min):").grid(
            row=2, column=0, padx=5, pady=4, sticky="e")
        self.reading_entry = tk.Entry(
            form, textvariable=self.reading_var, width=6,
            font=("Segoe UI", 11), justify="center",
            bg=CAMPO_BG, fg=FG_TITLE, insertbackground=FG_TITLE,
            disabledbackground=BG, disabledforeground=FG_DIM,
            relief="flat", highlightthickness=1,
            highlightbackground=BORDO, highlightcolor=COL_INFO,
        )
        self.reading_entry.grid(row=2, column=1, columnspan=3, pady=4, sticky="w")

        # --- Riga attivita' serali escludibili ---
        extras = tk.Frame(form, bg=BG)
        extras.grid(row=3, column=0, columnspan=5, pady=(6, 0))
        _check(extras, self.cane_on,
               f"Cane (+{PASSEGGIATA_CANE_MIN})").pack(side="left", padx=8)
        _check(extras, self.cucina_on,
               f"Cucina/igiene (+{CUCINA_IGIENE_MIN})").pack(side="left", padx=8)

        # --- Tasto di commutazione fra calcolo a ritroso e in avanti ---
        self.mode_btn = tk.Button(
            self.root, text="Vado a dormire ora  →",
            font=("Segoe UI", 10, "bold"), fg=FG_TITLE, bg=BTN_BG,
            activebackground=BORDO, activeforeground=FG_TITLE,
            relief="flat", bd=0, padx=16, pady=7, cursor="hand2",
            command=self.commuta_modo,
        )
        self.mode_btn.pack(pady=(14, 0))

        # --- Risultato: orario a cui coricarsi, oppure sveglia nel modo in avanti ---
        self.result_label = tk.Label(
            self.root, text="--:--",
            font=("Segoe UI", 44, "bold"), fg=COL_INFO, bg=BG,
        )
        self.result_label.pack(pady=(14, 2))

        # Countdown "tra quanto / in ritardo di"
        self.sub_label = tk.Label(
            self.root, text="", font=("Segoe UI", 11), fg=FG_MUTED, bg=BG,
        )
        self.sub_label.pack()

        # Riepilogo orari intermedi
        self.info_label = tk.Label(
            self.root, text="", font=("Segoe UI", 9),
            fg=FG_TITLE, bg=BG, justify="center",
        )
        self.info_label.pack(pady=(8, 0))

        # Riga dei tempi sottratti: mostra solo le voci attive
        self.buffer_label = tk.Label(
            self.root, text="",
            font=("Segoe UI", 8, "italic"), fg=FG_DIM, bg=BG,
        )
        self.buffer_label.pack(pady=(6, 0))

        # --- Spegnimento automatico ---
        tk.Frame(self.root, bg=BORDO, height=1).pack(fill="x", padx=20, pady=(14, 8))
        sez = tk.Frame(self.root, bg=BG)
        sez.pack(padx=16)
        _check(sez, self.auto_on, "Spegnimento automatico all'inizio della routine",
               bold=True).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 6))
        _etichetta(sez, "Azione:", size=10).grid(row=1, column=0, sticky="e", padx=(0, 4))
        _combo(sez, self.azione_var, self.azioni, width=11).grid(row=1, column=1, sticky="w")
        _etichetta(sez, "Correzione:", size=10).grid(row=1, column=2, sticky="e", padx=(14, 4))
        _spin(sez, self.correzione_var, -CORREZIONE_MAX_MIN, CORREZIONE_MAX_MIN,
              passo=5, larghezza=5).grid(row=1, column=3, sticky="w")
        _etichetta(sez, "min", size=10).grid(row=1, column=4, sticky="w", padx=(4, 0))
        opzioni = tk.Frame(sez, bg=BG)
        opzioni.grid(row=2, column=0, columnspan=5, pady=(8, 0))
        _check(opzioni, self.gentile_var, "Chiusura gentile").pack(side="left", padx=6)
        _check(opzioni, self.anti_sosp_var, "Impedisci sospensione").pack(side="left", padx=6)
        _check(opzioni, self.prova_var, "Modalità prova").pack(side="left", padx=6)

        self.stato_label = tk.Label(
            self.root, text="", font=("Segoe UI", 10, "bold"), fg=FG_MUTED, bg=BG,
            wraplength=430, justify="center",
        )
        self.stato_label.pack(pady=(10, 6))
        pulsanti = tk.Frame(self.root, bg=BG)
        pulsanti.pack(pady=(0, 16))
        self.btn_rinvia = _bottone(pulsanti, f"Rinvia {RINVIO_MIN} min", self.rinvia)
        self.btn_annulla = _bottone(pulsanti, "Annulla stasera", self.toggle_annulla)
        self.btn_manuale = _bottone(pulsanti, "Timer manuale…", self.apri_timer_manuale)
        self.btn_countdown = _bottone(pulsanti, "Countdown", self.mostra_countdown)
        for bottone in (self.btn_rinvia, self.btn_annulla, self.btn_manuale, self.btn_countdown):
            bottone.pack(side="left", padx=3)

    def _crea_icona(self):
        if pystray is None:
            return None
        comando = self._comandi.put
        voce = pystray.MenuItem
        # Le azioni del menu girano nel thread di pystray: passano dalla coda al thread Tk
        menu = pystray.Menu(
            voce("Mostra finestra", lambda: comando("mostra"), default=True),
            voce("Mostra countdown", lambda: comando("countdown")),
            pystray.Menu.SEPARATOR,
            voce(f"Rinvia {RINVIO_MIN} min", lambda: comando("rinvia"),
                 enabled=lambda _v: self._puo_rinviare),
            voce("Annulla un rinvio", lambda: comando("annulla_rinvio"),
                 enabled=lambda _v: self._puo_annullare_rinvio),
            voce(lambda _v: self._etichetta_annulla, lambda: comando("annulla"),
                 enabled=lambda _v: self._puo_annullare),
            voce(lambda _v: "Ferma timer manuale" if self.manuale else "Timer manuale…",
                 lambda: comando("manuale")),
            pystray.Menu.SEPARATOR,
            voce("Esci", lambda: comando("esci")),
        )
        try:
            icona = pystray.Icon(NOME_ISTANZA, disegna_icona(FG_DIM), "Sonno e spegnimento", menu)
            threading.Thread(target=icona.run, daemon=True, name="icona").start()
        except Exception as e:
            registra_errore(f"icona non avviata: {e!r}")
            return None
        return icona

    # ------------------------------------------------------------------
    # Ciclo: tick al secondo, coda comandi a 100 ms
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._fase = not self._fase
        try:
            self._aggiorna()
            self.countdown.mantieni_in_cima()
            if self._da_salvare:
                self._salva()
        except Exception:
            registra_errore(traceback.format_exc())
        finally:
            if self._vivo:
                self.root.after(1000, self._tick)

    def _poll(self) -> None:
        try:
            if self.istanza and self.istanza.richiesta_mostra():
                self.mostra_finestra()
            azioni = {"mostra": self.mostra_finestra, "countdown": self.mostra_countdown,
                      "rinvia": self.rinvia, "annulla_rinvio": self.annulla_rinvio,
                      "annulla": self.toggle_annulla,
                      "manuale": self.apri_timer_manuale, "esci": self.esci}
            while self._vivo:
                try:
                    comando = self._comandi.get_nowait()
                except queue.Empty:
                    break
                azioni[comando]()
        except Exception:
            registra_errore(traceback.format_exc())
        if self._vivo:
            self.root.after(100, self._poll)

    # ------------------------------------------------------------------
    # Modifiche dell'utente
    # ------------------------------------------------------------------

    def _sveglia_modificata(self, *_) -> None:
        # Ignora le scritture fatte dall'app stessa quando cambia il giorno mostrato
        if self._sync or self._giorno_rif is None:
            return
        voce = self.imp["sveglie"][str(self._giorno_rif)]
        voce["ora"] = f"{self.hour_var.get()}:{self.minute_var.get()}"
        self._modificato()

    def _input_modificato(self, *_) -> None:
        imp = self.imp
        try:
            imp["sonno_h"] = int(self.sleep_h_var.get())
            imp["sonno_m"] = int(self.sleep_m_var.get())
        except ValueError:
            pass
        # Lettura non valida: resta in uso l'ultimo valore valido (bordo rosso nel campo)
        try:
            lettura = int(self.reading_var.get())
            if lettura >= 0:
                imp["lettura_min"] = lettura
        except ValueError:
            pass
        imp["lettura_on"] = self.reading_on.get()
        imp["cane_on"] = self.cane_on.get()
        imp["cucina_on"] = self.cucina_on.get()
        self._modificato()

    def _opzioni_modificate(self, *_) -> None:
        sp = self.imp["spegnimento"]
        sp["attivo"] = self.auto_on.get()
        sp["azione"] = self.azione_var.get()
        try:
            correzione = int(self.correzione_var.get())
            if abs(correzione) <= CORREZIONE_MAX_MIN:
                sp["correzione_min"] = correzione
        except ValueError:
            pass
        sp["gentile"] = self.gentile_var.get()
        sp["impedisci_sospensione"] = self.anti_sosp_var.get()
        sp["prova"] = self.prova_var.get()
        self._modificato()

    def _modificato(self) -> None:
        self._da_salvare = True
        if self._pronta:
            self._aggiorna()

    def imposta_sveglia(self, giorno: int, attiva: bool, ora: str) -> None:
        try:
            parse_ora(ora)
        except ValueError:
            return
        self.imp["sveglie"][str(giorno)] = {"attiva": bool(attiva), "ora": ora}
        self._giorno_rif = None   # forza la risincronizzazione delle tendine principali
        self._modificato()

    def memorizza_pos_countdown(self, x: int, y: int) -> None:
        self.imp["countdown_pos"] = [int(x), int(y)]
        self._da_salvare = True

    def memorizza_dim_countdown(self, dimensione: int) -> None:
        self.imp["countdown_dim"] = int(dimensione)
        self._da_salvare = True

    # ------------------------------------------------------------------
    # Calcolo dello stato e regola di scatto
    # ------------------------------------------------------------------

    def _catena(self, sveglia):
        """Catena a ritroso con gli ultimi valori validi (le impostazioni, non i campi)."""
        imp = self.imp
        lettura = imp["lettura_min"] if imp["lettura_on"] else 0
        cane = PASSEGGIATA_CANE_MIN if imp["cane_on"] else 0
        cucina = CUCINA_IGIENE_MIN if imp["cucina_on"] else 0
        return calcola_catena(sveglia, imp["sonno_h"], imp["sonno_m"], lettura, cane, cucina)

    def _calcola_stato(self, now: datetime):
        """(sveglia, catena, stato) all'istante now."""
        sveglia = prossima_sveglia(now, self.imp["sveglie"])
        catena = self._catena(sveglia) if sveglia else None
        sp = self.imp["spegnimento"]
        if self.manuale:
            target = self.manuale["target"]
            stato = Stato("manuale", target, self.manuale["azione"],
                          "armato" if target > now else "in_ritardo")
        elif not sp["attivo"]:
            stato = Stato(None, None, sp["azione"], "disattivo")
        elif catena is None:
            stato = Stato(None, None, sp["azione"], "nessuna_sveglia")
        else:
            chiave = chiave_serata(sveglia)
            serata = self.imp["serate"].get(chiave, {})
            rinvii = int(serata.get("rinvii", 0))
            target = catena[2] + timedelta(
                minutes=sp["correzione_min"] + RINVIO_MIN * rinvii)
            if serata.get("eseguita"):
                esito = "eseguita"
            elif serata.get("annullata"):
                esito = "annullata"
            elif target <= now:
                esito = "in_ritardo"
            else:
                esito = "armato"
            stato = Stato("sonno", target, sp["azione"], esito, rinvii, chiave)
        return sveglia, catena, stato

    def _aggiorna(self) -> None:
        now = adesso()
        sveglia, catena, stato = self._calcola_stato(now)
        self._sincronizza_sveglia(now, sveglia)
        self._mostra_sonno(now, sveglia, catena)
        if self._gestisci_bersaglio(now, stato):
            # Scatto avvenuto o timer manuale decaduto: lo stato e' cambiato
            sveglia, catena, stato = self._calcola_stato(now)
        self._mostra_spegnimento(now, stato)

    def _gestisci_bersaglio(self, now: datetime, stato: Stato) -> bool:
        """Scatto, decadenza del timer manuale e avvisi a soglia. Vero se lo stato cambia."""
        idoneo = stato.tipo is not None and stato.esito in ("armato", "in_ritardo")
        target = stato.target if idoneo else None
        scatta = deve_scattare(self._prev_now, self._prev_target, now, target)
        self._prev_now, self._prev_target = now, target
        if scatta:
            self._scatta(stato)
            return True
        if stato.tipo == "manuale" and stato.esito == "in_ritardo":
            # Scaduto senza osservazione (PC sospeso o spento): il timer manuale decade
            self.manuale = None
            self._notifica("Timer manuale annullato",
                           "È scaduto mentre il PC era sospeso o spento.")
            return True
        if stato.esito == "armato":
            rimasti = (stato.target - now).total_seconds()
            nuove = [s for s in SOGLIE_AVVISO_MIN
                     if rimasti <= s * 60 and (stato.target, s) not in self._notificate]
            if nuove:
                self._notificate.update((stato.target, s) for s in nuove)
                self._notifica(f"{stato.azione} alle {stato.target:%H:%M}",
                               f"Mancano {formatta_durata(rimasti)}.")
                self._mostra_cd = True
        return False

    def _scatta(self, stato: Stato) -> None:
        if stato.tipo == "sonno":
            self.imp["serate"].setdefault(stato.chiave, {})["eseguita"] = True
        else:
            self.manuale = None
        # Salvataggio PRIMA dell'azione: dopo un riavvio o un risveglio la serata e' gia' chiusa
        self._salva()
        self.countdown.nascondi()
        if self.imp["spegnimento"]["prova"]:
            self._notifica("Modalità prova", f"Countdown a zero: adesso eseguirei «{stato.azione}».")
            self.mostra_finestra()
            messagebox.showinfo(
                "Modalità prova",
                f"Countdown a zero.\nSenza la modalità prova adesso eseguirei «{stato.azione}».",
                parent=self.root)
            return
        errore = esegui_azione(stato.azione, self.imp["spegnimento"]["gentile"])
        if errore:
            self._notifica(f"{stato.azione}: non riuscito", errore[:200])

    # ------------------------------------------------------------------
    # Visualizzazione
    # ------------------------------------------------------------------

    def _sincronizza_sveglia(self, now: datetime, sveglia) -> None:
        """La riga Sveglia mostra e modifica la sveglia del giorno della prossima sveglia."""
        giorno = sveglia.weekday() if sveglia else (now + timedelta(days=1)).weekday()
        if giorno == self._giorno_rif:
            return
        self._giorno_rif = giorno
        try:
            h, m = parse_ora(self.imp["sveglie"][str(giorno)]["ora"])
        except ValueError:
            h, m = 7, 0
        self._sync = True
        try:
            self.hour_var.set(f"{h:02d}")
            self.minute_var.set(f"{m:02d}")
        finally:
            self._sync = False
        self.sveglia_label.config(text=f"Sveglia {GIORNI[giorno]}:")

    def _mostra_sonno(self, now: datetime, sveglia, catena) -> None:
        # Campo lettura disabilitato visivamente quando la spunta e' spenta
        self.reading_entry.config(
            state="normal" if self.reading_on.get() else "disabled")
        try:
            valida = int(self.reading_var.get()) >= 0
        except ValueError:
            valida = False
        self.reading_entry.config(highlightbackground=BORDO if valida else COL_BAD)
        self._refresh_buffer()
        if self.modo_avanti:
            self._mostra_avanti(now)
            return
        if catena is None:
            self.result_label.config(text="--:--", fg=COL_WARN)
            self.sub_label.config(text="Nessuna sveglia attiva", fg=COL_WARN)
            self.info_label.config(text="Attiva almeno un giorno in «Settimana…»")
            return

        addormentato, a_letto, vai_a_dormire = catena
        diff_sec = (vai_a_dormire - now).total_seconds()
        diff_min = int(diff_sec // 60)

        self.result_label.config(text=vai_a_dormire.strftime("%H:%M"))

        if diff_sec <= 0:
            # Ora di coricarsi gia' passata: dormiresti meno del desiderato
            late = abs(diff_min)
            self.result_label.config(fg=COL_BAD)
            self.sub_label.config(text=f"in ritardo di {late // 60}h {late % 60:02d}m", fg=COL_BAD)
        else:
            color = COL_WARN if diff_min <= SOGLIA_GIALLO_MIN else COL_OK
            self.result_label.config(fg=color)
            self.sub_label.config(text=f"tra {formatta_durata(diff_sec)}", fg=FG_MUTED)

        # Riepilogo sveglia + sonno + orari intermedi
        wd = GIORNI[sveglia.weekday()]
        sonno_txt = formatta_sonno(int((sveglia - addormentato).total_seconds() // 60))
        self.info_label.config(
            text=(f"Sveglia {wd} {sveglia.strftime('%H:%M')}  ·  dormi {sonno_txt}\n"
                  f"a letto {a_letto.strftime('%H:%M')}  ·  "
                  f"addormentato {addormentato.strftime('%H:%M')}")
        )

    def _mostra_avanti(self, now: datetime) -> None:
        """Modo in avanti: la routine parte adesso, il risultato e' l'ora della sveglia."""
        imp = self.imp
        lettura = imp["lettura_min"] if imp["lettura_on"] else 0
        cane = PASSEGGIATA_CANE_MIN if imp["cane_on"] else 0
        cucina = CUCINA_IGIENE_MIN if imp["cucina_on"] else 0
        a_letto, addormentato, sveglia = calcola_catena_avanti(
            now, imp["sonno_h"], imp["sonno_m"], lettura, cane, cucina)
        # Niente semaforo: non esiste un ritardo
        self.result_label.config(text=sveglia.strftime("%H:%M"), fg=COL_INFO)
        self.sub_label.config(
            text=f"sveglia di {GIORNI[sveglia.weekday()]}  ·  tra "
                 f"{formatta_durata((sveglia - now).total_seconds())}",
            fg=FG_MUTED)
        sonno_txt = formatta_sonno(imp["sonno_h"] * 60 + imp["sonno_m"])
        self.info_label.config(
            text=(f"Inizi adesso {now:%H:%M}  ·  dormi {sonno_txt}\n"
                  f"a letto {a_letto:%H:%M}  ·  addormentato {addormentato:%H:%M}"))

    def commuta_modo(self) -> None:
        """Passa dal calcolo a ritroso a quello in avanti, e viceversa."""
        self.modo_avanti = avanti = not self.modo_avanti
        self.mode_btn.config(
            text="←  Torna all'ora di coricarti" if avanti else "Vado a dormire ora  →",
            bg=COL_INFO if avanti else BTN_BG,
            fg=BG if avanti else FG_TITLE,
            activebackground="#79c0ff" if avanti else BORDO,
            activeforeground=BG if avanti else FG_TITLE,
        )
        self.subtitle_label.config(
            text="A che ora suonare la sveglia, se ti corichi adesso" if avanti
            else "L'ora per coricarti e dormire quanto vuoi")
        # Nel modo in avanti la sveglia e' il risultato: la riga Sveglia esce dal calcolo
        self.sveglia_label.config(fg=FG_OFF if avanti else FG_MUTED)
        self.sveglia_due_punti.config(fg=FG_OFF if avanti else FG_TITLE)
        stato = "disabled" if avanti else "readonly"
        self.sveglia_h_combo.config(state=stato)
        self.sveglia_m_combo.config(state=stato)
        self._aggiorna()

    def _refresh_buffer(self) -> None:
        """Riga dei tempi sottratti: solo le voci attive."""
        imp = self.imp
        parti = [f"+{BUFFER_ADDORMENTAMENTO_MIN} addormentamento"]
        if imp["lettura_on"]:
            parti.append(f"+{imp['lettura_min']} lettura")
        if imp["cane_on"]:
            parti.append(f"+{PASSEGGIATA_CANE_MIN} cane")
        if imp["cucina_on"]:
            parti.append(f"+{CUCINA_IGIENE_MIN} cucina/igiene")
        self.buffer_label.config(text="  ·  ".join(parti))

    def _mostra_spegnimento(self, now: datetime, stato: Stato) -> None:
        sp = self.imp["spegnimento"]
        armato = stato.esito == "armato"
        manuale = stato.tipo == "manuale"
        rimasti = (stato.target - now).total_seconds() if stato.target else 0.0
        quando = (f"{GIORNI[stato.target.weekday()]} {stato.target:%H:%M}"
                  if stato.target else "")
        if manuale:
            testo = (f"Timer manuale: {stato.azione} alle {stato.target:%H:%M:%S}"
                     f" · tra {formatta_durata(rimasti)}")
            colore = COL_INFO
        elif armato:
            testo = (f"{stato.azione} {quando} · tra {formatta_durata(rimasti)}"
                     f" · rinvii {stato.rinvii}/{MAX_RINVII}")
            colore = COL_WARN if rimasti <= SOGLIA_GIALLO_MIN * 60 else COL_OK
        elif stato.esito == "annullata":
            testo, colore = f"Annullato per stasera (era {quando})", FG_MUTED
        elif stato.esito == "eseguita":
            testo, colore = f"Eseguito stasera ({quando})", FG_MUTED
        elif stato.esito == "in_ritardo":
            testo = f"Orario passato ({quando}): stasera nessuno spegnimento automatico"
            colore = COL_BAD
        elif stato.esito == "disattivo":
            testo, colore = "Spegnimento automatico disattivato", FG_DIM
        else:
            testo, colore = "Nessuna sveglia attiva: nessuno spegnimento automatico", FG_DIM
        if sp["prova"]:
            testo += "  ·  MODALITÀ PROVA"
        self.stato_label.config(text=testo, fg=colore)

        # Pulsanti: rinvio solo per la serata armata; annullo o riattivazione della serata
        self._puo_rinviare = stato.tipo == "sonno" and armato and stato.rinvii < MAX_RINVII
        self._puo_annullare_rinvio = self._rinvio_annullabile(stato, now)
        if stato.esito == "annullata":
            self._etichetta_annulla = "Riattiva stasera"
            self._puo_annullare = stato.target is not None and stato.target > now
        else:
            self._etichetta_annulla = "Annulla stasera"
            self._puo_annullare = stato.tipo == "sonno" and armato
        self._etichetta_annulla_cd = "Ferma timer" if manuale else "Annulla stasera"
        self.btn_rinvia.config(state="normal" if self._puo_rinviare else "disabled")
        self.btn_annulla.config(text=self._etichetta_annulla,
                                state="normal" if self._puo_annullare else "disabled")
        self.btn_manuale.config(text="Ferma timer manuale" if manuale else "Timer manuale…")
        self.btn_countdown.config(state="normal" if armato else "disabled")

        # Countdown: contenuto aggiornato prima di mostrarlo (la posizione dipende dalla misura)
        if armato and (self.countdown.visibile or self._mostra_cd):
            titolo = f"{stato.azione} {stato.target:%H:%M}" + (" · prova" if sp["prova"] else "")
            self.countdown.aggiorna(rimasti, titolo, self._fase)
            if self._mostra_cd:
                self.countdown.mostra(solo_primario=self._cd_al_primario)
        elif not armato:
            self.countdown.nascondi()
        self._mostra_cd = False
        self._cd_al_primario = False

        # Anti-sospensione solo con un bersaglio armato
        voglio = armato and sp["impedisci_sospensione"]
        if voglio != self._anti_sosp:
            imposta_anti_sospensione(voglio)
            self._anti_sosp = voglio

        self._aggiorna_icona(armato, rimasti, testo)

    def _aggiorna_icona(self, armato: bool, rimasti: float, testo: str) -> None:
        if not self.icona:
            return
        if not armato:
            colore = FG_DIM
        elif rimasti <= 10 * 60:
            colore = COL_BAD
        elif rimasti <= SOGLIA_GIALLO_MIN * 60:
            colore = COL_WARN
        else:
            colore = COL_INFO
        titolo = ("Sonno e spegnimento\n" + testo)[:127]
        menu = (self._puo_rinviare, self._puo_annullare_rinvio, self._puo_annullare,
                self._etichetta_annulla, bool(self.manuale))
        vecchia = self._firma_icona
        if vecchia == (colore, titolo, menu):
            return
        self._firma_icona = (colore, titolo, menu)
        try:
            if vecchia is None or vecchia[0] != colore:
                self.icona.icon = disegna_icona(colore)
            if vecchia is None or vecchia[1] != titolo:
                self.icona.title = titolo
            if vecchia is None or vecchia[2] != menu:
                self.icona.update_menu()
        except Exception as e:
            registra_errore(f"icona: {e!r}")

    # ------------------------------------------------------------------
    # Comandi (finestra, countdown, icona)
    # ------------------------------------------------------------------

    def mostra_finestra(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()

    def nascondi_finestra(self) -> None:
        self.root.withdraw()

    def mostra_countdown(self) -> None:
        self._mostra_cd = True
        self._aggiorna()
        if not self.countdown.visibile:
            self._notifica("Nessun countdown attivo", self.stato_label.cget("text"))
            return
        # Richiesta esplicita: il countdown va dove l'utente sta guardando (monitor del mouse)
        self.countdown.porta_vicino_al_mouse()
        self.countdown.mostra()

    @staticmethod
    def _rinvio_annullabile(stato: Stato, now: datetime) -> bool:
        """Un rinvio si annulla solo se l'orario che ne risulta resta nel futuro.

        Margine di 1 minuto: un orario gia' passato farebbe saltare lo spegnimento
        di stasera invece di anticiparlo.
        """
        if stato.tipo != "sonno" or stato.esito != "armato" or stato.rinvii <= 0:
            return False
        anticipato = stato.target - timedelta(minutes=RINVIO_MIN)
        return anticipato > now + timedelta(minutes=1)

    def annulla_rinvio(self) -> None:
        """Toglie un rinvio della serata (un clic sbagliato su «Rinvia»)."""
        _, _, stato = self._calcola_stato(adesso())
        if not self._rinvio_annullabile(stato, adesso()):
            return
        self.imp["serate"].setdefault(stato.chiave, {})["rinvii"] = stato.rinvii - 1
        self._modificato()

    def rinvia(self) -> None:
        _, _, stato = self._calcola_stato(adesso())
        if stato.tipo != "sonno" or stato.esito != "armato" or stato.rinvii >= MAX_RINVII:
            return
        self.imp["serate"].setdefault(stato.chiave, {})["rinvii"] = stato.rinvii + 1
        self._modificato()

    def toggle_annulla(self) -> None:
        now = adesso()
        _, _, stato = self._calcola_stato(now)
        if stato.tipo != "sonno":
            return
        serata = self.imp["serate"].setdefault(stato.chiave, {})
        if stato.esito == "armato":
            serata["annullata"] = True
        elif stato.esito == "annullata" and stato.target > now:
            serata["annullata"] = False
            # Serata riattivata: gli avvisi di questo orario possono ripartire
            self._notificate = {n for n in self._notificate if n[0] != stato.target}
        self._modificato()

    def annulla_o_ferma(self) -> None:
        if self.manuale:
            self.ferma_manuale()
        else:
            self.toggle_annulla()

    def apri_settimana(self) -> None:
        if self._dlg_settimana and self._dlg_settimana.win.winfo_exists():
            self._dlg_settimana.win.lift()
            return
        self._dlg_settimana = DialogoSettimana(self)

    def apri_timer_manuale(self) -> None:
        if self.manuale:
            self.ferma_manuale()
            return
        if self._dlg_manuale and self._dlg_manuale.win.winfo_exists():
            self._dlg_manuale.win.lift()
            return
        self._dlg_manuale = DialogoTimerManuale(self)

    def avvia_manuale(self, target: datetime, azione: str, mostra: bool = True) -> None:
        self.manuale = {"target": target, "azione": azione}
        self._mostra_cd = mostra
        self._aggiorna()

    def ferma_manuale(self) -> None:
        self.manuale = None
        self._aggiorna()

    def esci(self) -> None:
        _, _, stato = self._calcola_stato(adesso())
        if stato.esito == "armato":
            self.mostra_finestra()
            if not messagebox.askyesno(
                    "Esci",
                    f"«{stato.azione}» è previsto alle {stato.target:%H:%M}.\n"
                    "Se esci, non avviene finché non riapri l'app.\n\nVuoi uscire?",
                    parent=self.root):
                return
        self._vivo = False
        self._salva()
        imposta_anti_sospensione(False)
        if self.icona:
            try:
                self.icona.stop()
            except Exception:
                pass
        self.root.destroy()

    # ------------------------------------------------------------------
    # Servizi
    # ------------------------------------------------------------------

    def _salva(self) -> None:
        try:
            salva_impostazioni(self.imp)
            self._da_salvare = False
        except OSError as e:
            registra_errore(f"salvataggio impostazioni: {e!r}")

    def _notifica(self, titolo: str, messaggio: str) -> None:
        if not self.icona:
            return
        try:
            self.icona.notify(messaggio, titolo)
        except Exception as e:
            registra_errore(f"notifica: {e!r}")

    def _notifica_avvio(self) -> None:
        _, _, stato = self._calcola_stato(adesso())
        if stato.esito == "armato":
            self._notifica("Spegnimento automatico attivo",
                           f"{stato.azione} {GIORNI[stato.target.weekday()]} "
                           f"alle {stato.target:%H:%M}.")

    def _errore_tk(self, tipo, valore, tb) -> None:
        registra_errore("".join(traceback.format_exception(tipo, valore, tb)))


def main() -> None:
    istanza = IstanzaSingola(NOME_ISTANZA)
    if not istanza.prima:
        return   # l'istanza attiva riceve la richiesta e mostra la sua finestra
    root = tk.Tk()
    SonnoApp(root, nascosta="--nascosta" in sys.argv[1:], istanza=istanza)
    root.mainloop()


if __name__ == "__main__":
    main()
