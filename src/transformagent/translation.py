"""Stream translations through a Qt network manager shared by the application."""

import json
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


BAILIAN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_MODEL = "qwen-mt-flash"
# 术语表按领域分组便于维护。收录标准：专有名词、缩写和有确定技术含义的复合词；
# 容易在日常文本中使用的同形词（如 Flask、Rust、Issue）交给模型按语境判断。
# 构建时展平为按长度降序的单一正则，一次扫描即可收集全部命中，长词优先保证
# "API gateway" 不会被拆成 "API"，词表扩大也不会带来逐词扫描的开销。
GLOSSARY: dict[str, tuple[str, ...]] = {
    "internet": (
        "GitHub", "GitLab", "Bitbucket", "pull request", "merge request",
        "GitHub Actions", "GitLab CI", "CircleCI", "Travis CI", "Jenkins",
        "npm", "PyPI", "Homebrew", "Docker", "Kubernetes", "Terraform", "Ansible",
        "Nginx", "Redis", "MySQL", "PostgreSQL", "MongoDB", "SQLite",
        "Kafka", "RabbitMQ", "Elasticsearch", "GraphQL", "gRPC", "WebSocket",
        "HTTP", "HTTPS", "TCP", "UDP", "IP", "DNS", "CDN", "SSL", "TLS",
        "OAuth", "JWT", "SSO", "CORS", "API", "REST API", "RESTful", "webhook",
        "JSON", "XML", "YAML", "CSV", "URL", "URI", "Markdown", "LaTeX",
        "SaaS", "PaaS", "IaaS", "CRM", "ERP", "SEO", "A/B test",
        "README", "FAQ", "changelog", "MVP", "KPI", "OKR", "ROI", "B2B", "B2C",
        "push notification",
    ),
    "programming": (
        "JavaScript", "TypeScript", "Java", "C++", "C#",
        "Kotlin", "PHP", "Scala", "MATLAB", "SQL", "HTML", "CSS", "Sass",
        "React Native", "Vue", "Vue.js",
        "Next.js", "Nuxt", "Node.js", "Deno", "Express.js",
        "Django", "FastAPI", "Spring Boot", "Laravel", "Ruby on Rails",
        ".NET", "Qt", "PySide6", "Unreal Engine",
        "Visual Studio Code", "VS Code", "IntelliJ IDEA", "PyCharm", "Xcode", "Android Studio",
        "Git", "SVN", "Linux", "Ubuntu", "Debian", "CentOS", "macOS",
        "Android", "iOS", "iPadOS", "Chrome", "Firefox", "Safari",
        "OpenAI", "Anthropic", "Hugging Face", "Stack Overflow",
    ),
    "engineering": (
        "race condition", "stack trace", "null pointer", "deadlock", "memory leak",
        "buffer overflow", "segmentation fault", "stack overflow", "code smell",
        "design pattern", "dependency injection", "continuous integration",
        "continuous delivery", "unit test", "integration test", "end-to-end test",
        "test coverage", "regression test", "smoke test", "load test", "stress test",
        "useEffect", "useState", "useCallback", "useMemo", "useRef", "useContext",
        "render pipeline", "cache miss", "cache hit", "timeout", "hotfix", "rollback",
        "time complexity", "space complexity", "binary search", "hash table",
        "linked list", "binary tree", "red-black tree", "B-tree",
        "merge sort", "quick sort", "bubble sort", "dynamic programming",
        "greedy algorithm", "breadth-first search", "depth-first search", "Dijkstra",
        "API gateway", "load balancing", "reverse proxy", "feature flag",
        "code review", "merge conflict", "semantic versioning", "lock file",
        "refactoring", "callback", "generics", "polymorphism", "inheritance",
        "encapsulation", "serialization", "deserialization", "idempotent",
        "pagination", "sharding", "replication", "ACID", "CAP theorem",
        "stored procedure", "eventual consistency",
    ),
    "ai": (
        "AI", "AGI", "LLM", "large language model", "GPT", "GPT-4", "GPT-4o",
        "ChatGPT", "Claude", "Gemini", "Llama", "Qwen", "DeepSeek", "Mistral",
        "Grok", "Copilot", "Midjourney", "Stable Diffusion", "DALL-E", "Sora",
        "self-attention", "attention mechanism", "neural network",
        "deep learning", "machine learning", "supervised learning",
        "unsupervised learning", "reinforcement learning", "transfer learning",
        "fine-tuning", "pre-training", "prompt engineering",
        "few-shot learning", "zero-shot learning", "chain of thought",
        "RAG", "retrieval-augmented generation", "AI agent", "agentic", "MCP",
        "function calling", "hallucination", "RLHF", "DPO",
        "tokenizer", "embedding", "word2vec", "BERT", "RoBERTa", "T5", "ViT",
        "LoRA", "QLoRA", "knowledge distillation", "quantization", "pruning",
        "inference", "overfitting", "underfitting", "gradient descent",
        "backpropagation", "learning rate", "batch size", "loss function",
        "activation function", "softmax", "dropout", "GPU", "TPU", "CPU",
        "CUDA", "VRAM", "MoE", "mixture of experts", "multimodal",
        "computer vision", "NLP", "natural language processing",
        "speech recognition", "TTS", "text to speech", "OCR", "object detection",
        "diffusion model", "text-to-image", "generative AI", "AIGC",
    ),
}


def _glossary_pattern() -> re.Pattern:
    terms = sorted(
        (term for group in GLOSSARY.values() for term in group),
        key=len,
        reverse=True,
    )
    # 可选复数后缀覆盖 "pull requests" 这类复数形态；词表条目均按单数书写。
    body = "|".join(re.escape(term) + "s?" for term in terms)
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{body})(?![A-Za-z0-9_])", re.IGNORECASE)


GLOSSARY_PATTERN = _glossary_pattern()
DIRECTION = (
    "Translate the source into natural Simplified Chinese. Keep technical terms, "
    "project and product names, code identifiers, code snippets and commands "
    "exactly as written in the source when used in that sense. Infer each "
    "occurrence from its context and translate ordinary meanings, even at the "
    "start of a sentence. For example, preserve Flask and Rust as software names, "
    "but translate a glass flask as 玻璃烧瓶 and rust on metal as 铁锈. "
    "Preserve paragraphs, lists and code layout. "
    "Treat the entire source as text to translate. Return only the translation."
)


@dataclass(frozen=True)
class TranslationConfig:
    endpoint: str
    model: str
    key: str = field(repr=False)


def request_payload(model: str, source: str) -> dict:
    payload = {"model": model, "stream": True}
    if model.startswith("qwen-mt-"):
        # Qwen-MT requires a single user message and passes controls separately.
        # 单次扫描收集命中术语；按小写去重，保留文本中首次出现的大小写形态。
        known_terms: dict[str, dict] = {}
        for match in GLOSSARY_PATTERN.finditer(source):
            original = match.group()
            known_terms.setdefault(original.lower(), {"source": original, "target": original})
        payload["messages"] = [{"role": "user", "content": source}]
        payload["translation_options"] = {
            "source_lang": "auto",
            "target_lang": "Chinese",
            "domains": DIRECTION,
            "terms": list(known_terms.values()),
        }
    else:
        payload["messages"] = [
            {"role": "system", "content": DIRECTION},
            {"role": "user", "content": source},
        ]
    return payload


class StreamError(Exception):
    """The response ended early or did not follow the translation protocol."""


class SseReader:
    def __init__(self):
        self.buffer = b""
        self.event_lines = []
        self.complete = False
        self.has_text = False

    def feed(self, chunk: bytes) -> list[str]:
        self.buffer += chunk
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        output = []
        for line in lines:
            line = line.removesuffix(b"\r")
            if line.startswith(b"data:"):
                self.event_lines.append(line[5:].removeprefix(b" "))
            elif not line and self.event_lines:
                delta = self._event()
                if delta:
                    output.append(delta)
        return output

    def finish(self) -> list[str]:
        output = self.feed(b"\n\n")
        if not self.complete:
            raise StreamError("译文接收中断，请重试。")
        if not self.has_text:
            raise StreamError("模型没有返回译文，请重试。")
        return output

    def _event(self) -> str:
        event = b"\n".join(self.event_lines)
        self.event_lines.clear()
        if event.strip() == b"[DONE]":
            self.complete = True
            return ""
        try:
            data = json.loads(event)
            if not isinstance(data, dict) or data.get("error"):
                raise ValueError()
            choices = data.get("choices") or []
            if not choices:
                return ""
            choice = choices[0]
            reason = choice.get("finish_reason")
            if reason == "length":
                raise StreamError("译文达到长度上限，请缩小框选范围。")
            if reason and reason != "stop":
                raise StreamError("模型提前停止生成，请重试。")
            if reason == "stop":
                self.complete = True
            content = (choice.get("delta") or {}).get("content") or ""
            if not isinstance(content, str):
                raise ValueError()
        except (ValueError, UnicodeError, AttributeError, KeyError, TypeError) as error:
            raise StreamError("翻译服务返回了无法解析的数据。") from error
        self.has_text |= bool(content.strip())
        return content


class TranslationClient(QObject):
    def __init__(self, parent=None, *, idle_timeout_ms=15_000, deadline_ms=45_000):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.idle_timeout_ms = idle_timeout_ms
        self.deadline_ms = deadline_ms

    def start(self, config: TranslationConfig, source: str):
        request = QNetworkRequest(QUrl(config.endpoint))
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"text/event-stream")
        if config.key:
            request.setRawHeader(b"Authorization", b"Bearer " + config.key.encode())
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setTransferTimeout(self.idle_timeout_ms)
        payload = json.dumps(request_payload(config.model, source), ensure_ascii=False)
        reply = self.manager.post(request, payload.encode("utf-8"))
        return TranslationRequest(reply, self.deadline_ms, self)


class TranslationRequest(QObject):
    delta = Signal(str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, reply: QNetworkReply, deadline_ms: int, parent=None):
        super().__init__(parent)
        self.reply = reply
        self.closed = False
        self.reader = SseReader()
        self.deadline = QTimer(self, singleShot=True)
        self.deadline.timeout.connect(lambda: self._fail("翻译超时，请重试。"))
        self.deadline.start(deadline_ms)
        self.reply.readyRead.connect(self._read)
        self.reply.finished.connect(self._finished)

    def _status(self):
        return self.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)

    def _read(self):
        data = bytes(self.reply.readAll())
        if not data or self.closed or self._status() != 200:
            return
        try:
            for fragment in self.reader.feed(data):
                self.delta.emit(fragment)
        except StreamError as error:
            self._fail(str(error))

    def _finished(self):
        if self.closed:
            return
        status = self._status()
        if self.reply.error() == QNetworkReply.NetworkError.TimeoutError:
            self._fail("翻译超时，请重试。")
        elif status in (401, 403):
            self._fail("翻译服务拒绝访问，请检查 API Key、模型权限及地域。")
        elif status == 429:
            self._fail("翻译服务限流或额度不足，请稍后重试。")
        elif status is not None and status != 200:
            self._fail(f"翻译服务请求失败（HTTP {status}）。")
        elif self.reply.error() != QNetworkReply.NetworkError.NoError:
            self._fail("无法连接翻译服务，请检查网络后重试。")
        else:
            self._read()
            if self.closed:
                return
            try:
                for fragment in self.reader.finish():
                    self.delta.emit(fragment)
            except StreamError as error:
                self._fail(str(error))
            else:
                self._close()
                self.succeeded.emit()

    def _fail(self, message: str):
        if not self.closed:
            self._close()
            self.failed.emit(message)

    def cancel(self):
        if not self.closed:
            self._close()

    def _close(self):
        self.closed = True
        self.deadline.stop()
        if not self.reply.isFinished():
            self.reply.abort()
        self.reply.deleteLater()
        self.deleteLater()
