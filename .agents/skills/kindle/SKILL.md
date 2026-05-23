---
name: kindle
description: 書籍タイトルを自然言語で指示するだけで、Kindle ライブラリからメタデータ取得 → 全ページ OCR → テキスト保存まで全自動実行。
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, mcp__browser-use__browser_navigate, mcp__browser-use__browser_screenshot, mcp__browser-use__browser_click, mcp__browser-use__browser_type, mcp__browser-use__browser_close_session, mcp__browser-use__browser_get_state, mcp__browser-use__browser_list_tabs, mcp__browser-use__browser_switch_tab, mcp__browser-use__browser_list_sessions, mcp__browser-use__browser_extract_content
---

# /kindle - Kindle 書籍テキスト全自動取り込み

このスキルは、導入先プロジェクトの `books/` ディレクトリを基準に動作する。`~/knowledge-hub` 以外へ導入した場合は、コマンド例の `~/knowledge-hub` を導入先の絶対パスに読み替える。

## アーキテクチャ

トークン消費を最小限にするため、2段階で処理する：

```
Claude Code（トークン使う）:  ログイン確認 → 本を探す → メタデータ取得
Python スクリプト（トークンゼロ）: ブラウザ起動 → 本を開く → 1ページ目移動 → 全ページ スクショ → OCR → content.md 保存
```

**重要: Python スクリプトは自己完結型。Browser Use でブラウザを開いたままにする必要はない。**

## 入力

自然言語でOK：
```
/kindle ゼロ・トゥ・ワン
/kindle Atomic Habits
```

---

## 既知のハマりポイント（必読）

### 1. 日本語縦書き本のページ送り方向
- 日本語縦書き本は**右→左**に読む
- UI の `<`（左矢印）= **次のページ**（前に進む）
- UI の `>`（右矢印）= **前のページ**（戻る）
- キーボード: **ArrowLeft = 次ページ**、ArrowRight = 前ページ
- Python スクリプトは `meta.json` の `language` を見て自動判定する

### 2. 1ページ目への移動方法
- **最も確実な方法**: `⋮` メニュー → 「Go to Page」→ `1` を入力 → 「Go」
- URL パラメータ（`?location=1`）は効かない
- プログレスバーのクリックも不安定
- Python スクリプトが「Go to Page」ダイアログを自動操作するので、Claude Code 側での移動は不要

### 3. Browser Use と Python スクリプトのプロファイル競合
- Browser Use と Python スクリプト（Playwright）は同じブラウザプロファイルを同時に使えない
- **Browser Use のセッションは Python スクリプト実行前に必ず閉じる**
- `mcp__browser-use__browser_close_session` で閉じてからスクリプトを実行

### 4. 表紙ページの特殊挙動
- Page 1（グレースケール表紙）と Location 1（カラー表紙）が別扱いの場合がある
- 本文は通常 Page 3〜5 あたりから始まる
- スクリプトは Page 1 から全ページキャプチャするので気にしなくてOK

### 5. テキスト抽出
- `browser_extract_content` は Kindle Cloud Reader では使えない（canvas 描画のため）
- OCR（tesseract）でスクリーンショットからテキスト抽出する

---

## Phase 1: フォルダ準備

1. `knowledge-hub/books/data/` の既存フォルダを確認し、次の3桁番号（NNN）を決定
2. 引数のタイトルから slug を生成（日本語そのままでOK）
3. `knowledge-hub/books/data/{NNN}_{slug}/` と `screenshots/` を作成

## Phase 2: Amazon ログイン確認 + メタデータ取得（Claude Code が担当）

1. Browser Use で `https://read.amazon.co.jp/` にアクセス
2. **Kindle ライブラリが表示されたら → ログインスキップ**（cookie が効いてる）
3. ログインページが表示された場合のみ：
   a. macOS キーチェーンから認証情報を取得：
      ```bash
      security find-generic-password -a "amazon-email" -s "kindle-capture" -w 2>/dev/null
      security find-generic-password -a "amazon-pass" -s "kindle-capture" -w 2>/dev/null
      ```
   b. キーチェーンに情報がない場合 → 登録コマンドを案内して終了
   c. Browser Use でログインフォームに入力・送信
   d. **認証情報は変数としてのみ扱い、絶対にファイルに書き出さない**
   e. **2FA / CAPTCHA が出たらユーザーに手動対応を依頼**
4. Kindle ライブラリ内で、指示されたタイトルの本を探す
5. 本をクリックして開く（**新しいタブで開かれることがある → タブ切り替えで対応**）
6. ASIN をURLから抽出（`?asin=BXXXXXXXXX`）
7. タイトル・著者名をページから取得
8. `meta.json` を生成して保存：
   ```json
   {
     "book_code": "NNN",
     "title": "正式タイトル",
     "author": "著者名",
     "asin": "BXXXXXXXXX",
     "language": "ja",
     "status": "processing",
     "captured_at": null,
     "total_pages": null
   }
   ```
9. **Browser Use セッションを閉じる**（Python スクリプトとのプロファイル競合を避けるため）

## Phase 3: 全ページキャプチャ + OCR（Python スクリプトが担当 = トークン消費ゼロ）

**Browser Use を閉じた後、Python スクリプトをバックグラウンドで実行する：**

```bash
nohup python3 <PROJECT_ROOT>/books/kindle_capture.py --book {NNN}_{slug} > <PROJECT_ROOT>/books/data/{NNN}_{slug}/capture.log 2>&1 &
echo $!
```

**重要:**
- Browser Use セッションが閉じていることを確認してから実行
- `run_in_background: true` で Bash を実行すること
- スクリプトの PID を控えておく

スクリプトの処理内容（自動実行）：
1. ブラウザを `.browser-profile` で起動（cookie でログイン済み）
2. `https://read.amazon.co.jp/?asin={ASIN}` を開く
3. リーダーの読み込みを待機
4. 「Go to Page」ダイアログで指定ページ（デフォルト: 1）に移動
5. 各ページをスクリーンショット
6. md5 ハッシュで重複検出（3回連続同一ページで終了＝最終ページ判定）
7. tesseract で OCR テキスト抽出
8. 全ページ結合して content.md に保存
9. meta.json を status=done に更新
10. ブラウザを閉じる

**オプション:**
- `--pages N`: 最大N ページまでキャプチャ（テスト用）
- `--start N`: ページ N から開始（途中再開用）

**このスクリプトは数分〜数十分かかる。**

### 完了の検知方法

スクリプトが終了すると `meta.json` の `status` が `done` に変わる。以下で確認：
```bash
cat <PROJECT_ROOT>/books/data/{NNN}_{slug}/meta.json | grep status
```

ユーザーに「バックグラウンドでキャプチャ中です。完了したら教えますね」と伝え、完了を待つ。
capture.log で進捗も確認できる：
```bash
tail -5 <PROJECT_ROOT>/books/data/{NNN}_{slug}/capture.log
```

## Phase 4: クリーンアップ（Claude Code が担当）

スクリプト完了後：
1. 「screenshots/ を削除しますか？」と聞く（容量節約）
2. content.md が生成されたか確認
3. git add -A && git commit && git push
4. 完了報告：
   ```
   ✅ 取り込み完了！
   📖 タイトル: {タイトル}
   👤 著者: {著者}
   📄 ページ数: {ページ数}
   💾 保存先: <PROJECT_ROOT>/books/data/{NNN}_{slug}/content.md
   ```

## セキュリティ

- Amazon のログイン情報は **macOS キーチェーンにのみ保存**
- キーチェーンから取得した情報は **メモリ上でのみ使用し、ファイル・ログに出力しない**
- cookie は `<PROJECT_ROOT>/books/.browser-profile/` に保存（.gitignore 対象）

## エラーハンドリング

- 本が見つからない → 「ライブラリに見当たりません。タイトルを確認してください」
- ログインが必要 → Browser Use で Phase 2 のログインフローを実行
- Python スクリプトがエラー → capture.log を確認してユーザーに報告
- スクリプトが途中で止まった → `--start N` で途中から再開可能
- Playwright 未インストール → `pip3 install --user playwright && python3 -m playwright install chromium`

## 依存関係

```bash
# 初回セットアップ（1回だけ）
pip3 install --user playwright
python3 -m playwright install chromium
gcloud auth application-default login
gcloud config set project <your-gcp-project-id>
gcloud services enable vision.googleapis.com
export KINDLE_CAPTURE_GCP_PROJECT="<your-gcp-project-id>"
```

## ルール

- screenshots/ と .browser-profile/ は .gitignore 対象
- 既存の content.md がある場合は上書きするか聞く
- 全ステップ完了後に git commit && git push
