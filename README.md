# Battle Cards Server

バトルカードメーカーiPadアプリ用のバックエンドサーバーです。  
Google Gemini APIを使用して、撮影した写真からバトルカード風の画像を生成します。

## 技術スタック

- **Python 3.12+**
- **FastAPI** — Webフレームワーク
- **SQLite** — データベース（セッション・アップロード・ジョブ管理）
- **Google Gemini API** — AI画像生成
- **Poetry** — パッケージ管理

## セットアップ

### 1. 前提条件

- Python 3.12以上
- Poetry ([インストール方法](https://python-poetry.org/docs/#installation))
- Google Gemini APIキー ([取得方法](https://aistudio.google.com/apikey))

### 2. インストール

```bash
cd battle-cards-server
poetry install
```

### 3. 環境変数

`.env` ファイルを作成してAPIキーを設定：

```bash
echo "GEMINI_API_KEY=your-api-key-here" > .env
```

### 4. サーバー起動

```bash
# 環境変数を読み込み
export $(cat .env | xargs)

# 開発サーバー起動（ポート8000）
poetry run fastapi dev app/main.py --port 8000
```

サーバーが起動したら http://localhost:8000/docs でAPIドキュメントが確認できます。

### 5. iPadアプリとの接続

iPadアプリをビルドする際に、サーバーのURLを指定します：

```bash
# ローカルネットワーク内で接続する場合（HTTPSが必要）
flutter run --dart-define=API_BASE_URL=https://your-server-ip:8000
```

> **注意:** iPadアプリはHTTPS接続を要求します。ローカル開発時は自己署名証明書を使用するか、ngrokなどのトンネリングサービスを利用してください。

## API仕様

### `POST /api/session` — セッション作成
```json
// Response
{ "session_id": "uuid" }
```

### `POST /api/upload` — 画像アップロード
```
Content-Type: multipart/form-data
Fields: image (file), index (1-6), session_id (string)
```
```json
// Response
{ "status": "ok" }
```

### `POST /api/generate` — 画像生成開始
```json
// Request
{
  "session_id": "uuid",
  "name": "タロウ",
  "location": "トウキョウ",
  "advertise": false,
  "mode": "single"
}
// Response
{ "job_id": "uuid" }
```

### `GET /api/status?job_id=xxx` — 生成状況取得
```json
// Response
{
  "progress": 0-100,
  "images": ["/api/images/job_id/1", ...],
  "status": "processing" | "completed" | "failed"
}
```

### `GET /api/images/{job_id}/{index}` — 生成画像取得
生成された画像ファイルを返します。

## ディレクトリ構成

```
battle-cards-server/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPIエンドポイント（4つのAPI + 画像配信）
│   ├── database.py         # SQLiteデータベース管理
│   └── gemini_service.py   # Gemini API画像生成サービス
├── data/                   # 実行時に自動作成（DB・アップロード画像・生成画像）
├── pyproject.toml
├── poetry.lock
└── README.md
```

## 撮影モードと生成ロジック

| 条件 | 撮影枚数 | 生成枚数 | 備考 |
|------|---------|---------|------|
| `mode: single` | 1 | 6 | 同一画像から6パターン生成 |
| `mode: multi` + `advertise: true` | 5 | 6 | 6枚目=広告画像 |
| `mode: multi` + `advertise: false` | 6 | 6 | 全て撮影画像から生成 |
