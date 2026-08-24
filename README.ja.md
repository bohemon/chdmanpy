# chdmanpy

[English](README.md)

chdmanpyは、CHDMANをパイプラインから利用しやすくするコマンドライン
フロントエンドです。変換ジョブを計画し、同時実行数を制限しながら並列実行します。
標準出力には、機械可読なJSON Linesだけを出力します。アーカイブの展開は
[ArcShuttle](https://github.com/bohemon/ArcShuttle)の役割です。chdmanpy自身はZIPを
展開せず、ArcShuttleも起動しません。

## 動作要件

- Python 3.11以降が動作するWindowsまたはLinux
- 別途インストールし、`PATH`、`--chdman`、または設定で指定した`chdman`
- アーカイブを展開する場合のみArcShuttle

## インストール

固定したv0.1.0タグを、GitHubから隔離環境へ直接インストールします。

```console
pipx install "chdmanpy @ git+https://github.com/bohemon/chdmanpy.git@v0.1.0"
chdmanpy --version
```

パッケージインデックスでリリースが公開されている場合は、
`pipx install chdmanpy`も使えます。ソースのチェックアウトからインストールする場合は
`pipx install .`を使います。タグ付きソースからのインストールにはGitが必要です。
chdmanpyをインストールしても、CHDMANやArcShuttleはインストールされません。

## クイックスタート

同梱のPlayStation 2プリセットを使って、ディレクトリを直接変換します。

```console
chdmanpy convert ./input --output-dir ./chd --preset ps2 >results.jsonl
```

アーカイブを扱う場合は、ArcShuttleのschema-v2結果ストリームを明示的に接続します。

```sh
arcshuttle extract --output-dir ./extracted game.zip |
  chdmanpy convert --arcshuttle-results - --output-dir ./chd --preset ps2 \
  >results.jsonl
```

診断メッセージは標準エラー出力に表示されます。ArcShuttleプロセスの終了コードも
検証する必要がある場合は、直接パイプラインを使う前に使用方法を確認してください。

## 文書

- [使用方法と移行ガイド](https://github.com/bohemon/chdmanpy/blob/main/docs/usage.ja.md)
  ([English](https://github.com/bohemon/chdmanpy/blob/main/docs/usage.md))
- [chdmanpy JSON Lines schema v1](https://github.com/bohemon/chdmanpy/blob/main/docs/schema-v1.md)
- [ArcShuttle schema-v2の取り込み](https://github.com/bohemon/chdmanpy/blob/main/docs/arcshuttle-schema-v2.ja.md)
  ([English](https://github.com/bohemon/chdmanpy/blob/main/docs/arcshuttle-schema-v2.md))
- [テスト](https://github.com/bohemon/chdmanpy/blob/main/docs/testing.md)
- [0.1.0リリースノート](https://github.com/bohemon/chdmanpy/blob/main/docs/release-notes-0.1.0.md)
