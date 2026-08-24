from __future__ import annotations

from pathlib import Path

import pytest

from chdmanpy.config import FormatConfig, load_preset, resolve_config
from chdmanpy.errors import ConfigurationError


def test_bundled_presets_are_exact_and_immutable() -> None:
    assert dict(load_preset("ps2")) == {
        ".iso": FormatConfig("createdvd", ("-c", "zlib")),
        ".cue": FormatConfig("createcd", ()),
    }
    assert dict(load_preset("psp")) == {
        ".iso": FormatConfig("createdvd", ("-hs", "2048", "-c", "zstd"))
    }
    assert dict(load_preset("others")) == {".cue": FormatConfig("createcd", ())}
    with pytest.raises(TypeError):
        load_preset("ps2")[".cue"] = FormatConfig("createcd", ())  # type: ignore[index]


def test_historical_options_table_replaces_the_preset(tmp_path: Path) -> None:
    config_file = tmp_path / "custom.toml"
    config_file.write_text(
        '[options]\n".ISO" = ["createdvd", "-c", "none"]\n',
        encoding="utf-8",
    )

    config = resolve_config(
        preset="others",
        config_path=config_file,
        output_dir="result",
        environ={},
        cwd=tmp_path,
    )

    assert dict(config.formats) == {".iso": FormatConfig("createdvd", ("-c", "none"))}
    assert config.output_dir == str(tmp_path / "result")
    assert config.existing == "fail"
    assert config.priority == 0


def test_cli_environment_file_and_default_precedence(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[planning]
output_dir = "from-file"
existing = "skip"
priority = 10
""".strip(),
        encoding="utf-8",
    )
    environment = {
        "CHDMANPY_OUTPUT_DIR": "from-environment",
        "CHDMANPY_EXISTING": "rename",
        "CHDMANPY_PRIORITY": "20",
        "CHDMANPY_PRESET": "psp",
    }

    from_environment = resolve_config(
        config_path=config_file,
        environ=environment,
        cwd=tmp_path,
    )
    assert from_environment.output_dir == str(tmp_path / "from-environment")
    assert from_environment.existing == "rename"
    assert from_environment.priority == 20
    assert from_environment.preset == "psp"

    from_cli = resolve_config(
        preset="ps2",
        config_path=config_file,
        output_dir="from-cli",
        existing="fail",
        priority=-2,
        environ=environment,
        cwd=tmp_path,
    )
    assert from_cli.output_dir == str(tmp_path / "from-cli")
    assert from_cli.existing == "fail"
    assert from_cli.priority == -2
    assert from_cli.preset == "ps2"
    assert from_cli.formats[".iso"].options == ("-c", "zlib")


@pytest.mark.parametrize(
    "contents, message",
    [
        ("unknown = true\n", "unknown"),
        ("[planning]\nworkerz = 2\n", "unknown"),
        ("[planning]\npriority = true\n", "integer"),
        ("[planning]\nexisting = 2\n", "string"),
        ('[options]\n"iso" = ["createdvd"]\n', "extension"),
        ('[options]\n".iso" = "createdvd"\n', "array"),
        ('[options]\n".iso" = ["copy"]\n', "createcd"),
        ('[options]\n".iso" = ["createdvd", "-i", "x"]\n', "managed"),
        ('[options]\n".iso" = ["createdvd", "--output=x"]\n', "managed"),
        ('[options]\n".ISO" = ["createdvd"]\n".iso" = ["createdvd"]\n', "duplicate"),
    ],
)
def test_configuration_is_strict(tmp_path: Path, contents: str, message: str) -> None:
    config_file = tmp_path / "invalid.toml"
    config_file.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        resolve_config(
            config_path=config_file,
            output_dir=tmp_path / "output",
            environ={},
        )


def test_rejects_bom_unknown_environment_and_missing_output(tmp_path: Path) -> None:
    config_file = tmp_path / "bom.toml"
    config_file.write_bytes(b"\xef\xbb\xbf[options]\n")
    with pytest.raises(ConfigurationError, match="BOM-free"):
        resolve_config(
            config_path=config_file,
            output_dir=tmp_path / "out",
            environ={},
        )
    with pytest.raises(ConfigurationError, match="unknown chdmanpy"):
        resolve_config(
            output_dir=tmp_path / "out",
            environ={"CHDMANPY_OUTPT_DIR": "typo"},
        )
    with pytest.raises(ConfigurationError, match="output directory is required"):
        resolve_config(environ={})


@pytest.mark.parametrize("preset", ["", "missing"])
def test_rejects_unknown_presets(tmp_path: Path, preset: str) -> None:
    with pytest.raises(ConfigurationError):
        resolve_config(preset=preset, output_dir=tmp_path / "out", environ={})


def test_output_directory_expands_user_before_resolving_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "user home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    config = resolve_config(output_dir="~/out", environ={}, cwd=tmp_path / "work")

    assert config.output_dir == str(home / "out")
