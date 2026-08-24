# chdmanpy 使用方法・移行ガイド

[English](usage.md) · [README](../README.ja.md) ·
[JSON Lines schema v1](schema-v1.md) ·
[0.1.0リリースノート](release-notes-0.1.0.md)

## インストールと実行要件

chdmanpyは、Python 3.11以降のWindowsとLinuxをサポートします。ソースを
チェックアウトせずに再現可能なインストールを行うには、固定したv0.1.0タグを
GitHubから直接指定します。

```console
pipx install "chdmanpy @ git+https://github.com/bohemon/chdmanpy.git@v0.1.0"
```

このタグ付きソース形式にはGitが必要です。パッケージインデックスでリリースが
公開されている場合は、`pipx install chdmanpy`も使えます。

オフラインまたはバージョン固定のインストールでは、プロジェクトのGitHub Releaseから
汎用wheelをダウンロードし、公開されたSHA-256ダイジェストを検証してから、検証済みの
ローカルファイルをインストールします。

```console
pipx install ./chdmanpy-0.1.0-py3-none-any.whl
```

ソースのチェックアウトでは`pipx install .`を使います。pipxではなく通常の
仮想環境を使う場合は、環境を作成して有効化し、
`python -m pip install ./chdmanpy-0.1.0-py3-none-any.whl`でリリースwheelを
インストールします。

パッケージインデックスからのインストールは`pipx upgrade chdmanpy`、有効化済みの
仮想環境では`python -m pip install --upgrade chdmanpy`で更新します。削除にはそれぞれ
`pipx uninstall chdmanpy`または`python -m pip uninstall chdmanpy`を使います。
`chdmanpy`と`python -m chdmanpy`は同じインターフェイスを提供します。

CHDMANは外部の実行要件です。別途インストールした上で、`--chdman`、
`CHDMANPY_CHDMAN`、`[runtime].chdman`、`PATH`の順で選択します。chdmanpyは
変換中にCHDMANをダウンロードせず、ArcShuttleをインストール・起動することもありません。

リポジトリの`install-chdman.ps1`は、chdmanpyのインストーラーではなく、ソース配布物用の
任意の補助スクリプトです。Windows x64またはArm64では、固定したMAME 0.287パッケージを
ダウンロードし、記録済みのSHA-256ダイジェストを検証して、スクリプトと同じ
ディレクトリへ`chdman.exe`をコピーします。`PATH`やpipx環境は更新せず、その場所に
既存の`chdman.exe`があれば置き換えます。明示的に実行する前にスクリプトを確認し、
得られた実行ファイルは`--chdman`または設定で指定してください。この補助スクリプトは
wheelに含まれず、自動実行されません。

## コマンドと入力

コマンドは3種類あります。

```text
chdmanpy plan [OPTIONS] PATH...
chdmanpy plan [OPTIONS] --files-from FILE
chdmanpy plan [OPTIONS] --files0-from FILE
chdmanpy plan [OPTIONS] --arcshuttle-results FILE
chdmanpy run --manifest FILE [OPTIONS]
chdmanpy convert [OPTIONS] PATH...
chdmanpy convert [OPTIONS] --files-from FILE
chdmanpy convert [OPTIONS] --files0-from FILE
chdmanpy convert [OPTIONS] --arcshuttle-results FILE
```

`plan`はCHDMANを探索・実行せず、入力を検証してschema-v1の`job`レコードを出力します。
`run`は保存済みのchdmanpyマニフェスト全体を事前検証してから実行します。`convert`は
計画と実行を1回で行います。

`plan`と`convert`では、次の入力形式からちょうど1つを選びます。

- 1件以上のファイルまたはディレクトリを位置引数のパスで指定
- 改行区切りのパスには`--files-from FILE`
- NUL区切りのパスには`--files0-from FILE`
- ArcShuttle 0.3.2 schema-v2展開結果ストリームには`--arcshuttle-results FILE`

標準入力を使えるのは、オプションが明示的に`-`を許可する場合だけです。chdmanpyは
標準入力を暗黙には読みません。ArcShuttleの結果は上流の実行レコードであり、chdmanpyの
マニフェストではないため、`run --manifest`へ渡せません。

計画オプションは`plan`と`convert`で使えます。

| オプション | 目的 |
| --- | --- |
| `--output-dir DIR` | 出力ルート。環境変数またはTOMLにない場合は必須です。 |
| `--preset others\|ps2\|psp` | 同梱の形式プリセットを選択します。既定は`others`です。 |
| `--config FILE` | 厳密なUTF-8 TOML設定を読みます。 |
| `--existing fail\|skip\|rename` | 既存出力の処理方針。既定は`fail`です。 |
| `--priority INTEGER` | 符号付き32ビットのスケジュール優先度をマニフェストへ記録します。 |
| `--on-upstream-error fail\|skip` | 正常完了していないArcShuttle結果の処理方針。`--arcshuttle-results`専用です。 |

実行オプションは`run`と`convert`で使えます。`run`も`--config FILE`を受け付けます。

| オプション | 目的 |
| --- | --- |
| `--chdman COMMAND` | CHDMANの実行ファイルを選択します。 |
| `--workers COUNT` | 同時に動かすCHDMANプロセス数を制限します。 |
| `--fail-fast` | 最初のジョブ失敗後に、新しいジョブの開始を止めます。 |
| `--allow-changed` | 変更された主入力を、失敗ではなく警告付きで実行します。 |
| `--log-dir DIR` | 実行ログとジョブログの保存先ルートを選択します。 |

正確なオプション構文は`chdmanpy COMMAND --help`で確認してください。

## 直接実行と確認可能なワークフロー

ディレクトリを直接変換します。

```console
chdmanpy convert ./input --output-dir ./chd --preset ps2 >results.jsonl
```

実行前にマニフェストの編集可能なフィールドを確認・編集する場合は、計画と実行を
分離します。

```console
chdmanpy plan ./input --output-dir ./chd --preset ps2 >jobs.jsonl
chdmanpy run --manifest jobs.jsonl >results.jsonl
```

CHDMANの探索やジョブ開始より前にマニフェスト全体を検証します。編集前に
[schema-v1契約](schema-v1.md)を確認してください。

## ArcShuttleワークフロー

chdmanpyはZIPを探索・展開しません。アーカイブの探索、展開、ステージング、後処理は
ArcShuttleの役割です。BashやZshなど、`pipefail`をサポートするシェルでは、次の
直接パイプラインを使えます。

```bash
set -o pipefail
arcshuttle extract --output-dir ./extracted game.zip |
  chdmanpy convert --arcshuttle-results - --output-dir ./chd --preset ps2 \
  >results.jsonl
```

PowerShell 7では次のように接続できます。

```powershell
& arcshuttle extract --output-dir .\extracted .\game.zip |
    & chdmanpy convert --arcshuttle-results - --output-dir .\chd --preset ps2 |
    Set-Content -Encoding utf8NoBOM .\results.jsonl
$pipelineSucceeded = $?
$chdmanpyStatus = $LASTEXITCODE
if (-not $pipelineSucceeded -and $chdmanpyStatus -eq 0) { exit 1 }
exit $chdmanpyStatus
```

PowerShellパイプラインの直後に、`$?`と`$LASTEXITCODE`の両方を保存してください。
`$pipelineSucceeded`は`Set-Content`の失敗を検出し、`$chdmanpyStatus`は別の
ネイティブプロセスを実行するまで、直前のネイティブプロセスの終了コードを保持します。
直接形式は簡潔ですが、`pipefail`は対応するシェルへパイプラインの失敗を報告するだけです。
生成側の終了コードをchdmanpyへ伝えず、後段の変換が始まらなかったことも保証しません。
ArcShuttle schema-v2の`summary`にも、生成側プロセスの終了コードは含まれません。
ArcShuttleプロセスの正常終了まで確認する必要がある場合は、変換前に出力を保存して
終了コードを確認します。

安全なPOSIXでの引き渡し例：

```sh
results=./arcshuttle-results.jsonl
if arcshuttle extract --output-dir ./extracted game.zip >"$results"; then
  chdmanpy convert --arcshuttle-results "$results" \
    --output-dir ./chd --preset ps2 >./results.jsonl
else
  arcshuttle_status=$?
  printf 'ArcShuttle failed with exit %s; conversion was not started.\n' \
    "$arcshuttle_status" >&2
  exit "$arcshuttle_status"
fi
```

バイト列を保持するPowerShellでの引き渡しにはPowerShell 7を使い、ネイティブ標準出力の
ストリームを直接コピーします。これにより、Windows PowerShell 5.1や古いPowerShellの
テキストリダイレクト時のエンコーディングに依存しません。

```powershell
$arcResults = Join-Path $PWD "arcshuttle-results.jsonl"
$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = "arcshuttle"
$start.UseShellExecute = $false
$start.RedirectStandardOutput = $true
foreach ($argument in @(
    "extract", "--output-dir", ".\extracted", ".\game.zip"
)) {
    [void] $start.ArgumentList.Add($argument)
}

$arcshuttle = [System.Diagnostics.Process]::Start($start)
$output = [System.IO.File]::Create($arcResults)
try {
    $arcshuttle.StandardOutput.BaseStream.CopyTo($output)
} finally {
    $output.Dispose()
}
$arcshuttle.WaitForExit()
if ($arcshuttle.ExitCode -ne 0) {
    throw "ArcShuttle failed with exit $($arcshuttle.ExitCode); conversion was not started."
}

& chdmanpy convert --arcshuttle-results $arcResults `
    --output-dir .\chd --preset ps2 > .\results.jsonl
$conversionSucceeded = $?
$chdmanpyStatus = $LASTEXITCODE
if (-not $conversionSucceeded -and $chdmanpyStatus -eq 0) { exit 1 }
exit $chdmanpyStatus
```

既定の`--on-upstream-error fail`は、確定済みの`success`でない結果または警告を1件でも
含むArcShuttleストリーム全体を拒否します。明示的な`--on-upstream-error skip`は、
検証済みの`success`ルートだけを使い、省略した全項目を標準エラー出力へ報告します。
後段の変換が成功しても終了コードは1です。不正な構造、矛盾する`summary`、安全でない
パス、不完全なステージングは常に拒否します。規範については、
[ArcShuttle取り込み契約](arcshuttle-schema-v2.ja.md)を参照してください。

## ストリーム、ログ、ステージング、終了コード

標準出力（`stdout`）は、BOMなしUTF-8 JSON Lines専用です。

- `plan`は`job`レコードだけを出力します。
- `run`と`convert`はジョブごとに順序を保った`result`を1件ずつ出力し、最後にちょうど1件の
  `summary`を出力します。

診断メッセージ、選択したCHDMANとそのバージョン、実行ログのパスは標準エラー出力
（`stderr`）へ出力します。実行イベントは進捗としてストリーム出力せず、実行ログへ
記録します。CHDMANの標準出力と標準エラー出力は、結果ごとの`log_path`へ記録します。
既定では、最初に計画された出力先の親ディレクトリ以下にある`.chdmanpy-logs`へ保存します。
別のルートを指定するには`--log-dir`を使います。

JSON Linesの利用側はEOFまで読み、末尾の`summary`を必須としてください。途中の
`success`結果だけでは、コマンド呼び出しの完了を意味しません。結果の状態は次のとおりです。

- `success`：検証済みCHDを正常に公開しました。
- `warning`：ジョブは完了しましたが、警告があります。
- `failed`：ジョブが失敗し、chdmanpyが所有するステージングを保持した場合があります。
- `skipped`：ジョブを意図的に実行しませんでした。
- `interrupted`：割り込みにより、完了前または実行中に停止しました。

各変換は、出力先と同じ親ディレクトリにある専用の`.failed`ステージングディレクトリへ
書き込み、検証済みCHDを上書きせずに公開します。成功時はchdmanpyが所有する
ステージングを削除します。失敗または割り込み時は検査用に保持し、絶対パスを
`staging_path`として報告します。chdmanpyは入力やArcShuttleの出力ディレクトリを
変更しません。

既存の出力先には、マニフェストで明示した`fail`（既定）、`skip`、再現可能な`rename`の
処理方針を使います。どの処理方針もCHDを破壊的に上書きしません。

| 終了コード | 意味 |
| ---: | --- |
| 0 | 正常終了 |
| 1 | 警告またはスキップを伴う完了。受理した上流処理の部分実行を含みます。 |
| 2 | 1件以上のCHDMANジョブが失敗 |
| 64 | 使用方法、設定、入力、ストリーム、マニフェストのエラー。事前検証の失敗後はジョブを開始しません。 |
| 130 | 割り込み。実行開始後は有効な`result`と`summary`を伴う場合があります。 |

終了コード1、2、130は、有効な`result`レコードとそれに続く`summary`を伴う場合があります。

## 設定

設定の優先順位は、CLI、`CHDMANPY_*`環境変数、明示的なTOML、同梱のプリセット、
既定値の順です。未知のキーと不正な型はエラーです。

```toml
[options]
".cue" = ["createcd"]
".iso" = ["createdvd", "-c", "zlib"]

[planning]
output_dir = "./chd"
existing = "fail"
priority = 0

[runtime]
chdman = "chdman"
```

同梱プリセットの対応は次のとおりです。

| プリセット | 拡張子 | CHDMAN作成引数 |
| --- | --- | --- |
| `others` | `.cue` | `createcd` |
| `ps2` | `.cue` | `createcd` |
| `ps2` | `.iso` | `createdvd -c zlib` |
| `psp` | `.iso` | `createdvd -hs 2048 -c zstd` |

従来の`[options]`テーブルは引き続き受理し、選択したプリセットの拡張子対応全体を
置き換えます。入力、出力、強制実行に関する引数はchdmanpyが管理するため、TOMLからは
指定できません。

サポートする環境変数は次のとおりです。

| 変数 | 受理する値と意味 |
| --- | --- |
| `CHDMANPY_OUTPUT_DIR` | `<path>`：空でない出力ルート。`--output-dir`相当です。 |
| `CHDMANPY_EXISTING` | `fail` / `skip` / `rename`。`--existing`相当です。 |
| `CHDMANPY_PRIORITY` | `-2147483648..2147483647`：10進整数。`--priority`相当です。 |
| `CHDMANPY_PRESET` | `others` / `ps2` / `psp`。`--preset`相当です。 |
| `CHDMANPY_CHDMAN` | `<executable-name-or-path>`：空でない単一の実行ファイル名またはパス。`PATH`より先に使い、シェル断片や引数は受理しません。 |

## 従来のスクリプトからの移行

従来の`python chdmanpy.py INPUT OUTPUT --config FILE`インターフェイスは、0.1.0では
互換性の対象ではありません。

| 従来の動作 | 0.1.0での置き換え |
| --- | --- |
| `python chdmanpy.py INPUT OUTPUT --config ps2.toml` | `chdmanpy convert INPUT --output-dir OUTPUT --preset ps2` |
| 独自の`[options]` TOML | `--config FILE`を継続利用できます。`[options]`も引き続きサポートします。 |
| ZIPの探索・展開 | ArcShuttleを別プロセスで実行し、`--arcshuttle-results`を渡します。chdmanpyにアーカイブ処理機能はありません。 |
| `--temp-dir`、`unzip_zip_files`、`_extracted`出力 | chdmanpy内の置き換えはありません。展開方針はArcShuttleが担当し、出力では名前空間を分けた相対パスを保持します。 |
| `[run].workers` | コマンド呼び出し全体に適用される`--workers COUNT`を使います。 |
| 標準出力上の人間向け進捗・結果 | 標準出力のJSON Linesを処理し、標準エラー出力の診断と結果のログパスを参照します。 |
| `chdmanpy.py`と同じ場所の`chdman.exe` | `--chdman`、`CHDMANPY_CHDMAN`、`[runtime].chdman`、`PATH`を使います。 |
