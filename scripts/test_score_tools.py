import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from score_tools import audit_score, load_score, export_score


def fixture(pitch=64, string=0, fret=0):
    root = ET.fromstring("<museScore version='4.0'><Score/></museScore>")
    score = root.find("Score")
    groups = [("Lead", "tablature", 6), ("Rhythm", "tablature", 6),
              ("Bass", "tablature", 4), ("Keys", "pitched", 5),
              ("Drums", "percussion", 5)]
    staff_id = 1
    for name, group, lines in groups:
        part = ET.SubElement(score, "Part")
        ET.SubElement(part, "trackName").text = name
        instrument = ET.SubElement(part, "Instrument")
        if group == "tablature":
            data = ET.SubElement(instrument, "StringData")
            tuning = [40, 45, 50, 55, 59, 64] if lines == 6 else [28, 33, 38, 43]
            for p in tuning:
                ET.SubElement(data, "string").text = str(p)
        for _ in range(2 if name == "Keys" else 1):
            definition = ET.SubElement(part, "Staff", id=str(staff_id))
            kind = ET.SubElement(definition, "StaffType", group=group)
            ET.SubElement(kind, "lines").text = str(lines)
            staff = ET.SubElement(score, "Staff", id=str(staff_id))
            voice = ET.SubElement(ET.SubElement(staff, "Measure"), "voice")
            if staff_id == 1:
                chord = ET.SubElement(voice, "Chord")
                note = ET.SubElement(chord, "Note")
                for tag, value in (("pitch", pitch), ("string", string), ("fret", fret)):
                    ET.SubElement(note, tag).text = str(value)
            else:
                ET.SubElement(voice, "Rest")
            staff_id += 1
    return root


class AuditTests(unittest.TestCase):
    def test_export_refuses_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileExistsError):
                export_score("unused.mscx", folder, "band", "unused.exe")

    def test_export_rejects_path_traversal_in_name(self):
        with self.assertRaises(ValueError):
            export_score("unused.mscx", "unused-output", "../source", "unused.exe")

    def test_valid_high_string_is_zero_in_mscx(self):
        report = audit_score(fixture())
        self.assertEqual(report["errors"], [])
        self.assertFalse(report["musical_accuracy_verified"])

    def test_wrong_fret_cannot_pass(self):
        report = audit_score(fixture(pitch=64, fret=2))
        self.assertIn("tab_pitch_mismatch", {v["code"] for v in report["errors"]})

    def test_out_of_range_string_cannot_pass(self):
        report = audit_score(fixture(string=6))
        self.assertIn("invalid_tab", {v["code"] for v in report["errors"]})

    def test_misaligned_measure_counts_cannot_pass(self):
        root = fixture()
        ET.SubElement(root.find("Score/Staff"), "Measure")
        self.assertIn("measure_count_mismatch", {v["code"] for v in audit_score(root)["errors"]})

    def test_two_notes_on_same_string_cannot_pass(self):
        root = fixture()
        chord = root.find("Score/Staff/Measure/voice/Chord")
        chord.append(ET.fromstring("<Note><pitch>65</pitch><string>0</string><fret>1</fret></Note>"))
        self.assertIn("duplicate_string", {v["code"] for v in audit_score(root)["errors"]})

    def test_compressed_score_uses_declared_rootfile(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "中文 score.mscz"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("excerpt.mscx", "<invalid/>")
                archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="main.mscx"/></rootfiles></container>')
                archive.writestr("main.mscx", ET.tostring(fixture()))
            self.assertEqual(len(load_score(path).findall("Score/Part")), 5)


if __name__ == "__main__":
    unittest.main()
