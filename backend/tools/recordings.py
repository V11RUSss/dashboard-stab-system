"""Inspect and export trusted BRO30 recording sessions.

Pickle can execute code while loading. This utility deliberately resolves
files only through storage/recordings/index.json. Do not replace catalogued
files with data from an untrusted source.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.mj_decoder import (
    TLV_HEADER_SIZE,
    TLV_MESSAGE_NAMES,
    decode,
    parse_tlv_header,
)
from core.recording_manager import RECORDING_FORMAT


class RecordingToolError(RuntimeError):
    pass


def load_catalog(root: Path) -> dict:
    path = root / "index.json"
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RecordingToolError(f"Каталог не найден: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RecordingToolError(f"Не удалось прочитать каталог: {exc}") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get("recordings"), list):
        raise RecordingToolError("Некорректная структура index.json")
    return catalog


def find_entry(catalog: dict, selector: str) -> dict:
    exact = []
    prefix = []
    for entry in catalog["recordings"]:
        recording_id = str(entry.get("id", ""))
        names = {
            recording_id,
            str(entry.get("path", "")),
            Path(str(entry.get("path", ""))).name,
        }
        if selector in names:
            exact.append(entry)
        elif recording_id.startswith(selector):
            prefix.append(entry)
    matches = exact or prefix
    if not matches:
        raise RecordingToolError(f"Запись не найдена: {selector}")
    if len(matches) > 1:
        raise RecordingToolError(f"Селектор неоднозначен: {selector}")
    return matches[0]


def recording_path(root: Path, entry: dict) -> Path:
    relative = (
        entry.get("partial_path")
        if entry.get("status") in {"recording", "finalizing", "interrupted"}
        and entry.get("partial_path")
        else entry.get("path")
    )
    if not relative:
        raise RecordingToolError("В каталоге отсутствует путь к записи")
    root_resolved = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root_resolved):
        raise RecordingToolError("Путь записи выходит за каталог recordings")
    if not path.is_file():
        raise RecordingToolError(f"Файл записи не найден: {path}")
    return path


def iter_raw_records(path: Path):
    """Yield timestamp_ns/raw bytes and tolerate a truncated final batch."""
    with path.open("rb") as source:
        batch_number = 0
        while True:
            try:
                item = pickle.load(source)
            except EOFError:
                return
            except (pickle.UnpicklingError, AttributeError, ImportError, IndexError) as exc:
                raise RecordingToolError(
                    f"Повреждён pickle-блок {batch_number + 1}: {exc}"
                ) from exc
            batch_number += 1
            if not isinstance(item, dict) or item.get("format") != RECORDING_FORMAT:
                raise RecordingToolError(
                    f"Неподдерживаемый формат pickle-блока {batch_number}"
                )
            records = item.get("records")
            if not isinstance(records, list):
                raise RecordingToolError(
                    f"В pickle-блоке {batch_number} нет списка records"
                )
            for offset, record in enumerate(records, start=1):
                if (
                    not isinstance(record, tuple)
                    or len(record) != 2
                    or not isinstance(record[0], int)
                    or not isinstance(record[1], bytes)
                ):
                    raise RecordingToolError(
                        f"Некорректная запись {offset} в pickle-блоке {batch_number}"
                    )
                yield record


def _iso_from_ns(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(
        timestamp_ns / 1_000_000_000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def decode_record(record_number: int, timestamp_ns: int, raw_frame: bytes) -> dict:
    result = {
        "record": record_number,
        "stored_at": _iso_from_ns(timestamp_ns),
        "stored_at_ns": timestamp_ns,
    }
    header = parse_tlv_header(raw_frame)
    if header is None:
        return {**result, "error": "short TLV header"}
    result["tlv"] = {
        **header,
        "message_name": TLV_MESSAGE_NAMES.get(header["msg_type"], "unknown"),
    }
    expected_size = TLV_HEADER_SIZE + header["length"]
    if len(raw_frame) != expected_size:
        result["error"] = (
            f"TLV frame size {len(raw_frame)}, expected {expected_size}"
        )
        return result
    decoded = decode(header, raw_frame[TLV_HEADER_SIZE:])
    result["decoded"] = decoded
    if decoded is None:
        result["error"] = "unsupported or invalid payload"
    return result


def parse_time(value: str | None) -> int | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def selected_records(path: Path, args):
    from_ns = parse_time(args.from_time)
    to_ns = parse_time(args.to_time)
    emitted = 0
    for record_number, (timestamp_ns, raw_frame) in enumerate(
        iter_raw_records(path), start=1
    ):
        if from_ns is not None and timestamp_ns < from_ns:
            continue
        if to_ns is not None and timestamp_ns > to_ns:
            continue
        record = decode_record(record_number, timestamp_ns, raw_frame)
        if not args.include_invalid and record.get("error"):
            continue
        decoded = record.get("decoded") or {}
        if args.message_type and decoded.get("msg_type") != args.message_type:
            continue
        if args.device and decoded.get("device_id") != args.device:
            continue
        yield record
        emitted += 1
        if args.limit and emitted >= args.limit:
            return


def write_records(records, output, output_format: str) -> None:
    if output_format == "jsonl":
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        return
    output.write("[\n")
    first = True
    for record in records:
        if not first:
            output.write(",\n")
        output.write(json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2))
        first = False
    output.write("\n]\n")


def print_list(catalog: dict, as_json: bool) -> None:
    entries = list(reversed(catalog["recordings"]))
    if as_json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return
    if not entries:
        print("Записей пока нет")
        return
    print(f"{'ID':8}  {'СТАТУС':12}  {'НАЧАЛО':20}  {'ПАКЕТЫ':>10}  {'РАЗМЕР':>11}  НАЗВАНИЕ")
    for entry in entries:
        size_mb = int(entry.get("file_size_bytes") or 0) / 1024**2
        print(
            f"{str(entry.get('id', ''))[:8]:8}  "
            f"{entry.get('status', '')!s:12}  "
            f"{str(entry.get('started_at', ''))[:20]:20}  "
            f"{int(entry.get('packet_count') or 0):10d}  "
            f"{size_mb:9.1f} MB  {entry.get('label') or '-'}"
        )


def add_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("selector", help="Полный/короткий ID или имя PKL")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Каталог записей BRO30")
    parser.add_argument(
        "--root",
        type=Path,
        default=BACKEND_DIR / "storage" / "recordings",
        help="Каталог recordings",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="Список записей")
    list_parser.add_argument("--json", action="store_true")

    info_parser = commands.add_parser("info", help="Метаданные записи")
    add_selector(info_parser)

    export_parser = commands.add_parser("export", help="Экспорт телеметрии")
    add_selector(export_parser)
    export_parser.add_argument("-o", "--output", type=Path)
    export_parser.add_argument("--format", choices=("jsonl", "json"), default="jsonl")
    export_parser.add_argument("--message-type", choices=tuple(TLV_MESSAGE_NAMES.values()))
    export_parser.add_argument("--device")
    export_parser.add_argument("--from-time")
    export_parser.add_argument("--to-time")
    export_parser.add_argument("--limit", type=int, default=0)
    export_parser.add_argument("--include-invalid", action="store_true")

    verify_parser = commands.add_parser("verify", help="Проверить весь PKL")
    add_selector(verify_parser)
    return parser


def run(args) -> int:
    root = args.root.resolve()
    catalog = load_catalog(root)
    if args.command == "list":
        print_list(catalog, args.json)
        return 0
    entry = find_entry(catalog, args.selector)
    if args.command == "info":
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        return 0
    path = recording_path(root, entry)
    if args.command == "verify":
        count = sum(1 for _record in iter_raw_records(path))
        expected = entry.get("packet_count")
        print(
            json.dumps(
                {
                    "id": entry.get("id"),
                    "path": str(path),
                    "records_read": count,
                    "catalog_packet_count": expected,
                    "matches_catalog": expected is None or count == expected,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if expected is None or count == expected else 1

    output_context = (
        args.output.open("w", encoding="utf-8")
        if args.output
        else nullcontext(sys.stdout)
    )
    with output_context as output:
        write_records(selected_records(path, args), output, args.format)
    return 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (RecordingToolError, OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
