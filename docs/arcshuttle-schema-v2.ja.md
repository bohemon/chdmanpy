# ArcShuttle schema-v2 result の取り込み

この文書は `--arcshuttle-results` の規範である。受理するproducer形式は
ArcShuttle 0.3.2のschema-v2実行streamであり、汎用JSON取り込みでもchdmanpyの
job manifestでもない。chdmanpyはArcShuttleをimport、探索、install、設定、起動
しない。

入力はBOMなしUTF-8のJSON LinesとしてEOFまで読む。空行、JSON object内の重複key、
不正なJSON、未知field、末尾の余分なrecordはerrorである。1件以上の`result`の後に、
最後の`summary`がちょうど1件必要である。全recordはschema version 2、全resultは
operation `extract`でなければならない。

## 受理するArcShuttle 0.3.2 record

全fieldが必須であり、追加fieldは受理しない。

```json
{"schema_version":2,"record_type":"result","run_id":"20260824T064152Z-796729f7","job_id":"402e72e71dc2221c1e433f99","path":"/archives/space name.zip","status":"success","exit_code":0,"started_at":"2026-08-24T06:41:52.011Z","finished_at":"2026-08-24T06:41:52.012Z","duration_ms":1,"assigned_cpu_tokens":1,"assigned_threads":1,"output_dir":"/extracted/space name","staging_dir":null,"log_path":"/logs/run/job","warnings":[],"operation":"extract","output_path":"/extracted/space name","staging_path":null}
{"schema_version":2,"record_type":"summary","run_id":"20260824T064152Z-796729f7","total":1,"success":1,"warning":0,"failed":0,"skipped":0,"interrupted":0,"duration_ms":3}
```

`output_dir`と`output_path`、`staging_dir`と`staging_path`はそれぞれ同一でなければ
ならない。run IDは一致し、job IDは重複しない24文字の小文字16進数とする。
output pathはhostのpath規則で重複できず、summaryのtotalと5種類すべてのstatus件数は
resultと厳密に一致しなければならない。割り当てthread数は割り当てCPU token数を
超えてはならない。

status依存fieldはArcShuttle 0.3.2と厳密に一致させる。successはexit code 0かつstaging
aliasがnull、warningはexit code 1かつ一致する非null staging aliasとする。failedは
null、0、その他のfailure exitを使用できるが1は使用できない。skippedはexitとstaging
aliasがともにnullである。interruptedは未開始時のnull値と、process開始後に観測される
exit/staging値の両方を許可する。

pathはproducer hostのnativeな絶対pathである。POSIXでは`/data/extracted/game`、
WindowsではJSON上でbackslashをescapeしたdrive付きpathまたはUNC pathとなる。
異なるOSのpath構文間でresult fileを移送できるという契約ではない。

## 確定済みrootとupstream policy

planner rootにできるのは、operationが`extract`、statusが`success`、exit codeが0、
output aliasが一致、staging aliasがnullであり、symlink、junction、reparse pointを経由
しない実在directoryを示すresultだけである。warning、failed、skipped、interruptedの
resultはrootにしない。特にwarning resultの`.failed` staging directoryは部分的な
復旧用dataであり、確定済みoutputではない。
success outputはhostのpath規則において、いずれの非null staging aliasとも衝突しては
ならない。ArcShuttleの`.arcshuttle-owned` markerを含むdirectoryはretained staging
であり、resultがsuccess outputとして示していても拒否する。

既定の`--on-upstream-error fail`は、非success statusまたはresult warningが1件でも
あればrootを一切返さない。`--on-upstream-error skip`は検証済みの確定済みsuccess
rootだけを保持できるが、省略した全resultとwarningを診断し、後続変換が成功しても
chdmanpyを非success終了にする。構造、summary、alias、path、確定済みdirectoryのerrorは
常にstream全体を拒否し、skip policyで検証を弱めることはない。

## Producer exitの制約

ArcShuttleのprocess exit codeはschema-v2 resultにもsummaryにも含まれない。また
ArcShuttleは`--on-input-error skip`によってexit 1を返しながら、plan時に省略した入力を
記録しないall-success streamを出力できる。このためchdmanpyはJSON Linesだけからその
producer側状態を検出できない。通常のArcShuttle既定値では入力error時にresult streamを
出さない。producer exitがcleanであることまで証明するworkflowでは、ArcShuttleの出力と
exitを別々に保存・確認し、その後で完全な保存済みstreamをchdmanpyへ渡す必要がある。
shellの`pipefail`はupstream exit codeをchdmanpy processへ伝えない。

公開形状から採取したfixtureは
`tests/fixtures/arcshuttle-v0.3.2-success.jsonl`に置く。
