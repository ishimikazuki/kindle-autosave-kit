# Kindle Autosave Kit

Minimal OSS kit for capturing Kindle Cloud Reader pages and saving OCR text into Markdown.

It is designed for Codex/Claude-style local agent workflows: the agent finds a book in Kindle Cloud Reader, creates `meta.json`, and the Python script captures pages, sends screenshots to Google Cloud Vision OCR, and writes `content.md`.

## Contents

```text
books/
  kindle_capture.py
  kindle_login.py
  reference.md
  data/.gitkeep
.agents/skills/kindle/SKILL.md
skills/kindle/SKILL.md
install.sh
requirements.txt
```

No book text, screenshots, debug images, browser cookies, or credentials are included.

## Install Into Another Project

```bash
git clone https://github.com/ishimikazuki/kindle-autosave-kit.git
cd kindle-autosave-kit
./install.sh /path/to/your-project
```

Then install dependencies:

```bash
python3 -m pip install --user -r /path/to/your-project/requirements-kindle.txt
python3 -m playwright install chromium
gcloud auth application-default login
gcloud services enable vision.googleapis.com --project <your-gcp-project-id>
export KINDLE_CAPTURE_GCP_PROJECT="<your-gcp-project-id>"
```

Store Amazon credentials in macOS Keychain:

```bash
security add-generic-password -a "amazon-email" -s "kindle-capture" -w "your@email.com" -U
security add-generic-password -a "amazon-pass" -s "kindle-capture" -w "your-password" -U
```

Create the local browser session:

```bash
python3 /path/to/your-project/books/kindle_login.py
```

If 2FA or CAPTCHA appears, complete it in the displayed browser.

## Usage

Ask the agent:

```text
/kindle Book Title
```

Manual capture requires a `books/data/<book>/meta.json` file first:

```json
{
  "book_code": "001",
  "title": "Sample Book",
  "author": "Author",
  "asin": "BXXXXXXXXX",
  "language": "ja",
  "status": "processing",
  "captured_at": null,
  "total_pages": null
}
```

Then run:

```bash
python3 /path/to/your-project/books/kindle_capture.py --book 001_sample
```

## Notes

- Only books available in Kindle Cloud Reader can be captured.
- OCR uses Google Cloud Vision API.
- `books/.browser-profile/` contains cookies and must not be committed or shared.
- `books/data/*/screenshots/` contains temporary capture images.
- Use this only for books and content you are authorized to access and process.
