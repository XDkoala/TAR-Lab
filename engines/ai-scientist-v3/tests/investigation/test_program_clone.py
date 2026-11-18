"""Program spec loading, program directory cloning, and the packaged search runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.investigation.program_clone import main
from src.investigation.program_spec import (
    ProgramSpecError,
    default_program_spec,
    entrypoint_relpath_from_spec,
    load_program_spec,
    render_default_program_spec_toml,
    resolve_entrypoint,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _spec_toml(
    *,
    schema_version: int = 1,
    code_root: str = "src",
    entrypoint: str = "program.py",
    exclude: list[str] | None = None,
    include: list[str] | None = None,
    tables: bool = True,
) -> str:
    text = (
        f"schema_version = {schema_version}\ncode_root = \"{code_root}\"\n\n"
        f"[entrypoint]\npath = \"{entrypoint}\"\nlanguage = \"python\"\ntype = \"script\"\n"
    )
    if tables:
        text += (
            f"\n[copy]\nexclude = {json.dumps(exclude or [])}\n"
            f"include = {json.dumps(include or [])}\n\n"
            '[integration]\nkind = "generic"\nnotes = ""\n'
        )
    return text


def _write_program_spec(program_dir: Path, toml_text: str | None = None) -> Path:
    program_dir.mkdir(parents=True, exist_ok=True)
    (program_dir / "program_spec.toml").write_text(toml_text or _spec_toml(), encoding="utf-8")
    return program_dir


def _write_program(program_dir: Path, toml_text: str | None = None) -> Path:
    _write_program_spec(program_dir, toml_text)
    (program_dir / "src").mkdir(parents=True, exist_ok=True)
    (program_dir / "src" / "program.py").write_text("print('ok')\n", encoding="utf-8")
    return program_dir


def _symlink(target: str | Path, link: Path, *, target_is_directory: bool = False) -> None:
    try:
        os.symlink(str(target), link, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks not supported: {exc}")


def _write_ready_artifacts(
    root: Path,
    program_dir: Path,
    *,
    manifest_program_id: str | None = None,
    metrics: str = '{"combined_score": 1.0}',
    correct: bool = True,
    rubric_passed: bool = False,
) -> None:
    (program_dir / "metrics.json").write_text(metrics, encoding="utf-8")
    (program_dir / "correct.json").write_text(json.dumps({"correct": correct}), encoding="utf-8")
    (program_dir / "evaluation_manifest.json").write_text(
        json.dumps({
            "producer": "isolated_evaluator",
            "schema_version": 1,
            "program_id": manifest_program_id or program_dir.name,
            "output_dir": f"../.evaluation_outputs/{program_dir.name}/attempt_x",
            "returncode": 0,
            "published_summaries": ["metrics.json", "correct.json"],
        }),
        encoding="utf-8",
    )
    rubric = json.dumps({
        "attempt_id": "attempt_1",
        "idea_id": "idea_1",
        "overall_pass": correct and rubric_passed,
        "rubrics": [{"id": "score", "pass": rubric_passed, "evidence": "checked"}],
    })
    (program_dir / "rubric_status.json").write_text(rubric, encoding="utf-8")
    (root / "rubric_status.json").write_text(rubric, encoding="utf-8")


def _clone(src_dir: Path, dst_dir: Path) -> None:
    main(["--from", str(src_dir), "--to", str(dst_dir)])


# ---------------------------------------------------------------------------
# program_spec
# ---------------------------------------------------------------------------


def test_load_program_spec_round_trips_default_and_parses_valid_minimal(tmp_path: Path) -> None:
    # the rendered default spec round-trips
    program_dir = _write_program_spec(tmp_path / "default", render_default_program_spec_toml())
    assert load_program_spec(program_dir, required=True) == default_program_spec()

    # a valid minimal spec parses and resolves its entrypoint
    program_dir = _write_program(tmp_path / "program_0")
    spec = load_program_spec(program_dir, required=True)
    assert spec is not None
    assert spec.schema_version == 1
    assert spec.code_root == "src"
    assert (spec.entrypoint.path, spec.entrypoint.language, spec.entrypoint.type) == (
        "program.py", "python", "script"
    )
    resolved = resolve_entrypoint(program_dir, spec)
    assert resolved.is_file()
    assert str(resolved).endswith(os.path.join("src", "program.py"))
    assert entrypoint_relpath_from_spec(program_dir) == "src/program.py"


def test_program_spec_rejects_invalid_specs_and_symlink_escapes(tmp_path: Path) -> None:
    # invalid specs are rejected at load time
    for name, toml_text in [
        ("wrong_schema_version", _spec_toml(schema_version=2)),
        ("code_root_not_src", _spec_toml(code_root="code")),
        ("missing_copy_and_integration_tables", _spec_toml(tables=False)),
        ("dotdot_entrypoint_escape", _spec_toml(entrypoint="../evil.py")),
    ]:
        program_dir = _write_program_spec(tmp_path / name, toml_text)
        with pytest.raises(ProgramSpecError):
            load_program_spec(program_dir, required=True)

    # symlinked code_root or entrypoint escapes are rejected at resolution time
    for escape in ("code_root", "entrypoint"):
        root = tmp_path / f"escape-{escape}"
        root.mkdir()
        program_dir = root / "program_0"
        if escape == "code_root":
            outside_dir = root / "outside_src"
            outside_dir.mkdir()
            (outside_dir / "program.py").write_text("print('outside')\n", encoding="utf-8")
            _write_program_spec(program_dir)
            _symlink(outside_dir, program_dir / "src")
        else:
            (root / "outside.py").write_text("print('outside')\n", encoding="utf-8")
            _write_program_spec(program_dir, _spec_toml(entrypoint="evil"))
            (program_dir / "src").mkdir()
            _symlink("../../outside.py", program_dir / "src" / "evil")
        spec = load_program_spec(program_dir, required=True)
        assert spec is not None
        with pytest.raises(ProgramSpecError):
            resolve_entrypoint(program_dir, spec)


# ---------------------------------------------------------------------------
# program_clone CLI readiness checks
# ---------------------------------------------------------------------------


def test_cli_clones_ready_sources_and_binds_evaluation_manifest(tmp_path: Path) -> None:
    # repair after an incorrect evaluation is allowed
    root = tmp_path / "repair"
    src_dir = _write_program_spec(root / "program_1")
    _write_ready_artifacts(root, src_dir, correct=False, rubric_passed=True)
    _clone(src_dir, root / "program_2")
    assert (root / "program_2" / "program_spec.toml").is_file()

    # an arbitrarily named pipeline seed is allowed
    root = tmp_path / "seed"
    src_dir = _write_program_spec(root / "selected_winner")
    dst_dir = root / "attempt" / "program_0"
    _clone(src_dir, dst_dir)
    assert (dst_dir / "program_spec.toml").is_file()

    # the evaluation manifest is bound to the source candidate
    root = tmp_path / "manifest"
    src_dir = _write_program_spec(root / "program_1")
    dst_dir = root / "program_2"
    _write_ready_artifacts(root, src_dir, manifest_program_id="program_0")
    with pytest.raises(ValueError, match="program_id.*program_1"):
        _clone(src_dir, dst_dir)
    assert not dst_dir.exists()
    manifest_path = src_dir / "evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["program_id"] = "program_1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _clone(src_dir, dst_dir)
    assert (dst_dir / "program_spec.toml").is_file()
    assert not (dst_dir / "metrics.json").exists()
    assert not (dst_dir / "rubric_status.json").exists()


def test_cli_rejects_unready_cross_attempt_malformed_and_untrusted_sources(tmp_path: Path) -> None:
    # an unevaluated non-seed source is rejected
    root = tmp_path / "unevaluated"
    src_dir = _write_program_spec(root / "program_1")
    with pytest.raises(ValueError, match="not ready.*metrics.json"):
        _clone(src_dir, root / "program_2")
    assert not (root / "program_2").exists()

    # a cross-attempt candidate clone is rejected
    root = tmp_path / "cross"
    src_dir = _write_program_spec(root / "attempt_a" / "program_1")
    dst_dir = root / "attempt_b" / "program_2"
    _write_ready_artifacts(src_dir.parent, src_dir)
    with pytest.raises(ValueError, match="same experiment directory"):
        _clone(src_dir, dst_dir)
    assert not dst_dir.exists()

    # malformed summary artifacts are rejected
    root = tmp_path / "malformed"
    src_dir = _write_program_spec(root / "program_1")
    _write_ready_artifacts(root, src_dir, metrics="{}")
    with pytest.raises(ValueError, match="combined_score"):
        _clone(src_dir, root / "program_2")
    assert not (root / "program_2").exists()

    # untrusted artifact shapes are rejected
    for filename, payload, error in [
        ("correct.json", '{"correct": "yes"}', "correct.*boolean"),
        (
            "evaluation_manifest.json",
            '{"producer": "unknown", "program_id": "program_1"}',
            "unsupported producer",
        ),
        ("rubric_status.json", "{}", "attempt_id"),
    ]:
        root = tmp_path / f"shape-{filename}"
        src_dir = _write_program_spec(root / "program_1")
        _write_ready_artifacts(root, src_dir)
        (src_dir / filename).write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError, match=error):
            _clone(src_dir, root / "program_2")
        assert not (root / "program_2").exists(), filename

    # non-regular and oversized artifacts are rejected
    for boundary in ("symlink", "oversized"):
        root = tmp_path / f"boundary-{boundary}"
        src_dir = _write_program_spec(root / "program_1")
        _write_ready_artifacts(root, src_dir)
        metrics = src_dir / "metrics.json"
        if boundary == "symlink":
            external = root / "external_metrics.json"
            external.write_text('{"combined_score": 1.0}', encoding="utf-8")
            metrics.unlink()
            _symlink(external, metrics)
            error = "regular file"
        else:
            metrics.write_text("x" * (16 * 1024 * 1024 + 1), encoding="utf-8")
            error = "too large"
        with pytest.raises(ValueError, match=error):
            _clone(src_dir, root / "program_2")
        assert not (root / "program_2").exists(), boundary

    # mismatched or already-passing rubric pointers are rejected
    for boundary in ("mismatch", "already_passed", "inconsistent_pass"):
        root = tmp_path / f"rubric-{boundary}"
        src_dir = _write_program_spec(root / "program_1")
        _write_ready_artifacts(root, src_dir)
        rubric_path = root / "rubric_status.json"
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        if boundary == "mismatch":
            rubric["attempt_id"] = "another_attempt"
            error = "does not match"
        elif boundary == "already_passed":
            rubric["overall_pass"] = True
            rubric["rubrics"][0]["pass"] = True
            (src_dir / "rubric_status.json").write_text(json.dumps(rubric), encoding="utf-8")
            error = "already passes"
        else:
            rubric["rubrics"][0]["pass"] = True
            (src_dir / "rubric_status.json").write_text(json.dumps(rubric), encoding="utf-8")
            error = "inconsistent"
        rubric_path.write_text(json.dumps(rubric), encoding="utf-8")
        with pytest.raises(ValueError, match=error):
            _clone(src_dir, root / "program_2")
        assert not (root / "program_2").exists(), boundary


# ---------------------------------------------------------------------------
# clone_program_dir copy semantics
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Packaged search runtime
# ---------------------------------------------------------------------------


