import json
import os
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent
_INPUT_DEFAULT = _TOOL_DIR / "id_collection_state.json"
_OUTPUT_DEFAULT = _TOOL_DIR
OUTPUT_NAME = "mod_urls.json"

STEAM_WORKSHOP_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"


def mod_id_to_url(mod_id: str) -> str:
    return STEAM_WORKSHOP_URL.format(mod_id=mod_id)


def load_mod_ids(path: str) -> list[str]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"未找到输入文件: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        ids = raw
    elif isinstance(raw, dict) and isinstance(raw.get("ids"), list):
        ids = raw["ids"]
    else:
        raise ValueError(
            '输入须为 JSON 字符串数组，或含 "ids" 数组的对象（同 id_collection_state.json）'
        )
    return [str(x) for x in ids if str(x).isdigit()]


def mod_ids_to_urls(mod_ids: list[str]) -> list[str]:
    return [mod_id_to_url(mid) for mid in mod_ids]


def export_mod_urls(input_path: str, out_dir: str, out_name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, out_name)

    mod_ids = load_mod_ids(input_path)
    urls = mod_ids_to_urls(mod_ids)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(urls, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return out_path


if __name__ == "__main__":
    path = export_mod_urls(str(_INPUT_DEFAULT), str(_OUTPUT_DEFAULT), OUTPUT_NAME)
    print(path)
