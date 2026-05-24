#!/usr/bin/env python3
"""
流水线冒烟测试：在运行 ``python main.py`` 前发现常见致命问题。

检查项（均可单独跳过见 ``--skip``）：
  config      — cfg/base.json、cfg/crawler.json 可读
  data_dir    — data-folder 可创建/可写
  proxy_base  — base.json 的 PORT 已监听（若配置了代理）
  app_workshop — Steam Store API：有效 APPID 且含创意工坊
  workshop_hub — 能打开 steamcommunity 工坊主页（阶段 1 同款出口）
  fetch_deps    — 阶段 2 所需 aiohttp（优先 resumable-batch-fetch/.venv）
  proxy_crawler — crawler.json 至少有一个端口能访问 test_url

退出码：0 全部通过；1 业务/配置问题；2 环境/网络问题。
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "lib"
_APPID_SRC = _REPO / "appid-steamworkshop-table" / "src"
_FETCH_SRC = _REPO / "resumable-batch-fetch" / "src"
for _p in (_LIB, _APPID_SRC, _FETCH_SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from paradox_paths import (  # noqa: E402
    base_json_path,
    find_repo_root,
    load_base_json,
    load_layout,
    parse_appid,
)
from app_config import (  # noqa: E402
    crawler_config_path,
    load_app_config,
    STEAMPP_LOCAL_PROXY_PORT,
)
from base_config import parse_proxy_port  # noqa: E402
from http_tls import clear_proxy_env, open_url  # noqa: E402

_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&l=english"
_WORKSHOP_URL_TMPL = "https://steamcommunity.com/app/{appid}/workshop/"
_PROBE_TIMEOUT_S = 15.0
_CRAWLER_PROBE_TIMEOUT_S = 8.0
_TCP_TIMEOUT_S = 2.0
_MAX_CRAWLER_HTTP_PROBES = 6


@dataclass
class CheckResult:
    name: str
    ok: bool
    summary: str
    fix: str = ""
    exit_class: int = 2  # 1=config/business, 2=network/env


def _verify_tls(cfg: dict) -> bool:
    return not bool(cfg.get("no_tls_verify"))


def _has_workshop_category(categories: list) -> bool:
    for c in categories:
        if not isinstance(c, dict):
            continue
        if c.get("id") == 30:
            return True
        if "workshop" in (c.get("description") or "").lower():
            return True
    return False


def _tcp_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_TCP_TIMEOUT_S):
            return True
    except OSError:
        return False


def _http_probe(
    url: str,
    *,
    proxy_port: int | None,
    verify_tls: bool,
    timeout: float = _PROBE_TIMEOUT_S,
) -> tuple[bool, str]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "analysis-paradox-smoke/1.0"}
    )
    try:
        with open_url(
            req, verify_tls=verify_tls, proxy_port=proxy_port, timeout=timeout
        ) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return True, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            return True, f"HTTP {e.code} (reachable, rate-limited)"
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, OSError):
            return False, f"{type(reason).__name__}: {reason}"
        return False, str(reason)
    except TimeoutError:
        return False, "timeout"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def check_config(repo: Path) -> CheckResult:
    base = base_json_path(repo)
    if not base.is_file():
        return CheckResult(
            "config",
            False,
            f"缺少 {base}",
            "在仓库根创建 cfg/base.json（见 cfg/README.md）",
            1,
        )
    try:
        load_base_json(repo)
    except SystemExit:
        return CheckResult(
            "config",
            False,
            "cfg/base.json 格式无效",
            "修正 JSON 语法与 target-game-id 字段",
            1,
        )
    try:
        load_app_config()
    except (OSError, ValueError) as e:
        return CheckResult(
            "config",
            False,
            f"爬虫配置无效: {e}",
            "检查 cfg/crawler.json（见 cfg/README.md）",
            1,
        )
    return CheckResult("config", True, "cfg/base.json 与 cfg/crawler.json 可读")


def check_data_dir(repo: Path, cfg: dict) -> CheckResult:
    try:
        layout = load_layout(repo, cfg=cfg)
    except SystemExit as e:
        return CheckResult(
            "data_dir",
            False,
            str(e),
            "修正 cfg/base.json 的 target-game-id / data-folder",
            1,
        )
    root = layout.root
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".smoke_write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return CheckResult(
            "data_dir",
            False,
            f"数据目录不可写: {root} ({e})",
            "更换 data-folder 或修正目录权限",
            2,
        )
    return CheckResult("data_dir", True, f"数据目录可写: {root}")


def check_proxy_base(cfg: dict) -> CheckResult:
    port = parse_proxy_port(cfg)
    if port is None:
        return CheckResult(
            "proxy_base",
            True,
            "简略爬取: 直连（未配置 PORT 或 PORT=-1）",
        )
    if not _tcp_listening("127.0.0.1", port):
        return CheckResult(
            "proxy_base",
            False,
            f"127.0.0.1:{port} 无监听 (Connection refused)",
            f"启动本地代理（如 Steam++/Watt 26561），或把 cfg/base.json 的 PORT 改为实际端口/-1",
            2,
        )
    verify = _verify_tls(cfg)
    ok, detail = _http_probe(
        "https://steamcommunity.com/",
        proxy_port=port,
        verify_tls=verify,
    )
    if not ok:
        return CheckResult(
            "proxy_base",
            False,
            f"代理 {port} 已监听但访问 Steam 失败: {detail}",
            "检查代理规则、节点；Steam++ 场景保留 no_tls_verify: true",
            2,
        )
    return CheckResult(
        "proxy_base",
        True,
        f"简略爬取代理 127.0.0.1:{port} 可用 ({detail})",
    )


def check_app_workshop(cfg: dict) -> CheckResult:
    try:
        appid = parse_appid(cfg)
    except SystemExit:
        return CheckResult(
            "app_workshop",
            False,
            "target-game-id 无效",
            "在 cfg/base.json 设置正整数 Steam APPID",
            1,
        )
    port = parse_proxy_port(cfg)
    verify = _verify_tls(cfg)
    url = _APPDETAILS_URL.format(appid=appid)
    req = urllib.request.Request(
        url, headers={"User-Agent": "analysis-paradox-smoke/1.0"}
    )
    try:
        with open_url(
            req, verify_tls=verify, proxy_port=port, timeout=_PROBE_TIMEOUT_S
        ) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        return CheckResult(
            "app_workshop",
            False,
            f"Store API 网络失败: {getattr(e, 'reason', e)}",
            "先修复 proxy_base；或暂时 PORT=-1 测试直连",
            2,
        )
    except TimeoutError:
        return CheckResult(
            "app_workshop",
            False,
            "Store API 超时",
            "检查代理与网络",
            2,
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return CheckResult(
            "app_workshop",
            False,
            "Store API 返回非 JSON",
            "可能被代理劫持或阻断，检查代理与 no_tls_verify",
            2,
        )

    key = str(appid)
    entry = payload.get(key)
    if not isinstance(entry, dict) or not entry.get("success"):
        return CheckResult(
            "app_workshop",
            False,
            f"APPID {appid} 在 Steam 商店无效或不可查询",
            "用 test/checkid 或核对 README 中的 APPID 列表",
            1,
        )
    data = entry.get("data")
    if not isinstance(data, dict):
        return CheckResult(
            "app_workshop",
            False,
            "商店无 data 字段",
            "确认这是 Steam 游戏而非工具/DLC 误填",
            1,
        )
    name = data.get("name") or "(unknown)"
    if not _has_workshop_category(data.get("categories") or []):
        return CheckResult(
            "app_workshop",
            False,
            f"《{name}》商店页未标注 Steam Workshop",
            "换有创意工坊的游戏 APPID；本流水线依赖工坊",
            1,
        )
    return CheckResult(
        "app_workshop",
        True,
        f"APPID {appid} 《{name}》支持 Steam Workshop",
    )


def check_workshop_hub(cfg: dict) -> CheckResult:
    appid = parse_appid(cfg)
    port = parse_proxy_port(cfg)
    verify = _verify_tls(cfg)
    url = _WORKSHOP_URL_TMPL.format(appid=appid)
    ok, detail = _http_probe(url, proxy_port=port, verify_tls=verify)
    if not ok:
        return CheckResult(
            "workshop_hub",
            False,
            f"工坊主页不可达: {detail}",
            "与 proxy_base 相同；阶段 1 会在 fetch_workshop_main 处失败",
            2,
        )
    return CheckResult("workshop_hub", True, f"工坊主页可访问 ({detail})")


def check_fetch_deps(repo: Path) -> CheckResult:
    from subprocess_python import resolve_package_python

    pkg = repo / "resumable-batch-fetch"
    req = pkg / "requirements.txt"
    try:
        py = resolve_package_python(pkg, required_imports=("aiohttp", "requests"))
    except RuntimeError as e:
        return CheckResult(
            "fetch_deps",
            False,
            "缺少 aiohttp/requests，阶段 2 无法启动",
            str(e),
            2,
        )
    rel = py
    try:
        rel = py.relative_to(repo)
    except ValueError:
        pass
    return CheckResult(
        "fetch_deps",
        True,
        f"详情爬虫依赖已就绪 ({rel})",
        f"依赖文件: {req}",
    )


def check_proxy_crawler() -> CheckResult:
    try:
        cfg = load_app_config()
    except (OSError, ValueError) as e:
        return CheckResult(
            "proxy_crawler",
            False,
            str(e),
            "修正 cfg/crawler.json",
            1,
        )
    urls = cfg.test_urls
    if not urls:
        return CheckResult(
            "proxy_crawler",
            False,
            "crawler.json test_url 为空",
            "在 cfg/crawler.json 添加 Steam 测试 URL",
            1,
        )
    test_url = urls[0]
    ports = cfg.crawler.ports
    ignore_tls = cfg.safety.ignore_tls_for_26561

    # HTTP 探测顺序：直连优先，其次已监听的本地端口（ capped 避免扫 20+ 端口过久）
    candidates: list[int] = []
    if -1 in ports:
        candidates.append(-1)
    listening = [p for p in ports if p != -1 and _tcp_listening("127.0.0.1", p)]
    for p in listening:
        if p not in candidates:
            candidates.append(p)
        if len(candidates) >= _MAX_CRAWLER_HTTP_PROBES:
            break

    if not candidates and ports:
        return CheckResult(
            "proxy_crawler",
            False,
            f"crawler.json 中 {len(ports)} 个代理端口均未监听",
            "启动 Mihomo/Clash/Steam++ 或加入 -1 直连；运行 resumable-batch-fetch/test/checkport.py",
            2,
        )

    def _try(port: int) -> tuple[int, bool, str]:
        ok, detail = _http_probe(
            test_url,
            proxy_port=None if port == -1 else port,
            verify_tls=not (ignore_tls and port == STEAMPP_LOCAL_PROXY_PORT),
            timeout=_CRAWLER_PROBE_TIMEOUT_S,
        )
        return port, ok, detail

    working: list[str] = []
    failed_samples: list[str] = []
    workers = min(4, max(1, len(candidates)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_try, p): p for p in candidates}
        for fut in as_completed(futures):
            port, ok, detail = fut.result()
            label = "direct" if port == -1 else str(port)
            if ok:
                working.append(f"{label} ({detail})")
            else:
                failed_samples.append(f"{port}: {detail}")

    if working:
        return CheckResult(
            "proxy_crawler",
            True,
            f"详情爬取至少 1 个出口可用: {working[0]}",
        )
    hint = failed_samples[0] if failed_samples else "无候选端口"
    return CheckResult(
        "proxy_crawler",
        False,
        f"已探测 {len(candidates)} 个出口均失败 ({hint} …)",
        "启动代理或修正 cfg/crawler.json 的 Ports；见 resumable-batch-fetch/test/checkport.py",
        2,
    )


ALL_CHECKS: dict[str, Callable[..., CheckResult]] = {
    "config": lambda repo, cfg: check_config(repo),
    "data_dir": lambda repo, cfg: check_data_dir(repo, cfg),
    "proxy_base": lambda repo, cfg: check_proxy_base(cfg),
    "app_workshop": lambda repo, cfg: check_app_workshop(cfg),
    "workshop_hub": lambda repo, cfg: check_workshop_hub(cfg),
    "fetch_deps": lambda repo, cfg: check_fetch_deps(repo),
    "proxy_crawler": lambda repo, cfg: check_proxy_crawler(),
}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Paradox 工坊流水线冒烟测试")
    ap.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="NAME",
        help=f"跳过检查: {', '.join(ALL_CHECKS)}",
    )
    ap.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="仅输出失败项与最终摘要",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    clear_proxy_env()
    repo = find_repo_root(_REPO)
    cfg = load_base_json(repo)
    layout = load_layout(repo, cfg=cfg)

    skip = {s.strip().lower() for s in args.skip}
    unknown = skip - set(ALL_CHECKS)
    if unknown:
        print(f"ERROR: 未知 --skip 项: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"冒烟测试 — APPID {layout.appid}")
        print(f"  配置: {base_json_path(repo)}")
        print(f"  数据: {layout.root}")
        print(f"  爬虫: {crawler_config_path()}")
        print()

    results: list[CheckResult] = []
    for name, fn in ALL_CHECKS.items():
        if name in skip:
            continue
        result = fn(repo, cfg)
        results.append(result)
        if args.quiet:
            if not result.ok:
                print(f"✗ {result.name}: {result.summary}")
                if result.fix:
                    print(f"    → {result.fix}")
        else:
            mark = "✓" if result.ok else "✗"
            print(f"{mark} [{result.name}] {result.summary}")
            if not result.ok and result.fix:
                print(f"    处理: {result.fix}")

    passed = sum(1 for r in results if r.ok)
    total = len(results)
    failed = [r for r in results if not r.ok]

    print()
    if not failed:
        print(f"通过 {passed}/{total}。可以运行: python main.py")
        return 0

    exit_code = 2
    if failed and all(r.exit_class == 1 for r in failed):
        exit_code = 1
    elif any(r.exit_class == 1 for r in failed) and any(
        r.exit_class == 2 for r in failed
    ):
        exit_code = 2
    elif any(r.exit_class == 2 for r in failed):
        exit_code = 2

    print(f"失败 {len(failed)}/{total}（退出码 {exit_code}）")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
