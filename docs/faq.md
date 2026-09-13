# Frequently Asked Questions

Questions that have come up more than once, mostly from the
[issue tracker](https://github.com/jldz9/InSARHub/issues).

## The `insarhub-app` link will not open

`insarhub-app` prints a URL such as `http://127.0.0.1:8080`, but the browser shows
nothing — a connection error, a blank page, or someone else's application.

Port **8080** is a popular default, so something else on the machine has very
likely taken it. Start the app somewhere else:

```bash
insarhub-app --port 12345
```

Then open the URL the app prints, not the old one. Any free port works.

??? note "Other things worth checking"
    - **Open the URL it printed.** With `--host 0.0.0.0` the app is reachable
      from other machines, but the address to type is still the host's own.
    - **Remote or HPC sessions.** If `insarhub-app` runs on a remote machine,
      forward the port first: `ssh -L 12345:127.0.0.1:12345 user@host`, then
      open `http://127.0.0.1:12345` on your own computer.
    - **Blank page rather than a connection error.** That means the server is
      up but the frontend bundle is missing — reinstall InSARHub, and see
      [Installation](quickstart/install.md).

## Search finds scenes, but Download fails with "Search does not return any result"

Fixed in **0.4.0** ([#7](https://github.com/jldz9/InSARHub/issues/7)). Upgrade:

```bash
conda install -c conda-forge insarhub
```

`asf_search` 13.0.0 changed how it decides which frame field a query means. Its
`should_use_asf_frame()` stopped recognising a generic `platform=SENTINEL-1`
search, so the frame filter silently queried the **ESA** frame number and
matched nothing — which is why searching worked while downloading found zero
scenes. InSARHub now routes frame filters to `asfFrame` explicitly, which
behaves the same on `asf_search` 12.x and 13.x.

If you previously pinned `asf_search=12.3.2` as a workaround, you can lift the
pin.

## ISCE2 stops part-way through and the terminal process disappears

Two different causes, and they look identical from the outside.

**A silent crash at a fixed step** was a bug in 0.3.x
([#6](https://github.com/jldz9/InSARHub/issues/6)): `_fix_cmd` failed to strip
the trailing `&` that ISCE2 writes into every `run_files` line, so the command
returned instantly with success while its real work was orphaned. **Fixed in
0.4.0.**

**A crash at a varying step, often on a large stack, is usually the kernel's
OOM killer.** InSARHub runs each `run_files` line in parallel, and several
ISCE2 steps are memory-hungry, so the total can exceed RAM. Lower the
concurrency:

```bash
insarhub processor -N ISCE2_S1 -w /path/to/workdir submit --max_workers 2
```

`--max_workers` is the knob for every step except `topo` (`run_01`), which is a
single command — use `--num_proc4topo` for that one. See
[Processor](advanced/processor.md).

??? note "Telling the two apart"
    Run with `INSARHUB_DEBUG=1` and check `executor.log` in the work directory.
    An OOM kill leaves the step at `RUNNING` with no error, and
    `dmesg | grep -i "killed process"` names the process. See
    [Logging](advanced/cli_reference.md#logging).

## What does `pip install -e .` install?

The **trailing dot is required** — it means "the package in the current
directory" ([#2](https://github.com/jldz9/InSARHub/issues/2)). Activate the
environment first, and run it from the repository root:

```bash
conda activate insarhub
cd InSARHub
pip install -e .
```

`-e` is an *editable* install: the environment points at your checkout, so code
changes take effect without reinstalling. You only need this to work on
InSARHub itself — to simply use it, install from conda-forge as in
[Installation](quickstart/install.md).

## Which satellites are supported?

Sentinel-1 and NISAR today ([#1](https://github.com/jldz9/InSARHub/issues/1)):

| Product | Search & download | Processing |
|---|---|---|
| Sentinel-1 SLC | yes | HyP3, ISCE2, GMTSAR |
| Sentinel-1 Burst | yes | ISCE3 / COMPASS + dolphin |
| NISAR GSLC | yes | ISCE3 + dolphin |
| NISAR RSLC, GUNW | yes | not yet — no processor consumes them |

NISAR RSLC and GUNW are hidden in the web UI for that reason: they would
download a large volume of data that nothing can then process. They remain
available from the CLI and the Python API.

ALOS, ERS and other platforms are not supported yet.

## How do I see what InSARHub is doing?

It is quiet by default — only the command's output, plus warnings and errors.
Set `INSARHUB_DEBUG=1` for the full log:

```bash
INSARHUB_DEBUG=1 insarhub processor -N ISCE2_S1 -w /path/to/workdir submit
```

This works for the CLI, `insarhub-app` and `import insarhub` alike. Details in
[Logging](advanced/cli_reference.md#logging).

## `--select-pair` warns that no pair graph will be generated

Expected for **S1_Burst** and the **NISAR** products. ASF publishes no
perpendicular baseline for them, so a baseline-vs-time network cannot be drawn.

The pairs are still created, and processing is unaffected: the ISCE3 processors
build their own interferogram network through dolphin's phase linking, using
temporal and index constraints rather than a perpendicular-baseline threshold.
See [Pair Quality Scoring](advanced/pair_quality.md).
