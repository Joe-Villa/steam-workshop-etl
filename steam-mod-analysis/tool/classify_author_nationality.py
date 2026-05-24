import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_paths import (  # noqa: E402
    AUTHOR_CLASSIFICATION_CHECKPOINT_JSON,
    AUTHOR_CLASSIFICATION_UNCERTAIN_JSON,
    AUTHOR_CLASSIFICATION_JSON,
    AUTHOR_MOD_TITLES_JSON,
    ensure_data_dirs,
)

DEFAULT_INPUT_JSON = AUTHOR_MOD_TITLES_JSON
DEFAULT_OUTPUT_CLASSIFIED = AUTHOR_CLASSIFICATION_JSON
DEFAULT_OUTPUT_UNCERTAIN = AUTHOR_CLASSIFICATION_UNCERTAIN_JSON
DEFAULT_CHECKPOINT_PATH = AUTHOR_CLASSIFICATION_CHECKPOINT_JSON

DEFAULT_MODEL = "Qwen/Qwen3.6-27B"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_RETRY_TIMES = 6
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_RETRY_BACKOFF_SECONDS = 2.5
DEFAULT_MAX_WORKERS = 2
DEFAULT_MAX_TITLES = 6
DEFAULT_CHECKPOINT_EVERY = 100


PROMPT_TEMPLATE = """你是“作者是否为中国人”分类器（默认必须做出判断）。

任务：根据输入 JSON 判断作者是否是中国人。
只输出一个字符：1 / 0 / 2
- 1：是中国人
- 0：不是中国人
- 2：无法确定

核心原则：
- 除非极端缺乏信息，否则不要输出 2。
- 只要有明显语言线索，就必须在 1/0 中二选一。

强规则（命中即优先）：
1) 只要任一模组标题出现明显中文（尤其整句/短语中文），直接判 1。
   例："中文人名补充"、"那个投资池看起来生气了" -> 1
2) 若作者名虽特殊（如 "#define"、"...", 符号串），但模组标题含明确中文，仍判 1。
3) 若多条标题持续呈现俄语圈特征（如 "RU" 前缀、"Russian Translation by Deepl" 等），判 0。
   例：大量 "RU ... / Russian Translation by Deepl" -> 0
4) 若标题整体长期偏某一非中文语种（俄/日/韩/西/德等）且无中文证据，判 0。
5) 若同时存在中外文混合，但出现了明确中文标题，优先判 1。

2 的使用限制（非常严格）：
- 仅当样本几乎无可用信息（author 与 titles 都是无意义符号/极短噪声）时可输出 2。
- 只要有任何有效语言证据，一律输出 1 或 0，不得输出 2。

输出要求（严格）：
- 只能输出 1 或 0 或 2
- 不要输出任何解释、标点、空格或换行外文本

输入：
{payload}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="调用 SiliconFlow API 判断作者是否是中国人，并输出分类结果。"
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        default=DEFAULT_INPUT_JSON,
        help=f"输入 JSON 文件路径（默认：{DEFAULT_INPUT_JSON}）",
    )
    parser.add_argument(
        "--output-classified",
        type=Path,
        default=DEFAULT_OUTPUT_CLASSIFIED,
        help=f"输出已分类结果 JSON（默认：{DEFAULT_OUTPUT_CLASSIFIED}）",
    )
    parser.add_argument(
        "--output-uncertain",
        type=Path,
        default=DEFAULT_OUTPUT_UNCERTAIN,
        help=f"输出不确定作者 JSON（默认：{DEFAULT_OUTPUT_UNCERTAIN}）",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT_PATH,
        help=f"断点续跑检查点文件（默认：{DEFAULT_CHECKPOINT_PATH}）",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help=f"每处理多少条落盘一次（默认：{DEFAULT_CHECKPOINT_EVERY}）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"模型名（默认：{DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=DEFAULT_BASE_URL,
        help=f"API 地址（默认：{DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default="",
        help="API Key。若不填则读取环境变量 SILICONFLOW_API_KEY。",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_RETRY_TIMES,
        help=f"单条记录最多重试次数（默认：{DEFAULT_RETRY_TIMES}）",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"请求超时秒数（默认：{DEFAULT_TIMEOUT_SECONDS}）",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"重试前退避秒数（默认：{DEFAULT_RETRY_BACKOFF_SECONDS}）",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"并发请求数（默认：{DEFAULT_MAX_WORKERS}）",
    )
    parser.add_argument(
        "--max-titles",
        type=int,
        default=DEFAULT_MAX_TITLES,
        help=f"每个作者最多传入多少个标题（默认：{DEFAULT_MAX_TITLES}）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅处理前 N 条记录，用于联调（0 表示处理全部）。",
    )
    return parser.parse_args()


def load_author_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"输入文件不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("输入 JSON 顶层必须是数组。")
    checked: list[dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx} 项不是对象。")
        if "author" not in item:
            raise ValueError(f"第 {idx} 项缺少字段：author")
        checked.append(item)
    return checked


def build_compact_item(item: dict[str, Any], max_titles: int) -> dict[str, Any]:
    author = str(item.get("author", "")).strip()
    raw_titles = item.get("mod_titles", [])
    titles: list[str] = []
    if isinstance(raw_titles, list):
        for title in raw_titles:
            if not isinstance(title, str):
                continue
            text = title.strip()
            if text:
                titles.append(text[:140])
            if len(titles) >= max_titles:
                break
    compact = {
        "author": author,
        "sample_mod_titles": titles,
        "total_mod_count": len(raw_titles) if isinstance(raw_titles, list) else 0,
    }
    return compact


def load_checkpoint(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"检查点格式错误（顶层应为对象）：{path}")
    raw_results = raw.get("processed_results", {})
    if not isinstance(raw_results, dict):
        raise ValueError(f"检查点格式错误（processed_results 应为对象）：{path}")
    processed_results: dict[int, str] = {}
    for idx_str, result in raw_results.items():
        idx = int(idx_str)
        text = str(result).strip()
        if text in {"0", "1", "2"}:
            processed_results[idx] = text
    return processed_results


def write_checkpoint(path: Path, processed_results: dict[int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {str(k): v for k, v in sorted(processed_results.items())}
    payload = {
        "processed_results": serializable,
        "processed_count": len(serializable),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_outputs(
    items: list[dict[str, Any]], processed_results: dict[int, str]
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    classified = {
        "is_chinese": [],
        "not_chinese": [],
    }
    uncertain: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        result = processed_results.get(idx)
        if result not in {"0", "1", "2"}:
            continue
        author = str(item.get("author", "")).strip()
        if result == "1":
            classified["is_chinese"].append(author)
        elif result == "0":
            classified["not_chinese"].append(author)
        else:
            uncertain.append(item)
    return classified, uncertain


def write_outputs(
    *,
    items: list[dict[str, Any]],
    processed_results: dict[int, str],
    output_classified: Path,
    output_uncertain: Path,
) -> tuple[int, int, int]:
    classified, uncertain = build_outputs(items, processed_results)
    output_classified.write_text(
        json.dumps(classified, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_uncertain.write_text(
        json.dumps(uncertain, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return (
        len(classified["is_chinese"]),
        len(classified["not_chinese"]),
        len(uncertain),
    )


def format_request_error(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = exc.response
        if resp is not None:
            body = (resp.text or "").replace("\n", "\\n")
            body_preview = body[:400]
            return (
                f"HTTPError status={resp.status_code} reason={resp.reason!r} "
                f"body_preview={body_preview!r}"
            )
        return f"HTTPError detail={str(exc)!r}"
    if isinstance(exc, requests.exceptions.Timeout):
        return f"Timeout detail={str(exc)!r}"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"ConnectionError detail={str(exc)!r}"
    if isinstance(exc, requests.exceptions.RequestException):
        return f"RequestException detail={str(exc)!r}"
    return f"{type(exc).__name__} detail={str(exc)!r}"


def is_rate_limit_error(exc: Exception) -> bool:
    if not isinstance(exc, requests.exceptions.HTTPError):
        return False
    resp = exc.response
    return resp is not None and resp.status_code == 429


def compute_retry_sleep_seconds(
    *,
    attempt: int,
    retry_backoff_seconds: float,
    rate_limited: bool,
) -> float:
    base = max(0.1, float(retry_backoff_seconds))
    if rate_limited:
        # Exponential backoff on 429 to avoid retry storms.
        delay = min(base * (2 ** (attempt - 1)), 60.0)
        jitter = random.uniform(0.0, min(1.0, base))
        return delay + jitter
    return base


def classify_one(
    *,
    item: dict[str, Any],
    api_url: str,
    api_key: str,
    model: str,
    timeout_seconds: int,
    round_tag: str,
) -> str:
    payload_with_tag = {
        "round_tag": round_tag,
        "author_info": item,
    }
    payload_text = json.dumps(payload_with_tag, ensure_ascii=False)
    prompt = PROMPT_TEMPLATE.format(payload=payload_text)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        # Keep enough budget so final visible token is not truncated.
        "max_tokens": 64,
        # For thinking-capable models, ask for direct answer mode.
        "enable_thinking": False,
    }
    resp = requests.post(
        api_url,
        headers=headers,
        json=body,
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json()
    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    raw_content = message.get("content", "")
    reasoning_content = str(message.get("reasoning_content", "")).strip()
    # Compatible with providers that return content as an array of parts.
    if isinstance(raw_content, list):
        joined_parts: list[str] = []
        for part in raw_content:
            if isinstance(part, dict):
                text_part = str(part.get("text", ""))
                if text_part:
                    joined_parts.append(text_part)
            elif isinstance(part, str):
                joined_parts.append(part)
        content = "".join(joined_parts).strip()
    else:
        content = str(raw_content).strip()
    if not content:
        # Some responses put text directly on choice.text.
        content = str(choice.get("text", "")).strip()
    if not content and reasoning_content:
        # Some gateways may fill reasoning_content but leave content empty.
        # As a fallback, extract the first valid class digit from reasoning.
        match = re.search(r"[012]", reasoning_content)
        if match:
            content = match.group(0)
            print(f"[DEBUG] fallback_from_reasoning_output={content!r}")
    if not content:
        print(f"[DEBUG] empty_content_full_response={json.dumps(data, ensure_ascii=False)[:1000]}")
    return content


def classify_with_retry(
    *,
    item: dict[str, Any],
    api_url: str,
    api_key: str,
    model: str,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
    round_tag: str,
) -> str:
    author = str(item.get("author", "")).strip()
    for attempt in range(1, max_retries + 1):
        rate_limited = False
        try:
            content = classify_one(
                item=item,
                api_url=api_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
                round_tag=round_tag,
            )
            # Debug raw model output to diagnose format issues.
            print(
                f"[DEBUG] author={author!r} round={round_tag} attempt={attempt} "
                f"raw_output={content!r}"
            )
            if content in {"0", "1", "2"}:
                return content
            # Be tolerant to wrappers like "答案：1" / "1。"
            match = re.search(r"[012]", content)
            if match:
                normalized = match.group(0)
                print(
                    f"[DEBUG] author={author!r} round={round_tag} attempt={attempt} "
                    f"normalized_output={normalized!r}"
                )
                return normalized
            print(
                f"[DEBUG] author={author!r} round={round_tag} attempt={attempt} "
                f"invalid_output={content!r}"
            )
        except Exception as exc:
            rate_limited = is_rate_limit_error(exc)
            err_text = format_request_error(exc)
            stack_tail = traceback.format_exc(limit=1).strip().replace("\n", " | ")
            print(
                f"[DEBUG] author={author!r} round={round_tag} attempt={attempt} "
                f"request_failed error={err_text} traceback={stack_tail}"
            )
        if attempt < max_retries:
            sleep_seconds = compute_retry_sleep_seconds(
                attempt=attempt,
                retry_backoff_seconds=retry_backoff_seconds,
                rate_limited=rate_limited,
            )
            if rate_limited:
                print(
                    f"[DEBUG] author={author!r} round={round_tag} attempt={attempt} "
                    f"rate_limited_backoff_seconds={sleep_seconds:.2f}"
                )
            time.sleep(sleep_seconds)
    return "2"


def classify_by_double_judgment(
    *,
    item: dict[str, Any],
    api_url: str,
    api_key: str,
    model: str,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
) -> str:
    first = classify_with_retry(
        item=item,
        api_url=api_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        round_tag="A",
    )
    second = classify_with_retry(
        item=item,
        api_url=api_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        round_tag="B",
    )
    if first == second and first in {"0", "1", "2"}:
        return first
    # Prefer decisive labels: when one side is 2 and the other is 0/1, trust 0/1.
    if first in {"0", "1"} and second == "2":
        return first
    if second in {"0", "1"} and first == "2":
        return second
    # Only true conflict (0 vs 1) stays uncertain.
    return "2"


def main() -> None:
    ensure_data_dirs()
    args = parse_args()
    api_key = args.api_key.strip() or os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        raise ValueError("未提供 API Key。请设置 --api-key 或环境变量 SILICONFLOW_API_KEY。")

    items = load_author_items(args.input_json)
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    total = len(items)
    safe_workers = max(1, int(args.max_workers))
    max_titles = max(1, int(args.max_titles))
    processed_results = load_checkpoint(args.checkpoint)
    valid_indexes = set(range(1, total + 1))
    processed_results = {k: v for k, v in processed_results.items() if k in valid_indexes}
    resumed = len(processed_results)
    checkpoint_every = max(1, int(args.checkpoint_every))
    if resumed > 0:
        print(
            f"[INFO] 读取检查点成功：{args.checkpoint}，"
            f"已完成 {resumed}/{total}，将从断点继续。"
        )
    else:
        print("[INFO] 未发现可用检查点，将从头开始处理。")
    write_checkpoint(args.checkpoint, processed_results)
    write_outputs(
        items=items,
        processed_results=processed_results,
        output_classified=args.output_classified,
        output_uncertain=args.output_uncertain,
    )

    def worker(index_and_item: tuple[int, dict[str, Any]]) -> tuple[int, str, str]:
        idx, raw_item = index_and_item
        compact_item = build_compact_item(raw_item, max_titles=max_titles)
        author = str(raw_item.get("author", "")).strip()
        result = classify_by_double_judgment(
            item=compact_item,
            api_url=args.api_url,
            api_key=api_key,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
        )
        return idx, author, result

    remaining_entries = [
        (i, item)
        for i, item in enumerate(items, start=1)
        if i not in processed_results
    ]
    completed = resumed
    dirty_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=safe_workers) as executor:
        future_map = {
            executor.submit(worker, (i, item)): (i, item)
            for i, item in remaining_entries
        }
        for future in concurrent.futures.as_completed(future_map):
            i, item = future_map[future]
            author = str(item.get("author", "")).strip()
            try:
                idx, author, result = future.result()
            except Exception as exc:
                idx = i
                result = "2"
                print(f"[WARN] {i}/{total} author={author!r} 并发任务失败: {exc}")

            processed_results[idx] = result
            completed += 1
            dirty_count += 1
            if dirty_count >= checkpoint_every:
                write_checkpoint(args.checkpoint, processed_results)
                cn_count, non_cn_count, uncertain_count = write_outputs(
                    items=items,
                    processed_results=processed_results,
                    output_classified=args.output_classified,
                    output_uncertain=args.output_uncertain,
                )
                dirty_count = 0
                print(
                    f"[INFO] 已批量落盘：completed={completed}/{total} "
                    f"中国作者={cn_count} 非中国作者={non_cn_count} 不确定={uncertain_count}"
                )
            print(f"[INFO] {completed}/{total} author={author!r} -> {result}")

    if dirty_count > 0:
        write_checkpoint(args.checkpoint, processed_results)

    cn_count, non_cn_count, uncertain_count = write_outputs(
        items=items,
        processed_results=processed_results,
        output_classified=args.output_classified,
        output_uncertain=args.output_uncertain,
    )

    print(f"[DONE] 已输出分类结果：{args.output_classified}")
    print(f"[DONE] 已输出不确定作者：{args.output_uncertain}")
    print(f"[DONE] 已输出断点检查点：{args.checkpoint}")
    print(
        "[DONE] 统计："
        f"中国作者={cn_count}，"
        f"非中国作者={non_cn_count}，"
        f"不确定={uncertain_count}"
    )


if __name__ == "__main__":
    main()
