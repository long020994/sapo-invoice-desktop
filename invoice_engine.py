import base64
import bisect
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from PIL import Image, ImageOps, UnidentifiedImageError
from thefuzz import fuzz

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SAPO_DATABASE_PATH", BASE_DIR / "sapo_database.json"))
LEARNING_PATH = Path(os.getenv("SAPO_LEARNING_PATH", BASE_DIR / "learning_rules.json"))
PRICE_HISTORY_PATH = Path(os.getenv("SAPO_PRICE_HISTORY_PATH", BASE_DIR / "price_history.json"))
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", 12 * 1024 * 1024))
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", 45 * 1024 * 1024))
MAX_IMAGE_SIDE = int(os.getenv("MAX_IMAGE_SIDE", 2200))
MAX_FILES_PER_REQUEST = int(os.getenv("MAX_FILES_PER_REQUEST", 10))
MAX_BATCH_BYTES = int(os.getenv("MAX_BATCH_BYTES", 45 * 1024 * 1024))
PRICE_TOLERANCE = float(os.getenv("PRICE_TOLERANCE", 5000))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", 12))
PRICE_ALERT_PERCENT = float(os.getenv("PRICE_ALERT_PERCENT", 8.0))
PRICE_ALERT_AMOUNT = float(os.getenv("PRICE_ALERT_AMOUNT", 1000.0))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("sapo-agent")
api_key = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=api_key, timeout=90.0, max_retries=0) if OpenAI is not None and api_key else None

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_REQUEST_BYTES", 65 * 1024 * 1024))
CORS(app, resources={r"/api/*": {"origins": os.getenv("SAPO_ALLOWED_ORIGINS", "*")}})

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "qty": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "total": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    "tax_rate_percent": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                },
                "required": ["name", "qty", "unit_price", "total", "tax_rate_percent"],
                "additionalProperties": False,
            },
        },
        "default_tax_rate_percent": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "invoice_subtotal_before_tax": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "invoice_tax_total": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "invoice_grand_total": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "supplier_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "invoice_number": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "invoice_date": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": [
        "invoice_items",
        "default_tax_rate_percent",
        "invoice_subtotal_before_tax",
        "invoice_tax_total",
        "invoice_grand_total",
        "supplier_name",
        "invoice_number",
        "invoice_date",
    ],
    "additionalProperties": False,
}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_index": {"type": "integer"},
                    "matched": {"type": "boolean"},
                    "variant_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "reason": {"type": "string"},
                },
                "required": ["line_index", "matched", "variant_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


def normalize_text(value):
    """Chuẩn hóa tên nhưng không xóa từ có nghĩa như 'đèn' hay 'bóng'."""
    text = str(value or "").casefold().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\((?:nha|kho)\s*\d+\)", " ", text)
    text = re.sub(r"(\d+)[,.](\d+)", r"\1.\2", text)

    def capacity(match):
        whole, decimal = match.group(1), match.group(2)
        return f"{whole}l" if set(decimal) == {"0"} else f"{whole}.{decimal}l"

    text = re.sub(r"\b(\d+)\s*l\s*(\d+)\b", capacity, text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:lit|l)\b", r"\1l", text)
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return " ".join(text.split())


def number_tokens(text):
    return set(re.findall(r"\b\d+(?:\.\d+)?[a-z]*\b", text))


def parse_number(value, default=0.0):
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d,.-]", "", str(value))
    if not cleaned:
        return default
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(".") > 1 or ("." in cleaned and len(cleaned.rsplit(".", 1)[1]) == 3):
        cleaned = cleaned.replace(".", "")
    elif cleaned.count(",") > 1 or ("," in cleaned and len(cleaned.rsplit(",", 1)[1]) == 3):
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return default


def expand_vnd_thousand_shorthand(unit_price, total, qty):
    """Đổi cách ghi 42/25 (nghìn đồng) thành 42.000/25.000 đồng.

    Chỉ áp dụng khi đơn giá dương nhỏ hơn 1.000. Thành tiền chỉ được nhân theo
    khi nó cũng đang ở cùng thang nghìn; số 0 của hàng tặng vẫn được giữ nguyên.
    """
    raw_unit = parse_number(unit_price)
    raw_total = parse_number(total) if total is not None else None
    quantity = max(parse_number(qty, 1.0), 0.0)
    if not 0 < raw_unit < 1000:
        return raw_unit, raw_total, False

    expanded_unit = raw_unit * 1000
    expanded_total = raw_total
    if raw_total is not None and raw_total != 0:
        raw_expected = raw_unit * quantity
        same_raw_scale = abs(raw_total - raw_expected) <= max(1.0, abs(raw_expected) * 0.25)
        if same_raw_scale or abs(raw_total) < 10000:
            expanded_total = raw_total * 1000
    return expanded_unit, expanded_total, True


class ProductIndex:
    def __init__(self, products):
        self.products = []
        self.by_variant_id = {}
        self.token_index = defaultdict(set)
        price_rows = []
        for raw in products:
            variant_id = int(parse_number(raw.get("variant_id")))
            name = str(raw.get("name") or "").strip()
            if not variant_id or not name:
                continue
            item = dict(raw)
            item["variant_id"] = variant_id
            item["_normalized"] = normalize_text(name)
            item["_numbers"] = number_tokens(item["_normalized"])
            item["_tokens"] = set(item["_normalized"].split())
            item["prices"] = sorted({parse_number(p) for p in item.get("prices", []) if parse_number(p) > 0})
            idx = len(self.products)
            self.products.append(item)
            self.by_variant_id[variant_id] = item
            for token in item["_tokens"]:
                self.token_index[token].add(idx)
            for price in item["prices"]:
                price_rows.append((price, idx))
        price_rows.sort()
        self.price_values = [row[0] for row in price_rows]
        self.price_rows = price_rows
        logger.info("Đã lập chỉ mục %s sản phẩm và %s mức giá", len(self.products), len(price_rows))

    def ids_near_price(self, price, tolerance):
        if price <= 0:
            return set()
        left = bisect.bisect_left(self.price_values, price - tolerance)
        right = bisect.bisect_right(self.price_values, price + tolerance)
        return {idx for _, idx in self.price_rows[left:right]}

    def shortlist(self, name, price, limit=MAX_CANDIDATES):
        target = normalize_text(name)
        tokens = set(target.split())
        postings = sorted(
            (self.token_index[token] for token in tokens if token in self.token_index),
            key=len,
        )
        name_ids = set()
        for posting in postings[:5]:
            name_ids.update(posting)
        tolerance = max(PRICE_TOLERANCE, min(20000.0, price * 0.04)) if price > 0 else PRICE_TOLERANCE
        price_ids = self.ids_near_price(price, tolerance)
        if name_ids and price_ids:
            pool = (name_ids & price_ids) | set(list(name_ids)[:800]) | set(list(price_ids)[:500])
        else:
            pool = name_ids or price_ids
        if not pool:
            pool = set(range(len(self.products)))
        scored = [self.score(target, price, self.products[idx]) for idx in pool]
        scored.sort(
            key=lambda row: (
                row["score"],
                -(row["price_diff"] if row["price_diff"] is not None else float("inf")),
                -len(row["item"]["_normalized"]),
            ),
            reverse=True,
        )
        return scored[:limit]

    @staticmethod
    def score(target, price, item):
        candidate = item["_normalized"]
        name_score = 0.7 * fuzz.token_set_ratio(target, candidate) + 0.3 * fuzz.ratio(target, candidate)
        target_numbers = number_tokens(target)
        missing_numbers = target_numbers - item["_numbers"]
        conflicting_numbers = item["_numbers"] - target_numbers
        number_adjustment = -24 * len(missing_numbers)
        if target_numbers and not missing_numbers:
            number_adjustment += 8
        if target_numbers and conflicting_numbers:
            number_adjustment -= min(12, 4 * len(conflicting_numbers))
        diffs = [abs(p - price) for p in item["prices"]] if price > 0 else []
        price_diff = min(diffs) if diffs else None
        if price_diff is None:
            price_bonus = 0
        elif price_diff <= 500:
            price_bonus = 16
        elif price_diff <= 2000:
            price_bonus = 13
        elif price_diff <= 5000:
            price_bonus = 9
        elif price_diff <= max(10000, price * 0.05):
            price_bonus = 5
        else:
            price_bonus = 0
        containment_bonus = 4 if target and (target in candidate or candidate in target) else 0
        extra_words = max(0, len(item["_tokens"]) - len(set(target.split())))
        score = 0.7 * name_score + price_bonus + number_adjustment + containment_bonus - min(4, extra_words * 0.5)
        return {
            "item": item,
            "score": round(max(0.0, score), 2),
            "name_score": round(name_score, 2),
            "price_diff": price_diff,
        }


class LearningStore:
    """Ghi nhớ lựa chọn đã được người dùng xác nhận, lưu cục bộ và nguyên tử."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.rules = {}
        self.load()

    def load(self):
        with self.lock:
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                rules = payload.get("rules", {}) if isinstance(payload, dict) else {}
                self.rules = rules if isinstance(rules, dict) else {}
            except FileNotFoundError:
                self.rules = {}
            except (OSError, ValueError):
                logger.exception("Không thể đọc learning_rules.json; tạm dùng bộ nhớ trống")
                self.rules = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"version": 1, "rules": self.rules}, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def learn(self, original_name, price, variant_id):
        key = normalize_text(original_name)
        if not key:
            raise ValueError("Tên sản phẩm hóa đơn không hợp lệ")
        price = parse_number(price)
        variant_id = int(variant_id)
        with self.lock:
            previous = self.rules.get(key, {})
            same_variant = int(parse_number(previous.get("variant_id"))) == variant_id
            confirmations = int(parse_number(previous.get("confirmations"))) + 1 if same_variant else 1
            price_count = int(parse_number(previous.get("price_count"))) if same_variant else 0
            price_sum = parse_number(previous.get("price_sum")) if same_variant else 0.0
            if price > 0:
                price_count += 1
                price_sum += price
            rule = {
                "original_name": str(original_name).strip(),
                "variant_id": variant_id,
                "confirmations": confirmations,
                "price_count": price_count,
                "price_sum": round(price_sum, 2),
                "average_price": round(price_sum / price_count, 2) if price_count else 0.0,
                "last_confirmed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "corrections": int(parse_number(previous.get("corrections"))) + (0 if same_variant or not previous else 1),
            }
            self.rules[key] = rule
            self.save()
            return dict(rule)

    def match(self, original_name, price):
        key = normalize_text(original_name)
        with self.lock:
            rule = dict(self.rules.get(key, {}))
        if not rule:
            return None
        average_price = parse_number(rule.get("average_price"))
        current_price = parse_number(price)
        if average_price > 0 and current_price > 0:
            tolerance = max(5000.0, average_price * 0.35)
            if abs(current_price - average_price) > tolerance:
                return None
        return rule

    def count(self):
        with self.lock:
            return len(self.rules)


class PriceHistoryStore:
    """Lưu giá nhập đã được xác nhận và so sánh theo sản phẩm/nhà cung cấp."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.records = {}
        self.batch_ids = []
        self.load()

    def load(self):
        with self.lock:
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                records = payload.get("records", {}) if isinstance(payload, dict) else {}
                batches = payload.get("batch_ids", []) if isinstance(payload, dict) else []
                self.records = records if isinstance(records, dict) else {}
                self.batch_ids = batches if isinstance(batches, list) else []
            except FileNotFoundError:
                self.records, self.batch_ids = {}, []
            except (OSError, ValueError):
                logger.exception("Không thể đọc price_history.json; tạm dùng lịch sử trống")
                self.records, self.batch_ids = {}, []

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "records": self.records, "batch_ids": self.batch_ids[-200:]},
                handle,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(temporary, self.path)

    def compare(self, variant_id, current_price, supplier_name="", fallback_price=0):
        current_price = parse_number(current_price)
        supplier_key = normalize_text(supplier_name)
        with self.lock:
            all_records = [dict(record) for record in self.records.get(str(int(variant_id)), [])]
        supplier_records = [
            record for record in all_records
            if supplier_key and normalize_text(record.get("supplier_name")) == supplier_key
        ]
        relevant = supplier_records or all_records
        baseline = parse_number(relevant[-1].get("price")) if relevant else parse_number(fallback_price)
        basis = (
            "same_supplier_last" if supplier_records
            else "last_purchase" if all_records
            else "sapo_cost" if baseline > 0
            else "none"
        )
        change_amount = current_price - baseline if baseline > 0 and current_price > 0 else 0.0
        change_percent = change_amount / baseline * 100 if baseline > 0 and current_price > 0 else 0.0
        average_price = (
            sum(parse_number(record.get("price")) for record in relevant) / len(relevant)
            if relevant else baseline
        )
        is_alert = (
            baseline > 0
            and current_price > 0
            and abs(change_amount) >= PRICE_ALERT_AMOUNT
            and abs(change_percent) >= PRICE_ALERT_PERCENT
        )
        return {
            "history_count": len(all_records),
            "same_supplier_count": len(supplier_records),
            "baseline_price": round(baseline, 2),
            "average_price": round(average_price, 2),
            "current_price": round(current_price, 2),
            "change_amount": round(change_amount, 2),
            "change_percent": round(change_percent, 2),
            "direction": "increase" if change_amount > 0 else "decrease" if change_amount < 0 else "same",
            "basis": basis,
            "is_alert": is_alert,
        }

    def record_batch(self, batch_id, items):
        batch_id = str(batch_id or "").strip()
        if not batch_id:
            raise ValueError("Thiếu mã xác nhận hóa đơn")
        with self.lock:
            if batch_id in self.batch_ids:
                return 0
            recorded = 0
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for item in items:
                variant_id = int(parse_number(item.get("variant_id")))
                price = parse_number(item.get("price"))
                if not variant_id or price <= 0:
                    continue
                record = {
                    "price": round(price, 2),
                    "qty": parse_number(item.get("qty")),
                    "supplier_name": str(item.get("supplier_name") or "").strip(),
                    "invoice_number": str(item.get("invoice_number") or "").strip(),
                    "invoice_date": str(item.get("invoice_date") or "").strip(),
                    "invoice_filename": str(item.get("invoice_filename") or "").strip(),
                    "recorded_at": timestamp,
                }
                key = str(variant_id)
                self.records.setdefault(key, []).append(record)
                self.records[key] = self.records[key][-100:]
                recorded += 1
            self.batch_ids.append(batch_id)
            self.batch_ids = self.batch_ids[-200:]
            self.save()
            return recorded

    def total_records(self):
        with self.lock:
            return sum(len(records) for records in self.records.values())


def load_database():
    try:
        with DB_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Không thể đọc cơ sở dữ liệu {DB_PATH}: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError("sapo_database.json phải chứa một mảng sản phẩm")
    return ProductIndex(data)


product_index = load_database()
learning_store = LearningStore(LEARNING_PATH)
price_history_store = PriceHistoryStore(PRICE_HISTORY_PATH)


def configure_runtime(api_key_value, database_path, learning_path=None, price_history_path=None, model=None):
    """Cấu hình động cơ cho ứng dụng desktop, không cần chạy Flask server."""
    global client, product_index, learning_store, price_history_store, MODEL_NAME
    api_key_value = str(api_key_value or "").strip()
    if not api_key_value:
        raise ValueError("Chưa nhập OpenAI API key")
    database_path = Path(database_path)
    with database_path.open("r", encoding="utf-8") as handle:
        products = json.load(handle)
    product_index = ProductIndex(products)
    learning_store = LearningStore(learning_path or database_path.with_name("learning_rules.json"))
    price_history_store = PriceHistoryStore(
        price_history_path or database_path.with_name("price_history.json")
    )
    if model:
        MODEL_NAME = str(model).strip()
    if OpenAI is None:
        raise RuntimeError("Thiếu thư viện OpenAI")
    client = OpenAI(api_key=api_key_value, timeout=90.0, max_retries=0)


def file_to_document(path):
    """Đọc ảnh/PDF từ ổ đĩa thành tài liệu nội bộ."""
    path = Path(path)
    raw = path.read_bytes()
    mime = "application/pdf" if path.suffix.casefold() == ".pdf" else "image/jpeg"
    data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    return decode_document(data_url, path.name)


def image_to_data_url(image):
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def call_openai(prompt, schema, schema_name, document=None, documents=None):
    if client is None:
        raise RuntimeError("Chưa cấu hình biến môi trường OPENAI_API_KEY")
    last_error = None
    for attempt in range(2):
        try:
            content = [{"type": "input_text", "text": prompt}]
            supplied_documents = documents if documents is not None else ([document] if document else [])
            for supplied_document in supplied_documents:
                if supplied_document["kind"] == "image":
                    content.append({
                        "type": "input_image",
                        "image_url": image_to_data_url(supplied_document["image"]),
                        "detail": "high",
                    })
                elif supplied_document["kind"] == "pdf":
                    content.append({
                        "type": "input_file",
                        "filename": supplied_document["filename"],
                        "file_data": supplied_document["data_url"],
                    })
            response = client.responses.create(
                model=MODEL_NAME,
                input=[{"role": "user", "content": content}],
                reasoning={"effort": REASONING_EFFORT},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                max_output_tokens=5000,
                store=False,
            )
            if not response.output_text:
                raise RuntimeError("OpenAI không trả về nội dung")
            return json.loads(response.output_text)
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if "insufficient_quota" in message or "billing_hard_limit_reached" in message:
                raise RuntimeError("OpenAI API đã hết tín dụng hoặc chạm giới hạn chi tiêu") from exc
            if "invalid_api_key" in message or "Incorrect API key" in message:
                raise RuntimeError("OPENAI_API_KEY không hợp lệ") from exc
            if attempt == 0 and any(code in message for code in ("429", "500", "502", "503", "504")):
                time.sleep(1.2)
                continue
            break
    raise RuntimeError(f"OpenAI không trả về kết quả hợp lệ: {last_error}") from last_error


def decode_document(file_value, filename=""):
    if not file_value:
        raise ValueError("Chưa nhận được ảnh hoặc PDF hóa đơn")
    header, encoded = file_value.split(",", 1) if "," in file_value else ("", file_value)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Dữ liệu ảnh/PDF không hợp lệ") from exc

    declared_pdf = header.casefold().startswith("data:application/pdf")
    actual_pdf = raw.lstrip().startswith(b"%PDF-")
    if declared_pdf or actual_pdf:
        if not actual_pdf:
            raise ValueError("Tệp được chọn không phải PDF hợp lệ")
        if len(raw) > MAX_PDF_BYTES:
            raise ValueError(f"PDF vượt quá giới hạn {MAX_PDF_BYTES // (1024 * 1024)} MB")
        safe_name = Path(str(filename or "hoa-don.pdf")).name
        safe_name = re.sub(r"[^\w. -]", "_", safe_name, flags=re.UNICODE).strip() or "hoa-don.pdf"
        if not safe_name.casefold().endswith(".pdf"):
            safe_name += ".pdf"
        canonical_data_url = "data:application/pdf;base64," + base64.b64encode(raw).decode("ascii")
        return {
            "kind": "pdf",
            "filename": safe_name,
            "data_url": canonical_data_url,
            "byte_size": len(raw),
        }

    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"Ảnh vượt quá giới hạn {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(raw)))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Không đọc được định dạng ảnh") from exc
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    safe_name = Path(str(filename or "hoa-don.jpg")).name
    safe_name = re.sub(r"[^\w. -]", "_", safe_name, flags=re.UNICODE).strip() or "hoa-don.jpg"
    return {"kind": "image", "filename": safe_name, "image": image, "byte_size": len(raw)}


def decode_documents(payload):
    """Nhận định dạng nhiều file mới và vẫn tương thích với một file cũ."""
    raw_files = payload.get("files")
    if raw_files is None:
        file_value = payload.get("file") or payload.get("image", "")
        raw_files = [{"data": file_value, "filename": payload.get("filename", "")}]
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("Vui lòng chọn ít nhất một ảnh hoặc PDF hóa đơn")
    if len(raw_files) > MAX_FILES_PER_REQUEST:
        raise ValueError(f"Chỉ được gửi tối đa {MAX_FILES_PER_REQUEST} file mỗi lần")

    documents = []
    for index, entry in enumerate(raw_files, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"File thứ {index} không hợp lệ")
        file_value = entry.get("data") or entry.get("file") or entry.get("image", "")
        filename = entry.get("filename") or f"hoa-don-{index}"
        documents.append(decode_document(file_value, filename))
    total_bytes = sum(document.get("byte_size", 0) for document in documents)
    if total_bytes > MAX_BATCH_BYTES:
        raise ValueError(
            f"Tổng dung lượng các file vượt quá {MAX_BATCH_BYTES // (1024 * 1024)} MB"
        )
    return documents


def promotion_group_key(name):
    """Gom dòng bán và dòng tặng của cùng một sản phẩm, không gom khác model."""
    normalized = normalize_text(name)
    normalized = re.sub(
        r"\b(?:hang tang|qua tang|tang kem|khuyen mai|mien phi|free)\b",
        " ",
        normalized,
    )
    return " ".join(normalized.split())


def consolidate_invoice_items(items):
    """Tính giá nhập đã có thuế và bình quân cả số lượng khuyến mãi 0đ."""
    groups = {}
    for item in items:
        key = promotion_group_key(item.get("name"))
        if not key:
            continue
        qty = parse_number(item.get("qty"))
        if qty <= 0:
            continue
        listed_price_before_tax = parse_number(item.get("listed_price"))
        tax_rate = parse_number(item.get("tax_rate_percent"))
        tax_rate = tax_rate if 0 <= tax_rate <= 100 else 0.0
        tax_multiplier = 1 + tax_rate / 100
        listed_price = listed_price_before_tax * tax_multiplier
        line_total = item.get("total")
        explicit_total = parse_number(line_total) if line_total is not None else None
        actual_total_before_tax = (
            explicit_total if explicit_total is not None else listed_price_before_tax * qty
        )
        actual_total = actual_total_before_tax * tax_multiplier

        group = groups.setdefault(key, {
            "name": item["name"],
            "qty": 0.0,
            "paid_total": 0.0,
            "paid_total_before_tax": 0.0,
            "tax_amount": 0.0,
            "listed_total": 0.0,
            "listed_total_before_tax": 0.0,
            "free_qty": 0.0,
            "source_lines": 0,
        })
        group["qty"] += qty
        group["paid_total"] += actual_total
        group["paid_total_before_tax"] += actual_total_before_tax
        group["tax_amount"] += actual_total - actual_total_before_tax
        group["listed_total"] += listed_price * qty
        group["listed_total_before_tax"] += listed_price_before_tax * qty
        group["source_lines"] += 1
        if explicit_total == 0 and listed_price_before_tax > 0:
            group["free_qty"] += qty

    consolidated = []
    for group in groups.values():
        qty = group["qty"]
        # Nếu chỉ có một dòng 0đ mà không có dòng trả tiền đi kèm, dùng đơn giá
        # in trên hóa đơn để tránh biến một sản phẩm đơn lẻ thành giá 0 do OCR sai.
        total_for_average = group["paid_total"]
        total_before_tax = group["paid_total_before_tax"]
        tax_amount = group["tax_amount"]
        if total_for_average <= 0 and group["listed_total"] > 0:
            total_for_average = group["listed_total"]
            total_before_tax = group["listed_total_before_tax"]
            tax_amount = total_for_average - total_before_tax
            group["free_qty"] = 0.0
        effective_price = total_for_average / qty
        listed_price = group["listed_total"] / qty if group["listed_total"] > 0 else 0.0
        free_qty = int(group["free_qty"]) if group["free_qty"].is_integer() else group["free_qty"]
        final_qty = int(qty) if qty.is_integer() else qty
        pricing_notes = []
        if free_qty > 0 and group["source_lines"] > 1:
            total_text = f"{round(total_for_average):,.0f}".replace(",", ".")
            pricing_notes.append(
                f"Đã gộp {group['source_lines']} dòng cùng sản phẩm; gồm {free_qty} hàng tặng; "
                f"giá bình quân {total_text} / {final_qty}"
            )
        effective_tax_rate = (tax_amount / total_before_tax * 100) if total_before_tax > 0 else 0.0
        if tax_amount > 0.005:
            tax_text = f"{effective_tax_rate:.4f}".rstrip("0").rstrip(".")
            pricing_notes.append(f"Đã cộng VAT {tax_text}% vào giá nhập Sapo")
        consolidated.append({
            "name": group["name"],
            "qty": final_qty,
            "price": round(effective_price, 2),
            "listed_price": round(listed_price, 2),
            "listed_price_before_tax": round(
                group["listed_total_before_tax"] / qty if qty > 0 else 0.0, 2
            ),
            "total": round(total_for_average, 2),
            "total_before_tax": round(total_before_tax, 2),
            "tax_amount": round(tax_amount, 2),
            "tax_rate_percent": round(effective_tax_rate, 4),
            "free_qty": free_qty,
            "source_lines": group["source_lines"],
            "pricing_note": ". ".join(pricing_notes),
        })
    return consolidated


def extract_invoice(documents):
    if not isinstance(documents, list):
        documents = [documents]
    prompt = """
Các file được cung cấp theo đúng thứ tự và đều là các trang/phần của CÙNG MỘT HÓA ĐƠN.
Đọc tất cả các trang và từng dòng hàng trên hóa đơn tiếng Việt. Giữ tên sản phẩm sát nguyên văn, gồm hãng,
mã mẫu, kích thước và dung tích. Bỏ dòng tiêu đề, thuế, tổng cộng và ghi chú.
Mỗi STT và STT phụ (ví dụ 2 và 2.1) là một dòng riêng; không tự gộp các dòng.
Phải giữ cả dòng hàng tặng/chiết khấu 100% có thành tiền bằng 0, không được bỏ qua.
Tên chỉ chứa tên sản phẩm; không thêm chữ "hàng tặng" hoặc "khuyến mãi" vào tên.
qty là số lượng của đúng dòng. unit_price là đơn giá niêm yết trước chiết khấu.
total là thành tiền thực trả của riêng dòng sau chiết khấu: ghi chính xác 0 nếu hóa đơn
ghi 0; chỉ để null khi thật sự không có hoặc không đọc được cột thành tiền.
Các số unit_price và total chưa tự cộng thuế GTGT. Lưu ý nhiều phiếu ghi tiền theo
ĐƠN VỊ NGHÌN ĐỒNG: ví dụ đơn giá 42 và thành tiền 294 phải trả về lần lượt là
42000 và 294000; đơn giá 25 phải trả về 25000. Không áp dụng quy đổi này cho số lượng.
default_tax_rate_percent là thuế suất GTGT chung ghi ở cuối hóa đơn, ví dụ xuất 8 cho 8%;
để null nếu hóa đơn không ghi thuế suất chung. tax_rate_percent của từng dòng chỉ điền
khi hóa đơn ghi thuế suất riêng cho dòng đó; nếu dùng thuế suất chung thì để null.
invoice_subtotal_before_tax là dòng tổng tiền hàng trước thuế in trên hóa đơn.
invoice_tax_total là tổng tiền thuế GTGT in trên hóa đơn. invoice_grand_total là dòng
tổng tiền phải thanh toán sau thuế in trên hóa đơn. Giữ đúng số in trên giấy; để null
nếu hóa đơn không có hoặc không đọc rõ giá trị tương ứng, không tự tính ba trường này.
supplier_name là tên đơn vị bán/nhà cung cấp ghi trên hóa đơn. invoice_number là số hoặc
ký hiệu hóa đơn. invoice_date là ngày hóa đơn theo dạng YYYY-MM-DD nếu đọc được. Ba trường
này để null khi không có hoặc không đọc rõ, tuyệt đối không tự đoán.
Nếu PDF hoặc nhiều ảnh có nhiều trang, bỏ dòng tiêu đề bảng, tổng cộng và phần đầu/cuối trang bị lặp.
Tuy nhiên nếu cùng một mặt hàng xuất hiện ở các trang/ảnh khác nhau như các dòng bán thực tế,
phải giữ đầy đủ tất cả các dòng để ứng dụng cộng dồn số lượng sau đó. Không tự đoán chữ hoặc số bị che;
chỉ xuất các dòng hàng đủ nhận diện.
""".strip()
    response_data = call_openai(prompt, INVOICE_SCHEMA, "invoice_items", documents=documents)
    raw_items = response_data.get("invoice_items", []) if isinstance(response_data, dict) else []
    default_tax_rate = (
        parse_number(response_data.get("default_tax_rate_percent"))
        if isinstance(response_data, dict) and response_data.get("default_tax_rate_percent") is not None
        else 0.0
    )
    if not 0 <= default_tax_rate <= 100:
        default_tax_rate = 0.0
    items = []
    used_thousand_shorthand = False
    for raw in raw_items if isinstance(raw_items, list) else []:
        name = str(raw.get("name") or "").strip()
        qty = parse_number(raw.get("qty"), 1.0)
        unit_price = parse_number(raw.get("unit_price"))
        total = parse_number(raw.get("total")) if raw.get("total") is not None else None
        unit_price, total, expanded = expand_vnd_thousand_shorthand(unit_price, total, qty)
        used_thousand_shorthand = used_thousand_shorthand or expanded
        line_tax_rate = (
            parse_number(raw.get("tax_rate_percent"))
            if raw.get("tax_rate_percent") is not None
            else default_tax_rate
        )
        if not 0 <= line_tax_rate <= 100:
            line_tax_rate = default_tax_rate
        if not name or qty <= 0:
            continue
        items.append({
            "name": name,
            "qty": int(qty) if qty.is_integer() else qty,
            "listed_price": round(unit_price, 2),
            "total": round(total, 2) if total is not None else None,
            "tax_rate_percent": line_tax_rate,
        })
    consolidated = consolidate_invoice_items(items)

    calculated_subtotal = round(sum(parse_number(item.get("total_before_tax")) for item in consolidated), 2)
    calculated_tax = round(sum(parse_number(item.get("tax_amount")) for item in consolidated), 2)
    calculated_grand_total = round(sum(parse_number(item.get("total")) for item in consolidated), 2)

    def declared_number(field, calculated_value):
        value = response_data.get(field) if isinstance(response_data, dict) else None
        if value is None:
            return None
        parsed = parse_number(value)
        if used_thousand_shorthand and parsed != 0:
            if calculated_value > 0:
                parsed = min((parsed, parsed * 1000), key=lambda candidate: abs(candidate - calculated_value))
            elif abs(parsed) < 10000:
                parsed *= 1000
        return round(parsed, 2)

    declared_subtotal = declared_number("invoice_subtotal_before_tax", calculated_subtotal)
    declared_tax = declared_number("invoice_tax_total", calculated_tax)
    declared_grand_total = declared_number("invoice_grand_total", calculated_grand_total)
    comparison_total = declared_grand_total
    if comparison_total is None and declared_subtotal is not None and declared_tax is not None:
        comparison_total = round(declared_subtotal + declared_tax, 2)
    discrepancy = (
        round(calculated_grand_total - comparison_total, 2)
        if comparison_total is not None
        else None
    )
    tolerance = max(1000.0, calculated_grand_total * 0.001)
    summary = {
        "line_group_count": len(consolidated),
        "total_quantity": float(sum(parse_number(item.get("qty")) for item in consolidated)),
        "free_quantity": float(sum(parse_number(item.get("free_qty")) for item in consolidated)),
        "calculated_subtotal_before_tax": calculated_subtotal,
        "calculated_tax_total": calculated_tax,
        "calculated_grand_total": calculated_grand_total,
        "declared_subtotal_before_tax": declared_subtotal,
        "declared_tax_total": declared_tax,
        "declared_grand_total": declared_grand_total,
        "comparison_total": comparison_total,
        "discrepancy": discrepancy,
        "balanced": abs(discrepancy) <= tolerance if discrepancy is not None else None,
        "supplier_name": str(response_data.get("supplier_name") or "").strip(),
        "invoice_number": str(response_data.get("invoice_number") or "").strip(),
        "invoice_date": str(response_data.get("invoice_date") or "").strip(),
    }
    if summary["total_quantity"].is_integer():
        summary["total_quantity"] = int(summary["total_quantity"])
    if summary["free_quantity"].is_integer():
        summary["free_quantity"] = int(summary["free_quantity"])
    for item in consolidated:
        item["invoice_summary"] = summary
    return consolidated


def extract_invoices(documents, mode):
    if mode == "single_invoice":
        source_name = " + ".join(document.get("filename", "") for document in documents)
        return [
            {**item, "invoice_index": 0, "invoice_filename": source_name}
            for item in extract_invoice(documents)
        ]
    if mode != "separate_invoices":
        raise ValueError("Chế độ xử lý hóa đơn không hợp lệ")

    # Mỗi file được đọc độc lập để thuế và khuyến mãi của hóa đơn này
    # không bị áp nhầm sang hóa đơn khác. Tối đa 3 lượt song song để xử lý nhanh.
    worker_count = min(3, len(documents))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        invoice_groups = list(executor.map(lambda item: extract_invoice([item]), documents))
    return [
        {
            **invoice_item,
            "invoice_index": invoice_index,
            "invoice_filename": documents[invoice_index].get("filename", f"Hóa đơn {invoice_index + 1}"),
        }
        for invoice_index, group in enumerate(invoice_groups)
        for invoice_item in group
    ]


def confidence_for(candidates):
    if not candidates:
        return 0.0, False
    best = candidates[0]["score"]
    second = candidates[1]["score"] if len(candidates) > 1 else 0.0
    margin = best - second
    confident = (best >= 84 and margin >= 3) or (best >= 76 and margin >= 8)
    confidence = min(0.99, max(0.0, (best / 100) * 0.78 + min(margin, 20) / 100))
    return round(confidence, 3), confident


def candidates_with_learning(invoice):
    candidates = product_index.shortlist(invoice["name"], invoice["price"])
    rule = learning_store.match(invoice["name"], invoice["price"])
    if not rule:
        return candidates, None
    learned_item = product_index.by_variant_id.get(int(parse_number(rule.get("variant_id"))))
    if not learned_item:
        return candidates, None
    learned_candidate = {
        "item": learned_item,
        "score": 100.0,
        "name_score": 100.0,
        "price_diff": min(
            (abs(price - invoice["price"]) for price in learned_item.get("prices", [])),
            default=None,
        ),
    }
    candidates = [
        learned_candidate,
        *(candidate for candidate in candidates if candidate["item"]["variant_id"] != learned_item["variant_id"]),
    ][:MAX_CANDIDATES]
    return candidates, rule


def resolve_ambiguous(rows):
    if not rows:
        return {}
    cases, allowed = [], {}
    for row in rows:
        line_index = row["line_index"]
        allowed[line_index] = {candidate["item"]["variant_id"] for candidate in row["candidates"]}
        cases.append({
            "line_index": line_index,
            "invoice": row["invoice"],
            "candidates": [
                {
                    "variant_id": c["item"]["variant_id"],
                    "name": c["item"]["name"],
                    "code": c["item"].get("sku") or c["item"].get("barcode") or "",
                    "prices": c["item"].get("prices", []),
                    "local_score": c["score"],
                    "price_diff": c["price_diff"],
                }
                for c in row["candidates"][:8]
            ],
        })
    prompt = (
        "Bạn đối chiếu sản phẩm hóa đơn với danh mục Sapo. Ưu tiên: mã model/dung tích/kích thước "
        "phải khớp; sau đó tên và hãng; cuối cùng giá thực mỗi đơn vị (total/qty). Chỉ chọn "
        "variant_id trong candidates của đúng line_index. Nếu thiếu bằng chứng hoặc hai ứng viên ngang "
        "nhau, matched=false. Dữ liệu:\n" + json.dumps(cases, ensure_ascii=False)
    )
    response_data = call_openai(prompt, DECISION_SCHEMA, "product_decisions")
    decisions = response_data.get("decisions", []) if isinstance(response_data, dict) else []
    result = {}
    for decision in decisions if isinstance(decisions, list) else []:
        line_index = int(decision.get("line_index", -1))
        variant_id = decision.get("variant_id")
        variant_id = int(variant_id) if variant_id is not None else None
        if line_index in allowed and decision.get("matched") and variant_id in allowed[line_index]:
            result[line_index] = {"variant_id": variant_id, "reason": decision.get("reason", "AI phân xử")}
    return result


def public_suggestion(candidate):
    item = candidate["item"]
    return {
        "variant_id": item["variant_id"],
        "name": item["name"],
        "sku": item.get("sku", ""),
        "barcode": item.get("barcode", ""),
        "search_query": item.get("sku") or item.get("barcode") or item["name"],
        "prices": item.get("prices", []),
        "cost": item.get("cost", 0),
        "price": item.get("price", 0),
        "score": candidate["score"],
        "price_diff": candidate["price_diff"],
    }


def search_catalog(query, limit=100):
    """Tìm trong toàn bộ danh mục để người dùng luôn có thể tự chọn sản phẩm đúng."""
    query_text = str(query or "").strip()
    normalized_query = normalize_text(query_text)
    if not normalized_query:
        return []
    query_tokens = set(normalized_query.split())
    compact_query = re.sub(r"\s+", "", normalized_query)
    ranked = []
    for item in product_index.products:
        name = item.get("_normalized", "")
        sku = normalize_text(item.get("sku", ""))
        barcode = normalize_text(item.get("barcode", ""))
        searchable = f"{name} {sku} {barcode}".strip()
        compact_searchable = re.sub(r"\s+", "", searchable)
        if compact_query and compact_query in compact_searchable:
            score = 160 if compact_query in (sku, barcode) else 125
        else:
            overlap = len(query_tokens & set(searchable.split()))
            if overlap == 0 and len(normalized_query) < 3:
                continue
            score = fuzz.WRatio(normalized_query, searchable) + overlap * 8
        if score < 42:
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]["name"]))
    return [
        {
            "variant_id": item["variant_id"],
            "name": item["name"],
            "sku": item.get("sku", ""),
            "barcode": item.get("barcode", ""),
            "cost": item.get("cost", 0),
            "price": item.get("price", 0),
        }
        for _score, item in ranked[:max(1, min(int(limit), 300))]
    ]


def promote_contextual_duplicates(results):
    """Cứu dòng OCR lỗi khi hai dòng cùng mẫu/giá đã khớp chắc vào một variant."""
    matched_by_variant = defaultdict(list)
    for result in results:
        if result.get("matched") and result.get("variant_id") is not None:
            group_key = (result.get("invoice_index", 0), result["variant_id"])
            matched_by_variant[group_key].append(result)

    promoted = []
    for raw_result in results:
        result = dict(raw_result)
        if result.get("matched"):
            promoted.append(result)
            continue
        target_tokens = normalize_text(result.get("original_name")).split()
        target_price = parse_number(result.get("price"))
        if len(target_tokens) < 2 or target_price <= 0:
            promoted.append(result)
            continue

        eligible = []
        target_invoice_index = result.get("invoice_index", 0)
        for (invoice_index, variant_id), peers in matched_by_variant.items():
            if invoice_index != target_invoice_index:
                continue
            contextual_peers = []
            for peer in peers:
                peer_tokens = normalize_text(peer.get("original_name")).split()
                peer_price = parse_number(peer.get("price"))
                price_tolerance = max(1.0, target_price * 0.01)
                if (
                    len(peer_tokens) >= 2
                    and tuple(peer_tokens[:2]) == tuple(target_tokens[:2])
                    and abs(peer_price - target_price) <= price_tolerance
                ):
                    contextual_peers.append(peer)
            if len(contextual_peers) >= 2:
                eligible.append((variant_id, contextual_peers))

        if len(eligible) != 1:
            promoted.append(result)
            continue
        variant_id, peers = eligible[0]
        representative = max(peers, key=lambda peer: parse_number(peer.get("confidence")))
        result.update({
            "matched": True,
            "variant_id": variant_id,
            "sapo_name": representative.get("sapo_name"),
            "search_query": representative.get("search_query"),
            "sku": representative.get("sku", ""),
            "barcode": representative.get("barcode", ""),
            "system_cost": representative.get("system_cost", 0),
            "system_sale_price": representative.get("system_sale_price", 0),
            "new_sale_price": representative.get("new_sale_price", 0),
            "confidence": max(0.83, min(0.90, parse_number(representative.get("confidence")))),
            "match_reason": (
                f"Tự khớp theo ngữ cảnh: {len(peers)} nhóm khác có cùng tiền tố tên, "
                "cùng đơn giá và cùng khớp sản phẩm Sapo này"
            ),
        })
        promoted.append(result)
    return promoted


def generate_missing_skus(results):
    """Tạo SKU thiếu và sửa SKU đụng SKU/barcode của một variant khác."""
    identifier_owners = {}
    for product in product_index.products:
        owner = str(product.get("variant_id") or "")
        for field in ("sku", "barcode"):
            identifier = str(product.get(field) or "").strip().casefold()
            if identifier:
                identifier_owners.setdefault(identifier, set()).add(owner)

    def conflicts(identifier, variant_id):
        owners = identifier_owners.get(str(identifier or "").strip().casefold(), set())
        return bool(owners.difference({str(variant_id)}))

    def reserve(identifier, variant_id):
        identifier_owners.setdefault(identifier.casefold(), set()).add(str(variant_id))

    generated_by_variant = {}
    for raw_result in results:
        result = dict(raw_result)
        current = str(result.get("sku") or "").strip()
        variant_id = result.get("variant_id")
        if not result.get("matched") or variant_id is None:
            yield result
            continue
        current_conflict = bool(current and conflicts(current, variant_id))
        if current and not current_conflict:
            reserve(current, variant_id)
            yield result
            continue
        if variant_id in generated_by_variant:
            result["sku"] = generated_by_variant[variant_id]
            result["generated_sku"] = True
            result["sku_changed"] = current_conflict or bool(result.get("sku_changed"))
            yield result
            continue
        own_barcode = str(result.get("barcode") or "").strip()
        if own_barcode:
            base = own_barcode[:48]
        else:
            # Giữ dấu & giống cách người dùng đang đặt: head&shoulderday.
            normalized = str(result.get("sapo_name") or result.get("original_name") or "").casefold().replace("đ", "d")
            normalized = unicodedata.normalize("NFD", normalized)
            normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
            base = re.sub(r"[^a-z0-9&]+", "", normalized).strip("&")[:48] or f"sp{variant_id}"
        candidate = base
        counter = 1
        while conflicts(candidate, variant_id):
            candidate = f"{base[:44]}{counter}"
            counter += 1
        reserve(candidate, variant_id)
        generated_by_variant[variant_id] = candidate
        result["sku"] = candidate
        result["generated_sku"] = True
        if current_conflict:
            result["sku_changed"] = True
            result["sku_conflict_repaired"] = True
            note = f"SKU {current} bị trùng với mã của sản phẩm khác; đề xuất đổi thành {candidate}"
            previous_reason = str(result.get("match_reason") or "").strip()
            result["match_reason"] = f"{previous_reason}. {note}" if previous_reason else note
        yield result


def merge_results_by_variant(results):
    """Gộp các nhóm hóa đơn cùng khớp một phiên bản Sapo trước khi nhập hàng."""
    merged = []
    positions = {}
    for result in results:
        variant_id = result.get("variant_id") if result.get("matched") else None
        if variant_id is None:
            merged.append(dict(result))
            continue

        merge_key = (result.get("invoice_index", 0), variant_id)

        qty = parse_number(result.get("qty"))
        line_total = (
            parse_number(result.get("total"))
            if result.get("total") is not None
            else parse_number(result.get("price")) * qty
        )
        listed_total = parse_number(result.get("listed_price")) * qty
        if merge_key not in positions:
            clone = dict(result)
            clone["_merge_count"] = 1
            clone["_merge_total"] = line_total
            clone["_merge_listed_total"] = listed_total
            clone["_merge_names"] = [str(result.get("original_name") or "").strip()]
            positions[merge_key] = len(merged)
            merged.append(clone)
            continue

        target = merged[positions[merge_key]]
        target["_merge_count"] += 1
        target["_merge_total"] += line_total
        target["_merge_listed_total"] += listed_total
        source_name = str(result.get("original_name") or "").strip()
        if source_name and source_name not in target["_merge_names"]:
            target["_merge_names"].append(source_name)
        target["qty"] = parse_number(target.get("qty")) + qty
        target["free_qty"] = parse_number(target.get("free_qty")) + parse_number(result.get("free_qty"))
        target["confidence"] = min(
            parse_number(target.get("confidence")), parse_number(result.get("confidence"))
        )

    for result in merged:
        merge_count = result.pop("_merge_count", 1)
        total = result.pop("_merge_total", None)
        listed_total = result.pop("_merge_listed_total", None)
        source_names = [name for name in result.pop("_merge_names", []) if name]
        qty = parse_number(result.get("qty"))
        if merge_count <= 1 or qty <= 0:
            continue
        result["qty"] = int(qty) if qty.is_integer() else qty
        result["total"] = round(total, 2)
        result["price"] = round(total / qty, 2)
        result["listed_price"] = round(listed_total / qty, 2)
        result["free_qty"] = (
            int(result["free_qty"])
            if float(result["free_qty"]).is_integer()
            else result["free_qty"]
        )
        result["source_group_count"] = merge_count
        result["source_names"] = source_names
        result["original_name"] = (
            f"{source_names[0]} (+{merge_count - 1} nhóm cùng sản phẩm)"
            if source_names
            else result.get("original_name", "")
        )
        merge_note = (
            f"Đã gộp {merge_count} nhóm hóa đơn cùng khớp một sản phẩm Sapo; "
            f"tổng số lượng {result['qty']}"
        )
        previous_reason = str(result.get("match_reason") or "").strip()
        result["match_reason"] = f"{merge_note}. {previous_reason}" if previous_reason else merge_note
    return merged


def attach_price_insights(results):
    enriched = []
    for raw_result in results:
        result = dict(raw_result)
        if result.get("matched") and result.get("variant_id") is not None:
            product = product_index.by_variant_id.get(int(result["variant_id"]), {})
            summary = result.get("invoice_summary") or {}
            result["price_insight"] = price_history_store.compare(
                result["variant_id"],
                result.get("price"),
                summary.get("supplier_name", ""),
                product.get("cost", 0),
            )
        else:
            result["price_insight"] = None
        enriched.append(result)
    return enriched


def analyze_documents(documents, mode="single_invoice"):
    """Phân tích trực tiếp cho ứng dụng desktop; không mở cổng mạng nội bộ."""
    invoice_items = extract_invoices(documents, mode)
    if not invoice_items:
        raise ValueError("Không tìm thấy dòng sản phẩm nào trên hóa đơn")

    rows, ambiguous = [], []
    for line_index, invoice in enumerate(invoice_items):
        candidates, learned_rule = candidates_with_learning(invoice)
        confidence, confident = (0.995, True) if learned_rule else confidence_for(candidates)
        row = {
            "line_index": line_index,
            "invoice": invoice,
            "candidates": candidates,
            "confidence": confidence,
            "confident": confident,
            "learned_rule": learned_rule,
        }
        rows.append(row)
        if candidates and not confident:
            ambiguous.append(row)

    ai_choices = {}
    if ambiguous:
        try:
            ai_choices = resolve_ambiguous(ambiguous)
        except RuntimeError as exc:
            logger.warning("Bỏ qua bước phân xử AI: %s", exc)

    results = []
    for row in rows:
        invoice, candidates = row["invoice"], row["candidates"]
        chosen = candidates[0] if candidates and row["confident"] else None
        learned_rule = row.get("learned_rule")
        reason = (
            f"Đã học từ {learned_rule.get('confirmations', 1)} lần bạn xác nhận trước đó"
            if learned_rule else "Khớp chắc chắn theo tên, mã/số và giá"
        )
        ai_choice = ai_choices.get(row["line_index"])
        if ai_choice:
            chosen = next(
                (candidate for candidate in candidates
                 if candidate["item"]["variant_id"] == ai_choice["variant_id"]),
                None,
            )
            reason = ai_choice["reason"]
        item = chosen["item"] if chosen else None
        pricing_note = invoice.get("pricing_note", "")
        display_reason = f"{pricing_note}. {reason}" if pricing_note and item else reason
        results.append({
            "invoice_index": invoice.get("invoice_index", 0),
            "invoice_filename": invoice.get("invoice_filename", ""),
            "invoice_summary": invoice.get("invoice_summary", {}),
            "original_name": invoice["name"],
            "search_query": (
                item.get("sku") or item.get("barcode") or item["name"]
            ) if item else invoice["name"],
            "qty": invoice["qty"],
            "price": invoice["price"],
            "listed_price": invoice["listed_price"],
            "total": invoice.get("total"),
            "free_qty": invoice.get("free_qty", 0),
            "matched": item is not None,
            "learned_match": bool(learned_rule and item),
            "variant_id": item["variant_id"] if item else None,
            "sapo_name": item["name"] if item else None,
            "sku": item.get("sku", "") if item else "",
            "barcode": item.get("barcode", "") if item else "",
            "system_cost": item.get("cost", 0) if item else 0,
            "system_sale_price": item.get("price", 0) if item else 0,
            "new_sale_price": item.get("price", 0) if item else 0,
            "confidence": row["confidence"] if item else 0.0,
            "match_reason": display_reason if item else "Chưa đủ chắc chắn; cần người dùng chọn",
            "suggestions": [public_suggestion(candidate) for candidate in candidates[:3]],
        })
    results = promote_contextual_duplicates(results)
    results = merge_results_by_variant(results)
    results = list(generate_missing_skus(results))
    return attach_price_insights(results)


def analyze_paths(paths, mode="single_invoice"):
    documents = [file_to_document(path) for path in paths]
    return analyze_documents(documents, mode)


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "provider": "openai",
        "model": MODEL_NAME,
        "products": len(product_index.products),
        "openai_configured": client is not None,
        "learned_rules": learning_store.count(),
        "price_history_records": price_history_store.total_records(),
        "upload_modes": ["single_invoice", "separate_invoices"],
        "max_files_per_request": MAX_FILES_PER_REQUEST,
    })


@app.post("/api/learn")
def learn_product_match():
    payload = request.get_json(silent=True) or {}
    original_name = str(payload.get("original_name") or "").strip()
    variant_id = int(parse_number(payload.get("variant_id")))
    if not original_name or not variant_id:
        return jsonify({"error": "Thiếu tên hóa đơn hoặc ID phiên bản Sapo"}), 400
    product = product_index.by_variant_id.get(variant_id)
    if not product:
        return jsonify({"error": "Không tìm thấy phiên bản sản phẩm trong database Sapo"}), 404
    try:
        rule = learning_store.learn(original_name, payload.get("price"), variant_id)
    except (OSError, ValueError) as exc:
        logger.exception("Không thể lưu lựa chọn đã học")
        return jsonify({"error": str(exc)}), 500
    logger.info("Đã học '%s' -> variant %s (%s)", original_name, variant_id, product["name"])
    return jsonify({
        "ok": True,
        "variant_id": variant_id,
        "sapo_name": product["name"],
        "confirmations": rule["confirmations"],
        "learned_rules": learning_store.count(),
    })


@app.post("/api/prices/confirm")
def confirm_purchase_prices():
    payload = request.get_json(silent=True) or {}
    batch_id = str(payload.get("batch_id") or "").strip()
    raw_items = payload.get("items", [])
    if not batch_id or not isinstance(raw_items, list) or not raw_items:
        return jsonify({"error": "Thiếu mã xác nhận hoặc danh sách giá nhập"}), 400
    if len(raw_items) > 500:
        return jsonify({"error": "Danh sách xác nhận vượt quá 500 sản phẩm"}), 400
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        variant_id = int(parse_number(raw.get("variant_id")))
        if variant_id not in product_index.by_variant_id:
            continue
        items.append({
            "variant_id": variant_id,
            "price": parse_number(raw.get("price")),
            "qty": parse_number(raw.get("qty")),
            "supplier_name": str(raw.get("supplier_name") or "")[:200],
            "invoice_number": str(raw.get("invoice_number") or "")[:100],
            "invoice_date": str(raw.get("invoice_date") or "")[:40],
            "invoice_filename": str(raw.get("invoice_filename") or "")[:260],
        })
    if not items:
        return jsonify({"error": "Không có sản phẩm đã khớp hợp lệ để ghi lịch sử"}), 422
    try:
        recorded = price_history_store.record_batch(batch_id, items)
    except (OSError, ValueError) as exc:
        logger.exception("Không thể ghi lịch sử giá nhập")
        return jsonify({"error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "recorded": recorded,
        "price_history_records": price_history_store.total_records(),
    })


@app.post("/api/analyze")
def analyze():
    started = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    try:
        documents = decode_documents(payload)
        mode = str(payload.get("mode") or "single_invoice")
        results = analyze_documents(documents, mode)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        logger.exception("Không thể đọc hóa đơn")
        return jsonify({"error": str(exc)}), 503
    logger.info(
        "Đã xử lý %s file theo chế độ %s: %s sản phẩm trong %.2f giây",
        len(documents),
        mode,
        len(results),
        time.perf_counter() - started,
    )
    return jsonify(results)


if __name__ == "__main__":
    logger.info("Sapo Invoice Agent chạy tại http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", 5000)), threaded=True)
