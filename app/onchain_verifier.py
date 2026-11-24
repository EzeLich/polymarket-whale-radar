"""
Real On-Chain Payment Verifier (Zero Custody, Zero Payment Processor KYC).
Verifies real ERC-20 USDC/USDT transfers on Base / Arbitrum / Polygon and
SPL USDC/USDT transfers on Solana directly via public JSON-RPC endpoints.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import StudioConfig

# keccak256("Transfer(address,address,uint256)")
ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


@dataclass
class VerificationResult:
    verified: bool
    network: str
    token: str
    amount_usd: float
    recipient: str
    sender: str
    tx_hash: str
    reason: str
    raw_proof: dict[str, Any]


def _rpc_post(url: str, payload: dict[str, Any], timeout_s: float = 10.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "chainguard-studio/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def decode_evm_stablecoin_transfer(
    receipt: dict[str, Any],
    network: str,
    expected_recipient: str,
    min_amount_usd: float,
) -> VerificationResult:
    """Pure deterministic parser for eth_getTransactionReceipt logs (unit-testable offline)."""
    tx_hash = str(receipt.get("transactionHash") or "")
    status_hex = str(receipt.get("status") or "0x0").lower()
    if status_hex != "0x1":
        return VerificationResult(
            verified=False,
            network=network,
            token="",
            amount_usd=0.0,
            recipient=expected_recipient,
            sender="",
            tx_hash=tx_hash,
            reason="Transaction failed or not confirmed (status != 0x1).",
            raw_proof={"status": status_hex},
        )

    target_addr = expected_recipient.lower().removeprefix("0x").rjust(64, "0")
    contracts = StudioConfig.EVM_STABLECOINS.get(network, {})
    contract_lookup = {addr.lower(): (sym, dec) for sym, (addr, dec) in contracts.items()}

    for log in receipt.get("logs") or []:
        log_addr = str(log.get("address") or "").lower()
        topics = [str(t).lower() for t in (log.get("topics") or [])]
        if log_addr not in contract_lookup or len(topics) < 3:
            continue
        if topics[0] != ERC20_TRANSFER_TOPIC:
            continue
        to_topic = topics[2].removeprefix("0x").rjust(64, "0")
        if to_topic != target_addr:
            continue
        raw_data = str(log.get("data") or "0x0")
        raw_int = int(raw_data, 16)
        sym, decimals = contract_lookup[log_addr]
        amount_usd = raw_int / (10**decimals)
        sender = "0x" + topics[1].removeprefix("0x")[-40:]
        if amount_usd + 1e-6 >= min_amount_usd:
            return VerificationResult(
                verified=True,
                network=network,
                token=sym,
                amount_usd=round(amount_usd, 6),
                recipient=expected_recipient,
                sender=sender,
                tx_hash=tx_hash,
                reason=f"Verified on-chain {amount_usd:.2f} {sym} transfer on {network}.",
                raw_proof={
                    "blockNumber": receipt.get("blockNumber"),
                    "contract": log_addr,
                    "token": sym,
                    "amount_usd": amount_usd,
                },
            )
        return VerificationResult(
            verified=False,
            network=network,
            token=sym,
            amount_usd=round(amount_usd, 6),
            recipient=expected_recipient,
            sender=sender,
            tx_hash=tx_hash,
            reason=f"Transfer amount ${amount_usd:.2f} {sym} is below required ${min_amount_usd:.2f}.",
            raw_proof={"amount_usd": amount_usd, "required_usd": min_amount_usd},
        )

    return VerificationResult(
        verified=False,
        network=network,
        token="",
        amount_usd=0.0,
        recipient=expected_recipient,
        sender="",
        tx_hash=tx_hash,
        reason=f"No matching USDC/USDT Transfer event to {expected_recipient} found in receipt.",
        raw_proof={"logs_checked": len(receipt.get("logs") or [])},
    )


def decode_solana_stablecoin_transfer(
    tx_result: dict[str, Any],
    expected_recipient: str,
    min_amount_usd: float,
    tx_sig: str,
) -> VerificationResult:
    """Pure deterministic parser for Solana getTransaction SPL token balance deltas."""
    meta = tx_result.get("meta") or {}
    if meta.get("err") is not None:
        return VerificationResult(
            verified=False,
            network="solana",
            token="",
            amount_usd=0.0,
            recipient=expected_recipient,
            sender="",
            tx_hash=tx_sig,
            reason=f"Solana transaction failed: {meta.get('err')}",
            raw_proof={"err": meta.get("err")},
        )

    mint_lookup = {mint: sym for sym, (mint, _) in StudioConfig.SOLANA_STABLECOINS.items()}
    pre_map: dict[tuple[str, str], float] = {}
    for entry in meta.get("preTokenBalances") or []:
        owner = str(entry.get("owner") or "")
        mint = str(entry.get("mint") or "")
        amt = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
        if owner == expected_recipient and mint in mint_lookup:
            pre_map[(owner, mint)] = amt

    for entry in meta.get("postTokenBalances") or []:
        owner = str(entry.get("owner") or "")
        mint = str(entry.get("mint") or "")
        if owner != expected_recipient or mint not in mint_lookup:
            continue
        post_amt = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
        delta = post_amt - pre_map.get((owner, mint), 0.0)
        sym = mint_lookup[mint]
        if delta + 1e-6 >= min_amount_usd:
            return VerificationResult(
                verified=True,
                network="solana",
                token=sym,
                amount_usd=round(delta, 6),
                recipient=expected_recipient,
                sender="solana_payer",
                tx_hash=tx_sig,
                reason=f"Verified on-chain {delta:.2f} {sym} SPL transfer on Solana.",
                raw_proof={"slot": tx_result.get("slot"), "mint": mint, "delta": delta},
            )
        if delta > 0:
            return VerificationResult(
                verified=False,
                network="solana",
                token=sym,
                amount_usd=round(delta, 6),
                recipient=expected_recipient,
                sender="solana_payer",
                tx_hash=tx_sig,
                reason=f"Transfer amount ${delta:.2f} {sym} is below required ${min_amount_usd:.2f}.",
                raw_proof={"delta": delta, "required_usd": min_amount_usd},
            )

    return VerificationResult(
        verified=False,
        network="solana",
        token="",
        amount_usd=0.0,
        recipient=expected_recipient,
        sender="",
        tx_hash=tx_sig,
        reason=f"No matching USDC/USDT SPL credit to {expected_recipient} found in transaction.",
        raw_proof={},
    )


class OnChainPaymentVerifier:
    """Queries live public RPCs to verify real stablecoin transfers to Phantom or creator wallets."""

    def verify(
        self,
        network: str,
        tx_hash: str,
        min_amount_usd: float,
        expected_recipient: str = "",
    ) -> VerificationResult:
        net = network.strip().lower()
        tx = tx_hash.strip()
        if not tx:
            return VerificationResult(
                verified=False,
                network=net,
                token="",
                amount_usd=0.0,
                recipient="",
                sender="",
                tx_hash=tx,
                reason="Empty transaction hash.",
                raw_proof={},
            )

        if net in StudioConfig.EVM_RPCS:
            target = (expected_recipient or StudioConfig.PHANTOM_EVM_ADDRESS).strip()
            return self._verify_evm(net, tx, min_amount_usd, target)
        if net == "solana":
            target = (expected_recipient or StudioConfig.PHANTOM_SOL_ADDRESS).strip()
            return self._verify_solana(tx, min_amount_usd, target)
        return VerificationResult(
            verified=False,
            network=net,
            token="",
            amount_usd=0.0,
            recipient="",
            sender="",
            tx_hash=tx,
            reason=f"Unsupported network '{net}'. Use base, arbitrum, polygon, bsc, or solana.",
            raw_proof={},
        )

    def _verify_evm(
        self,
        network: str,
        tx_hash: str,
        min_amount_usd: float,
        expected_recipient: str = "",
    ) -> VerificationResult:
        target = (expected_recipient or StudioConfig.PHANTOM_EVM_ADDRESS).strip()
        rpc_url = StudioConfig.EVM_RPCS[network]
        try:
            resp = _rpc_post(
                rpc_url,
                {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]},
            )
            receipt = resp.get("result")
            if not receipt:
                return VerificationResult(
                    verified=False,
                    network=network,
                    token="",
                    amount_usd=0.0,
                    recipient=target,
                    sender="",
                    tx_hash=tx_hash,
                    reason="Transaction receipt not found yet on RPC (wait a few seconds for confirmation).",
                    raw_proof={"rpc": rpc_url},
                )
            return decode_evm_stablecoin_transfer(
                receipt=receipt,
                network=network,
                expected_recipient=target,
                min_amount_usd=min_amount_usd,
            )
        except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError) as exc:
            return VerificationResult(
                verified=False,
                network=network,
                token="",
                amount_usd=0.0,
                recipient=target,
                sender="",
                tx_hash=tx_hash,
                reason=f"RPC verification error on {network}: {exc}",
                raw_proof={},
            )

    def _verify_solana(
        self,
        tx_sig: str,
        min_amount_usd: float,
        expected_recipient: str = "",
    ) -> VerificationResult:
        target = (expected_recipient or StudioConfig.PHANTOM_SOL_ADDRESS).strip()
        try:
            resp = _rpc_post(
                StudioConfig.SOLANA_RPC_URL,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [tx_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                },
            )
            tx_result = resp.get("result")
            if not tx_result:
                return VerificationResult(
                    verified=False,
                    network="solana",
                    token="",
                    amount_usd=0.0,
                    recipient=target,
                    sender="",
                    tx_hash=tx_sig,
                    reason="Solana transaction not found yet on RPC.",
                    raw_proof={},
                )
            return decode_solana_stablecoin_transfer(
                tx_result=tx_result,
                expected_recipient=target,
                min_amount_usd=min_amount_usd,
                tx_sig=tx_sig,
            )
        except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError) as exc:
            return VerificationResult(
                verified=False,
                network="solana",
                token="",
                amount_usd=0.0,
                recipient=target,
                sender="",
                tx_hash=tx_sig,
                reason=f"Solana RPC error: {exc}",
                raw_proof={},
            )
