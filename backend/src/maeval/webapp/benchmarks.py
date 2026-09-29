from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from pathlib import Path
from typing import Any

from .db import Database, utcnow


IMPORTER_VERSION = "3"
DOWNLOAD_CACHE = Path(__file__).resolve().parents[3] / "data" / "downloads"

CATALOG: dict[str, dict[str, Any]] = {
    "gsm8k": {
        "name": "GSM8K",
        "description": "官方测试集中的多步数学文字题，以最终数值自动评分。",
        "source_url": "https://github.com/openai/grade-school-math",
        "license": "MIT",
        "version": "official-test",
        "source_revision": "master",
        "task_type": "math_reasoning",
        "language": "en",
        "scorer_version": "numeric-v2",
    },
    "mmlu": {
        "name": "MMLU",
        "description": "覆盖 57 个学科的四选一知识与推理基准。",
        "source_url": "https://github.com/hendrycks/test",
        "license": "MIT",
        "version": "official-test",
        "source_revision": "master",
        "task_type": "knowledge_mcq",
        "language": "en",
        "scorer_version": "choice-v1",
    },
    "arc-challenge": {
        "name": "ARC-Challenge",
        "description": "AI2 Reasoning Challenge 的高难度小学科学四选一测试集。",
        "source_url": "https://allenai.org/data/arc",
        "license": "CC-BY-SA-4.0",
        "version": "official-test-547c80c",
        "source_revision": "547c80c1b531504a731b16f3f74dc7a07656923b",
        "task_type": "science_reasoning_mcq",
        "language": "en",
        "scorer_version": "choice-v1",
    },
    "hellaswag": {
        "name": "HellaSwag",
        "description": "选择最合理的事件后续，评估常识推理和情境理解。",
        "source_url": "https://github.com/rowanz/hellaswag",
        "license": "MIT",
        "version": "official-validation",
        "source_revision": "main",
        "task_type": "commonsense_completion_mcq",
        "language": "en",
        "scorer_version": "choice-v1",
    },
    "truthfulqa": {
        "name": "TruthfulQA MC",
        "description": "测试模型是否会模仿常见误解；使用官方推荐的二选一版本。",
        "source_url": "https://github.com/sylinrl/TruthfulQA",
        "license": "Apache-2.0",
        "version": "mc-2025",
        "source_revision": "main",
        "task_type": "factuality_mcq",
        "language": "en",
        "scorer_version": "choice-v1",
    },
    "humaneval": {
        "name": "HumanEval",
        "description": "Python 函数生成与官方单元测试；执行生成代码需要显式授权。",
        "source_url": "https://github.com/openai/human-eval",
        "license": "MIT",
        "version": "official-v1",
        "source_revision": "master",
        "task_type": "code_generation",
        "language": "python",
        "unsafe": True,
        "scorer_version": "humaneval-v1",
    },
    "mbpp-sanitized": {
        "name": "MBPP Sanitized",
        "description": "Google Research 清洗后的基础 Python 编程问题，采用官方测试任务区间。",
        "source_url": "https://github.com/google-research/google-research/tree/master/mbpp",
        "license": "CC-BY-4.0",
        "version": "sanitized-test-11-510",
        "source_revision": "master",
        "task_type": "code_generation",
        "language": "python",
        "unsafe": True,
        "scorer_version": "mbpp-v1",
    },
    "repo-repair": {
        "name": "RepoRepair Smoke",
        "description": "本地 Python 仓库修复任务，用于验证 Agent 端到端执行链路。",
        "source_url": "local://fixtures/buggy_calculator",
        "license": "Local fixture",
        "version": "local-1",
        "source_revision": "fixture-v1",
        "task_type": "repository_agent",
        "language": "python",
        "official": False,
        "scorer_version": "pytest-v1",
    },
    "hardware-schematic-knowledge-zh": {
        "name": "硬件原理图知识（中文）",
        "description": "覆盖电源、接口保护、模拟/数字电路连接、器件选型与原理图审查的中文硬件题库。",
        "source_url": "local://benchmarks/hardware-schematic-knowledge-zh",
        "license": "Project built-in",
        "version": "1.0.0",
        "source_revision": "built-in-v1",
        "task_type": "hardware_schematic_mcq",
        "language": "zh-CN",
        "scorer_version": "choice-v1",
    },
}

HARDWARE_SCHEMATIC_ITEMS = [
    ("power-decoupling", "电源与去耦", "MCU 每个 VDD 引脚附近最常见且合理的去耦连接是哪一种？\nA. 100nF 电容紧靠 VDD 与 GND\nB. 100Ω 电阻串联 VDD\nC. VDD 悬空\nD. 仅在板边放一个电容\n\n只回答选项字母。", "A"),
    ("ldo-capacitor", "电源与去耦", "使用 LDO 时，输入和输出电容的首要依据是什么？\nA. 外壳颜色\nB. 数据手册规定的容量、ESR 和布局要求\nC. PCB 层数\nD. MCU 主频\n\n只回答选项字母。", "B"),
    ("led-current-limit", "基础连接", "GPIO 驱动普通 LED 时通常必须串联什么器件？\nA. 晶振\nB. 保险丝\nC. 限流电阻\nD. 共模电感\n\n只回答选项字母。", "C"),
    ("i2c-pullup", "数字接口", "I²C 的 SDA 和 SCL 线上通常需要什么？\nA. 对电源的上拉电阻\nB. 对地的下拉电阻\nC. 串联电解电容\nD. 终端变压器\n\n只回答选项字母。", "A"),
    ("usb-differential", "高速接口", "USB D+ 与 D- 在原理图和 PCB 中应作为什么信号处理？\nA. 两路无关 GPIO\nB. 差分对\nC. 模拟电源\nD. 开漏总线\n\n只回答选项字母。", "B"),
    ("rs485-termination", "通信接口", "长距离 RS-485 总线的终端电阻通常连接在哪里？\nA. A 与 B 之间、总线物理末端\nB. VCC 与 GND 之间\nC. MCU TX 与 RX 之间\nD. 晶振两端\n\n只回答选项字母。", "A"),
    ("can-protection", "通信接口", "CANH/CANL 接口靠近连接器的位置常放置哪类保护器件？\nA. TVS 二极管\nB. 运算放大器\nC. 线性稳压器\nD. 光敏电阻\n\n只回答选项字母。", "A"),
    ("crystal-layout", "时钟与复位", "MCU 外部晶振及负载电容在 PCB 上应如何放置？\nA. 尽量远离 MCU\nB. 靠近晶振引脚且回路短\nC. 放在电源入口\nD. 与开关节点并行走线\n\n只回答选项字母。", "B"),
    ("reset-pull", "时钟与复位", "低有效复位引脚的典型默认连接是？\nA. 上拉到电源，并可由按键拉低\nB. 永久接地\nC. 串联电感后悬空\nD. 接到时钟输出\n\n只回答选项字母。", "A"),
    ("mosfet-gate", "功率电路", "为避免 MOSFET 栅极在控制器未上电时漂浮，常增加什么？\nA. 栅源下拉或上拉电阻\nB. 栅漏大电容\nC. 漏源短路\nD. 栅极保险丝\n\n只回答选项字母。", "A"),
    ("flyback-diode", "功率电路", "低边 MOSFET 驱动继电器线圈时，线圈两端通常并联什么？\nA. 反向续流二极管\nB. 正向 LED\nC. 晶振\nD. 采样电阻\n\n只回答选项字母。", "A"),
    ("erc-unconnected", "原理图审查", "ERC 报告某输入引脚悬空时，最合适的处理是？\nA. 不分析直接忽略全部 ERC\nB. 核对设计意图，连接、上拉/下拉或用明确的 No Connect 标记\nC. 删除该器件\nD. 将所有悬空脚短接\n\n只回答选项字母。", "B"),
]


def seed_catalog(db: Database) -> None:
    with db.connect() as conn:
        for key, item in CATALOG.items():
            metadata = {
                "unsafe": item.get("unsafe", False),
                "redistribution": "reference-source",
            }
            conn.execute(
                """INSERT INTO benchmarks(
                id,name,description,source_url,license,metadata_json,version,source_revision,
                task_type,language,official,prompt_template_version,scorer_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,description=excluded.description,source_url=excluded.source_url,
                license=excluded.license,metadata_json=excluded.metadata_json,version=excluded.version,
                source_revision=excluded.source_revision,task_type=excluded.task_type,
                language=excluded.language,official=excluded.official,
                prompt_template_version=excluded.prompt_template_version,
                scorer_version=excluded.scorer_version""",
                (
                    key,
                    item["name"],
                    item["description"],
                    item["source_url"],
                    item["license"],
                    json.dumps(metadata),
                    item["version"],
                    item["source_revision"],
                    item["task_type"],
                    item["language"],
                    int(item.get("official", True)),
                    item.get("prompt_template_version", "1"),
                    item.get("scorer_version", "exact-v1"),
                ),
            )
        conn.execute("UPDATE benchmarks SET slug=id WHERE slug IS NULL")
        prompt = (
            "Fix calculator.median. Odd lengths return the sorted middle value; even lengths "
            "return the mean of the two middle values. Do not mutate input. Empty input raises ValueError."
        )
        metadata = {
            "kind": "repo",
            "fixture": "fixtures/buggy_calculator",
            "command": ["python", "{fixture}/.maeval/test_hidden.py"],
        }
        conn.execute(
            """INSERT OR IGNORE INTO benchmark_items
            (benchmark_id,item_key,category,prompt,expected_json,scorer_type,metadata_json)
            VALUES('repo-repair','repair-median','python-agent',?,NULL,'command',?)""",
            (prompt, json.dumps(metadata)),
        )
        conn.execute(
            "UPDATE benchmark_items SET access_level='shared' WHERE benchmark_id='repo-repair'"
        )
        digest = hashlib.sha256(
            json.dumps({"prompt": prompt, "metadata": metadata}, sort_keys=True).encode()
        ).hexdigest()
        conn.execute(
            """UPDATE benchmarks SET status='installed',item_count=1,content_sha256=?,
            installed_at=COALESCE(installed_at,?) WHERE id='repo-repair'""",
            (digest, utcnow()),
        )
        conn.execute(
            """INSERT OR IGNORE INTO benchmark_versions(
            benchmark_id,version,source_revision,content_sha256,item_count,importer_version,
            metadata_json,installed_at) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "repo-repair",
                CATALOG["repo-repair"]["version"],
                CATALOG["repo-repair"]["source_revision"],
                digest,
                1,
                IMPORTER_VERSION,
                json.dumps({"built_in": True}),
                utcnow(),
            ),
        )
        hardware_id = "hardware-schematic-knowledge-zh"
        for item_key, category, item_prompt, expected in HARDWARE_SCHEMATIC_ITEMS:
            conn.execute(
                """INSERT INTO benchmark_items
                (benchmark_id,item_key,category,prompt,expected_json,scorer_type,metadata_json,access_level)
                VALUES(?,?,?,?,?,'multiple_choice','{}','shared')
                ON CONFLICT(benchmark_id,item_key) DO UPDATE SET
                category=excluded.category,prompt=excluded.prompt,
                expected_json=excluded.expected_json,scorer_type=excluded.scorer_type,
                metadata_json=excluded.metadata_json,access_level=excluded.access_level""",
                (hardware_id, item_key, category, item_prompt, json.dumps(expected)),
            )
        hardware_snapshot = [
            {"key": key, "category": category, "prompt": item_prompt, "expected": expected}
            for key, category, item_prompt, expected in HARDWARE_SCHEMATIC_ITEMS
        ]
        hardware_digest = hashlib.sha256(
            json.dumps(hardware_snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        conn.execute(
            """UPDATE benchmarks SET status='installed',item_count=?,content_sha256=?,
            installed_at=COALESCE(installed_at,?) WHERE id=?""",
            (len(HARDWARE_SCHEMATIC_ITEMS), hardware_digest, utcnow(), hardware_id),
        )
        conn.execute(
            """INSERT OR IGNORE INTO benchmark_versions(
            benchmark_id,version,source_revision,content_sha256,item_count,importer_version,
            metadata_json,installed_at) VALUES(?,?,?,?,?,?,?,?)""",
            (
                hardware_id,
                CATALOG[hardware_id]["version"],
                CATALOG[hardware_id]["source_revision"],
                hardware_digest,
                len(HARDWARE_SCHEMATIC_ITEMS),
                IMPORTER_VERSION,
                json.dumps({"built_in": True}, ensure_ascii=False),
                utcnow(),
            ),
        )
        # Older databases predate content fingerprints. Preserve their installed
        # rows and derive a deterministic normalized snapshot instead of silently
        # re-downloading a potentially changed upstream dataset.
        for benchmark_id, catalog in CATALOG.items():
            benchmark = conn.execute(
                "SELECT * FROM benchmarks WHERE id=?", (benchmark_id,)
            ).fetchone()
            if not benchmark or not benchmark["item_count"]:
                continue
            content_hash = benchmark["content_sha256"]
            basis = "upstream-download"
            if not content_hash:
                hasher = hashlib.sha256()
                for row in conn.execute(
                    """SELECT item_key,category,prompt,expected_json,scorer_type,metadata_json
                    FROM benchmark_items WHERE benchmark_id=? ORDER BY item_key""",
                    (benchmark_id,),
                ):
                    hasher.update(
                        json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode("utf-8")
                    )
                    hasher.update(b"\n")
                content_hash = hasher.hexdigest()
                basis = "normalized-existing-items"
                conn.execute(
                    "UPDATE benchmarks SET content_sha256=? WHERE id=?",
                    (content_hash, benchmark_id),
                )
            conn.execute(
                """INSERT OR IGNORE INTO benchmark_versions(
                benchmark_id,version,source_revision,content_sha256,item_count,importer_version,
                metadata_json,installed_at) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    benchmark_id,
                    catalog["version"],
                    catalog["source_revision"],
                    content_hash,
                    benchmark["item_count"],
                    IMPORTER_VERSION,
                    json.dumps({"content_basis": basis}),
                    benchmark["installed_at"] or utcnow(),
                ),
            )


def _download(url: str) -> bytes:
    cache_path = DOWNLOAD_CACHE / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".bin")
    if cache_path.is_file():
        return cache_path.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": "prism-eval/0.3"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                content = response.read()
                DOWNLOAD_CACHE.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(content)
                return content
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        completed = subprocess.run(
            [
                curl,
                "-L",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "3",
                "--connect-timeout",
                "30",
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
        if completed.returncode == 0:
            DOWNLOAD_CACHE.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(completed.stdout)
            return completed.stdout
        last_error = RuntimeError(completed.stderr.decode("utf-8", errors="replace")[-1000:])
    raise RuntimeError(f"download failed after retries: {last_error}")


def _huggingface_rows(
    dataset: str,
    config: str,
    split: str,
    revision: str,
    limit: int | None,
) -> tuple[list[dict[str, Any]], bytes]:
    rows: list[dict[str, Any]] = []
    offset = 0
    total = None
    while total is None or offset < total:
        length = min(100, (limit - len(rows)) if limit else 100)
        if length <= 0:
            break
        query = urlencode(
            {
                "dataset": dataset,
                "config": config,
                "split": split,
                "offset": offset,
                "length": length,
                "revision": revision,
            }
        )
        page = json.loads(
            _download(f"https://datasets-server.huggingface.co/rows?{query}").decode("utf-8")
        )
        page_rows = [entry["row"] for entry in page.get("rows", [])]
        rows.extend(page_rows)
        total = int(page.get("num_rows_total", len(rows)))
        if not page_rows:
            break
        offset += len(page_rows)
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            break
    normalized = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return rows, normalized


def install_benchmark(db: Database, benchmark_id: str, limit: int | None = None) -> int:
    if benchmark_id not in CATALOG or benchmark_id in {
        "repo-repair", "hardware-schematic-knowledge-zh"
    }:
        raise ValueError(f"unknown or built-in benchmark: {benchmark_id}")
    if benchmark_id == "gsm8k":
        raw = _download(
            "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"
        )
        source = [json.loads(line) for line in raw.decode().splitlines()]
        items = [
            {
                "key": str(index),
                "category": "math",
                "prompt": row["question"] + "\n\nGive the final answer as a number.",
                "expected": row["answer"].split("####")[-1].strip(),
                "scorer": "numeric_answer",
                "metadata": {},
            }
            for index, row in enumerate(source)
        ]
    elif benchmark_id == "arc-challenge":
        source, raw = _huggingface_rows(
            "allenai/ai2_arc",
            "ARC-Challenge",
            "test",
            CATALOG[benchmark_id]["source_revision"],
            limit,
        )
        items = []
        for row in source:
            labels = row["choices"]["label"]
            texts = row["choices"]["text"]
            choices = "\n".join(f"{label}. {text}" for label, text in zip(labels, texts))
            items.append(
                {
                    "key": row["id"],
                    "category": "science",
                    "prompt": f"{row['question']}\n{choices}\n\nAnswer with only the choice label.",
                    "expected": row["answerKey"],
                    "scorer": "multiple_choice",
                    "metadata": {"split": "test"},
                }
            )
    elif benchmark_id == "hellaswag":
        source, raw = _huggingface_rows(
            "Rowan/hellaswag",
            "default",
            "validation",
            CATALOG[benchmark_id]["source_revision"],
            limit,
        )
        items = []
        for row in source:
            choices = "\n".join(
                f"{letter}. {ending}" for letter, ending in zip("ABCD", row["endings"])
            )
            items.append(
                {
                    "key": str(row["ind"]),
                    "category": row["activity_label"],
                    "prompt": f"Complete the scenario with the most plausible ending.\n\n{row['ctx']}\n{choices}\n\nAnswer with only A, B, C, or D.",
                    "expected": "ABCD"[int(row["label"])],
                    "scorer": "multiple_choice",
                    "metadata": {"split_type": row["split_type"]},
                }
            )
    elif benchmark_id == "humaneval":
        compressed = _download(
            "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
        )
        raw = gzip.decompress(compressed)
        source = [json.loads(line) for line in raw.decode().splitlines()]
        items = [
            {
                "key": row["task_id"],
                "category": "python",
                "prompt": "Complete this Python function. Return only complete Python code.\n\n"
                + row["prompt"],
                "expected": {"test": row["test"], "entry_point": row["entry_point"]},
                "scorer": "humaneval",
                "metadata": {"unsafe": True},
            }
            for row in source
        ]
    elif benchmark_id == "mbpp-sanitized":
        raw = _download(
            "https://huggingface.co/datasets/Muennighoff/mbpp/resolve/main/data/sanitized-mbpp.json"
        )
        source = [row for row in json.loads(raw) if 11 <= int(row["task_id"]) <= 510]
        items = [
            {
                "key": str(row["task_id"]),
                "category": "python",
                "prompt": (
                    "Write Python code that solves the task. Return only complete Python code.\n\n"
                    f"Task: {row['prompt']}\n\nVisible tests:\n"
                    + "\n".join(row["test_list"])
                ),
                "expected": {
                    "test_imports": row.get("test_imports", []),
                    "test_list": row["test_list"],
                },
                "scorer": "mbpp",
                "metadata": {"unsafe": True, "split": "test"},
            }
            for row in source
        ]
    elif benchmark_id == "truthfulqa":
        raw = _download(
            "https://raw.githubusercontent.com/sylinrl/TruthfulQA/main/TruthfulQA.csv"
        )
        rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        items = []
        for index, row in enumerate(rows):
            question = row["Question"]
            correct = row["Best Answer"]
            incorrect = row["Best Incorrect Answer"]
            correct_first = int(hashlib.sha256(question.encode()).hexdigest(), 16) % 2 == 0
            choices = (correct, incorrect) if correct_first else (incorrect, correct)
            items.append(
                {
                    "key": str(index),
                    "category": row.get("Category") or "truthfulness",
                    "prompt": f"{question}\nA. {choices[0]}\nB. {choices[1]}\n\nAnswer with only A or B.",
                    "expected": "A" if correct_first else "B",
                    "scorer": "multiple_choice",
                    "metadata": {"format": "recommended-binary-mc"},
                }
            )
    else:  # MMLU
        raw = _download("https://people.eecs.berkeley.edu/~hendrycks/data.tar")
        items = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            members = sorted(
                member
                for member in archive.getmembers()
                if "/test/" in member.name and member.name.endswith("_test.csv")
            )
            for member in members:
                category = Path(member.name).stem.removesuffix("_test")
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                reader = csv.reader(io.TextIOWrapper(extracted, encoding="utf-8"))
                for index, row in enumerate(reader):
                    if len(row) < 6:
                        continue
                    choices = "\n".join(
                        f"{letter}. {value}" for letter, value in zip("ABCD", row[1:5])
                    )
                    items.append(
                        {
                            "key": f"{category}:{index}",
                            "category": category,
                            "prompt": f"{row[0]}\n{choices}\n\nAnswer with only A, B, C, or D.",
                            "expected": row[5].strip(),
                            "scorer": "multiple_choice",
                            "metadata": {},
                        }
                    )
    if limit and benchmark_id not in {"arc-challenge", "hellaswag"}:
        items = items[:limit]
    digest = hashlib.sha256(raw).hexdigest()
    catalog = CATALOG[benchmark_id]
    now = utcnow()
    with db.connect() as conn:
        conn.execute("DELETE FROM benchmark_items WHERE benchmark_id=?", (benchmark_id,))
        conn.executemany(
            """INSERT INTO benchmark_items(
            benchmark_id,item_key,category,prompt,expected_json,scorer_type,metadata_json)
            VALUES(?,?,?,?,?,?,?)""",
            [
                (
                    benchmark_id,
                    item["key"],
                    item["category"],
                    item["prompt"],
                    json.dumps(item["expected"], ensure_ascii=False),
                    item["scorer"],
                    json.dumps(item["metadata"]),
                )
                for item in items
            ],
        )
        conn.execute(
            """UPDATE benchmarks SET status='installed',item_count=?,installed_at=?,
            content_sha256=? WHERE id=?""",
            (len(items), now, digest, benchmark_id),
        )
        conn.execute(
            """INSERT OR IGNORE INTO benchmark_versions(
            benchmark_id,version,source_revision,content_sha256,item_count,importer_version,installed_at,metadata_json)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                benchmark_id,
                catalog["version"],
                catalog["source_revision"],
                digest,
                len(items),
                IMPORTER_VERSION,
                now,
                json.dumps({"limit": limit}),
            ),
        )
    return len(items)
