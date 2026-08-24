# chdmanpy

[English](README.md)

chdmanpyは、CHDMANをpipelineから利用しやすくするcommand-line frontendです。
変換jobを計画し、上限付きで並列実行しながら、機械可読なJSON Linesだけをstdoutへ
出力します。archiveの展開は
[ArcShuttle](https://github.com/bohemon/ArcShuttle)の役割です。chdmanpy自身はZIPを
展開せず、ArcShuttleも起動しません。

## 動作要件

- Python 3.11以降が動作するWindowsまたはLinux
- 別途installし、`PATH`、`--chdman`、または設定で指定した`chdman`
- archiveを展開する場合のみArcShuttle

## install

公開済みreleaseは、隔離された環境へinstallします。

```console
pipx install chdmanpy
```

source checkoutからinstallする場合は`pipx install .`を使います。chdmanpyを
installしてもCHDMANやArcShuttleはinstallされません。

## quick start

bundled PlayStation 2 presetでdirectoryを直接変換します。

```console
chdmanpy convert ./input --output-dir ./chd --preset ps2 >results.jsonl
```

archiveにはArcShuttleのschema-v2 result streamを明示的に接続します。

```sh
arcshuttle extract --output-dir ./extracted game.zip |
  chdmanpy convert --arcshuttle-results - --output-dir ./chd --preset ps2 \
  >results.jsonl
```

diagnosticはstderrに表示されます。ArcShuttle processのexitも検証する必要がある場合は、
direct pipelineを使う前にusage manualを確認してください。

## 文書

- [usageと移行guide](https://github.com/bohemon/chdmanpy/blob/main/docs/usage.ja.md)
  ([English](https://github.com/bohemon/chdmanpy/blob/main/docs/usage.md))
- [chdmanpy JSON Lines schema v1](https://github.com/bohemon/chdmanpy/blob/main/docs/schema-v1.md)
- [ArcShuttle schema-v2の取り込み](https://github.com/bohemon/chdmanpy/blob/main/docs/arcshuttle-schema-v2.ja.md)
  ([English](https://github.com/bohemon/chdmanpy/blob/main/docs/arcshuttle-schema-v2.md))
- [test](https://github.com/bohemon/chdmanpy/blob/main/docs/testing.md)
- [0.1.0 release notes](https://github.com/bohemon/chdmanpy/blob/main/docs/release-notes-0.1.0.md)
