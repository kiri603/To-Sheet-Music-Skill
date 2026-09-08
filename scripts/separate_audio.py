"""Free local, time-aligned Demucs analysis stems for band transcription."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError

import numpy as np


def prepare_mix(audio):
    if audio.ndim != 2 or audio.shape[1] != 2 or len(audio) < 2:
        raise ValueError("Expected nonempty stereo audio")
    if not np.isfinite(audio).all():
        raise ValueError("Input contains nonfinite samples")
    ref = audio.mean(axis=1)
    mean = float(ref.mean())
    scale = float(ref.std())
    if scale < 1e-8:  # Do not mistake antiphase stereo for silence.
        scale = float(audio.std())
    if scale < 1e-8:
        raise ValueError("Input is silent or constant")
    return np.ascontiguousarray((audio.T - mean) / scale), mean, scale


def diagnose_stems(audio, stems):
    if not stems:
        raise ValueError("No stems were generated")
    stats = {}
    reconstruction = np.zeros_like(audio)
    for name, signal in stems.items():
        if signal.shape != audio.shape:
            raise ValueError(f"Stem shape mismatch: {name}: {signal.shape} != {audio.shape}")
        if not np.isfinite(signal).all():
            raise ValueError(f"Nonfinite samples in {name}")
        reconstruction += signal
        stats[name] = {"frames": len(signal), "channels": signal.shape[1],
                       "rms": float(np.sqrt(np.mean(signal.astype(np.float64) ** 2))),
                       "peak": float(np.max(np.abs(signal))),
                       "above_full_scale_samples": int(np.count_nonzero(np.abs(signal) > 1.0))}
    error = float(np.sqrt(np.mean((audio.astype(np.float64) - reconstruction) ** 2)))
    base = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    return {"stems": stats, "reconstruction_error_db": float(20 * np.log10(max(error, 1e-12) / max(base, 1e-12))),
            "sample_timeline_verified": True, "separation_quality_verified": False,
            "note": "Equal length and reconstruction energy do not prove instrument isolation or transcription accuracy."}


def select_roles(results):
    anchor = results.get("htdemucs_ft", results.get("htdemucs_6s", {})).get("stems", {})
    six = results.get("htdemucs_6s", {}).get("stems", {})
    roles = {name: anchor[name] for name in ("vocals", "bass", "drums", "other") if name in anchor}
    roles.update({role: six[name] for role, name in (("guitar_candidate", "guitar"),
                  ("keyboard_candidate", "piano"), ("other_candidate", "other")) if name in six})
    return roles


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_model_with_retry(loader, name, pause=2):
    for attempt in range(3):
        try:
            return loader(name)
        except (URLError, TimeoutError, ConnectionError) as exc:
            if isinstance(exc, HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            if attempt == 2:
                raise
            print(f"Model download interrupted; retry {attempt + 1}/2: {exc}", flush=True)
            time.sleep(pause)


def run(args):
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    source = args.input.resolve(strict=True)
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = out / "manifest.json"
    models = ["htdemucs_ft", "htdemucs_6s"] if args.profile == "high" else ["htdemucs_6s"]
    report = {"status": "running", "source": str(source), "input_sha256": file_hash(source),
              "source_offset_seconds": args.start, "requested_duration": args.duration,
              "profile": args.profile, "requested_models": models, "models": {},
              "seed": args.seed, "shifts": args.shifts, "overlap": args.overlap,
              "segment_seconds": args.segment, "demucs_version": importlib.metadata.version("demucs"),
              "torch_version": torch.__version__, "separation_quality_verified": False}
    write_manifest(manifest, report)
    try:
        normalized = out / "mixture.wav"
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source)]
        if args.start:
            command += ["-ss", str(args.start)]
        if args.duration is not None:
            command += ["-t", str(args.duration)]
        command += ["-map", "0:a:0", "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", str(normalized)]
        subprocess.run(command, check=True, timeout=600)
        audio, sample_rate = sf.read(normalized, dtype="float32", always_2d=True)
        prepared, mean, scale = prepare_mix(audio)
        mix = torch.from_numpy(prepared)
        report.update({"sample_rate": sample_rate, "frames": len(audio),
                       "seconds": len(audio) / sample_rate, "normalized_sha256": file_hash(normalized)})
        torch.set_num_threads(args.threads)
        device = args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
        for name in models:
            print(f"Separating {name} on {device}...", flush=True)
            started = time.monotonic()
            model = load_model_with_retry(get_model, name).eval()
            if model.samplerate != sample_rate or model.audio_channels != 2:
                raise ValueError("Model sample rate/channel count differs from normalized input")
            torch.manual_seed(args.seed)
            actual_device, retried = device, False
            with torch.inference_mode():
                try:
                    separated = apply_model(model, mix[None], device=device, shifts=args.shifts,
                                            overlap=args.overlap, segment=args.segment, progress=True)[0]
                except RuntimeError as exc:
                    if device != "cuda" or "out of memory" not in str(exc).lower():
                        raise
                    print("CUDA out of memory; retrying this model once on CPU.", flush=True)
                    model.cpu()
                    torch.cuda.empty_cache()
                    actual_device, retried = "cpu", True
                    torch.manual_seed(args.seed)
                    separated = apply_model(model, mix[None], device="cpu", shifts=args.shifts,
                                            overlap=args.overlap, segment=min(args.segment, 4), progress=True)[0]
            separated = separated * scale + mean
            stems = {name_: separated[i].cpu().numpy().T for i, name_ in enumerate(model.sources)}
            diagnostics = diagnose_stems(audio, stems)
            model_dir = out / name
            model_dir.mkdir()
            files = {}
            for name_, signal in stems.items():
                if not re.fullmatch(r"[a-z0-9_-]+", name_):
                    raise ValueError("Invalid source name supplied by model")
                path = model_dir / f"{name_}.wav"
                # Floating-point WAV preserves peaks and relative gains; never normalize each stem.
                sf.write(path, signal, sample_rate, subtype="FLOAT")
                check = sf.info(path)
                if check.frames != len(audio) or check.samplerate != sample_rate or check.channels != 2:
                    raise ValueError(f"Written stem failed alignment check: {name_}")
                files[name_] = path.relative_to(out).as_posix()
            signatures = getattr(model, "models", [model])
            report["models"][name] = {"stems": files, "device": actual_device, "cpu_retry": retried,
                                      "elapsed_seconds": time.monotonic() - started,
                                      "model_class": type(model).__name__, "submodels": len(signatures),
                                      "diagnostics": diagnostics}
            report["roles"] = select_roles(report["models"])
            write_manifest(manifest, report)
            del model, stems, separated
        report["status"] = "complete"
        report["role_note"] = "Roles combine alternative analyses, not disjoint stems: never sum all role files. Guitar roles require phrase/harmony analysis."
        write_manifest(manifest, report)
        print(str(manifest), flush=True)
    except Exception as exc:
        report["status"], report["error"] = "failed", str(exc)
        write_manifest(manifest, report)
        raise


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path, help="New directory; existing directories are never overwritten")
    parser.add_argument("--profile", choices=["high", "balanced"], default="high")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--model-cache", type=Path, help="Optional TORCH_HOME directory; reuse downloaded weights")
    parser.add_argument("--start", type=float, default=0, help="Source offset for a diagnostic excerpt only")
    parser.add_argument("--duration", type=float, help="Diagnostic excerpt length; omitted means full remaining input")
    parser.add_argument("--segment", type=float, default=7)
    parser.add_argument("--overlap", type=float, default=0.5)
    parser.add_argument("--shifts", type=int, default=1)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.start < 0 or (args.duration is not None and args.duration <= 0):
        parser.error("Start must be nonnegative and duration positive")
    if not 0 < args.segment <= 7.8 or not 0 <= args.overlap < 1 or args.shifts < 0 or args.threads < 1:
        parser.error("Invalid segment, overlap, shifts or thread count")
    if args.model_cache:
        os.environ["TORCH_HOME"] = str(args.model_cache.resolve())
    run(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Separation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
