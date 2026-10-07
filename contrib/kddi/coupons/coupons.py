#!/usr/bin/env python3
"""KDDI Marzban Stock Coupon Manager & Cleaner CLI

Implements coupon batch creation and expiration/quota cleanup for Marzban v0.8.4.
Strictly relies on the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Constants & Defaults
DEFAULT_CONFIG_PATH = "/root/.secrets/auth/kddi-marzban.json"
DEFAULT_OUTPUT_DIR = "/root/.secrets/proxy/coupon-batches"
DEFAULT_API_URL = "http://127.0.0.1:18002"
DEFAULT_PREFIX = "kc_"
DEFAULT_REALITY_INBOUND_TAG = "kddi-reality"
BYTES_PER_GB = 1_000_000_000  # Decimal GB, as advertised; not GiB.
SECONDS_PER_DAY = 86400

# Regex for Marzban username validation: 3 to 32 characters, [a-zA-Z0-9-_@.] with optional underscore separators
USERNAME_PATTERN = re.compile(r"^(?=\w{3,32}\b)[a-zA-Z0-9-_@.]+(?:_[a-zA-Z0-9-_@.]+)*$")


class CouponError(Exception):
    """Base exception for coupon operations."""


class ConfigError(CouponError):
    """Raised when configuration file is invalid or missing."""


class APIError(CouponError):
    """Raised when Marzban API returns an error or fails."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


@dataclass
class MarzbanConfig:
    api_url: str
    username: str
    password: str
    public_url: str
    share_gateway_config: Optional[str] = None


def sanitize_message(msg: str) -> str:
    """Sanitize message to ensure credentials, tokens, and passwords are not printed."""
    # Redact Bearer tokens
    msg = re.sub(r'(Bearer\s+)[A-Za-z0-9\-_.]+', r'\1[REDACTED]', msg)
    # Redact access_token in json
    msg = re.sub(r'("access_token"\s*:\s*")[^"]+(")', r'\1[REDACTED]\2', msg)
    # Redact password in json or query
    msg = re.sub(r'("password"\s*:\s*")[^"]+(")', r'\1[REDACTED]\2', msg)
    msg = re.sub(r'(password=)[^& \n]+', r'\1[REDACTED]', msg)
    return msg


def log_info(msg: str) -> None:
    sys.stdout.write(f"[INFO] {sanitize_message(msg)}\n")
    sys.stdout.flush()


def log_error(msg: str) -> None:
    sys.stderr.write(f"[ERROR] {sanitize_message(msg)}\n")
    sys.stderr.flush()


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> MarzbanConfig:
    """Load and validate credentials from JSON file. No credentials logged."""
    if not os.path.exists(config_path):
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ConfigError(f"Failed to read or parse configuration file: {e}")

    api_url = data.get("api_url", DEFAULT_API_URL).rstrip("/")
    username = data.get("username")
    password = data.get("password")
    public_url = data.get("public_url", "").rstrip("/")

    if not username or not password:
        raise ConfigError("Missing required fields in configuration: 'username' and 'password' are required")

    return MarzbanConfig(
        api_url=api_url,
        username=username,
        password=password,
        public_url=public_url,
        share_gateway_config=data.get("share_gateway_config"),
    )


class MarzbanClient:
    """HTTP Client for Marzban v0.8.4 API using standard library urllib."""

    def __init__(self, config: MarzbanConfig, timeout: int = 30):
        self.config = config
        self.timeout = timeout
        self._token: Optional[str] = None

    def _request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        auth_required: bool = True,
    ) -> Tuple[int, Dict[str, Any]]:
        if headers is None:
            headers = {}

        if auth_required:
            if not self._token:
                self.authenticate()
            headers["Authorization"] = f"Bearer {self._token}"

        url = f"{self.config.api_url}{path}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_code = resp.getcode()
                raw_body = resp.read().decode("utf-8")
                try:
                    resp_json = json.loads(raw_body) if raw_body else {}
                except json.JSONDecodeError:
                    resp_json = {"raw": raw_body}
                return resp_code, resp_json
        except urllib.error.HTTPError as e:
            raw_err = e.read().decode("utf-8") if e.fp else ""
            e.close()
            sanitized_err = sanitize_message(raw_err)
            raise APIError(
                f"HTTP {e.code} for {method} {path}: {sanitized_err}",
                status_code=e.code,
                response_body=sanitized_err,
            ) from None
        except urllib.error.URLError as e:
            raise APIError(f"Network error connecting to API: {e.reason}") from None
        except Exception as e:
            raise APIError(f"Unexpected request error: {e}") from None

    def authenticate(self) -> str:
        """Authenticate with Marzban admin token endpoint (/api/admin/token)."""
        form_data = urllib.parse.urlencode({
            "username": self.config.username,
            "password": self.config.password,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        try:
            code, data = self._request(
                "POST",
                "/api/admin/token",
                headers=headers,
                data=form_data,
                auth_required=False,
            )
            token = data.get("access_token")
            if not token:
                raise APIError("Token response did not contain 'access_token'")
            self._token = token
            return self._token
        except APIError as e:
            raise APIError(f"Authentication failed: {e.args[0]}") from None

    def get_user(self, username: str) -> Dict[str, Any]:
        """Fetch user by username (/api/user/{username})."""
        encoded_username = urllib.parse.quote(username)
        code, data = self._request("GET", f"/api/user/{encoded_username}")
        return data

    def create_user(self, user_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create user (/api/user)."""
        body = json.dumps(user_payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        code, data = self._request("POST", "/api/user", headers=headers, data=body)
        return data

    def modify_user(self, username: str, user_modify_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Modify user status/quota (/api/user/{username})."""
        encoded_username = urllib.parse.quote(username)
        body = json.dumps(user_modify_payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        code, data = self._request("PUT", f"/api/user/{encoded_username}", headers=headers, data=body)
        return data

    def delete_user(self, username: str) -> bool:
        """Delete user (/api/user/{username})."""
        encoded_username = urllib.parse.quote(username)
        code, data = self._request("DELETE", f"/api/user/{encoded_username}")
        return code == 200

    def get_users(self, offset: int = 0, limit: int = 100, search: Optional[str] = None) -> Dict[str, Any]:
        """List users (/api/users)."""
        params = {"offset": offset, "limit": limit}
        if search:
            params["search"] = search
        query_str = urllib.parse.urlencode(params)
        code, data = self._request("GET", f"/api/users?{query_str}")
        return data


def generate_coupon_username(prefix: str = DEFAULT_PREFIX, label: str = "stock") -> str:
    """Generate a random compliant Marzban username."""
    # Prefix + label + random hex (total length <= 32)
    # Sanitize label: letters, digits, underscores only
    clean_label = re.sub(r"[^a-zA-Z0-9_]", "", label)
    if not clean_label:
        clean_label = "stock"
    # Ensure prefix format
    clean_prefix = re.sub(r"[^a-zA-Z0-9_]", "", prefix)
    random_part = secrets.token_hex(4)  # 8 hex chars
    username = f"{clean_prefix}{clean_label}_{random_part}"
    if len(username) > 32:
        username = f"{clean_prefix}{random_part}"
    if not USERNAME_PATTERN.match(username):
        username = f"kc_{secrets.token_hex(6)}"
    return username


def build_subscription_url(public_url: str, raw_sub_url: str) -> str:
    """Format final subscription URL, rewriting host to public_url if configured."""
    if not raw_sub_url:
        return ""
    if not public_url:
        return raw_sub_url

    parsed_sub = urllib.parse.urlparse(raw_sub_url)
    parsed_pub = urllib.parse.urlparse(public_url)

    # Use public_url's scheme and netloc, preserving sub url's path and query
    full_path = parsed_sub.path
    if parsed_pub.path and parsed_pub.path != "/":
        pub_base = parsed_pub.path.rstrip("/")
        full_path = f"{pub_base}{full_path}"

    replaced = parsed_sub._replace(
        scheme=parsed_pub.scheme or "http",
        netloc=parsed_pub.netloc,
        path=full_path,
    )
    return urllib.parse.urlunparse(replaced)


def execute_batch(
    client: MarzbanClient,
    count: int,
    traffic_gb: int,
    days: int,
    label: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
    reality_inbound_tag: str = DEFAULT_REALITY_INBOUND_TAG,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Create a batch of on_hold stock coupons, verify read-back, and save exports."""
    if count <= 0:
        raise ValueError("Count must be greater than 0")
    if traffic_gb <= 0:
        raise ValueError("Traffic GB must be greater than 0")
    if days <= 0:
        raise ValueError("Days must be greater than 0")

    batch_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    batch_id = f"batch_{batch_timestamp}_{secrets.token_hex(3)}"

    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    # Ensure output_dir permissions are 0700
    os.chmod(output_dir, 0o700)

    txt_file = os.path.join(output_dir, f"{batch_id}.txt")
    csv_file = os.path.join(output_dir, f"{batch_id}.csv")
    manifest_file = os.path.join(output_dir, f"{batch_id}.json")

    if not overwrite:
        for p in (txt_file, csv_file, manifest_file):
            if os.path.exists(p):
                raise CouponError(f"Target file already exists and overwrite is disabled: {p}")

    data_limit_bytes = int(traffic_gb * BYTES_PER_GB)
    on_hold_expire_duration_seconds = int(days * SECONDS_PER_DAY)

    records = []
    created_usernames = []

    log_info(f"Starting batch creation: id={batch_id}, count={count}, traffic={traffic_gb}GB, days={days}d, inbound={reality_inbound_tag}")

    for i in range(count):
        coupon_id = f"cpn_{secrets.token_hex(6)}"
        username = generate_coupon_username(prefix=prefix, label=label)

        # Ensure uniqueness in current run
        while username in created_usernames:
            username = generate_coupon_username(prefix=prefix, label=label)

        note_data = json.dumps({"coupon_id": coupon_id, "batch_id": batch_id, "label": label})

        payload = {
            "username": username,
            "proxies": {
                "vless": {"flow": "xtls-rprx-vision"}
            },
            "inbounds": {
                "vless": [reality_inbound_tag]
            },
            "expire": None,
            "data_limit": data_limit_bytes,
            "data_limit_reset_strategy": "no_reset",
            "status": "on_hold",
            "note": note_data,
            "on_hold_timeout": None,
            "on_hold_expire_duration": on_hold_expire_duration_seconds,
            "next_plan": None,
        }

        # 1. Create user
        try:
            created_resp = client.create_user(payload)
        except APIError as e:
            log_error(f"Failed to create coupon user {username}: {e}")
            raise CouponError(f"Batch aborted: failed to create coupon {username}") from e

        # Durable issuance journal before read-back/export: never lose successful
        # creations if a later item or the API fails midway through a batch.
        journal_file = os.path.join(output_dir, f"{batch_id}.issued.jsonl")
        journal_fd = os.open(journal_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(journal_fd, "a", encoding="utf-8") as journal:
            journal.write(json.dumps({"username": username, "coupon_id": coupon_id,
                                      "subscription_url": created_resp.get("subscription_url"),
                                      "batch_id": batch_id}) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        # 2. Strict read-back verification
        try:
            read_back = client.get_user(username)
            if read_back.get("username") != username:
                raise CouponError(f"Read-back username mismatch: expected {username}, got {read_back.get('username')}")
            if read_back.get("status") != "on_hold":
                raise CouponError(f"Read-back status mismatch for {username}: expected on_hold, got {read_back.get('status')}")
        except APIError as e:
            log_error(f"Read-back verification failed for user {username}: {e}")
            raise CouponError(f"Batch aborted: read-back verification failed for {username}") from e

        raw_sub = read_back.get("subscription_url", "")
        if client.config.share_gateway_config:
            from share_links import ShareLinks, ShareLinkError
            gateway = ShareLinks(client.config.share_gateway_config)
            try:
                final_sub_url = gateway.ensure(username, raw_sub)
            except ShareLinkError as e:
                raise CouponError("Share registration failed; recover created user from issuance journal") from e
            finally:
                gateway.close()
        else:
            final_sub_url = build_subscription_url(client.config.public_url, raw_sub)

        records.append({
            "coupon_id": coupon_id,
            "username": username,
            "status": read_back.get("status"),
            "data_limit_gb": traffic_gb,
            "data_limit_bytes": data_limit_bytes,
            "days": days,
            "on_hold_expire_duration": on_hold_expire_duration_seconds,
            "inbound_tag": reality_inbound_tag,
            "subscription_url": final_sub_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        created_usernames.append(username)

    # Write files with 0600 mode
    # 1. TXT file (one link per line)
    with open(txt_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(f"{r['subscription_url']}\n")
    os.chmod(txt_file, 0o600)

    # 2. CSV file
    csv_headers = ["coupon_id", "username", "status", "data_limit_gb", "days", "inbound_tag", "subscription_url", "created_at"]
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_headers)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "coupon_id": r["coupon_id"],
                "username": r["username"],
                "status": r["status"],
                "data_limit_gb": r["data_limit_gb"],
                "days": r["days"],
                "inbound_tag": r["inbound_tag"],
                "subscription_url": r["subscription_url"],
                "created_at": r["created_at"],
            })
    os.chmod(csv_file, 0o600)

    # 3. JSON Manifest
    manifest_data = {
        "batch_id": batch_id,
        "label": label,
        "count": len(records),
        "traffic_gb": traffic_gb,
        "days": days,
        "inbound_tag": reality_inbound_tag,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "coupons": [
            {
                "coupon_id": r["coupon_id"],
                "username": r["username"],
                "status": r["status"],
                "subscription_url": r["subscription_url"],
            }
            for r in records
        ],
    }
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)
    os.chmod(manifest_file, 0o600)

    log_info(f"Batch completed successfully. Generated {len(records)} coupons.")
    return {
        "batch_id": batch_id,
        "count": len(records),
        "txt_file": txt_file,
        "csv_file": csv_file,
        "manifest_file": manifest_file,
    }


def execute_cleanup(
    client: MarzbanClient,
    prefix: str = DEFAULT_PREFIX,
    dry_run: bool = False,
    audit_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan and cleanup expired or quota-exceeded users matching the coupon prefix.

    Rules:
    - Only process usernames starting with prefix (e.g. kc_).
    - NEVER touch or delete 'on_hold' stock coupons!
    - Evaluate active/limited/expired/disabled users.
    - If active and (used_traffic >= data_limit or (expire and expire <= now)):
        1. Step 1: First disable user (modify status to 'disabled')
        2. Step 2: Delete user and subscription
    - If status is already limited or expired or disabled:
        Delete user directly (or ensure disabled then delete).
    - If update or delete fails, do NOT claim success.
    - Log audit trail (coupon_id, username, action, reason, timestamp) - NO destination IPs/domains stored.
    """
    now_ts = time.time()
    log_info(f"Starting cleanup check (prefix='{prefix}', dry_run={dry_run})")

    # Fetch all users (handle pagination)
    offset = 0
    limit = 100
    candidate_users: List[Dict[str, Any]] = []

    while True:
        resp = client.get_users(offset=offset, limit=limit)
        users = resp.get("users", [])
        if not users:
            break
        for u in users:
            username = u.get("username", "")
            if username.startswith(prefix):
                candidate_users.append(u)
        offset += limit
        if offset >= resp.get("total", 0):
            break

    log_info(f"Found {len(candidate_users)} users matching prefix '{prefix}'")

    processed = []
    skipped_on_hold = 0
    errors = []

    for user in candidate_users:
        username = user.get("username", "")
        status = user.get("status", "")
        data_limit = user.get("data_limit") or 0
        used_traffic = user.get("used_traffic") or 0
        expire = user.get("expire")

        # NEVER delete on_hold stock coupons!
        if status == "on_hold":
            skipped_on_hold += 1
            continue

        quota_exceeded = (data_limit > 0 and used_traffic >= data_limit)
        time_expired = (expire is not None and expire > 0 and expire <= now_ts)

        should_cleanup = False
        reason = ""

        if status in ("limited", "expired"):
            should_cleanup = True
            reason = f"Status is {status}"
        elif status == "disabled":
            # Disabled users that also hit quota or expiration or were disabled previously
            if quota_exceeded or time_expired:
                should_cleanup = True
                reason = "Disabled and exceeded quota/time"
        elif status == "active":
            if quota_exceeded and time_expired:
                should_cleanup = True
                reason = "Active but exceeded traffic quota and expiration date"
            elif quota_exceeded:
                should_cleanup = True
                reason = f"Active but traffic quota exceeded ({used_traffic}/{data_limit} bytes)"
            elif time_expired:
                should_cleanup = True
                reason = f"Active but expired (expired at {expire}, now {int(now_ts)})"

        if not should_cleanup:
            continue

        log_info(f"Targeting user '{username}' for cleanup: {reason}")

        if dry_run:
            processed.append({
                "username": username,
                "action": "dry_run_cleanup",
                "reason": reason,
                "status": "success",
            })
            continue

        # Real cleanup:
        # Step 1: If active, modify status to disabled first
        try:
            if status == "active":
                modify_res = client.modify_user(username, {"status": "disabled"})
                # Read-back verification
                check_mod = client.get_user(username)
                if check_mod.get("status") != "disabled":
                    raise CouponError(f"Verification failed: expected disabled status, got {check_mod.get('status')}")
        except Exception as e:
            err_msg = f"Failed to disable user {username} prior to deletion: {e}"
            log_error(err_msg)
            errors.append({"username": username, "error": err_msg})
            continue

        # Step 2: Delete user
        try:
            delete_ok = client.delete_user(username)
            if not delete_ok:
                raise CouponError("Delete API returned non-200 code")

            # Verify deletion with read-back (should return 404)
            try:
                client.get_user(username)
                # If get_user did not raise 404, user still exists!
                raise CouponError(f"Verification failed: user {username} still exists after deletion")
            except APIError as e:
                if e.status_code == 404:
                    # Verified successfully deleted
                    pass
                else:
                    raise CouponError(f"Unexpected error verifying deletion of {username}: {e}") from e

            processed.append({
                "username": username,
                "action": "disabled_and_deleted",
                "reason": reason,
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            log_info(f"Successfully cleaned up user '{username}'")

        except Exception as e:
            err_msg = f"Failed to delete user {username}: {e}"
            log_error(err_msg)
            errors.append({"username": username, "error": err_msg})
            continue

    # Write audit log if configured or requested
    if audit_file and processed:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(audit_file)), mode=0o700, exist_ok=True)
            with open(audit_file, "a", encoding="utf-8") as f:
                for entry in processed:
                    f.write(json.dumps(entry) + "\n")
            os.chmod(audit_file, 0o600)
        except Exception as e:
            log_error(f"Failed to write audit log: {e}")

    return {
        "candidate_count": len(candidate_users),
        "skipped_on_hold": skipped_on_hold,
        "cleaned_count": len(processed),
        "error_count": len(errors),
        "processed": processed,
        "errors": errors,
    }


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Marzban v0.8.4 Stock Coupon & Cleanup CLI (Python standard library only)"
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to JSON credentials file (default: {DEFAULT_CONFIG_PATH})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommand to execute")

    # batch subcommand
    batch_parser = subparsers.add_parser("batch", help="Generate a batch of stock coupons")
    batch_parser.add_argument("--count", type=int, default=1, help="Number of coupons to create")
    batch_parser.add_argument("--traffic-gb", type=int, default=1, help="Data limit in GB (default: 1)")
    batch_parser.add_argument("--days", type=int, default=7, help="On-hold expire duration in days (default: 7)")
    batch_parser.add_argument("--label", default="stock", help="Label tag for this batch (default: stock)")
    batch_parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to store output files (default: {DEFAULT_OUTPUT_DIR})",
    )
    batch_parser.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"Username prefix (default: {DEFAULT_PREFIX})")
    batch_parser.add_argument(
        "--inbound-tag",
        default=DEFAULT_REALITY_INBOUND_TAG,
        help=f"Inbound tag to enable (default: {DEFAULT_REALITY_INBOUND_TAG})",
    )
    batch_parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting existing files (default: False)",
    )

    # cleanup subcommand
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up expired or exhausted coupons")
    cleanup_parser.add_argument(
        "--once",
        action="store_true",
        required=True,
        help="Run cleanup pass once",
    )
    cleanup_parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Only target usernames with this prefix (default: {DEFAULT_PREFIX})",
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate cleanup without modifying or deleting users",
    )
    cleanup_parser.add_argument(
        "--audit-file",
        default="/root/.secrets/proxy/coupon-cleanup-audit.log",
        help="Minimal audit log path for deletion records (no destinations stored)",
    )

    return parser.parse_args(args)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    try:
        config = load_config(args.config)
        client = MarzbanClient(config)

        if args.command == "batch":
            res = execute_batch(
                client=client,
                count=args.count,
                traffic_gb=args.traffic_gb,
                days=args.days,
                label=args.label,
                output_dir=args.output_dir,
                prefix=args.prefix,
                reality_inbound_tag=args.inbound_tag,
                overwrite=args.overwrite,
            )
            # Output absolute file paths only - DO NOT echo the subscription links to stdout!
            print(f"BATCH_ID: {res['batch_id']}")
            print(f"COUNT: {res['count']}")
            print(f"TXT_FILE: {res['txt_file']}")
            print(f"CSV_FILE: {res['csv_file']}")
            print(f"MANIFEST_FILE: {res['manifest_file']}")
            return 0

        elif args.command == "cleanup":
            res = execute_cleanup(
                client=client,
                prefix=args.prefix,
                dry_run=args.dry_run,
                audit_file=args.audit_file,
            )
            print(f"CANDIDATES: {res['candidate_count']}")
            print(f"SKIPPED_ON_HOLD: {res['skipped_on_hold']}")
            print(f"CLEANED: {res['cleaned_count']}")
            print(f"ERRORS: {res['error_count']}")
            if res["error_count"] > 0:
                return 1
            return 0

        else:
            log_error(f"Unknown command: {args.command}")
            return 1

    except CouponError as e:
        log_error(f"Operation failed: {e}")
        return 1
    except Exception as e:
        log_error(f"Unhandled error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
