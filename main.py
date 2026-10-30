import base64
import json
import re
from datetime import date, datetime, timedelta
from io import BytesIO
from mimetypes import guess_type
from pathlib import Path
from uuid import uuid4

import flet as ft

try:
    import pytesseract
except ModuleNotFoundError:  # optional OCR dependency
    pytesseract = None

try:
    from PIL import Image
except ModuleNotFoundError:  # optional OCR dependency
    Image = None


DATA_FILE = Path.home() / ".food_expiry_tracker.json"
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
FOOD_CATEGORIES = ("肉", "魚", "野菜", "果物", "お菓子", "その他")
DEFAULT_CATEGORY = "その他"


def normalize_name(value):
    cleaned = (value or "").strip()
    return cleaned if cleaned else "商品"


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


def scan_expiry_from_image_bytes(image_bytes):
    if not image_bytes:
        return None
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        text = pytesseract.image_to_string(image, lang="jpn+eng", config="--psm 6")
    except Exception:
        return None
    if not text:
        return None
    return extract_expiry_from_text(text)


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
    page.title = "たべごろ | 期限管理"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = PAPER
    page.padding = 24
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.scroll = ft.ScrollMode.AUTO

    foods = load_foods()
    current_filter = "all"
    current_category = "all"
    selected_photo = None
    photo_picker = ft.FilePicker()
    page.services.append(photo_picker)

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
    photo_preview = ft.Container(visible=False)
    privacy_note = ft.Text(
        "写真はなくてもOKです。顔や個人情報が写る写真は避けてください。",
        size=11,
        color=MUTED,
    )
    message_text = ft.Text(size=12, color=CORAL, visible=False)
    food_list = ft.Column(spacing=0)
    food_count = ft.Text(color=MUTED, size=12)
    summary_text = ft.Text(color=MUTED, size=11)
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
        nonlocal selected_photo, current_category
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
        if selected_photo:
            food["image"] = selected_photo
        foods.append(food)
        if persist():
            name_input.value = ""
            expiry_input.value = ""
            selected_photo = None
            photo_preview.content = None
            photo_preview.visible = False
        current_category = "all"
        category_filter.value = "all"
        set_filter("all")

    async def choose_photo(event):
        nonlocal selected_photo
        files = await photo_picker.pick_files(
            dialog_title="商品の写真を撮る / 選ぶ",
            file_type=ft.FilePickerFileType.IMAGE,
            allow_multiple=False,
            with_data=True,
            compression_quality=70,
        )
        if not files:
            return
        photo = files[0]
        if photo.bytes is None:
            message_text.value = "写真を読み込めませんでした。もう一度お試しください。"
            message_text.visible = True
            page.update()
            return
        mime_type = guess_type(photo.name)[0] or "image/jpeg"
        encoded_photo = base64.b64encode(photo.bytes).decode("ascii")
        selected_photo = f"data:{mime_type};base64,{encoded_photo}"
        detected = scan_expiry_from_image_bytes(photo.bytes)
        if detected:
            expiry_date, expiry_type = detected
            expiry_input.value = expiry_date
            expiry_type_input.value = expiry_type
            message_text.value = f"写真から期限を読み取りました: {expiry_date}"
            message_text.visible = True
        else:
            message_text.visible = False
        photo_preview.content = ft.Row(
            [
                ft.Image(
                    src=selected_photo,
                    width=96,
                    height=72,
                    fit=ft.BoxFit.COVER,
                    border_radius=6,
                ),
                ft.Text(f"写真を追加しました: {photo.name}", size=12, color=GREEN),
                ft.TextButton("写真を外す", on_click=remove_photo),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        photo_preview.visible = True
        page.update()

    def remove_photo(event):
        nonlocal selected_photo
        selected_photo = None
        photo_preview.content = None
        photo_preview.visible = False
        page.update()

    def toggle_eaten(food_id):
        for food in foods:
            if food["id"] == food_id:
                food["eaten"] = not food["eaten"]
                break
        persist()
        render()

    def delete_food(food_id):
        foods[:] = [food for food in foods if food["id"] != food_id]
        persist()
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
        visible_foods = [
            food
            for food in foods
            if (not search_query or search_query in food["name"].casefold())
            and (current_category == "all" or food["category"] == current_category)
            and (
                current_filter == "all"
                or (current_filter == "soon" and not food["eaten"]
                    and date.fromisoformat(food["expiry_date"]) <= today + timedelta(days=3))
                or (current_filter == "eaten" and food["eaten"])
            )
        ]
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
            food_content = [
                ft.Row(
                    [
                        ft.Text(food["name"], size=15, color=INK, weight=ft.FontWeight.W_600, expand=True),
                        ft.Text(food["category"], size=11, color=GREEN),
                        ft.Text(status, size=12, color=status_color, weight=ft.FontWeight.BOLD),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        ft.Text(
                            f"{EXPIRY_TYPES[food['expiry_type']]}：{food['expiry_date']}",
                            size=12,
                            color=MUTED,
                            expand=True,
                        ),
                        eaten_button,
                        delete_button,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ]
            if food.get("image"):
                food_content.insert(
                    0,
                    ft.Image(
                        src=food["image"],
                        width=120,
                        height=90,
                        fit=ft.BoxFit.COVER,
                        border_radius=6,
                    ),
                )
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
    composer = ft.Column(
        [
            name_input,
            category_input,
            ft.Row(
                [
                    expiry_input,
                    expiry_type_input,
                ],
                spacing=8,
            ),
            photo_preview,
            privacy_note,
            ft.Row(
                [
                    ft.OutlinedButton(
                        "写真を撮る / 選ぶ",
                        icon=ft.Icons.CAMERA_ALT,
                        on_click=choose_photo,
                        height=48,
                    ),
                    ft.Button(
                        "登録 ＋",
                        on_click=add_food,
                        bgcolor=GREEN,
                        color="white",
                        height=48,
                        expand=True,
                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=5)),
                    ),
                ],
                spacing=8,
            ),
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
                            ft.Text("TABEGORO", size=12, color=GREEN, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=10,
                    ),
                    today,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Column(
                [
                    ft.Text("たべごろ", size=38, color=INK, weight=ft.FontWeight.W_600),
                    ft.Text("食品や薬、日焼け止めの期限をひと目で見て、使い忘れを防ぎます。", size=13, color=MUTED),
                ],
                spacing=4,
            ),
            ft.Container(
                content=ft.Column(
                    [
                        ft.Container(content=composer, padding=18, bgcolor=GREEN_LIGHT),
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

    def resize_page(event):
        viewport_width = page.width or 748
        page.padding = 12 if viewport_width < 520 else 24
        content_width = min(700, max(272, viewport_width - page.padding * 2))
        app_container.width = content_width
        content.width = content_width
        workspace_card.width = content_width
        page.update()

    page.on_resize = resize_page
    page.add(app_container)
    render()


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.WEB_BROWSER, port=8550)
