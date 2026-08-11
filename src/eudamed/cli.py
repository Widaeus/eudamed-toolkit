"""Command-line entry point.

Filter flags for ``search`` and ``export`` are generated from
``client.VERIFIED_DEVICE_FILTERS`` rather than listed by hand, so the allow-list
and the command-line surface cannot drift apart: a filter the client has not
verified has no flag, and argparse rejects an unrecognised one before any
request is made. Everything else in this module is thin dispatch onto
``client``, ``export``, ``nomenclature``, ``reference`` and ``urls``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from eudamed import __version__, nomenclature
from eudamed.client import VERIFIED_DEVICE_FILTERS, EudamedClient
from eudamed.export import FORMATS, export_devices
from eudamed.reference import ReferenceMaps
from eudamed.urls import actor_url, device_url

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")

# One help string per verified filter, carrying its real semantics -- prefix
# match, substring, exact, full refdata code -- rather than restating the flag
# name. This matters most for --name: without the caveat here, --help alone
# would lead a reader to believe it searches the device name, when it matches
# a field that includes the manufacturer's registered name instead. A filter
# added to VERIFIED_DEVICE_FILTERS without an entry here still gets a flag --
# see the fallback in _add_filter_arguments -- so the generation can never
# silently drop one, but it should get an entry before release.
FILTER_HELP: dict[str, str] = {
    "cndCode": "EMDN nomenclature code, prefix match (e.g. Z1203)",
    "riskClassCode": "risk class, full refdata code (e.g. refdata.risk-class.class-iib)",
    "deviceStatusCode": (
        "device status, full refdata code "
        "(e.g. refdata.device-model-status.on-the-market)"
    ),
    "applicableLegislation": (
        "legislation, full refdata code (e.g. refdata.applicable-legislation.mdr)"
    ),
    "tradeName": "trade name, substring match, case-insensitive",
    "name": (
        "substring match against a field that INCLUDES the manufacturer's "
        "registered name -- this is not a device-name search, and a hit does "
        "not mean the device itself is named this"
    ),
    "primaryDi": "primary DI, exact match",
    "basicUdi": "Basic UDI-DI, exact match",
    "deviceTypes": (
        "special device type, full refdata code "
        "(e.g. refdata.special-mdr-device-type.software)"
    ),
    "deviceCriteria": "STANDARD (MDR) or LEGACY (Art. 120 MDD/AIMDD transitional), exact match",
}


def _kebab(name: str) -> str:
    """``cndCode`` -> ``cnd-code``. Used to derive flag names from filter names."""
    return _CAMEL_BOUNDARY.sub("-", name).lower()


def _add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("filters")
    for filter_name in sorted(VERIFIED_DEVICE_FILTERS):
        group.add_argument(
            f"--{_kebab(filter_name)}",
            dest=filter_name,
            default=None,
            help=FILTER_HELP.get(filter_name, f"filter on {filter_name} (undocumented)"),
        )


def _collect_filters(args: argparse.Namespace) -> dict[str, Any]:
    return {
        name: getattr(args, name)
        for name in VERIFIED_DEVICE_FILTERS
        if getattr(args, name, None) is not None
    }


def _build_client(args: argparse.Namespace) -> EudamedClient:
    return EudamedClient(
        cache_dir=args.cache_dir,
        run_log=args.log,
        min_interval=args.min_interval,
        contact=args.contact,
    )


def _global_parser() -> argparse.ArgumentParser:
    """Flags shared by every subcommand that talks to the public API."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--contact", default=None, help="contact string appended to the User-Agent header"
    )
    parser.add_argument(
        "--cache-dir",
        default="data/raw/.cache",
        help="directory for cached responses (default: %(default)s)",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=0.4,
        help="minimum seconds between requests (default: %(default)s)",
    )
    parser.add_argument(
        "--log",
        default="logs/requests.jsonl",
        help="path to the request log (default: %(default)s)",
    )
    return parser


def _cmd_search(args: argparse.Namespace) -> int:
    client = _build_client(args)
    filters = _collect_filters(args)
    if args.count:
        total = client.count_devices(**filters)
        print(f"{total:,}")
        return 0
    page = client.search_devices(page=args.page, page_size=args.page_size, **filters)
    print(json.dumps(page, indent=2, ensure_ascii=False))
    return 0


def _cmd_device(args: argparse.Namespace) -> int:
    client = _build_client(args)
    record = client.device_detail(args.uuid)
    if record is None:
        print(f"no device found for {args.uuid}", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print()
    print(device_url(args.uuid))
    return 0


def _cmd_actor(args: argparse.Namespace) -> int:
    client = _build_client(args)
    record = client.actor(args.uuid)
    if record is None:
        print(f"no actor found for {args.uuid}", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print()
    print(actor_url(args.uuid))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    client = _build_client(args)
    filters = _collect_filters(args)
    report = export_devices(
        client, Path(args.out), fmt=args.format, enrich=args.enrich, **filters
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _cmd_nomenclature_walk(args: argparse.Namespace) -> int:
    client = _build_client(args)
    nodes = list(nomenclature.walk(client, root_uuid=args.root, max_depth=args.max_depth))
    if args.suffix:
        nodes = nomenclature.terminal_codes(nodes, suffix=args.suffix)
    print(json.dumps(nodes, indent=2, ensure_ascii=False))
    return 0


def _cmd_nomenclature_sweep(args: argparse.Namespace) -> int:
    client = _build_client(args)
    rows = nomenclature.sweep(client, args.codes)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def _cmd_missing_subcommand(args: argparse.Namespace) -> int:
    args._parser.print_usage(sys.stderr)
    return 2


def _cmd_reference(args: argparse.Namespace) -> int:
    maps = ReferenceMaps.load(Path(args.cache) if args.cache else None)
    if args.code and args.id is not None:
        print(getattr(maps, args.code)(args.id))
        return 0
    print(json.dumps(maps.maps, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    glob = _global_parser()

    parser = argparse.ArgumentParser(
        prog="eudamed", description="Command-line access to the EUDAMED public read API."
    )
    parser.add_argument(
        "--version", action="version", version=f"eudamed-toolkit {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")

    search = subparsers.add_parser(
        "search", parents=[glob], help="count or fetch a page of UDI-DI search results"
    )
    search.add_argument("--count", action="store_true", help="print the total match count only")
    search.add_argument("--page", type=int, default=0, help="zero-based page number")
    search.add_argument("--page-size", type=int, default=300, help="results per page")
    _add_filter_arguments(search)
    search.set_defaults(func=_cmd_search)

    device = subparsers.add_parser(
        "device", parents=[glob], help="print a UDI-DI record and its interface link"
    )
    device.add_argument("uuid", help="UDI-DI uuid")
    device.set_defaults(func=_cmd_device)

    actor = subparsers.add_parser(
        "actor", parents=[glob], help="print an economic operator record and its interface link"
    )
    actor.add_argument("uuid", help="actor uuid")
    actor.set_defaults(func=_cmd_actor)

    export = subparsers.add_parser(
        "export", parents=[glob], help="stream a filtered device search to disk"
    )
    export.add_argument("out", help="output file path")
    export.add_argument(
        "--format",
        choices=FORMATS,
        default="jsonl",
        help="output format (default: %(default)s)",
    )
    export.add_argument(
        "--enrich", action="store_true", help="merge in each device's Basic UDI-DI detail"
    )
    _add_filter_arguments(export)
    export.set_defaults(func=_cmd_export)

    nomenclature_parser = subparsers.add_parser(
        "nomenclature", help="traverse the EMDN tree or count devices per code"
    )
    nomenclature_parser.set_defaults(func=_cmd_missing_subcommand, _parser=nomenclature_parser)
    nomenclature_sub = nomenclature_parser.add_subparsers(dest="nomenclature_command")

    walk = nomenclature_sub.add_parser(
        "walk", parents=[glob], help="list nodes below an EMDN tree node"
    )
    walk.add_argument("--root", default=None, help="uuid of the node to walk from")
    walk.add_argument("--max-depth", type=int, default=8, help="maximum depth to descend")
    walk.add_argument("--suffix", default=None, help="keep only codes ending in this suffix")
    walk.set_defaults(func=_cmd_nomenclature_walk)

    sweep = nomenclature_sub.add_parser(
        "sweep", parents=[glob], help="count devices for each of the given EMDN codes"
    )
    sweep.add_argument("codes", nargs="+", help="EMDN codes to count")
    sweep.set_defaults(func=_cmd_nomenclature_sweep)

    reference = subparsers.add_parser(
        "reference", help="look up or dump reference-value labels"
    )
    reference.add_argument("--cache", default=None, help="path to a reference-map cache file")
    reference.add_argument(
        "--code", choices=("risk_class", "legislation", "status"), default=None,
        help="reference map to look up a single value in",
    )
    reference.add_argument("--id", default=None, help="reference value id to look up")
    reference.set_defaults(func=_cmd_reference)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
