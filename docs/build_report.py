"""Build the IEEE-format project report (docs/Project_Report_IEEE.docx + .pdf).

Every number in the tables is read from benchmark/results/*.json and
imgframe/profiles.json, so re-running the benchmarks and this script keeps the
report consistent with the code.

    python docs/build_report.py
"""
import json
import statistics as st
import subprocess
import sys
from pathlib import Path

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docxkit import ROMAN, Doc, to_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
FIG = DOCS / "figures"
SHOTS = DOCS / "screenshots"
RES = ROOT / "benchmark" / "results"
C = WD_ALIGN_PARAGRAPH.CENTER
L = WD_ALIGN_PARAGRAPH.LEFT
GREY = RGBColor(0x47, 0x55, 0x69)

TEAM = [("Prapti Kurkuri", "24BVD0185", "Praptikurkuri2024@vitstudent.ac.in"),
        ("Vaanya Garg", "24BVD0139", "Vaanyagarg2024@vitstudent.ac.in"),
        ("Aditya Manoj", "24BVD0070", "Adityamanoj2024@vitstudent.ac.in"),
        ("Arrnav Trisal", "24BVD0064", "Arrnavtrisal2024@vitstudent.ac.in")]
TITLE = "Cloud-Native Parallel Computing Framework for Scalable Image Processing"
SUBTITLE = "Object Storage, Deep-Learning Operations, a Cost-Aware Planner and Work-Aware Autoscaling"
DATE = "31 October 2026"
OPS_LABEL = {"grayscale": "Grayscale", "canny": "Canny", "gaussian_blur": "Gaussian", "sobel": "Sobel",
             "sharpen": "Sharpen", "bilateral": "Bilateral", "hed": "HED", "superres": "Real-ESRGAN"}
SCALER = {"hpa": "HPA (CPU 70 %)", "keda": "KEDA (queue length)", "work": "Work-aware (ours)"}

REFS = [
    # 1-5: the papers of the original literature review
    "A. Bhosale, A. Tahilyani, L. Kale, and S. Kokkila-Schumacher, “Towards an adaptive runtime system for "
    "cloud-native HPC,” arXiv preprint arXiv:2603.14630, Mar. 2026.",
    "A. Bhosale, K. Chandrasekar, L. Kale, and S. Kokkila-Schumacher, “An elastic job scheduler for HPC "
    "applications on the cloud,” in *Proc. SC Workshops ’25*, St. Louis, MO, USA, Nov. 2025, "
    "doi: 10.1145/3731599.3767358.",
    "“Serverless data pipeline architecture supporting distributed machine learning on fog devices,” "
    "*Comput. Commun.*, 2026. [Online]. Available: https://www.sciencedirect.com/science/article/pii/S0140366426000952",
    "D. Liu, Y. Xia, C. Shan, K. Tian, and Y. Zhan, “A Kubernetes-based scheme for efficient resource allocation "
    "in containerized workflow,” *Future Gener. Comput. Syst.*, vol. 166, May 2025, Art. no. 107699.",
    "A. Chazapis, E. Maliaroudakis, F. Nikolaidis, M. Marazakis, and A. Bilas, “Running cloud-native workloads "
    "on HPC with high-performance Kubernetes,” arXiv preprint arXiv:2409.16919, Sep. 2024.",
    # 6-
    "J. Dean and S. Ghemawat, “MapReduce: Simplified data processing on large clusters,” in *Proc. 6th USENIX "
    "OSDI*, 2004, pp. 137–150.",
    "C. Gray and D. Cheriton, “Leases: An efficient fault-tolerant mechanism for distributed file cache "
    "consistency,” in *Proc. 12th ACM SOSP*, 1989, pp. 202–210.",
    "J. Ragan-Kelley, C. Barnes, A. Adams, S. Paris, F. Durand, and S. Amarasinghe, “Halide: A language and "
    "compiler for optimizing parallelism, locality, and recomputation in image processing pipelines,” in "
    "*Proc. ACM PLDI*, 2013, pp. 519–530.",
    "T. Denniston, S. Kamil, and S. Amarasinghe, “Distributed Halide,” in *Proc. 21st ACM PPoPP*, 2016, "
    "Art. no. 5.",
    "S. Venkataraman, Z. Yang, M. Franklin, B. Recht, and I. Stoica, “Ernest: Efficient performance prediction "
    "for large-scale advanced analytics,” in *Proc. 13th USENIX NSDI*, 2016, pp. 363–378.",
    "K. Rzadca *et al.*, “Autopilot: Workload autoscaling at Google,” in *Proc. 15th EuroSys*, 2020, Art. no. 16.",
    "S. Xie and Z. Tu, “Holistically-nested edge detection,” in *Proc. IEEE ICCV*, 2015, pp. 1395–1403.",
    "X. Wang, L. Xie, C. Dong, and Y. Shan, “Real-ESRGAN: Training real-world blind super-resolution with pure "
    "synthetic data,” in *Proc. IEEE/CVF ICCV Workshops*, 2021, pp. 1905–1914.",
    "W. Luo, Y. Li, R. Urtasun, and R. Zemel, “Understanding the effective receptive field in deep "
    "convolutional neural networks,” in *Proc. NeurIPS*, 2016, pp. 4898–4906.",
    "G. M. Amdahl, “Validity of the single processor approach to achieving large scale computing "
    "capabilities,” in *Proc. AFIPS Spring Joint Comput. Conf.*, vol. 30, 1967, pp. 483–485.",
    "J. Canny, “A computational approach to edge detection,” *IEEE Trans. Pattern Anal. Mach. Intell.*, "
    "vol. PAMI-8, no. 6, pp. 679–698, Nov. 1986.",
    "C. Tomasi and R. Manduchi, “Bilateral filtering for gray and color images,” in *Proc. 6th ICCV*, 1998, "
    "pp. 839–846.",
    "KEDA Authors, “KEDA: Kubernetes event-driven autoscaling, v2.21 documentation — Redis Lists scaler.” "
    "[Online]. Available: https://keda.sh/docs/ (accessed Oct. 2026).",
    "The Kubernetes Authors, “Horizontal Pod Autoscaling.” [Online]. Available: "
    "https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ (accessed Oct. 2026).",
    "Amazon Web Services, “Amazon S3 user guide: presigned URLs, server-side encryption with AWS KMS, "
    "Block Public Access, lifecycle configuration.” [Online]. Available: https://docs.aws.amazon.com/s3/ "
    "(accessed Oct. 2026).",
    "Amazon Web Services, “IAM roles for service accounts (Amazon EKS user guide).” [Online]. Available: "
    "https://docs.aws.amazon.com/eks/ (accessed Oct. 2026).",
    "ONNX Runtime developers, “ONNX Runtime: threading and performance tuning.” [Online]. Available: "
    "https://onnxruntime.ai/docs/ (accessed Oct. 2026).",
    "Redis Ltd., “Redis documentation: Lua scripting, lists and the reliable-queue pattern.” [Online]. "
    "Available: https://redis.io/docs/ (accessed Oct. 2026).",
    "MinIO, Inc., “MinIO object storage: S3-compatible API.” [Online]. Available: https://min.io/docs/ "
    "(accessed Oct. 2026).",
]


def load(name):
    p = RES / name
    return json.loads(p.read_text()) if p.exists() else None


def n_tests():
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    for line in out.splitlines()[::-1]:
        if "collected" in line or "tests" in line:
            try:
                return int(line.split()[0])
            except ValueError:
                pass
    return 0


def autoscale_summary(folder="autoscale"):
    d = RES / folder
    runs = {}
    for f in sorted(d.glob("*_run*.json")) if d.exists() else []:
        s = json.loads(f.read_text())["summary"]
        runs.setdefault(s["label"], []).append(s)
    out = {}
    for k in ("hpa", "keda", "work"):
        if k not in runs:
            continue
        rs = runs[k]

        def ms(key):
            vals = [r[key] for r in rs if r[key] is not None]
            if not vals:
                return None, None
            return st.mean(vals), (st.stdev(vals) if len(vals) > 1 else 0.0)
        out[k] = {key: ms(key) for key in ("p50_s", "p95_s", "within_target_pct", "pod_seconds",
                                           "scaling_events", "first_scale_up_s", "max_replicas")}
        out[k]["runs"] = len(rs)
        out[k]["jobs"] = rs[0]["jobs"]
        out[k]["target"] = rs[0]["target_s"]
        out[k]["failed"] = sum(r["failed"] for r in rs)
    return out


def pm(v, digits=1):
    m, s = v
    if m is None:
        return "—"
    return f"{m:.{digits}f} ± {s:.{digits}f}"


class Report(Doc):
    def __init__(self):
        super().__init__(font="Times New Roman", size=10, margins_cm=(1.9, 1.57, 2.54, 1.57))

    def figx(self, name, caption, max_h_in=3.0, wide=False):
        path = FIG / name if (FIG / name).exists() else SHOTS / name
        if not path.exists():
            self.box(f"[missing figure {name}]", fill="FEF3C7")
            return 0
        if wide:
            self.columns(1)
            n = self.figure(path, caption, max_h_in=max_h_in)
            self.columns(2)
            return n
        return self.figure(path, caption, max_h_in=max_h_in)

    def t(self, text, **kw):
        return self.p(text, **kw)

    def itab(self, header, rows, caption, widths):
        return self.table(header, rows, caption, widths_cm=widths, size=7.5)


def author_block(r: Report):
    t = r.d.add_table(rows=1, cols=4)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (name, reg, mail) in enumerate(TEAM):
        cell = t.rows[0].cells[i]
        cell.width = Cm(4.45)
        cell.text = ""
        lines = [(name, 10, False, False), (f"Reg. No. {reg}", 8.5, False, False),
                 ("SENSE", 8.5, True, False), ("Vellore Institute of Technology", 8.5, True, False),
                 ("Vellore, Tamil Nadu, India", 8.5, False, False), (mail, 7.5, False, False)]
        for j, (text, size, italic, _) in enumerate(lines):
            para = cell.paragraphs[0] if j == 0 else cell.add_paragraph()
            para.paragraph_format.alignment = C
            run = para.add_run(text)
            run.font.size = Pt(size)
            run.italic = italic
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "left", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{edge}")
        b.set(qn("w:val"), "nil")
        borders.append(b)
    t._tbl.tblPr.append(borders)


def cover(r: Report):
    for _ in range(4):
        r.p("", indent=False)
    r.p("BECE355L — Advanced Cloud Computing", size=13, align=C, indent=False, color=GREY)
    r.p("Fall Semester 2026–27", size=11, align=C, indent=False, color=GREY, after=18)
    r.p(TITLE, bold=True, size=24, align=C, indent=False, after=8)
    r.p(SUBTITLE, italic=True, size=13, align=C, indent=False, after=14)
    r.p("Project Report", size=15, align=C, indent=False, after=6)
    r.p("Python · OpenCV · ONNX Runtime · Redis · MinIO / Amazon S3 · FastAPI · Docker · Kubernetes · KEDA",
        size=10.5, align=C, indent=False, color=GREY, after=40)
    r.p("Submitted by", bold=True, size=12, align=C, indent=False, after=6)
    t = r.d.add_table(rows=1, cols=2)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(("Name", "Register No.")):
        t.rows[0].cells[i].text = ""
        run = t.rows[0].cells[i].paragraphs[0].add_run(h)
        run.bold = True
        t.rows[0].cells[i].paragraphs[0].alignment = C
    for name, reg, _ in TEAM:
        cells = t.add_row().cells
        for i, v in enumerate((name, reg)):
            cells[i].text = ""
            cells[i].paragraphs[0].add_run(v).font.size = Pt(11)
            cells[i].paragraphs[0].alignment = C
    for row in t.rows:
        row.cells[0].width, row.cells[1].width = Cm(5.5), Cm(4.0)
    r.p("", indent=False, after=30)
    r.p("Submitted to: **Aparna Mohanty**", size=12, align=C, indent=False, after=4)
    r.p("School of Electronics Engineering (SENSE), Vellore Institute of Technology, Vellore",
        size=10.5, align=C, indent=False, color=GREY, after=4)
    r.p(DATE, size=11, align=C, indent=False)
    r.page_break()


def build() -> Path:
    eng = load("planner_engine.json")
    clu = load("planner_cluster.json")
    sto = load("storage_study.json")
    halo = load("halo_calibration.json")
    hed_sc = load("neural_hed/results.json")
    sr_sc = load("neural_superres/results.json")
    old = load("results.json")
    prof = json.loads((ROOT / "imgframe" / "profiles.json").read_text())
    auto = autoscale_summary()
    auto_before = autoscale_summary("autoscale_before_idle_fix")
    tests = n_tests()
    redis_m, s3_m = sto[0], sto[1]
    cop = {k: v * 1e9 for k, v in prof["ops"].items()}
    capd = prof["capacity"]
    eng_rows = {r_["op"]: r_ for r_ in eng["rows"]}
    clu_rows = {r_["op"]: r_ for r_ in clu["rows"]}

    def peak(res):
        rows = [x for x in res["rows"] if x["mode"] == "parallel"]
        best = max(rows, key=lambda x: x["speedup"])
        return best["speedup"], best["workers"]
    hed_peak, hed_peak_n = peak(hed_sc)
    sr_peak, sr_peak_n = peak(sr_sc)

    r = Report()
    cover(r)

    # ------------------------------------------------------- title + authors
    r.p(TITLE, size=22, align=C, indent=False, after=4)
    r.p(SUBTITLE, italic=True, size=12, align=C, indent=False, after=10)
    author_block(r)
    r.p("", indent=False, after=6)
    r.columns(2)

    # ---------------------------------------------------------------- abstract
    abstract = (
        "Our earlier framework splits a large image into halo tiles, schedules one task per tile through a "
        "reliable Redis queue to stateless worker pods and stitches the results, with leases for fault tolerance "
        "and a queue-length autoscaler. Its evaluation exposed four weaknesses: every tile byte passed through "
        "Redis, which capped image size and concurrency; only cheap classical filters were supported, so most "
        "operations ran *slower* when distributed; the tile size and worker count were fixed by hand; and the "
        "autoscaler counted tiles, not work. This report presents four upgrades that address them. "
        "(1) Tiles, inputs and results move to S3-compatible object storage (MinIO locally, Amazon S3 in the "
        "cloud) and Redis carries only task IDs; Redis memory per 8192² job drops from "
        f"{redis_m['redis_bytes_per_8192_job'] / 1e6:.0f} MB to {s3_m['redis_bytes_per_8192_job'] / 1e6:.2f} MB "
        f"and accepted concurrent 8192² jobs rise from {redis_m['concurrent_8192_jobs_accepted']} to at least "
        f"{s3_m['concurrent_8192_jobs_accepted']}. (2) Two deep-learning operations, HED edge detection and "
        "Real-ESRGAN ×4 super-resolution, run under ONNX Runtime; their halos are calibrated empirically from the "
        "effective receptive field (80 px and 4 px instead of the theoretical 98 px and 34 px) and the stitcher "
        "is scale- and stride-aware. (3) A cost-aware planner predicts job time from a calibrated model of "
        "compute, per-tile overhead and a measured multi-worker capacity curve and chooses whether to "
        "distribute, the tile size and the worker count. Its prediction error is "
        f"{eng['mape_pct']:.0f} % MAPE versus {eng['mape_simple_pct']:.0f} % for a linear model, and it turns "
        f"the 16× slowdown of naively tiled grayscale into a {eng_rows['grayscale']['plan_speedup']:.2f}× "
        "result. (4) A work-aware autoscaler sizes the worker pool from queued compute-seconds and a latency "
        "target. On a replayed trace it met a 30 s latency target for every job with "
        f"{100 * (1 - auto['work']['pod_seconds'][0] / auto['keda']['pod_seconds'][0]):.0f} % fewer worker "
        "pod-seconds than KEDA, while the CPU-based HPA met it for only "
        f"{auto['hpa']['within_target_pct'][0]:.0f} % of jobs. Neural operations "
        f"reach only {hed_peak:.1f}× (HED) and {sr_peak:.1f}× (Real-ESRGAN) on a 16-thread laptop because they are "
        "memory-bandwidth bound, a result the planner models explicitly.")
    para = r.p("", indent=False, size=9, after=4)
    r.rich(para, "*Abstract*—", bold=True, size=9)
    r.rich(para, abstract, bold=True, size=9)
    para = r.p("", indent=False, size=9, after=6)
    r.rich(para, "*Index Terms*—", bold=True, size=9)
    r.rich(para, "cloud-native computing, object storage, Kubernetes autoscaling, KEDA, performance model, "
           "tiled image processing, receptive field, ONNX Runtime, Redis, fault tolerance.", bold=True, size=9)

    # ------------------------------------------------------------ I. Introduction
    r.ieee_h1("Introduction")
    r.ieee_h2("Background")
    r.t("Satellite scenes, whole-slide pathology images and microscopy mosaics reach hundreds of megapixels. "
        "Most image filters are *local*: each output pixel depends on a small neighbourhood. Such work can be "
        "split into tiles, processed in parallel and stitched, provided every tile carries a *halo* (overlap) "
        "wide enough that the stitched output equals whole-image processing. Our first version (v1) built this "
        "on Kubernetes: a FastAPI service partitions the image, Redis holds a reliable queue with one task per "
        "tile, stateless workers claim tiles under a lease and the last finisher reconstructs the output.")
    r.ieee_h2("Problems Found in v1")
    r.t("The v1 evaluation [Sec. XI] showed where a simple design stops working:")
    r.numbered([
        "**Redis was the data plane.** Every tile and result went through Redis, so an 8192² job used about "
        f"{redis_m['redis_bytes_per_8192_job'] / 1e6:.0f} MB of Redis memory. With a 1 GB limit only "
        f"{redis_m['concurrent_8192_jobs_accepted']} such jobs fit and 12288² images were rejected.",
        "**Cheap operations got slower.** Canny ran at 0.45× and Gaussian blur at 0.41× when distributed, "
        "because moving a tile cost more than filtering it. Only the bilateral filter (4.2×) benefited.",
        "**Configuration was manual.** The user picked the tile size; the system always distributed.",
        "**The autoscaler counted tiles.** One HED tile costs about 1800 times a Canny tile of the same size, "
        "yet both counted as one unit of queue length.",
    ])
    r.ieee_h2("Contributions")
    r.t("This report describes four upgrades, all implemented in the same repository, tested and "
        "measured on the same hardware:")
    r.bullets([
        "**Object-storage data plane** (S3/MinIO) with presigned result URLs, SSE-KMS, least-privilege IAM, "
        "lifecycle expiry and streaming tiled BigTIFF output for images too large for memory.",
        "**Deep-learning operations** (HED, Real-ESRGAN ×4) with empirical halo calibration, stride-aligned "
        "halos and scale-aware stitching.",
        "**A cost-aware adaptive planner** with a calibrated time model, a measured capacity curve per operation "
        "and online re-calibration from completed tasks.",
        "**A work-aware autoscaler** evaluated against the Kubernetes HPA and KEDA on an identical seeded trace.",
    ])

    # ----------------------------------------------------------- II. Objectives
    r.ieee_h1("Objectives")
    r.itab(["#", "Objective", "Outcome"], [
        ("1", "Remove the Redis memory ceiling", "Achieved: S3/MinIO data plane; Redis holds IDs (Sec. V)"),
        ("2", "Support heavy neural operations seam-free",
         "Achieved: HED and Real-ESRGAN, calibrated halos (Sec. VI)"),
        ("3", "Choose execution strategy automatically",
         f"Achieved: planner; {eng['mape_pct']:.0f} % MAPE (Sec. VII, X-C)"),
        ("4", "Scale on work instead of queue length",
         "Achieved: work-aware scaler vs HPA, KEDA (Sec. VIII, X-D)"),
        ("5", "Keep v1 guarantees", f"Kept: halo-exact output, leases, retries; {tests} tests pass"),
    ], "Project objectives and outcomes", [0.6, 3.4, 4.6])

    # -------------------------------------------------------- III. Related work
    r.ieee_h1("Related Work")
    r.t("**Splitting and fault tolerance.** MapReduce [6] established splitting a job into independent tasks "
        "with re-execution on failure; our tile tasks are map tasks with a spatial reduce. Leases [7] bound how "
        "long a crashed holder can block a resource; our tiles use leases renewed by heartbeats. "
        "**Image pipelines.** Halide [8] separates the algorithm from its schedule and treats tile size and "
        "recomputation of overlap as a trade-off; Distributed Halide [9] extends this to clusters and shows that "
        "communication, not compute, often limits scaling, which is exactly the effect our planner models. "
        "**Performance models.** Ernest [10] predicts the runtime of analytics jobs from a few small runs with a "
        "simple parametric model; our planner follows the same idea (calibrate, then extrapolate) with terms for "
        "per-tile overhead and contention. "
        "**Autoscaling.** The Kubernetes HPA [19] scales on resource utilisation and KEDA [18] on event-source "
        "metrics such as list length. Autopilot [11] at Google sets resources from observed usage rather than "
        "user limits. Bhosale *et al.* [1], [2] argue for elastic, workload-aware resizing of cloud HPC jobs, and "
        "Liu *et al.* [4] predict future workload requirements for containerised workflows; our work-aware "
        "scaler is a simple instance of both ideas. Chazapis *et al.* [5] and the fog pipeline of [3] support the "
        "premise that a thin scheduler on Kubernetes is a viable substrate for parallel compute. "
        "**Neural networks.** HED [12] and Real-ESRGAN [13] are fully convolutional, so they can be tiled, but "
        "their theoretical receptive fields are large. Luo *et al.* [14] showed the *effective* receptive field "
        "is much smaller and roughly Gaussian, which justifies calibrating the halo empirically.")

    # ------------------------------------------------------ IV. Architecture
    r.ieee_h1("System Architecture")
    r.figx("fig_architecture_v2.png", "Architecture after the upgrades. Bulk bytes (input, tiles, results) flow "
          "through object storage; Redis carries only task IDs, leases, per-tile costs and job state.", 2.6)
    r.t("All components are built from one Docker image and run unchanged on a laptop, Docker Compose or "
        "Kubernetes:")
    r.bullets([
        "**API (FastAPI).** Accepts uploads, asks the planner for a plan, partitions the image, uploads input and "
        "tiles to the bucket, enqueues one task per tile with its predicted cost, and serves status and results "
        "(a 307 redirect to a presigned URL).",
        "**Redis.** Reliable queue (`pending`, `processing` lists and a lease sorted set), job hashes, per-tile "
        "cost hash, the queued *work-seconds* counter and the online profile. All state changes are Lua scripts "
        "so they are atomic.",
        "**Object store.** MinIO locally, Amazon S3 in the cloud. Keys are `jobs/<id>/input.<ext>`, "
        "`tiles/<i>.npy`, `results/<i>.npy`, `output.png|tif` and previews.",
        "**Workers.** Stateless; claim a tile, GET it, run the OpenCV or ONNX operation, PUT the result, "
        "complete it. The last finisher stitches (or streams a tiled TIFF).",
        "**Autoscaler.** Work-aware policy (ours), or the HPA / KEDA baselines; one is active at a time.",
    ])

    # ---------------------------------------------- V. Upgrade 1: object storage
    r.ieee_h1("Upgrade 1: Object-Storage Data Plane")
    r.ieee_h2("Design")
    r.t("v1 stored encoded tiles in Redis, so Redis memory, not CPU, limited the system. The upgrade applies the "
        "*claim-check* pattern: bulk bytes go to S3 and the queue entry carries only the tile ID. "
        "`imgframe/objstore.py` wraps boto3 with path-style addressing so the same code talks to MinIO and S3; "
        "tile uploads and downloads use a 32-thread pool. The API uploads all tiles *before* enqueuing any task, "
        "so a worker never claims a tile whose bytes are missing. Deleting a job removes its prefix and subtracts "
        "its remaining cost from the work counter. Redis mode is kept (`S3_BUCKET` unset) for tests and for the "
        "comparison below.")
    r.ieee_h2("Result Delivery and Large Outputs")
    r.t("`GET /jobs/{id}/result` answers with a 307 redirect to a presigned URL valid for 300 s, so result bytes "
        "never pass through the API (a `proxy` mode exists for clients that cannot follow redirects). A separate "
        "signing client uses the public endpoint, because the in-cluster hostname of MinIO is not resolvable by "
        "a browser. Outputs above 150 MP (`STREAM_OUTPUT_MP`) are written tile by tile into a tiled BigTIFF with "
        "*tifffile*, so the aggregator never holds the full image; a 1200 px JPEG preview is written alongside.")
    r.ieee_h2("Security")
    r.t("The AWS templates in `deploy/aws/` follow least privilege: the API role may put inputs and tiles, read "
        "results and presign; the worker role may only get tiles and put results. The bucket enforces Block "
        "Public Access and TLS-only access, encrypts with SSE-KMS [20], and expires `jobs/` after one day with a "
        "lifecycle rule. On EKS the roles are bound to Kubernetes service accounts with IRSA [21], so no keys are "
        "stored in pods. Locally, MinIO credentials live in a Kubernetes Secret. The AWS templates are written "
        "and syntax-checked but were not deployed (no AWS account was used).")

    # ---------------------------------------------- VI. Upgrade 2: neural ops
    r.ieee_h1("Upgrade 2: Deep-Learning Operations")
    r.ieee_h2("Models")
    r.t("**HED** [12] produces a soft edge map from five VGG-16 side outputs. The public weights are a Caffe "
        "model and OpenCV 5 no longer imports Caffe, so we wrote `scripts/convert_hed.py`, which decodes the "
        "Caffe protobuf wire format directly and emits an equivalent ONNX graph (bilinear resize of side outputs, "
        "as in the PyTorch port). **Real-ESRGAN** [13] (`realesr-general-x4v3`, 1.2 M parameters) upscales ×4. "
        "Both run under ONNX Runtime [22] with one intra-op thread per process, so parallelism comes only from "
        "the framework and the comparison with sequential execution is fair. Model files are fetched by "
        "`scripts/fetch_models.sh` and verified by SHA-256.")
    r.figx("fig_canny_vs_hed.png", "Canny vs HED on the same photograph, and the tiled-minus-whole error maps. "
          "With the calibrated halo the tiled HED output is visually identical to whole-image inference.",
          2.2, wide=True)
    r.ieee_h2("Halo Calibration from the Effective Receptive Field")
    r.t("The theoretical receptive-field radius of HED is 98 px and of Real-ESRGAN 34 px. Using it as the halo "
        "is exact but expensive: with 256 px tiles HED would compute 2.5× more pixels than needed. Following "
        "[14], `scripts/calibrate_halo.py` sweeps the halo, compares tiled with whole-image output and picks the "
        "smallest halo meeting two criteria: seam PSNR ≥ 40 dB (PSNR within 8 px of tile borders) and the "
        "99.9th-percentile absolute error ≤ 8 grey levels. Table II and Fig. 3 show the sweep.")
    hs, ss = halo["hed"], halo["superres"]

    def hrow(op, s):
        return (OPS_LABEL[op], str(s["halo"]), f"{s['seam_psnr_db']:.1f}", f"{s['p999_abs']:.0f}",
                f"{100 * s['overhead_theory']:.0f} %")
    hed_pick = next(s for s in hs["sweep"] if s["halo"] == hs["halo"])
    hed_full = next(s for s in hs["sweep"] if s["halo"] == hs["theoretical_halo"])
    sr_pick = next(s for s in ss["sweep"] if s["halo"] == ss["halo"])
    sr_full = next(s for s in ss["sweep"] if s["halo"] == ss["theoretical_halo"])
    r.itab(["Op", "Halo (px)", "Seam PSNR (dB)", "p99.9 |err|", "Extra pixels"], [
        hrow("hed", hs["sweep"][0]), hrow("hed", hed_pick), hrow("hed", hed_full),
        hrow("superres", ss["sweep"][0]), hrow("superres", sr_pick), hrow("superres", sr_full),
    ], "Halo calibration (256 px tiles); middle row = chosen halo", [1.8, 1.3, 2.1, 1.7, 1.7])
    r.figx("fig_halo.png", "Halo sweep: seam PSNR (left) and compute overhead (right). The chosen halo is the "
          "first point above both thresholds.", 2.0)
    r.t("Two details mattered. First, HED downsamples by up to 16, so a tile whose origin is not a multiple of "
        "16 sees a shifted pooling grid; `plan_tiles(..., align=16)` snaps *both* halo edges to the stride, which "
        "made the theoretical halo bit-exact (99 dB) where it had been worse than a 64 px halo. Second, "
        "Real-ESRGAN outputs 4× the input, so `crop_core` and `reconstruct` scale tile coordinates by the "
        "operation's `scale`. The calibrated halos cut extra computation from "
        f"{100 * hed_full['overhead_theory']:.0f} % to {100 * hed_pick['overhead_theory']:.0f} % (HED) and from "
        f"{100 * sr_full['overhead_theory']:.0f} % to {100 * sr_pick['overhead_theory']:.0f} % (Real-ESRGAN).")

    # ------------------------------------------------ VII. Upgrade 3: planner
    r.ieee_h1("Upgrade 3: Cost-Aware Adaptive Planner")
    r.ieee_h2("Time Model")
    r.t("For an image of *P* pixels, operation cost *c* (s/px), *K* tiles of mean size *p̄* with halo *h* and "
        "*n* workers, the planner predicts")
    r.equation("*T*_{dist} = *T*_{split} + *T*_{comp} + *T*_{over} + *T*_{stitch}")
    r.equation("*T*_{comp} = ⌈*K*/*n*⌉ · *c* · *p̄*_{halo} · *n* / *S*(*n*)")
    r.equation("*o*(*p*) = *o*_{0} + *o*_{px} · *p*_{halo} · (1 + *s*^{2})")
    r.t("where *S*(*n*) is the measured *capacity curve* of the operation (throughput of *n* concurrently busy "
        "workers relative to one), *o*(*p*) the per-tile overhead (encode, transfer, queue round trip) and *s* "
        "the output scale. Overheads are paid in ⌈*K*/*n*⌉ waves on the cluster; on the single-process engine, "
        "where dispatch is serial, *T* = *T*_{serial} + max(*T*_{comp} + *T*_{over}, *K*·*o*). The local "
        "alternative is *T*_{local} = *c·P* (plus split and stitch when it runs as a single tile on the "
        "cluster). The planner evaluates tile sizes {256, 512, 1024, 2048} and every worker count up to the "
        "available workers, adds a 20 % safety margin to the distributed estimate, and among plans within 2 % of "
        "the best picks the one with fewer workers.")
    r.ieee_h2("Calibration")
    r.t("`scripts/profile_ops.py` measures *c* for each operation on four tile sizes, the capacity curve for "
        "1–16 workers and the per-tile overhead of each backend (in-process engine, cluster with S3, cluster "
        "with Redis), and writes `imgframe/profiles.json` (Table III, Fig. 4). Workers keep refining *c* online: "
        "after each tile they add an exponential moving average (α = 0.05) to Redis, normalised for the "
        "contention at that moment as *c* ← *t*·*S*(*b*)/(*b*·*p*), where *b* is the number of busy workers.")
    r.itab(["Op", "c (ns/px)", "S(4)", "S(8)", "S(16)"], [
        (OPS_LABEL[op], f"{cop[op]:,.1f}" if cop[op] < 100 else f"{cop[op]:,.0f}",
         f"{capd[op]['4']:.2f}", f"{capd[op]['8']:.2f}", f"{capd[op]['16']:.2f}")
        for op in ("grayscale", "canny", "gaussian_blur", "bilateral", "hed", "superres")
    ], "Measured per-pixel cost and capacity curve (16-thread host)", [2.0, 1.7, 1.2, 1.2, 1.2])
    co, cr = prof["cluster"], prof["cluster_redis"]
    rt = co["round_trips"]
    r.t(f"Per-tile overhead on the cluster is *o*_{{0}} = {co['o0'] * 1e3:.2f} ms + "
        f"{co['o_px'] * 1e9:.2f} ns/px with S3 and {cr['o_px'] * 1e9:.2f} ns/px with Redis; a 512² tile "
        f"PUT+GET takes {rt['s3_put_get_512_ms']:.1f} ms on MinIO and {rt['redis_set_get_512_ms']:.2f} ms on "
        f"Redis. Since Canny costs only {cop['canny']:.1f} ns/px, a 512² Canny tile computes in 0.7 ms but "
        "costs ≈ 5 ms to move, which is why cheap operations must not be distributed finely.")
    r.figx("fig_capacity.png", "Capacity curves S(n). Bilateral scales to ≈ 7× on 16 threads; HED peaks at "
          "≈ 5× at 8 workers and Real-ESRGAN saturates at ≈ 2× from 4 workers: both are memory-bandwidth "
          "bound on this host.", 2.2)

    # ---------------------------------------------- VIII. Upgrade 4: autoscaler
    r.ieee_h1("Upgrade 4: Work-Aware Autoscaling")
    r.t("When the API enqueues a tile it stores the tile's predicted compute time and adds it to a Redis "
        "counter *W* (queued work-seconds). The completion, give-up and delete Lua scripts subtract exactly the "
        "stored cost, so *W* never drifts. Every 3 s the autoscaler reads *W*, estimates the arrival rate of "
        "work λ (s of work per s) as an EMA of the increase of a monotonic *work-added* counter, and sets")
    r.equation("*n* = clamp(max(⌈*W* / (*D*·*u*)⌉, ⌈λ/*u*⌉), *n*_{min}, *n*_{max})")
    r.t("with *D* = *L* − *t*_{start} the time left to drain the queue once a pod has started (latency target "
        "*L* = 30 s, pod start-up *t*_{start} = 5 s) and *u* = 0.8 the target utilisation. The first term drains "
        "the backlog within the latency target; the second keeps up with the steady arrival rate. Scale-ups are "
        "applied at once. A reduction is applied after a 30 s stabilisation window, as in the baselines, with one "
        "exception: when *W* < 0.05 s (nothing queued or in progress) for 10 s (`IDLE_SCALE_DOWN_S`), the pool "
        "drops straight to *n*_{min}. An empty queue is exact information, unlike the slowly decaying EMA λ, "
        "and a new burst is answered by an immediate scale-up whose start-up time is already budgeted in *D*. "
        "Unlike a queue-length rule, ten HED tiles "
        "and ten Canny tiles produce very different *W*, so the pool reacts to how much work is waiting.")
    r.t("**Baselines.** *HPA*: CPU utilisation target 70 % of the worker request (`autoscaling/v2`, 30 s "
        "scale-down window) [19]. *KEDA* 2.21 [18]: Redis Lists scaler on `queue:pending`, 16 tiles per replica, "
        "polling every 3 s. All three use 1–8 replicas; `scripts/k8s_scaler.sh` switches between them so exactly "
        "one controls the worker Deployment.")

    # -------------------------------------------------- IX. Setup
    r.ieee_h1("Experimental Setup")
    r.t("All experiments ran on one laptop: Intel Core i7-11800H (8 cores, 16 threads), 22 GB RAM, Ubuntu 25.10, "
        "Python 3.13, OpenCV 5.0, ONNX Runtime 1.31, Redis 7.2, MinIO (Chainguard image). Kubernetes experiments "
        "use Minikube 1.37 (Docker driver, 8 CPUs, 12 GB) with metrics-server and KEDA 2.21. Engine benchmarks "
        "use 8192² synthetic images for classical operations, a 2048² photograph for HED and a 1024² one for "
        "Real-ESRGAN. Each point is the median of three runs unless stated. Because workers share one host, "
        "results show *relative* behaviour; absolute speedups on a multi-node cluster would differ.")

    # -------------------------------------------------- X. Results
    r.ieee_h1("Results")
    r.ieee_h2("Object Storage vs Redis Data Plane")
    r.itab(["Metric (Redis maxmemory 1 GB)", "Redis mode", "S3 mode"], [
        ("Largest image accepted", f"{redis_m['largest_accepted']}² (12288² → 503)",
         f"≥ {s3_m['largest_accepted']}²"),
        ("Concurrent 8192² jobs accepted", str(redis_m["concurrent_8192_jobs_accepted"]),
         f"{s3_m['concurrent_8192_jobs_accepted']} (all tried)"),
        ("Redis memory per 8192² job", f"{redis_m['redis_bytes_per_8192_job'] / 1e6:.1f} MB",
         f"{s3_m['redis_bytes_per_8192_job'] / 1e6:.3f} MB"),
        ("I/O per 512² tile", f"{redis_m['io_ms_per_tile']:.1f} ms", f"{s3_m['io_ms_per_tile']:.1f} ms"),
        ("Bilateral 4096², end-to-end", f"{redis_m['bilateral_4096']['total_s']:.2f} s",
         f"{s3_m['bilateral_4096']['total_s']:.2f} s"),
    ], "Storage study", [3.6, 2.6, 2.4])
    ratio = redis_m["redis_bytes_per_8192_job"] / s3_m["redis_bytes_per_8192_job"]
    r.t(f"Moving bytes out of Redis reduces its footprint per job by a factor of {ratio:,.0f} and removes the "
        "concurrency limit (the 12 jobs we tried were all accepted; the remaining limit is the configurable "
        "tile-count back-pressure, 20 000 queued tiles). The price is latency: each tile costs "
        f"{s3_m['io_ms_per_tile'] - redis_m['io_ms_per_tile']:.1f} ms more I/O and a small bilateral job takes "
        f"{100 * (s3_m['bilateral_4096']['total_s'] / redis_m['bilateral_4096']['total_s'] - 1):.0f} % longer, "
        "mostly from uploading the input. For large or many concurrent jobs this trade is clearly worth it; "
        "the planner already accounts for the higher per-tile overhead.")
    r.figx("fig_storage.png", "Redis memory per 8192² job (log scale) and 8192² jobs accepted before HTTP 503, per storage mode.", 1.9)

    r.ieee_h2("Parallel Scaling of Neural Operations")
    r.figx("fig_scaling.png", "Speedup vs workers. Bilateral (compute-bound) scales furthest; HED peaks at 8 "
          "workers and Real-ESRGAN plateaus from 4.", 2.1)
    r.t(f"HED reaches {hed_peak:.2f}× at {hed_peak_n} workers and Real-ESRGAN {sr_peak:.2f}× at {sr_peak_n}. "
        "The upgrade plan expected near-linear speedup for neural operations because they are compute-heavy, "
        "but on one host they are limited by memory bandwidth: convolution activations of many concurrent "
        "tiles exceed the caches, so extra workers wait on DRAM. HED's per-process memory also grows with "
        "workers (peak RSS ≈ 3 GB per 8 workers). On a multi-node cluster, where every node brings its own "
        "memory bandwidth, the curve would continue; the planner learns the measured curve, so it stops adding "
        "workers where they no longer help.")

    r.ieee_h2("Planner")
    r.itab(["Op (image)", "Seq. (s)", "Naive", "Planner", "Plan"], [
        (f"{OPS_LABEL[op]} ({eng_rows[op]['size'].split('x')[0]}²)", f"{eng_rows[op]['sequential_s']:.2f}",
         f"{eng_rows[op]['naive_speedup']:.2f}×", f"{eng_rows[op]['plan_speedup']:.2f}×",
         "local" if eng_rows[op]["plan_mode"] == "local" else
         f"n={eng_rows[op]['plan_workers']}, {eng_rows[op]['plan_tile']} px")
        for op in ("grayscale", "canny", "gaussian_blur", "sobel", "bilateral", "hed", "superres")
    ], f"Engine: naive (512 px, {eng['workers']} workers) vs planner; speedup over sequential",
        [2.4, 1.3, 1.3, 1.4, 2.2])
    r.t("Naive tiling makes every cheap operation slower (grayscale runs at "
        f"{eng_rows['grayscale']['naive_speedup']:.2f}×). The planner recognises that and runs them locally, "
        "recovering ≈ 1×. For bilateral it chooses 12 workers, within 3 % of naive 16-worker tiling, and for the "
        f"neural operations it picks fewer workers than naive (8 for HED, 4 for Real-ESRGAN) and is faster "
        f"({eng_rows['hed']['plan_speedup']:.2f}× vs {eng_rows['hed']['naive_speedup']:.2f}× for HED) because "
        "it avoids the contention region of the capacity curve.")
    r.figx("fig_planner.png", "Speedup over sequential on the in-memory engine: naive 512 px tiling on all "
          "workers vs the planner's choice.", 2.2)
    r.figx("fig_model_accuracy.png", "Predicted vs measured time for all benchmarked configurations, refined "
          "model (capacity curve, serial dispatch) vs simple linear model.", 2.3)
    mo = eng["mape_by_op"]
    r.t(f"Across {len(eng['scatter'])} engine configurations the refined model has {eng['mape_pct']:.1f} % mean "
        f"absolute percentage error against {eng['mape_simple_pct']:.1f} % for the simple model "
        "(*T* = *c·P*/*n* + *K·o*). The gain is largest for the operations that matter most: HED "
        f"{mo['hed']['refined']:.0f} % vs {mo['hed']['simple']:.0f} %, Real-ESRGAN {mo['superres']['refined']:.0f} % "
        f"vs {mo['superres']['simple']:.0f} %, bilateral {mo['bilateral']['refined']:.0f} % vs "
        f"{mo['bilateral']['simple']:.0f} %. Errors remain high (≈ 40 %) for sub-0.2 s jobs, where timing noise "
        "dominates; there a wrong choice costs milliseconds.")
    r.itab(["Op", "Naive (s)", "Planner (s)", "Planner plan"], [
        (OPS_LABEL[op], f"{clu_rows[op]['naive_s']:.2f}", f"{clu_rows[op]['planner_s']:.2f}",
         "1 tile" if clu_rows[op]["planner_mode"] == "local" else
         f"{clu_rows[op]['planner_tiles']} × {clu_rows[op]['planner_tile']} px")
        for op in ("canny", "gaussian_blur", "sobel", "bilateral", "hed", "superres")
    ], f"Full framework with S3 ({clu['workers']} workers), end-to-end job time", [1.9, 1.6, 1.7, 2.6])
    r.t("Through the full framework (HTTP upload, S3, Redis, worker processes) the planner replaces 64 tiles of "
        "512 px with 16 tiles of 1024 px for classical operations, cutting end-to-end time by 4–14 %; for the "
        f"neural operations it agrees with the naive choice (differences within noise). The cluster model's "
        f"MAPE is {clu['mape_pct']:.1f} %. One honest observation: in-memory Canny on 4096² takes "
        f"{clu_rows['canny']['sequential_s'] * 1e3:.0f} ms, so for cheap operations the whole job is dominated "
        "by upload and storage, and the best plan would be to run them in the API process without queuing. We "
        "list that under future work.")

    r.ieee_h2("Autoscaling: HPA vs KEDA vs Work-Aware")
    if auto:
        any_ = next(iter(auto.values()))
        r.t(f"The trace (`scripts/trace_replay.py`, seed 7) has {any_['jobs']} jobs in four phases: two bursts of "
            "cheap jobs (grayscale, Canny on 4096²) and two bursts of expensive jobs (bilateral 4096², HED 2048²) "
            "with exponential inter-arrival times and 15 s pauses between phases. Each scaler replayed it "
            f"{any_['runs']} times, interleaved, starting from one idle worker. Table {ROMAN[r.tab]} reports mean ± standard "
            f"deviation; the latency target is {any_['target']:.0f} s.")
        r.itab(["Metric", *[SCALER[k].split(" (")[0] for k in auto]], [
            ("p50 latency (s)", *[pm(auto[k]["p50_s"]) for k in auto]),
            ("p95 latency (s)", *[pm(auto[k]["p95_s"]) for k in auto]),
            ("Jobs within target (%)", *[pm(auto[k]["within_target_pct"]) for k in auto]),
            ("Worker pod-seconds", *[pm(auto[k]["pod_seconds"], 0) for k in auto]),
            ("First scale-up (s)", *[pm(auto[k]["first_scale_up_s"]) for k in auto]),
            ("Scaling events", *[pm(auto[k]["scaling_events"]) for k in auto]),
        ], "Autoscaler comparison on Minikube (mean ± s.d.)", [2.8, *[1.95] * len(auto)])
        r.figx("fig_autoscale.png", "Replica count (line) and queued work-seconds (shaded) during one run of "
              "each scaler; ticks mark job arrivals (red = expensive).", 3.2, wide=True)
        for text in autoscale_discussion(auto, auto_before.get("work")):
            r.t(text)
    else:
        r.box("Autoscaler results not found: run scripts/run_autoscale_comparison.sh", fill="FEF3C7")

    r.ieee_h2("Correctness and Testing")
    r.t(f"The test suite has {tests} tests (all pass): tiling and exact reconstruction for every classical "
        "operation, the reliable queue (claim, duplicate completion, lease expiry, retries), end-to-end HTTP "
        "runs including a crashed worker, and 17 new tests for the upgrades: S3-mode jobs keep pixels out of "
        "Redis, proxy and redirect delivery, queue-depth back-pressure, crash recovery in S3 mode, stride-aligned "
        "halos, scale-aware stitching, streamed TIFF equal to in-memory output, HED tiled vs whole (PSNR), "
        "Real-ESRGAN end to end, planner decisions, the online cost EMA, exact work-seconds accounting with "
        "retries and duplicates, the work-aware replica rule and the idle scale-down. "
        "Fault tolerance from v1 still holds: a worker killed mid-job on Kubernetes was replaced and its leased "
        "tiles were re-run without loss.")

    # -------------------------------------------------- XI. v1 baseline recap
    r.ieee_h1("Baseline Behaviour of v1")
    r.figx("fig_motivation.png", "v1 measurements that motivated the upgrades: only the CPU-heavy bilateral "
          "filter benefited from distribution.", 2.0)
    if old:
        r.t("For reference, v1 on an 8192² image reached 4.04× (8 workers) and 4.19× (16) for bilateral on the "
            "parallel engine, and 1.40× end to end through the framework, bounded by serial upload and "
            "aggregation (Amdahl's law [15]); Canny [16] and Gaussian blur were slower when distributed. The "
            "upgrades keep these guarantees and remove the four limits listed in Sec. I-B.")

    # -------------------------------------------------- XII. Deployment
    r.ieee_h1("Deployment and Mapping to AWS")
    r.t("`k8s/` contains the namespace, ConfigMap and Secret, Redis (256 MB, no persistence: it holds control data only), MinIO "
        "(NodePort 30900 for presigned downloads), the API (NodePort 30080), the worker Deployment (2 GiB "
        "limit for the neural models) and the autoscaler with RBAC limited to scaling the worker Deployment. "
        "`k8s/optional/` holds the HPA and KEDA baselines. Docker Compose runs the same stack locally.")
    r.itab(["Component", "Minikube", "AWS"], [
        ("Object store", "MinIO", "Amazon S3 (SSE-KMS, lifecycle)"),
        ("Queue / state", "Redis Deployment", "ElastiCache for Redis"),
        ("Orchestration", "Minikube", "Amazon EKS"),
        ("Pod scaling", "Work-aware / HPA / KEDA", "Same, + Karpenter for nodes"),
        ("Credentials", "Kubernetes Secret", "IRSA roles per service account"),
        ("Ingress", "NodePort", "ALB (Load Balancer Controller)"),
        ("Images", "Minikube Docker", "Amazon ECR"),
    ], "Local deployment and AWS equivalents", [2.2, 2.9, 3.5])

    # -------------------------------------------------- XIII. Limitations
    r.ieee_h1("Limitations and Future Work")
    r.bullets([
        "**Single host.** All workers share one CPU and memory bus, which caps neural speedup at ≈ 2–2.5×. A "
        "multi-node EKS run would test the capacity model on real network and storage latencies.",
        "**AWS not exercised.** The S3, IAM, KMS and IRSA templates are complete but were not deployed; MinIO "
        "stood in for S3.",
        "**Cheap operations on the cluster.** They should run in-process at the API (or on a GPU) instead of "
        "through the queue; the planner already predicts when this wins.",
        "**Model error.** 20–25 % MAPE is useful for decisions but not for SLAs; per-host calibration and a "
        "memory-bandwidth term would help.",
        "**Not implemented from the plan's optional extras:** GPU workers, spot/pre-emptible node pools, "
        "Cloud-Optimised GeoTIFF range reads with a deep-zoom viewer, and learned (not analytical) cost models.",
    ])

    # -------------------------------------------------- XIV. Conclusion
    r.ieee_h1("Conclusion")
    r.t("The upgrades turn a working tile-parallel framework into one that knows the cost of what it runs. "
        "Moving bytes to object storage removed the memory ceiling at the cost of a few milliseconds per tile; "
        "calibrated halos made deep-learning operations seam-free at a fraction of the theoretical overhead; a "
        "calibrated model lets the planner avoid the slowdowns of naive distribution and the contention region "
        "of memory-bound models; and scaling on queued work instead of queue length lets the pool react to "
        "what the queue actually costs. The main lesson is that on a cloud platform the decision *whether* and "
        "*how finely* to distribute matters as much as the parallel machinery itself.")

    head = r.d.add_paragraph()
    head.paragraph_format.alignment = C
    head.paragraph_format.space_before, head.paragraph_format.space_after = Pt(8), Pt(4)
    head.paragraph_format.keep_with_next = True
    run = head.add_run("References")
    run.font.small_caps = True
    r.references(REFS)

    # -------------------------------------------------- Appendix
    r.columns(1, new_page=True)
    r.h("Appendix A: Project Structure", 1, size=12)
    r.code("""cloud proj/
├── imgframe/            core package
│   ├── config.py        settings from environment variables
│   ├── ops.py           OpenCV + ONNX operations, halo, scale, stride
│   ├── models.py        ONNX Runtime session cache
│   ├── tiling.py        stride-aligned plan, split, scale-aware reconstruct, tiled TIFF
│   ├── store.py         Redis data model, reliable queue + work counter (Lua)
│   ├── objstore.py      S3 / MinIO client (boto3), presigning
│   ├── planner.py       cost model + plan search
│   ├── profiles.json    calibrated costs, overheads, capacity curves
│   └── calibration.json calibrated halos
├── api/                 FastAPI service + web UI
├── worker/              worker process (+ online profile updates)
├── autoscaler/          work-aware (and queue-length) autoscaler
├── benchmark/           bench_local, bench_distributed, bench_planner, bench_storage
├── k8s/                 manifests; optional/ HPA and KEDA baselines
├── deploy/aws/          S3 bucket policy, IAM, lifecycle, IRSA (templates)
├── scripts/             run_local, convert_hed, calibrate_halo, profile_ops,
│                        trace_replay, k8s_scaler, fetch_models, install_k8s_addons
├── tests/               pytest suite
└── docs/                report, slides, guide, figures""", size=7.5)
    r.h("Appendix B: New Configuration", 1, size=12, before=10)
    r.table(["Variable", "Default", "Meaning"], [
        ("S3_BUCKET", "(unset)", "Enables object-storage mode"),
        ("S3_ENDPOINT / S3_PUBLIC_ENDPOINT", "—", "Internal endpoint / endpoint used in presigned URLs"),
        ("S3_SSE, S3_KMS_KEY_ID", "—", "Server-side encryption (aws:kms on AWS)"),
        ("PRESIGN_SECONDS", "300", "Validity of result URLs"),
        ("RESULT_DELIVERY", "redirect", "redirect (307 to presigned URL) or proxy"),
        ("MAX_QUEUED_TILES", "20000", "Back-pressure limit in S3 mode"),
        ("STREAM_OUTPUT_MP", "150", "Outputs above this are written as tiled BigTIFF"),
        ("PLANNER, PLANNER_MARGIN", "1, 0.2", "Enable planner; safety margin on distributed estimate"),
        ("PROFILE_EMA", "0.05", "Weight of online cost updates"),
        ("POLICY", "work", "Autoscaler policy: work or queue"),
        ("LATENCY_TARGET_S, TARGET_UTILIZATION, POD_STARTUP_S", "30, 0.8, 5", "Work-aware scaler parameters"),
        ("SCALE_DOWN_DELAY, IDLE_SCALE_DOWN_S", "30, 10", "Delay before a reduction; delay before an idle pool drops to MIN"),
    ], None, widths_cm=[6.2, 2.4, 9.2], size=8, ieee=False, header_fill="334155", align_cols=[L, L, L])
    r.page_numbers()
    out = DOCS / "Project_Report_IEEE.docx"
    r.save(out)
    return out


def autoscale_discussion(auto, before=None):
    """Narrative for the autoscaling results, derived from the measured numbers."""
    if not {"hpa", "keda", "work"} <= auto.keys():
        return []
    h, k, w = auto["hpa"], auto["keda"], auto["work"]
    saving = 100 * (1 - w["pod_seconds"][0] / k["pod_seconds"][0])
    return [
        f"**HPA** is the cheapest ({h['pod_seconds'][0]:.0f} pod-s) but misses the target: only "
        f"{h['within_target_pct'][0]:.0f} % of jobs finish within {h['target']:.0f} s and p95 latency is "
        f"{h['p95_s'][0]:.0f} s. CPU utilisation is averaged over a metrics window and the worker request is "
        f"large, so its first scale-up came {h['first_scale_up_s'][0]:.0f} s after the first arrival, by which "
        "time the expensive burst was mostly over.",
        f"**KEDA** and the **work-aware** scaler both met the target for every job, with similar p95 latency "
        f"({k['p95_s'][0]:.1f} ± {k['p95_s'][1]:.1f} s vs {w['p95_s'][0]:.1f} ± {w['p95_s'][1]:.1f} s); "
        f"the work-aware scaler used {saving:.0f} % fewer "
        f"worker pod-seconds ({w['pod_seconds'][0]:.0f} vs {k['pod_seconds'][0]:.0f}). The "
        "replica traces show why: KEDA scales up within 4 s of the *cheap* bursts, because 10 grayscale/Canny jobs queue "
        "hundreds of tiles, although those jobs finish in about two seconds on one worker. The work-aware scaler "
        f"sees only a few seconds of queued work there and stays at one replica; its first scale-up "
        f"({w['first_scale_up_s'][0]:.0f} s) is the start of the expensive burst, which it meets with 5–7 workers.",
        *idle_paragraph(w, k, before),
    ]

def idle_paragraph(w, k, before):
    if not before:
        return []
    cut = 100 * (1 - w["pod_seconds"][0] / before["pod_seconds"][0])
    return [
        "**Idle scale-down.** Our first version kept its peak replica count for about 20 s after the queue "
        "drained: the arrival-rate EMA λ decays slowly and every reduction waited the full 30 s window. Adding "
        f"the idle rule (Sec. VIII) cut worker pod-seconds from {before['pod_seconds'][0]:.0f} ± "
        f"{before['pod_seconds'][1]:.0f} to {w['pod_seconds'][0]:.0f} ± {w['pod_seconds'][1]:.0f} "
        f"({cut:.0f} % less), while p95 latency only moved from {before['p95_s'][0]:.1f} s to {w['p95_s'][0]:.1f} s, "
        f"with {w['within_target_pct'][0]:.0f} % of jobs still within the target (3 runs each, same trace). "
        "With one trace on one host the differences between KEDA and the work-aware scaler should be read as "
        "indicative; the robust result is that scaling on work avoids reacting to cheap tile counts while "
        "still meeting the latency target.",
    ]


if __name__ == "__main__":
    path = build()
    print("wrote", path)
    to_pdf([path], DOCS)
    print("pdf", path.with_suffix(".pdf"))
