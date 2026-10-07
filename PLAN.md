# Plan: what is left

Written for the project owner. Everything built so far is described in `CLAUDE.md`; the finished plans are in git
history (`git show 7a38cfe:PLAN.md` for phases 0 to 4, `git show 5259d4a:PLAN.md` for the Pi session results,
`git show fc30566:PLAN.md` for the list that sections B to D of this file came from).
`ToDo.md` is the short list; this file is the reasoning.

**State on 2026-10-07 (version 1.1.0):** the decisions, the small fixes and the new plotting features that were
open are done and have tests (queue copies and order, plot adjustments, the area check, resuming after a power cut,
the pen log, the history schema version, the login throttle and HTTPS, the file name guard, test hygiene). Nothing
has run on a real plotter, so what is left is checking on hardware, and one optional change.

Sizes: S under an hour, M a few hours, L a day or more.

## Order

1. **When a plotter is connected:** the checklist below. It decides whether several features work. The new ones
   to look at: A13 to A16.
2. **Later:** measure D1 on a Pi.

---

## A. Needs a real plotter

None of this can be proved without hardware. The simulator (`tests/sim_plotter.py`) answers the queries
and drains a buffer, and has no CTS line and no pen, paper or mechanics. Run `tests/pi_serial_check.py`
first as a smoke test, then the real thing; each line below says what to look for.

| # | Check | What to look for | If it fails |
|---|---|---|---|
| A1 | **Stop** (`ESC.K` then `PU;`) in each flow control mode | The pen lifts and the plotter goes quiet within a second. Buffer modes (CTS/RTS, Software) abort the buffer; XON/XOFF, None and HP-IB do not send `ESC.K`, so the plotter keeps drawing what it holds: is that what you want? | Send `PU;` (and `ESC.K` where the plotter supports it) in the other modes too. |
| A2 | **Pause and Resume** | The plotter finishes its buffer, then idles; Resume carries on with no gap or repeat. | Note how much it draws after Pause; consider lowering the chunk size while paused. |
| A3 | **Pen change pause** with a hand-fitted holder | Pause happens at the pen boundary, the pen lifts (`PU;`), the dialog names the pen, and the plot continues in the new pen. Never `SP0`. | Check `SP` handling on that model. |
| A4 | **Jog, pen up/down, pick pen, trace plot area, where is the pen?** | The distances match the step size (this depends on the HP-GL unit size: 40 or 40.2 units per mm), the trace draws the real bounds, and `OA;` answers. | Wrong distances mean the unit constant (`UNITS_PER_MM`) is wrong for that plotter: make it a device setting. |
| A5 | **Resume accuracy** | Stop a plot half way, change nothing, Resume with rewind 0: is there a gap or an overdraw at the join? Try rewind 256 B and 1 KB without buffer feedback. | Tune the defaults in the resume dialog (0 with buffer feedback, 1 KB without). |
| A6 | **Reconnect** | Pull the USB adapter mid-plot, replug: the plot holds, reconnects, and after Resume the join is clean. | Tune `REWIND_NO_FEEDBACK` (1024) and `REWIND_HPIB` (64). |
| A7 | **Queue** | Two files with Tasmota on: the plotter is switched on once and off after the last, and a paper change pause holds between them. | See the Tasmota notes in `CLAUDE.md`. |
| A8 | **Tasmota power off** | The delay (default 30 s) is long enough for the last strokes. There is no verified HP-GL query for "finished drawing". | Try `OA;` polling for position stability, or `ESC.B` returning the full buffer plus a quiet period. |
| A9 | **CTS/RTS** | The hand-polled CTS (`getCTS`) works, since the simulator cannot test it. | Never enable pyserial's `rtscts` here (known bug); see `CLAUDE.md`. |
| A10 | **Software flow control throughput** | The sender sleeps 0.1 s per 30 byte chunk once the plotter's buffer is more than half full, which caps it at about 300 B/s. A fast plotter could be held back. Compare the speed of a dense plot with another sender. | Shorter sleep or larger chunk when the buffer is nearly empty; measure first. |
| A11 | **Origin across a power cut** | Whether the plotter keeps its origin across a power cycle. See A16, which builds on it. | Refuse to resume `interrupted` plots. |
| A12 | **HP-IB (Plug n Plot)** | 1 byte at a time at 9600 baud works and is not impossibly slow. | Larger chunks if the adapter buffers. |
| A13 | **Plot adjustments** (speed, force, acceleration) | The plotter takes `VS`, `FS`, `AS` with the values in the form and the pen visibly changes speed or pressure. The ranges in `hpgl_analysis` (`SPEED_RANGE`, `FORCE_RANGE`, `ACCEL_RANGE`) are guesses for HP plotters. | Narrow the ranges per plotter (a device setting). |
| A14 | **Origin offset** | The drawing lands where the offset says on the paper, for a file from vpype (relative moves after the first absolute one). | A plotter that treats its origin differently needs the offset applied another way (`IP`). |
| A15 | **Area check** | The pen goes round the right rectangle with the pen up and the plot waits for Resume; Resume then draws from the start with no stray mark. | Check the units (A4) and whether the trace needs the plotter's own `IN` first. |
| A16 | **Resume after a power cut** (D2 is built) | Switch the plotter off mid-plot, power it up, Resume the interrupted plot with a 4 KB rewind: does it keep its origin, and does the join look right? Same as A11, which this answers. | Raise the default rewind, or keep the origin in the page and refuse to resume. |


## D. Later, only if wanted

### D1. Conversion in its own process: built, to be measured (S)
Done in 1.1.0 (`convert_runner.py`, setting "Convert files in a separate process"). Still to do on a Pi Zero 2 W: repeat the measurement (six conversions during a 15 KB plot: 715 ms longest gap, 2.4 s longer, before) and look at the memory with the child running. If the Zero cannot spare a second copy of vpype, make the setting default to off there, or stop importing `convert_vpype` in `main.py` (move `output_name` and `_build` into a module without vpype).

### D4. Other things noticed
- Replay was smooth on 15 to 46 KB files; nothing here was a multi-megabyte vpype file. Try one before relying on it for large plots.
- `install.sh` is untouched by the CI fixes: its two guards (no `sudo bash install.sh`, `$HOME` must belong to the user) protect real users.

---

## Housekeeping

Keep `PLAN.md` while items are open and delete it when the list is empty. Update `CLAUDE.md` in the same commit as each feature, and tick the matching line in `ToDo.md`.
