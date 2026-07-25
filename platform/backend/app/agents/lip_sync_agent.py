"""唇形同步 Agent — 视频口型与配音音频对齐。

P4.4 升级：引入 LatentSync 1.6 实现唇形同步，消除口型脱节。

流程：
1. 受 settings.lip_sync_enabled 总开关控制，默认关闭
2. 调用 LatentSyncService.sync_lip 端到端处理
3. 失败时自动降级返回原视频 URL，不影响成片流程

设计原则：
- best-effort：唇形同步是质量增强，不应阻断主流程
- 降级透明：返回 synced=False 标记，调用方知情但不报错
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import AgentResponse, LipSyncRequest, LipSyncResult
from app.services.latentsync_service import LatentSyncService, LatentSyncServiceError

logger = logging.getLogger(__name__)


class LipSyncAgent(BaseAgent):
    """唇形同步 Agent：LatentSync 1.6 将视频口型与配音对齐。

    受 settings.lip_sync_enabled 控制：
    - True: 调用 LatentSync 服务执行唇形同步
    - False: 跳过，直接返回原视频

    LatentSync 失败时自动降级返回原视频，标记 synced=False。
    """

    def __init__(self):
        super().__init__("lip_sync_agent")
        self._latentsync: LatentSyncService | None = None

    @property
    def latentsync_service(self) -> LatentSyncService:
        """懒加载 LatentSyncService，复用 BaseAgent 的 httpx 客户端。"""
        if self._latentsync is None:
            self._latentsync = LatentSyncService(http_client=self.http)
        return self._latentsync

    async def execute(
        self,
        request: LipSyncRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """执行唇形同步。

        Args:
            request: LipSyncRequest 包含 video_url / audio_url / 可选参考图
            progress_callback: 可选进度回调

        Returns:
            AgentResponse.data 为 LipSyncResult.model_dump()
            - 主路径成功: success=True, synced=True, video_url 为新视频
            - 关闭或失败降级: success=True, synced=False, video_url 为原视频
            - 严重错误（如参数校验）: success=False
        """
        start = time.time()

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        try:
            # 总开关关闭：直接跳过，返回原视频
            if not settings.lip_sync_enabled:
                _report(100, "唇形同步已关闭，跳过")
                logger.info(
                    "唇形同步已关闭 (lip_sync_enabled=False)，跳过: scene_id=%s",
                    request.scene_id,
                )
                result = LipSyncResult(
                    scene_id=request.scene_id,
                    video_url=request.video_url,
                    original_video_url=request.video_url,
                    synced=False,
                    elapsed_seconds=time.time() - start,
                )
                return AgentResponse(
                    success=True,
                    data=result.model_dump(),
                    elapsed_seconds=time.time() - start,
                )

            # 主路径：LatentSync 1.6
            try:
                _report(5, "开始 LatentSync 唇形同步")
                result = await self.latentsync_service.sync_lip(
                    video_url=request.video_url,
                    audio_url=request.audio_url,
                    scene_id=request.scene_id,
                    reference_image_url=request.reference_image_url,
                    progress_callback=progress_callback,
                )
                synced_url = result["video_url"]
                logger.info(
                    "唇形同步成功: scene_id=%s -> %s",
                    request.scene_id, synced_url,
                )
                lip_result = LipSyncResult(
                    scene_id=request.scene_id,
                    video_url=synced_url,
                    original_video_url=request.video_url,
                    synced=True,
                    elapsed_seconds=time.time() - start,
                )
                return AgentResponse(
                    success=True,
                    data=lip_result.model_dump(),
                    elapsed_seconds=time.time() - start,
                )
            except (LatentSyncServiceError, TimeoutError, Exception) as e:
                # 降级：返回原视频，标记 synced=False
                _report(100, f"唇形同步失败，降级返回原视频: {e}")
                logger.warning(
                    "唇形同步失败，降级返回原视频: scene_id=%s err=%s",
                    request.scene_id, e,
                )
                lip_result = LipSyncResult(
                    scene_id=request.scene_id,
                    video_url=request.video_url,
                    original_video_url=request.video_url,
                    synced=False,
                    elapsed_seconds=time.time() - start,
                )
                return AgentResponse(
                    success=True,  # 降级视为成功（不阻断主流程）
                    data=lip_result.model_dump(),
                    elapsed_seconds=time.time() - start,
                )
        except Exception as e:
            # 严重错误：参数校验失败等
            logger.error(
                "唇形同步 Agent 严重错误: scene_id=%s err=%s",
                request.scene_id, e,
            )
            return AgentResponse(
                success=False,
                error=f"唇形同步失败: {e}",
                elapsed_seconds=time.time() - start,
            )


lip_sync_agent = LipSyncAgent()
