"""快速验证 Worker 负载均衡逻辑。"""
import asyncio
from unittest.mock import AsyncMock

from app.agents.base import BaseAgent


async def main():
    agent = BaseAgent("test")

    # Mock system_stats 响应：worker1 空闲显存少，worker2 空闲显存多
    async def mock_get(url: str, **kwargs):
        class Resp:
            status_code = 200

            def json(self):
                if "8000" in url:
                    return {"devices": [{"vram_free": 1000, "torch_vram_free": 800}]}
                if "8002" in url:
                    return {"devices": [{"vram_free": 8000, "torch_vram_free": 7500}]}
                if "8003" in url:
                    return {"devices": [{"vram_free": 2000, "torch_vram_free": 1500}]}
                if "8004" in url:
                    return {"devices": [{"vram_free": 6000, "torch_vram_free": 5500}]}
                return {"devices": []}

        return Resp()

    agent.http.get = mock_get  # type: ignore[method-assign]

    image_workers = await agent.get_available_image_workers(4)
    print("image workers (should prefer 8002):", image_workers)

    video_workers = await agent.get_available_video_workers(4)
    print("video workers (should prefer 8004):", video_workers)

    # 单 Worker 选择：应返回负载最低的
    w1 = await agent.get_available_image_worker()
    w2 = await agent.get_available_video_worker()
    print("single image worker:", w1)
    print("single video worker:", w2)

    assert "8002" in image_workers[0], "空闲显存最大的图像 Worker 应优先"
    assert "8004" in video_workers[0], "空闲显存最大的视频 Worker 应优先"
    print("负载均衡测试通过")


if __name__ == "__main__":
    asyncio.run(main())
