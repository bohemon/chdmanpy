# ArcShuttle schema-v2結果の取り込み

[English](arcshuttle-schema-v2.md) · [使用方法](usage.ja.md) ·
[README](../README.ja.md)

この文書は`--arcshuttle-results`の規範です。受理する生成側の形式は、
ArcShuttle 0.3.2のschema-v2実行ストリームです。汎用的なJSON取り込み形式でも、
chdmanpyのジョブマニフェストでもありません。chdmanpyはArcShuttleを
インポート、探索、インストール、設定、起動しません。

入力はBOMなしUTF-8のJSON LinesとしてEOFまで読みます。空行、JSONオブジェクト内の
重複キー、不正なJSON、未知のフィールド、末尾の余分なレコードはエラーです。
1件以上の`result`の後に、末尾の`summary`がちょうど1件必要です。すべてのレコードは
スキーマバージョン2、すべての結果は操作`extract`でなければなりません。

## 受理するArcShuttle 0.3.2レコード

すべてのフィールドが必須であり、追加フィールドは受理しません。

```json
{"schema_version":2,"record_type":"result","run_id":"20260824T064152Z-796729f7","job_id":"402e72e71dc2221c1e433f99","path":"/archives/space name.zip","status":"success","exit_code":0,"started_at":"2026-08-24T06:41:52.011Z","finished_at":"2026-08-24T06:41:52.012Z","duration_ms":1,"assigned_cpu_tokens":1,"assigned_threads":1,"output_dir":"/extracted/space name","staging_dir":null,"log_path":"/logs/run/job","warnings":[],"operation":"extract","output_path":"/extracted/space name","staging_path":null}
{"schema_version":2,"record_type":"summary","run_id":"20260824T064152Z-796729f7","total":1,"success":1,"warning":0,"failed":0,"skipped":0,"interrupted":0,"duration_ms":3}
```

`output_dir`と`output_path`、`staging_dir`と`staging_path`は、それぞれ同一でなければ
なりません。実行IDは一致し、ジョブIDは重複しない24文字の小文字16進数とします。
出力パスは実行元OSのパス規則で重複できません。`summary`の`total`と5種類すべての
状態件数は、`result`と厳密に一致する必要があります。割り当てスレッド数は、割り当て
CPUトークン数を超えてはなりません。

状態に依存するフィールドは、ArcShuttle 0.3.2と厳密に一致させます。`success`は
終了コード0で、ステージング先を表す2フィールドがともに`null`です。`warning`は
終了コード1で、ステージング先を表す2フィールドが互いに一致する非`null`値です。
`failed`の終了コードには`null`、0、その他の失敗コードを使用できますが、1は
使用できません。`skipped`は終了コードとステージング先がともに`null`です。
`interrupted`は、未開始時の`null`値と、プロセス開始後に観測される終了コードおよび
ステージング先の両方を許可します。

パスは生成側ホストのネイティブな絶対パスです。POSIXでは`/data/extracted/game`、
WindowsではJSON上でバックスラッシュをエスケープしたドライブ付きパスまたは
UNCパスになります。異なるOSのパス構文間で結果ファイルを移送できるという契約では
ありません。

## 確定済みルートと上流エラーの処理方針

計画時のルートにできるのは、操作が`extract`、状態が`success`、終了コードが0、
出力先を表す2フィールドが一致し、ステージング先を表す2フィールドが`null`であり、
シンボリックリンク、ジャンクション、再解析ポイントを経由しない実在ディレクトリを
示す結果だけです。`warning`、`failed`、`skipped`、`interrupted`の結果はルートに
しません。特に`warning`結果の`.failed`
ステージングディレクトリは部分的な復旧用データであり、確定済み出力ではありません。

正常終了した出力は、実行元OSのパス規則において、いずれの非`null`ステージング先とも
衝突してはなりません。ArcShuttleの`.arcshuttle-owned`マーカーを含むディレクトリは
保持されたステージングであるため、結果が正常終了した出力として示していても拒否します。

既定の`--on-upstream-error fail`は、`success`以外の状態または結果の警告が1件でも
あれば、ルートを一切返しません。`--on-upstream-error skip`は、検証済みの確定済み
`success`ルートだけを保持できますが、省略したすべての結果と警告を診断し、後続変換が
成功してもchdmanpyを正常終了にはしません。構造、`summary`、対応する2フィールド、
パス、確定済みディレクトリのエラーは常にストリーム全体を拒否し、`skip`方針によって
検証を弱めることはありません。

## 生成側の終了コードに関する制約

ArcShuttleプロセスの終了コードは、schema-v2の`result`にも`summary`にも含まれません。
またArcShuttleは、`--on-input-error skip`によって終了コード1を返しながら、計画時に
省略した入力を記録しない、すべて`success`のストリームを出力できます。このため、
chdmanpyはJSON Linesだけから生成側の状態を検出できません。通常のArcShuttle既定値では、
入力エラー時に結果ストリームを出しません。生成側の正常終了まで証明するワークフローでは、
ArcShuttleの出力と終了コードを別々に保存・確認し、その後で完全な保存済みストリームを
chdmanpyへ渡す必要があります。シェルの`pipefail`は、上流の終了コードをchdmanpy
プロセスへ伝えません。

公開形式から採取したテストデータは、
`tests/fixtures/arcshuttle-v0.3.2-success.jsonl`に置いています。
