#!/usr/bin/env python3
"""Local Reference Match Studio with profile-driven DCTL activation."""
from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
EXPORTS = DATA / "exports"
PROFILE_UPLOADS = DATA / "profiles"
PROFILE_SCHEMA = PROJECT / "profiles" / "rmatch-profile.schema.json"
ENGINE_DCTL = PROJECT / "dctl" / "ReferenceMatch.dctl"
PROFILE_HEADER = PROJECT / "dctl" / "ReferenceMatchProfile.h"
ACTIVE_SNAPSHOT = PROJECT / "dctl" / "ReferenceMatchProfile.active.json"
sys.path.insert(0, str(PROJECT / "scripts"))
import generate_reference_transfer_dctl as generator  # noqa: E402
import profile_bridge as bridge  # noqa: E402

ALLOWED_IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
MAX_PROFILE_BYTES = 1024 * 1024


def safe_name(value: str) -> str:
    return (re.sub(r"[^A-Za-z0-9_-]+", "-", Path(value).stem).strip("-") or "asset")[:64]


def image_array(path: Path) -> np.ndarray:
    with Image.open(path) as source:
        return np.asarray(ImageOps.exif_transpose(source).convert("RGB"), dtype=np.float64) / 255.0


def sampled_rgb(rgb: np.ndarray, limit: int = 100_000) -> np.ndarray:
    pixels = rgb.reshape(-1, 3)
    if len(pixels) > limit:
        pixels = pixels[np.linspace(0, len(pixels) - 1, limit, dtype=int)]
    return pixels


def encode_srgb(linear: np.ndarray) -> np.ndarray:
    magnitude = np.abs(linear)
    encoded = np.where(magnitude <= .0031308, magnitude * 12.92, 1.055 * np.power(magnitude, 1 / 2.4) - .055)
    return np.copysign(encoded, linear)


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    l = lab[..., 0] + .3963377774 * lab[..., 1] + .2158037573 * lab[..., 2]
    m = lab[..., 0] - .1055613458 * lab[..., 1] - .0638541728 * lab[..., 2]
    s = lab[..., 0] - .0894841775 * lab[..., 1] - 1.2914855480 * lab[..., 2]
    return np.stack((4.0767416621*l**3 - 3.3077115913*m**3 + .2309699292*s**3,
                     -1.2684380046*l**3 + 2.6097574011*m**3 - .3413193965*s**3,
                     -.0041960863*l**3 - .7034186147*m**3 + 1.7076147010*s**3), axis=-1)


def smoothstep(start: float, end: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - start) / (end - start), 0., 1.)
    return t*t*(3 - 2*t)


def controls(payload: dict[str, Any]) -> dict[str, float]:
    limits = {
        "mix": (0, 1), "shadows": (0, 1.5), "midtones": (0, 1.5), "highlights": (0, 1.5),
        "highlight_protect": (0, 1), "warm_protect": (0, 1), "hue_rotate": (-30, 30), "chroma": (0, 2),
    }
    defaults = {
        "mix": .75, "shadows": 1., "midtones": 1., "highlights": 1.,
        "highlight_protect": .5, "warm_protect": .25, "hue_rotate": 8., "chroma": .85,
    }
    result = {}
    for key, fallback in defaults.items():
        try:
            value = float(payload.get(key, fallback))
        except (TypeError, ValueError):
            value = fallback
        result[key] = float(np.clip(value, *limits[key]))
    return result


def profile_controls(payload: dict[str, Any]) -> dict[str, float]:
    values = controls(payload)
    return {
        "mix": values["mix"], "shadows": values["shadows"], "midtones": values["midtones"],
        "highlights": values["highlights"], "highlightProtect": values["highlight_protect"],
        "warmToneProtect": values["warm_protect"], "hueRotateDegrees": values["hue_rotate"],
        "chromaScale": values["chroma"],
    }


def controls_from_profile(profile: dict[str, Any]) -> dict[str, float]:
    value = profile["controls"]
    return {
        "mix": value["mix"], "shadows": value["shadows"], "midtones": value["midtones"],
        "highlights": value["highlights"], "highlight_protect": value["highlightProtect"],
        "warm_protect": value["warmToneProtect"], "hue_rotate": value["hueRotateDegrees"],
        "chroma": value["chromaScale"],
    }


def preview(source_path: Path, source_stats: tuple[np.ndarray, np.ndarray],
            target_stats: tuple[np.ndarray, np.ndarray], values: dict[str, float],
            linear_input: bool) -> np.ndarray:
    source = image_array(source_path)
    working = source if linear_input else generator.srgb_to_linear(source)
    lab = generator.linear_rgb_to_oklab(working)
    src_mean, src_std = source_stats
    tgt_mean, tgt_std = target_stats
    mapped = ((lab - src_mean) / src_std) * tgt_std + tgt_mean
    angle = np.deg2rad(values["hue_rotate"])
    a, b = mapped[..., 1] * values["chroma"], mapped[..., 2] * values["chroma"]
    mapped[..., 1] = np.cos(angle)*a - np.sin(angle)*b
    mapped[..., 2] = np.sin(angle)*a + np.cos(angle)*b
    shadow = 1 - smoothstep(.20, .45, lab[..., 0])
    high = smoothstep(.55, .85, lab[..., 0])
    mid = np.minimum(1 - shadow, 1 - high)
    tonal = shadow*values["shadows"] + mid*values["midtones"] + high*values["highlights"]
    warm_axis = np.clip((lab[..., 1]*.85 + lab[..., 2]*.35 + .025) / .10, 0, 1)
    chroma = np.sqrt(lab[..., 1]**2 + lab[..., 2]**2)
    warm = warm_axis * smoothstep(.025, .060, chroma) * (1 - high)
    amount = np.clip(values["mix"]*tonal*(1-values["highlight_protect"]*high)*(1-values["warm_protect"]*warm), 0, 1)[..., None]
    result = working*(1-amount) + oklab_to_linear(mapped)*amount
    return np.clip(encode_srgb(result) if not linear_input else result, 0, 1)


def analysis_array(rgb: np.ndarray) -> dict[str, Any]:
    pixels = sampled_rgb(rgb)
    centroids = pixels[np.linspace(0, len(pixels)-1, 10, dtype=int)].copy()
    for _ in range(16):
        labels = ((pixels[:, None]-centroids[None])**2).sum(2).argmin(1)
        centroids = np.array([
            pixels[labels == index].mean(0) if np.any(labels == index) else centroids[index]
            for index in range(10)
        ])
    luma = pixels @ np.array([.2126, .7152, .0722])
    palette = sorted([
        "#" + "".join(f"{part:02X}" for part in np.rint(np.clip(color, 0, 1)*255).astype(np.uint8))
        for color in centroids
    ], key=lambda value: sum(int(value[index:index+2], 16)*weight for index, weight in ((1, .2126), (3, .7152), (5, .0722))))
    histogram = [np.histogram(pixels[:, index], bins=128, range=(0, 1))[0].tolist() for index in range(3)]
    cb = (-.168736*pixels[:, 0] - .331264*pixels[:, 1] + .5*pixels[:, 2]) + .5
    cr = (.5*pixels[:, 0] - .418688*pixels[:, 1] - .081312*pixels[:, 2]) + .5
    vectorscope, _, _ = np.histogram2d(cb, cr, bins=96, range=((0, 1), (0, 1)))

    height, width = rgb.shape[:2]
    scale = min(1.0, 480 / max(width, 1), 270 / max(height, 1))
    spatial_size = (max(1, round(width*scale)), max(1, round(height*scale)))
    spatial = np.asarray(Image.fromarray(np.rint(rgb*255).astype(np.uint8)).resize(spatial_size, Image.Resampling.BOX), dtype=np.float64) / 255.0
    spatial_luma = spatial @ np.array([.2126, .7152, .0722])
    x = np.broadcast_to(np.linspace(0, 1, spatial.shape[1]), spatial_luma.shape).ravel()
    waveform, _, _ = np.histogram2d(x, spatial_luma.ravel(), bins=(192, 96), range=((0, 1), (0, 1)))
    linear = generator.srgb_to_linear(pixels)
    lab = generator.linear_rgb_to_oklab(linear)
    chroma = np.sqrt(lab[:, 1]**2 + lab[:, 2]**2)
    clipping = float(np.mean(np.any((pixels <= .001) | (pixels >= .999), axis=1)) * 100)
    return {
        "palette": palette,
        "histogram": histogram,
        "vectorscope": vectorscope.astype(int).ravel().tolist(),
        "scopeSize": 96,
        "waveform": waveform.astype(int).ravel().tolist(),
        "waveformWidth": 192,
        "waveformHeight": 96,
        "luma": [round(float(np.percentile(luma, percentile)), 4) for percentile in (10, 50, 90)],
        "oklabMean": [round(float(number), 7) for number in lab.mean(0)],
        "chromaMean": round(float(chroma.mean()), 7),
        "clippingPercent": round(clipping, 4),
    }


def analysis(path: Path) -> dict[str, Any]:
    return analysis_array(image_array(path))


def calibration(path: Path, item: dict[str, Any]) -> dict[str, Any]:
    lab = generator.load_oklab(path, 1_000_000)
    rgb = sampled_rgb(image_array(path), 200_000)
    luma = rgb @ np.array([.2126, .7152, .0722])
    return {
        "label": str(item["name"]),
        "width": int(item["width"]),
        "height": int(item["height"]),
        "mean": lab.mean(0).tolist(),
        "std": np.maximum(lab.std(0), generator.EPSILON).tolist(),
        "lumaPercentiles": [float(np.percentile(luma, percentile)) for percentile in (10, 50, 90)],
        "fingerprint": bridge.image_fingerprint(path),
    }


def match_delta(result: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    result_mean = np.asarray(result["oklabMean"], dtype=float)
    reference_mean = np.asarray(reference["mean"], dtype=float)
    reference_chroma = float(np.sqrt(reference_mean[1]**2 + reference_mean[2]**2))
    reference_luma = float(reference.get("lumaPercentiles", [0, reference_mean[0], 0])[1])
    return {
        "lumaP50Ire": round((float(result["luma"][1]) - reference_luma) * 100, 2),
        "oklabMeanDelta": round(float(np.linalg.norm(result_mean-reference_mean)), 4),
        "chromaDelta": round(float(result["chromaMean"] - reference_chroma), 4),
        "clippingPercent": result["clippingPercent"],
    }


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], resolve_lut_dir: Path | None):
        super().__init__(address, Handler)
        self.items: dict[str, dict[str, Any]] = {}
        self.resolve_lut_dir = resolve_lut_dir
        self.active_profile: dict[str, Any] | None = None
        if ACTIVE_SNAPSHOT.is_file():
            try:
                self.active_profile = bridge.validate_profile(json.loads(ACTIVE_SNAPSHOT.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, bridge.ProfileValidationError):
                self.active_profile = None


class Handler(BaseHTTPRequestHandler):
    server: Server

    def json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def fail(self, text: str, status: int = 400) -> None:
        self.json({"error": text}, status)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(fmt % args)

    def body(self) -> dict[str, Any]:
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())

    def item(self, key: Any, kind: str) -> dict[str, Any]:
        result = self.server.items.get(str(key))
        if not result or result["kind"] != kind:
            raise ValueError("素材或配置已失效，请重新导入。")
        return result

    def image_item(self, key: Any) -> dict[str, Any]:
        result = self.server.items.get(str(key))
        if not result or result["kind"] not in {"reference", "source"}:
            raise ValueError("图片已失效，请重新导入。")
        return result

    def multipart(self, maximum: int | None = None) -> tuple[str, bytes]:
        length = int(self.headers.get("Content-Length", 0))
        if maximum is not None and length > maximum:
            raise ValueError("文件过大。")
        raw = self.rfile.read(length)
        match = re.search(br'filename="([^"]+)"', raw[:4096])
        if not match:
            raise ValueError("未收到文件。")
        name = match.group(1).decode("utf-8", "replace")
        boundary = self.headers.get("Content-Type", "").split("boundary=")[-1].encode()
        if not boundary or b"\r\n\r\n" not in raw:
            raise ValueError("无效的上传请求。")
        data = raw.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--" + boundary, 1)[0]
        return name, data

    def media(self, token: str) -> None:
        item = self.server.items.get(token)
        if not item or "path" not in item:
            return self.fail("找不到文件。", 404)
        file = Path(item["path"])
        raw = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            active = self.server.active_profile
            return self.json({
                "ready": True,
                "product": "Reference Match Studio",
                "engineVersion": bridge.ENGINE_VERSION,
                "resolveConfigured": self.server.resolve_lut_dir is not None,
                "activeProfile": active["profile"]["name"] if active else None,
            })
        if path == "/api/profile/schema":
            return self.json(json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8")))
        if path.startswith("/media/"):
            return self.media(path.split("/")[2])
        requested = "index.html" if path in {"/", ""} else path.lstrip("/")
        file = (ROOT / requested).resolve()
        if ROOT not in file.parents or not file.is_file():
            return self.fail("找不到文件。", 404)
        raw = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        try:
            if self.path.startswith("/api/upload/"):
                return self.upload_image(self.path.rsplit("/", 1)[-1])
            if self.path == "/api/profile/import":
                return self.import_profile()
            payload = self.body()
            if self.path == "/api/analyse":
                return self.json(analysis(Path(self.image_item(payload.get("id"))["path"])))
            if self.path == "/api/preview":
                return self.make_preview(payload)
            if self.path == "/api/profile/save":
                return self.save_profile(payload)
            if self.path == "/api/profile/activate":
                return self.activate(payload)
            self.fail("找不到接口。", 404)
        except (ValueError, OSError, json.JSONDecodeError, bridge.ProfileValidationError) as error:
            self.fail(str(error))

    def upload_image(self, kind: str) -> None:
        if kind not in {"reference", "source"}:
            raise ValueError("无效素材类型。")
        name, data = self.multipart()
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_IMAGES:
            raise ValueError("仅支持 JPG、PNG、WebP、TIFF。")
        token = uuid.uuid4().hex
        UPLOADS.mkdir(parents=True, exist_ok=True)
        file = UPLOADS / f"{token}-{safe_name(name)}{suffix}"
        file.write_bytes(data)
        with Image.open(file) as image:
            width, height = ImageOps.exif_transpose(image).size
        self.server.items[token] = {
            "path": file, "kind": kind, "name": name, "width": width, "height": height,
        }
        self.json({"id": token, "name": name, "url": f"/media/{token}", "width": width, "height": height})

    def import_profile(self) -> None:
        name, data = self.multipart(MAX_PROFILE_BYTES)
        if not name.lower().endswith(".json"):
            raise ValueError("请选择 .rmatch.json 配置文件。")
        profile = bridge.validate_profile(json.loads(data.decode("utf-8")))
        token = uuid.uuid4().hex
        PROFILE_UPLOADS.mkdir(parents=True, exist_ok=True)
        file = PROFILE_UPLOADS / f"{token}-{safe_name(name)}.rmatch.json"
        file.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.server.items[token] = {"path": file, "kind": "profile", "name": name, "profile": profile}
        self.json({"profileId": token, "filename": name, "profile": profile})

    def profile_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = payload.get("profileId")
        if profile_id:
            base = copy.deepcopy(self.item(profile_id, "profile")["profile"])
            base["controls"] = profile_controls(payload)
            if payload.get("inputEncoding"):
                base["colorPipeline"]["inputEncoding"] = "linear-rec709" if payload["inputEncoding"] == "linear" else "srgb-display"
            if str(payload.get("profileName", "")).strip():
                base["profile"]["name"] = str(payload["profileName"]).strip()
            if isinstance(payload.get("tags"), list):
                base["profile"]["tags"] = [str(tag).strip() for tag in payload["tags"] if str(tag).strip()][:12]
            base["validation"] = {"previewTransformHash": bridge.profile_hash(base), "warnings": []}
            return bridge.validate_profile(base)

        reference = self.item(payload.get("referenceId"), "reference")
        source = self.item(payload.get("sourceId"), "source")
        encoding = "linear-rec709" if payload.get("inputEncoding") == "linear" else "srgb-display"
        return bridge.build_profile(
            name=str(payload.get("profileName", "Untitled Match")),
            tags=payload.get("tags", []) if isinstance(payload.get("tags"), list) else [],
            input_encoding=encoding,
            source=calibration(Path(source["path"]), source),
            reference=calibration(Path(reference["path"]), reference),
            controls=profile_controls(payload),
        )

    def make_preview(self, payload: dict[str, Any]) -> None:
        source = self.item(payload.get("sourceId"), "source")
        profile_id = payload.get("profileId")
        if profile_id:
            profile = self.profile_from_payload(payload)
            source_cal = profile["calibration"]["source"]
            target_cal = profile["calibration"]["reference"]
            source_stats = (np.asarray(source_cal["mean"]), np.asarray(source_cal["std"]))
            target_stats = (np.asarray(target_cal["mean"]), np.asarray(target_cal["std"]))
            values = controls_from_profile(profile)
            linear_input = profile["colorPipeline"]["inputEncoding"] == "linear-rec709"
        else:
            reference = self.item(payload.get("referenceId"), "reference")
            source_data = generator.load_oklab(Path(source["path"]), 1_000_000)
            reference_data = generator.load_oklab(Path(reference["path"]), 1_000_000)
            source_stats = (source_data.mean(0), np.maximum(source_data.std(0), generator.EPSILON))
            target_stats = (reference_data.mean(0), np.maximum(reference_data.std(0), generator.EPSILON))
            values = controls(payload)
            linear_input = payload.get("inputEncoding") == "linear"
            target_cal = calibration(Path(reference["path"]), reference)

        rendered = preview(Path(source["path"]), source_stats, target_stats, values, linear_input)
        original = image_array(Path(source["path"]))
        difference = np.clip(np.abs(rendered-original)*4.0, 0, 1)
        EXPORTS.mkdir(parents=True, exist_ok=True)
        preview_token = uuid.uuid4().hex
        difference_token = uuid.uuid4().hex
        preview_file = EXPORTS / f"preview-{preview_token}.png"
        difference_file = EXPORTS / f"difference-{difference_token}.png"
        Image.fromarray(np.rint(rendered*255).astype(np.uint8)).save(preview_file)
        Image.fromarray(np.rint(difference*255).astype(np.uint8)).save(difference_file)
        self.server.items[preview_token] = {"path": preview_file, "kind": "preview", "name": preview_file.name}
        self.server.items[difference_token] = {"path": difference_file, "kind": "difference", "name": difference_file.name}
        report = analysis_array(rendered)
        self.json({
            "url": f"/media/{preview_token}",
            "differenceUrl": f"/media/{difference_token}",
            "values": values,
            "analysis": report,
            "delta": match_delta(report, target_cal),
        })

    def save_profile(self, payload: dict[str, Any]) -> None:
        profile = self.profile_from_payload(payload)
        token = uuid.uuid4().hex
        EXPORTS.mkdir(parents=True, exist_ok=True)
        filename = f"{bridge.slug(profile['profile']['name'])}{bridge.PROFILE_SUFFIX}"
        file = EXPORTS / f"{token}-{filename}"
        file.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.server.items[token] = {"path": file, "kind": "profile", "name": filename, "profile": profile}
        self.json({"profileId": token, "filename": filename, "downloadUrl": f"/media/{token}", "profile": profile})

    def activate(self, payload: dict[str, Any]) -> None:
        profile = self.profile_from_payload(payload)
        result = bridge.activate_profile(
            profile,
            engine_path=ENGINE_DCTL,
            header_path=PROFILE_HEADER,
            active_snapshot_path=ACTIVE_SNAPSHOT,
            resolve_lut_dir=self.server.resolve_lut_dir,
        )
        self.server.active_profile = profile
        result["message"] = (
            "已写入 Resolve LUT 目录；请在 Resolve 中刷新 DCTL。" if result["resolveInstalled"] else
            "配置已在工作区激活；请复制 ReferenceMatch.dctl 与 ReferenceMatchProfile.h 到 Resolve LUT 目录后刷新。"
        )
        self.json(result)


def ensure_engine() -> None:
    ENGINE_DCTL.parent.mkdir(parents=True, exist_ok=True)
    expected = generator.generic_engine_text()
    if not ENGINE_DCTL.is_file() or ENGINE_DCTL.read_text(encoding="utf-8") != expected:
        ENGINE_DCTL.write_text(expected, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--resolve-lut-dir", type=Path, default=None,
                        help="Optional DaVinci Resolve LUT directory used by Install to Resolve")
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    ensure_engine()
    print(f"Reference Match Studio: http://127.0.0.1:{args.port}")
    if args.resolve_lut_dir:
        print(f"Resolve LUT target: {args.resolve_lut_dir}")
    Server(("127.0.0.1", args.port), args.resolve_lut_dir).serve_forever()


if __name__ == "__main__":
    main()
