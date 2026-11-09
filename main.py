import asyncio
import base64
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import flet as ft
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pywebpush import WebPushException, webpush

logger = logging.getLogger(__name__)

DATA_FILE = Path.home() / ".food_expiry_tracker.json"
PUSH_PRIVATE_KEY_FILE = Path.home() / ".food_expiry_push_private.pem"
PUSH_SUBSCRIPTIONS_FILE = Path.home() / ".food_expiry_push_subscriptions.json"
PUSH_DELIVERIES_FILE = Path.home() / ".food_expiry_push_deliveries.json"
ASSETS_DIR = Path(__file__).parent / "assets"
PUSH_CHECK_INTERVAL_SECONDS = 15 * 60
PUSH_VAPID_SUBJECT = os.environ.get(
    "PUSH_VAPID_SUBJECT",
    "https://github.com/Takaoka-Ryukoku-High-School/team2026-26H-iiokaka",
)
GREEN = "#2E6654"
GREEN_LIGHT = "#E2EDE5"
PAPER = "#F6F5EF"
INK = "#202C2A"
MUTED = "#77807B"
CORAL = "#CF6048"
EXPIRY_TYPES = {
    "best_before": "賞味期限",
    "use_by": "消費期限",
    "use_date": "使用期限",
}
FOOD_CATEGORIES = ("肉", "魚", "野菜", "果物", "飲み物", "お菓子", "その他")
DEFAULT_CATEGORY = "その他"


def normalize_name(value):
    cleaned = (value or "").strip()
    return cleaned if cleaned else "商品"


def update_food_details(food, name, expiry_value, category):
    try:
        expiry_date = date.fromisoformat((expiry_value or "").strip())
    except ValueError as error:
        raise ValueError("期限を YYYY-MM-DD 形式で入力してください。") from error
    if category not in FOOD_CATEGORIES:
        raise ValueError("ジャンルを選択してください。")
    food["name"] = normalize_name(name)
    food["expiry_date"] = expiry_date.isoformat()
    food["category"] = category


def parse_date_string(value):
    if not value:
        return None
    cleaned = re.sub(r"\s+", "", str(value))
    cleaned = cleaned.replace("／", "/").replace("。", ".").replace("年", "-").replace("月", "-").replace("日", "")
    cleaned = cleaned.replace(".", "-")
    cleaned = cleaned.replace("/", "-")
    cleaned = re.sub(r"-+", "-", cleaned)

    for fmt in ("%Y-%m-%d", "%Y-%m-%d", "%y-%m-%d"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def extract_expiry_from_text(text):
    if not text:
        return None
    normalized = text.replace("\n", " ")
    patterns = [
        ("best_before", r"(?:賞味期限|BEST\s*BEFORE|BESTBEFORE|best\s*before)",),
        ("use_by", r"(?:消費期限|USE\s*BY|USEBY|use\s*by)",),
        ("use_date", r"(?:使用期限|USE\s*DATE|USEDATE|use\s*date)",),
    ]
    date_pattern = r"\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|\d{4}\.\d{1,2}\.\d{1,2}"
    for key, pattern in patterns:
        match = re.search(rf"{pattern}\s*[:：]*\s*({date_pattern})", normalized, flags=re.IGNORECASE)
        if not match:
            match = re.search(rf"({date_pattern})\s*[:：]*\s*{pattern}", normalized, flags=re.IGNORECASE)
        if not match:
            continue
        date_text = match.group(1)
        parsed = parse_date_string(date_text)
        if parsed:
            return parsed, key
    return None


def get_expiring_foods(foods, today=None):
    today = today or date.today()
    latest_expiry = today + timedelta(days=3)
    return [
        food
        for food in foods
        if not food["eaten"] and date.fromisoformat(food["expiry_date"]) <= latest_expiry
    ]


def is_supported_push_endpoint(endpoint):
    if not isinstance(endpoint, str):
        return False
    try:
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    supported_hosts = {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "push.services.mozilla.com",
        "web.push.apple.com",
    }
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and (hostname in supported_hosts or hostname.endswith(".notify.windows.com"))
        and bool(parsed.path)
    )


def load_or_create_vapid_key():
    PUSH_PRIVATE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        private_key_pem = PUSH_PRIVATE_KEY_FILE.read_bytes()
    except FileNotFoundError:
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        try:
            file_descriptor = os.open(
                PUSH_PRIVATE_KEY_FILE,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            private_key_pem = PUSH_PRIVATE_KEY_FILE.read_bytes()
        else:
            with os.fdopen(file_descriptor, "wb") as key_file:
                key_file.write(private_key_pem)
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
        private_key.curve, ec.SECP256R1
    ):
        raise ValueError(f"Invalid Web Push VAPID key in {PUSH_PRIVATE_KEY_FILE}")
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key_text = base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=")
    return PUSH_PRIVATE_KEY_FILE, public_key_text


def read_json_file(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read notification data from {path}") from error


def write_json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    file_descriptor = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(file_descriptor, "w", encoding="utf-8") as data_file:
        json.dump(value, data_file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, path)


def load_push_subscriptions():
    subscriptions = read_json_file(PUSH_SUBSCRIPTIONS_FILE, [])
    if not isinstance(subscriptions, list) or any(
        not isinstance(subscription, dict)
        or not is_supported_push_endpoint(subscription.get("endpoint", ""))
        or not isinstance(subscription.get("keys"), dict)
        or not isinstance(subscription["keys"].get("p256dh"), str)
        or not isinstance(subscription["keys"].get("auth"), str)
        for subscription in subscriptions
    ):
        raise RuntimeError(f"Invalid notification subscriptions in {PUSH_SUBSCRIPTIONS_FILE}")
    return subscriptions


def send_web_push(subscription, payload):
    private_key_path, _ = load_or_create_vapid_key()
    webpush(
        subscription_info=subscription,
        data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=str(private_key_path),
        vapid_claims={"sub": PUSH_VAPID_SUBJECT},
        ttl=3600,
        timeout=10,
    )


def parse_push_subscription(payload):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Invalid push subscription")
    endpoint = payload.get("endpoint")
    keys = payload.get("keys")
    if (
        not isinstance(endpoint, str)
        or len(endpoint) > 4096
        or not is_supported_push_endpoint(endpoint)
        or not isinstance(keys, dict)
    ):
        raise HTTPException(status_code=422, detail="Invalid push subscription")
    normalized_keys = {}
    for key_name in ("p256dh", "auth"):
        key_value = keys.get(key_name)
        if not isinstance(key_value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,200}", key_value):
            raise HTTPException(status_code=422, detail="Invalid push subscription keys")
        normalized_keys[key_name] = key_value
    return {"endpoint": endpoint, "keys": normalized_keys}


async def parse_endpoint_request(request):
    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid JSON request") from error
    endpoint = payload.get("endpoint") if isinstance(payload, dict) else None
    if (
        not isinstance(endpoint, str)
        or len(endpoint) > 4096
        or not is_supported_push_endpoint(endpoint)
    ):
        raise HTTPException(status_code=422, detail="Invalid push endpoint")
    return endpoint


def create_app():
    app = ft.run(main, export_asgi_app=True)
    api = APIRouter()

    @api.get("/notifications")
    async def notification_settings():
        return FileResponse(
            ASSETS_DIR / "notifications.html",
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    @api.get("/service-worker.js")
    async def service_worker():
        return FileResponse(
            ASSETS_DIR / "service-worker.js",
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/",
            },
        )

    @api.get("/push/vapid-public-key")
    async def vapid_public_key():
        _, public_key = load_or_create_vapid_key()
        return {"publicKey": public_key}

    @api.post("/push/subscriptions")
    async def save_push_subscription(request: Request):
        try:
            payload = await request.json()
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid JSON request") from error
        subscription = parse_push_subscription(payload)
        subscriptions = load_push_subscriptions()
        subscriptions = [
            existing for existing in subscriptions if existing["endpoint"] != subscription["endpoint"]
        ]
        subscriptions.append(subscription)
        write_json_file(PUSH_SUBSCRIPTIONS_FILE, subscriptions)
        asyncio.create_task(notify_expiring_products())
        return {"saved": True}

    @api.delete("/push/subscriptions")
    async def delete_push_subscription(request: Request):
        endpoint = await parse_endpoint_request(request)
        subscriptions = [
            subscription
            for subscription in load_push_subscriptions()
            if subscription["endpoint"] != endpoint
        ]
        write_json_file(PUSH_SUBSCRIPTIONS_FILE, subscriptions)
        deliveries = read_json_file(PUSH_DELIVERIES_FILE, {})
        deliveries.pop(endpoint, None)
        write_json_file(PUSH_DELIVERIES_FILE, deliveries)
        return {"deleted": True}

    @api.post("/push/test")
    async def send_test_notification(request: Request):
        endpoint = await parse_endpoint_request(request)
        subscription = next(
            (item for item in load_push_subscriptions() if item["endpoint"] == endpoint),
            None,
        )
        if subscription is None:
            raise HTTPException(status_code=404, detail="Enable notifications before testing")
        try:
            await asyncio.to_thread(
                send_web_push,
                subscription,
                {
                    "title": "たべきりん",
                    "body": "テスト通知が届きました。",
                    "tag": "food-expiry-test",
                    "url": "/",
                },
            )
        except Exception as error:
            logger.exception("Could not send a test notification")
            raise HTTPException(status_code=502, detail="Could not send the test notification") from error
        return {"sent": True}

    async def notify_expiring_products():
        try:
            await asyncio.to_thread(send_pending_expiry_notifications)
        except Exception:
            logger.exception("Expiry notification check failed")

    worker_task = None

    async def start_notification_worker():
        nonlocal worker_task
        worker_task = asyncio.create_task(expiry_notification_worker())

    async def stop_notification_worker():
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    app.add_event_handler("startup", start_notification_worker)
    app.add_event_handler("shutdown", stop_notification_worker)
    mount_index = next(
        index for index, route in enumerate(app.router.routes) if getattr(route, "path", None) == ""
    )
    app.router.routes[mount_index:mount_index] = api.routes
    return app


def send_pending_expiry_notifications():
    subscriptions = load_push_subscriptions()
    expiring_foods = get_expiring_foods(load_foods())
    if not subscriptions or not expiring_foods:
        return

    deliveries = read_json_file(PUSH_DELIVERIES_FILE, {})
    if not isinstance(deliveries, dict):
        raise RuntimeError(f"Invalid notification delivery state in {PUSH_DELIVERIES_FILE}")
    changed = False
    for subscription in subscriptions[:]:
        endpoint = subscription["endpoint"]
        delivered = deliveries.setdefault(endpoint, {})
        new_foods = [
            food for food in expiring_foods if delivered.get(food["id"]) != food["expiry_date"]
        ]
        if not new_foods:
            continue
        names = [f"{food['name']}（{food['expiry_date']}）" for food in new_foods[:3]]
        if len(new_foods) > 3:
            names.append(f"ほか{len(new_foods) - 3}個")
        try:
            send_web_push(
                subscription,
                {
                    "title": "期限が近い商品があります",
                    "body": "、".join(names),
                    "tag": "food-expiry",
                    "url": "/",
                },
            )
        except WebPushException as error:
            if (
                error.response is not None
                and error.response.status_code in (404, 410)
            ):
                subscriptions.remove(subscription)
                deliveries.pop(endpoint, None)
                changed = True
                logger.info("Removed expired Web Push subscription")
                continue
            logger.exception("Could not send an expiry notification")
            continue
        delivered.update({food["id"]: food["expiry_date"] for food in new_foods})
        changed = True
    if changed:
        write_json_file(PUSH_SUBSCRIPTIONS_FILE, subscriptions)
        write_json_file(PUSH_DELIVERIES_FILE, deliveries)


async def expiry_notification_worker():
    while True:
        try:
            await asyncio.to_thread(send_pending_expiry_notifications)
        except Exception:
            logger.exception("Expiry notification check failed")
        await asyncio.sleep(PUSH_CHECK_INTERVAL_SECONDS)


def load_foods():
    try:
        foods = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(foods, list):
        return []

    valid_foods = []
    for food in foods:
        if not isinstance(food, dict):
            continue
        if (
            not isinstance(food.get("id"), str)
            or not isinstance(food.get("name"), str)
            or food.get("expiry_type") not in EXPIRY_TYPES
            or not isinstance(food.get("eaten"), bool)
            or ("image" in food and not isinstance(food["image"], str))
        ):
            continue
        food["name"] = normalize_name(food.get("name"))
        if food.get("category") not in FOOD_CATEGORIES:
            food["category"] = DEFAULT_CATEGORY
        try:
            date.fromisoformat(food.get("expiry_date", ""))
        except (TypeError, ValueError):
            continue
        valid_foods.append(food)
    return valid_foods


def save_foods(foods):
    DATA_FILE.write_text(json.dumps(foods, ensure_ascii=False, indent=2), encoding="utf-8")


def main(page: ft.Page):
    page.title = "たべきりん | 期限管理"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = PAPER
    page.padding = 24
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.scroll = ft.ScrollMode.AUTO

    foods = load_foods()
    current_filter = "all"
    current_category = "all"
    editing_food_id = None

    name_input = ft.TextField(
        label="商品名（入力しなくてもOK）",
        hint_text="例：牛乳、薬、日焼け止め",
        border_radius=6,
        border_color="#D4DED5",
        focused_border_color=GREEN,
        bgcolor="#FFFEFA",
        text_size=15,
        autofocus=True,
    )
    expiry_input = ft.TextField(
        label="期限",
        hint_text="YYYY-MM-DD",
        tooltip="例：2026-10-31",
        expand=True,
        border_radius=6,
        border_color="#D4DED5",
        focused_border_color=GREEN,
        bgcolor="#FFFEFA",
        text_size=15,
        keyboard_type=ft.KeyboardType.DATETIME,
        on_submit=lambda event: add_food(event),
    )
    expiry_type_input = ft.Dropdown(
        value="best_before",
        options=[
            ft.DropdownOption(key="best_before", text="賞味期限"),
            ft.DropdownOption(key="use_by", text="消費期限"),
            ft.DropdownOption(key="use_date", text="使用期限"),
        ],
        width=170,
        border_radius=6,
        border_color="#D4DED5",
        focused_border_color=GREEN,
        bgcolor="#FFFEFA",
    )
    category_input = ft.Dropdown(
        label="ジャンル",
        value=DEFAULT_CATEGORY,
        options=[ft.DropdownOption(key=category, text=category) for category in FOOD_CATEGORIES],
        border_radius=6,
        border_color="#D4DED5",
        focused_border_color=GREEN,
        bgcolor="#FFFEFA",
    )
    message_text = ft.Text(size=12, color=CORAL, visible=False)
    food_list = ft.Column(spacing=0)
    food_count = ft.Text(color=MUTED, size=12)
    summary_text = ft.Text(color=MUTED, size=11)
    notification_detail = ft.Text(size=12, color=INK)
    filter_buttons = {}
    search_input = ft.TextField(
        label="登録済み商品を検索",
        hint_text="商品名を入力",
        prefix_icon=ft.Icons.SEARCH,
        border_radius=6,
        border_color="#D4DED5",
        focused_border_color=GREEN,
        bgcolor="#FFFEFA",
        on_change=lambda event: render(),
    )
    category_filter = ft.Dropdown(
        value="all",
        options=[
            ft.DropdownOption(key="all", text="全ジャンル"),
            *[ft.DropdownOption(key=category, text=category) for category in FOOD_CATEGORIES],
        ],
        width=150,
        border_radius=6,
        border_color="#D4DED5",
        focused_border_color=GREEN,
        bgcolor="#FFFEFA",
        on_select=lambda event: set_category_filter(event.control.value),
    )
    expiry_notification = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED, color=CORAL),
                ft.Column(
                    [
                        ft.Text("期限が近い商品があります", size=14, color=CORAL, weight=ft.FontWeight.BOLD),
                        notification_detail,
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.TextButton("一覧を見る", on_click=lambda event: set_filter("soon")),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        bgcolor="#FFF0E8",
        border=ft.Border.all(1, "#F0C7B9"),
        border_radius=6,
        padding=12,
        visible=False,
    )
    expiry_entry_layout = ft.Container()
    composer_actions_layout = ft.Container()

    def persist():
        try:
            save_foods(foods)
            message_text.visible = False
            return True
        except OSError:
            message_text.value = "保存できませんでした。書き込み権限をご確認ください。"
            message_text.visible = True
            return False

    def set_filter(value):
        nonlocal current_filter
        current_filter = value
        render()

    def set_category_filter(value):
        nonlocal current_category
        current_category = value
        render()

    def add_food(event):
        nonlocal current_category
        name = normalize_name(name_input.value)
        try:
            expiry_date = date.fromisoformat((expiry_input.value or "").strip())
        except ValueError:
            message_text.value = "期限を YYYY-MM-DD 形式で入力してください。"
            message_text.visible = True
            page.update()
            return

        food = {
            "id": str(uuid4()),
            "name": name,
            "expiry_date": expiry_date.isoformat(),
            "expiry_type": expiry_type_input.value,
            "category": category_input.value,
            "eaten": False,
        }
        foods.append(food)
        if persist():
            name_input.value = ""
            expiry_input.value = ""
        current_category = "all"
        category_filter.value = "all"
        set_filter("all")

    def toggle_eaten(food_id):
        for food in foods:
            if food["id"] == food_id:
                food["eaten"] = not food["eaten"]
                break
        persist()
        render()

    def delete_food(food_id):
        nonlocal editing_food_id
        foods[:] = [food for food in foods if food["id"] != food_id]
        if editing_food_id == food_id:
            editing_food_id = None
        persist()
        render()

    def set_editing_food(food_id):
        nonlocal editing_food_id
        editing_food_id = food_id
        render()

    def save_food_edit(food_id, name_value, expiry_value, category_value):
        nonlocal current_category, editing_food_id
        food = next((item for item in foods if item["id"] == food_id), None)
        if food is None:
            editing_food_id = None
            render()
            return

        old_name = food["name"]
        old_expiry_date = food["expiry_date"]
        old_category = food["category"]
        try:
            update_food_details(food, name_value, expiry_value, category_value)
        except ValueError as error:
            message_text.value = str(error)
            message_text.visible = True
            page.update()
            return

        if not persist():
            food["name"] = old_name
            food["expiry_date"] = old_expiry_date
            food["category"] = old_category
            page.update()
            return
        editing_food_id = None
        if current_category != "all" and current_category != food["category"]:
            current_category = "all"
            category_filter.value = "all"
        render()

    def cancel_food_edit():
        nonlocal editing_food_id
        editing_food_id = None
        render()

    def expiry_status(food):
        days_left = (date.fromisoformat(food["expiry_date"]) - date.today()).days
        if days_left < 0:
            return f"期限から {abs(days_left)} 日", CORAL
        if days_left == 0:
            return "今日まで", CORAL
        if days_left == 1:
            return "あと1日", CORAL
        if days_left <= 3:
            return f"あと{days_left}日", "#9A7629"
        return f"あと{days_left}日", GREEN

    def render():
        today = date.today()
        search_query = (search_input.value or "").strip().casefold()
        expiring_foods = get_expiring_foods(foods, today)
        expiring_food_ids = {food["id"] for food in expiring_foods}
        visible_foods = [
            food
            for food in foods
            if (not search_query or search_query in food["name"].casefold())
            and (current_category == "all" or food["category"] == current_category)
            and (
                current_filter == "all"
                or (current_filter == "soon" and food["id"] in expiring_food_ids)
                or (current_filter == "eaten" and food["eaten"])
            )
        ]
        expiry_notification.visible = bool(expiring_foods)
        if expiring_foods:
            notification_detail.value = "、".join(
                f"{food['name']}（{food['expiry_date']}）" for food in expiring_foods[:3]
            )
            if len(expiring_foods) > 3:
                notification_detail.value += f" ほか{len(expiring_foods) - 3}個"
        visible_foods.sort(key=lambda food: (food["eaten"], food["expiry_date"], food["name"]))
        uneaten_count = sum(not food["eaten"] for food in foods)
        food_count.value = f"まだ使う: {uneaten_count} 個"
        summary_text.value = (
            f"使ったもの: {len(foods) - uneaten_count} 個"
            if foods
            else "商品を登録して、期限を忘れないようにしましょう"
        )

        for key, button in filter_buttons.items():
            selected = key == current_filter
            button.style = ft.ButtonStyle(
                color=GREEN if selected else MUTED,
                bgcolor=GREEN_LIGHT if selected else "transparent",
                shape=ft.RoundedRectangleBorder(radius=4),
            )

        rows = []
        is_mobile = (getattr(page, "width", None) or 748) < 520
        for food in visible_foods:
            status, status_color = expiry_status(food)
            if food["eaten"]:
                status, status_color = "食べました", GREEN
            eaten_button = ft.TextButton(
                "食べた" if not food["eaten"] else "未食に戻す",
                icon=ft.Icons.CHECK_CIRCLE_OUTLINE if not food["eaten"] else ft.Icons.UNDO,
                on_click=lambda event, food_id=food["id"]: toggle_eaten(food_id),
            )
            delete_button = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_color=MUTED,
                tooltip=f"「{food['name']}」を削除",
                on_click=lambda event, food_id=food["id"]: delete_food(food_id),
            )
            if editing_food_id == food["id"]:
                edit_name_input = ft.TextField(
                    label="商品名",
                    value=food["name"],
                    expand=True,
                    autofocus=True,
                )
                edit_expiry_input = ft.TextField(
                    label="期限（YYYY-MM-DD）",
                    value=food["expiry_date"],
                    expand=True,
                    keyboard_type=ft.KeyboardType.DATETIME,
                )
                edit_category_input = ft.Dropdown(
                    label="ジャンル",
                    value=food["category"],
                    options=[
                        ft.DropdownOption(key=category, text=category)
                        for category in FOOD_CATEGORIES
                    ],
                    expand=True,
                )
                def submit_food_edit(
                    event,
                    food_id=food["id"],
                    name_field=edit_name_input,
                    expiry_field=edit_expiry_input,
                    category_field=edit_category_input,
                ):
                    save_food_edit(
                        food_id,
                        name_field.value,
                        expiry_field.value,
                        category_field.value,
                    )
                edit_expiry_input.on_submit = submit_food_edit
                food_content = [
                    edit_name_input,
                    edit_category_input,
                        edit_expiry_input,
                        ft.Row(
                            [
                            ft.TextButton(
                                "保存",
                                icon=ft.Icons.CHECK,
                                on_click=submit_food_edit,
                            ),
                            ft.TextButton(
                                "キャンセル",
                                on_click=lambda event: cancel_food_edit(),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ]
            else:
                edit_button = ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    icon_color=MUTED,
                    tooltip=f"「{food['name']}」を編集",
                    on_click=lambda event, food_id=food["id"]: set_editing_food(food_id),
                )
                food_name = ft.Text(
                    food["name"],
                    size=15,
                    color=INK,
                    weight=ft.FontWeight.W_600,
                    expand=True,
                )
                food_status = ft.Text(
                    status,
                    size=12,
                    color=status_color,
                    weight=ft.FontWeight.BOLD,
                )
                food_expiry = ft.Text(
                    f"{EXPIRY_TYPES[food['expiry_type']]}：{food['expiry_date']}",
                    size=12,
                    color=MUTED,
                    expand=True,
                )
                if is_mobile:
                    food_content = [
                        ft.Row(
                            [food_name, food_status],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            [
                                ft.Text(food["category"], size=11, color=GREEN),
                                food_expiry,
                            ],
                            spacing=8,
                        ),
                        ft.Row(
                            [
                                eaten_button,
                                ft.Row([edit_button, delete_button], spacing=0),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ]
                else:
                    food_content = [
                        ft.Row(
                            [food_name, ft.Text(food["category"], size=11, color=GREEN), food_status],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            [food_expiry, eaten_button, edit_button, delete_button],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ]
            rows.extend(
                [
                    ft.Container(
                        content=ft.Column(food_content, spacing=2),
                        padding=ft.Padding.symmetric(vertical=8),
                    ),
                    ft.Divider(height=1, thickness=1, color="#DEDFD5"),
                ]
            )

        if not visible_foods:
            empty_text = (
                "検索条件に一致する商品がありません。"
                if search_query
                else "もうすぐ期限のものはありません。"
                if current_filter == "soon"
                else "まだ使った記録はありません。"
                if current_filter == "eaten"
                else "商品名と期限を入れてみましょう。"
            )
            rows = [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text("○", size=24, color="#9A7629", text_align=ft.TextAlign.CENTER),
                            ft.Text(empty_text, size=13, color=MUTED, text_align=ft.TextAlign.CENTER),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                    ),
                    padding=32,
                    alignment=ft.Alignment.CENTER,
                )
            ]

        food_list.controls = rows
        page.update()

    for key, label in (("all", "すべて"), ("soon", "期限が近い"), ("eaten", "食べた")):
        filter_buttons[key] = ft.TextButton(label, on_click=lambda event, value=key: set_filter(value))

    today = ft.Text(
        date.today().strftime("%Y年%m月%d日"),
        size=12,
        color=MUTED,
    )
    expiry_entry_layout.content = ft.Row(
        [expiry_input, expiry_type_input],
        spacing=8,
    )
    composer_actions_layout.content = ft.Row(
        [
            ft.Button(
                "登録 ＋",
                on_click=add_food,
                bgcolor=GREEN,
                color="white",
                height=48,
                expand=True,
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=5)),
            ),
            ft.TextButton(
                "期限通知を設定",
                icon=ft.Icons.NOTIFICATIONS_OUTLINED,
                url="/notifications",
            ),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    composer = ft.Column(
        [
            name_input,
            category_input,
            expiry_entry_layout,
            composer_actions_layout,
            message_text,
        ],
        spacing=10,
    )

    content = ft.Column(
        [
            ft.Row(
                [
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Text("食", color="white", size=15, weight=ft.FontWeight.BOLD),
                                width=28,
                                height=28,
                                bgcolor=GREEN,
                                border_radius=14,
                                alignment=ft.Alignment.CENTER,
                            ),
                            ft.Text("TABEKIRIN", size=12, color=GREEN, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=10,
                    ),
                    today,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Column(
                [
                    ft.Text("たべきりん", size=38, color=INK, weight=ft.FontWeight.W_600),
                    ft.Text("食品や薬、日焼け止めの期限をひと目で見て、使い忘れを防ぎます。", size=13, color=MUTED),
                ],
                spacing=4,
            ),
            ft.Container(
                content=ft.Column(
                    [
                        ft.Container(content=composer, padding=18, bgcolor=GREEN_LIGHT),
                        expiry_notification,
                        ft.Container(
                            content=search_input,
                            padding=ft.Padding(left=18, right=18, top=14, bottom=4),
                        ),
                        ft.Container(
                            content=ft.Row(
                                [ft.Row(list(filter_buttons.values()), spacing=0), food_count],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            padding=ft.Padding(left=18, right=18, top=8, bottom=8),
                            border=ft.Border.only(bottom=ft.BorderSide(1, "#DEDFD5")),
                        ),
                        ft.Container(
                            content=category_filter,
                            padding=ft.Padding(left=18, right=18, top=8, bottom=4),
                        ),
                        ft.Container(content=food_list, padding=ft.Padding.symmetric(horizontal=18)),
                        ft.Container(
                            content=summary_text,
                            padding=ft.Padding(left=18, right=18, top=8, bottom=8),
                            border=ft.Border.only(top=ft.BorderSide(1, "#DEDFD5")),
                        ),
                    ],
                    spacing=0,
                ),
                width=700,
                bgcolor="#FFFEFA",
                border=ft.Border.all(1, "#E3E3D9"),
                border_radius=8,
                shadow=ft.BoxShadow(blur_radius=28, color="#182D2314", offset=ft.Offset(0, 10)),
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            ),
            ft.Text(
                "※期限表示は、未開封で表示どおりに保存した場合の目安です。開封後は期限にかかわらず早めに食べましょう。",
                size=11,
                color=MUTED,
            ),
        ],
        width=700,
        spacing=28,
    )

    app_container = ft.Container(content=content, width=700, padding=ft.Padding.only(top=18, bottom=32))
    workspace_card = content.controls[2]

    def set_composer_layout(is_mobile):
        if is_mobile:
            expiry_type_input.width = None
            expiry_entry_layout.content = ft.Column(
                [expiry_input, expiry_type_input],
                spacing=8,
            )
            composer_actions_layout.content = ft.Column(
                [
                    ft.Button(
                        "登録 ＋",
                        on_click=add_food,
                        bgcolor=GREEN,
                        color="white",
                        height=48,
                        expand=True,
                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=5)),
                    ),
                    ft.TextButton(
                        "期限通知を設定",
                        icon=ft.Icons.NOTIFICATIONS_OUTLINED,
                        url="/notifications",
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            )
        else:
            expiry_type_input.width = 170
            expiry_entry_layout.content = ft.Row(
                [expiry_input, expiry_type_input],
                spacing=8,
            )
            composer_actions_layout.content = ft.Row(
                [
                    ft.Button(
                        "登録 ＋",
                        on_click=add_food,
                        bgcolor=GREEN,
                        color="white",
                        height=48,
                        expand=True,
                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=5)),
                    ),
                    ft.TextButton(
                        "期限通知を設定",
                        icon=ft.Icons.NOTIFICATIONS_OUTLINED,
                        url="/notifications",
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

    def resize_page(event):
        viewport_width = page.width or 748
        is_mobile = viewport_width < 520
        page.padding = 12 if is_mobile else 24
        content_width = min(700, max(272, viewport_width - page.padding * 2))
        app_container.width = content_width
        content.width = content_width
        workspace_card.width = content_width
        set_composer_layout(is_mobile)
        render()

    set_composer_layout((getattr(page, "width", None) or 748) < 520)
    page.on_resize = resize_page
    page.add(app_container)
    render()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8550)
