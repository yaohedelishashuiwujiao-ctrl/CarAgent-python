#!/usr/bin/env python3
"""Render the Markdown resume as a compact, single-page A4 PDF."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

import markdown
from bs4 import BeautifulSoup
from weasyprint import HTML


CSS = r"""
@page {
  size: A4;
  margin: 10mm 12mm 9mm;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
}

body {
  color: #263442;
  font-family: "Noto Sans CJK SC", "Droid Sans Fallback", sans-serif;
  font-size: 9.35pt;
  font-weight: 400;
  line-height: 1.32;
  text-rendering: optimizeLegibility;
}

.resume-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin: 0 0 3.2mm;
  padding: 0 0 2.5mm;
  border-bottom: 1.2pt solid #1d668d;
}

.resume-header h1 {
  margin: 0;
  color: #153b55;
  font-size: 22pt;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 1pt;
}

.resume-header p {
  margin: 0 0 .2mm;
  color: #51606e;
  font-size: 8.6pt;
  white-space: nowrap;
}

.resume-header strong {
  color: #263f52;
}

h2 {
  margin: 2.15mm 0 1.05mm;
  padding: 0 0 .65mm;
  border-bottom: .75pt solid #afc3cf;
  color: #15577b;
  font-size: 11.4pt;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: .25pt;
}

h3 {
  margin: 1.5mm 0 .7mm;
  color: #173d57;
  font-size: 10pt;
  font-weight: 700;
  line-height: 1.2;
}

p {
  margin: .55mm 0;
  orphans: 2;
  widows: 2;
}

ul {
  margin: .45mm 0 .8mm;
  padding-left: 4mm;
}

li {
  margin: .45mm 0;
  padding-left: .25mm;
}

strong {
  color: #193e57;
  font-weight: 700;
}

h2, h3, p:has(> strong:only-child) {
  break-after: avoid;
}

ul, li {
  break-inside: avoid;
}

.education-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: .35mm;
}

.education-grid p {
  margin: 0;
  line-height: 1.42;
}

.skills-grid {
  display: grid;
  grid-template-columns: .92fr 1.35fr .9fr;
  gap: 3.8mm;
  margin: 0;
  padding: 0;
  list-style: none;
}

.skills-grid li {
  margin: 0;
  padding: 1.5mm 1.8mm;
  border-top: 1.4pt solid #4b86a5;
  background: #f4f7f9;
  line-height: 1.36;
}

.job-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 4mm;
  margin-top: 1.6mm;
}

.job-heading .job-main {
  display: block;
}

.job-heading .job-sub {
  display: block;
  margin-top: .35mm;
  color: #536675;
  font-size: 8.7pt;
  font-weight: 400;
}

.job-heading .job-date {
  flex: 0 0 auto;
  color: #173d57;
  white-space: nowrap;
}

.project-title {
  margin: 1.15mm 0 .55mm;
  color: #15577b;
  font-size: 10.4pt;
}

.project-summary {
  margin: 0 0 1.25mm;
  color: #465662;
  line-height: 1.4;
}

.module-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3.2mm;
  align-items: stretch;
}

.module-card {
  padding: 2mm 2.25mm 1.75mm;
  border: .65pt solid #d2dee5;
  border-top: 2.2pt solid #337ba0;
  border-radius: 1.2mm;
  background: #fafcfd;
  break-inside: avoid;
}

.module-title {
  margin: 0 0 .8mm;
  color: #15577b;
  font-size: 10pt;
}

.module-card ul {
  margin: 0;
  padding-left: 3.8mm;
}

.module-card li {
  margin: 0 0 .75mm;
  line-height: 1.34;
}

.module-card li:last-child {
  margin-bottom: 0;
}

.tech-stack {
  margin: 1.1mm 0 1mm;
  color: #536675;
  font-size: 8.8pt;
}

.compact-list {
  margin-bottom: 0;
}

.compact-list li {
  margin: .35mm 0;
}

.agent-section-summary {
  margin: .08mm 0 .2mm;
  font-size: 9.15pt;
  line-height: 1.24;
}

.agent-section-list {
  margin: .05mm 0 .2mm;
}

.agent-section-list li {
  margin: .04mm 0;
  font-size: 9.05pt;
  line-height: 1.22;
}

.agent-section-title {
  margin: .35mm 0 .12mm;
  line-height: 1.08;
}

.agent-section-list + .tech-stack {
  margin: .25mm 0 .45mm;
}

/* Classic single-column engineering resume. */
@page {
  size: A4;
  margin: 8mm 11mm 7mm;
}

body {
  color: #25282b;
  font-size: 9.95pt;
  line-height: 1.44;
}

strong {
  color: #202326;
}

.resume-header {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 12mm;
  margin-bottom: 1.35mm;
  padding-bottom: 1.35mm;
  border-bottom: .8pt solid #7f8f99;
}

.resume-header h1 {
  flex: 0 0 31mm;
  margin-right: 4mm;
  color: #202b33;
  font-size: 21pt;
  letter-spacing: .7pt;
}

.resume-header p {
  flex: 1 1 auto;
  min-width: 0;
  margin-right: 0;
  color: #4c5358;
  font-size: 8.6pt;
  text-align: center;
}

.resume-photo {
  display: none;
}

h2 {
  margin: 1.75mm 0 .75mm;
  padding-bottom: 0;
  border-bottom: 0;
  color: #273b48;
  font-size: 12.35pt;
  font-weight: 800;
  letter-spacing: .15pt;
}

h3 {
  color: #202b33;
  font-size: 11pt;
  font-weight: 800;
}

.education-grid {
  gap: .2mm;
}

.education-grid p {
  font-size: 9.45pt;
  line-height: 1.32;
}

.skills-grid {
  display: block;
  margin: .25mm 0 .6mm;
  padding-left: 4mm;
  list-style: disc;
}

.skills-grid li {
  margin: .3mm 0;
  padding: 0;
  border: 0;
  background: transparent;
  line-height: 1.3;
}

.job-heading {
  margin: 1.2mm 0 .45mm;
  color: #202b33;
  font-size: 11.15pt;
  font-weight: 800;
}

.job-heading .job-sub {
  margin-top: .2mm;
  color: #5a6065;
  font-size: 8.7pt;
}

.job-heading .job-date {
  color: #202b33;
}

.project-title {
  margin: .75mm 0 .3mm;
  color: #202b33;
  font-size: 10.75pt;
  font-weight: 800;
}

.project-summary {
  margin: 0 0 .85mm;
  color: #3d4246;
  line-height: 1.42;
}

.module-grid {
  display: block;
  margin: .25mm 0 .65mm;
}

.module-card {
  margin: .8mm 0 0;
  padding: .2mm 0 .45mm 2.1mm;
  border: 0;
  border-left: 1.5pt solid #6f8794;
  background: transparent;
  break-inside: avoid;
}

.module-title {
  margin: 0 0 .25mm;
  padding: 0;
  border-left: 0;
  color: #273b48;
  font-size: 10.55pt;
  font-weight: 800;
  line-height: 1.15;
}

.module-card ul {
  margin: .1mm 0 0;
  padding-left: 3.8mm;
}

.module-card li {
  margin: .42mm 0;
  line-height: 1.4;
}

.tech-stack {
  margin: .45mm 0 .6mm;
  color: #4f565b;
  font-size: 8.8pt;
}

.compact-list {
  margin-top: .2mm;
}
"""


def _new_tag(soup: BeautifulSoup, name: str, class_name: str):
    tag = soup.new_tag(name)
    tag["class"] = class_name
    return tag


def _style_document(body: str, photo_src: str) -> str:
    soup = BeautifulSoup(body, "html.parser")

    # Header: name on the left, contact details on the right.
    h1 = soup.find("h1")
    if h1 is not None:
        contact = h1.find_next_sibling("p")
        header = _new_tag(soup, "div", "resume-header")
        h1.insert_before(header)
        header.append(h1.extract())
        if contact is not None:
            header.append(contact.extract())
        photo = soup.new_tag("img", src=photo_src, alt="个人照片")
        photo["class"] = "resume-photo"
        header.append(photo)
        divider = header.find_next_sibling("hr")
        if divider is not None:
            divider.decompose()

    # Education is short enough to scan as two balanced columns.
    education = next((h for h in soup.find_all("h2") if h.get_text(strip=True) == "教育经历"), None)
    if education is not None:
        grid = _new_tag(soup, "div", "education-grid")
        cursor = education.find_next_sibling()
        while cursor is not None and cursor.name != "h2":
            following = cursor.find_next_sibling()
            if cursor.name == "p":
                grid.append(cursor.extract())
            cursor = following
        education.insert_after(grid)

    skills = next((h for h in soup.find_all("h2") if h.get_text(strip=True) == "专业技能"), None)
    if skills is not None:
        skills_list = skills.find_next_sibling("ul")
        if skills_list is not None:
            skills_list["class"] = "skills-grid"

    # Company headings use a conventional left/right resume alignment.
    for heading in soup.find_all("h3"):
        parts = [part.strip() for part in heading.get_text().split("\u3000|\u3000")]
        if len(parts) < 2:
            continue
        heading.clear()
        heading["class"] = "job-heading"
        info = _new_tag(soup, "span", "job-info")
        main = _new_tag(soup, "span", "job-main")
        main.string = parts[0]
        info.append(main)
        if len(parts) > 2:
            sub = _new_tag(soup, "span", "job-sub")
            sub.string = "　|　".join(parts[1:-1])
            info.append(sub)
        date = _new_tag(soup, "span", "job-date")
        date.string = parts[-1]
        heading.append(info)
        heading.append(date)

    project_strong = next(
        (strong for strong in soup.find_all("strong") if strong.get_text(strip=True).startswith("汽车底盘竞品数据智能管理平台")),
        None,
    )
    if project_strong is not None and project_strong.parent.name == "p":
        project_strong.parent["class"] = "project-title"
        summary = project_strong.parent.find_next_sibling("p")
        if summary is not None:
            summary["class"] = "project-summary"

    # Present the two equal project modules side by side without changing copy.
    module_nodes = []
    for title in ("竞品数据治理平台", "竞品数据分析 Agent"):
        strong = next((item for item in soup.find_all("strong") if item.get_text(strip=True).startswith(title)), None)
        if strong is None or strong.parent.name != "p":
            continue
        title_p = strong.parent
        items = title_p.find_next_sibling("ul")
        if items is not None:
            module_nodes.append((title_p, items))
    if len(module_nodes) == 2:
        module_grid = _new_tag(soup, "div", "module-grid")
        module_nodes[0][0].insert_before(module_grid)
        for title_p, items in module_nodes:
            card = _new_tag(soup, "section", "module-card")
            title_p["class"] = "module-title"
            card.append(title_p.extract())
            card.append(items.extract())
            module_grid.append(card)

    tech = next((strong for strong in soup.find_all("strong") if strong.get_text(strip=True) == "技术栈："), None)
    if tech is not None and tech.parent.name == "p":
        tech.parent["class"] = "tech-stack"

    agent_title = next(
        (item for item in soup.find_all("strong") if item.get_text(strip=True).startswith("竞品数据智能分析 Agent")),
        None,
    )
    if agent_title is not None and agent_title.parent.name == "p":
        agent_title.parent["class"] = "agent-section-title"
        agent_summary = agent_title.parent.find_next_sibling("p")
        agent_list = agent_summary.find_next_sibling("ul") if agent_summary is not None else None
        if agent_summary is not None:
            agent_summary["class"] = "agent-section-summary"
        if agent_list is not None:
            agent_list["class"] = "agent-section-list"

    research = next((h for h in soup.find_all("h2") if h.get_text(strip=True) == "科研与荣誉"), None)
    if research is not None:
        research_list = research.find_next_sibling("ul")
        if research_list is not None:
            research_list["class"] = "compact-list"

    return str(soup)


def render(source: Path, output: Path) -> None:
    body = markdown.markdown(
        source.read_text(encoding="utf-8"),
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )
    photo_candidates = (
        source.parent / "assets" / "resume_photo.jpg",
        source.parent / "assets" / "resume_photo.jpeg",
        source.parent / "assets" / "resume_photo.png",
        source.parent / "assets" / "resume_photo.webp",
    )
    photo_path = next((path for path in photo_candidates if path.exists()), None)
    if photo_path is None:
        photo_path = source.parent / "assets" / "resume_photo_placeholder.svg"
    body = _style_document(body, photo_path.resolve().as_uri())
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>赵云鹏 - 简历</title>
  <style>{CSS}</style>
</head>
<body>{body}</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if chrome:
        output.unlink(missing_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as handle:
                handle.write(document)
                temp_path = Path(handle.name)
            try:
                subprocess.run(
                    [
                        chrome,
                        "--headless",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--no-pdf-header-footer",
                        "--run-all-compositor-stages-before-draw",
                        f"--print-to-pdf={output}",
                        temp_path.as_uri(),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.CalledProcessError):
                HTML(string=document, base_url=str(source.parent)).write_pdf(output)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    else:
        HTML(string=document, base_url=str(source.parent)).write_pdf(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="简历_v1_0714.md")
    parser.add_argument("output", nargs="?", default="简历_v1_0714.pdf")
    args = parser.parse_args()
    render(Path(args.source).resolve(), Path(args.output).resolve())


if __name__ == "__main__":
    main()
