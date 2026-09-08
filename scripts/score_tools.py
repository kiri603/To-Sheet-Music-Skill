"""Inspect native MuseScore scores; structural checks are not an audio accuracy test."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def load_score(path):
    path = Path(path)
    if path.suffix.lower() == ".mscz":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            candidates = [n for n in names if n.lower().endswith(".mscx")]
            if "META-INF/container.xml" in names:
                container = ET.fromstring(archive.read("META-INF/container.xml"))
                declared = [el.get("full-path") for el in container.iter()
                            if el.tag.split("}")[-1] == "rootfile"]
                candidates = [n for n in declared if n in candidates] or candidates
            if len(candidates) != 1:
                raise ValueError("Cannot identify one main MSCX score in archive")
            info = archive.getinfo(candidates[0])
            if info.file_size > 64 * 1024 * 1024:
                raise ValueError("Score XML exceeds the 64 MiB inspection limit")
            root = ET.fromstring(archive.read(candidates[0]))
    else:
        root = ET.parse(path).getroot()
    if root.tag != "museScore" or root.find("Score") is None:
        raise ValueError("Expected native MSCX/MSCZ; import MusicXML with MuseScore first")
    return root


def audit_score(root, expected_parts=5, max_fret_span=4):
    score = root.find("Score")
    parts, staves = score.findall("Part"), score.findall("Staff")
    report = {"errors": [], "warnings": [], "parts": [],
              "musical_accuracy_verified": False}

    def issue(code, part="", bar=None, severity="errors"):
        report[severity].append({"code": code, "part": part, "bar": bar})

    if len(parts) != expected_parts:
        issue("part_count_mismatch")
    counts = [len(s.findall("Measure")) for s in staves]
    if not counts or not all(counts) or len(set(counts)) != 1:
        issue("measure_count_mismatch")
    definitions = [(p, s) for p in parts for s in p.findall("Staff")]
    if len(definitions) != len(staves):
        issue("staff_definition_mismatch")
    for (part, definition), staff in zip(definitions, staves):
        name = part.findtext("trackName", "Unnamed")
        kind = definition.find("StaffType")
        group = kind.get("group", "pitched") if kind is not None else "pitched"
        tuning = [int(v.text) for v in part.findall("Instrument/StringData/string")]
        # MSCX stores tuning low-to-high, while Note/string is zero-based high-to-low.
        high_to_low = list(reversed(tuning))
        notes = staff.findall(".//Note")
        report["parts"].append({"name": name, "staff_id": staff.get("id"),
                                "group": group, "measures": len(staff.findall("Measure")),
                                "notes": len(notes)})
        if not notes:
            issue("silent_staff_review", name, severity="warnings")
        if group != "tablature":
            continue
        for bar, measure in enumerate(staff.findall("Measure"), 1):
            for chord in measure.findall(".//Chord"):
                used, fretted = set(), []
                for note in chord.findall("Note"):
                    try:
                        string, fret, pitch = (int(note.findtext(k, "")) for k in ("string", "fret", "pitch"))
                    except ValueError:
                        issue("invalid_tab", name, bar)
                        continue
                    max_fret = int(part.findtext("Instrument/StringData/frets", "24"))
                    if not 0 <= string < len(high_to_low) or not 0 <= fret <= max_fret:
                        issue("invalid_tab", name, bar)
                        continue
                    if string in used:
                        issue("duplicate_string", name, bar)
                    used.add(string)
                    if high_to_low[string] + fret != pitch:
                        issue("tab_pitch_mismatch", name, bar)
                    if fret:
                        fretted.append(fret)
                if fretted and max(fretted) - min(fretted) > max_fret_span:
                    issue("wide_fret_span_review", name, bar, "warnings")
    report["structural_checks_passed"] = not report["errors"]
    return report


def export_score(source, out_dir, name, musescore=None, timeout=240):
    if not name or any(c in name for c in '/\\:*?"<>|') or name in (".", ".."):
        raise ValueError("Name must be a filename stem without path separators")
    out = Path(out_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Output directory already exists: {out}")
    source = Path(source).resolve(strict=True)
    if source.suffix.lower() not in (".mscz", ".mscx", ".musicxml", ".mxl", ".xml"):
        raise ValueError("Export a notation source, not an unedited transcription MIDI")
    executable = musescore or os.environ.get("MUSESCORE_BIN")
    if not executable:
        executable = next((p for n in ("MuseScore4", "mscore", "musescore") if (p := shutil.which(n))), None)
    if not executable:
        raise ValueError("MuseScore not found; pass --musescore with its executable path")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="band-export-", dir=out.parent) as temporary:
        stage = Path(temporary)
        canonical = stage / f"{name}.mscz"
        logs = []
        for input_path, output_path in ((source, canonical), (canonical, stage / f"{name}.pdf"),
                                         (canonical, stage / f"{name}.mid")):
            command = [str(executable), "-o", str(output_path), str(input_path)]
            completed = subprocess.run(command, capture_output=True, timeout=timeout)
            logs.append({"format": output_path.suffix, "returncode": completed.returncode,
                         "output": (completed.stdout + completed.stderr).decode("utf-8", errors="replace")[-12000:]})
            if completed.returncode or not output_path.is_file() or output_path.stat().st_size < 16:
                raise RuntimeError(f"MuseScore export failed: {logs[-1]}")
        root = load_score(canonical)
        if not root.findall("Score/Part"):
            raise ValueError("Exported MSCZ contains no instruments")
        for suffix, magic in ((".pdf", b"%PDF-"), (".mid", b"MThd")):
            if not (stage / f"{name}{suffix}").read_bytes().startswith(magic):
                raise ValueError(f"Invalid exported {suffix} file")
        # This proves common provenance, not PDF readability or musical accuracy.
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir() if p.is_file()}
        report = {"source": str(source), "files_sha256": hashes, "exports": logs,
                  "musical_accuracy_verified": False}
        (stage / "export-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # The fresh directory is published only after all three exports are valid files.
        stage.rename(out)
    return {"out_dir": str(out), "files": list(hashes)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "export"])
    parser.add_argument("--score", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expected-parts", type=int, default=5)
    parser.add_argument("--max-fret-span", type=int, default=4)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--name", default="band-score")
    parser.add_argument("--musescore", type=Path)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if args.command == "export":
        if not args.out_dir:
            parser.error("export requires --out-dir")
        print(json.dumps(export_score(args.score, args.out_dir, args.name, args.musescore, args.timeout), ensure_ascii=False))
        return 0
    result = audit_score(load_score(args.score), args.expected_parts, args.max_fret_span)
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(content, encoding="utf-8")
    print(content)
    return 0 if result["structural_checks_passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, ET.ParseError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
