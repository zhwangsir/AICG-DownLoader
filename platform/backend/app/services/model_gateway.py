"""本地模型网关（DramaClaw litellm/NewAPI 网关的本地化对等实现）。

DramaClaw 的外部服务依赖 → 本项目本地部署服务的映射：

| DramaClaw 外部依赖          | 本地替换服务                              |
|-----------------------------|-------------------------------------------|
| 灵山编导大模型（远程网关）  | spark02 qwen3.6-uncensored :8000（LLM）   |
| Gemini Flash（草图 VLM 门禁）| spark01 Qwen3-VL :8000（MacStudio studio04 已下线） |
| NanoBanana（宫格/草图生成） | ComfyUI-LB SDXL :8188（图像）             |
| Seedance/happyhorse（视频） | MiniMax H3 :8195 / LTX-2.5 :8198（视频）  |
| edge-tts / IndexTTS2(fal)   | IndexTTS2 :9200（TTS 配音）               |
| OpenAI Whisper API          | workstation faster-whisper :9210            |
| 阿里云 OSS（产物存储）      | 本地 output/ + NAS（44T SMB）             |

能力注册表（Capability）：每种能力声明主端点 + 可选回退链 + 健康探针。
网关职责：
1. 统一端点解析（Agent/Service 不再各自硬编码 URL）
2. 健康检查（TTL 缓存，失败自动走回退链）
3. 调用指标（次数/最近延迟/错误数），供 /gateway/health 与前端可视
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.core.node_logger import node_log

# 健康检查 TTL（秒）：避免每次调用都探测
_HEALTH_TTL = 30.0
# 探测超时（秒）：内网服务应快速响应
_PROBE_TIMEOUT = 4.0


@dataclass(frozen=True)
class CapabilitySpec:
    """一种模型能力的路由声明。"""

    name: str
    description: str
    # 主端点 + 依序回退端点（全部本地部署）
    endpoints: tuple[str, ...]
    # 健康探针路径（GET，2xx 视为健康）；空串 = 仅 TCP 连接探测
    health_path: str = ""
    # 端点类型标签（统计/展示用）
    kind: str = "http"
    # False = 退役/可选，健康报告不因它失败闭合，也不探测死端点
    required: bool = True


@dataclass
class EndpointMetrics:
    """单端点调用指标。"""

    calls: int = 0
    errors: int = 0
    last_latency_ms: float = 0.0
    last_error: str = ""
    last_called_at: float = 0.0


@dataclass
class HealthRecord:
    healthy: bool
    checked_at: float
    detail: str = ""


class ModelGateway:
    """本地模型网关：能力 → 本地端点的统一路由层。"""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=_PROBE_TIMEOUT, trust_env=False)
        self._health_cache: dict[str, HealthRecord] = {}
        self._metrics: dict[str, EndpointMetrics] = {}
        # 额外能力注册（测试注入/运行时扩展）；内建能力由 _build_registry()
        # 每次访问时从 settings 动态构建 — 保证运行期改配置（含测试 monkeypatch）
        # 立即生效，网关永不持有过期端点
        self._capabilities: dict[str, CapabilitySpec] = {}

    # ------------------------------------------------------------------
    # 能力注册表
    # ------------------------------------------------------------------

    def _build_registry(self) -> dict[str, CapabilitySpec]:
        s = settings
        return {
            "llm": CapabilitySpec(
                name="llm",
                description="主 LLM（剧本/角色/分镜/质检）：spark02 qwen3.6-uncensored",
                endpoints=(s.exo_base_url.removesuffix("/v1"),),
                health_path="/v1/models",
            ),
            "vlm": CapabilitySpec(
                name="vlm",
                description="视觉质检 VLM（M16.2 拼贴/M18.2 三视图/H3 画风漂移）",
                endpoints=(s.visual_model_url.removesuffix("/v1"),),
                health_path="/v1/models",
            ),
            "vlm_heavy": CapabilitySpec(
                name="vlm_heavy",
                description="重型 VLM：spark01 Qwen3-VL（MacStudio studio04 :9303 已下线）",
                endpoints=(s.visual_model_url.removesuffix("/v1"),),
                health_path="/v1/models",
            ),
            "image": CapabilitySpec(
                name="image",
                description="图像生成：ComfyUI-LB（主）→ 本地后端（回退）（majicMIX 写实 / animagineXL 动漫）",
                endpoints=(s.comfyui_image_hq, s.comfyui_image_fast),
                health_path="/system_stats",
            ),
            "video_comfy": CapabilitySpec(
                name="video_comfy",
                description="视频生成（ComfyUI-LB 通用视频工作池）：LB 入口 → 本地后端",
                endpoints=(s.comfyui_video_a, s.comfyui_video_b),
                health_path="/system_stats",
            ),
            "video_h3": CapabilitySpec(
                name="video_h3",
                description="视频生成（对白/角色一致性镜头）：MiniMax H3 ComfyUI",
                endpoints=(s.h3_comfyui_url,),
                health_path="/system_stats",
            ),
            "video_ltx": CapabilitySpec(
                name="video_ltx",
                description="视频生成（空镜/动作/长场景/分镜预览）：LTX-2.5 专用实例（已退役）",
                endpoints=(s.ltx_comfyui_url,),
                health_path="/system_stats",
                required=bool(s.ltx_enabled),
            ),
            "tts": CapabilitySpec(
                name="tts",
                description="TTS 配音：IndexTTS-2（workstation GPU0）",
                endpoints=(s.indextts_endpoint,),
                health_path="/docs",
            ),
            "asr": CapabilitySpec(
                name="asr",
                description="ASR 字幕：workstation faster-whisper :9210（MacStudio studio02 已下线）",
                endpoints=(s.ai_omni_asr_endpoint,),
                health_path="/health",
            ),
            "embedding": CapabilitySpec(
                name="embedding",
                description="文本嵌入：Qwen3-Embedding-4B（RAG/语义检索）",
                endpoints=("http://192.168.71.127:9302",),
                health_path="/v1/models",
            ),
            "music_caption": CapabilitySpec(
                name="music_caption",
                description="音乐反推：spark01 Omni-Captioner",
                endpoints=("http://192.168.71.82:8000",),
                health_path="/v1/models",
            ),
            "demucs": CapabilitySpec(
                name="demucs",
                description="人声分离：studio01 demucs-mlx（MacStudio 已下线，非必选）",
                endpoints=("http://100.67.43.40:9221",),
                health_path="/health",
                required=False,
            ),
        }

    # ------------------------------------------------------------------
    # 端点解析
    # ------------------------------------------------------------------

    def endpoint(self, capability: str) -> str:
        """返回能力的主端点（不做健康裁决；健康路由见 route()）。"""
        spec = self._spec(capability)
        return spec.endpoints[0]

    def openai_base_url(self, capability: str) -> str:
        """OpenAI 兼容能力的 base_url（端点 + /v1，幂等，供 AsyncOpenAI 使用）。"""
        ep = self.endpoint(capability)
        if not ep or ep.endswith("/v1"):
            return ep
        return ep + "/v1"

    def endpoints(self, capability: str) -> list[str]:
        """返回能力的完整端点链（主 + 回退）。"""
        return list(self._spec(capability).endpoints)

    def _spec(self, capability: str) -> CapabilitySpec:
        # 额外注册优先（测试注入），内建能力每次从 settings 动态构建（配置热生效）
        spec = self._capabilities.get(capability) or self._build_registry().get(capability)
        if spec is None:
            raise KeyError(
                f"未注册的能力: {capability}（可用: {sorted(self._all_specs())}）"
            )
        return spec

    def _all_specs(self) -> dict[str, CapabilitySpec]:
        """内建能力（动态）+ 额外注册能力的合并视图。"""
        return {**self._build_registry(), **self._capabilities}

    async def route(self, capability: str, *, require_healthy: bool = True) -> str:
        """健康路由：返回第一个健康端点；全部不健康时按 require_healthy 决定
        抛错还是回退主端点（fail-open，让调用方自行报错）。"""
        spec = self._spec(capability)
        for ep in spec.endpoints:
            if await self.is_healthy(capability, ep):
                node_log("gateway.route", "ok", capability=capability, endpoint=ep)
                return ep
        if require_healthy:
            node_log(
                "gateway.route", "error", capability=capability,
                error=f"全部端点离线: {list(spec.endpoints)}",
            )
            raise RuntimeError(
                f"能力 {capability} 全部端点离线: {list(spec.endpoints)}"
            )
        node_log(
            "gateway.route", "error", capability=capability,
            endpoint=spec.endpoints[0], error="全部端点离线，fail-open 回主端点",
        )
        return spec.endpoints[0]

    # ------------------------------------------------------------------
    # 健康检查（TTL 缓存）
    # ------------------------------------------------------------------

    async def is_healthy(self, capability: str, endpoint: str | None = None) -> bool:
        spec = self._spec(capability)
        ep = endpoint or spec.endpoints[0]
        key = f"{capability}|{ep}"
        cached = self._health_cache.get(key)
        now = time.time()
        if cached and now - cached.checked_at < _HEALTH_TTL:
            return cached.healthy
        healthy, detail = await self._probe(ep, spec.health_path)
        self._health_cache[key] = HealthRecord(healthy=healthy, checked_at=now, detail=detail)
        return healthy

    async def _probe(self, endpoint: str, health_path: str) -> tuple[bool, str]:
        url = f"{endpoint}{health_path}" if health_path else endpoint
        try:
            resp = await self._http.get(url)
            if resp.status_code < 500:
                # 2xx/3xx/404 均视为服务在线（404 说明端口活着只是路径不对）
                return True, f"HTTP {resp.status_code}"
            return False, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)

    async def health_report(self) -> dict[str, Any]:
        """全能力健康报告（/gateway/health 用）。"""
        report: dict[str, Any] = {}
        for name, spec in self._all_specs().items():
            if not spec.required:
                # 退役/可选能力不探测死端点，避免拖垮 /gateway/health 也不失败闭合
                eps = [
                    {
                        "endpoint": ep,
                        "healthy": False,
                        "detail": "optional/retired, not required",
                    }
                    for ep in spec.endpoints
                ]
                report[name] = {
                    "description": spec.description,
                    "endpoints": eps,
                    "healthy": True,
                    "required": False,
                }
                continue
            eps = []
            for ep in spec.endpoints:
                ok = await self.is_healthy(name, ep)
                rec = self._health_cache.get(f"{name}|{ep}")
                eps.append({
                    "endpoint": ep,
                    "healthy": ok,
                    "detail": rec.detail if rec else "",
                })
            report[name] = {
                "description": spec.description,
                "endpoints": eps,
                "healthy": any(e["healthy"] for e in eps),
                "required": True,
            }
        return report

    def invalidate_health_cache(self, capability: str | None = None) -> None:
        """失效健康缓存（服务重启后立即复测用）。"""
        if capability is None:
            self._health_cache.clear()
        else:
            for key in [k for k in self._health_cache if k.startswith(f"{capability}|")]:
                del self._health_cache[key]

    # ------------------------------------------------------------------
    # 调用指标
    # ------------------------------------------------------------------

    def record_call(self, capability: str, latency_ms: float, error: str = "") -> None:
        m = self._metrics.setdefault(capability, EndpointMetrics())
        m.calls += 1
        m.last_latency_ms = latency_ms
        m.last_called_at = time.time()
        if error:
            m.errors += 1
            m.last_error = error

    def metrics_report(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "calls": m.calls,
                "errors": m.errors,
                "last_latency_ms": round(m.last_latency_ms, 1),
                "last_error": m.last_error,
                "last_called_at": m.last_called_at,
            }
            for name, m in self._metrics.items()
        }

    def capabilities_report(self) -> list[dict[str, Any]]:
        """能力清单（/gateway/capabilities 用）。"""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "endpoints": list(spec.endpoints),
                "kind": spec.kind,
            }
            for spec in self._all_specs().values()
        ]

    async def aclose(self) -> None:
        await self._http.aclose()


# 全局单例
model_gateway = ModelGateway()
