"""Read-only Word extraction exported from the maintained DocKit reader."""
import re
import zipfile
from lxml import etree
from docx.oxml.ns import qn

def extract_paragraphs(docx_path: str) -> list[dict]:
    """提取所有段落，返回 [{style, text, level}]"""
    paragraphs = []
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        # 读取样式映射
        styles_map = {}
        if "word/styles.xml" in zf.namelist():
            styles_xml = zf.read("word/styles.xml")
            stree = etree.fromstring(styles_xml)
            for s in stree.iter(qn("w:style")):
                sid = s.get(qn("w:styleId"), "")
                name_elem = s.find(qn("w:name"))
                name = name_elem.get(qn("w:val"), sid) if name_elem is not None else sid
                styles_map[sid] = name

        tree = etree.fromstring(doc_xml)
        body = tree.find(qn("w:body"))
        if body is None:
            return paragraphs

        for para in body.iter(qn("w:p")):
            # 提取样式
            ppr = para.find(qn("w:pPr"))
            style_id = ""
            outline_level = -1
            if ppr is not None:
                ps = ppr.find(qn("w:pStyle"))
                if ps is not None:
                    style_id = ps.get(qn("w:val"), "")
                ol = ppr.find(qn("w:outlineLvl"))
                if ol is not None:
                    outline_level = int(ol.get(qn("w:val"), "-1"))

            style_name = styles_map.get(style_id, style_id)

            # 计算标题级别
            level = -1
            heading_match = re.match(r"[Hh]eading\s*(\d+)", style_name)
            if heading_match:
                level = int(heading_match.group(1))
            elif outline_level >= 0:
                level = outline_level + 1

            # 提取文本
            texts = []
            for t in para.iter(qn("w:t")):
                if t.text:
                    texts.append(t.text)
            text = "".join(texts).strip()

            if text:
                paragraphs.append(
                    {
                        "style": style_name,
                        "style_id": style_id,
                        "text": text,
                        "level": level,
                    }
                )

    return paragraphs

def paragraphs_to_markdown(paragraphs: list[dict]) -> str:
    """将段落列表转为 Markdown"""
    lines = []
    for p in paragraphs:
        style = p["style"].lower()
        text = p["text"]
        level = p["level"]

        if level >= 1:
            lines.append(f"\n{'#' * level} {text}\n")
        elif "表" in style or "图" in style or "caption" in style:
            lines.append(f"\n**[{text}]**\n")
        elif "题目" in style or "title" in style:
            lines.append(f"\n**{text}**\n")
        else:
            lines.append(f"\n{text}\n")

    return "\n".join(lines).strip() + "\n"
