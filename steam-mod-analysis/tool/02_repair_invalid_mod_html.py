import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for _root in (_here, *_here.parents):
    if (_root / "cfg" / "config_loader.py").is_file():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break
else:
    raise RuntimeError("未找到 vic3analysis/cfg/config_loader.py，请从项目内运行本脚本。")

import os
import random
import re
import time
from glob import glob

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from cfg.config_loader import load_config

CFG = load_config("repair_invalid_mod_html")
APP_ID = int(CFG["app"]["id"])
MODS_ROOT = str(CFG["paths"]["mods_root"])
REQUEST_TIMEOUT_SECONDS = int(CFG["timing"]["request_timeout_seconds"])
HTML_SLEEP_MIN_SECONDS = float(CFG["timing"]["html_sleep_min_seconds"])
HTML_SLEEP_MAX_SECONDS = float(CFG["timing"]["html_sleep_max_seconds"])
MAX_HTML_RETRIES = int(CFG["retry"]["max_html_retries"])

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

HEADERS = {
    "User-Agent": str(CFG["http"]["headers"]["user_agent"]),
    "Accept": str(CFG["http"]["headers"]["accept"]),
    "Accept-Language": str(CFG["http"]["headers"]["accept_language"]),
}

FILENAME_ID_RE = re.compile(r"mod_(\d+)\.html$")

def extract_mod_id(path: str) -> str | None:
    m = FILENAME_ID_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def is_invalid_html(html_text: str) -> bool:
    lower = html_text.lower()
    # Steam workshop page should have these core markers.
    has_workshop_core = ("workshopitemtitle" in lower) or ("stats_table" in lower)
    if has_workshop_core:
        return False

    # Known hijacked/login page markers.
    if "dr.comwebloginid" in lower or "login.jlu.edu.cn" in lower:
        return True

    # Very short HTML usually indicates redirect/captive portal.
    return len(html_text) < 2000


def collect_invalid_files() -> list[tuple[str, str]]:
    invalid: list[tuple[str, str]] = []
    paths = glob(os.path.join(MODS_ROOT, "**", "mod_*.html"), recursive=True)
    for path in paths:
        mod_id = extract_mod_id(path)
        if not mod_id:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                html_text = f.read()
        except OSError:
            continue
        if is_invalid_html(html_text):
            invalid.append((mod_id, path))
    return invalid


def target_path(mod_id: str) -> str:
    bucket = mod_id[:2]
    bucket_dir = os.path.join(MODS_ROOT, bucket)
    os.makedirs(bucket_dir, exist_ok=True)
    return os.path.join(bucket_dir, f"mod_{mod_id}.html")


def fetch_mod_html(session: requests.Session, mod_id: str) -> bool:
    url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}&appid={APP_ID}"
    for attempt in range(1, MAX_HTML_RETRIES + 1):
        try:
            resp = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
                verify=False,
            )
        except requests.RequestException as e:
            print(f"⚠️ 请求异常 ID={mod_id} attempt={attempt}: {e}")
            time.sleep(2)
            continue

        if resp.status_code != 200:
            print(f"⚠️ 抓取失败 ID={mod_id} status={resp.status_code}")
            time.sleep(2)
            continue

        html_text = resp.text
        if is_invalid_html(html_text):
            print(f"⚠️ 返回仍为异常页 ID={mod_id}")
            time.sleep(2)
            continue

        out = target_path(mod_id)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html_text)
        return True

    return False


def main() -> None:
    invalid = collect_invalid_files()
    if not invalid:
        print("✅ 未发现异常HTML，无需修复。")
        return

    print(f"🧹 发现异常HTML: {len(invalid)} 个，先删除再重抓。")
    for _mod_id, path in invalid:
        try:
            os.remove(path)
        except OSError:
            pass

    session = requests.Session()
    success = 0
    failed: list[str] = []
    for i, (mod_id, _old_path) in enumerate(invalid, start=1):
        ok = fetch_mod_html(session, mod_id)
        if ok:
            success += 1
            print(f"✅ [{i}/{len(invalid)}] 修复成功 ID={mod_id}")
        else:
            failed.append(mod_id)
            print(f"❌ [{i}/{len(invalid)}] 修复失败 ID={mod_id}")
        time.sleep(random.uniform(HTML_SLEEP_MIN_SECONDS, HTML_SLEEP_MAX_SECONDS))

    print(f"🏁 修复完成: 成功 {success} / {len(invalid)}")
    if failed:
        print("❌ 仍失败的ID：")
        for mod_id in failed:
            print(f"- {mod_id}")


if __name__ == "__main__":
    main()
