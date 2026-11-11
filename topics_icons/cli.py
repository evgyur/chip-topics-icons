"""CLI: offline preparation by default; explicit live publication/application."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import sys

from .artwork import prepare, prepared_integrity
from .model import IconError, load, save
from .telegram import Bot, Forum
from .workflow import apply, publish, verify


@contextmanager
def single_writer(path):
    # This package targets Unix agent hosts. One lock covers a private run directory.
    import fcntl
    lock = Path(path).parent / ".topics-icons.lock"
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise IconError("Another writer owns this run directory") from None
        yield
    finally:
        os.close(fd)


def outside_package(path):
    path = Path(path).resolve()
    package = Path(__file__).resolve().parent.parent
    if path.is_relative_to(package):
        raise IconError("Keep private run output outside the public skill checkout")
    return path


def forum(args):
    origin = os.environ.get("FORUM_API_URL")
    if not origin:
        raise IconError("Set FORUM_API_URL to your existing authenticated forum adapter")
    adapter = Forum(origin, args.chat, os.environ.get("FORUM_API_TOKEN"))
    adapter.identity(args.account)
    return adapter


def parser():
    p = argparse.ArgumentParser(description="Prepare, publish and verify unique Telegram topic icons")
    sub = p.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory", help="Read the complete topic inventory; no writes to Telegram")
    inventory.add_argument("--chat", required=True)
    inventory.add_argument("--account", required=True)
    inventory.add_argument("--out", required=True)
    render = sub.add_parser("prepare", help="Render local assets and complete labelled previews, offline")
    render.add_argument("--inventory", required=True)
    render.add_argument("--design", required=True)
    render.add_argument("--assets", required=True)
    render.add_argument("--out", required=True)
    check = sub.add_parser("check", help="Validate a prepared manifest and its exact files, offline")
    check.add_argument("--manifest", required=True)
    pub = sub.add_parser("publish", help="Upload custom emoji through the incumbent bot; does not change topics")
    pub.add_argument("--manifest", required=True)
    pub.add_argument("--state", required=True)
    pub.add_argument("--owner", type=int, required=True)
    pub.add_argument("--bot", required=True, help="Exact incumbent bot username without @")
    pub.add_argument("--prefix", required=True, help="Fresh short pack-name prefix")
    pub.add_argument("--pack-size", type=int, default=80)
    pub.add_argument("--confirm-chat", required=True)
    pub.add_argument("--approve-designs", action="store_true", help="Attest that every preview and 22px icon was visually inspected")
    for command in ("apply", "verify"):
        cmd = sub.add_parser(command, help="Icon-only mutation" if command == "apply" else "Read-only full Telegram verification")
        cmd.add_argument("--manifest", required=True)
        cmd.add_argument("--publication", required=True)
        cmd.add_argument("--chat", required=True)
        cmd.add_argument("--account", required=True)
        if command == "apply":
            cmd.add_argument("--receipts", required=True)
            cmd.add_argument("--confirm-chat", required=True)
        else:
            cmd.add_argument("--out", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "inventory":
            out = outside_package(args.out)
            data = forum(args).inventory()
            save(out, data)
            print("Inventoried", len(data["topics"]), "topics, including General")
        elif args.command == "prepare":
            out = outside_package(args.out)
            with single_writer(out):
                data = prepare(load(args.inventory), load(args.design), args.assets, out)
            print("Prepared", len(data["icons"]), "unique icons; inspect every contact sheet and 22px list mockup")
        elif args.command == "check":
            data = load(args.manifest)
            prepared_integrity(data)
            print("Validated", len(data["icons"]), "distinct designs and decoded images")
        elif args.command == "publish":
            if not args.approve_designs:
                raise IconError("Inspect every preview before --approve-designs")
            state = outside_package(args.state)
            with single_writer(state):
                data = publish(load(args.manifest), Bot(os.environ.get("TELEGRAM_BOT_TOKEN")), args.owner,
                               args.bot, args.prefix, state, args.confirm_chat, args.pack_size)
            print("Published and read back", len(data["assignments"]), "unique custom emoji; topics not edited")
        else:
            manifest = load(args.manifest)
            publication = load(args.publication)
            prepared_integrity(manifest)
            adapter = forum(args)
            if args.command == "apply":
                path = outside_package(args.receipts)
                with single_writer(path):
                    data = apply(manifest, publication, adapter, path, args.confirm_chat)
            else:
                path = outside_package(args.out)
                data = verify(manifest, publication, adapter)
                save(path, data)
            print("Verified", data["verified_icons"], "unique topic icons; General and non-icon fields preserved")
        return 0
    except (IconError, OSError, KeyError, TypeError, ValueError):
        # No arbitrary exception text or provider payloads reach public logs.
        # Known IconError messages are written by this package and contain no secrets.
        error = sys.exception()
        print("ERROR: " + (str(error) if isinstance(error, IconError) else "Invalid input or local file access failure"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
