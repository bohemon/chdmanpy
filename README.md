# chdmanpy

This is a python script for `CHDMAN`.

## Installation (Windows)

Clone the repository, then run the installer from PowerShell:

```powershell
.\install-chdman.ps1
```

The installer downloads the official MAME 0.287 package for Windows x64 or
Arm64, verifies its SHA-256 checksum, and installs `chdman.exe` in the
repository directory. If script execution is disabled, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-chdman.ps1
```

## How this works

Basically, this script runs `chdman createcd` for `.cue` files, and `chdman createdvd` for `.iso` files.
In addition, this script extracts ZIP files in advance.

Overall flow:

1. Recursively search `<input_dir>` and collect ZIP files.
2. For each collected ZIP file, in parallel:
    1. Extract it into a temporary directory.
3. Search both the `<input_dir>` and the temporary directory, and collect files with the target extensions (i.e., `.cue` and `.iso`).
4. For each collected target file, in parallel:
    1. Set the output root to `<output_dir>` if the file is not from an extracted ZIP archive; otherwise, set it to `<output_dir>/_extracted`.
    2. Apply `CHDMAN` to the file with the configured options.

## Usage

`python chdmanpy.py <input_dir> <output_dir> --config <config_toml> [--temp-dir <directory>]`

Example:

```bash
python ./chdmanpy.py ./input ./output --config ps2.toml # ps2
```

```bash
python ./chdmanpy.py ./input ./output/ --config psp.toml # psp
```

ZIP archives are extracted under the operating system's default temporary
directory. If that filesystem does not have enough free space (for example,
when `/tmp` is a small `tmpfs` on Linux), use `--temp-dir` to select a
directory on a filesystem with sufficient capacity:

```bash
mkdir -p ./chdman-tmp
python3 ./chdmanpy.py INPUT_DIR OUTPUT_DIR \
  --config ps2.toml \
  --temp-dir ./chdman-tmp
```

The script creates a uniquely named working directory below the selected
directory and removes it automatically when processing finishes.
