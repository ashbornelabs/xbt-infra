#!/usr/bin/env python3
"""Checks that the Electrum server serves the regtest headers across the BLAKE2b activation.

Legacy headers are 80 bytes and version 2 headers 164 bytes. With protocol 1.4, blockchain.block.headers
returns them concatenated, and clients split them by the version bit of each header. From protocol 1.6, it
returns them as a list. Every header is compared with the Knots node's, byte for byte.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Any

XBT = Path(__file__).resolve().parent.parent / "bin" / "xbt"
LEGACY_HEADER_SIZE = 80
V2_HEADER_SIZE = 164
# Bit 31 of the little-endian version field: the high bit of the header's fourth byte.
V2_VERSION_BYTE = 3
V2_VERSION_MASK = 0x80
# Headers requested on each side of the activation.
MARGIN = 5


def knots(*args: str) -> str:
    """Runs bitcoin-cli on the Knots node and returns its output."""
    result = subprocess.run([str(XBT), "cli", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def knots_header(height: int) -> str:
    return knots("getblockheader", knots("getblockhash", str(height)), "false")


def split_headers(blob: bytes) -> list[bytes]:
    """Splits concatenated headers, reading each header's size from its version bit."""
    headers = []
    offset = 0
    while offset < len(blob):
        if len(blob) - offset <= V2_VERSION_BYTE:
            raise ValueError(f"truncated header at offset {offset}")
        size = V2_HEADER_SIZE if blob[offset + V2_VERSION_BYTE] & V2_VERSION_MASK else LEGACY_HEADER_SIZE
        header = blob[offset : offset + size]
        if len(header) != size:
            raise ValueError(f"truncated header at offset {offset}: {len(header)} of {size} bytes")
        headers.append(header)
        offset += size
    return headers


class ElectrumClient:
    """Minimal Electrum client: newline-delimited JSON-RPC over TCP."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        try:
            self._socket = socket.create_connection((host, port), timeout)
        except OSError as error:
            raise ConnectionError(f"cannot connect to the Electrum server at {host}:{port}: {error}") from error
        self._reader = self._socket.makefile("r", encoding="utf-8")
        self._last_id = 0

    def __enter__(self) -> ElectrumClient:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self._reader.close()
        self._socket.close()

    # JSON-RPC results have no static type: callers index them by the documented shape of each method.
    def call(self, method: str, *params: int | str) -> Any:  # noqa: ANN401
        self._last_id += 1
        request = {"jsonrpc": "2.0", "id": self._last_id, "method": method, "params": list(params)}
        self._socket.sendall((json.dumps(request) + "\n").encode())
        while True:
            line = self._reader.readline()
            if not line:
                raise ConnectionError(f"the server closed the connection during {method}")
            response = json.loads(line)
            # Subscriptions can send notifications, which have no id.
            if response.get("id") == self._last_id:
                break
        if response.get("error"):
            raise RuntimeError(f"{method} failed: {response['error']}")
        return response["result"]


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def expect(self, condition: bool, description: str, found: object = None) -> None:
        if condition:
            print(f"ok: {description}")
        else:
            print(f"FAIL: {description} (found: {found})", file=sys.stderr)
            self.failures += 1


def wait_for_tip(client: ElectrumClient, tip: int, timeout: float) -> None:
    """Waits until the server has indexed the node's tip."""
    deadline = time.monotonic() + timeout
    while (height := client.call("blockchain.headers.subscribe")["height"]) < tip:
        if time.monotonic() > deadline:
            raise TimeoutError(f"the server is at height {height} after {timeout}s, the node at {tip}")
        time.sleep(0.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1", help="Electrum server host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=51001, help="Electrum server TCP port (default: %(default)s)")
    parser.add_argument(
        "--timeout", type=float, default=30, help="seconds to wait for the server (default: %(default)s)"
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    activation = int(json.loads(knots("getdeploymentinfo"))["blake2b"]["height"])
    tip = int(knots("getblockcount"))
    if tip < activation + MARGIN:
        print(f"FAIL: the chain is at height {tip}, mine past {activation + MARGIN} first", file=sys.stderr)
        return 1

    start = activation - MARGIN
    count = 2 * MARGIN
    expected = [knots_header(height) for height in range(start, start + count)]
    expected_sizes = [LEGACY_HEADER_SIZE] * MARGIN + [V2_HEADER_SIZE] * MARGIN
    checks = Checks()

    with ElectrumClient(args.host, args.port, args.timeout) as client:
        client.call("server.version", "xbt-infra-check", "1.4")
        wait_for_tip(client, tip, args.timeout)
        result = client.call("blockchain.block.headers", start, count)
        headers = [header.hex() for header in split_headers(bytes.fromhex(result["hex"]))]
        checks.expect(
            result["count"] == count and len(headers) == count,
            f"protocol 1.4 returns {count} headers from height {start}",
            f"count {result['count']}, split into {len(headers)} headers",
        )
        sizes = [len(header) // 2 for header in headers]
        checks.expect(
            sizes == expected_sizes, "headers are 80 bytes before the activation and 164 bytes from it", sizes
        )
        checks.expect(headers == expected, "protocol 1.4 headers match the node's", headers)
        checks.expect(
            client.call("blockchain.block.header", activation) == expected[MARGIN],
            f"blockchain.block.header at the activation height {activation} matches the node's",
        )
        subscription = client.call("blockchain.headers.subscribe")
        checks.expect(
            subscription["hex"] == knots_header(subscription["height"]),
            "blockchain.headers.subscribe returns the node's header at the tip",
            subscription,
        )

    with ElectrumClient(args.host, args.port, args.timeout) as client:
        client.call("server.version", "xbt-infra-check", "1.6")
        result = client.call("blockchain.block.headers", start, count)
        checks.expect(
            result.get("headers") == expected, "protocol 1.6 returns the headers as a list matching the node's", result
        )

    if checks.failures:
        print(f"{checks.failures} check(s) failed", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = parse_args()
    try:
        return run(args)
    except subprocess.CalledProcessError as error:
        print(f"FAIL: bitcoin-cli failed, is the environment up? {error.stderr.strip()}", file=sys.stderr)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
