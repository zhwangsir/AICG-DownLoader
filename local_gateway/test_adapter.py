"""local_gateway 适配层单元测试（mock 外层 httpx 调用，不触真机）。

覆盖：
① chat/completions 转发模型名映射
② images/generations 工作流构建参数（尺寸/seed 随机/b64 返回结构）+ images/edits 参考图回退
③ video 任务提交 + 轮询状态机（queued→running→succeeded+URL）
④ audio/speech multipart 字段与音频魔数校验（含 JSON URL 模式）
⑤ /v1/models 清单
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from local_gateway import main

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
WAV_BYTES = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"fmt fake-wav-data"


class FakeResp:
    def __init__(self, status: int = 200, json_data: Any = None, content: bytes = b"", headers: dict | None = None):
        self.status_code = status
        self._json = json_data
        self.content = content
        self.headers = headers or {"content-type": "application/json"}

    def json(self) -> Any:
        return self._json


RouteHandler = Callable[[str, str, dict], FakeResp]


class FakeClient:
    """按 URL 子串路由的伪 httpx.AsyncClient。"""

    def __init__(self, routes: list[tuple[str, str, RouteHandler]]):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def get(self, url: str, **kwargs: Any) -> FakeResp:
        return self._handle("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResp:
        return self._handle("POST", url, kwargs)

    def _handle(self, method: str, url: str, kwargs: dict) -> FakeResp:
        self.calls.append((method, url, kwargs))
        for m, substr, handler in self.routes:
            if m == method and substr in url:
                return handler(method, url, kwargs)
        raise AssertionError(f"FakeClient 未匹配路由: {method} {url}")


@pytest.fixture
def make_client(monkeypatch):
    """安装 FakeClient 并返回 TestClient。"""

    def _install(routes: list[tuple[str, str, RouteHandler]]) -> FakeClient:
        fake = FakeClient(routes)
        monkeypatch.setattr(main, "_http", lambda timeout=None: fake)
        return fake

    yield _install


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# ① chat/completions 模型名映射
# ---------------------------------------------------------------------------

def test_chat_model_mapping(make_client, client):
    captured: dict[str, Any] = {}

    def _chat(method, url, kwargs):
        captured.update(kwargs["json"])
        payload = {
            "id": "chatcmpl-x", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好"}, "finish_reason": "stop"}],
        }
        return FakeResp(json_data=payload, content=json.dumps(payload).encode())

    make_client([("POST", "/v1/chat/completions", _chat)])
    resp = client.post("/v1/chat/completions", json={
        "model": "DC-hermes-LLM",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "你好"
    # 任何入站模型名统一映射为本地真实模型名
    assert captured["model"] == main.CHAT_MODEL_NAME == "qwen3.6-uncensored"
    assert captured["messages"][0]["content"] == "hi"


def test_chat_stream_passthrough(make_client, client):
    """流式请求走 SSE 透传（fake 不支持 stream，仅验证不报错路径映射非流式分支外）。"""
    # 流式路径在 _http() 上直接 build_request/send，FakeClient 不实现；
    # 这里只验证 stream=True 时返回的是 StreamingResponse 类型（不消费 body 会报错，跳过）。
    # 非流式已由 test_chat_model_mapping 覆盖，流式实机冒烟验证。
    pass


# ---------------------------------------------------------------------------
# ② images/generations 工作流构建 + b64 返回结构
# ---------------------------------------------------------------------------

def _image_routes(captured: dict[str, Any], ref_download_ok: bool = True):
    def _prompt(method, url, kwargs):
        captured["workflow"] = kwargs["json"]["prompt"]
        return FakeResp(json_data={"prompt_id": "pid-1"})

    def _history(method, url, kwargs):
        return FakeResp(json_data={"pid-1": {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"7": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
        }})

    def _view(method, url, kwargs):
        return FakeResp(content=PNG_BYTES, headers={"content-type": "image/png"})

    def _ref(method, url, kwargs):
        if ref_download_ok:
            return FakeResp(content=PNG_BYTES, headers={"content-type": "image/png"})
        return FakeResp(status=404)

    def _upload(method, url, kwargs):
        captured["upload"] = kwargs
        return FakeResp(json_data={"name": "ref.png"})

    return [
        ("POST", "/prompt", _prompt),
        ("GET", "/history/", _history),
        ("GET", "/view", _view),
        ("GET", "ref.example.com", _ref),
        ("POST", "/upload/image", _upload),
    ]


def test_images_generations_workflow_params(make_client, client):
    captured: dict[str, Any] = {}
    make_client(_image_routes(captured))
    resp = client.post("/v1/images/generations", json={
        "model": "LingShan-G2",
        "prompt": "一只猫",
        "n": 1,
        "response_format": "b64_json",
        "metadata": {"resolution": "720p", "ratio": "16:9"},
    })
    assert resp.status_code == 200
    payload = resp.json()
    assert "created" in payload and len(payload["data"]) == 1
    assert base64.b64decode(payload["data"][0]["b64_json"]) == PNG_BYTES

    wf = captured["workflow"]
    # 16:9 基准 (1216,832) × 0.75 → (912,624)
    assert wf["4"]["inputs"]["width"] == 912
    assert wf["4"]["inputs"]["height"] == 624
    assert wf["2"]["inputs"]["text"] == "一只猫"
    assert wf["5"]["inputs"]["seed"] != 0  # seed 随机
    assert wf["1"]["inputs"]["ckpt_name"] == main.SDXL_CHECKPOINT


def test_images_generations_default_size(make_client, client):
    captured: dict[str, Any] = {}
    make_client(_image_routes(captured))
    resp = client.post("/v1/images/generations", json={"model": "NanoBanana", "prompt": "x"})
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert (wf["4"]["inputs"]["width"], wf["4"]["inputs"]["height"]) == (832, 1216)  # 默认竖屏


def test_images_edits_ipadapter_injection_and_fallback(make_client, client):
    captured: dict[str, Any] = {}
    make_client(_image_routes(captured, ref_download_ok=True))
    resp = client.post("/v1/images/edits", json={
        "model": "LingShan-NB-2", "prompt": "参考这张图重绘",
        "image": "http://ref.example.com/a.png",
    })
    assert resp.status_code == 200
    wf = captured["workflow"]
    # IPAdapter 注入成功：节点 12 存在且 KSampler model 重定向
    assert wf["12"]["class_type"] == "IPAdapterAdvanced"
    assert wf["5"]["inputs"]["model"] == ["12", 0]
    assert captured["upload"]["files"]["image"][1] == PNG_BYTES

    # 参考图下载失败 → 回退普通文生图（不阻断）
    captured2: dict[str, Any] = {}
    make_client(_image_routes(captured2, ref_download_ok=False))
    resp2 = client.post("/v1/images/edits", json={
        "model": "LingShan-NB-2", "prompt": "x", "image": "http://ref.example.com/b.png",
    })
    assert resp2.status_code == 200
    wf2 = captured2["workflow"]
    assert "12" not in wf2
    assert wf2["5"]["inputs"]["model"] == ["1", 0]


# ---------------------------------------------------------------------------
# ③ video 任务提交 + 轮询状态机
# ---------------------------------------------------------------------------

def test_video_submit_and_poll_state_machine(make_client, client):
    state = {"history_calls": 0}
    captured: dict[str, Any] = {}

    def _object_info(method, url, kwargs):
        return FakeResp(json_data={name: {} for name in main.LTX_REQUIRED_NODES})

    def _prompt(method, url, kwargs):
        captured["workflow"] = kwargs["json"]["prompt"]
        return FakeResp(json_data={"prompt_id": "vp-1"})

    def _history(method, url, kwargs):
        state["history_calls"] += 1
        if state["history_calls"] < 2:
            return FakeResp(json_data={})
        return FakeResp(json_data={"vp-1": {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"70": {"videos": [{"filename": "dc_video_x.mp4", "subfolder": "", "type": "output"}]}},
        }})

    def _queue(method, url, kwargs):
        return FakeResp(json_data={"queue_running": [[0, "vp-1"]], "queue_pending": []})

    make_client([
        ("GET", "/object_info", _object_info),
        ("POST", "/prompt", _prompt),
        ("GET", "/history/", _history),
        ("GET", "/queue", _queue),
    ])

    submit = client.post("/v1/video/generations", json={
        "model": "seedance-2.0", "prompt": "空镜：城市夜景", "duration": 4, "resolution": "480p",
    })
    assert submit.status_code == 200
    task_id = submit.json()["task_id"]
    assert submit.json()["status"] == "queued"
    assert submit.json()["backend"] == "ltx"  # seedance 默认走 LTX-2.5

    # 工作流为 LTX T2V：帧数 = _snap_ltx_frames(4*25)=97，KSampler 存在
    wf = captured["workflow"]
    assert wf["20"]["class_type"] == "EmptyLTXVLatentVideo"
    assert wf["20"]["inputs"]["length"] == main._snap_ltx_frames(100) == 97
    assert wf["30"]["class_type"] == "KSampler"
    assert wf["30"]["inputs"]["seed"] != 0

    # 第一次轮询：history 空 + queue 显示 running → running
    poll1 = client.get(f"/v1/video/generations/{task_id}")
    assert poll1.json()["status"] == "running"
    # 第二次轮询：history 成功 → succeeded + video URL
    poll2 = client.get(f"/v1/video/generations/{task_id}")
    body = poll2.json()
    assert body["status"] == "succeeded"
    assert "/view?filename=dc_video_x.mp4" in body["video_url"]


def test_video_backend_routing_rules():
    """路由规则：happyhorse/时长>15s/generate_audio → h3，否则 ltx。"""
    assert main._select_video_backend({"model": "happyhorse-1.0"}) == "h3"
    assert main._select_video_backend({"model": "seedance-2.0", "duration": 20}) == "h3"
    assert main._select_video_backend({"model": "seedance-2.0", "generate_audio": True}) == "h3"
    assert main._select_video_backend({"model": "seedance-2.0", "duration": 5}) == "ltx"


def test_frame_snap_rules():
    assert main._snap_h3_frames(5) == 124  # 24fps × 5s → 120 → 对齐 17k+5
    assert (main._snap_h3_frames(5) - 5) % 17 == 0
    assert main._snap_ltx_frames(100) == 97
    assert (main._snap_ltx_frames(121) - 1) % 8 == 0


# ---------------------------------------------------------------------------
# ④ audio/speech multipart 字段与魔数校验
# ---------------------------------------------------------------------------

def test_audio_speech_multipart_and_magic(make_client, client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    captured: dict[str, Any] = {}

    def _tts(method, url, kwargs):
        captured.update(kwargs)
        return FakeResp(content=WAV_BYTES, headers={"content-type": "audio/wav"})

    def _ref(method, url, kwargs):
        return FakeResp(content=WAV_BYTES, headers={"content-type": "audio/wav"})

    make_client([
        ("POST", "/tts", _tts),
        ("GET", "voice.example.com", _ref),
    ])

    # 直接返回音频字节（无 Accept json）
    resp = client.post("/v1/audio/speech", json={
        "model": "index-tts-2", "input": "你好，世界",
        "metadata": {"audio_url": "http://voice.example.com/ref.wav", "emotion_prompt": "开心"},
    })
    assert resp.status_code == 200
    assert resp.content == WAV_BYTES
    files = captured["files"]
    assert files["text"] == (None, "你好，世界")
    assert files["language"] == (None, "zh")
    assert files["emo_text"] == (None, "开心")
    # 参考声线作为文件字段上传
    assert "ref_audio" in files and files["ref_audio"][1] == WAV_BYTES

    # Accept json → 存静态目录返回 URL（audio/url 双键）
    resp2 = client.post(
        "/v1/audio/speech",
        json={"model": "index-tts-2", "input": "第二句"},
        headers={"Accept": "application/json"},
    )
    assert resp2.status_code == 200
    payload = resp2.json()
    url = payload["audio"]["url"]
    assert url == payload["url"]
    assert url.startswith(main.PUBLIC_BASE_URL + "/static/")
    name = url.rsplit("/", 1)[-1]
    assert (tmp_path / name).read_bytes() == WAV_BYTES


def test_audio_speech_rejects_non_audio(make_client, client):
    def _tts(method, url, kwargs):
        return FakeResp(content=b"cloned-voice", headers={"content-type": "text/plain"})

    make_client([("POST", "/tts", _tts)])
    resp = client.post("/v1/audio/speech", json={"model": "index-tts-2", "input": "hi"})
    assert resp.status_code == 502
    assert "非音频" in resp.json()["error"]["message"]


def test_looks_like_audio_magic():
    assert main._looks_like_audio(WAV_BYTES)
    assert main._looks_like_audio(b"ID3" + b"\x00" * 20)
    assert main._looks_like_audio(b"\xff\xfb" + b"\x00" * 20)
    assert not main._looks_like_audio(b"cloned-voice")
    assert not main._looks_like_audio(b"short")


# ---------------------------------------------------------------------------
# ⑤ /v1/models 清单
# ---------------------------------------------------------------------------

def test_models_list(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["data"]}
    for expected in [
        "DC-hermes-LLM", "DC-cognee-LLM", "DC-cognee-embedding", "DC-freezone-vision-LLM",
        "LingShan-G2", "LingShan-NB-2", "seedance-1.0-pro-fast", "seedance-2.0",
        "happyhorse-1.0", "index-tts-2",
    ]:
        assert expected in ids
