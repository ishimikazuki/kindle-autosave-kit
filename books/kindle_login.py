#!/usr/bin/env python3
"""
Kindle ログイン用ヘルパー。
ブラウザを .browser-profile で開き、キーチェーンの認証情報で自動ログイン。
2FA が出たら Cancel → パスワード再入力で自動回避。

使い方:
  python3 kindle_login.py
"""

import site
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, site.getusersitepackages())

from playwright.sync_api import sync_playwright

BROWSER_PROFILE = Path(__file__).parent / ".browser-profile"


def get_keychain_credential(account: str) -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", "kindle-capture", "-w"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def fill_password_and_submit(page, password: str) -> bool:
    """パスワードを入力してサインイン"""
    for selector in ['#ap_password', 'input[name="password"]', 'input[type="password"]']:
        try:
            field = page.locator(selector)
            if field.count() > 0 and field.first.is_visible():
                field.first.fill(password)
                print("   🔑 パスワード入力OK")
                time.sleep(0.5)
                page.locator('#signInSubmit, input[type="submit"]').first.click()
                print("   → サインイン送信")
                time.sleep(5)
                return True
        except Exception:
            continue
    return False


def main():
    print("🔐 Kindle ログインヘルパー")
    print(f"   プロファイル: {BROWSER_PROFILE}")

    email = get_keychain_credential("amazon-email")
    password = get_keychain_credential("amazon-pass")

    if not email or not password:
        print("❌ キーチェーンに認証情報がありません。以下で登録してください：")
        print('   security add-generic-password -a "amazon-email" -s "kindle-capture" -w "your@email.com"')
        print('   security add-generic-password -a "amazon-pass" -s "kindle-capture" -w "yourpassword"')
        sys.exit(1)

    print(f"   ✉️ メール: {email[:3]}***")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )

        page = browser.new_page()

        # まずライブラリにアクセスしてログイン状態を確認
        page.goto("https://read.amazon.co.jp/kindle-library")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(2)

        # ログイン済みチェック
        if "kindle-library" in page.url:
            print("✅ 既にログイン済みです！")
            browser.close()
            return

        # ログインが必要 → Amazon サインインページに直接アクセス
        print("📎 ログインが必要です。Amazon サインインページに移動...")
        signin_url = (
            "https://www.amazon.co.jp/ap/signin?"
            "openid.return_to=https%3A%2F%2Fread.amazon.co.jp%2Fkindle-library"
            "&openid.assoc_handle=amzn_kindle_mykindle_jp"
            "&openid.mode=checkid_setup"
            "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
            "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
            "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
        )
        page.goto(signin_url)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(2)
        print(f"   📄 URL: {page.url}")

        # メール入力
        for selector in ['#ap_email', 'input[name="email"]', 'input[type="email"]']:
            try:
                field = page.locator(selector)
                if field.count() > 0 and field.first.is_visible():
                    field.first.fill(email)
                    print("   ✉️ メールアドレス入力OK")
                    break
            except Exception:
                continue

        # パスワードが同じページにあれば直接入力（2FA 回避）
        if not fill_password_and_submit(page, password):
            # Continue → パスワード
            try:
                page.locator('#continue, input[id="continue"]').first.click()
                time.sleep(2)
                page.wait_for_load_state("networkidle", timeout=10000)
                print("   → Continue クリック")
            except Exception:
                pass
            fill_password_and_submit(page, password)

        # 2FA チェック → Cancel → パスワード再入力
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        time.sleep(2)

        page_text = page.text_content("body") or ""
        if any(kw in page_text.lower() for kw in ["verification", "captcha", "otp", "two-step", "approve"]):
            print("   ⚠️ 2FA 検出 → Cancel → パスワード再入力で回避...")

            # Cancel ボタンをクリック
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
                        break
                except Exception:
                    continue

            time.sleep(3)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            # パスワード再入力
            if not fill_password_and_submit(page, password):
                print("   ❌ パスワード再入力に失敗しました")

        # 最終確認
        time.sleep(2)
        page.goto("https://read.amazon.co.jp/")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(3)

        if "kindle-library" in page.url:
            print("✅ ログイン成功！Kindle ライブラリが表示されています。cookie 保存済み。")
        else:
            page.screenshot(path=str(Path(__file__).parent / "data" / "debug_login_final.png"))
            print(f"❌ ログインに失敗しました。URL: {page.url}")
            print(f"   デバッグスクショ: data/debug_login_final.png")

        browser.close()

    print("🌐 ブラウザを閉じました。kindle_capture.py を実行できます。")


if __name__ == "__main__":
    main()
