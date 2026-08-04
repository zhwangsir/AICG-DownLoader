"""剧本与角色数据模型 — 与 PLATFORM_SPEC.md §4.8 关键数据结构对齐。"""

from __future__ import annotations

from enum import Enum
import time
from typing import Any

from pydantic import BaseModel, Field


class ShotType(str, Enum):
    CLOSEUP = "特写"
    BUST = "近景"
    MEDIUM = "中景"
    WIDE = "远景"


class Emotion(str, Enum):
    TENSION = "tension"
    ROMANTIC = "romantic"
    HAPPY = "happy"
    SAD = "sad"
    MYSTERIOUS = "mysterious"


class CameraMovement(str, Enum):
    STATIC = "static"
    PAN = "pan"
    TILT = "tilt"
    ZOOM = "zoom"


class Character(BaseModel):
    character_id: str
    name: str
    role: str = ""
    age: int | None = None
    description: str = ""
    personality: str = ""
    voice_id: str = ""
    reference_views: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    scene_id: int
    episode: int = 1
    shot_type: str = "中景"
    description: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    character_actions: str = ""
    dialogue: str = ""
    emotion: str = "neutral"
    duration_seconds: int = 5
    camera_movement: str = "static"
    narrative_beat: str = Field(
        "",
        description="叙事节拍：hook(强钩子)/escalation(冲突升级)/reversal(反转)/cliffhanger(悬念)/emotional_beat(情绪落点)/transition(过渡)",
    )


class Script(BaseModel):
    """剧本 JSON 结构（对应 PLATFORM_SPEC §4.8）。"""

    project_id: str = ""
    title: str = ""
    genre: str = ""
    aspect_ratio: str = "9:16"
    total_episodes: int = 1
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)


class ScriptRequest(BaseModel):
    """剧本 Agent 输入。"""

    premise: str = Field(..., description="一句话创意")
    genre: str = Field("都市悬疑", description="题材")
    episodes: int = Field(1, ge=1, le=100, description="集数")
    scenes_per_episode: int = Field(5, ge=1, le=30, description="每集分镜数")
    monetization_mode: str = Field(
        "iaa",
        description="变现模式：iaa(免费剧/红果模式，每集末尾钩子驱动完播) / iap(付费剧，第8-12集设首充卡点)",
    )


class CharacterAsset(BaseModel):
    """角色资产库条目：跨集/跨镜一致性的外观锁定卡（本地 JSON 持久化）。"""

    character_id: str
    name: str
    role: str = ""
    age: int | None = None
    description: str = ""
    personality: str = ""
    reference_images: dict[str, str] = Field(default_factory=dict, description="三视图定妆照 URL（front/side/closeup）")
    used_prompts: dict[str, str] = Field(default_factory=dict, description="定妆照生成时使用的提示词")
    appearance_lock: str = Field("", description="外观锁定卡：分镜生成时强制注入的核心外观关键词")
    locked: bool = Field(True, description="锁定后分镜/视频生成强制引用外观锁定卡")
    consistency_level: str = "L3"
    created_at: int = 0
    updated_at: int = 0


class CharacterAssetUpdateRequest(BaseModel):
    """角色资产局部更新请求（仅白名单字段生效）。"""

    name: str | None = None
    role: str | None = None
    age: int | None = None
    description: str | None = None
    personality: str | None = None
    appearance_lock: str | None = None
    locked: bool | None = None
    consistency_level: str | None = None


class CharacterCard(BaseModel):
    """角色卡结构（对应 PLATFORM_SPEC §4.8）。"""

    character_id: str
    name: str
    anchor_points: int = 200
    feature_vector: list[float] = Field(default_factory=list)
    reference_images: dict[str, str] = Field(default_factory=dict)
    lora_path: str = ""
    voice_sample: str = ""
    consistency_level: str = "L2"
    used_prompts: dict[str, str] = Field(default_factory=dict, description="生成时使用的提示词（供前端编辑）")


class CharacterRequest(BaseModel):
    """角色 Agent 输入。"""

    character: Character
    style: str = Field("写实电影感", description="画风")
    consistency_level: str = Field("L3", description="一致性层级 L1-L4")
    custom_positive_prompt: str = Field("", description="自定义正面提示词（非空时跳过 LLM 生成）")
    custom_negative_prompt: str = Field("", description="自定义负面提示词（非空时跳过 LLM 生成）")
    # 预览阶段传入已编辑的提示词，直接生成图片
    preview_positive_prompt: str = Field("", description="预览确认后的正面提示词（三视图共用）")
    preview_negative_prompt: str = Field("", description="预览确认后的负面提示词")


class CharacterPreviewRequest(BaseModel):
    """角色生成预览请求：搜索参考资料 + 生成提示词，不生成图片。"""

    character: Character
    style: str = Field("写实电影感", description="画风")


class CharacterPreviewResponse(BaseModel):
    """角色生成预览响应。"""

    character_id: str
    character: Character
    style: str
    search_reference: str = Field("", description="AI 联网搜索到的参考资料")
    prompts: dict[str, str] = Field(default_factory=dict, description="LLM 生成的三视图提示词")


class StoryboardRequest(BaseModel):
    """分镜 Agent 输入。"""

    scene: Scene
    characters: list[Character] = Field(default_factory=list)
    style: str = Field("写实电影感", description="画风")


class StoryboardResult(BaseModel):
    """分镜 Agent 输出。"""

    scene_id: int
    image_url: str
    prompt_used: str = ""
    # P4.3: LTX-Video 分镜预览视频 URL（settings.ltx_video_enabled=True 时填充）
    # 仅用于快速预览分镜动态效果，不替代正式视频生成
    preview_video_url: str = ""


class StoryboardBatchRequest(BaseModel):
    """分镜批量生成请求：一次并行生成多个场景的关键帧。"""

    scenes: list[Scene] = Field(..., description="待生成分镜的场景列表")
    characters: list[Character] = Field(default_factory=list)
    style: str = Field("写实电影感", description="画风")


class StoryboardBatchResult(BaseModel):
    """分镜批量生成结果。"""

    results: list[StoryboardResult] = Field(default_factory=list)
    failed_scenes: list[int] = Field(default_factory=list)


class VideoRequest(BaseModel):
    """视频 Agent 输入。"""

    scene_id: int
    image_url: str = Field(..., description="分镜关键帧图片 URL")
    prompt: str = Field("", description="英文正面提示词")
    negative_prompt: str = Field("", description="英文反向提示词")
    duration_seconds: int = Field(3, description="视频时长（秒）")


class VideoResult(BaseModel):
    """视频 Agent 输出。"""

    scene_id: int
    video_url: str
    duration_seconds: int = 3


class VideoBatchRequest(BaseModel):
    """视频批量生成请求：一次并行生成多个场景的视频片段。"""

    items: list[VideoRequest] = Field(..., description="待生成视频的场景请求列表")


class VideoBatchResult(BaseModel):
    """视频批量生成结果。"""

    results: list[VideoResult] = Field(default_factory=list)
    failed_scenes: list[int] = Field(default_factory=list)


class DialogueLine(BaseModel):
    """单条台词配音输入。"""

    text: str = Field(..., description="台词文本")
    character_name: str = Field("", description="角色名")
    character_role: str = Field("", description="角色定位（主角/反派/旁白等）")
    character_age: int | None = Field(None, description="角色年龄")
    rate: str = Field("+0%", description="语速调整，如 +10% / -10%")


class VoiceRequest(BaseModel):
    """配音 Agent 输入。"""

    scene_id: int
    dialogues: list[DialogueLine] = Field(..., description="台词列表")


class VoiceResult(BaseModel):
    """配音 Agent 输出。"""

    scene_id: int
    audio_urls: list[dict[str, Any]] = Field(default_factory=list)
    total_lines: int = 0


class SubtitleRequest(BaseModel):
    """字幕 Agent 输入。"""

    scene_id: int
    audio_url: str = Field(..., description="音频文件 URL")
    language: str = Field("zh", description="语言代码（zh/en）")


class SubtitleSegment(BaseModel):
    """字幕片段。"""

    start: float
    end: float
    text: str


class SubtitleResult(BaseModel):
    """字幕 Agent 输出。"""

    scene_id: int
    srt_content: str = ""
    segments: list[SubtitleSegment] = Field(default_factory=list)
    language: str = "zh"


class EditSegment(BaseModel):
    """剪辑片段输入：一个场景的视频/音频/字幕素材。"""

    scene_id: int
    video_url: str = Field(..., description="视频片段 URL")
    audio_url: str = Field(..., description="配音音频 URL")
    subtitle_url: str = Field(..., description="SRT 字幕 URL")
    duration_seconds: int = Field(5, description="目标时长（以音频为准）")


class EditRequest(BaseModel):
    """剪辑 Agent 输入。"""

    project_id: str = Field("", description="项目 ID")
    title: str = Field("", description="成片标题")
    segments: list[EditSegment] = Field(..., description="按顺序排列的片段素材")
    transition: str = Field("none", description="转场类型：none / fade")
    bgm_url: str | None = Field(None, description="背景音乐 URL（可选）")
    output_resolution: str = Field("1080x1920", description="输出分辨率，如 1080x1920")
    output_fps: int = Field(24, ge=1, le=60, description="输出帧率")
    # 2026-09-01 新规：AI 生成微短剧须在每集明显位置添加提示标识
    ai_label_enabled: bool = Field(True, description="是否在成片右上角烧录「AI生成」标识（合规要求默认开启）")
    license_number: str = Field("", description="短剧备案号/节目编号（非空时随标识一并烧录）")


class EditResult(BaseModel):
    """剪辑 Agent 输出。"""

    project_id: str
    title: str
    final_video_url: str
    duration_seconds: float
    segments_count: int


class PipelineRunRequest(BaseModel):
    """M7 全链路自动编排请求：从一句话创意一键生成短剧成片。"""

    premise: str = Field(..., description="一句话创意", min_length=1)
    genre: str = Field("都市悬疑", description="题材")
    episodes: int = Field(1, ge=1, le=10, description="集数")
    scenes_per_episode: int = Field(3, ge=1, le=10, description="每集分镜数")
    monetization_mode: str = Field("iaa", description="变现模式：iaa / iap")
    style: str = Field("写实电影感", description="画风")
    generate_character_refs: bool = Field(True, description="是否生成角色定妆照（耗时较长）")
    max_character_refs: int = Field(2, ge=0, le=10, description="最多生成定妆照的角色数")
    video_duration_seconds: int = Field(3, ge=1, le=10, description="单镜头视频时长（秒）")
    run_quality_check: bool = Field(True, description="成片后是否执行文本质检")
    ai_label_enabled: bool = Field(True, description="成片烧录「AI生成」标识（合规默认开启）")
    license_number: str = Field("", description="短剧备案号（非空时随标识烧录）")
    output_resolution: str = Field("1080x1920", description="输出分辨率")
    output_fps: int = Field(24, ge=1, le=60, description="输出帧率")


class AsyncTaskResponse(BaseModel):
    """异步任务创建响应。"""

    task_id: str
    agent: str
    status: str = "pending"
    poll_url: str = ""
    stream_url: str = ""


class QualityCheckItem(BaseModel):
    """单条质检问题。"""

    category: str = Field(..., description="问题类别：consistency/logic/sensitive/grammar")
    severity: str = Field(..., description="严重级别：info/warning/critical")
    scene_id: int | None = Field(None, description="关联场景 ID（可选）")
    message: str = Field(..., description="问题描述")
    suggestion: str = Field("", description="修改建议")


class QualityCheckRequest(BaseModel):
    """质检 Agent 输入。"""

    project_id: str = Field("", description="项目 ID")
    title: str = Field("", description="剧本标题")
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    subtitles: list[SubtitleResult] = Field(default_factory=list)
    check_types: list[str] = Field(
        default_factory=lambda: ["consistency", "logic", "sensitive"],
        description="检查类型列表",
    )


class QualityCheckResult(BaseModel):
    """质检 Agent 输出。"""

    project_id: str
    title: str
    score: int = Field(..., ge=0, le=100, description="综合质量分")
    summary: str = ""
    issues: list[QualityCheckItem] = Field(default_factory=list)
    checked_at: float = Field(default_factory=time.time)


class QualityVisualItem(BaseModel):
    """视觉质检单条问题。"""

    category: str = Field(..., description="问题类别：visual_consistency/coherence/anomaly/subtitle")
    severity: str = Field(..., description="严重级别：info/warning/critical")
    timestamp: float | None = Field(None, description="问题发生的时间戳（秒），可选")
    message: str = Field(..., description="问题描述")
    suggestion: str = Field("", description="修改建议")


class QualityVisualRequest(BaseModel):
    """视觉质检 Agent 输入。"""

    project_id: str = Field("", description="项目 ID")
    title: str = Field("", description="视频标题")
    scene_id: int = Field(0, description="场景 ID")
    video_url: str = Field(..., description="待检查的视频 URL")
    check_types: list[str] = Field(
        default_factory=lambda: ["visual_consistency", "coherence", "anomaly"],
        description="检查类型列表",
    )
    max_frames: int = Field(8, ge=1, le=32, description="最大抽帧数")


class QualityVisualResult(BaseModel):
    """视觉质检 Agent 输出。"""

    project_id: str
    title: str
    scene_id: int
    score: int = Field(..., ge=0, le=100, description="视觉质量分")
    summary: str = ""
    issues: list[QualityVisualItem] = Field(default_factory=list)
    checked_at: float = Field(default_factory=time.time)


class SubtitleFixRequest(BaseModel):
    """字幕回写修正请求：基于质检 issues 自动修正 ASR 错别字。"""

    subtitles: list[SubtitleResult] = Field(..., description="待修正的字幕列表")
    issues: list[QualityCheckItem] = Field(..., description="质检发现的问题列表")
    persist: bool = Field(True, description="是否回写到 SRT 文件")


class SubtitleFixItem(BaseModel):
    """单条字幕修正结果。"""

    scene_id: int
    original_text: str = Field("", description="修正前文本片段")
    fixed_text: str = Field("", description="修正后文本片段")
    applied: list[dict[str, str]] = Field(
        default_factory=list, description="应用的修正对 [{wrong, right}]"
    )


class SubtitleFixResult(BaseModel):
    """字幕回写修正结果。"""

    fixed_subtitles: list[SubtitleResult] = Field(default_factory=list)
    corrections: list[dict[str, str]] = Field(
        default_factory=list, description="提取到的全部修正对 [{wrong, right}]"
    )
    fixed_count: int = Field(0, description="被修改的字幕段数")
    details: list[SubtitleFixItem] = Field(default_factory=list)
    persisted_files: list[str] = Field(default_factory=list, description="回写的 SRT 文件路径")


class LipSyncRequest(BaseModel):
    """唇形同步 Agent 输入。

    P4.4: LatentSync 1.6 将视频人物口型与配音音频对齐。
    失败时自动降级返回原视频 URL，不影响成片流程。
    """

    scene_id: int = Field(0, description="场景 ID")
    video_url: str = Field(..., description="待同步的视频 URL")
    audio_url: str = Field(..., description="目标配音音频 URL")
    # 可选参考图：用于固定角色面部 ID（PuLID 风格）
    reference_image_url: str | None = Field(
        None, description="角色参考图 URL（可选，提升一致性）"
    )


class LipSyncResult(BaseModel):
    """唇形同步 Agent 输出。"""

    scene_id: int
    video_url: str = Field(..., description="唇形同步后的视频 URL")
    original_video_url: str = Field("", description="原始视频 URL（用于回退对比）")
    synced: bool = Field(True, description="是否成功执行唇形同步（False 表示已降级）")
    elapsed_seconds: float = 0.0


class PostprocessStep(str, Enum):
    """后处理步骤枚举。"""

    SUPER_RESOLUTION = "super_resolution"  # RealBasicVSR x4 超分
    FRAME_INTERPOLATION = "frame_interpolation"  # RIFE 插帧
    INPAINTING = "inpainting"  # ProPainter 修复
    AUDIO_DENOISE = "audio_denoise"  # DeepFilterNet3 降噪
    FINAL_ENCODE = "final_encode"  # Mac FFmpeg H.265 编码


class PostprocessStepResult(BaseModel):
    """单步后处理结果。"""

    step: PostprocessStep
    success: bool
    output_url: str = Field("", description="该步骤输出 URL（失败时为空）")
    elapsed_seconds: float = 0.0
    message: str = Field("", description="步骤说明或错误信息")
    skipped: bool = Field(False, description="是否跳过（开关关闭）")


class PostprocessRequest(BaseModel):
    """后处理 Agent 输入。

    P4.4: 编排超分 → 插帧 → 修复 → 降噪 → H.265 编码。
    单步失败时记录并跳过，不阻断整体流程（best-effort）。
    """

    scene_id: int = Field(0, description="场景 ID")
    video_url: str = Field(..., description="待后处理的视频 URL")
    audio_url: str | None = Field(None, description="音频 URL（降噪需要）")
    # 步骤覆盖：为空时按 settings 单步开关决定；非空时仅执行指定步骤
    steps: list[PostprocessStep] = Field(
        default_factory=list,
        description="指定步骤（空则按 settings 单步开关执行全部启用步骤）",
    )
    # 输出分辨率覆盖（None 时用 settings.postprocess_final_resolution）
    output_resolution: str | None = Field(None, description="最终输出分辨率，如 3840x2160")


class PostprocessResult(BaseModel):
    """后处理 Agent 输出。"""

    scene_id: int
    final_video_url: str = Field(..., description="最终成片 URL")
    original_video_url: str = Field("", description="原始视频 URL")
    steps: list[PostprocessStepResult] = Field(default_factory=list)
    success: bool = Field(True, description="是否所有启用步骤均成功")
    elapsed_seconds: float = 0.0


class AgentResponse(BaseModel):
    """Agent 统一响应。"""

    success: bool
    data: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0


class RAGOptimizeRequest(BaseModel):
    """RAG 提示词优化请求。"""

    user_prompt: str = Field(..., description="用户原始中文/英文描述")
    domain: str = Field("video", description="目标领域：image / video")
    style_hint: str = Field("", description="风格提示，用于增强检索")
    extra_instruction: str = Field("", description="额外优化指令")


class RAGOptimizeResponse(BaseModel):
    """RAG 提示词优化响应。"""

    optimized_positive: str = Field("", description="优化后的英文正向提示词")
    optimized_negative: str = Field("", description="优化后的英文负向提示词")
    style_notes: str = Field("", description="中文风格/技法说明")
    tags: list[str] = Field(default_factory=list, description="检索/分类标签")
    lora_recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="推荐 LoRA 列表（filename/style_key/trigger_words/weight）"
    )
    original_prompt: str = Field("", description="原始用户描述")
    retrieved_count: int = Field(0, description="检索到的知识条目数")
    fallback: bool = Field(False, description="是否使用了兜底结果")


class AgentAssistRequest(BaseModel):
    """通用智能体辅助请求：对指定上下文中的文本进行润色/扩写/精简/改写。"""

    text: str = Field(..., description="待处理的原始文本", min_length=1)
    context: str = Field(..., description="上下文类型：script/character/storyboard/video/voice/subtitle/edit/quality")
    action: str = Field("polish", description="动作：polish(润色)/expand(扩写)/shorten(精简)/rewrite(改写)")
    extra_instruction: str = Field("", description="额外要求")


class AgentAssistResponse(BaseModel):
    """通用智能体辅助响应。"""

    text: str = Field("", description="处理后的文本")
    action: str = Field("", description="执行的动作")
    context: str = Field("", description="上下文类型")
