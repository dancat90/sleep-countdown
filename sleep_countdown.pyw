"""
Calcolatore ore di sonno - GUI real-time.

Due modi di calcolo, commutabili col tasto centrale:
- A RITROSO (default): dalla sveglia ricava l'orario a cui iniziare la
  routine serale.
- IN AVANTI: dall'ora attuale ricava l'orario a cui mettere la sveglia.

In entrambi i modi la catena e' la stessa - routine serale, buffer di
addormentamento, sonno desiderato - e cambia solo l'estremo noto.

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
FG_OFF = "#484f58"    # campo escluso dal calcolo corrente
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
        self.root.geometry("460x500")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        # Variabili di input
        self.hour_var = tk.StringVar(value="07")
        self.minute_var = tk.StringVar(value="00")
        self.sleep_h_var = tk.StringVar(value="08")
        self.sleep_m_var = tk.StringVar(value="00")
        self.reading_var = tk.StringVar(value="30")
        # Spunte attivita' serali: se disattivate, il tempo relativo non viene sottratto
        self.reading_on = tk.BooleanVar(value=True)
        self.cane_on = tk.BooleanVar(value=True)
        self.cucina_on = tk.BooleanVar(value=True)
        # False = a ritroso dalla sveglia, True = in avanti dall'ora attuale.
        # All'avvio si riparte sempre dal modo a ritroso.
        self.modo_avanti = False

        self._build_ui()

        # Ricalcola a ogni modifica di un campo
        for var in (self.hour_var, self.minute_var, self.sleep_h_var,
                    self.sleep_m_var, self.reading_var,
                    self.reading_on, self.cane_on, self.cucina_on):
            var.trace_add("write", lambda *_: self._refresh())

        self._tick()

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

        hours = [f"{i:02d}" for i in range(24)]
        minutes5 = [f"{i:02d}" for i in range(0, 60, 5)]
        sleep_hours = [f"{i:02d}" for i in range(4, 13)]
        minutes15 = [f"{i:02d}" for i in range(0, 60, 15)]

        def combo(parent, var, values):
            return ttk.Combobox(
                parent, textvariable=var, values=values,
                width=4, state="readonly", font=("Segoe UI", 11),
            )

        # --- Riga sveglia (si attenua nel modo in avanti: li' e' il risultato) ---
        self.wake_label = tk.Label(form, text="Sveglia:", font=("Segoe UI", 11),
                                   fg=FG_MUTED, bg=BG)
        self.wake_label.grid(row=0, column=0, padx=5, pady=4, sticky="e")
        self.wake_h_combo = combo(form, self.hour_var, hours)
        self.wake_h_combo.grid(row=0, column=1)
        self.wake_colon = tk.Label(form, text=":", fg=FG_TITLE, bg=BG,
                                   font=("Segoe UI", 12, "bold"))
        self.wake_colon.grid(row=0, column=2)
        self.wake_m_combo = combo(form, self.minute_var, minutes5)
        self.wake_m_combo.grid(row=0, column=3, sticky="w")

        # --- Riga sonno desiderato ---
        tk.Label(form, text="Voglio dormire:", font=("Segoe UI", 11),
                 fg=FG_MUTED, bg=BG).grid(row=1, column=0, padx=5, pady=4, sticky="e")
        combo(form, self.sleep_h_var, sleep_hours).grid(row=1, column=1)
        tk.Label(form, text=":", fg=FG_TITLE, bg=BG,
                 font=("Segoe UI", 12, "bold")).grid(row=1, column=2)
        combo(form, self.sleep_m_var, minutes15).grid(row=1, column=3, sticky="w")

        def check(parent, var, text):
            # Spunta in tema scuro: il testo resta leggibile anche quando non selezionata
            return tk.Checkbutton(
                parent, text=text, variable=var, font=("Segoe UI", 10),
                fg=FG_MUTED, bg=BG, activebackground=BG, activeforeground=FG_TITLE,
                selectcolor="#161b22", highlightthickness=0, bd=0, cursor="hand2",
            )

        # --- Riga lettura (spunta per escluderla senza azzerare i minuti) ---
        check(form, self.reading_on, "Lettura (min):").grid(
            row=2, column=0, padx=5, pady=4, sticky="e")
        self.reading_entry = tk.Entry(
            form, textvariable=self.reading_var, width=6,
            font=("Segoe UI", 11), justify="center",
            bg="#161b22", fg=FG_TITLE, insertbackground=FG_TITLE,
            disabledbackground="#0d1117", disabledforeground=FG_DIM,
            relief="flat", highlightthickness=1,
            highlightbackground="#30363d", highlightcolor=COL_INFO,
        )
        self.reading_entry.grid(row=2, column=1, columnspan=3, pady=4, sticky="w")

        # --- Riga attivita' serali escludibili ---
        extras = tk.Frame(form, bg=BG)
        extras.grid(row=3, column=0, columnspan=4, pady=(6, 0))
        check(extras, self.cane_on,
              f"Cane (+{PASSEGGIATA_CANE_MIN})").pack(side="left", padx=8)
        check(extras, self.cucina_on,
              f"Cucina/igiene (+{CUCINA_IGIENE_MIN})").pack(side="left", padx=8)

        # --- Tasto di commutazione fra i due modi di calcolo ---
        self.mode_btn = tk.Button(
            self.root, text="Vado a dormire ora  →",
            font=("Segoe UI", 10, "bold"), fg=FG_TITLE, bg="#21262d",
            activebackground="#30363d", activeforeground=FG_TITLE,
            relief="flat", bd=0, padx=16, pady=7, cursor="hand2",
            command=self._commuta_modo,
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

        # Riga dei tempi sottratti: mostra solo le voci attive (aggiornata in _refresh)
        self.buffer_label = tk.Label(
            self.root, text="",
            font=("Segoe UI", 8, "italic"), fg=FG_DIM, bg=BG,
        )
        self.buffer_label.pack(pady=(6, 0))

    def _calcola(self):
        """Ritorna (sveglia, addormentato, a_letto, vai_a_dormire) o None se input non valido.

        La catena e' sempre: inizio routine -> a letto -> addormentato -> sveglia.
        Nel modo a ritroso l'estremo noto e' la sveglia impostata.
        Nel modo in avanti l'estremo noto e' l'ora attuale.
        """
        try:
            h = int(self.hour_var.get())
            m = int(self.minute_var.get())
            sh = int(self.sleep_h_var.get())
            sm = int(self.sleep_m_var.get())
            # Lettura esclusa dalla spunta: i minuti restano nel campo ma non contano
            lettura = int(self.reading_var.get()) if self.reading_on.get() else 0
            if not (0 <= h < 24 and 0 <= m < 60) or lettura < 0 or sh < 0 or sm < 0:
                raise ValueError
        except (ValueError, TypeError):
            return None
        cane = PASSEGGIATA_CANE_MIN if self.cane_on.get() else 0
        cucina = CUCINA_IGIENE_MIN if self.cucina_on.get() else 0

        attivita = timedelta(minutes=lettura + cane + cucina)
        buffer_sonno = timedelta(minutes=BUFFER_ADDORMENTAMENTO_MIN)
        sonno = timedelta(hours=sh, minutes=sm)
        now = datetime.now()

        if self.modo_avanti:
            # In avanti: la routine parte adesso, la sveglia e' il risultato
            vai_a_dormire = now
            a_letto = vai_a_dormire + attivita
            addormentato = a_letto + buffer_sonno
            sveglia = addormentato + sonno
        else:
            # A ritroso: sveglia -> addormentato -> a letto -> inizio routine
            sveglia = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if sveglia <= now:
                sveglia += timedelta(days=1)
            addormentato = sveglia - sonno
            a_letto = addormentato - buffer_sonno
            vai_a_dormire = a_letto - attivita
        return sveglia, addormentato, a_letto, vai_a_dormire

    def _refresh(self) -> None:
        # Campo lettura disabilitato visivamente quando la spunta e' spenta
        self.reading_entry.config(
            state="normal" if self.reading_on.get() else "disabled")
        self._refresh_buffer()
        ris = self._calcola()
        if ris is None:
            self.result_label.config(text="--:--", fg=COL_WARN)
            self.sub_label.config(text="Input non valido", fg=COL_WARN)
            self.info_label.config(text="")
            return

        sveglia, addormentato, a_letto, vai_a_dormire = ris
        now = datetime.now()
        wd = GIORNI[sveglia.weekday()]
        sonno_min = int((sveglia - addormentato).total_seconds() // 60)
        if sonno_min % 60 == 0:
            sonno_txt = f"{sonno_min // 60}h"
        else:
            sonno_txt = f"{sonno_min // 60}h{sonno_min % 60:02d}"

        if self.modo_avanti:
            # Il risultato e' l'ora della sveglia: niente semaforo, non c'e' un ritardo
            tot_min = int((sveglia - now).total_seconds() // 60)
            self.result_label.config(text=sveglia.strftime("%H:%M"), fg=COL_INFO)
            self.sub_label.config(
                text=f"sveglia di {wd}  ·  fra {tot_min // 60}h {tot_min % 60:02d}m",
                fg=FG_MUTED)
            self.info_label.config(
                text=(f"Inizi adesso {vai_a_dormire.strftime('%H:%M')}  ·  dormi {sonno_txt}\n"
                      f"a letto {a_letto.strftime('%H:%M')}  ·  "
                      f"addormentato {addormentato.strftime('%H:%M')}")
            )
            return

        # Modo a ritroso: countdown e semaforo verso l'ora di coricarsi
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
        self.info_label.config(
            text=(f"Sveglia {wd} {sveglia.strftime('%H:%M')}  ·  dormi {sonno_txt}\n"
                  f"a letto {a_letto.strftime('%H:%M')}  ·  "
                  f"addormentato {addormentato.strftime('%H:%M')}")
        )

    def _commuta_modo(self) -> None:
        """Passa dal calcolo a ritroso a quello in avanti, e viceversa."""
        self.modo_avanti = not self.modo_avanti
        avanti = self.modo_avanti
        self.mode_btn.config(
            text="←  Torna all'ora di coricarti" if avanti else "Vado a dormire ora  →",
            bg=COL_INFO if avanti else "#21262d",
            fg=BG if avanti else FG_TITLE,
            activebackground="#79c0ff" if avanti else "#30363d",
            activeforeground=BG if avanti else FG_TITLE,
        )
        self.subtitle_label.config(
            text="A che ora suonare la sveglia, se ti corichi adesso" if avanti
            else "L'ora per coricarti e dormire quanto vuoi")
        # Nel modo in avanti la sveglia e' il risultato: i suoi campi escono dal calcolo
        self.wake_label.config(fg=FG_OFF if avanti else FG_MUTED)
        self.wake_colon.config(fg=FG_OFF if avanti else FG_TITLE)
        stato = "disabled" if avanti else "readonly"
        self.wake_h_combo.config(state=stato)
        self.wake_m_combo.config(state=stato)
        self._refresh()

    def _refresh_buffer(self) -> None:
        """Riga dei tempi sottratti: solo le voci attive."""
        parti = [f"+{BUFFER_ADDORMENTAMENTO_MIN} addormentamento"]
        if self.reading_on.get():
            try:
                parti.append(f"+{int(self.reading_var.get())} lettura")
            except ValueError:
                parti.append("+? lettura")
        if self.cane_on.get():
            parti.append(f"+{PASSEGGIATA_CANE_MIN} cane")
        if self.cucina_on.get():
            parti.append(f"+{CUCINA_IGIENE_MIN} cucina/igiene")
        self.buffer_label.config(text="  ·  ".join(parti))

    def _tick(self) -> None:
        self._refresh()
        self.root.after(1000, self._tick)


def main() -> None:
    root = tk.Tk()
    SonnoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
