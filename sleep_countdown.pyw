"""
Calcolatore ore di sonno - GUI real-time.
Calcola A RITROSO dalla sveglia l'orario a cui andare a dormire (inizio
della routine serale), dati le ore di sonno desiderate e i minuti di
lettura, scontando i tempi fissi: buffer addormentamento, passeggiata
cane, riassetto cucina + pulizia personale.

Stile dark theme, sempre in primo piano, aggiornamento al secondo.
Gemello desktop della web app PWA (stesso repo: index.html).
"""

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

# === Tempi fissi (minuti) - modificabili qui ===
BUFFER_ADDORMENTAMENTO_MIN = 15   # tempo per prendere sonno una volta a letto
PASSEGGIATA_CANE_MIN = 20         # passeggiata cane
CUCINA_IGIENE_MIN = 15            # riassetto cucina + pulizia personale
SOGLIA_GIALLO_MIN = 30            # entro quanto dall'ora di coricarsi scatta il giallo

# === Palette (coerente con la web app) ===
BG = "#0d1117"
FG_TITLE = "#c9d1d9"
FG_MUTED = "#8b949e"
FG_DIM = "#6e7681"
COL_OK = "#3fb950"
COL_WARN = "#d29922"
COL_BAD = "#f85149"
COL_INFO = "#58a6ff"

# Giorni abbreviati in italiano (strftime('%a') dipende dal locale di sistema)
GIORNI = {0: "lun", 1: "mar", 2: "mer", 3: "gio", 4: "ven", 5: "sab", 6: "dom"}


class SonnoApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("A che ora dormire")
        self.root.geometry("460x400")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        # Variabili di input
        self.hour_var = tk.StringVar(value="07")
        self.minute_var = tk.StringVar(value="00")
        self.sleep_h_var = tk.StringVar(value="08")
        self.sleep_m_var = tk.StringVar(value="00")
        self.reading_var = tk.StringVar(value="30")

        self._build_ui()

        # Ricalcola a ogni modifica di un campo
        for var in (self.hour_var, self.minute_var, self.sleep_h_var,
                    self.sleep_m_var, self.reading_var):
            var.trace_add("write", lambda *_: self._refresh())

        self._tick()

    def _build_ui(self) -> None:
        tk.Label(
            self.root, text="\U0001F319  A che ora dormire",
            font=("Segoe UI", 16, "bold"), fg=FG_TITLE, bg=BG,
        ).pack(pady=(15, 2))
        tk.Label(
            self.root, text="L'ora per coricarti e dormire quanto vuoi",
            font=("Segoe UI", 9), fg=FG_DIM, bg=BG,
        ).pack(pady=(0, 10))

        form = tk.Frame(self.root, bg=BG)
        form.pack(pady=2)

        hours = [f"{i:02d}" for i in range(24)]
        minutes5 = [f"{i:02d}" for i in range(0, 60, 5)]
        sleep_hours = [f"{i:02d}" for i in range(4, 13)]
        minutes15 = [f"{i:02d}" for i in range(0, 60, 15)]

        def combo(parent, var, values):
            return ttk.Combobox(
                parent, textvariable=var, values=values,
                width=4, state="readonly", font=("Segoe UI", 11),
            )

        # --- Riga sveglia ---
        tk.Label(form, text="Sveglia:", font=("Segoe UI", 11),
                 fg=FG_MUTED, bg=BG).grid(row=0, column=0, padx=5, pady=4, sticky="e")
        combo(form, self.hour_var, hours).grid(row=0, column=1)
        tk.Label(form, text=":", fg=FG_TITLE, bg=BG,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=2)
        combo(form, self.minute_var, minutes5).grid(row=0, column=3, sticky="w")

        # --- Riga sonno desiderato ---
        tk.Label(form, text="Voglio dormire:", font=("Segoe UI", 11),
                 fg=FG_MUTED, bg=BG).grid(row=1, column=0, padx=5, pady=4, sticky="e")
        combo(form, self.sleep_h_var, sleep_hours).grid(row=1, column=1)
        tk.Label(form, text=":", fg=FG_TITLE, bg=BG,
                 font=("Segoe UI", 12, "bold")).grid(row=1, column=2)
        combo(form, self.sleep_m_var, minutes15).grid(row=1, column=3, sticky="w")

        # --- Riga lettura ---
        tk.Label(form, text="Lettura (min):", font=("Segoe UI", 11),
                 fg=FG_MUTED, bg=BG).grid(row=2, column=0, padx=5, pady=4, sticky="e")
        tk.Entry(
            form, textvariable=self.reading_var, width=6,
            font=("Segoe UI", 11), justify="center",
            bg="#161b22", fg=FG_TITLE, insertbackground=FG_TITLE,
            relief="flat", highlightthickness=1,
            highlightbackground="#30363d", highlightcolor=COL_INFO,
        ).grid(row=2, column=1, columnspan=3, pady=4, sticky="w")

        # --- Risultato: orario a cui coricarsi ---
        self.result_label = tk.Label(
            self.root, text="--:--",
            font=("Segoe UI", 44, "bold"), fg=COL_INFO, bg=BG,
        )
        self.result_label.pack(pady=(18, 2))

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

        tk.Label(
            self.root,
            text=(f"+{BUFFER_ADDORMENTAMENTO_MIN} addormentamento  ·  "
                  f"+{PASSEGGIATA_CANE_MIN} cane  ·  "
                  f"+{CUCINA_IGIENE_MIN} cucina/igiene"),
            font=("Segoe UI", 8, "italic"), fg=FG_DIM, bg=BG,
        ).pack(pady=(6, 0))

    def _calcola(self):
        """Ritorna (sveglia, addormentato, a_letto, vai_a_dormire) o None se input non valido."""
        try:
            h = int(self.hour_var.get())
            m = int(self.minute_var.get())
            sh = int(self.sleep_h_var.get())
            sm = int(self.sleep_m_var.get())
            lettura = int(self.reading_var.get())
            if not (0 <= h < 24 and 0 <= m < 60) or lettura < 0 or sh < 0 or sm < 0:
                raise ValueError
        except (ValueError, TypeError):
            return None

        now = datetime.now()
        sveglia = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if sveglia <= now:
            sveglia += timedelta(days=1)

        # Catena a ritroso: sveglia -> addormentato -> a letto -> inizio routine
        addormentato = sveglia - timedelta(hours=sh, minutes=sm)
        a_letto = addormentato - timedelta(minutes=BUFFER_ADDORMENTAMENTO_MIN)
        vai_a_dormire = a_letto - timedelta(
            minutes=lettura + PASSEGGIATA_CANE_MIN + CUCINA_IGIENE_MIN)
        return sveglia, addormentato, a_letto, vai_a_dormire

    def _refresh(self) -> None:
        ris = self._calcola()
        if ris is None:
            self.result_label.config(text="--:--", fg=COL_WARN)
            self.sub_label.config(text="Input non valido", fg=COL_WARN)
            self.info_label.config(text="")
            return

        sveglia, addormentato, a_letto, vai_a_dormire = ris
        now = datetime.now()
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
            self.sub_label.config(text=f"tra {diff_min // 60}h {diff_min % 60:02d}m", fg=FG_MUTED)

        # Riepilogo sveglia + sonno + orari intermedi
        wd = GIORNI[sveglia.weekday()]
        sonno_min = int((sveglia - addormentato).total_seconds() // 60)
        if sonno_min % 60 == 0:
            sonno_txt = f"{sonno_min // 60}h"
        else:
            sonno_txt = f"{sonno_min // 60}h{sonno_min % 60:02d}"
        self.info_label.config(
            text=(f"Sveglia {wd} {sveglia.strftime('%H:%M')}  ·  dormi {sonno_txt}\n"
                  f"a letto {a_letto.strftime('%H:%M')}  ·  "
                  f"addormentato {addormentato.strftime('%H:%M')}")
        )

    def _tick(self) -> None:
        self._refresh()
        self.root.after(1000, self._tick)


def main() -> None:
    root = tk.Tk()
    SonnoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
