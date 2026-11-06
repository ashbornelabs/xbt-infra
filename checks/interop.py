#!/usr/bin/env python3
"""Checks that the two Lightning implementations interoperate on the XBT regtest chain.

Core Lightning opens a channel to LND, each pays an invoice of the other, and Core Lightning closes the
channel cooperatively. Along the way it checks the XBT rules of the lightning-blake2b specification: both
nodes advertise option_blake2b and option_unified_sigs, the channel type is static_remotekey, anchors and
unified_sigs, and the closing transaction is signed with SIGHASH_ALL_UNIFIED. It also reports whether each
implementation's invoices carry option_blake2b.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

XBT = Path(__file__).resolve().parent.parent / "bin" / "xbt"
OPTION_BLAKE2B = 512
OPTION_UNIFIED_SIGS = 514
XBT_CHANNEL_TYPE = [12, 22, OPTION_UNIFIED_SIGS]
SIGHASH_ALL_UNIFIED = 0x21
FUNDING_BTC = "1"
CHANNEL_SAT = 1_000_000
PAYMENT_SAT = 50_000
# LND only holds what it was paid, and must keep its channel reserve (1% of the capacity): pay back less.
RETURN_PAYMENT_SAT = 20_000
CONFIRMATIONS = 6
# Regtest has no fee estimates, so the nodes' default closing fees differ: accept any reasonable fee.
CLOSING_FEERANGE = '["253perkw","100000perkw"]'


def xbt(*args: str) -> str:
    result = subprocess.run([str(XBT), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


# Node replies are JSON objects with no static type: callers index them by each command's documented shape.
def cln(*args: str) -> Any:  # noqa: ANN401
    # -N none drops the progress notifications that some commands print before their JSON reply.
    return json.loads(xbt("cln", "-N", "none", *args))


def lnd(*args: str) -> Any:  # noqa: ANN401
    return json.loads(xbt("lnd", *args))


def knots(*args: str) -> Any:  # noqa: ANN401
    return json.loads(xbt("cli", *args))


def json_values(text: str) -> list[Any]:
    """Decodes a sequence of JSON values written one after the other."""
    decoder = json.JSONDecoder()
    values = []
    rest = text.lstrip()
    while rest:
        value, end = decoder.raw_decode(rest)
        values.append(value)
        rest = rest[end:].lstrip()
    return values


def has_bit(features: int, bit: int) -> bool:
    return (features >> bit) & 1 == 1


def has_either_bit(features: int, even_bit: int) -> bool:
    """Whether a feature is set as required (even bit) or optional (odd bit)."""
    return has_bit(features, even_bit) or has_bit(features, even_bit + 1)


def wait_until(condition: Callable[[], bool], description: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out after {timeout}s waiting until {description}")
        time.sleep(0.5)


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def expect(self, condition: bool, description: str, found: object = None) -> None:
        if condition:
            print(f"ok: {description}")
        else:
            print(f"FAIL: {description} (found: {found})", file=sys.stderr)
            self.failures += 1


def fund_nodes(timeout: float) -> None:
    xbt("fund", cln("newaddr")["p2tr"], FUNDING_BTC)
    xbt("fund", lnd("newaddress", "p2wkh")["address"], FUNDING_BTC)
    wait_until(
        lambda: any(output["status"] == "confirmed" for output in cln("listfunds")["outputs"]),
        "Core Lightning sees its funds",
        timeout,
    )
    wait_until(lambda: int(lnd("walletbalance")["confirmed_balance"]) > 0, "LND sees its funds", timeout)


def check_peer_features(checks: Checks, cln_id: str, lnd_id: str, timeout: float) -> None:
    cln("connect", f"{lnd_id}@lnd:9735")
    wait_until(
        lambda: any(peer["pub_key"] == cln_id for peer in lnd("listpeers")["peers"]), "LND sees the peer", timeout
    )
    seen_by_cln = int(cln("listpeers", lnd_id)["peers"][0]["features"], 16)
    seen_by_lnd = {
        int(bit) for peer in lnd("listpeers")["peers"] if peer["pub_key"] == cln_id for bit in peer["features"]
    }
    for name, bit in (("option_blake2b", OPTION_BLAKE2B), ("option_unified_sigs", OPTION_UNIFIED_SIGS)):
        checks.expect(has_either_bit(seen_by_cln, bit), f"Core Lightning sees LND advertise {name}")
        checks.expect(bit in seen_by_lnd or bit + 1 in seen_by_lnd, f"LND sees Core Lightning advertise {name}")


def open_channel(checks: Checks, lnd_id: str, timeout: float) -> None:
    channel_type = cln("fundchannel", lnd_id, str(CHANNEL_SAT))["channel_type"]["bits"]
    checks.expect(channel_type == XBT_CHANNEL_TYPE, f"the channel type is {XBT_CHANNEL_TYPE}", channel_type)
    xbt("mine", str(CONFIRMATIONS))
    wait_until(
        lambda: cln("listpeerchannels", lnd_id)["channels"][0]["state"] == "CHANNELD_NORMAL",
        "the channel is open on Core Lightning",
        timeout,
    )
    wait_until(
        lambda: any(channel["active"] for channel in lnd("listchannels")["channels"]),
        "the channel is active on LND",
        timeout,
    )


def report_invoice_features(name: str, features: int) -> None:
    carried = "carry" if has_bit(features, OPTION_BLAKE2B) else "do not carry"
    print(f"info: {name} invoices {carried} option_blake2b")


def check_payments(checks: Checks) -> None:
    lnd_invoice = lnd("addinvoice", "--amt", str(PAYMENT_SAT))["payment_request"]
    report_invoice_features("LND", int(cln("decode", lnd_invoice)["features"], 16))
    status = cln("pay", lnd_invoice)["status"]
    checks.expect(status == "complete", "Core Lightning pays an LND invoice", status)

    label = f"interop-{secrets.token_hex(8)}"
    cln_invoice = cln("invoice", f"{RETURN_PAYMENT_SAT * 1000}msat", label, "interoperability check")["bolt11"]
    lnd_features = sum(1 << int(bit) for bit in lnd("decodepayreq", cln_invoice)["features"])
    report_invoice_features("Core Lightning", lnd_features)
    # payinvoice --json prints one JSON object per payment update: the last one is the outcome.
    outcome = json_values(xbt("lnd", "payinvoice", "--force", "--json", cln_invoice))[-1]
    checks.expect(outcome["status"] == "SUCCEEDED", "LND pays a Core Lightning invoice", outcome["status"])


def check_cooperative_close(checks: Checks, lnd_id: str) -> None:
    # A mutual close has a single transaction.
    txid = cln("-k", "close", f"id={lnd_id}", f"feerange={CLOSING_FEERANGE}")["txids"][0]
    witness = knots("getrawtransaction", txid, "true")["vin"][0]["txinwitness"]
    # A 2-of-2 P2WSH spend: an empty element, the two signatures and the script.
    signatures = witness[1:3]
    sighash_types = [int(signature[-2:], 16) for signature in signatures]
    checks.expect(
        len(witness) == 4 and sighash_types == [SIGHASH_ALL_UNIFIED] * 2,
        "both signatures of the closing transaction use SIGHASH_ALL_UNIFIED",
        witness,
    )
    xbt("mine", "1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--timeout", type=float, default=60, help="seconds to wait for each step (default: %(default)s)"
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    cln_id = cln("getinfo")["id"]
    lnd_id = lnd("getinfo")["identity_pubkey"]
    if cln("listpeerchannels")["channels"]:
        print("FAIL: Core Lightning already has channels: start from a fresh environment", file=sys.stderr)
        return 1
    checks = Checks()
    fund_nodes(args.timeout)
    check_peer_features(checks, cln_id, lnd_id, args.timeout)
    open_channel(checks, lnd_id, args.timeout)
    check_payments(checks)
    check_cooperative_close(checks, lnd_id)
    if checks.failures:
        print(f"{checks.failures} check(s) failed", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = parse_args()
    try:
        return run(args)
    except subprocess.CalledProcessError as error:
        output = (error.stderr or error.stdout or "").strip()
        print(
            f"FAIL: {' '.join(error.cmd[1:])} failed, is the environment up with --interop? {output}", file=sys.stderr
        )
    except (OSError, KeyError, ValueError) as error:
        print(f"FAIL: {error!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
