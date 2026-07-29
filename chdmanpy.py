from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore


def normalize_ext(ext: str) -> str:
    ext = ext.strip().lower()
    if not ext.startswith("."):
        ext = "." + ext
    return ext


def load_config(config_path: Path) -> dict:
    with config_path.open("rb") as f:
        return tomllib.load(f)


def find_files_recursive(root: Path, exts: tuple[str, ...]) -> list[Path]:
    ext_set = {e.lower() for e in exts}
    results: list[Path] = []

    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in ext_set:
            results.append(p)

    return results


def find_zip_files(root: Path) -> list[Path]:
    return find_files_recursive(root, (".zip",))


def make_unique_extract_dir(base_tmp: Path, zip_path: Path) -> Path:
    digest = hashlib.sha256(str(zip_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return base_tmp / f"{zip_path.stem}_{digest}"


def extract_zip(zip_path: Path, tmp_root: Path) -> dict:
    """
    ZIP を 1 件解凍する。
    失敗時は例外を握りつぶして結果 dict に入れて返す。
    """
    extract_dir = make_unique_extract_dir(tmp_root, zip_path)

    try:
        extract_dir.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(str(zip_path), str(extract_dir))
        return {
            "zip_file": str(zip_path),
            "extract_dir": str(extract_dir),
            "success": True,
            "error": "",
        }
    except Exception as e:
        # 途中まで展開された残骸を掃除
        shutil.rmtree(extract_dir, ignore_errors=True)
        return {
            "zip_file": str(zip_path),
            "extract_dir": str(extract_dir),
            "success": False,
            "error": repr(e),
        }


def parallel_extract_zips(
    zip_files: list[Path],
    tmp_dir: Path,
    workers_unzip: int,
) -> tuple[list[Path], list[dict]]:
    extracted_roots: list[Path] = []
    results: list[dict] = []

    total = len(zip_files)
    done = 0

    print_progress("[UNZIP]", done, total)

    with ThreadPoolExecutor(max_workers=workers_unzip) as executor:
        futures = {
            executor.submit(extract_zip, zip_path, tmp_dir): zip_path
            for zip_path in zip_files
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            if result["success"]:
                extracted_roots.append(Path(result["extract_dir"]))

            done += 1
            print_progress("[UNZIP]", done, total)

    print()
    return extracted_roots, results


def collect_target_files(
    original_root: Path,
    extracted_roots: Iterable[Path],
    target_exts: tuple[str, ...],
) -> list[Path]:
    results: list[Path] = []
    seen: set[Path] = set()

    for root in [original_root, *extracted_roots]:
        for p in find_files_recursive(root, target_exts):
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                results.append(p)

    return results


def find_chdman() -> str | None:
    executable_name = "chdman.exe" if os.name == "nt" else "chdman"
    local_executable = Path(__file__).resolve().with_name(executable_name)

    if local_executable.is_file() and os.access(local_executable, os.X_OK):
        return str(local_executable)

    return shutil.which("chdman")


def build_command(
    input_file: Path,
    output_file: Path,
    options_by_ext: dict[str, list[str]],
    chdman_command: str,
) -> list[str]:
    ext = input_file.suffix.lower()

    if ext not in options_by_ext:
        raise ValueError(f"unsupported extension: {ext}")

    return [
        chdman_command,
        *options_by_ext[ext],
        "-i",
        str(input_file),
        "-o",
        str(output_file),
    ]


def run(
    input_file: Path,
    output_file: Path,
    options_by_ext: dict[str, list[str]],
    chdman_command: str,
) -> dict:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        command = build_command(
            input_file,
            output_file,
            options_by_ext,
            chdman_command,
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "file": str(input_file),
            "output": str(output_file),
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except Exception as e:
        return {
            "file": str(input_file),
            "output": str(output_file),
            "command": None,
            "returncode": -1,
            "stdout": "",
            "stderr": repr(e),
        }


def parallel_run(
    jobs: list[tuple[Path, Path]],
    options_by_ext: dict[str, list[str]],
    workers: int,
    chdman_command: str,
) -> list[dict]:
    results: list[dict] = []
    done = 0
    total = len(jobs)

    print_progress("[RUN]", done, total)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                run,
                input_file,
                output_file,
                options_by_ext,
                chdman_command,
            ): (
                input_file,
                output_file,
            )
            for input_file, output_file in jobs
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            done += 1
            print_progress("[RUN]", done, total)

    print()  # progress bar の改行
    return results

def print_progress(prefix: str, done: int, total: int) -> None:
    width = 30
    ratio = 1.0 if total == 0 else done / total
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r{prefix} [{bar}] {done}/{total}", end="", flush=True)


def parse_config(config: dict) -> dict:
    """
    想定する TOML 構造を Python の扱いやすい形へ寄せる。
    """

    options_by_ext_raw = config["options"]
    options_by_ext = {
        normalize_ext(ext): list(opts)
        for ext, opts in options_by_ext_raw.items()
    }

    target_exts = tuple(options_by_ext.keys())

    workers = int(config.get("run", {}).get("workers", os.cpu_count() or 1))
    unzip = bool(config.get("run", {}).get("unzip_zip_files", True))

    return {
        "options_by_ext": options_by_ext,
        "target_exts": target_exts,
        "workers": max(1, workers),
        "unzip_zip_files": unzip,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path, help="directory to search")
    parser.add_argument("output_dir", type=Path, help="output directory")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="TOML configuration file",
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        help=(
            "parent directory for temporary ZIP extraction data "
            "(default: system temporary directory)"
        ),
    )
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    config_path = args.config.resolve()
    temp_dir = args.temp_dir.expanduser().resolve() if args.temp_dir else None

    chdman_command = find_chdman()
    if chdman_command is None:
        print(
            "[ERROR] chdman was not found. Place the executable in the same "
            "directory as chdmanpy.py, or install the chdman command and add "
            "it to PATH.",
            file=sys.stderr,
        )
        if os.name == "nt":
            print(
                "[INFO] On Windows, run .\\install-chdman.ps1 to download it.",
                file=sys.stderr,
            )
        raise SystemExit(1)

    print(f"[INFO] chdman: {chdman_command}")

    if not input_dir.is_dir():
        raise NotADirectoryError(f"input_dir is not a directory: {input_dir}")

    config_raw = load_config(config_path)
    conf = parse_config(config_raw)

    options_by_ext: dict[str, list[str]] = conf["options_by_ext"]
    target_exts: tuple[str, ...] = conf["target_exts"]
    workers: int = conf["workers"]
    workers_unzip: int = max(1, workers // 2)  # ZIP 展開用のワーカー数（全体の半分程度を割り当てる）
    unzip_zip_files: bool = conf["unzip_zip_files"]

    output_dir.mkdir(parents=True, exist_ok=True)

    if temp_dir is not None:
        if temp_dir.exists() and not temp_dir.is_dir():
            raise NotADirectoryError(
                f"temp_dir is not a directory: {temp_dir}"
            )
        temp_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="batch_tool_",
        dir=temp_dir,
    ) as tmp:
        tmp_dir = Path(tmp)
        print(f"[INFO] temporary directory: {tmp_dir}")
        extracted_roots: list[Path] = []

        # 1, 2. ZIP 列挙と展開
        if unzip_zip_files:
            zip_files = find_zip_files(input_dir)
            print(f"[INFO] zip files: {len(zip_files)}")

            extracted_roots, unzip_results = parallel_extract_zips(
                zip_files=zip_files,
                tmp_dir=tmp_dir,
                workers_unzip=workers_unzip,
            )

            unzip_success = sum(1 for r in unzip_results if r["success"])
            unzip_fail = len(unzip_results) - unzip_success
            print(f"[INFO] unzip success={unzip_success}, fail={unzip_fail}")

        # 3. 元ディレクトリと展開先から対象ファイルを列挙
        original_targets = find_files_recursive(input_dir, target_exts)

        extracted_targets: list[Path] = []
        for root in extracted_roots:
            extracted_targets.extend(find_files_recursive(root, target_exts))

        total_targets = len(original_targets) + len(extracted_targets)
        print(f"[INFO] target files: {total_targets}")

        # 入力ファイルごとに対応する source_root を持たせる
        jobs: list[tuple[Path, Path]] = []

        for f in original_targets:
            jobs.append((f, output_dir / f.with_suffix(".chd").name))

        for root in extracted_roots:
            for f in find_files_recursive(root, target_exts):
                jobs.append((f, output_dir / "_extracted" / f.with_suffix(".chd").name))

        # 4. 並列実行
        # 外部コマンド呼び出し主体なので ThreadPoolExecutor を使う。
        # CPU を内部で大量消費する別プロセス群を起動する用途ではこちらの方が扱いやすいことが多い。
        run_results = parallel_run(
            jobs=jobs,
            options_by_ext=options_by_ext,
            workers=workers,
            chdman_command=chdman_command,
        )

        success_count = sum(r["returncode"] == 0 for r in run_results)
        fail_count = len(run_results) - success_count

        print(f"[SUMMARY] success={success_count}, fail={fail_count}")

        failed_unzips = [r for r in unzip_results if not r["success"]]
        if failed_unzips:
            print("[FAILED UNZIP FILES]")
            for r in failed_unzips:
                print(f"- {r['zip_file']}")
                print(f"  error: {r['error']}")

        if fail_count:
            print("[FAILED FILES]")
            for r in run_results:
                if r["returncode"] != 0:
                    print(f"- {r['file']}")
                    err = r["stderr"].strip()
                    if err:
                        print(f"  stderr: {err}")


if __name__ == "__main__":
    main()
