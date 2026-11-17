"""
Small UI for corelation.py

1. Pick the VD PWR input folder, the C2 EPR folder and an output folder.
2. The input is copied to  <output>\\VDPWR_EPR\\<input folder name>
   (the original input folder is never changed).
3. Steps 1-3 of corelation.py run on that copy.

Run:  python corelation_ui.py
"""

import os
import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import corelation

OUTPUT_SUBFOLDER = "VDPWR_EPR"


def destination_for(input_dir: Path, output_dir: Path) -> Path:
    base = output_dir / OUTPUT_SUBFOLDER
    # input already called VDPWR_EPR -> don't nest VDPWR_EPR\VDPWR_EPR
    if input_dir.name.lower() == OUTPUT_SUBFOLDER.lower():
        return base
    return base / input_dir.name


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Corelation - VD PWR EPR report update")
        self.geometry("820x560")
        self.minsize(640, 420)
        self.log_queue = queue.Queue()
        self.worker = None

        self.input_var = tk.StringVar()
        self.c2_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.step_vars = {s: tk.BooleanVar(value=True) for s in ("1", "2", "3")}

        self._build()
        self.after(100, self._drain_log)

    # ------------------------------------------------------------ layout
    def _build(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        rows = [
            ("VD PWR input folder:", self.input_var),
            ("C2 EPR folder:", self.c2_var),
            ("Output folder:", self.output_var),
        ]
        for r, (label, var) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(frm, textvariable=var).grid(row=r, column=1, sticky="ew", padx=5)
            ttk.Button(frm, text="Browse...",
                       command=lambda v=var: self._browse(v)).grid(row=r, column=2)

        steps = ttk.LabelFrame(frm, text="Steps", padding=6)
        steps.grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        texts = {
            "1": "1. Delete testresult.deb from PHY.ALL.1-5 .grltrace",
            "2": "2. Delete PDF and CSV report files",
            "3": "3. Copy C2 EPR pass results into PD_Merged.xml (PHY.ALL.1-3)",
        }
        for i, s in enumerate(("1", "2", "3")):
            ttk.Checkbutton(steps, text=texts[s], variable=self.step_vars[s]) \
                .grid(row=i, column=0, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, sticky="w")
        self.run_btn = ttk.Button(btns, text="Run", command=self._run)
        self.run_btn.pack(side="left")
        self.open_btn = ttk.Button(btns, text="Open output folder",
                                   command=self._open_output, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Clear log",
                   command=lambda: self.log.delete("1.0", "end")).pack(side="left")

        self.log = ScrolledText(frm, height=18, font=("Consolas", 9))
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        frm.rowconfigure(5, weight=1)

    def _browse(self, var):
        path = filedialog.askdirectory(initialdir=var.get() or None)
        if path:
            var.set(str(Path(path)))

    def _open_output(self):
        dest = getattr(self, "last_dest", None)
        if dest and dest.exists():
            os.startfile(dest)

    # ------------------------------------------------------------ run
    def _run(self):
        inp = Path(self.input_var.get().strip().strip('"'))
        c2 = self.c2_var.get().strip().strip('"')
        out = self.output_var.get().strip().strip('"')
        steps = {s for s, v in self.step_vars.items() if v.get()}

        if not self.input_var.get().strip() or not inp.is_dir():
            return messagebox.showerror("Input", "Select a valid VD PWR input folder.")
        if not out:
            return messagebox.showerror("Output", "Select an output folder.")
        if not steps:
            return messagebox.showerror("Steps", "Select at least one step.")
        if "3" in steps and (not c2 or not Path(c2).exists()):
            return messagebox.showerror("C2 EPR", "Select a valid C2 EPR folder for step 3.")

        out = Path(out)
        dest = destination_for(inp, out)
        if is_inside(out, inp):
            return messagebox.showerror("Output",
                                        "Output folder must not be inside the input folder.")
        if dest.exists():
            if not messagebox.askyesno(
                    "Output exists",
                    f"{dest}\nalready exists.\n\nDelete it and create it again from the input?"):
                return

        self.run_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.last_dest = dest
        self.worker = threading.Thread(target=self._work,
                                       args=(inp, c2, dest, steps), daemon=True)
        self.worker.start()

    def _work(self, inp, c2, dest, steps):
        def log(msg):
            self.log_queue.put(msg + "\n")

        ok = False
        try:
            if dest.exists():
                log(f"Removing old output: {dest}")
                shutil.rmtree(dest)
            log(f"Copying\n  {inp}\n-> {dest}")
            # PDF/CSV reports are not copied at all when step 2 is selected
            corelation.copy_run(inp, dest, skip_reports="2" in steps, log=log)
            corelation.process([str(dest)], c2, steps, log=log)
            ok = True
        except Exception as e:  # show any error in the log
            log(f"\nERROR: {e}")
        finally:
            self.log_queue.put(("__finished__", ok))

    def _drain_log(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple):
                    self.run_btn.config(state="normal")
                    self.open_btn.config(state="normal")
                    if item[1]:
                        messagebox.showinfo("Done", f"Output ready:\n{self.last_dest}")
                    else:
                        messagebox.showerror("Failed", "See the log for details.")
                else:
                    self.log.insert("end", item)
                    self.log.see("end")
        except queue.Empty:
            pass
        self.after(100, self._drain_log)


if __name__ == "__main__":
    App().mainloop()
