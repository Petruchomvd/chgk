"""Export a transcript JSON into a Word-compatible .docx file.

The document contains two sections:
1. Formatted transcript with time ranges.
2. A no-time version split into shorter dialogue-like replicas.
"""

from __future__ import annotations

import argparse
import html
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


def format_hms(seconds: float) -> str:
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def xml_escape(text: str) -> str:
    filtered = "".join(ch for ch in text if ch == "\t" or ord(ch) >= 32)
    return html.escape(filtered, quote=False)


def paragraph_xml(
    text: str,
    *,
    bold: bool = False,
    size: int | None = None,
    spacing_after: int | None = None,
    page_break_before: bool = False,
) -> str:
    ppr = []
    if spacing_after is not None:
        ppr.append(f'<w:spacing w:after="{spacing_after}"/>')
    ppr_xml = f"<w:pPr>{''.join(ppr)}</w:pPr>" if ppr else ""

    rpr = []
    if bold:
        rpr.append("<w:b/>")
        rpr.append("<w:bCs/>")
    if size is not None:
        rpr.append(f'<w:sz w:val="{size}"/>')
        rpr.append(f'<w:szCs w:val="{size}"/>')
    rpr_xml = f"<w:rPr>{''.join(rpr)}</w:rPr>" if rpr else ""

    parts = [ppr_xml]
    if page_break_before:
        parts.append('<w:r><w:br w:type="page"/></w:r>')
    parts.append(
        "<w:r>"
        f"{rpr_xml}"
        f'<w:t xml:space="preserve">{xml_escape(text)}</w:t>'
        "</w:r>"
    )
    return f"<w:p>{''.join(parts)}</w:p>"


def iter_time_paragraphs(segments: list[dict]) -> Iterable[tuple[float, float, str]]:
    current_text: list[str] = []
    start = None
    end = None
    char_count = 0

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        if start is None:
            start = seg["start"]
            end = seg["end"]
            current_text = [text]
            char_count = len(text)
            continue

        gap = seg["start"] - end
        duration = end - start
        should_break = gap > 1.2 or char_count >= 1200 or duration >= 75

        if should_break:
            yield start, end, " ".join(current_text)
            start = seg["start"]
            current_text = [text]
            char_count = len(text)
        else:
            current_text.append(text)
            char_count += len(text) + 1
        end = seg["end"]

    if current_text and start is not None and end is not None:
        yield start, end, " ".join(current_text)


def iter_replicas(segments: list[dict]) -> Iterable[str]:
    reply_starts = (
        "да",
        "нет",
        "угу",
        "хорошо",
        "конечно",
        "может быть",
        "смотрите",
        "вообще",
        "ну да",
        "ну нет",
    )

    current_text: list[str] = []
    prev_end = None
    char_count = 0

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        if not current_text:
            current_text = [text]
            prev_end = seg["end"]
            char_count = len(text)
            continue

        gap = seg["start"] - prev_end
        prev_text = current_text[-1].rstrip()
        lowered = text.lower().lstrip(" \t\n\"'“”«»([{")

        should_break = False
        if gap > 0.9:
            should_break = True
        elif prev_text.endswith("?"):
            should_break = True
        elif char_count >= 450:
            should_break = True
        elif char_count >= 180 and lowered.startswith(reply_starts):
            should_break = True

        if should_break:
            yield " ".join(current_text)
            current_text = [text]
            char_count = len(text)
        else:
            current_text.append(text)
            char_count += len(text) + 1
        prev_end = seg["end"]

    if current_text:
        yield " ".join(current_text)


def build_document_xml(title: str, time_blocks: list[tuple[float, float, str]], replicas: list[str]) -> str:
    paragraphs = [
        paragraph_xml(title, bold=True, size=32, spacing_after=240),
        paragraph_xml("Версия 1. С таймкодами", bold=True, size=28, spacing_after=200),
    ]

    for start, end, text in time_blocks:
        paragraphs.append(
            paragraph_xml(
                f"[{format_hms(start)} - {format_hms(end)}]",
                bold=True,
                spacing_after=80,
            )
        )
        paragraphs.append(paragraph_xml(text, spacing_after=180))

    paragraphs.append(
        paragraph_xml(
            "Версия 2. Без времени, разбивка на реплики",
            bold=True,
            size=28,
            spacing_after=200,
            page_break_before=True,
        )
    )

    for replica in replicas:
        paragraphs.append(paragraph_xml(replica, spacing_after=120))

    sect_pr = (
        "<w:sectPr>"
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
        'w:header="708" w:footer="708" w:gutter="0"/>'
        "</w:sectPr>"
    )

    body = "".join(paragraphs) + sect_pr
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
        'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
        'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'mc:Ignorable="w14 wp14">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )


def write_docx(out_path: Path, document_xml: str, title: str) -> None:
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""
    package_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{xml_escape(title)}</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>
"""
    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>
"""

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", package_rels)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/app.xml", app_xml)
        zf.writestr("word/document.xml", document_xml)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript_json", help="Path to transcript JSON with segments")
    parser.add_argument("--output-docx", required=True, help="Output .docx file")
    parser.add_argument("--output-no-time", help="Optional plain-text file for the no-time replica version")
    args = parser.parse_args()

    transcript_path = Path(args.transcript_json).expanduser().resolve()
    out_docx = Path(args.output_docx).expanduser().resolve()

    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    segments = data["segments"]
    time_blocks = list(iter_time_paragraphs(segments))
    replicas = list(iter_replicas(segments))
    title = f"Транскрипт: {Path(data.get('audio_path', transcript_path.stem)).name}"

    document_xml = build_document_xml(title, time_blocks, replicas)
    write_docx(out_docx, document_xml, title)

    if args.output_no_time:
        out_no_time = Path(args.output_no_time).expanduser().resolve()
        out_no_time.write_text("\n\n".join(replicas).rstrip() + "\n", encoding="utf-8")

    print(out_docx)
    print(f"time_blocks={len(time_blocks)}")
    print(f"replicas={len(replicas)}")


if __name__ == "__main__":
    main()
