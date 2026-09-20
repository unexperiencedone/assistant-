"""Create Word documents through Word's own automation (COM) API.

Much more reliable than clicking through Word's window: the text, headings and
bullets go straight into the document, and it works the same whatever state the
Word UI is in. Content is simple Markdown:

    # Title / heading 1      ## Heading 2      ### Heading 3
    - bullet or * bullet     blank line = new paragraph     **bold** markers are removed

    nova-cli word write --file article.md [--title "AirGated"] [--out C:\\path\\article.docx] [--hidden]
"""

from __future__ import annotations

import re
import time
from pathlib import Path

WD_STYLE = {"title": -63, "h1": -2, "h2": -3, "h3": -4, "normal": -1, "bullet": -49, "number": -50, "quote": -1, "image": -1}
WD_FORMAT_DOCX = 16  # wdFormatDocumentDefault


class OfficeError(RuntimeError):
    pass


def default_documents_dir() -> Path:
    import ctypes
    from ctypes import wintypes

    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_PERSONAL: follows OneDrive redirection
    return Path(buf.value or Path.home() / "Documents")


def parse_markdown(text: str) -> list[tuple[str, str]]:
    """-> [(style, text)], merging wrapped lines into paragraphs."""
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("normal", " ".join(paragraph)))
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if m := re.match(r"^(#{1,3})\s+(.*)", line):
            flush()
            blocks.append(({1: "h1", 2: "h2", 3: "h3"}[len(m.group(1))], m.group(2)))
        elif m := re.match(r"^[-*•]\s+(.*)", line):
            flush()
            blocks.append(("bullet", m.group(1)))
        elif m := re.match(r"^\d+[.)]\s+(.*)", line):
            flush()
            blocks.append(("number", m.group(1)))
        elif m := re.match(r"^!\[(.*?)\]\((.*?)\)", line):
            flush()
            blocks.append(("image", f"{m.group(1)}|{m.group(2)}"))
        elif m := re.match(r"^>\s*(.*)", line):
            flush()
            blocks.append(("quote", m.group(1)))
        else:
            paragraph.append(line)
    flush()
    return [(style, re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), body)) for style, body in blocks]


def write_document(content: str, title: str = "", out: Path | None = None, visible: bool = True) -> Path:
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OfficeError("pywin32 is required for Word automation") from exc

    blocks = parse_markdown(content)
    if title and not (blocks and blocks[0][0] in ("h1", "title") and blocks[0][1].strip() == title.strip()):
        blocks.insert(0, ("title", title))
    elif blocks and blocks[0][0] == "h1":
        blocks[0] = ("title", blocks[0][1])  # first heading becomes the document title
    if not blocks:
        raise OfficeError("nothing to write")

    name = re.sub(r'[<>:"/\\|?*]+', "", (title or blocks[0][1]))[:80].strip() or "Document"
    out = out or default_documents_dir() / f"{name}.docx"
    out = Path(out).expanduser().resolve()
    if out.exists():
        out = out.with_name(f"{out.stem} {time.strftime('%Y-%m-%d %H%M%S')}{out.suffix}")  # never overwrite
    out.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    try:
        word = win32com.client.Dispatch("Word.Application")
    except Exception as exc:
        raise OfficeError(f"couldn't start Word: {exc}") from exc
    word.Visible = visible
    doc = word.Documents.Add()
    selection = word.Selection
    for i, (style, text) in enumerate(blocks):
        if style == "image":
            caption, img_path = text.split("|", 1) if "|" in text else ("", text)
            p = Path(img_path.strip()).expanduser().resolve()
            if p.is_file():
                selection.InlineShapes.AddPicture(FileName=str(p), LinkToFile=False, SaveWithDocument=True)
                selection.TypeParagraph()
                if caption.strip():
                    selection.Style = doc.Styles(WD_STYLE["normal"])
                    selection.Font.Italic = True
                    selection.TypeText(caption.strip())
                    selection.Font.Italic = False
                    if i < len(blocks) - 1:
                        selection.TypeParagraph()
            continue
        elif style == "quote":
            selection.Style = doc.Styles(WD_STYLE["normal"])
            selection.Font.Italic = True
            selection.TypeText(f"“{text}”")
            selection.Font.Italic = False
        else:
            selection.Style = doc.Styles(WD_STYLE[style])
            selection.TypeText(text)
        if i < len(blocks) - 1:
            selection.TypeParagraph()
    doc.SaveAs2(str(out), FileFormat=WD_FORMAT_DOCX)
    if visible:
        doc.Activate()
        selection.HomeKey(Unit=6)  # wdStory: scroll back to the top
    else:
        doc.Close(SaveChanges=False)
        if word.Documents.Count == 0:
            word.Quit()
    return out
