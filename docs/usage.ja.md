# chdmanpy usage・移行guide

[English](usage.md) · [README](../README.ja.md) ·
[JSON Lines schema v1](schema-v1.md)

## installとruntime要件

chdmanpyはPython 3.11以降のWindowsとLinuxをsupportします。公開済みreleaseの
推奨install方法は次のとおりです。

```console
pipx install chdmanpy
```

offlineまたはversion固定installでは、projectのGitHub Releaseからuniversal wheelを
downloadし、公開されたSHA-256 digestを検証してから、検証済みlocal fileをinstallします。

```console
pipx install ./chdmanpy-0.1.0-py3-none-any.whl
```

source checkoutでは`pipx install .`を使います。pipxではなく通常のvirtual environmentを
使う場合は、環境を作成・activateし、
`python -m pip install ./chdmanpy-0.1.0-py3-none-any.whl`でrelease wheelをinstallします。

registryからのinstallは`pipx upgrade chdmanpy`、activate済みvirtual environmentでは
`python -m pip install --upgrade chdmanpy`でupgradeします。削除にはそれぞれ
`pipx uninstall chdmanpy`または`python -m pip uninstall chdmanpy`を使います。
`chdmanpy`と`python -m chdmanpy`は同じinterfaceを公開します。

CHDMANは外部runtime要件です。別途installした上で、`--chdman`、
`CHDMANPY_CHDMAN`、`[runtime].chdman`、`PATH`の順で選択します。chdmanpyは
変換中にCHDMANをdownloadせず、ArcShuttleをinstall・起動することもありません。

repositoryの`install-chdman.ps1`は、chdmanpy installerではなく、source
distribution用の任意helperです。Windows x64またはArm64で、固定したMAME 0.287
packageをdownloadし、記録済みSHA-256 digestを検証して、scriptと同じdirectoryへ
`chdman.exe`をcopyします。`PATH`やpipx環境は更新せず、その場所に既存の
`chdman.exe`があれば置き換えます。明示的に実行する前にscriptを確認し、得られた
executableは`--chdman`または設定で指定してください。このhelperはwheelに含まれず、
自動実行されません。

## commandと入力

3種類のcommand familyがあります。

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

`plan`はCHDMANを探索・実行せず、入力を検証してschema-v1 `job` recordを出力します。
`run`は保存済みchdmanpy manifest全体をpreflightしてから実行します。`convert`は計画と
実行を1回で行います。

`plan`と`convert`では、次の入力形式からちょうど1つを選びます。

- 1件以上のfileまたはdirectoryをpositional pathで指定
- 改行区切りpathには`--files-from FILE`
- NUL区切りpathには`--files0-from FILE`
- ArcShuttle 0.3.2 schema-v2 extract streamには`--arcshuttle-results FILE`

stdinを使えるのは、optionが明示的に`-`を許可する場合だけです。chdmanpyはstdinを暗黙に
読みません。ArcShuttle resultはupstream実行recordであり、chdmanpy manifestではない
ため、`run --manifest`へ渡せません。

planning optionは`plan`と`convert`で使えます。

| option | 目的 |
| --- | --- |
| `--output-dir DIR` | 出力root。環境変数またはTOMLにない場合は必須です。 |
| `--preset others|ps2|psp` | bundled format presetを選択します。既定は`others`です。 |
| `--config FILE` | 厳密なUTF-8 TOML設定を読みます。 |
| `--existing fail|skip|rename` | 既存出力policy。既定は`fail`です。 |
| `--priority INTEGER` | signed 32-bit scheduling priorityをmanifestへ記録します。 |
| `--on-upstream-error fail|skip` | cleanでないArcShuttle resultのpolicy。`--arcshuttle-results`専用です。 |

runtime optionは`run`と`convert`で使えます。`run`も`--config FILE`を受け付けます。

| option | 目的 |
| --- | --- |
| `--chdman COMMAND` | CHDMAN executableを選択します。 |
| `--workers COUNT` | 同時に動かすCHDMAN process数を制限します。 |
| `--fail-fast` | 最初のjob failure後に新しいjobの開始を止めます。 |
| `--allow-changed` | 変更されたprimary inputをfailureではなくwarning付きで実行します。 |
| `--log-dir DIR` | run/job logのrootを選択します。 |

正確なoption構文は`chdmanpy COMMAND --help`で確認してください。

## direct workflowと確認可能なworkflow

directoryを直接変換します。

```console
chdmanpy convert ./input --output-dir ./chd --preset ps2 >results.jsonl
```

実行前にmanifestの編集可能fieldを確認・編集する場合は、計画と実行を分離します。

```console
chdmanpy plan ./input --output-dir ./chd --preset ps2 >jobs.jsonl
chdmanpy run --manifest jobs.jsonl >results.jsonl
```

CHDMANの探索やjob開始より前にmanifest全体を検証します。編集前に
[schema-v1契約](schema-v1.md)を確認してください。

## ArcShuttle workflow

chdmanpyはZIPを探索・展開しません。archiveの探索、展開、staging、cleanupは
ArcShuttleの役割です。BashやZshなど`pipefail`をsupportするshellでは、次のdirect
pipelineを使えます。

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

PowerShell pipeline直後に`$?`と`$LASTEXITCODE`の両方を保存してください。
`$pipelineSucceeded`は`Set-Content` failureを検出し、`$chdmanpyStatus`は別のnative
processを実行するまで直近のnative process statusを保持します。direct形式は簡潔ですが、
`pipefail`はsupportするshellにpipeline failureを報告するだけです。producer exitを
chdmanpyへ伝えず、downstream変換が始まらなかったことも保証しません。ArcShuttle
schema-v2 summaryにもproducer process exitは含まれません。ArcShuttle processがcleanに
終了したことまで必要な場合は、変換前に出力を保存してexitを確認します。

安全なPOSIX handoff:

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

byte-preservingなPowerShell handoffではPowerShell 7を使い、native stdout streamを
直接copyします。これにより、Windows PowerShell 5.1や古いPowerShellのtext redirection
encodingへ依存しません。

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

既定の`--on-upstream-error fail`は、finalized successでないresultまたはwarningを1件でも
含むArcShuttle stream全体を拒否します。明示的な`--on-upstream-error skip`は、検証済み
success rootだけを使い、省略した全項目をstderrへ報告します。downstream変換が成功しても
exit 1です。不正な構造、矛盾するsummary、安全でないpath、不完全なstagingは常に拒否
します。規範は[ArcShuttle取り込み契約](arcshuttle-schema-v2.ja.md)を参照してください。

## stream、log、staging、exit

stdoutはBOMなしUTF-8 JSON Lines専用です。

- `plan`は`job` recordだけを出力します。
- `run`と`convert`はjobごとに順序を保った`result`を1件ずつ出力し、最後にちょうど1件の
  `summary`を出力します。

diagnostic、選択したCHDMAN/version、run log pathはstderrへ出力します。実行eventは進捗と
してstreamせず、run logへ記録します。CHDMANのstdout/stderrはresultごとの`log_path`に
記録します。既定では最初に計画されたdestinationのparent以下の`.chdmanpy-logs` treeへ
保存します。別のrootには`--log-dir`を使います。

JSON Lines consumerはEOFまで読み、末尾の`summary`を必須としてください。途中の
`success` resultだけではinvocation完了を意味しません。result statusの意味は次のとおりです。

- `success`: 検証済みCHDをcleanにpublishしました。
- `warning`: jobは完了しましたがwarningがあります。
- `failed`: jobがfailureとなり、owned stagingを保持した場合があります。
- `skipped`: jobを意図的に実行しませんでした。
- `interrupted`: interruptにより完了前または実行中に停止しました。

各変換はprivateなsibling `.failed` staging directoryへ書き、検証済みCHDを上書きなしで
publishします。成功したowned stagingは削除します。failureまたはinterrupt時のowned
stagingは検査用に保持し、絶対pathを`staging_path`として報告します。chdmanpyはinputや
ArcShuttle output directoryを変更しません。

既存destinationにはmanifestで明示した`fail`（既定）、`skip`、deterministic `rename`の
policyを使います。どのpolicyもCHDを破壊的に上書きしません。

| exit | 意味 |
| ---: | --- |
| 0 | clean success |
| 1 | warningまたはskipを伴う完了。受理したpartial upstream runを含みます。 |
| 2 | 1件以上のCHDMAN job failure |
| 64 | usage、設定、入力、stream、manifest error。preflight failure後はjobを開始しません。 |
| 130 | interrupt。実行開始後は有効なresultとsummaryを伴う場合があります。 |

exit 1、2、130は、有効なresult recordとそれに続くsummaryを伴う場合があります。

## 設定

設定の優先順位はCLI、`CHDMANPY_*`環境変数、明示的TOML、bundled preset/defaultです。
未知keyと不正な型はerrorです。

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

bundled preset mappingは次のとおりです。

| preset | extension | CHDMAN creation argument |
| --- | --- | --- |
| `others` | `.cue` | `createcd` |
| `ps2` | `.cue` | `createcd` |
| `ps2` | `.iso` | `createdvd -c zlib` |
| `psp` | `.iso` | `createdvd -hs 2048 -c zstd` |

historical `[options]` tableは引き続き受理し、選択したpresetのextension mapping全体を
置き換えます。input/output/force argumentはchdmanpyが管理し、TOMLから指定できません。

supportする環境変数は次のとおりです。

| 変数 | 受理する値と意味 |
| --- | --- |
| `CHDMANPY_OUTPUT_DIR` | `<path>`: 空でない出力root。`--output-dir`相当です。 |
| `CHDMANPY_EXISTING` | `fail` / `skip` / `rename`。`--existing`相当です。 |
| `CHDMANPY_PRIORITY` | `-2147483648..2147483647`: 10進integer。`--priority`相当です。 |
| `CHDMANPY_PRESET` | `others` / `ps2` / `psp`。`--preset`相当です。 |
| `CHDMANPY_CHDMAN` | `<executable-name-or-path>`: 空でない単一のexecutable名またはpath。`PATH`より先に使い、shell fragmentやargumentは受理しません。 |

## historical scriptからの移行

historical `python chdmanpy.py INPUT OUTPUT --config FILE` interfaceは0.1.0の
compatibility surfaceではありません。

| historical動作 | 0.1.0での置き換え |
| --- | --- |
| `python chdmanpy.py INPUT OUTPUT --config ps2.toml` | `chdmanpy convert INPUT --output-dir OUTPUT --preset ps2` |
| custom `[options]` TOML | `--config FILE`を継続利用できます。`[options]`も引き続きsupportします。 |
| ZIP探索・展開 | ArcShuttleを別processで実行し、`--arcshuttle-results`を渡します。chdmanpyにarchive backendはありません。 |
| `--temp-dir`、`unzip_zip_files`、`_extracted`出力 | chdmanpy内の置き換えはありません。展開policyはArcShuttleが担当し、出力はnamespace付き相対pathを保持します。 |
| `[run].workers` | invocation-wide `--workers COUNT`を使います。 |
| stdout上の人間向け進捗・result | stdoutのJSON Linesを処理し、stderrのdiagnosticとresultのlog pathを参照します。 |
| `chdmanpy.py`と同じ場所の`chdman.exe` | `--chdman`、`CHDMANPY_CHDMAN`、`[runtime].chdman`、`PATH`を使います。 |
