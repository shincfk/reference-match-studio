"""Versioned Reference Match profiles and the DCTL compile-time bridge."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = "1.0"
ENGINE_ID = "reference-match"
ENGINE_VERSION = "0.2.0"
PROFILE_SUFFIX = ".rmatch.json"

CONTROL_SPECS: dict[str, tuple[float, float, float]] = {
    "mix": (0.0, 1.0, 0.75),
    "shadows": (0.0, 1.5, 1.0),
    "midtones": (0.0, 1.5, 1.0),
    "highlights": (0.0, 1.5, 1.0),
    "highlightProtect": (0.0, 1.0, 0.5),
    "warmToneProtect": (0.0, 1.0, 0.25),
    "hueRotateDegrees": (-30.0, 30.0, 8.0),
    "chromaScale": (0.0, 2.0, 0.85),
}

CONTROL_TO_DCTL = {
    "mix": "PROFILE_MIX",
    "shadows": "PROFILE_SHADOWS",
    "midtones": "PROFILE_MIDTONES",
    "highlights": "PROFILE_HIGHLIGHTS",
    "highlightProtect": "PROFILE_HIGHLIGHT_PROTECT",
    "warmToneProtect": "PROFILE_WARM_PROTECT",
    "hueRotateDegrees": "PROFILE_HUE_ROTATE",
    "chromaScale": "PROFILE_CHROMA",
}

STAT_TO_DCTL = {
    ("source", "mean"): ("SRC_L", "SRC_A", "SRC_B"),
    ("source", "std"): ("SRC_STD_L", "SRC_STD_A", "SRC_STD_B"),
    ("reference", "mean"): ("TGT_L", "TGT_A", "TGT_B"),
    ("reference", "std"): ("TGT_STD_L", "TGT_STD_A", "TGT_STD_B"),
}


class ProfileValidationError(ValueError):
    """A profile field is absent, incompatible, or out of range."""


def slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-").lower()
    return (result or "untitled-match")[:64]


def _require(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise ProfileValidationError(f"{path}.{key}: 缺少必填字段。")
    return mapping[key]


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError(f"{path}: 应为对象。")
    return value


def _vector(value: Any, path: str, *, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ProfileValidationError(f"{path}: 应为包含 3 个数字的数组。")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError) as error:
        raise ProfileValidationError(f"{path}: 包含非数字值。") from error
    if not np.all(np.isfinite(result)):
        raise ProfileValidationError(f"{path}: 只能包含有限数字。")
    if positive and any(item <= 0 for item in result):
        raise ProfileValidationError(f"{path}: 标准差必须大于 0。")
    return result


def normalized_controls(value: Any) -> dict[str, float]:
    source = _mapping(value, "controls")
    result: dict[str, float] = {}
    for key, (minimum, maximum, fallback) in CONTROL_SPECS.items():
        raw = source.get(key, fallback)
        try:
            number = float(raw)
        except (TypeError, ValueError) as error:
            raise ProfileValidationError(f"controls.{key}: 应为数字。") from error
        if not np.isfinite(number) or number < minimum or number > maximum:
            raise ProfileValidationError(f"controls.{key}: 应在 {minimum:g}–{maximum:g} 范围内，实际为 {raw}。")
        result[key] = number
    return result


def validate_profile(value: Any) -> dict[str, Any]:
    profile = copy.deepcopy(_mapping(value, "$"))
    schema = str(_require(profile, "schemaVersion", "$"))
    if schema.split(".", 1)[0] != SCHEMA_VERSION.split(".", 1)[0]:
        raise ProfileValidationError(f"schemaVersion: 不支持 {schema}，当前支持 {SCHEMA_VERSION}。")

    identity = _mapping(_require(profile, "profile", "$"), "profile")
    for key in ("id", "name", "type"):
        if not isinstance(_require(identity, key, "profile"), str) or not identity[key].strip():
            raise ProfileValidationError(f"profile.{key}: 应为非空文本。")
    if identity["type"] != "shotMatch":
        raise ProfileValidationError("profile.type: v0.2 仅支持 shotMatch。")
    if not isinstance(_require(identity, "createdAt", "profile"), str) or not identity["createdAt"].strip():
        raise ProfileValidationError("profile.createdAt: 应为非空的 ISO 8601 时间文本。")
    tags = _require(identity, "tags", "profile")
    if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(tag, str) for tag in tags):
        raise ProfileValidationError("profile.tags: 应为最多 12 项的文本数组。")

    engine = _mapping(_require(profile, "engine", "$"), "engine")
    if _require(engine, "id", "engine") != ENGINE_ID:
        raise ProfileValidationError(f"engine.id: 应为 {ENGINE_ID}。")
    minimum_version = str(_require(engine, "minVersion", "engine"))
    if minimum_version.split(".", 1)[0] != ENGINE_VERSION.split(".", 1)[0]:
        raise ProfileValidationError(f"engine.minVersion: {minimum_version} 与引擎 {ENGINE_VERSION} 不兼容。")

    pipeline = _mapping(_require(profile, "colorPipeline", "$"), "colorPipeline")
    encoding = _require(pipeline, "inputEncoding", "colorPipeline")
    if encoding not in {"srgb-display", "linear-rec709"}:
        raise ProfileValidationError("colorPipeline.inputEncoding: 仅支持 srgb-display 或 linear-rec709。")
    if _require(pipeline, "workingPrimaries", "colorPipeline") != "rec709-srgb":
        raise ProfileValidationError("colorPipeline.workingPrimaries: v0.2 仅支持 rec709-srgb。")
    if _require(pipeline, "transferSpace", "colorPipeline") != "oklab":
        raise ProfileValidationError("colorPipeline.transferSpace: v0.2 仅支持 oklab。")

    calibration = _mapping(_require(profile, "calibration", "$"), "calibration")
    for side in ("source", "reference"):
        stats = _mapping(_require(calibration, side, "calibration"), f"calibration.{side}")
        if not isinstance(_require(stats, "label", f"calibration.{side}"), str):
            raise ProfileValidationError(f"calibration.{side}.label: 应为文本。")
        stats["mean"] = _vector(_require(stats, "mean", f"calibration.{side}"), f"calibration.{side}.mean")
        stats["std"] = _vector(_require(stats, "std", f"calibration.{side}"), f"calibration.{side}.std", positive=True)
        stats["lumaPercentiles"] = _vector(
            _require(stats, "lumaPercentiles", f"calibration.{side}"),
            f"calibration.{side}.lumaPercentiles",
        )
        fingerprint = _require(stats, "fingerprint", f"calibration.{side}")
        if not isinstance(fingerprint, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None:
            raise ProfileValidationError(f"calibration.{side}.fingerprint: 应为 sha256 指纹。")
        for dimension in ("width", "height"):
            if dimension in stats and (not isinstance(stats[dimension], int) or stats[dimension] < 1):
                raise ProfileValidationError(f"calibration.{side}.{dimension}: 应为正整数。")

    profile["controls"] = normalized_controls(_require(profile, "controls", "$"))
    validation = _mapping(_require(profile, "validation", "$"), "validation")
    transform_hash = _require(validation, "previewTransformHash", "validation")
    if not isinstance(transform_hash, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", transform_hash) is None:
        raise ProfileValidationError("validation.previewTransformHash: 应为 sha256 哈希。")
    warnings = _require(validation, "warnings", "validation")
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ProfileValidationError("validation.warnings: 应为文本数组。")
    return profile


def profile_hash(profile: dict[str, Any]) -> str:
    payload = copy.deepcopy(profile)
    payload.pop("validation", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def image_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def build_profile(*, name: str, tags: list[str], input_encoding: str,
                  source: dict[str, Any], reference: dict[str, Any],
                  controls: dict[str, float], profile_id: str | None = None,
                  created_at: str | None = None) -> dict[str, Any]:
    display_name = name.strip() or "Untitled Match"
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "profile": {
            "id": profile_id or slug(display_name),
            "name": display_name,
            "type": "shotMatch",
            "createdAt": created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tags": [str(tag).strip() for tag in tags if str(tag).strip()][:12],
        },
        "engine": {"id": ENGINE_ID, "minVersion": ENGINE_VERSION},
        "colorPipeline": {
            "inputEncoding": input_encoding,
            "workingPrimaries": "rec709-srgb",
            "transferSpace": "oklab",
            "outputEncoding": "same-as-input",
        },
        "calibration": {"source": source, "reference": reference},
        "controls": controls,
    }
    result["validation"] = {"previewTransformHash": profile_hash(result), "warnings": []}
    return validate_profile(result)


def apply_control_overrides(profile: dict[str, Any], controls: dict[str, float]) -> dict[str, Any]:
    result = validate_profile(profile)
    result["controls"] = normalized_controls(controls)
    result["validation"] = {"previewTransformHash": profile_hash(result), "warnings": []}
    return result


def header_text(profile: dict[str, Any]) -> str:
    profile = validate_profile(profile)
    lines = [
        "// Machine generated by Reference Match Bridge. Do not edit.",
        f"// Profile: {profile['profile']['name']}",
        f"// Profile ID: {profile['profile']['id']}",
        f"// Profile hash: {profile_hash(profile)}",
        "#ifndef REFERENCE_MATCH_PROFILE_H",
        "#define REFERENCE_MATCH_PROFILE_H",
        "",
    ]
    for (side, field), names in STAT_TO_DCTL.items():
        for constant, number in zip(names, profile["calibration"][side][field]):
            lines.append(f"__CONSTANT__ float {constant} = {number:.9f}f;")
    lines.append("")
    for key, macro in CONTROL_TO_DCTL.items():
        lines.append(f"#define {macro} {profile['controls'][key]:.6f}f")
    encoding = 1 if profile["colorPipeline"]["inputEncoding"] == "srgb-display" else 0
    lines.extend([f"#define PROFILE_INPUT_ENCODING {encoding}", "", "#endif", ""])
    return "\n".join(lines)


def activate_profile(profile: dict[str, Any], *, engine_path: Path, header_path: Path,
                     active_snapshot_path: Path, resolve_lut_dir: Path | None = None) -> dict[str, Any]:
    profile = validate_profile(profile)
    if not engine_path.is_file():
        raise FileNotFoundError(f"找不到通用 DCTL 引擎：{engine_path}")
    header_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_header = header_path.with_suffix(header_path.suffix + ".tmp")
    temporary_header.write_text(header_text(profile), encoding="utf-8")
    temporary_header.replace(header_path)
    snapshot = copy.deepcopy(profile)
    snapshot["validation"]["previewTransformHash"] = profile_hash(snapshot)
    temporary_snapshot = active_snapshot_path.with_suffix(active_snapshot_path.suffix + ".tmp")
    temporary_snapshot.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_snapshot.replace(active_snapshot_path)

    installed = False
    installed_paths: list[str] = []
    if resolve_lut_dir is not None:
        resolve_lut_dir.mkdir(parents=True, exist_ok=True)
        for source in (engine_path, header_path):
            destination = resolve_lut_dir / source.name
            shutil.copy2(source, destination)
            installed_paths.append(str(destination))
        installed = True
    return {
        "profileName": profile["profile"]["name"],
        "profileHash": profile_hash(profile),
        "engineVersion": ENGINE_VERSION,
        "workspaceEngine": str(engine_path),
        "workspaceHeader": str(header_path),
        "resolveInstalled": installed,
        "installedPaths": installed_paths,
    }
