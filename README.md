# AI写作评估平台

一个基于 Flask 的英文作文评估网站，支持：

- 客观语言特征分析：词汇多样性、句法复杂度、名词化、情态动词、认知标记语、语篇标记语。
- AI 老师七维度主观评分：对齐作文评价的 7 个维度。
- 评分后继续聊天：基于作文、客观特征和评分结果继续追问修改建议。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python server.py
```

打开：

```text
http://127.0.0.1:8000
```

## Render 部署

Render Web Service 配置：

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: gunicorn server:app
```

如果需要让服务端读取 API Key，在 Render 的 Environment 中添加：

```text
OPENAI_API_KEY=你的 key
```

网页也支持用户直接填写 API Key。
