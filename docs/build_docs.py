"""Build the project report and the run/demo guide (.docx, plus .pdf via LibreOffice).

Numbers come from benchmark/results/*.json and docs/logs, images from
docs/screenshots and benchmark/results, so re-running the benchmarks and
scripts/capture_screenshots.py and then this script keeps everything in sync.

    python docs/build_docs.py
"""
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"
RESULTS = ROOT / "benchmark" / "results"
LOGS = DOCS / "logs"

TEAM = [("Prapti Kurkuri", "24BVD0185"), ("Vaanya Garg", "24BVD0139"),
        ("Aditya Manoj", "24BVD0077"), ("Arrnav Trisal", "24BVD0064")]
COURSE = "BECE355L — Advanced Cloud Computing"
TITLE = "Cloud-Native Parallel Computing Framework for Scalable Image Processing"
ACCENT = RGBColor(0x1E, 0x3A, 0x8A)
MISSING: list[str] = []


# ======================================================================
# docx helpers
# ======================================================================
class Doc:
    def __init__(self):
        self.d = Document()
        self.fig = 0
        self.tab = 0
        sec = self.d.sections[0]
        sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
        for side in ("left_margin", "right_margin"):
            setattr(sec, side, Cm(2.3))
        sec.top_margin = sec.bottom_margin = Cm(2.0)
        st = self.d.styles
        st["Normal"].font.name = "Calibri"
        st["Normal"].font.size = Pt(11)
        st["Normal"].paragraph_format.space_after = Pt(6)
        st["Normal"].paragraph_format.line_spacing = 1.15
        for lvl, size in ((1, 16), (2, 13), (3, 11.5)):
            h = st[f"Heading {lvl}"]
            h.font.name = "Calibri"
            h.font.size = Pt(size)
            h.font.color.rgb = ACCENT
            h.font.bold = True
            h.paragraph_format.space_before = Pt(14 if lvl == 1 else 10)
            h.paragraph_format.space_after = Pt(6)
            h.paragraph_format.keep_with_next = True
        self._footer()

    @property
    def width(self):
        sec = self.d.sections[0]
        return (sec.page_width - sec.left_margin - sec.right_margin) / 914400  # inches

    def _footer(self):
        p = self.d.sections[0].footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        for tag, text in (("begin", None), (None, "PAGE"), ("end", None)):
            if tag:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), tag)
            else:
                el = OxmlElement("w:instrText")
                el.set(qn("xml:space"), "preserve")
                el.text = text
            run._r.append(el)
        run.font.size = Pt(9)

    # -- text ------------------------------------------------------------
    def h(self, text, level=1):
        return self.d.add_heading(text, level)

    def p(self, text="", bold=False, italic=False, size=None, align=None, color=None, after=None):
        para = self.d.add_paragraph()
        self._rich(para, text, bold, italic, size, color)
        if align:
            para.alignment = align
        if after is not None:
            para.paragraph_format.space_after = Pt(after)
        return para

    def _rich(self, para, text, bold=False, italic=False, size=None, color=None):
        """Supports **bold**, `code` and [[highlighted placeholder]] markup."""
        for part in re.split(r"(\*\*.+?\*\*|`.+?`|\[\[.+?\]\])", text):
            if not part:
                continue
            if part.startswith("**"):
                r = para.add_run(part[2:-2])
                r.bold = True
            elif part.startswith("`"):
                r = para.add_run(part[1:-1])
                r.font.name = "Consolas"
                r.font.size = Pt(9.5)
                r.font.color.rgb = RGBColor(0x9F, 0x12, 0x39)
            elif part.startswith("[["):
                r = para.add_run(part[2:-2])
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW
                r.bold = True
            else:
                r = para.add_run(part)
            if bold:
                r.bold = True
            if italic:
                r.italic = True
            if size:
                r.font.size = Pt(size)
            if color:
                r.font.color.rgb = color
        return para

    def bullets(self, items, style="List Bullet"):
        for it in items:
            para = self.d.add_paragraph(style=style)
            self._rich(para, it)
            para.paragraph_format.space_after = Pt(3)

    def numbered(self, items):
        # Explicit numbers: Word's "List Number" style continues counting across lists.
        for i, it in enumerate(items, 1):
            para = self.d.add_paragraph()
            pf = para.paragraph_format
            pf.left_indent = Cm(0.9)
            pf.first_line_indent = Cm(-0.6)
            pf.space_after = Pt(3)
            self._rich(para, f"{i}.\t{it}")
            pf.tab_stops.add_tab_stop(Cm(0.9))

    def code(self, text, size=8.5):
        for i, line in enumerate(text.rstrip("\n").split("\n")):
            para = self.d.add_paragraph()
            pf = para.paragraph_format
            pf.space_after = Pt(0)
            pf.space_before = Pt(4) if i == 0 else Pt(0)
            pf.line_spacing = 1.0
            pf.left_indent = Cm(0.3)
            r = para.add_run(line or " ")
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            r.font.size = Pt(size)
            self._shade(para, "F1F5F9")
        self.d.add_paragraph().paragraph_format.space_after = Pt(2)

    def note(self, text, fill="EFF6FF", label="Note"):
        para = self.d.add_paragraph()
        self._rich(para, f"**{label}:** {text}")
        self._shade(para, fill)
        para.paragraph_format.left_indent = Cm(0.3)
        para.paragraph_format.right_indent = Cm(0.3)
        return para

    @staticmethod
    def _shade(para, fill):
        ppr = para._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        ppr.append(shd)

    def page_break(self):
        self.d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # -- tables & figures -------------------------------------------------
    def table(self, header, rows, caption=None, widths=None, size=9.5):
        if caption:
            self.tab += 1
            cp = self.p(f"Table {self.tab}: {caption}", bold=True, size=9.5, after=3)
            cp.paragraph_format.keep_with_next = True
        t = self.d.add_table(rows=1, cols=len(header))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, text in enumerate(header):
            cell = t.rows[0].cells[i]
            cell.text = ""
            self._rich(cell.paragraphs[0], str(text), bold=True, size=size, color=RGBColor(255, 255, 255))
            self._cell_fill(cell, "1E3A8A")
        for ri, row in enumerate(rows):
            cells = t.add_row().cells
            for i, text in enumerate(row):
                cells[i].text = ""
                self._rich(cells[i].paragraphs[0], str(text), size=size)
                if ri % 2:
                    self._cell_fill(cells[i], "F1F5F9")
        # Fixed layout with explicit grid widths (LibreOffice ignores per-cell widths alone).
        total = self.width * 2.54
        widths = widths or [total / len(header)] * len(header)
        t.autofit = False
        tblpr = t._tbl.tblPr
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tblpr.append(layout)
        for i, w in enumerate(widths):
            t.columns[i].width = Cm(w)
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
        for row in t.rows:
            for c in row.cells:
                for para in c.paragraphs:
                    para.paragraph_format.space_after = Pt(1)
        self.d.add_paragraph().paragraph_format.space_after = Pt(2)
        return t

    @staticmethod
    def _cell_fill(cell, fill):
        tcpr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        tcpr.append(shd)

    def figure(self, path: Path, caption: str, width=None, max_height=8.6):
        if not path.exists():
            MISSING.append(str(path.relative_to(ROOT)))
            self.note(f"missing image {path.name}", fill="FEF3C7")
            return
        with Image.open(path) as im:
            w_px, h_px = im.size
        width = width or self.width
        if width * h_px / w_px > max_height:
            width = max_height * w_px / h_px
        para = self.d.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.keep_with_next = True
        para.add_run().add_picture(str(path), width=Inches(width))
        self.fig += 1
        cp = self.p(f"Figure {self.fig}: {caption}", italic=True, size=9.5,
                    align=WD_ALIGN_PARAGRAPH.CENTER, after=10)
        return cp

    def placeholder_figure(self, caption: str, how: str):
        self.fig += 1
        para = self.d.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._rich(para, f"[[ Paste screenshot here: {caption} ]]")
        para2 = self.p(f"({how})", italic=True, size=9, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
        para2.paragraph_format.keep_with_next = True
        self.p(f"Figure {self.fig}: {caption}", italic=True, size=9.5,
               align=WD_ALIGN_PARAGRAPH.CENTER, after=10)

    def save(self, path: Path):
        self.d.save(path)
        print("wrote", path.relative_to(ROOT))


# ======================================================================
# data
# ======================================================================
def load_results():
    local = json.loads((RESULTS / "results.json").read_text())
    dist = json.loads((RESULTS / "distributed_bilateral.json").read_text())
    return local, dist


def test_names():
    log = LOGS / "term_01_tests.txt"
    if not log.exists():
        return [], ""
    text = log.read_text()
    names = re.findall(r"tests/(\w+)\.py::(\S+) PASSED", text)
    summary = re.search(r"=+ (\d+ passed.*?) =+", text)
    return names, summary.group(1) if summary else ""


def fmt(v, digits=2, suffix=""):
    return "—" if v is None else f"{v:.{digits}f}{suffix}"


# ======================================================================
# REPORT
# ======================================================================
def build_report():
    local, dist = load_results()
    lrows, drows = local["rows"], dist["rows"]
    doc = Doc()

    def lrow(op, workers=None, mode="parallel"):
        return next(r for r in lrows if r["op"] == op and r["mode"] == mode and
                    (workers is None or r["workers"] == workers))

    seq_b = lrow("bilateral", mode="sequential")
    par_b = [r for r in lrows if r["op"] == "bilateral" and r["mode"] == "parallel"]
    best_b = max(par_b, key=lambda r: r["speedup"])
    b8 = lrow("bilateral", 8)
    dseq = next(r for r in drows if r["mode"] == "sequential")
    dpar = [r for r in drows if r["mode"] == "distributed"]
    d8 = max(dpar, key=lambda r: r["workers"])
    d1 = min(dpar, key=lambda r: r["workers"])
    serial_8 = d8["partition_s"] + d8["aggregate_s"]
    overhead_8 = d8["time_s"] - d8["partition_s"] - d8["processing_s"] - d8["aggregate_s"]
    bound = dseq["time_s"] / (serial_8 + overhead_8)
    tests, test_summary = test_names()
    n_tests = len(tests) or 24

    # ---------------------------------------------------------- title page
    for _ in range(3):
        doc.p("")
    doc.p(COURSE, size=13, align=WD_ALIGN_PARAGRAPH.CENTER, color=RGBColor(0x47, 0x55, 0x69))
    doc.p("Fall Semester 2026–27", size=11, align=WD_ALIGN_PARAGRAPH.CENTER,
          color=RGBColor(0x47, 0x55, 0x69), after=40)
    doc.p(TITLE, bold=True, size=24, align=WD_ALIGN_PARAGRAPH.CENTER, color=ACCENT, after=12)
    doc.p("Project Report", size=15, align=WD_ALIGN_PARAGRAPH.CENTER, after=6)
    doc.p("Python · OpenCV · Redis · FastAPI · Docker · Kubernetes", size=11, italic=True,
          align=WD_ALIGN_PARAGRAPH.CENTER, color=RGBColor(0x47, 0x55, 0x69), after=50)
    doc.p("Submitted by", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, after=4)
    t = doc.table(["Name", "Register No."], TEAM, widths=[6, 4], size=11)
    doc.p("")
    doc.p("Submitted to: [[Faculty name]]", align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.p(dt.date.today().strftime("%d %B %Y"), align=WD_ALIGN_PARAGRAPH.CENTER,
          color=RGBColor(0x47, 0x55, 0x69))
    doc.page_break()

    # ------------------------------------------------------------ abstract
    doc.h("Abstract")
    doc.p(
        "Processing very large images (satellite scenes, medical scans, microscopy) on a single "
        "machine, one step after another, does not scale. Kubernetes can run many containers but has "
        "no built-in notion of splitting one large job into parallel tasks, while general-purpose "
        "distributed frameworks such as Spark or Ray are heavyweight for this purpose. This project "
        "builds a lightweight, container-first framework that divides a large image into tiles, "
        "schedules the tiles through a Redis task queue to a pool of parallel worker pods that "
        "process them with OpenCV, and aggregates the results back into the final image.")
    doc.p(
        "The framework supports the full workflow: image upload, partitioning with overlapping "
        "halos (so the stitched output is identical to whole-image processing), task scheduling, "
        "parallel processing, result aggregation and reconstruction. It is fault tolerant: every "
        "claimed tile carries a lease that the worker renews with heartbeats, and tiles held by a "
        "crashed worker are retried automatically. It scales dynamically: an autoscaler adjusts "
        "the number of worker pods to the length of the task queue. The system is packaged with "
        "Docker and deployed with Docker Compose or Kubernetes (Minikube).")
    doc.p(
        f"Performance was evaluated on an 8192 × 8192 image (67 megapixels). For the CPU-heavy "
        f"bilateral filter the tiled parallel engine reached a **{b8['speedup']:.2f}× speedup with 8 "
        f"workers** and **{best_b['speedup']:.2f}× with {best_b['workers']}** over sequential "
        f"execution, with output identical to the sequential result. Through the complete framework "
        f"(HTTP upload, Redis, workers, aggregation) the parallel phase sped up "
        f"**{d8['processing_speedup']:.2f}×** with {d8['workers']} workers and end-to-end time improved "
        f"**{d8['speedup']:.2f}×**, the gap being explained by the serial upload/partition and "
        f"aggregation stages (Amdahl's law). Lightweight operations such as Canny are faster "
        f"sequentially, because moving the data costs more than the computation saved. A "
        f"deliberately killed worker was recovered automatically with no loss of output.")
    doc.p("**Keywords:** cloud-native, parallel computing, Kubernetes, Redis, task queue, image "
          "processing, OpenCV, fault tolerance, autoscaling, speedup.")
    doc.page_break()

    # ------------------------------------------------------------ contents
    doc.h("Contents")
    contents = [
        "1. Introduction", "2. Objectives", "3. Literature Review", "4. System Architecture",
        "5. Tools and Technology Stack", "6. Design and Implementation",
        "7. Results: the System in Operation", "8. Performance Evaluation", "9. Testing",
        "10. Deployment on Kubernetes and Mapping to AWS", "11. Limitations and Future Work",
        "12. Conclusion", "References", "Appendix A: Project Structure",
        "Appendix B: Configuration and API Reference"]
    for c in contents:
        doc.p(c, after=2)
    doc.page_break()

    # ------------------------------------------------------- 1 introduction
    doc.h("1. Introduction")
    doc.h("1.1 Background", 2)
    doc.p(
        "Image sizes keep growing: a single satellite scene or whole-slide pathology image can have "
        "hundreds of megapixels. Many image operations (filtering, edge detection, denoising) are "
        "local: the value of an output pixel depends only on a small neighbourhood of input pixels. "
        "Such work is naturally data-parallel: the image can be cut into pieces, the pieces processed "
        "independently on different machines or cores, and the outputs reassembled. Cloud platforms "
        "provide exactly the elastic compute this needs, and containers plus Kubernetes make it "
        "practical to run many identical workers and replace them when they fail.")
    doc.h("1.2 Problem Statement", 2)
    doc.numbered([
        "**Sequential execution does not scale.** Large computational jobs take too long when run "
        "task-by-task on a single machine.",
        "**Kubernetes stops at orchestration.** It manages containers well but has no built-in "
        "concept of splitting one large job into parallel tasks, tracking them and combining the "
        "results.",
        "**Existing frameworks are heavyweight.** Spark, Ray and Celery solve distributed compute "
        "but are not container-first and carry significant setup overhead for a focused workload "
        "like tile-based image processing."])
    doc.h("1.3 Motivation", 2)
    doc.p(
        "The aim is a lightweight, container-first framework that combines automatic task splitting "
        "with cloud-native elasticity and fault recovery, filling the gap between plain Kubernetes "
        "and heavy distributed-compute frameworks. The framework should be small enough to "
        "understand completely, yet show the core ideas of distributed systems: partitioning, "
        "scheduling, at-least-once delivery with idempotent completion, leases and heartbeats, "
        "autoscaling, and the limits of parallel speedup.")

    # --------------------------------------------------------- 2 objectives
    doc.h("2. Objectives")
    doc.table(
        ["#", "Objective", "Outcome", "Where"],
        [["1", "Design a cloud-native architecture that splits a large job into independent "
               "parallel tasks", "Achieved: image → halo tiles → one Redis task per tile",
          "§4, §6.1–6.2"],
         ["2", "Containerise and orchestrate the pipeline (Docker, Kubernetes/Minikube)",
          "Achieved: one Docker image; Compose file; Kubernetes manifests with RBAC, probes, "
          "resource limits", "§6.7, §10"],
         ["3", "Demonstrate measurable speedup: sequential vs 2/4/8 workers",
          f"Achieved: {b8['speedup']:.2f}× at 8 workers (compute), "
          f"{d8['processing_speedup']:.2f}× parallel phase end-to-end", "§8"],
         ["4", "Fault tolerance: retry a task automatically when its worker fails",
          "Achieved: leases + heartbeats + reaper; demonstrated by killing a worker", "§6.5, §7.3"],
         ["5", "Elastic scaling of worker pods from task-queue length",
          "Achieved: queue-length autoscaler (+ HPA / KEDA alternatives); 1 → 8 pods on a live "
          "Minikube cluster", "§6.6, §10.2"]],
        caption="Project objectives and outcomes", widths=[0.8, 6.2, 6.4, 2.2])

    # ---------------------------------------------------- 3 literature review
    doc.h("3. Literature Review")
    papers = [
        ("Towards an Adaptive Runtime System for Cloud-Native HPC (2026)",
         "Proposes adaptive runtime techniques for cloud-native HPC, focusing on dynamic CPU/GPU "
         "resources, load balancing and cloud variability, using Charm++ for adaptive and resilient "
         "parallel computing.",
         "Motivates dynamic resizing of the worker pool and over-decomposition (many more tiles than "
         "workers) so that load balances automatically.", "https://arxiv.org/abs/2603.14630"),
        ("An Elastic Job Scheduler for HPC Applications on the Cloud (SC25, 2025)",
         "Introduces an elastic scheduler for HPC workloads on Kubernetes that scales resources "
         "according to cluster conditions to improve utilisation and reduce job response time.",
         "Our queue-length autoscaler is a simple instance of this idea: demand (pending tiles) "
         "drives the replica count of the worker Deployment.",
         "https://ieeexplore.ieee.org/document/11358356/"),
        ("Serverless Data Pipeline Architecture Supporting Distributed Machine Learning on Fog "
         "Devices (2026)",
         "Combines serverless computing, Kubernetes and distributed ML using OpenFaaS and KubeRay, "
         "and explores fault tolerance across edge, fog and cloud.",
         "Shares the queue-plus-stateless-workers design; our workers are stateless and any of them "
         "can take any tile, which is what makes retry and scaling simple.",
         "https://www.sciencedirect.com/science/article/pii/S0140366426000952"),
        ("A Kubernetes-Based Scheme for Efficient Resource Allocation in Containerized Workflow "
         "(2025)",
         "Proposes dynamic resource allocation for Kubernetes workflows that predicts future "
         "workload requirements, improving execution time and utilisation.",
         "Points to future work: predictive scaling from the size of submitted jobs instead of "
         "reacting to queue length.",
         "https://www.sciencedirect.com/science/article/pii/S0167739X240066305"),
        ("Running Cloud-Native Workloads on HPC with High-Performance Kubernetes (2024)",
         "Studies running cloud-native workloads directly on HPC systems by combining Kubernetes "
         "with HPC infrastructure, addressing the gap between the two worlds.",
         "Supports the premise of the project: Kubernetes is a viable substrate for parallel "
         "compute when a thin scheduling layer is added on top.",
         "https://arxiv.org/abs/2409.16919")]
    for i, (title, summary, rel, link) in enumerate(papers, 1):
        doc.p(f"**[{i}] {title}**", after=2)
        doc.p(summary, after=2)
        doc.p(f"**Relevance:** {rel}", after=2)
        doc.p(link, size=9, color=RGBColor(0x25, 0x63, 0xEB), after=8)
    doc.p(
        "Compared with general frameworks (Apache Spark, Ray, Celery), this project trades "
        "generality for transparency: the whole scheduling layer is a few hundred lines on top of "
        "Redis primitives, every state transition is an atomic Lua script, and the system is "
        "container-native from the start.")

    # --------------------------------------------------------- 4 architecture
    doc.h("4. System Architecture")
    doc.figure(SHOTS / "fig_architecture.png", "System architecture and data flow")
    doc.p("The system has four kinds of components, all built from one Docker image:")
    doc.bullets([
        "**API service (FastAPI).** Accepts uploads, partitions the image, enqueues tasks, reports "
        "job status, timings and cluster metrics, serves the final image and the web UI. It also "
        "runs the reaper that recovers tiles from failed workers.",
        "**Redis.** Acts as both the task queue (lists, a sorted set of leases) and the shared store "
        "for tiles, per-tile results, job metadata and the final image.",
        "**Worker pods.** Stateless processes that claim tiles, run the OpenCV operation and write "
        "results. The worker that completes a job's last tile performs aggregation and "
        "reconstruction.",
        "**Autoscaler.** Watches the queue depth and scales the worker Deployment through the "
        "Kubernetes API."])
    doc.p("**Data flow** (numbers match Figure 1):")
    doc.numbered([
        "The client uploads an image with `POST /jobs`, choosing the operation and tile size.",
        "The API decodes it and splits it into a grid of tiles, each with a halo of overlapping "
        "context pixels.",
        "Tiles are written to Redis and one task per tile is pushed onto `queue:pending`.",
        "Workers atomically move a task to `queue:processing` and record a lease.",
        "Each worker processes its tile with OpenCV and stores the result. Progress is counted "
        "exactly once per tile.",
        "The worker that completes the final tile crops the halos and stitches the full image.",
        "The client polls `GET /jobs/{id}` and downloads the result from `GET /jobs/{id}/result`."])

    # --------------------------------------------------------------- 5 tools
    doc.h("5. Tools and Technology Stack")
    doc.table(
        ["Layer", "Technology", "Role in the project"],
        [["Language", "Python 3.12", "All services, tests and benchmarks"],
         ["Image processing", "OpenCV 4 (opencv-python-headless), NumPy",
          "Canny, Sobel, Gaussian blur, bilateral filter, sharpen, grayscale; array slicing for tiles"],
         ["API", "FastAPI + Uvicorn", "REST API, job tracking, web UI, Swagger documentation"],
         ["Queue / store", "Redis 7.2 + Lua scripts", "Task queue with leases; tiles, results, metadata"],
         ["Compute", "concurrent.futures (ProcessPoolExecutor)",
          "Optional process pool inside each worker; benchmark engine"],
         ["Containers", "Docker, Docker Compose", "Single image for all roles; local multi-container runs"],
         ["Orchestration", "Kubernetes (Minikube)", "Deployments, Services, probes, RBAC, scaling, self-healing"],
         ["Scaling", "Custom autoscaler (kubernetes Python client); HPA / KEDA manifests",
          "Elastic worker count"],
         ["Testing", "pytest, fakeredis, httpx", f"{n_tests} automated tests"],
         ["Measurement", "psutil, matplotlib", "CPU / memory sampling, charts"]],
        caption="Technology stack", widths=[3, 5.4, 7.2])

    # ------------------------------------------------------ 6 implementation
    doc.h("6. Design and Implementation")
    doc.h("6.1 Image Upload and Partitioning", 2)
    doc.p(
        "The uploaded image is decoded with OpenCV and divided into a grid of square tiles "
        "(512 × 512 px by default; edge tiles are smaller). A tile cannot be processed in "
        "isolation: a 15 × 15 blur near the tile border needs 7 pixels from the neighbouring tile, "
        "otherwise visible seams appear. Each tile therefore has a **core** region (the pixels it owns in the "
        "output) and a **halo** region, the core expanded by `halo` pixels on every side and clipped to "
        "the image. Workers process the halo region; reconstruction keeps only the core. The halo "
        "is declared by each operation:")
    doc.table(["Operation", "Kernel / method", "Halo (px)", "Tiled result vs whole image"],
              [["gaussian_blur", "15 × 15 Gaussian", "7", "identical"],
               ["sobel", "3 × 3 Sobel gradient magnitude", "1", "identical"],
               ["sharpen", "3 × 3 kernel", "1", "identical"],
               ["bilateral", "d = 9, σ = 75", "4", "identical"],
               ["grayscale", "colour conversion", "0", "identical"],
               ["canny", "5 × 5 Gaussian + Canny (50/150)", "16",
                "≥ 99.97 % of pixels identical (hysteresis can follow edges further than any finite halo)"]],
              caption="Operations and their halo sizes", widths=[2.6, 5, 1.7, 6.3])
    doc.figure(SHOTS / "fig_tiling.png",
               "Partitioning a 2048 × 2048 image into 256 px tiles (green, numbered). The orange box "
               "shows the halo region of tile 27 (exaggerated ×3 for visibility).", width=4.6)
    doc.code(
        "def plan_tiles(height, width, tile_size, halo):\n"
        "    for y0 in range(0, height, tile_size):\n"
        "        for x0 in range(0, width, tile_size):\n"
        "            y1, x1 = min(y0 + tile_size, height), min(x0 + tile_size, width)\n"
        "            yield TileSpec(core=(y0, y1, x0, x1),\n"
        "                           halo=(max(0, y0 - halo), min(height, y1 + halo),\n"
        "                                 max(0, x0 - halo), min(width, x1 + halo)))")
    doc.p(
        "Tiles travel between the API, Redis and the workers in a compact raw format (a 16-byte "
        "header with height, width and channels followed by the pixels). Profiling showed that "
        "PNG-encoding the tiles made partitioning and aggregation several times slower, and these "
        "tiles never leave the system.")

    doc.h("6.2 Task Scheduling: a Reliable Queue on Redis", 2)
    doc.p("All scheduling state lives in Redis:")
    doc.table(["Key", "Type", "Purpose"],
              [["imgframe:queue:pending", "LIST", "Task ids (`<job>:<tile>`) waiting for a worker"],
               ["imgframe:queue:processing", "LIST", "Tasks currently claimed"],
               ["imgframe:queue:leases", "ZSET", "Task → lease expiry time; heartbeats push it forward"],
               ["imgframe:queue:attempts", "HASH", "Task → number of claims (for the retry limit)"],
               ["imgframe:job:<id>", "HASH", "Status, operation, size, tile count, done counter, timings"],
               ["imgframe:job:<id>:tiles / :specs", "HASH", "Input tile bytes / tile geometry"],
               ["imgframe:job:<id>:results / :completed", "HASH / SET",
                "Output tile bytes / indices already counted"],
               ["imgframe:job:<id>:result", "STRING", "Reconstructed final image"]],
              caption="Redis data model", widths=[5.6, 2, 8])
    doc.p(
        "Every state transition is a **Lua script executed atomically inside Redis**, so concurrent "
        "workers can never claim the same task twice or lose one between two commands. Claiming a "
        "task moves it from pending to processing, records its lease and marks the job running, in "
        "one step:")
    doc.code(
        "local task = redis.call('LMOVE', KEYS[1], KEYS[2], 'LEFT', 'RIGHT')   -- pending -> processing\n"
        "if not task then return false end\n"
        "redis.call('ZADD', KEYS[3], now + lease_seconds, task)                -- lease\n"
        "redis.call('HINCRBY', KEYS[4], task, 1)                               -- attempt count\n"
        "if redis.call('HSETNX', job, 'started', now) == 1 then\n"
        "  redis.call('HSET', job, 'status', 'running')\n"
        "end\n"
        "return task")
    doc.p(
        "Because there are many more tiles than workers (an 8192² image at 512 px gives 256 tiles), "
        "scheduling is naturally load balanced: a fast worker simply claims more tiles. This "
        "over-decomposition is the same idea used by adaptive HPC runtimes [1].")

    doc.h("6.3 Parallel Processing", 2)
    doc.p(
        "A worker runs a loop: claim a task, load the tile, run the OpenCV operation, store the "
        "result. A background thread renews the lease of every in-flight tile every few seconds "
        "(heartbeat). Parallelism exists at two levels: across **worker pods** (scaled by "
        "Kubernetes) and, optionally, across a **process pool inside each worker** "
        "(`WORKER_PROCS`, using `concurrent.futures.ProcessPoolExecutor`). OpenCV is pinned to one "
        "internal thread per process (`cv2.setNumThreads(1)`) so that all parallelism comes from the "
        "framework and the sequential baseline is genuinely sequential.")

    doc.h("6.4 Result Aggregation and Reconstruction", 2)
    doc.p(
        "When a worker finishes a tile, a Lua script removes the task from the processing list "
        "and adds the tile index to the job's `completed` set. Only if the index was not already "
        "there does it store the result and increment the job's `done` counter. Completion is "
        "therefore **idempotent**: if a slow worker and a retry both finish the same tile, it is "
        "counted once. The script returns `done` and `total`; the worker that observes "
        "`done == total` (exactly one, because the increment is atomic) performs aggregation. It "
        "loads all result tiles, crops each tile's halo, writes the cores into a new array and "
        "stores the reconstructed image. Per-tile data is then deleted. Encoding the final image as "
        "PNG/JPEG happens on the first download and is cached, so the job completes as soon as "
        "the pixels are ready.")

    doc.h("6.5 Fault Tolerance", 2)
    doc.p("The framework provides at-least-once processing with exactly-once accounting:")
    doc.table(["Failure", "Detection", "Recovery"],
              [["Worker pod crashes or is killed (SIGKILL, OOM, node loss)",
                "Its leases stop being renewed and expire (10 s on Kubernetes)",
                "The reaper (in the API and every worker) moves expired tasks back to the front of "
                "the pending queue; Kubernetes recreates the pod"],
               ["Worker shut down gracefully (scale-down, rolling update: SIGTERM)",
                "Signal handler", "In-flight tiles are released immediately without counting an attempt"],
               ["Tile processing raises an error", "Exception in the worker",
                "The lease is expired at once, so the tile is retried"],
               ["A tile fails repeatedly (e.g. corrupt data)", "Attempt counter reaches MAX_RETRIES (3)",
                "The job is marked failed with an error message; remaining tiles are discarded"],
               ["Retried tile also finished by the original worker", "Index already in `completed` set",
                "Duplicate result ignored"],
               ["Transient Redis connection drop", "redis-py connection error",
                "Client retries with exponential back-off; aggregation is retried 3 times"],
               ["Redis memory nearly full", "Admission check before accepting a job",
                "API answers HTTP 503 with Retry-After (back-pressure); clients retry; finished "
                "results expire after 15 min or are deleted with DELETE /jobs/{id}"]],
              caption="Failure scenarios and how they are handled", widths=[4.6, 4.6, 6.4])
    doc.p(
        "For testing, a worker can be told to crash at random while holding a tile "
        "(`CHAOS_CRASH_RATE`), and the local launcher can SIGKILL a worker mid-job (`--kill-worker`).")

    doc.h("6.6 Dynamic Scaling", 2)
    doc.p(
        "The autoscaler runs as its own Deployment with a ServiceAccount allowed only to read and "
        "patch the scale of the `worker` Deployment. Every 3 seconds it computes:")
    doc.code("desired = clamp( ceil(queue_depth / TASKS_PER_WORKER), MIN_WORKERS, MAX_WORKERS )\n"
             "        = clamp( ceil(queue_depth / 16), 1, 8 )")
    doc.p(
        "Scale-up is applied immediately; scale-down waits until demand has been low for 30 seconds "
        "to avoid churning pods between jobs. Workers removed during scale-down receive SIGTERM and "
        "hand back their in-flight tiles. Two alternatives are included: a standard CPU-based "
        "HorizontalPodAutoscaler and a KEDA ScaledObject that scales on the Redis list length.")

    doc.h("6.7 Containerisation and Orchestration", 2)
    doc.p(
        "A single Docker image (python:3.12-slim with the dependencies, running as a non-root user) "
        "contains all roles; the container command selects API, worker or autoscaler. Docker "
        "Compose runs Redis, the API and a scalable number of workers for local multi-container "
        "testing. The Kubernetes manifests define:")
    doc.table(["Manifest", "Objects", "Key settings"],
              [["00-namespace.yaml", "Namespace, ConfigMap", "Shared configuration (Redis URL, tile size, lease)"],
               ["10-redis.yaml", "Deployment, Service", "redis:7.2-alpine, readiness probe, 3 GB maxmemory"],
               ["20-api.yaml", "Deployment, NodePort Service", "Port 30080, readiness and liveness probes on /healthz"],
               ["30-worker.yaml", "Deployment", "CPU request 0.5 / limit 1 per pod, 30 s termination grace"],
               ["40-autoscaler.yaml", "ServiceAccount, Role, RoleBinding, Deployment",
                "Least-privilege RBAC, scaling policy via environment"],
               ["optional/hpa-cpu.yaml", "HorizontalPodAutoscaler", "Alternative: 70 % CPU target"],
               ["optional/keda-scaledobject.yaml", "ScaledObject", "Alternative: Redis list length"]],
              caption="Kubernetes manifests", widths=[4.4, 4.6, 6.6])

    doc.h("6.8 REST API and Web Interface", 2)
    doc.table(["Method", "Endpoint", "Description"],
              [["POST", "/jobs", "Upload an image (multipart: file, op, tile_size); returns the job"],
               ["GET", "/jobs", "Recent jobs"],
               ["GET", "/jobs/{id}", "Status, progress, retries and timings of each phase"],
               ["GET", "/jobs/{id}/result", "Final image; ?format=png|jpg|webp, ?max_side=N to downscale"],
               ["GET", "/jobs/{id}/preview", "Downscaled copy of the upload"],
               ["DELETE", "/jobs/{id}", "Delete a job and free its memory"],
               ["GET", "/metrics", "Queue depth, in-flight tasks, retries, per-worker statistics"],
               ["GET", "/ops", "Available operations"],
               ["GET", "/docs", "Interactive API documentation (Swagger UI)"]],
              caption="REST API", widths=[1.6, 4, 10])
    doc.p(
        "The web interface (served at `/`) lets the user upload an image, follow progress tile by "
        "tile, compare the original and the result, and see the per-phase timings. It also shows "
        "live cluster metrics and the list of workers with the number of tiles each has processed.")

    # ------------------------------------------------------------- 7 results
    doc.h("7. Results: the System in Operation")
    doc.p(
        "All screenshots in this section were captured from the running system: Redis 7.2, the "
        "FastAPI service and four worker processes on the development machine, driven through the "
        "web UI by an automated browser (`scripts/capture_screenshots.py`).")
    doc.h("7.1 Web Interface", 2)
    doc.figure(SHOTS / "ui_01_idle.png", "Web UI ready for a job; four workers registered")
    doc.figure(SHOTS / "ui_02_canny_done.png",
               "Canny edge detection on a 2048 × 2048 photograph split into 64 tiles of 256 px")
    doc.figure(SHOTS / "ui_03_bilateral_running.png",
               "A 67-megapixel (8192 × 8192) bilateral-filter job in progress: tiles are being "
               "processed by four workers in parallel")
    doc.figure(SHOTS / "ui_04_bilateral_done.png",
               "The same job completed: 256 tiles, timings for every phase, tiles shared evenly "
               "between the workers")
    doc.h("7.2 Correctness of Tiled Processing", 2)
    doc.figure(SHOTS / "fig_operations.png",
               "Outputs of the tiled, parallel pipeline for each supported operation")
    doc.figure(SHOTS / "fig_seamless.png",
               "Full-resolution Canny output around tile boundaries (red lines). Edges continue "
               "across the joins: the halos remove seams.", width=4.2)
    doc.p(
        "Automated tests compare the tiled result with whole-image processing: Gaussian blur, Sobel, "
        "sharpen, bilateral and grayscale are **bit-for-bit identical**; Canny agrees on more than "
        "99.97 % of pixels (the end-to-end demo measured 99.98 %).")
    doc.h("7.3 Fault-Tolerance Demonstration", 2)
    doc.p(
        "While a 256-tile job was running, one of the four workers was killed with SIGKILL, "
        "simulating a crashed pod. The tile it was holding stayed in `queue:processing` until its "
        "lease expired. The reaper then requeued it, a surviving worker processed it, and the job "
        "completed with a correct result and `retries = 1`.")
    doc.figure(SHOTS / "ui_05_fault_recovered.png",
               "Job completed after a worker was killed: 1 retry, the dead worker shown in red, "
               "the other three workers finished the job")
    doc.figure(SHOTS / "term_03_fault.png",
               "Command-line fault-tolerance demo: worker killed mid-job, job completes, output "
               "100 % identical to the sequential result")
    doc.h("7.4 End-to-End Command-Line Demo, API and Metrics", 2)
    doc.figure(SHOTS / "term_02_demo.png",
               "Command-line demo: upload, 64 tiles processed by 4 workers, result verified against "
               "sequential Canny")
    if (SHOTS / "ui_06_api_docs.png").exists():
        doc.figure(SHOTS / "ui_06_api_docs.png", "Interactive API documentation generated by FastAPI")
    doc.figure(SHOTS / "ui_07_metrics_json.png", "The /metrics endpoint used by the UI and the autoscaler")

    # ----------------------------------------------------------- 8 performance
    doc.h("8. Performance Evaluation")
    doc.h("8.1 Experimental Setup", 2)
    doc.table(["Item", "Value"],
              [["Machine", f"8 physical / {local['cpus']} logical cores, 23 GB RAM, Linux 6.17"],
               ["Test image", f"{local['size']} × {local['size']} px, 3 channels (192 MB), synthetic "
                              "shapes + noise (deterministic)"],
               ["Tile size", f"{local['tile_size']} px → 256 tiles"],
               ["Operations", "bilateral (CPU-heavy), canny and gaussian_blur (lightweight)"],
               ["Workers", "1, 2, 4, 8, 16 processes (compute benchmark); 1, 2, 4, 8 workers (framework)"],
               ["Method", "Median of 3 runs (compute), best of 2 (framework); OpenCV limited to 1 thread "
                          "per process; CPU and memory sampled every 100 ms with psutil"],
               ["Metrics", "Processing time, speedup S = T_seq / T_par, efficiency E = S / N, "
                           "CPU cores busy, peak memory"]],
              caption="Benchmark configuration", widths=[3.2, 12.4])
    doc.p("Two experiments were run:")
    doc.bullets([
        "**Compute-level** (`benchmark/bench_local.py`): the partition → parallel process → "
        "reconstruct engine with N worker processes, without network or Redis. It measures how well "
        "the work itself parallelises.",
        "**Full framework** (`benchmark/bench_distributed.py`): the real system (HTTP upload to the "
        "API, Redis queue, N worker processes, aggregation), timed from upload until the "
        "reconstructed image is ready."])

    doc.h("8.2 Compute-Level Speedup", 2)
    rows = []
    for r in lrows:
        rows.append([r["op"], "sequential" if r["mode"] == "sequential" else str(r["workers"]),
                     fmt(r["time_s"], 3), fmt(r["speedup"]), fmt(r["efficiency"] * 100, 0, " %"),
                     fmt(r["cores_busy_avg"], 1), fmt(r["rss_peak_mb"], 0), fmt(r["match_pct"], 3, " %")])
    doc.table(["Operation", "Workers", "Time (s)", "Speedup", "Efficiency", "Cores busy",
               "Peak mem (MB)", "Match"], rows,
              caption=f"Sequential vs parallel, {local['size']}² image", size=8.5,
              widths=[2.4, 1.9, 1.7, 1.7, 1.8, 1.8, 2.2, 2.1])
    doc.figure(RESULTS / "charts.png",
               "Processing time (log scale), speedup and CPU utilisation versus number of workers")
    doc.figure(SHOTS / "term_04_bench_local.png", "Benchmark run (terminal output)")

    doc.h("8.3 End-to-End Speedup of the Full Framework", 2)
    rows = []
    for r in drows:
        rows.append(["sequential" if r["mode"] == "sequential" else str(r["workers"]),
                     fmt(r["time_s"], 2), fmt(r["speedup"]),
                     fmt(r["partition_s"], 2), fmt(r["processing_s"], 2),
                     fmt(r["processing_speedup"]), fmt(r["aggregate_s"], 2)])
    doc.table(["Workers", "End-to-end (s)", "Speedup", "Upload+partition (s)", "Parallel phase (s)",
               "Phase speedup", "Aggregation (s)"], rows,
              caption=f"Full framework, bilateral filter on {dist['size']}² image", size=8.5,
              widths=[2.2, 2.3, 1.8, 2.6, 2.4, 2.1, 2.2])
    doc.figure(RESULTS / "distributed_bilateral.png",
               "Where the time goes in the full framework, and speedup of the parallel phase versus "
               "end-to-end")
    doc.figure(SHOTS / "term_05_bench_distributed.png", "Full-framework benchmark run (terminal output)")

    doc.h("8.4 Analysis", 2)
    doc.p(
        f"**CPU-heavy work scales well.** The bilateral filter takes {seq_b['time_s']:.2f} s "
        f"sequentially. With 2, 4 and 8 workers the speedup is "
        f"{lrow('bilateral', 2)['speedup']:.2f}×, {lrow('bilateral', 4)['speedup']:.2f}× and "
        f"{b8['speedup']:.2f}×. CPU utilisation rises with the worker count, so the extra workers "
        f"are actually used. Efficiency falls as N grows ({lrow('bilateral', 2)['efficiency']*100:.0f} % "
        f"at 2, {b8['efficiency']*100:.0f} % at 8) because of the serial partition/stitch steps, the cost "
        f"of copying tiles to and from worker processes, and memory bandwidth shared by all cores. "
        f"Going from 8 to 16 workers adds only a little "
        f"({lrow('bilateral', 16)['speedup']:.2f}×): the machine has 8 physical cores, and the second "
        f"hardware thread of each core shares its execution units.")
    doc.p(
        f"**Lightweight work does not benefit.** OpenCV's Canny and Gaussian blur are highly "
        f"optimised (SIMD) and process the whole 67-megapixel image in about "
        f"{lrow('canny', mode='sequential')['time_s']:.2f} s. Sending the tiles to other processes "
        f"and collecting the results costs more than that, so the parallel versions are slower "
        f"(speedup below 1). Distributing work only pays off when the computation per byte moved is "
        f"large enough: the compute-to-communication ratio.")
    doc.p(
        f"**Amdahl's law limits the end-to-end gain.** In the full framework the parallel phase sped "
        f"up {d8['processing_speedup']:.2f}× with {d8['workers']} workers "
        f"({d1['processing_s']:.2f} s → {d8['processing_s']:.2f} s), but the end-to-end time improved "
        f"{d8['speedup']:.2f}× ({dseq['time_s']:.2f} s sequential → {d8['time_s']:.2f} s). Decoding the "
        f"uploaded PNG and partitioning ({d8['partition_s']:.2f} s) and aggregation "
        f"({d8['aggregate_s']:.2f} s) are serial, and HTTP transfer and polling add about "
        f"{overhead_8:.2f} s. Even with infinitely many workers the end-to-end speedup could not "
        f"exceed about {bound:.1f}× for this job. Larger images, heavier operations, or "
        f"parallelising the partition and aggregation steps would raise this bound.")
    doc.p(
        "**Resource utilisation.** Peak memory grows with the number of workers because every "
        "process holds its own copies of the tiles it is working on; with 512 px tiles this stays "
        "around 1–2 GB for a 192 MB image. In the full framework Redis holds each job's input and "
        "output tiles (about twice the raw image size) until aggregation, after which they are "
        "deleted. The Redis deployment is therefore given a 3 GB memory limit (enough for four concurrent 67-megapixel jobs).")
    doc.p(
        f"**Fault tolerance cost.** In the fault demonstration the job still finished with an "
        f"identical result. The only cost was the lease timeout (5 s locally, 10 s on Kubernetes) "
        f"before the lost tile was retried. A shorter lease recovers faster but risks retrying "
        f"tiles that are only slow.")

    # ---------------------------------------------------------------- 9 tests
    doc.h("9. Testing")
    doc.p(
        f"The project has {n_tests} automated tests (pytest). They run against an in-memory Redis "
        f"(fakeredis) with the same Lua scripts, so they need no external services.")
    groups = {
        "test_tiling": "Partitioning and reconstruction",
        "test_store": "Reliable queue (claim, complete, lease expiry, retries)",
        "test_pipeline": "End-to-end through the HTTP API, fault recovery, autoscaler policy"}
    rows = []
    for f, area in groups.items():
        names = [n.replace("_", " ").replace("[", " [") for g, n in tests if g == f]
        if names:
            rows.append([f"**{area}**\n`tests/{f}.py`", str(len(names)), "; ".join(names)])
    if rows:
        doc.table(["Area", "Tests", "What is checked (all passed)"], rows,
                  caption="Automated tests", size=9, widths=[4.6, 1.3, 10.4])
    doc.figure(SHOTS / "term_01_tests.png", f"Test run: {test_summary or 'all tests passed'}",
               max_height=7.5)

    # ---------------------------------------------------- 10 kubernetes + AWS
    kubernetes_section(doc)
    doc.h("10.6 Mapping to AWS Services", 2)
    doc.p("The same design maps directly onto managed AWS services covered in the course:")
    doc.table(["Component", "Local / Minikube", "AWS equivalent"],
              [["Container registry", "Minikube Docker daemon", "Amazon ECR"],
               ["Orchestration", "Minikube", "Amazon EKS (or ECS on Fargate)"],
               ["Task queue + store", "Redis Deployment", "Amazon ElastiCache for Redis (Multi-AZ); "
                                                          "large images in Amazon S3"],
               ["Worker scaling", "Queue autoscaler / HPA / KEDA",
                "KEDA or HPA on EKS + Cluster Autoscaler / Karpenter for nodes"],
               ["Ingress", "NodePort 30080", "Application Load Balancer (AWS Load Balancer Controller)"],
               ["Permissions", "Kubernetes RBAC", "RBAC + IAM Roles for Service Accounts (IRSA)"],
               ["Monitoring", "/metrics endpoint", "Amazon CloudWatch (Container Insights)"],
               ["Batch alternative", "—", "AWS Lambda per tile triggered by SQS for bursty workloads"]],
              caption="Mapping to AWS", widths=[3.4, 4.6, 7.6])

    # ------------------------------------------------------- 11 limitations
    doc.h("11. Limitations and Future Work")
    doc.bullets([
        "**Serial stages.** Upload decoding, partitioning and aggregation run in one process and cap "
        "the end-to-end speedup. Workers could read their tile regions directly from object storage "
        "(S3) and write results in place, removing both serial steps.",
        "**Redis as a data store.** Tiles pass through Redis memory, which limits image size to the "
        "Redis memory limit. Storing pixels in S3 or a shared volume and keeping only metadata in "
        "Redis would remove this limit.",
        "**Single Redis instance.** Redis is a single point of failure in this deployment. A managed "
        "replicated service (ElastiCache Multi-AZ) or Redis Sentinel would address this.",
        "**Canny is not exactly tile-local.** Its hysteresis stage can follow edges further than "
        "any finite halo, so a tiny fraction of pixels can differ.",
        "**Predictive scaling.** Scaling reacts to queue length. Scaling from the size of incoming "
        "jobs, or pre-warming pods, would reduce start-up latency [4].",
        "**GPU workers.** Heavy operations could use CUDA-enabled OpenCV on GPU node pools."])

    # ------------------------------------------------------- 12 conclusion
    doc.h("12. Conclusion")
    doc.p(
        f"The project delivers a working cloud-native framework for parallel image processing. A "
        f"large image is partitioned into halo tiles, scheduled through an atomic Redis-based "
        f"queue, processed by stateless parallel workers and reconstructed into an output identical "
        f"to whole-image processing. The framework tolerates worker failures through leases, "
        f"heartbeats and idempotent completion, scales workers from queue length, and is packaged "
        f"for Docker and Kubernetes. Measurements show a {b8['speedup']:.2f}× speedup on 8 workers "
        f"for CPU-heavy work and clearly show the two classic limits of parallel computing: the "
        f"compute-to-communication ratio (light operations do not benefit) and Amdahl's law (serial "
        f"stages bound the end-to-end gain). These results show which workloads benefit from "
        f"cloud-native parallelism and where to optimise next.")

    # -------------------------------------------------------------- references
    doc.h("References")
    refs = [
        "Towards an Adaptive Runtime System for Cloud-Native HPC, 2026. arXiv:2603.14630.",
        "An Elastic Job Scheduler for HPC Applications on the Cloud, SC25, 2025. IEEE Xplore 11358356.",
        "Serverless Data Pipeline Architecture Supporting Distributed Machine Learning on Fog "
        "Devices, Computer Communications, 2026.",
        "A Kubernetes-Based Scheme for Efficient Resource Allocation in Containerized Workflow, "
        "Future Generation Computer Systems, 2025.",
        "Running Cloud-Native Workloads on HPC with High-Performance Kubernetes, 2024. arXiv:2409.16919.",
        "G. M. Amdahl, Validity of the single processor approach to achieving large scale computing "
        "capabilities, AFIPS Conference Proceedings, 1967.",
        "J. Canny, A Computational Approach to Edge Detection, IEEE TPAMI, 1986.",
        "C. Tomasi and R. Manduchi, Bilateral Filtering for Gray and Color Images, ICCV, 1998.",
        "OpenCV documentation, https://docs.opencv.org/4.x/",
        "Redis documentation: Lua scripting and the reliable queue pattern, https://redis.io/docs/",
        "Kubernetes documentation: Deployments, RBAC, Horizontal Pod Autoscaling, https://kubernetes.io/docs/",
        "FastAPI documentation, https://fastapi.tiangolo.com/"]
    for i, r in enumerate(refs, 1):
        doc.p(f"[{i}] {r}", size=10, after=3)

    # -------------------------------------------------------------- appendix
    doc.h("Appendix A: Project Structure")
    doc.code(
        "cloud proj/\n"
        "├── imgframe/          core package\n"
        "│   ├── config.py      settings from environment variables\n"
        "│   ├── ops.py         OpenCV operations + halo sizes\n"
        "│   ├── tiling.py      plan/split/reconstruct, tile codec\n"
        "│   ├── store.py       Redis data model + reliable queue (Lua)\n"
        "│   ├── pipeline.py    partition / process / assemble stages\n"
        "│   └── sample.py      deterministic synthetic test images\n"
        "├── api/               FastAPI service + web UI (index.html)\n"
        "├── worker/            worker process\n"
        "├── autoscaler/        queue-length autoscaler\n"
        "├── benchmark/         bench_local, bench_distributed, plots, results/\n"
        "├── k8s/               Kubernetes manifests (+ optional HPA, KEDA)\n"
        "├── scripts/           run_local, load_test, kill_worker, install_k8s_tools,\n"
        "│                      build_redis, capture_screenshots, capture_k8s\n"
        "├── tests/             pytest suite\n"
        "├── docs/              report, guide, screenshots, logs\n"
        "├── Dockerfile, docker-compose.yml, requirements*.txt, README.md")
    doc.h("Appendix B: Configuration and API Reference")
    doc.table(["Variable", "Default", "Meaning"],
              [["REDIS_URL", "redis://localhost:6379/0", "Redis connection"],
               ["TILE_SIZE", "512", "Default tile edge in pixels"],
               ["LEASE_SECONDS", "15 (10 on Kubernetes)", "Time a claimed tile may go without a heartbeat"],
               ["HEARTBEAT_SECONDS", "3 (2 on Kubernetes)", "Lease renewal interval"],
               ["MAX_RETRIES", "3", "Attempts per tile before the job fails"],
               ["WORKER_PROCS", "1", "Process-pool slots per worker"],
               ["OPENCV_THREADS", "1", "OpenCV internal threads per process"],
               ["CHAOS_CRASH_RATE", "0", "Probability that a worker crashes mid-tile (testing)"],
               ["RESULT_TTL_SECONDS", "900", "How long a finished image is kept"],
               ["REDIS_MEMORY_BUDGET", "0.9", "Fraction of Redis maxmemory usable before new jobs get 503"],
               ["MAX_CONCURRENT_UPLOADS", "2", "Uploads partitioned at the same time (bounds API memory)"],
               ["MIN/MAX_WORKERS, TASKS_PER_WORKER", "1 / 8, 16", "Autoscaler policy"]],
              caption="Configuration", widths=[5.4, 3.8, 6.4])

    out = DOCS / "Project_Report.docx"
    doc.save(out)
    return out


def _fig_or_placeholder(doc, path: Path, caption: str, how: str, **kw):
    if path.exists():
        doc.figure(path, caption, **kw)
    else:
        doc.placeholder_figure(caption, how)


def kubernetes_section(doc):
    doc.h("10. Deployment on Kubernetes and Mapping to AWS")
    doc.p(
        "The framework was deployed on a single-node Kubernetes cluster created with Minikube "
        "(v1.37, Kubernetes v1.34, Docker driver, 8 CPUs and 8 GB of memory allocated) on the "
        "development machine. Each worker pod is limited to 1 CPU, so the number of pods is the "
        "degree of parallelism.")
    doc.h("10.1 Deployment", 2)
    doc.code(
        "minikube start --driver=docker --cpus=8 --memory=8g\n"
        "eval $(minikube docker-env)           # build straight into Minikube's Docker\n"
        "docker build -t imgframe:latest .\n"
        "kubectl apply -f k8s/\n"
        "kubectl -n imgframe get pods -o wide   # redis, api, workers, autoscaler\n"
        "# Web UI: http://$(minikube ip):30080")
    _fig_or_placeholder(doc, SHOTS / "k8s_01_deployment.png",
                        "The deployment running on Minikube: Deployments, Services and pods",
                        "run: kubectl -n imgframe get pods -o wide")
    _fig_or_placeholder(doc, SHOTS / "k8s_02_ui.png",
                        "The web UI served by the API pod through the NodePort Service. The workers "
                        "listed are Kubernetes pods.", "open http://$(minikube ip):30080")

    doc.h("10.2 Dynamic Scaling", 2)
    tl_path = RESULTS / "k8s_scaling_timeline.json"
    if tl_path.exists():
        tl = json.loads(tl_path.read_text())
        busy = [s for s in tl if s["pending"] + s["processing"] > 0]
        t_load = busy[0]["t"] if busy else 0
        up = next((s["t"] for s in tl if s["spec"] > 1), None)
        peak = max(s["ready"] for s in tl)
        t_peak = next(s["t"] for s in tl if s["ready"] == peak)
        t_empty = next((s["t"] for s in tl if s["t"] > t_peak and s["pending"] + s["processing"] == 0), None)
        down = next((s["t"] for s in tl if t_empty and s["t"] > t_empty and s["spec"] == 1), None)
        doc.p(
            f"Four 67-megapixel jobs (1024 tiles in total) were submitted at once with "
            f"`scripts/load_test.py` while the worker Deployment was at its minimum of one pod. "
            f"The autoscaler saw the queue grow and raised the replica count "
            f"{'%.0f s' % (up - t_load) if up is not None else ''} after the first tiles were queued. "
            f"Kubernetes had **{peak} worker pods** ready {t_peak - t_load:.0f} s after the load "
            f"started. When the queue emptied the pods stayed for the 30-second cool-down and "
            f"were then scaled back to one" + (f" ({down - t_empty:.0f} s after the queue emptied)."
                                             if down else "."))
    _fig_or_placeholder(doc, SHOTS / "k8s_scaling_timeline.png",
                        "Queue depth and worker pods over time during the autoscaling test",
                        "run scripts/capture_k8s.py --only scaling")
    _fig_or_placeholder(doc, SHOTS / "k8s_04_autoscaler.png",
                        "Autoscaler decisions (scale up on demand, scale down after the cool-down)",
                        "run: kubectl -n imgframe logs deploy/autoscaler")
    _fig_or_placeholder(doc, SHOTS / "k8s_03_scaling.png",
                        "Load test output and the worker pods at peak", "run: kubectl -n imgframe get pods -w")

    doc.h("10.3 Fault Tolerance on Kubernetes", 2)
    doc.p(
        "Two kinds of failure were injected while jobs were running. In the first, a worker's "
        "container was killed with SIGKILL on the node, as when a process crashes or is "
        "OOM-killed. Its tile lease expired and the reaper requeued the tile, so the load test "
        "reports a retry. Kubernetes restarted the container (RESTARTS = 1), and all jobs "
        "completed. In the second, a worker pod was deleted with `kubectl delete pod`, as happens "
        "during scale-down, eviction or a rolling update. The pod received SIGTERM, its worker "
        "handed the in-flight tile straight back to the queue without waiting for the lease, and "
        "the Deployment created a replacement pod immediately.")
    _fig_or_placeholder(doc, SHOTS / "k8s_05_crash.png",
                        "Worker container killed mid-job: tile retried, container restarted by Kubernetes",
                        "kill a worker container during a load test")
    _fig_or_placeholder(doc, SHOTS / "k8s_06_delete_pod.png",
                        "Worker pod deleted mid-job: graceful hand-back and an immediate replacement pod",
                        "run: kubectl -n imgframe delete pod <worker-pod>")

    doc.h("10.4 Performance on Kubernetes", 2)
    kb = RESULTS / "k8s_bilateral.json"
    if kb.exists():
        rows_k = json.loads(kb.read_text())["rows"]
        seq = rows_k[0]
        pods = [r for r in rows_k if r["mode"] != "sequential"]
        best = max(pods, key=lambda r: r["workers"])
        doc.p(
            "The full-framework benchmark was repeated against the cluster: the autoscaler was "
            "paused and the worker Deployment scaled to 1, 2, 4 and 8 pods by hand. Images were "
            "uploaded through the NodePort Service. The sequential baseline is the same operation "
            "on one core of the host.")
        doc.table(["Worker pods", "End-to-end (s)", "Speedup", "Upload+partition (s)",
                   "Parallel phase (s)", "Phase speedup", "Aggregation (s)"],
                  [["sequential" if r["mode"] == "sequential" else str(r["workers"]),
                    fmt(r["time_s"], 2), fmt(r["speedup"]), fmt(r["partition_s"], 2),
                    fmt(r["processing_s"], 2), fmt(r["processing_speedup"]), fmt(r["aggregate_s"], 2)]
                   for r in rows_k],
                  caption="Kubernetes results, bilateral filter on an 8192² image", size=8.5,
                  widths=[2.2, 2.3, 1.8, 2.6, 2.4, 2.1, 2.2])
        doc.p(
            f"With {best['workers']} pods the parallel phase ran {best['processing_speedup']:.2f}× "
            f"faster than sequential and the whole job {best['speedup']:.2f}× faster "
            f"({seq['time_s']:.2f} s → {best['time_s']:.2f} s). The pattern matches the local "
            f"measurements in Section 8: near-linear gains in the parallel phase, bounded overall "
            f"by the serial upload, partition and aggregation steps.")
    _fig_or_placeholder(doc, RESULTS / "k8s_bilateral.png",
                        "Kubernetes: where the time goes, and speedup versus worker pods",
                        "run scripts/capture_k8s.py --only bench")

    doc.p(
        "**Lessons from deployment.** The first runs on the cluster exposed a problem that the "
        "local tests had not: every finished job kept its full-resolution result (about 200 MB of "
        "raw pixels for an 8192² image) in Redis for an hour. After a dozen demonstration jobs "
        "Redis reached its memory limit and refused writes. The workers' status updates then "
        "failed, crashing the workers into CrashLoopBackOff, and the API pod was OOM-killed while "
        "several large uploads waited. Five changes fixed it, and the measurements in this section "
        "were taken afterwards: (1) admission control: the API estimates a job's Redis footprint "
        "and answers HTTP 503 with Retry-After when the budget would be exceeded; (2) at most two "
        "uploads are partitioned at once, bounding API memory; (3) results expire after 15 "
        "minutes and `DELETE /jobs/{id}` frees them immediately; (4) workers survive Redis errors "
        "in their status loop; (5) clients retry on 503. Back-pressure of this kind is standard "
        "practice for cloud services and is the behaviour expected from a managed queue.")

    doc.h("10.5 Docker Compose", 2)
    doc.p(
        "The same image also runs with Docker Compose (Redis, API and a scalable number of worker "
        "containers). Killing a worker container mid-job with `docker kill` gives the same "
        "lease-based recovery.")
    _fig_or_placeholder(doc, SHOTS / "compose_01.png",
                        "Docker Compose: four worker containers, one killed mid-job, jobs still complete",
                        "run: docker compose up -d --scale worker=4")


# ======================================================================
# GUIDE
# ======================================================================
def build_guide():
    doc = Doc()
    doc.p(TITLE, bold=True, size=18, color=ACCENT, after=2)
    doc.p("How to Run and Demonstrate the Project", size=14, after=2)
    doc.p(f"{COURSE} · step-by-step guide for the team", italic=True, size=10,
          color=RGBColor(0x47, 0x55, 0x69), after=12)

    doc.note("Everything lives in the folder **~/Downloads/cloud proj** (note the space in the name: "
             "always put the path in quotes). Commands are typed in a terminal. Lines starting with `#` "
             "are comments; do not type them.", label="Read first")

    doc.h("Overview: which way to run it?")
    doc.table(["Option", "Needs", "Shows", "Time"],
              [["A. Local (no Docker)", "Nothing extra: already set up on this laptop",
                "Everything: UI, parallel workers, fault tolerance, benchmarks", "1 min"],
               ["B. Docker Compose", "Docker", "The same, as real containers", "5 min"],
               ["C. Kubernetes (Minikube)", "Docker + Minikube",
                "Pods, autoscaling, self-healing: the cloud-native part", "15 min"]],
              caption="Ways to run the project", widths=[3.6, 4.6, 5.6, 1.8])
    doc.p("For the review, **show C (Kubernetes) and keep A ready as a backup**. Rehearse once with "
          "the checklist in section 0. Section 5 is a ready-made 10-minute demo script. Every "
          "command in this guide was run and checked on this laptop.")
    doc.note("Options A and B both use port 8000, so run **one at a time**: stop one (Ctrl+C, or "
             "`docker compose down`) before starting the other. Option C uses its own address and can "
             "run alongside either.", label="One at a time")

    # ------------------------------------------------------------ 0 checklist
    doc.h("0. Before Every Demo: 2-Minute Checklist")
    doc.numbered([
        "Open a terminal and go to the project folder: `cd ~/Downloads/\"cloud proj\"`",
        "Check Docker access: `docker ps` must print a header line (CONTAINER ID …). If it says "
        "**permission denied**, log out of Ubuntu and log back in once (needed one time after the "
        "install), then try again.",
        "Check the code: `.venv/bin/python -m pytest -q` must end with **26 passed**.",
        "For Kubernetes: `minikube start` (about 30 s), then `kubectl -n imgframe get pods` must show "
        "**4 pods Running** (redis, api, autoscaler, one worker).",
        "Note the Kubernetes UI address: `echo http://$(minikube ip):30080` (normally "
        "http://192.168.49.2:30080) and open it in Firefox.",
        "Copy commands from this guide's boxes. Lines in one box can be pasted together; the "
        "terminal runs them one after another.",
        "Open these two PDFs in a viewer so you can show figures quickly: "
        "`docs/Project_Report.pdf` and this guide."])

    # ------------------------------------------------------------ 1 setup
    doc.h("1. One-time Setup")
    doc.h("1.1 On this laptop (already done)", 2)
    doc.p("The Python environment (`.venv`), a local Redis server (`.tools/bin/redis-server`), Docker, "
          "Minikube, kubectl and the Minikube cluster with the app deployed are all already "
          "installed. The only thing left is to **log out and back in once** so your user can use "
          "Docker without sudo. Then do the checklist in section 0.")
    doc.h("1.2 On another computer (Ubuntu / Linux)", 2)
    doc.code("git clone https://github.com/GHOSTALERT/cloud-native-ing-proc.git \"cloud proj\"\n"
             'cd "cloud proj"\n'
             "sudo apt install -y python3-venv python3-pip build-essential\n"
             "python3 -m venv .venv\n"
             ".venv/bin/pip install -r requirements-dev.txt\n"
             "bash scripts/build_redis.sh             # builds redis-server into .tools/bin (1-2 min)\n"
             ".venv/bin/python scripts/make_samples.py   # creates samples/synthetic_8192.png (not in git)\n"
             ".venv/bin/python -m pytest -q           # expect: 26 passed")
    doc.p("On Windows, use WSL2 (Ubuntu) and follow the same steps. Replace `~/Downloads/\"cloud "
          "proj\"` in this guide with wherever you cloned the project.")
    doc.h("1.3 Docker, Minikube and kubectl (for options B and C)", 2)
    doc.p("**Already installed on this laptop** (Docker 29, Minikube v1.37, kubectl v1.34). On another "
          "Ubuntu machine, install everything with one command (asks for your password):")
    doc.code("sudo bash scripts/install_k8s_tools.sh")
    doc.note("After installing, **log out and log back in once** (or reboot) so your user can run "
             "`docker` and `minikube` without sudo. Until then they fail with `permission denied`; as "
             "a stop-gap you can wrap a command as `sg docker -c \"...\"`.",
             fill="FEF3C7", label="Important")
    doc.p("Then create the cluster and deploy the app once (section 4.1, \"first time\").")

    # ------------------------------------------------------------ 2 local
    doc.h("2. Option A: Run Locally (no Docker)")
    doc.h("2.1 Start the system and use the web UI", 2)
    doc.p("**Terminal 1:**")
    doc.code('cd ~/Downloads/"cloud proj"\n'
             ".venv/bin/python scripts/run_local.py --workers 4")
    doc.p("Wait for `API ready at http://127.0.0.1:8000`, then open **http://127.0.0.1:8000** in "
          "Firefox. Leave terminal 1 running (it stops with **Ctrl+C**). In the browser:")
    doc.numbered([
        "The right-hand panel shows **4 workers** (green dots) and an empty queue.",
        "Click **Browse**, choose `samples/astronaut_2048.png`, operation **canny**, tile size **256**, "
        "then **Process**. The original and the edge map appear side by side.",
        "Choose `samples/synthetic_8192.png` (a 67-megapixel image), operation **bilateral**, tile "
        "size **512**, **Process**. Watch the progress bar and the tiles counter while the four "
        "workers share 256 tiles. Point out the timings table and the per-worker tile counts.",
        "Open **http://127.0.0.1:8000/docs** to show the REST API, and "
        "**http://127.0.0.1:8000/metrics** for live metrics."])
    doc.h("2.2 Fault-tolerance demo (kill a worker during a job)", 2)
    doc.p("**Way 1, live in the UI** (with terminal 1 from 2.1 still running). Open **terminal 2** and "
          "paste these lines. The first waits until the workers are busy and then kills one worker "
          "process with SIGKILL; the second submits four large bilateral jobs (8192 × 8192):")
    doc.code('cd ~/Downloads/"cloud proj"\n'
             "bash scripts/kill_worker.sh local &\n"
             ".venv/bin/python scripts/load_test.py")
    doc.p("Expected: `killing worker process …`, then the **retries** column changes from 0 to **1** "
          "and all four jobs finish "
          "(`4/4`); one job shows `1 retries`. In the browser, the killed worker's dot turns red about "
          "10 s later and **Retries = 1**. To get 4 workers back, press Ctrl+C in terminal 1 and start "
          "run_local.py again.")
    doc.p("**Way 2, fully automatic** (prints a pixel-by-pixel check). First press **Ctrl+C in "
          "terminal 1** (it uses the same port), then:")
    doc.code(".venv/bin/python scripts/run_local.py --workers 4 --demo --kill-worker --op bilateral --size 4096")
    doc.p("Expected: `!! killed worker pid ... with SIGKILL at N/64 tiles`, the counter pauses at "
          "63/64 for about 5 s (the lease expiring), then `done 64/64 tiles, retries=1` and "
          "`pixel agreement with sequential whole-image bilateral: 100.0000%`.")
    doc.h("2.3 Run the benchmarks", 2)
    doc.p("Stop run_local.py first (the benchmarks start their own workers):")
    doc.code("# compute engine: sequential vs 1/2/4/8/16 processes (~3 min)\n"
             ".venv/bin/python -m benchmark.bench_local --size 8192 --ops bilateral canny gaussian_blur\n"
             "\n"
             "# full framework through API + Redis with 1/2/4/8 workers (~3 min)\n"
             ".venv/bin/python -m benchmark.bench_distributed --workers 1 2 4 8 --op bilateral --size 8192")
    doc.p("Results: `benchmark/results/results.csv`, `charts.png`, `distributed_bilateral.csv` and "
          "`distributed_bilateral.png`. Close other heavy programs first; numbers vary a little "
          "between runs. In a review it is usually enough to show the charts in the report (§8).")

    # ------------------------------------------------------------ 3 compose
    doc.h("3. Option B: Docker Compose")
    doc.p("Use a **new terminal** (not one where you ran `eval $(minikube docker-env)`), and stop "
          "Option A first. Then step by step:")
    doc.numbered([
        "Start Redis, the API and 4 workers in the background (first build takes a few minutes):"
        "\n`docker compose up -d --build --scale worker=4`",
        "Check the containers: `docker compose ps` lists `imgframe-redis-1`, `imgframe-api-1` and "
        "`imgframe-worker-1` … `-4`, all running. Open **http://localhost:8000**.",
        "Crash a container during a job: paste the two lines of the box below. Expected: "
        "`killing container imgframe-worker-1`, the progress pauses for about 10 s with one tile "
        "in progress (the lease expiring), then **retries 1** and both jobs done.",
        "Scale up: `docker compose up -d --scale worker=8`, then `docker compose ps` shows 8 workers "
        "(the killed one is started again).",
        "Show logs: `docker compose logs --tail 20 worker`",
        "Stop and remove everything: `docker compose down`"])
    doc.code("# step 3: crash a worker container during a job\n"
             "bash scripts/kill_worker.sh compose &\n"
             ".venv/bin/python scripts/load_test.py --jobs 2")

    # ------------------------------------------------------------ 4 kubernetes
    doc.h("4. Option C: Kubernetes (Minikube)")
    doc.h("4.1 Start the cluster and deploy", 2)
    doc.p("**On this laptop** the cluster exists and the app is deployed, so after a reboot you only "
          "need:")
    doc.code('cd ~/Downloads/"cloud proj"\n'
             "minikube start                                 # starts the existing cluster (~30 s)\n"
             "kubectl -n imgframe get pods                   # 4 pods Running? then go to 4.2")
    doc.p("**First time on a machine, or after changing the code**, build the image inside Minikube "
          "and deploy:")
    doc.code("minikube start --driver=docker --cpus=8 --memory=8g   # first run downloads ~800 MB\n"
             "eval $(minikube docker-env)                    # use Minikube's Docker\n"
             "docker build -t imgframe:latest .\n"
             "kubectl apply -f k8s/\n"
             "kubectl -n imgframe rollout restart deploy     # only needed after rebuilding the image\n"
             "kubectl -n imgframe get pods -w                # wait until all are Running, then Ctrl+C")
    doc.p("Expected pods: `redis-…`, `api-…`, `worker-…` and `autoscaler-…`, all **Running**. With no "
          "work queued the autoscaler keeps a single worker. Open the UI at the address from:")
    doc.code("echo http://$(minikube ip):30080                # normally http://192.168.49.2:30080")
    doc.h("4.2 Show the deployment", 2)
    doc.code("kubectl -n imgframe get all\n"
             "kubectl -n imgframe describe deploy worker     # resources (1 CPU), replicas\n"
             "kubectl -n imgframe logs deploy/api --tail 20")
    doc.h("4.3 Autoscaling demo", 2)
    doc.p("Open three terminals side by side (in each, first `cd ~/Downloads/\"cloud proj\"`):")
    doc.code("# terminal 1: watch the pods\n"
             "kubectl -n imgframe get pods -w\n"
             "\n"
             "# terminal 2: watch the autoscaler's decisions\n"
             "kubectl -n imgframe logs -f deploy/autoscaler\n"
             "\n"
             "# terminal 3: submit 6 large bilateral jobs at once (1536 tiles)\n"
             ".venv/bin/python scripts/load_test.py --api http://$(minikube ip):30080 --jobs 6")
    doc.p("Expected, within about 5 s: terminal 2 prints `queue depth …: scaling up 1 -> 8`, "
          "terminal 1 shows 7 new worker pods (ContainerCreating → Running), and terminal 3 shows "
          "**workers** rising to 8 and **pending** falling to 0 (about 15–20 s in total). About 30 s "
          "after the queue empties, terminal 2 prints `scaling down 8 -> 1` and the extra pods "
          "terminate. The browser UI shows the worker list growing and shrinking too.")
    doc.h("4.4 Self-healing / fault-tolerance demo", 2)
    doc.p("Keep terminals 1 and 2 from 4.3 open. The helper `scripts/kill_worker.sh` waits until the "
          "workers are busy with tiles and then breaks the oldest worker pod (the one certainly "
          "holding a tile), so the timing is automatic.")
    doc.p("**a) Crash a worker** (like a process crash or out-of-memory kill). In terminal 3 paste:")
    doc.code("bash scripts/kill_worker.sh k8s &\n"
             ".venv/bin/python scripts/load_test.py --api http://$(minikube ip):30080 --jobs 6")
    doc.p("Expected: the script prints `crashing pod worker-…` and the pod list shows **RESTARTS 1** "
          "on that pod: Kubernetes restarted the crashed container. In terminal 3 the last tile waits "
          "about 10 s (the lease expiring), then **retries 1** and all 6 jobs finish (`6/6`).")
    doc.p("**b) Delete a worker pod** (like scale-down, eviction or a rolling update). Wait until "
          "the cluster has scaled back down (terminal 2 prints `scaling down 8 -> 1`), then paste:")
    doc.code("bash scripts/kill_worker.sh k8s-delete &\n"
             ".venv/bin/python scripts/load_test.py --api http://$(minikube ip):30080 --jobs 6")
    doc.p("Expected: `pod \"worker-…\" deleted`, terminal 1 shows the old pod **Terminating** and a "
          "new one appearing, and the load test finishes with **0 retries**. The pod received "
          "SIGTERM and its worker handed the in-flight tile straight back to the queue.")
    doc.p("If asked what the helper does: it polls `/metrics` until 200+ tiles are pending and some "
          "are in progress, then runs these standard commands:")
    doc.code("kubectl -n imgframe get pods -l app=worker --sort-by=.metadata.creationTimestamp\n"
             "minikube ssh -- docker ps -q --filter label=io.kubernetes.pod.name=<worker-pod> \\\n"
             "                         --filter label=io.kubernetes.container.name=worker\n"
             "minikube ssh -- docker kill --signal KILL <container-id>      # a) crash\n"
             "kubectl -n imgframe delete pod <worker-pod>                    # b) delete")
    doc.h("4.5 Re-capturing the report's Kubernetes screenshots", 2)
    doc.p("All Kubernetes screenshots, charts and numbers in the report were produced by one script. "
          "To refresh them (about 15 minutes), with the cluster running:")
    doc.code(".venv/bin/python scripts/capture_k8s.py      # deployment, UI, scaling, failures, benchmark, compose\n"
             ".venv/bin/python docs/build_docs.py          # rebuild the report and this guide")
    doc.h("4.6 Clean up", 2)
    doc.code("minikube stop                   # stop the cluster; minikube start brings it back as it was\n"
             "kubectl delete -f k8s/          # (optional) remove the app from the cluster\n"
             "eval $(minikube docker-env -u)  # point docker back at the normal daemon, if you used docker-env")

    # ------------------------------------------------------------ 5 demo script
    doc.page_break()
    doc.h("5. Suggested 10-Minute Review Demo")
    doc.p("Before the review: checklist (section 0), terminals open in the project folder, Firefox on "
          "the Kubernetes UI, the report PDF open.")
    doc.table(["Min", "Show", "Say"],
              [["0–1", "Architecture diagram (report, Figure 1)",
                "Upload → partition → Redis queue → parallel workers → aggregation. Kubernetes runs it; "
                "the autoscaler sizes the worker pool."],
               ["1–2", "`kubectl -n imgframe get pods`, then the UI",
                "One Docker image, four roles. Workers are stateless, so any worker can take any tile."],
               ["2–4", "Canny on the astronaut photo, then bilateral on the 8192² image (both in samples/)",
                "256 tiles shared by the workers. Halos (overlap) make the stitched result identical "
                "to processing the whole image at once."],
               ["4–6", "Section 4.3: load_test.py + `get pods -w` + autoscaler logs",
                "Queue length drives the replica count: ceil(pending / 16), between 1 and 8."],
               ["6–7", "Section 4.4: `kill_worker.sh k8s` with a load test, then `kill_worker.sh "
                       "k8s-delete`",
                "Leases + heartbeats: the lost tile is requeued after the lease expires; completion is "
                "idempotent, so nothing is counted twice. Kubernetes restarts / replaces the pod."],
               ["7–9", "Benchmark charts (report §8 and §10.4)",
                "Bilateral: about 4× on 8 workers. Canny is faster sequentially: the communication cost "
                "outweighs the compute. Serial stages limit end-to-end speedup (Amdahl's law)."],
               ["9–10", "Limitations / future work", "S3 for pixels, parallel aggregation, ElastiCache, GPU workers."]],
              caption="Demo script", widths=[1.3, 5.6, 8.7], size=9)
    doc.p("**Backup plan:** if Minikube misbehaves, run Option A (section 2): it shows the same UI, "
          "parallel workers and fault tolerance in under a minute.")

    # ------------------------------------------------------------ 6 Q&A
    doc.h("6. Likely Questions and Short Answers")
    qa = [
        ("Why split into tiles and not rows?",
         "Square tiles keep the halo overhead small (perimeter / area) and give many more tasks than "
         "workers, which balances the load automatically."),
        ("What is the halo?",
         "Extra pixels around each tile so that filters near the border see their real neighbours; "
         "after processing, only the core is kept. Without it you get visible seams."),
        ("How do you make sure two workers don't take the same tile?",
         "Claiming is a Lua script that runs atomically in Redis (LMOVE pending → processing plus a lease)."),
        ("What happens if a worker dies?",
         "Its lease stops being renewed; the reaper requeues the tile when the lease expires; after 3 "
         "failed attempts the job is marked failed. Kubernetes also recreates the pod."),
        ("Can a tile be processed twice?",
         "Yes, in rare cases (at-least-once). But results are recorded with a set of completed tile "
         "indices, so progress is counted exactly once and the output is unchanged."),
        ("Why isn't the speedup 8× with 8 workers?",
         "Serial parts (decode, partition, aggregation), data movement and shared memory bandwidth; "
         "past 8 workers the machine only has 8 physical cores. That is Amdahl's law."),
        ("Why is Canny slower in parallel?",
         "It takes about 0.2 s for the whole image; moving 192 MB of tiles costs more than that."),
        ("Why Redis and not Kafka/RabbitMQ/SQS?",
         "Redis gives a queue and a fast shared store in one service, with atomic scripts for the "
         "lease logic. On AWS this becomes ElastiCache, or SQS for the queue plus S3 for the pixels."),
        ("How does the autoscaler get permission to scale?",
         "A ServiceAccount bound to a Role that may only get/patch the scale of the worker Deployment "
         "(least privilege)."),
        ("How would this run on AWS?",
         "EKS for Kubernetes, ECR for the image, ElastiCache for Redis, S3 for images, KEDA + Karpenter "
         "for scaling, an ALB for ingress and CloudWatch for monitoring.")]
    for q, a in qa:
        doc.p(f"**Q: {q}**", after=1)
        doc.p(a, after=7)

    # ------------------------------------------------------------ 7 troubleshooting
    doc.h("7. Troubleshooting")
    doc.table(["Problem", "Fix"],
              [["run_local.py says `Port 8000 … already in use`",
                "Another run_local.py or docker compose is still running: press Ctrl+C in its terminal "
                "or run `docker compose down`. Or use `--port 8001 --redis-port 6391`."],
               ["A crash demo ends with **0 retries**",
                "Rare: the kill landed between two tiles. Just run the two lines again. (With "
                "`k8s-delete`, 0 retries is the expected result: the tile is handed back.)"],
               ["`kill_worker.sh` keeps printing `waiting for workers…`",
                "No load is running yet: start the load test. If the API runs on another port, set "
                "`API=http://127.0.0.1:8001 bash scripts/kill_worker.sh local`."],
               ["`permission denied` from docker or minikube",
                "Log out of Ubuntu and back in once (group membership from the install), or wrap the "
                "command: `sg docker -c \"...\"`."],
               ["`docker compose` builds or runs inside Minikube",
                "That terminal ran `eval $(minikube docker-env)`: open a new terminal, or run "
                "`eval $(minikube docker-env -u)`."],
               ["Browser shows nothing at 127.0.0.1:8000", "Wait for `API ready`; check the terminal for errors."],
               ["`.venv/bin/python: No such file`", "You are not in the project folder: `cd ~/Downloads/\"cloud proj\"`."],
               ["Pods stuck in `ErrImageNeverPull`",
                "The image was built outside Minikube: run `eval $(minikube docker-env)` and build again."],
               ["Pods stuck in `Pending`", "Not enough CPU: `minikube start --cpus=8` or lower MAX_WORKERS in "
                                           "k8s/40-autoscaler.yaml."],
               ["Cluster still runs old code after a rebuild",
                "Build inside Minikube (`eval $(minikube docker-env)` then `docker build`), then "
                "`kubectl -n imgframe rollout restart deploy`. `minikube image load` cannot replace an "
                "image that containers still use."],
               ["Can't open the NodePort URL", "Check `minikube status`; or use `minikube service api -n imgframe --url` "
                                               "and keep it running."],
               ["Upload of a very large image fails on Kubernetes", "Redis memory limit (3 GB): use a smaller image "
                                                                     "or raise maxmemory and the limit in k8s/10-redis.yaml."],
               ["Benchmark numbers differ from the report", "Normal variation; close other programs and re-run."]],
              caption="Common problems", widths=[6, 9.6], size=9)

    doc.h("8. Regenerating Screenshots and these Documents")
    doc.code(".venv/bin/python scripts/capture_screenshots.py   # needs Firefox; re-runs demos & benchmarks\n"
             ".venv/bin/python docs/build_docs.py               # rebuilds Project_Report and this guide")

    out = DOCS / "How_to_Run_and_Demo.docx"
    doc.save(out)
    return out


def to_pdf(paths):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        print("LibreOffice not found; skipping PDF export")
        return
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(DOCS),
                    *map(str, paths)], check=True, capture_output=True, timeout=300)
    for p in paths:
        print("wrote", p.with_suffix(".pdf").relative_to(ROOT))


if __name__ == "__main__":
    outs = [build_report(), build_guide()]
    to_pdf(outs)
    if MISSING:
        print("WARNING: missing images:", *MISSING, sep="\n  ")
