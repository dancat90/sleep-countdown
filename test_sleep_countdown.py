"""Collaudo di sonno_gui.pyw: logica pura + scenario GUI con orologio simulato.

Uso: python test_sonno.py <percorso sonno_gui.pyw> <cartella screenshot>
L'azione finale e le finestre di dialogo sono sostituite: il PC non si spegne.
"""
import copy
import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta

PERCORSO, USCITA = sys.argv[1], sys.argv[2]
os.makedirs(USCITA, exist_ok=True)
# Coordinate Tk in pixel fisici, coerenti con gli screenshot
ctypes.windll.shcore.SetProcessDpiAwareness(2)

loader = importlib.machinery.SourceFileLoader("sonno_gui", PERCORSO)
spec = importlib.util.spec_from_loader("sonno_gui", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

# --- Isolamento: impostazioni in una cartella temporanea, nessuna azione reale ---
tmp = tempfile.mkdtemp(prefix="sonno_test_")
m.CARTELLA_DATI = tmp
m.FILE_IMPOSTAZIONI = os.path.join(tmp, "impostazioni.json")
registro = []
m.esegui_azione = lambda azione, gentile: (registro.append(("azione", azione, gentile)), None)[1]
anti = []
m.imposta_anti_sospensione = lambda attiva: anti.append(attiva)

esiti = []


def verifica(cond, nome):
    esiti.append((bool(cond), nome))
    print(("OK   " if cond else "FAIL ") + nome, flush=True)


# ======================================================================
# Logica pura
# ======================================================================
verifica(m.parse_ora("07:05") == (7, 5), "parse_ora valido")
for cattivo in ("24:00", "7", "aa:bb", None, "07:60"):
    try:
        m.parse_ora(cattivo)
        verifica(False, f"parse_ora rifiuta {cattivo!r}")
    except ValueError:
        verifica(True, f"parse_ora rifiuta {cattivo!r}")

sv = m.impostazioni_predefinite()["sveglie"]
ven18 = datetime(2026, 10, 30, 18, 0)   # venerdi'
verifica(m.prossima_sveglia(ven18, sv) == datetime(2026, 10, 31, 7, 0), "prossima sveglia: domani 07:00")
verifica(m.prossima_sveglia(datetime(2026, 10, 30, 6, 0), sv) == datetime(2026, 10, 30, 7, 0),
         "prossima sveglia: oggi 07:00")
sv2 = copy.deepcopy(sv)
sv2["5"]["attiva"] = sv2["6"]["attiva"] = False
verifica(m.prossima_sveglia(ven18, sv2) == datetime(2026, 11, 2, 7, 0), "weekend spento: lunedi'")
sv3 = {str(g): {"attiva": False, "ora": "07:00"} for g in range(7)}
verifica(m.prossima_sveglia(ven18, sv3) is None, "nessuna sveglia attiva")
sv4 = copy.deepcopy(sv3)
sv4["4"] = {"attiva": True, "ora": "07:00"}
verifica(m.prossima_sveglia(datetime(2026, 10, 30, 8, 0), sv4) == datetime(2026, 11, 6, 7, 0),
         "solo venerdi': venerdi' prossimo")
sv5 = copy.deepcopy(sv)
sv5["5"]["ora"] = "09:30"
verifica(m.prossima_sveglia(ven18, sv5) == datetime(2026, 10, 31, 9, 30), "sabato alle 09:30")

addorm, a_letto, vai = m.calcola_catena(datetime(2026, 10, 31, 7, 0), 8, 0, 30, 20, 15)
verifica(vai == datetime(2026, 10, 30, 21, 40) and a_letto == datetime(2026, 10, 30, 22, 45)
         and addorm == datetime(2026, 10, 30, 23, 0), "caso di riferimento 21:40 / 22:45 / 23:00")
verifica(m.chiave_serata(datetime(2026, 10, 31, 7, 0)) == "2026-10-31", "chiave serata")

T = datetime(2026, 10, 30, 21, 40)
s = timedelta(seconds=1)
verifica(m.deve_scattare(T - s, T, T, T), "scatto: attraversamento osservato")
verifica(not m.deve_scattare(T - 2 * s, T, T - s, T), "nessuno scatto prima dell'orario")
verifica(not m.deve_scattare(T - 60 * s, T, T + s, T), "nessuno scatto dopo un salto (sospensione)")
verifica(not m.deve_scattare(T - s, T + timedelta(minutes=15), T, T), "nessuno scatto se il bersaglio cambia")
verifica(not m.deve_scattare(None, None, T, T), "nessuno scatto al primo tick")
verifica(not m.deve_scattare(T + s, T, T + 2 * s, T), "nessuno scatto se gia' passato")
verifica(not m.deve_scattare(T - s, T, T, None), "nessuno scatto senza bersaglio")

verifica(m.formatta_durata(0) == "meno di un minuto", "durata < 1 min")
verifica(m.formatta_durata(60) == "1 min", "durata 1 min")
verifica(m.formatta_durata(3900) == "1h 05m", "durata 1h 05m")
verifica(m.formatta_durata(90000) == "1g 01h 00m", "durata 1g 01h")
verifica(m.formatta_orologio(39.2) == "0:00:40", "orologio arrotondato per eccesso")
verifica(m.formatta_orologio(3661) == "1:01:01", "orologio 1:01:01")
verifica(m.colori_countdown(31 * 60) == (m.BG, m.COL_OK), "testo verde oltre 30' su sfondo scuro")
verifica(m.colori_countdown(30 * 60) == (m.BG, m.COL_WARN), "testo giallo a 30'")
verifica(m.colori_countdown(61) == (m.BG, m.CD_ARANCIO), "testo arancio sopra 1'")
verifica(m.colori_countdown(60)[0] == m.CD_ROSSO, "sfondo rosso nell'ultimo minuto")
verifica(m.colori_countdown(30, True) == (m.BG, m.CD_ROSSO), "lampeggio rosso/nero")

imp = m.impostazioni_predefinite()
imp["sonno_h"] = 7
imp["serate"] = {"2026-10-31": {"rinvii": 1}, "2026-01-01": {"eseguita": True}}
m.salva_impostazioni(imp)
ricaricate = m.carica_impostazioni()
verifica(ricaricate["sonno_h"] == 7 and "2026-10-31" in ricaricate["serate"], "salva e ricarica")
verifica("2026-01-01" not in ricaricate["serate"], "serate vecchie potate")
with open(m.FILE_IMPOSTAZIONI, "w", encoding="utf-8") as f:
    f.write("{non e' json")
verifica(m.carica_impostazioni() == m.impostazioni_predefinite(), "file rotto -> predefinite")
with open(m.FILE_IMPOSTAZIONI, "w", encoding="utf-8") as f:
    f.write('{"sonno_h": "otto", "spegnimento": {"azione": "Esplodi", "correzione_min": 999},'
            ' "sveglie": {"2": {"ora": "99:99"}}, "countdown_pos": [1, "x"], "countdown_dim": 99}')
cattive = m.carica_impostazioni()
verifica(cattive["sonno_h"] == 8 and cattive["spegnimento"]["azione"] == "Spegni"
         and cattive["spegnimento"]["correzione_min"] == 120
         and cattive["sveglie"]["2"]["ora"] == "07:00" and cattive["countdown_pos"] is None
         and cattive["countdown_dim"] == m.COUNTDOWN_DIM_MAX,
         "valori sbagliati -> ripuliti")

a = m.IstanzaSingola("SonnoTestIstanza")
b = m.IstanzaSingola("SonnoTestIstanza")
verifica(a.prima and not b.prima, "istanza singola: la seconda si accorge della prima")
verifica(a.richiesta_mostra() and not a.richiesta_mostra(), "seconda istanza -> richiesta di mostrarsi (una volta)")
b.chiudi()
a.chiudi()
print("Ibernazione disponibile:", m.ibernazione_disponibile(), flush=True)

# ======================================================================
# Scenario GUI con orologio simulato
# ======================================================================
import tkinter as tk
from PIL import ImageGrab

m.messagebox.showinfo = lambda *a, **k: registro.append(("prova", a[0] if a else ""))
risposte = [False, True]
m.messagebox.askyesno = lambda *a, **k: (registro.append(("chiedi_uscita",)), risposte.pop(0))[1]
notifiche = []
m.SonnoApp._notifica = lambda self, titolo, testo: notifiche.append((titolo, testo))

imp = m.impostazioni_predefinite()
imp["spegnimento"].update(azione="Blocca", prova=True)
m.salva_impostazioni(imp)

# Orologio simulato: 40 s prima delle 21:40 di una sera con sveglia alle 07:00
reale = datetime.now()
T = reale.replace(hour=21, minute=40, second=0, microsecond=0)
if T - timedelta(minutes=2) <= reale:
    T += timedelta(days=1)
scarto = [T - timedelta(seconds=40) - reale]
m.adesso = lambda: datetime.now() + scarto[0]


def porta_a(istante):
    scarto[0] = istante - datetime.now()


root = tk.Tk()
app = m.SonnoApp(root, nascosta=False, istanza=None)
root.after(120000, root.destroy)   # sicurezza: il collaudo non resta appeso


def scatta(nome, finestra):
    finestra.update_idletasks()
    finestra.update()
    x, y = finestra.winfo_rootx(), finestra.winfo_rooty()
    w, h = finestra.winfo_width(), finestra.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(os.path.join(USCITA, nome))


def serata(chiave):
    return app.imp["serate"].get(chiave, {})


chiave1 = (T + timedelta(days=1)).date().isoformat()
chiave2 = (T + timedelta(days=2)).date().isoformat()
passi = []


def passo(ritardo_ms):
    def deco(f):
        passi.append((ritardo_ms, f))
        return f
    return deco


@passo(1500)
def p_iniziale():
    verifica(app.countdown.visibile, "countdown visibile a 40 s dallo spegnimento")
    verifica(len(notifiche) == 1, f"una sola notifica per le soglie 30/10/1 ({len(notifiche)})")
    verifica("Blocca" in app.stato_label.cget("text") and "rinvii 0/2" in app.stato_label.cget("text"),
             "riga di stato: azione e rinvii")
    verifica(anti and anti[-1] is True, "anti-sospensione attiva con bersaglio armato")
    verifica(app.sveglia_label.cget("text").startswith("Sveglia ") and app.hour_var.get() == "07",
             "riga sveglia sincronizzata sul giorno della prossima sveglia")
    scatta("01_principale.png", root)
    scatta("02_countdown_rosso.png", app.countdown.win)


@passo(100)
def p_rinvio1():
    app.rinvia()
    verifica(serata(chiave1).get("rinvii") == 1, "primo rinvio registrato")


@passo(1200)
def p_dopo_rinvio1():
    verifica(app.countdown.visibile, "countdown ancora visibile dopo il rinvio")
    verifica(len(notifiche) == 2, "notifica di conferma del nuovo orario")
    scatta("03_countdown_giallo.png", app.countdown.win)


@passo(100)
def p_rinvio_limite():
    app.rinvia()
    app.rinvia()
    verifica(serata(chiave1).get("rinvii") == 2, "limite di 2 rinvii rispettato")
    verifica(str(app.btn_rinvia.cget("state")) == "disabled", "pulsante Rinvia disattivato al limite")
    # Salto in avanti (come una sospensione) a 5 s dal nuovo orario: T + 30 min
    porta_a(T + timedelta(minutes=30) - timedelta(seconds=5))


@passo(8000)
def p_scatto_prova():
    prove = [r for r in registro if r[0] == "prova"]
    verifica(len(prove) == 1, f"scatto in modalita' prova, una sola volta ({len(prove)})")
    verifica(not any(r[0] == "azione" for r in registro), "in modalita' prova nessuna azione reale")
    verifica(serata(chiave1).get("eseguita") is True, "serata segnata come eseguita")
    verifica(not app.countdown.visibile, "countdown nascosto dopo lo scatto")
    verifica("Eseguito" in app.stato_label.cget("text"), "riga di stato: eseguito")
    verifica(anti[-1] is False, "anti-sospensione rilasciata dopo lo scatto")


@passo(100)
def p_manuale():
    app.prova_var.set(False)
    app.avvia_manuale(m.adesso() + timedelta(seconds=4), "Blocca", mostra=True)


@passo(1500)
def p_manuale_visibile():
    verifica(app.countdown.visibile, "countdown del timer manuale visibile")
    verifica(str(app.btn_annulla.cget("state")) == "disabled", "Annulla stasera spento col timer manuale")
    scatta("04_countdown_manuale.png", app.countdown.win)


@passo(4500)
def p_manuale_scattato():
    verifica(("azione", "Blocca", False) in registro, "timer manuale: azione eseguita (finta)")
    verifica(app.manuale is None, "timer manuale chiuso dopo lo scatto")


@passo(100)
def p_manuale_salto():
    app.avvia_manuale(m.adesso() + timedelta(seconds=60), "Blocca", mostra=False)
    porta_a(m.adesso() + timedelta(minutes=5))


@passo(2500)
def p_manuale_decaduto():
    verifica(app.manuale is None, "timer manuale scaduto durante un salto: decade")
    verifica(any(t == "Timer manuale annullato" for t, _ in notifiche), "notifica di decadenza")
    verifica(sum(1 for r in registro if r[0] == "azione") == 1, "nessuna azione dopo il salto")


@passo(100)
def p_serata_due():
    porta_a(T + timedelta(days=1) - timedelta(minutes=10))


@passo(1500)
def p_serata_due_armata():
    verifica(app.countdown.visibile, "seconda serata: countdown a 10 minuti")
    app.correzione_var.set("-30")


@passo(2500)
def p_correzione_nel_passato():
    verifica(not any(r[0] == "prova" for r in registro[1:]) and
             sum(1 for r in registro if r[0] == "azione") == 1,
             "correzione che porta l'orario nel passato: nessuno scatto")
    verifica("Orario passato" in app.stato_label.cget("text"), "riga di stato: orario passato")
    verifica(not app.countdown.visibile, "countdown nascosto con orario passato")
    app.correzione_var.set("0")


@passo(1500)
def p_annulla():
    verifica("tra" in app.stato_label.cget("text"), "correzione azzerata: serata di nuovo armata")
    app.toggle_annulla()


@passo(1200)
def p_annullata():
    verifica(serata(chiave2).get("annullata") is True, "serata annullata")
    verifica(not app.countdown.visibile, "countdown nascosto dopo l'annullo")
    verifica(app.btn_annulla.cget("text") == "Riattiva stasera", "pulsante diventa Riattiva")
    app.toggle_annulla()


@passo(1500)
def p_riattivata():
    verifica(serata(chiave2).get("annullata") is False, "serata riattivata")
    verifica(app.countdown.visibile, "countdown di nuovo visibile dopo la riattivazione")
    scatta("05_riattivato.png", app.countdown.win)
    # Rotellina: dimensione su e giu', limiti rispettati, valore da salvare
    class Rotella:
        def __init__(self, delta):
            self.delta = delta
    base = app.imp["countdown_dim"]
    larghezza = app.countdown.win.winfo_reqwidth()
    app.countdown._rotellina(Rotella(120))
    app.countdown.win.update_idletasks()
    verifica(app.imp["countdown_dim"] == base + 1 and app._da_salvare,
             "rotellina in su: countdown piu' grande, da salvare")
    verifica(app.countdown.win.winfo_reqwidth() > larghezza, "la finestra cresce davvero")
    for _ in range(40):
        app.countdown._rotellina(Rotella(-120))
    verifica(app.imp["countdown_dim"] == m.COUNTDOWN_DIM_MIN, "rotellina in giu': minimo rispettato")
    scatta("05b_countdown_minimo.png", app.countdown.win)
    app.memorizza_dim_countdown(base)
    app.countdown._applica_dimensione()
    # Sveglia del giorno spenta: lo spegnimento slitta alla sera dopo
    giorno = (T + timedelta(days=2)).weekday()
    app.imposta_sveglia(giorno, False, "07:00")
    _, _, stato = app._calcola_stato(m.adesso())
    verifica(stato.target == T + timedelta(days=2), "giorno spento: spegnimento alla sera successiva")
    app.imposta_sveglia(giorno, True, "07:00")
    # Modifica dalla riga Sveglia principale
    app.hour_var.set("08")
    verifica(app.imp["sveglie"][str(giorno)]["ora"] == "08:00", "riga Sveglia modifica il giorno indicato")
    app.hour_var.set("07")


def monitor_secondari():
    """Aree di lavoro dei monitor non primari (lista vuota con un solo monitor)."""
    from ctypes import wintypes
    aree = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def su_monitor(handle, _hdc, _rect, _lp):
        info = m._MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info))
        if not info.dwFlags & 1:
            r = info.rcWork
            aree.append((r.left, r.top, r.right, r.bottom))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(su_monitor), 0)
    return aree


@passo(300)
def p_multi_monitor():
    verifica(m.monitor_di(-10 ** 6, -10 ** 6, nullo_se_fuori=True) is None,
             "punto fuori da tutti i monitor riconosciuto")
    verifica(m.monitor_di(0, 0) is not None, "monitor primario trovato")
    altri = monitor_secondari()
    if not altri:
        print("Un solo monitor: prova di spostamento saltata", flush=True)
        return
    # Countdown su un monitor secondario, mouse al centro del primario, clic su «Countdown»
    sinistra, alto, _, _ = altri[0]
    app.countdown.win.geometry(f"+{sinistra + 60}+{alto + 60}")
    app.countdown.win.update()
    primario = m.monitor_di(0, 0)
    pl, pt, pr, pb = primario[1]
    m.posizione_mouse = lambda: ((pl + pr) // 2, (pt + pb) // 2)
    app.btn_countdown.invoke()
    app.countdown.win.update()
    x, y = app.countdown.win.winfo_rootx(), app.countdown.win.winfo_rooty()
    verifica(app.countdown.visibile and m.monitor_di(x + 10, y + 10)[0] == primario[0],
             "«Countdown» riporta il countdown sul monitor del mouse")
    verifica(app.imp["countdown_pos"] == [app.countdown.win.winfo_x(), app.countdown.win.winfo_y()],
             "nuova posizione memorizzata")
    # Posizione salvata nel vuoto fuori dai monitor: alla prossima comparsa torna sul primario
    app.countdown.nascondi()
    app.imp["countdown_pos"] = [-10 ** 6, -10 ** 6]
    app.mostra_countdown()
    app.countdown.win.update()
    x, y = app.countdown.win.winfo_rootx(), app.countdown.win.winfo_rooty()
    verifica(m.monitor_di(x + 10, y + 10, nullo_se_fuori=True) is not None,
             "posizione salvata non valida: countdown di nuovo su un monitor")
    scatta("09_countdown_riportato.png", app.countdown.win)


@passo(300)
def p_dialoghi():
    app.apri_settimana()


@passo(1000)
def p_screenshot_settimana():
    scatta("06_principale_con_settimana.png", root)
    scatta("07_settimana.png", app._dlg_settimana.win)
    app._dlg_settimana.win.destroy()
    app.apri_timer_manuale()


@passo(1000)
def p_screenshot_manuale():
    scatta("08_timer_manuale.png", app._dlg_manuale.win)
    app._dlg_manuale.win.destroy()
    verifica(app.icona is not None and app.icona.visible, "icona nell'area di notifica visibile")
    verifica(os.path.exists(m.FILE_IMPOSTAZIONI), "impostazioni salvate su file")


@passo(300)
def p_esci():
    app.esci()   # prima risposta: No
    verifica(app._vivo, "uscita rifiutata: l'app resta attiva")
    app.esci()   # seconda risposta: Si'


def esegui_passi(i=0):
    if i >= len(passi):
        return
    ritardo, funzione = passi[i]

    def lancia():
        try:
            funzione()
        except Exception:
            traceback.print_exc()
            verifica(False, f"eccezione nel passo {funzione.__name__}")
        esegui_passi(i + 1)

    root.after(ritardo, lancia)


esegui_passi()
root.mainloop()

salvate = m.carica_impostazioni()
verifica(salvate["serate"].get(chiave1, {}).get("eseguita") is True, "dopo l'uscita: serata eseguita su file")
errori_log = os.path.join(tmp, "errori.log")
if os.path.exists(errori_log):
    print("--- errori.log ---")
    print(open(errori_log, encoding="utf-8").read())
verifica(not os.path.exists(errori_log), "nessun errore registrato")

falliti = [n for ok, n in esiti if not ok]
print(f"\n{len(esiti) - len(falliti)}/{len(esiti)} verifiche superate")
for n in falliti:
    print("  FALLITA:", n)
sys.exit(1 if falliti else 0)
