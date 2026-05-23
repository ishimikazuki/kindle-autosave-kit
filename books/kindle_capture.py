#!/usr/bin/env python3
"""
Kindle Cloud Reader の全ページをスクショ → OCR → content.md に保存。
自動ログイン機能付き（macOS キーチェーンから認証情報取得）。

使い方:
  python3 kindle_capture.py --book 001_slug [--pages 50] [--start 1]
"""

import argparse
import base64
import hashlib
import json
import os
import re
import site
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, site.getusersitepackages())

from playwright.sync_api import sync_playwright

# --- 設定 ---
BOOKS_DIR = Path(__file__).parent / "data"
BROWSER_PROFILE = Path(__file__).parent / ".browser-profile"
SCREENSHOT_DELAY = 2
KINDLE_BASE_URL = "https://read.amazon.co.jp"
GCP_PROJECT = os.environ.get("KINDLE_CAPTURE_GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
NEXT_PAGE_KEY = {"ja": "ArrowLeft", "en": "ArrowRight"}


def load_meta(book_dir: Path) -> dict:
    meta_path = book_dir / "meta.json"
    if not meta_path.exists():
        print(f"❌ {meta_path} が見つかりません")
        sys.exit(1)
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_meta(book_dir: Path, meta: dict):
    with open(book_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def load_progress(book_dir: Path) -> dict:
    progress_path = book_dir / "progress.json"
    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(book_dir: Path, progress: dict):
    progress["updated_at"] = datetime.now().isoformat()
    with open(book_dir / "progress.json", "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def auto_detect_start(book_dir: Path) -> int:
    """progress.json から自動で再開ページを判定"""
    progress = load_progress(book_dir)
    if progress and progress.get("last_completed_page", 0) > 0:
        resume_page = progress["last_completed_page"] + 1
        print(f"   🔄 progress.json 検出: ページ {resume_page} から再開")
        return resume_page
    return 1


def append_to_content(book_dir: Path, page_num: int, text: str):
    """content_partial.md にページを逐次追記"""
    partial_path = book_dir / "content_partial.md"
    with open(partial_path, "a", encoding="utf-8") as f:
        if page_num > 1:
            f.write("\n\n---\n\n")
        f.write(f"<!-- page {page_num} -->\n{text}")


def init_content_partial(book_dir: Path, meta: dict):
    """content_partial.md のヘッダーを書き出し（新規開始時のみ）"""
    partial_path = book_dir / "content_partial.md"
    if partial_path.exists():
        return  # 再開時は既存ファイルを維持
    title = meta.get("title", "Unknown")
    header = f"# {title}\n\n"
    header += f"> Author: {meta.get('author', '不明')}\n"
    header += f"> Captured: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    header += f"> Pages: (in progress)\n\n---\n\n"
    with open(partial_path, "w", encoding="utf-8") as f:
        f.write(header)


def finalize_content(book_dir: Path, page_count: int):
    """content_partial.md → content.md にリネーム、ページ数を更新"""
    partial_path = book_dir / "content_partial.md"
    content_path = book_dir / "content.md"
    if not partial_path.exists():
        return
    # ヘッダーのページ数を更新
    text = partial_path.read_text(encoding="utf-8")
    text = text.replace("> Pages: (in progress)", f"> Pages: {page_count}")
    with open(content_path, "w", encoding="utf-8") as f:
        f.write(text)
    partial_path.unlink()
    print(f"   ✅ content.md 確定 ({page_count} ページ)")


def image_hash(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _get_gcp_token() -> str:
    """gcloud CLI からアクセストークンを取得"""
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def ocr_image(path: Path, language: str = "ja") -> str:
    """Google Cloud Vision API で画像からテキスト抽出（縦書き日本語対応）"""
    try:
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        token = _get_gcp_token()
        lang_hint = "ja" if language in ("ja", "jpn") else "en"

        payload = json.dumps({
            "requests": [{
                "image": {"content": img_b64},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": [lang_hint]}
            }]
        }).encode()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if GCP_PROJECT:
            headers["x-goog-user-project"] = GCP_PROJECT

        req = urllib.request.Request(
            "https://vision.googleapis.com/v1/images:annotate",
            data=payload,
            headers=headers,
        )
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        text = result["responses"][0].get("fullTextAnnotation", {}).get("text", "")
        return text.strip()
    except Exception as e:
        print(f"  ⚠️ OCR エラー: {e}")
        return ""


def do_login(page, book_dir: Path):
    """Amazon Kindle Cloud Reader にログイン"""
    print("   🔐 自動ログイン中...")

    email = subprocess.run(
        ["security", "find-generic-password", "-a", "amazon-email", "-s", "kindle-capture", "-w"],
        capture_output=True, text=True, timeout=5
    ).stdout.strip()
    password = subprocess.run(
        ["security", "find-generic-password", "-a", "amazon-pass", "-s", "kindle-capture", "-w"],
        capture_output=True, text=True, timeout=5
    ).stdout.strip()

    if not email or not password:
        print("   ❌ キーチェーンから認証情報を取得できません")
        return False

    # ランディングページにアクセス
    page.goto(f"{KINDLE_BASE_URL}/")
    time.sleep(3)

    current_url = page.url
    print(f"   現在のURL: {current_url}")

    # 既にログイン済みならスキップ
    if "kindle-library" in current_url:
        print("   ✅ 既にログイン済み")
        return True

    # ランディングページからサインインページへ
    # 「アカウントでログイン」ボタンか、直接サインインリンクを探す
    clicked = False
    for selector in [
        'button:has-text("ログイン")',
        'button:has-text("Sign In")',
        'button:has-text("サインイン")',
        'a[href*="ap/signin"]',
    ]:
        try:
            el = page.locator(selector)
            if el.count() > 0 and el.first.is_visible():
                el.first.click()
                clicked = True
                print(f"   クリック: {selector}")
                time.sleep(3)
                break
        except Exception:
            continue

    if not clicked:
        print("   ⚠️ ログインボタンが見つかりません")
        page.screenshot(path=str(book_dir / "debug_no_login_btn.png"))
        return False

    print(f"   サインインページ: {page.url}")

    # Create account ページに飛ばされた場合
    body_text = page.text_content("body") or ""
    if "Create" in body_text and "Already" in body_text:
        print("   → Create account ページを検出、Sign in へ遷移")
        try:
            page.click('a:has-text("Sign in")', timeout=5000)
            time.sleep(3)
            print(f"   → Sign in ページ: {page.url}")
        except Exception:
            page.screenshot(path=str(book_dir / "debug_no_signin_link.png"))
            return False

    # メール入力
    try:
        email_field = page.locator('#ap_email')
        if email_field.count() == 0:
            email_field = page.locator('input[type="email"]')
        if email_field.count() > 0:
            email_field.first.click()
            time.sleep(0.3)
            email_field.first.press_sequentially(email, delay=50)
            time.sleep(0.5)
            page.keyboard.press("Enter")
            time.sleep(3)
            print("   ✅ メール入力完了")
        else:
            print("   ⚠️ メールフィールドが見つかりません")
            page.screenshot(path=str(book_dir / "debug_no_email.png"))
    except Exception as e:
        print(f"   ⚠️ メール入力エラー: {e}")
        page.screenshot(path=str(book_dir / "debug_email_err.png"))

    # パスワード入力
    try:
        pass_field = page.locator('#ap_password')
        if pass_field.count() == 0:
            pass_field = page.locator('input[type="password"]')
        if pass_field.count() > 0:
            pass_field.first.click()
            time.sleep(0.3)
            pass_field.first.fill(password)
            time.sleep(0.5)
            page.screenshot(path=str(book_dir / "debug_before_submit.png"))
            # JavaScript でフォーム送信
            page.evaluate("document.querySelector('#signInSubmit')?.click() || document.querySelector('form')?.submit()")
            time.sleep(8)
            print("   ✅ パスワード入力完了")
        else:
            print("   ⚠️ パスワードフィールドが見つかりません")
            page.screenshot(path=str(book_dir / "debug_no_pass.png"))
    except Exception as e:
        print(f"   ⚠️ パスワード入力エラー: {e}")
        page.screenshot(path=str(book_dir / "debug_pass_err.png"))

    # リダイレクト完了を待つ
    try:
        page.wait_for_url("**/kindle-library**", timeout=15000)
    except Exception:
        pass
    time.sleep(2)

    # 2FA 検出 → Cancel → パスワード再入力で回避
    page_text = page.text_content("body") or ""
    is_2fa = False
    try:
        qr = page.locator('img[alt*="QR"], canvas, #auth-mfa-otpcode, input[name="otpCode"]')
        if qr.count() > 0:
            is_2fa = True
        if any(kw in page_text.lower() for kw in ["approve the notification", "enter otp", "enter the otp", "verification code", "two-step"]):
            is_2fa = True
    except Exception:
        pass

    if is_2fa:
        print("   ⚠️ 2FA 検出 → Cancel → パスワード再入力で回避...")
        cancelled = False
        for selector in [
            'a:has-text("Cancel")', 'button:has-text("Cancel")',
            'a:has-text("キャンセル")', 'button:has-text("キャンセル")',
            '#auth-cancel-button',
        ]:
            try:
                btn = page.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    print("   ✅ Cancel クリック")
                    cancelled = True
                    break
            except Exception:
                continue

        if cancelled:
            time.sleep(3)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            # パスワード再入力
            for sel in ['#ap_password', 'input[name="password"]', 'input[type="password"]']:
                try:
                    field = page.locator(sel)
                    if field.count() > 0 and field.first.is_visible():
                        field.first.fill(password)
                        print("   🔑 パスワード再入力OK")
                        time.sleep(0.5)
                        page.evaluate("document.querySelector('#signInSubmit')?.click() || document.querySelector('form')?.submit()")
                        time.sleep(8)
                        break
                except Exception:
                    continue
            try:
                page.wait_for_url("**/kindle-library**", timeout=15000)
            except Exception:
                pass
            time.sleep(2)
        else:
            print("   ❌ Cancel ボタンが見つかりません。手動で2FAを完了してください。")
            page.screenshot(path=str(book_dir / "debug_2fa.png"))
            return False

    final_url = page.url
    print(f"   ログイン後URL: {final_url}")
    page.screenshot(path=str(book_dir / "debug_after_login.png"))

    # ポップアップダイアログがあれば閉じる
    try:
        page.click('button:has-text("OK")', timeout=3000)
        time.sleep(1)
    except Exception:
        pass
    try:
        page.click('button:has-text("Close")', timeout=2000)
        time.sleep(1)
    except Exception:
        pass

    if "kindle-library" not in final_url:
        print("   ❌ ログイン失敗（kindle-library に到達できず）")
        page.screenshot(path=str(book_dir / "debug_login_failed.png"))
        return False
    return True


def set_font_size_min(page):
    """フォントサイズを最小に設定（マウスドラッグ版）"""
    print("   🔤 フォントサイズを最小に設定中...")
    try:
        # 1. Reader settings パネルを開く（ツールバーが隠れていてもJSで動く）
        result = page.evaluate("""() => {
            const btn = document.querySelector('ion-button[aria-label="Reader settings"]');
            if (!btn) return 'no settings button';
            btn.click();
            return 'opened';
        }""")
        if result != "opened":
            print(f"   ⚠️ 設定ボタンが見つかりません: {result}")
            return
        time.sleep(2)

        # 2. スライダーの位置を取得
        range_info = page.evaluate("""() => {
            const range = document.querySelector('ion-range[aria-label="Choose your preferred font size"]');
            if (!range) return null;
            const knob = range.shadowRoot?.querySelector('.range-knob-handle');
            return {
                value: range.value, min: range.min, max: range.max,
                knobRect: knob ? knob.getBoundingClientRect() : null,
                rangeRect: range.getBoundingClientRect(),
            };
        }""")
        if not range_info:
            print("   ⚠️ フォントサイズスライダーが見つかりません")
            page.keyboard.press("Escape")
            return

        old_value = range_info["value"]
        rect = range_info["rangeRect"]
        knob = range_info.get("knobRect")

        # 3. マウスドラッグでスライダーを左端（min）に移動
        start_x = knob["x"] + knob["width"] / 2 if knob else rect["x"] + rect["width"] / 2
        start_y = rect["y"] + rect["height"] / 2
        end_x = rect["x"] + 5  # 左端 = min

        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(end_x, start_y, steps=20)
        page.mouse.up()
        time.sleep(1)

        new_value = page.evaluate("""() => {
            const r = document.querySelector('ion-range[aria-label="Choose your preferred font size"]');
            return r ? r.value : -1;
        }""")
        print(f"   フォントサイズ: {old_value} → {new_value}")

        # 4. 設定パネルを閉じる
        page.keyboard.press("Escape")
        time.sleep(1)

        print("   ✅ フォントサイズ最小化完了")
    except Exception as e:
        print(f"   ⚠️ フォントサイズ設定失敗（続行）: {e}")


def _get_current_page(page) -> int:
    """body テキストから現在のページ番号を取得"""
    try:
        body = page.text_content("body") or ""
        m = re.search(r'Page (\d+) of (\d+)', body)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return -1


def _go_to_page_dialog(page, target_page: int) -> bool:
    """Go to Page ダイアログでページ移動"""
    try:
        page.mouse.click(640, 450)
        time.sleep(1)
        menu = page.locator('ion-icon[name="ellipsis-vertical"]')
        if menu.count() > 0:
            menu.first.click()
            time.sleep(1)
        gtp = page.get_by_text("Go to Page", exact=True)
        gtp.click(timeout=3000)
        time.sleep(1)
        inp = page.locator('input[placeholder="page number"]')
        inp.wait_for(state="visible", timeout=3000)
        inp.fill(str(target_page))
        page.locator('button:has-text("Go")').last.click()
        time.sleep(3)
        page.mouse.click(640, 450)
        time.sleep(1)
        return True
    except Exception as e:
        print(f"   ⚠️ Go to Page ダイアログ失敗: {e}")
        # ダイアログが開いたままかもしれないので閉じる
        try:
            page.keyboard.press("Escape")
            time.sleep(1)
        except Exception:
            pass
        return False


def navigate_to_page(page, target_page: int, language: str = "ja"):
    """ページ移動（改善版）"""
    prev_key = "ArrowRight" if language == "ja" else "ArrowLeft"
    next_key = "ArrowLeft" if language == "ja" else "ArrowRight"

    if target_page <= 1:
        print("   📍 最初のページに移動中...")

        # 1. Home キー
        page.keyboard.press("Home")
        time.sleep(3)

        # 2. Page 1 になるまで矢印キー（上限200回）
        current = _get_current_page(page)
        if current != 1:
            print(f"   現在ページ: {current}、1ページ目まで戻ります...")
            for i in range(200):
                page.keyboard.press(prev_key)
                time.sleep(0.3)
                if (i + 1) % 20 == 0:
                    current = _get_current_page(page)
                    if current == 1:
                        break
                    print(f"   ...{i+1}回押下、現在ページ: {current}")
            time.sleep(2)

        print("   ✅ 最初のページに移動完了")
        return True

    # 再開時: 特定ページへの移動
    print(f"   📍 ページ {target_page} に移動中...")

    # 戦略1: 現在のページ位置を確認し、差分で移動
    current = _get_current_page(page)
    if current > 0:
        diff = target_page - current
        if diff == 0:
            print(f"   ✅ 既にページ {target_page} にいます")
            return True
        if abs(diff) <= 20:
            # 差が小さければ矢印キーで微調整
            key = next_key if diff > 0 else prev_key
            print(f"   矢印キーで {abs(diff)} ページ移動...")
            for _ in range(abs(diff)):
                page.keyboard.press(key)
                time.sleep(0.5)
            time.sleep(1)
            print(f"   ✅ ページ {target_page} に移動完了")
            return True

    # 戦略2: Go to Page ダイアログ
    if _go_to_page_dialog(page, target_page):
        print(f"   ✅ ページ {target_page} に移動完了（Go to Page）")
        return True

    # 戦略3: 矢印キー連打（最終手段）
    print(f"   矢印キーで {target_page - 1} 回移動（最終手段）...")
    for i in range(target_page - 1):
        page.keyboard.press(next_key)
        time.sleep(0.4)
        if (i + 1) % 50 == 0:
            print(f"   ...{i+1}/{target_page-1} 回")
    time.sleep(2)
    print(f"   ✅ ページ {target_page} に移動完了（矢印キー）")
    return True


def capture_book(book_name: str, max_pages: int = 0, start_page: int = 1):
    book_dir = BOOKS_DIR / book_name
    if not book_dir.exists():
        print(f"❌ ディレクトリが見つかりません: {book_dir}")
        sys.exit(1)

    meta = load_meta(book_dir)
    language = meta.get("language", "ja")
    asin = meta.get("asin", "")
    next_key = NEXT_PAGE_KEY.get(language, "ArrowLeft")

    if not asin:
        print("❌ meta.json に ASIN がありません")
        sys.exit(1)

    ss_dir = book_dir / "screenshots"
    ss_dir.mkdir(exist_ok=True)

    meta["status"] = "processing"
    save_meta(book_dir, meta)

    title = meta.get("title", book_name)
    print(f"📖 キャプチャ開始: {title}")
    print(f"   ASIN: {asin} | 言語: {language} | キー: {next_key}")
    if start_page > 1:
        print(f"   開始ページ: {start_page}")
    if max_pages > 0:
        print(f"   最大ページ: {max_pages}")
    print()

    prev_hash = None
    page_count = 0
    consecutive_duplicates = 0
    skipped_pages = []

    # progress.json の初期化/読み込み
    progress = load_progress(book_dir)
    if not progress:
        progress = {
            "last_completed_page": 0,
            "total_captured": 0,
            "skipped_pages": [],
            "started_at": datetime.now().isoformat(),
        }
    else:
        page_count = progress.get("total_captured", 0)
        skipped_pages = progress.get("skipped_pages", [])
    save_progress(book_dir, progress)

    # content_partial.md のヘッダー書き出し
    init_content_partial(book_dir, meta)

    with sync_playwright() as p:
        print("🌐 ブラウザ起動中...")
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )

        # デフォルトページを使用（new_page() だと別タブで開く問題を回避）
        page = browser.pages[0] if browser.pages else browser.new_page()
        book_url = f"{KINDLE_BASE_URL}/?asin={asin}"
        print(f"   📚 本を開く: {book_url}")
        page.goto(book_url)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        time.sleep(5)

        # ログインチェック
        current_url = page.url
        is_reader = "asin=" in current_url and "read.amazon" in current_url
        if not is_reader:
            if not do_login(page, book_dir):
                print("   ❌ ログインに失敗しました")
                page.screenshot(path=str(book_dir / "debug_login_failed.png"))
                browser.close()
                sys.exit(1)

            # ログイン後、本のURLに再アクセス
            page.goto(book_url)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            time.sleep(5)

        # ポップアップを回避: まずライブラリに行ってからポップアップを処理し、本を開き直す
        page.bring_to_front()
        time.sleep(2)

        # ポップアップ処理（改善版）
        # Kindle Cloud Reader は複数のダイアログを出すことがある:
        # - "vertical reading" お知らせ（OKボタン）
        # - "Most Recent Page Read" アラート（No/Yesボタン）
        # 再開時（start_page > 1）は "Most Recent Page Read" で Yes を押して
        # Kindle が記憶している読書位置を活用する
        is_resuming = start_page > 1
        for attempt in range(5):
            try:
                # ion-alert を優先的に処理（Most Recent Page Read など）
                alert = page.locator('ion-alert')
                if alert.count() > 0 and alert.first.is_visible():
                    alert_btns = alert.first.locator('button')
                    if alert_btns.count() > 0:
                        if is_resuming and alert_btns.count() >= 2:
                            # 再開時: 2番目のボタン（Yes）で最後に読んだページへ
                            alert_btns.nth(1).click()
                            print(f"   ✅ アラート: 読書位置を受け入れ（{alert_btns.nth(1).text_content().strip()}）")
                        else:
                            # 新規: 最初のボタン（No）で最初のページから
                            alert_btns.first.click()
                            print(f"   ✅ アラート閉じた（{alert_btns.first.text_content().strip()}）")
                        time.sleep(2)
                        continue

                # 通常のポップアップ（OK/Close/Got it）
                found = False
                for sel in [
                    'button:has-text("OK")',
                    'button:has-text("Close")',
                    'button:has-text("Got it")',
                ]:
                    btn = page.locator(sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click()
                        print(f"   ✅ ポップアップ閉じた: {sel}")
                        time.sleep(2)
                        found = True
                        break

                if not found:
                    # ポップアップなし → 完了
                    break
            except Exception:
                time.sleep(1)

        page.screenshot(path=str(book_dir / "debug_after_popup.png"))

        # 全タブを確認してリーダーを見つける
        print(f"   タブ数: {len(browser.pages)}")
        for i, p in enumerate(browser.pages):
            has_footer = False
            try:
                has_footer = p.locator('ion-footer').count() > 0
            except Exception:
                pass
            print(f"   タブ{i}: {p.url[:80]} | ion-footer={has_footer}")
            if has_footer:
                page = p
                print(f"   📖 リーダータブ検出: タブ{i}")
                break
        else:
            # ion-footer がなければ URL で判定
            for p in browser.pages:
                if "asin=" in p.url:
                    page = p
                    break
            print(f"   現在のURL: {page.url}")

        # 本のリーダーページにいるか確認（ion-footer があればリーダー）
        is_reader = page.locator('ion-footer').count() > 0
        if not is_reader:
            print(f"   📚 リーダーが開いていません。本のURLに再アクセス...")
            page.goto(book_url)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            time.sleep(8)
            # 再度ポップアップチェック
            for sel in ['ion-button:has-text("OK")', 'button:has-text("OK")', 'button:has-text("Close")']:
                try:
                    btn = page.locator(sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click()
                        time.sleep(2)
                except Exception:
                    pass

        # リーダー読み込み待機（ion-header の存在で判定）
        print("   ⏳ リーダー読み込み待機...")
        for i in range(30):
            try:
                # リーダーの特徴: ion-header + ページ情報
                if page.locator('ion-header').count() > 0:
                    body = page.text_content("body") or ""
                    if "Page " in body and " of " in body:
                        print("   ✅ リーダー準備完了")
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            page.screenshot(path=str(book_dir / "debug_reader_wait.png"))
            print("   ⚠️ リーダー待機タイムアウト。キャプチャを試みます...")

        # フォントサイズを最小に設定
        set_font_size_min(page)

        # フォント設定でリーダーから離れた場合、本を開き直す
        if page.locator('ion-footer').count() == 0:
            print("   📖 リーダーに戻ります...")
            page.goto(book_url)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            time.sleep(5)

        # 1ページ目に移動
        navigate_to_page(page, start_page, language)

        # ツールバー非表示
        page.mouse.click(640, 450)
        time.sleep(0.5)
        page.mouse.click(640, 450)
        time.sleep(1)

        print("📸 スクリーンショット開始...\n")

        # 最終チェック: リーダーが表示されているか
        body_text = page.text_content("body") or ""
        if "Page " not in body_text or "Library" in page.title():
            print("   ❌ リーダーが表示されていません。現在のURL: " + page.url)
            page.screenshot(path=str(book_dir / "debug_before_capture.png"))
            browser.close()
            sys.exit(1)

        m = re.search(r'Page (\d+) of (\d+)', body_text)
        if m:
            print(f"   📄 現在位置: Page {m.group(1)} of {m.group(2)}")

        while True:
            if max_pages > 0 and page_count >= max_pages:
                print(f"\n✅ 指定ページ数 ({max_pages}) に達しました")
                break

            ss_path = ss_dir / f"page_{page_count:04d}.png"

            # スクショ（リトライ付き）
            screenshot_ok = False
            for attempt in range(3):
                try:
                    page.screenshot(path=str(ss_path))
                    screenshot_ok = True
                    break
                except Exception as e:
                    if attempt < 2:
                        print(f"  ⚠️ スクショリトライ ({attempt+1}/3): {e}")
                        time.sleep(2)
                    else:
                        print(f"  ❌ スクショ3回失敗（スキップ）: {e}")
                        skipped_pages.append(page_count + 1)

            if not screenshot_ok:
                page.keyboard.press(next_key)
                time.sleep(SCREENSHOT_DELAY)
                page_count += 1
                progress["last_completed_page"] = page_count
                progress["total_captured"] = page_count
                progress["skipped_pages"] = skipped_pages
                save_progress(book_dir, progress)
                continue

            current_hash = image_hash(ss_path)
            if current_hash == prev_hash:
                consecutive_duplicates += 1
                if consecutive_duplicates >= 3:
                    print(f"\n✅ 最終ページに到達（同一ページ3回）")
                    ss_path.unlink()
                    break
                ss_path.unlink()
            else:
                consecutive_duplicates = 0
                prev_hash = current_hash

                # OCR（リトライ付き）
                text = ""
                for attempt in range(3):
                    text = ocr_image(ss_path, language)
                    if text:
                        break
                    if attempt < 2:
                        print(f"  ⚠️ OCRリトライ ({attempt+1}/3)")
                        time.sleep(2)

                page_count += 1
                if text:
                    append_to_content(book_dir, page_count, text)
                else:
                    skipped_pages.append(page_count)

                # progress.json 更新（毎ページ）
                progress["last_completed_page"] = page_count
                progress["total_captured"] = page_count
                progress["skipped_pages"] = skipped_pages
                save_progress(book_dir, progress)

                char_count = len(text) if text else 0
                if page_count % 10 == 0:
                    print(f"  📄 {page_count} ページ完了")
                else:
                    print(f"  📄 ページ {page_count} ({char_count} 文字)")

            page.keyboard.press(next_key)
            time.sleep(SCREENSHOT_DELAY)

        browser.close()
        print("🌐 ブラウザを閉じました")

    # content_partial.md → content.md に確定
    if page_count > 0:
        finalize_content(book_dir, page_count)
        print(f"\n✅ 完了！ {page_count} ページ")
        if skipped_pages:
            print(f"   ⚠️ スキップしたページ: {skipped_pages}")
    else:
        print("\n⚠️ テキストが取得できませんでした")

    meta["status"] = "done"
    meta["captured_at"] = datetime.now().isoformat()
    meta["total_pages"] = page_count
    save_meta(book_dir, meta)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", required=True)
    parser.add_argument("--pages", type=int, default=0)
    parser.add_argument("--start", type=int, default=0)  # 0 = 自動判定
    args = parser.parse_args()

    start_page = args.start
    if start_page == 0:
        # progress.json から自動で再開ページを判定
        book_dir = BOOKS_DIR / args.book
        if book_dir.exists():
            start_page = auto_detect_start(book_dir)
        else:
            start_page = 1

    capture_book(args.book, args.pages, start_page)


if __name__ == "__main__":
    main()
