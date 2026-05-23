# Books リファレンス

## 概要

書籍タイトルを自然言語で指示するだけで、Kindle ライブラリから全自動でテキスト取り込みする。
Browser Use で Amazon ログイン → メタデータ取得 → 全ページ OCR → content.md 保存まで一気通貫。

## ディレクトリ構造

```
books/
├── reference.md              # 本ファイル
├── .gitignore                # screenshots/ を除外
└── data/
    ├── 001_ゼロトゥワン/
    │   ├── meta.json          # 書籍メタデータ（自動生成）
    │   ├── content.md         # OCR抽出テキスト（本文）
    │   └── screenshots/       # スクショ一時保存（.gitignore 対象）
    ├── 002_次の本/
    │   └── ...
```

---

## 使い方

タイトルを言うだけ：
```
/kindle ゼロ・トゥ・ワン
/kindle Atomic Habits
```

あとは全自動。

---

## meta.json（自動生成）

`/kindle` コマンドが Kindle ライブラリから情報を取得して自動作成する。

```json
{
  "book_code": "001",
  "title": "ゼロ・トゥ・ワン",
  "author": "ピーター・ティール",
  "asin": "B00LTBGBNS",
  "language": "ja",
  "status": "done",
  "captured_at": "2026-04-07T15:30:00",
  "total_pages": 215
}
```

---

## 取り込みフロー

```
1. /kindle タイトル名
2. Claude Code が全自動で:
   a. フォルダ + 番号を自動決定
   b. meta.json 作成（ASIN, タイトル, 著者）
   c. kindle_capture.py 実行（Playwright で自動ログイン→キャプチャ→OCR）
   d. content.md に結合して保存
   e. git commit & push
```

## 必要な環境

```bash
pip3 install --user playwright
python3 -m playwright install chromium
gcloud auth login                         # Google Cloud Vision API 用
gcloud services enable vision.googleapis.com
```

## 注意事項

- 2FA は自動で Cancel → パスワード再入力で回避。それでもダメな場合のみ手動対応
- Kindle Cloud Reader 非対応の書籍は取り込み不可
- screenshots/ は .gitignore で除外（容量が大きい）
- Amazon のログイン情報はファイルに保存しない（macOS キーチェーンのみ）
- OCR は Google Cloud Vision API（$0.0015/ページ、初1000枚/月無料）

## AI 連携

テキスト保管なので他のコマンドから直接参照できる：
```
/play この本の内容を踏まえて〇〇を分析して
→ books/data/001_ゼロトゥワン/content.md を Read して活用
```
