"""Readable GitHub check summary without write permissions on pull requests."""

import os
from pathlib import Path
import xml.etree.ElementTree as ET

lines = ["## System regression results", "", "| Image / feature | Result |", "| --- | --- |"]
files = sorted((Path(__file__).parent / "artifacts").glob("*.xml"))
for file in files:
    for case in ET.parse(file).getroot().iter("testcase"):
        failed = case.find("failure") is not None or case.find("error") is not None
        result = "FAILED" if failed else "SKIPPED" if case.find("skipped") is not None else "Passed"
        name = case.attrib["name"].replace("|", "\\|")
        lines.append(f"| {name} | {result} |")
if not files:
    lines.append("| Build / setup | Failed before a report was produced; see job logs. |")
output = "\n".join(lines) + "\n"
print(output)
if os.getenv("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(output)
