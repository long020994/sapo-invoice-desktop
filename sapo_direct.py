import base64
import csv
import ctypes
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from ctypes import wintypes
from pathlib import Path


PAGE_SIZE = 250
MAX_PAGES = 250
MAX_WORKERS = 4
LEGACY_CONFIG_PATH = Path(os.getenv("APPDATA", "")) / "SapoAIAgent" / "sapo_sync.bin"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _unprotect_bytes(encrypted):
    buffer = ctypes.create_string_buffer(encrypted)
    incoming = DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    outgoing = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(outgoing)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


def load_saved_config():
    if not LEGACY_CONFIG_PATH.exists():
        raise FileNotFoundError(
            "Chưa có cấu hình kết nối Sapo. Hãy mở biểu tượng Sapo AI Agent ở khay hệ thống "
            "và chọn 'Cấu hình đồng bộ Sapo'."
        )
    try:
        payload = json.loads(_unprotect_bytes(LEGACY_CONFIG_PATH.read_bytes()).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Không đọc được cấu hình Sapo đã mã hóa trên máy") from exc
    return validate_config(payload)


def clean_text(value):
    return str(value or "").strip()


def normalize_store(value):
    store = clean_text(value).lower()
    store = re.sub(r"^https?://", "", store).split("/", 1)[0].strip(".")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.mysapo\.(?:net|vn)", store):
        raise ValueError("Tên cửa hàng Sapo không hợp lệ")
    return store


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Cấu hình Sapo không hợp lệ")
    auth_mode = config.get("auth_mode", "basic")
    validated = {**config, "store": normalize_store(config.get("store")), "auth_mode": auth_mode}
    if auth_mode == "token":
        if not clean_text(config.get("access_token")):
            raise ValueError("Thiếu Sapo Access Token")
    elif auth_mode == "basic":
        if not clean_text(config.get("api_key")) or not clean_text(config.get("api_secret")):
            raise ValueError("Thiếu API Key hoặc API Secret của Sapo")
    else:
        raise ValueError("Kiểu xác thực Sapo không hợp lệ")
    return validated


class SapoApiClient:
    def __init__(self, config, timeout=45):
        self.config = validate_config(config)
        self.timeout = timeout

    def _headers(self):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Sapo-Invoice-Desktop/1.0",
        }
        if self.config["auth_mode"] == "token":
            headers["X-Sapo-Access-Token"] = clean_text(self.config["access_token"])
        else:
            raw = f"{self.config['api_key']}:{self.config['api_secret']}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        return headers

    def request_json(self, method, path, params=None, payload=None):
        query = urllib.parse.urlencode(params or {})
        url = f"https://{self.config['store']}{path}" + (f"?{query}" if query else "")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        last_error = None
        for attempt in range(6):
            request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as exc:
                last_error = exc
                body = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code in (401, 403):
                    raise PermissionError(
                        "Sapo từ chối cập nhật. Hãy cấp quyền 'Sản phẩm, phiên bản và danh mục: Đọc và ghi' "
                        "cho Ứng dụng riêng rồi thử lại."
                    ) from exc
                if exc.code == 422:
                    raise ValueError(f"Sapo không chấp nhận dữ liệu sản phẩm: {body}") from exc
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(12, 1.5 * (attempt + 1))
                    time.sleep(delay)
                    continue
                if exc.code < 500:
                    raise RuntimeError(f"Sapo API báo lỗi HTTP {exc.code}: {body}") from exc
            except (OSError, urllib.error.URLError, ValueError) as exc:
                last_error = exc
            if attempt < 5:
                time.sleep(min(8, 1.25 * (attempt + 1)))
        raise RuntimeError(f"Không kết nối được Sapo API: {last_error}") from last_error

    def get_json(self, path, params=None):
        return self.request_json("GET", path, params=params)

    def put_variant_sku(self, variant_id, sku):
        return self.put_variant(variant_id, {"sku": str(sku)})

    def put_variant(self, variant_id, fields):
        return self.request_json(
            "PUT",
            f"/admin/variants/{int(variant_id)}.json",
            payload={"variant": {"id": int(variant_id), **dict(fields)}},
        )

    def create_product(self, name, sku, barcode="", sale_price=0):
        variant = {
            "option1": "Default Title",
            "price": str(round(float(sale_price or 0))),
            "sku": clean_text(sku),
            "inventory_management": "bizweb",
            "inventory_quantity": 0,
        }
        if clean_text(barcode):
            variant["barcode"] = clean_text(barcode)
        response = self.request_json(
            "POST", "/admin/products.json",
            payload={"product": {"name": clean_text(name), "variants": [variant]}},
        )
        product = response.get("product") if isinstance(response, dict) else None
        variants = (product or {}).get("variants") or []
        if not product or not variants:
            raise RuntimeError("Sapo không trả về sản phẩm vừa tạo")
        return product, variants[0]


def sync_invoice_products(client, items, progress=None):
    """Tạo sản phẩm mới và cập nhật SKU/giá bán trước khi xuất đơn nhập."""
    actions = []
    for item in items:
        if item.get("new_product"):
            actions.append(("create", item, None))
            continue
        if not item.get("matched") or item.get("variant_id") is None:
            continue
        fields = {}
        if (item.get("generated_sku") or item.get("sku_changed")) and clean_text(item.get("sku")):
            fields["sku"] = clean_text(item.get("sku"))
        if item.get("barcode_changed"):
            fields["barcode"] = clean_text(item.get("barcode"))
        old_sale = round(float(item.get("system_sale_price") or 0))
        new_sale = round(float(item.get("new_sale_price") or old_sale))
        if new_sale != old_sale:
            fields["price"] = str(new_sale)
        if fields:
            actions.append(("update", item, fields))

    created = 0
    updated = 0
    total = len(actions)
    for position, (action, item, fields) in enumerate(actions, start=1):
        if action == "create":
            product, variant = client.create_product(
                item.get("sapo_name") or item.get("original_name"),
                item.get("sku"), item.get("barcode"), item.get("new_sale_price"),
            )
            item.update({
                "matched": True,
                "new_product": False,
                "product_id": product.get("id"),
                "variant_id": variant.get("id"),
                "sapo_name": product.get("name") or item.get("sapo_name"),
                "sku": clean_text(variant.get("sku") or item.get("sku")),
                "barcode": clean_text(variant.get("barcode") or item.get("barcode")),
                "system_sale_price": float(variant.get("price") or item.get("new_sale_price") or 0),
                "generated_sku": False,
            })
            created += 1
        else:
            response = client.put_variant(item["variant_id"], fields)
            variant = response.get("variant", {}) if isinstance(response, dict) else {}
            if "sku" in fields:
                item["sku"] = clean_text(variant.get("sku") or fields["sku"])
                item["generated_sku"] = False
                item["sku_changed"] = False
            if "barcode" in fields:
                item["barcode"] = clean_text(variant.get("barcode") or fields["barcode"])
                item["barcode_changed"] = False
            if "price" in fields:
                item["system_sale_price"] = float(variant.get("price") or fields["price"])
                item["new_sale_price"] = item["system_sale_price"]
            updated += 1
        if progress:
            progress(position, total, action, item)
    return {"created": created, "updated": updated, "total": total}


def download_products(client, progress=None):
    products = []
    for page in range(1, MAX_PAGES + 1):
        payload = client.get_json("/admin/products.json", {"limit": PAGE_SIZE, "page": page})
        batch = payload.get("products", []) if isinstance(payload, dict) else []
        if not isinstance(batch, list):
            raise ValueError("Sapo trả về danh sách sản phẩm không hợp lệ")
        products.extend(batch)
        if progress:
            progress("download", len(products), None)
        if len(batch) < PAGE_SIZE:
            return products
    raise RuntimeError("Danh sách sản phẩm vượt quá giới hạn an toàn")


def iter_variants(products):
    for product in products:
        variants = product.get("variants") or product.get("product_variants") or []
        for variant in variants if isinstance(variants, list) else []:
            try:
                variant_id = int(float(variant.get("id") or variant.get("variant_id")))
            except (TypeError, ValueError):
                continue
            if variant_id:
                yield product, variant, variant_id


def plan_bulk_skus(products):
    entries = list(iter_variants(products))
    # SKU đã tồn tại là dữ liệu người dùng đã đặt và tuyệt đối không được thay đổi.
    identifier_owners = {}
    for _product, variant, variant_id in entries:
        for field in ("sku", "barcode"):
            identifier = clean_text(variant.get(field)).casefold()
            if identifier:
                identifier_owners.setdefault(identifier, set()).add(variant_id)

    def conflicts(identifier, variant_id):
        return bool(identifier_owners.get(identifier.casefold(), set()).difference({variant_id}))

    changes = []
    duplicates = 0
    no_barcode = 0
    existing_sku = 0
    for product, variant, variant_id in entries:
        old_sku = clean_text(variant.get("sku"))
        if old_sku:
            existing_sku += 1
            continue
        barcode = clean_text(variant.get("barcode"))
        if not barcode:
            no_barcode += 1
            continue
        sku = barcode
        suffix = 1
        while conflicts(sku, variant_id):
            sku = f"{barcode}{suffix}"
            suffix += 1
        if sku != barcode:
            duplicates += 1
        identifier_owners.setdefault(sku.casefold(), set()).add(variant_id)
        changes.append({
            "variant_id": variant_id,
            "old_sku": "",
            "new_sku": sku,
            "barcode": barcode,
            "name": clean_text(product.get("name") or product.get("title")),
            "variant": variant,
        })
    return {
        "products": products,
        "variant_count": len(entries),
        "changes": changes,
        "duplicate_suffixes": duplicates,
        "no_barcode": no_barcode,
        "existing_sku": existing_sku,
    }


def load_sku_backup_csv(path):
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = set(reader.fieldnames or [])
        required = {"Id phiên bản", "Mã SKU"}
        missing = required.difference(headers)
        if missing:
            raise ValueError("File sao lưu thiếu cột: " + ", ".join(sorted(missing)))
        backup = {}
        duplicate_ids = 0
        for row in reader:
            try:
                variant_id = int(float(clean_text(row.get("Id phiên bản"))))
            except (TypeError, ValueError):
                continue
            original_sku = clean_text(row.get("Mã SKU"))
            if not original_sku:
                continue
            if variant_id in backup:
                duplicate_ids += 1
                continue
            backup[variant_id] = original_sku
    if not backup:
        raise ValueError("File không có SKU gốc nào để khôi phục")
    return backup, duplicate_ids


def plan_restore_skus(products, backup):
    current = {variant_id: (product, variant) for product, variant, variant_id in iter_variants(products)}
    changes = []
    missing_current = 0
    for variant_id, original_sku in backup.items():
        entry = current.get(variant_id)
        if not entry:
            missing_current += 1
            continue
        product, variant = entry
        current_sku = clean_text(variant.get("sku"))
        if current_sku == original_sku:
            continue
        changes.append({
            "variant_id": variant_id,
            "old_sku": current_sku,
            "new_sku": original_sku,
            "barcode": clean_text(variant.get("barcode")),
            "name": clean_text(product.get("name") or product.get("title")),
            "variant": variant,
        })
    duplicate_values = len(backup) - len({sku.casefold() for sku in backup.values()})
    return {
        "products": products,
        "variant_count": len(current),
        "backup_sku_count": len(backup),
        "changes": changes,
        "already_correct": len(backup) - len(changes) - missing_current,
        "missing_current": missing_current,
        "duplicate_backup_skus": duplicate_values,
    }


def apply_bulk_skus(client, plan, progress=None, cancel_event=None):
    changes = plan["changes"]
    total = len(changes)
    completed = 0
    failures = []
    lock = threading.Lock()

    def update(change):
        if cancel_event and cancel_event.is_set():
            return change, "cancelled"
        client.put_variant_sku(change["variant_id"], change["new_sku"])
        change["variant"]["sku"] = change["new_sku"]
        return change, None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        iterator = iter(changes)
        pending = set()
        for _ in range(MAX_WORKERS * 2):
            try:
                pending.add(executor.submit(update, next(iterator)))
            except StopIteration:
                break
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    _change, error = future.result()
                    if error == "cancelled":
                        continue
                except Exception as exc:
                    failures.append(str(exc))
                    if isinstance(exc, PermissionError) or len(failures) >= 20:
                        if cancel_event:
                            cancel_event.set()
                with lock:
                    completed += 1
                    if progress:
                        progress("update", completed, total)
                if not (cancel_event and cancel_event.is_set()):
                    try:
                        pending.add(executor.submit(update, next(iterator)))
                    except StopIteration:
                        pass
    if failures:
        raise RuntimeError(
            f"Đã cập nhật {completed - len(failures):,}/{total:,} SKU; có {len(failures)} lỗi. "
            f"Lỗi đầu tiên: {failures[0]}"
        )
    return completed


def plan_allow_negative_inventory(products):
    """Plan only variants that are not yet allowed to sell below zero stock."""
    changes = []
    for product, variant, variant_id in iter_variants(products):
        current_policy = clean_text(variant.get("inventory_policy")).casefold()
        if current_policy == "continue":
            continue
        changes.append({
            "variant_id": variant_id,
            "old_policy": current_policy or "deny",
            "name": clean_text(product.get("name") or product.get("title")),
            "variant": variant,
        })
    return {
        "products": products,
        "variant_count": sum(1 for _ in iter_variants(products)),
        "changes": changes,
        "already_enabled": sum(1 for _product, variant, _variant_id in iter_variants(products)
                               if clean_text(variant.get("inventory_policy")).casefold() == "continue"),
    }


def apply_allow_negative_inventory(client, plan, progress=None, cancel_event=None):
    """Set inventory_policy=continue without changing quantity, SKU or prices."""
    changes = plan["changes"]
    total = len(changes)
    completed = 0
    failures = []
    lock = threading.Lock()

    def update(change):
        if cancel_event and cancel_event.is_set():
            return change, "cancelled"
        client.put_variant(change["variant_id"], {"inventory_policy": "continue"})
        change["variant"]["inventory_policy"] = "continue"
        return change, None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        iterator = iter(changes)
        pending = set()
        for _ in range(MAX_WORKERS * 2):
            try:
                pending.add(executor.submit(update, next(iterator)))
            except StopIteration:
                break
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    _change, error = future.result()
                    if error == "cancelled":
                        continue
                except Exception as exc:
                    failures.append(str(exc))
                    if isinstance(exc, PermissionError) or len(failures) >= 20:
                        if cancel_event:
                            cancel_event.set()
                with lock:
                    completed += 1
                    if progress:
                        progress("update", completed, total)
                if not (cancel_event and cancel_event.is_set()):
                    try:
                        pending.add(executor.submit(update, next(iterator)))
                    except StopIteration:
                        pass
    if failures:
        raise RuntimeError(
            f"Đã bật bán âm {completed - len(failures):,}/{total:,} phiên bản; có {len(failures)} lỗi. "
            f"Lỗi đầu tiên: {failures[0]}"
        )
    return completed


def parse_number(value):
    try:
        return float(str(value or "").strip().replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def build_database(products, existing_database=None):
    existing_by_id = {
        str(item.get("variant_id")): item
        for item in (existing_database or [])
        if isinstance(item, dict) and item.get("variant_id") is not None
    }
    database = []
    for product, variant, variant_id in iter_variants(products):
        old = existing_by_id.get(str(variant_id), {})
        name = clean_text(product.get("name") or product.get("title"))
        options = []
        for key in ("option1", "option2", "option3"):
            value = clean_text(variant.get(key))
            if value and value.casefold() != "default title":
                options.append(value)
        full_name = " - ".join([name, *options]).strip(" -")
        price = parse_number(variant.get("price") or variant.get("retail_price"))
        cost = parse_number(
            variant.get("cost") or variant.get("cost_price") or variant.get("purchase_price")
        )
        if price <= 0:
            price = parse_number(old.get("price"))
        if cost <= 0:
            cost = parse_number(old.get("cost"))
        known_prices = {
            parse_number(value) for value in old.get("prices", [])
            if parse_number(value) > 0
        }
        known_prices.update(value for value in (price, cost) if value > 0)
        database.append({
            "name": full_name,
            "sku": clean_text(variant.get("sku")),
            "barcode": clean_text(variant.get("barcode")),
            "variant_id": variant_id,
            "price": price,
            "cost": cost,
            "prices": sorted(known_prices),
        })
    return database


def save_database(products, destination):
    destination = Path(destination)
    existing_database = []
    try:
        loaded = json.loads(destination.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            existing_database = loaded
    except (FileNotFoundError, OSError, ValueError):
        pass
    database = build_database(products, existing_database)
    if not database:
        raise ValueError("Không thể tạo database từ dữ liệu Sapo")
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(database, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return len(database)
